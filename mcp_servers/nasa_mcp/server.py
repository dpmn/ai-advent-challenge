"""
MCP-сервер для NASA API.

Предоставляет три инструмента:
  - apod — Astronomy Picture of the Day
  - mars_photos — фотографии с марсоходов NASA
  - neo_feed — список астероидов, пролетающих близко к Земле

Запуск:
  python mcp_servers/nasa_mcp/server.py [--port PORT]

Переменные окружения:
  NASA_API_KEY — ключ API NASA (по умолчанию DEMO_KEY)
"""

import argparse
import json
import os
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from typing import Optional

from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

_dotenv_path = Path(__file__).parent.parent.parent / ".env"
if _dotenv_path.exists():
    load_dotenv(dotenv_path=str(_dotenv_path))

NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
NASA_BASE = "https://api.nasa.gov"
IMAGES_BASE = "https://images-api.nasa.gov"


import time


def _nasa_get(path: str, params: dict, retries: int = 3, delay: float = 2.0) -> dict:
    """GET-запрос к NASA API с retry при временных ошибках."""
    params["api_key"] = NASA_API_KEY
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{NASA_BASE}{path}?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code in (503, 502, 504) and attempt < retries:
                last_error = RuntimeError(f"NASA API HTTP {e.code}: {body[:200]}")
                time.sleep(delay)
                continue
            raise RuntimeError(f"NASA API HTTP {e.code}: {body[:500]}")
        except urllib.error.URLError as e:
            if attempt < retries:
                last_error = RuntimeError(f"NASA API connection error: {e.reason}")
                time.sleep(delay)
                continue
            raise RuntimeError(f"NASA API connection error: {e.reason}")
    raise last_error or RuntimeError("NASA API request failed after all retries")


def _images_get(params: dict) -> dict:
    """GET-запрос к NASA Image and Video Library API, возвращает распарсенный JSON."""
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    url = f"{IMAGES_BASE}/search?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Images API HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Images API connection error: {e.reason}")


mcp = FastMCP("nasa-api")


@mcp.tool(
    description="Get the Astronomy Picture of the Day from NASA. "
                "Returns the image URL, title, explanation and date. "
                "If no date is provided, returns today's picture."
)
def apod(date_str: Optional[str] = None) -> str:
    """Astronomy Picture of the Day — фото дня от NASA с описанием.

    Args:
        date_str: Дата в формате YYYY-MM-DD (опционально, по умолчанию сегодня).
    """
    params = {}
    if date_str:
        params["date"] = date_str
    data = _nasa_get("/planetary/apod", params)
    title = data.get("title", "N/A")
    explanation = data.get("explanation", "N/A")
    url = data.get("url", data.get("hdurl", "N/A"))
    media_type = data.get("media_type", "unknown")
    return (
        f"Title: {title}\n"
        f"Date: {data.get('date', 'N/A')}\n"
        f"Media type: {media_type}\n"
        f"URL: {url}\n"
        f"Description: {explanation[:2000]}"
    )


@mcp.tool(
    description="Search photos from NASA Mars rovers (Curiosity, Opportunity, Spirit) "
                "via the NASA Image and Video Library. Returns image titles, thumbnails, "
                "and descriptions. Default rover is Curiosity."
)
def mars_photos(
    rover: str = "curiosity",
    query: Optional[str] = None,
    page: int = 1,
) -> str:
    """Фотографии с марсоходов NASA (Curiosity, Opportunity, Spirit) из NASA Image Library.

    Args:
        rover: Название марсохода (curiosity, opportunity, spirit).
        query: Дополнительный поисковый запрос (например, "selfie", "drill", "landscape").
        page: Номер страницы результатов (по умолчанию 1).
    """
    rover = rover.strip().lower()
    if rover not in ("curiosity", "opportunity", "spirit"):
        return f"Unknown rover '{rover}'. Choose: curiosity, opportunity, spirit."

    search_query = f"Mars rover {rover}"
    if query:
        search_query = f"{search_query} {query}"

    try:
        data = _images_get({"q": search_query, "media_type": "image", "page": page})
    except RuntimeError as e:
        return str(e)

    items = data.get("collection", {}).get("items", [])
    if not items:
        return f"No photos found for {rover} (query: '{search_query}')."

    lines = [f"Rover: {rover} — {len(items)} photo(s) found", ""]
    for i, item in enumerate(items[:10], 1):
        item_data = item.get("data", [{}])[0]
        title = item_data.get("title", "N/A")
        description = item_data.get("description", "")
        date_created = item_data.get("date_created", "N/A")[:10]
        links = item.get("links", [])
        thumb = links[0].get("href", "N/A") if links else "N/A"
        lines.append(f"{i}. {title}")
        lines.append(f"   Thumbnail: {thumb}")
        lines.append(f"   Date: {date_created}")
        lines.append(f"   Description: {description[:200]}")
        lines.append("")

    metadata = data.get("collection", {}).get("metadata", {})
    total = metadata.get("total_hits", len(items))
    if total > len(items):
        lines.append(f"... and {total - len(items)} more. Use page={page + 1} to see more.")

    return "\n".join(lines)


@mcp.tool(
    description="Get the list of Near Earth Objects (asteroids) passing close to Earth "
                "within a date range. Returns asteroid name, diameter, velocity, "
                "miss distance, and hazard status. "
                "Defaults to the next 7 days if no dates provided."
)
def neo_feed(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """Список астероидов, пролетающих близко к Земле, за период.

    Args:
        start_date: Начало периода в формате YYYY-MM-DD (по умолчанию сегодня).
        end_date: Конец периода в формате YYYY-MM-DD (по умолчанию start_date + 7 дней).
    """
    today = date.today()
    if not start_date:
        start_date = today.isoformat()
    if not end_date:
        end_date = (date.fromisoformat(start_date) + timedelta(days=7)).isoformat()

    data = _nasa_get("/neo/rest/v1/feed", {
        "start_date": start_date,
        "end_date": end_date,
    })

    neo_data = data.get("near_earth_objects", {})
    if not neo_data:
        return f"No asteroids found between {start_date} and {end_date}."

    total = 0
    hazardous = 0
    lines = [f"NEO feed: {start_date} → {end_date}", ""]

    for day in sorted(neo_data.keys()):
        asteroids = neo_data[day]
        if not asteroids:
            continue
        total += len(asteroids)
        lines.append(f"--- {day} ({len(asteroids)} object(s)) ---")
        for ast in asteroids:
            name = ast.get("name", "N/A")
            is_hazardous = ast.get("is_potentially_hazardous_asteroid", False)
            if is_hazardous:
                hazardous += 1

            diameter = "N/A"
            if "estimated_diameter" in ast:
                meters = ast["estimated_diameter"].get("meters", {})
                d_min = meters.get("estimated_diameter_min")
                d_max = meters.get("estimated_diameter_max")
                if d_min and d_max:
                    diameter = f"{d_min:.2f}–{d_max:.2f} m"

            close_data = ast.get("close_approach_data", [{}])[0]
            velocity = "N/A"
            miss_distance = "N/A"
            if close_data:
                vel = close_data.get("relative_velocity", {})
                if "kilometers_per_hour" in vel:
                    velocity = f"{float(vel['kilometers_per_hour']):.1f} km/h"
                miss = close_data.get("miss_distance", {})
                if "kilometers" in miss:
                    miss_distance = f"{float(miss['kilometers']):,.0f} km"

            hazard_flag = "⚠️ HAZARDOUS" if is_hazardous else "safe"
            lines.append(f"  • {name} [{hazard_flag}]")
            lines.append(f"    Diameter: {diameter} | Velocity: {velocity} | Miss: {miss_distance}")

        lines.append("")

    lines.append(f"Total objects: {total} | Potentially hazardous: {hazardous}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="NASA API MCP Server")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()
    print(f"Starting NASA MCP server on port {args.port}...")
    mcp.settings.port = args.port
    mcp.settings.host = "127.0.0.1"
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
