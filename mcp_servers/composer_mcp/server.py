"""
MCP-сервер Composer — пайплайн композиции MCP-инструментов.

Предоставляет 3 инструмента для построения пайплайна:
  - fetch_data      — получить данные из внешнего источника (NASA APOD / NEO)
  - summarize_text  — сжать текст до указанного числа слов
  - save_to_file    — сохранить текст в файл

Пайплайн (оркестрируется LLM):
  fetch_data("apod") → summarize_text(...) → save_to_file(...)

Запуск:
  python mcp_servers/composer_mcp/server.py [--port PORT]
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

_dotenv_path = Path(__file__).parent.parent.parent / ".env"
if _dotenv_path.exists():
    load_dotenv(dotenv_path=str(_dotenv_path))

NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
NASA_BASE = "https://api.nasa.gov"
DATA_DIR = Path(__file__).parent / "output"
DATA_DIR.mkdir(exist_ok=True)

mcp = FastMCP("composer")


# ── Вспомогательное ──────────────────────────────────────────────────

def _nasa_get(path: str, params: dict, retries: int = 2, delay: float = 2.0) -> dict:
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


# ── Инструмент 1: fetch_data ─────────────────────────────────────────

@mcp.tool(
    description="Fetch data from a NASA source. "
                "Supports 'apod' (Astronomy Picture of the Day) and "
                "'neo' (Near Earth Objects / asteroid feed). "
                "Returns the raw data as formatted text.",
)
def fetch_data(
    source: str,
    param: Optional[str] = None,
) -> str:
    """Получить данные из NASA API.

    Args:
        source: источник данных — "apod" или "neo".
        param: для apod — дата в формате YYYY-MM-DD (опционально),
               для neo — дата начала периода (опционально).
    """
    source = source.strip().lower()
    if source == "apod":
        return _fetch_apod(param)
    elif source == "neo":
        end_date = None
        if param:
            start = param
            parsed = date.fromisoformat(start)
            end = (parsed + timedelta(days=7)).isoformat()
            end_date = end
        return _fetch_neo(param, end_date)
    else:
        return f"Unknown source '{source}'. Supported: apod, neo."


def _fetch_apod(date_str: Optional[str] = None) -> str:
    """APOD: Astronomy Picture of the Day."""
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


def _fetch_neo(start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
    """NEO: список астероидов, пролетающих близко к Земле."""
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

            hazard_flag = "HAZARDOUS" if is_hazardous else "safe"
            lines.append(f"  * {name} [{hazard_flag}]")
            lines.append(f"    Diameter: {diameter} | Velocity: {velocity} | Miss: {miss_distance}")

        lines.append("")

    lines.append(f"Total objects: {total} | Potentially hazardous: {hazardous}")
    return "\n".join(lines)


# ── Инструмент 2: summarize_text ─────────────────────────────────────

@mcp.tool(
    description="Summarize a block of text to a maximum number of words. "
                "Structured data (APOD, NEO) is handled specially — "
                "key fields are preserved. Returns a concise summary.",
)
def summarize_text(
    text: str,
    max_words: int = 50,
) -> str:
    """Сжать текст до указанного числа слов с сохранением ключевой информации.

    Args:
        text: исходный текст для сжатия.
        max_words: максимальное число слов в результате (по умолчанию 50).
    """
    if not text.strip():
        return ""

    # Структурированный текст APOD: сохраняем Title + Date + первые 2 предложения описания
    if text.startswith("Title:") and "Description:" in text:
        return _summarize_apod(text, max_words)

    # Структурированный текст NEO: сохраняем заголовок + итог
    if text.startswith("NEO feed:") and "Total objects:" in text:
        return _summarize_neo(text, max_words)

    # Обычный текст: извлекативное сжатие
    return _summarize_generic(text, max_words)


def _summarize_apod(text: str, max_words: int) -> str:
    """Сжатие APOD: Title, Date, ключевые предложения из Description."""
    lines = text.split("\n")
    title = ""
    date_val = ""
    description_lines = []
    in_desc = False
    for line in lines:
        if line.startswith("Title:"):
            title = line
        elif line.startswith("Date:"):
            date_val = line
        elif line.startswith("Description:"):
            in_desc = True
            desc_text = line[len("Description:"):].strip()
            if desc_text:
                description_lines.append(desc_text)
        elif in_desc and line.strip():
            description_lines.append(line.strip())

    desc_text = " ".join(description_lines)
    words = desc_text.split()
    if len(words) > max_words - 10:
        words = words[:max_words - 10]
        desc_text = " ".join(words) + "..."

    result = f"{title}\n{date_val}\nDescription: {desc_text}"
    return result


def _summarize_neo(text: str, max_words: int) -> str:
    """Сжатие NEO: первая и последняя строка (шапка + итог)."""
    lines = text.strip().split("\n")
    header = lines[0] if lines else ""
    # последняя строка — "Total objects: N | Potentially hazardous: M"
    total_line = ""
    for line in reversed(lines):
        if line.startswith("Total objects:"):
            total_line = line
            break
    result = f"{header}\n{total_line}"
    if len(result.split()) > max_words:
        result = f"{header[:60]}...\n{total_line}"
    return result


def _summarize_generic(text: str, max_words: int) -> str:
    """Извлекативное сжатие произвольного текста: первые N слов."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


# ── Инструмент 3: save_to_file ───────────────────────────────────────

@mcp.tool(
    description="Save text content to a file in the output directory. "
                "Returns the absolute file path and size in bytes. "
                "Path traversal ('..' or '/') in the filename is rejected.",
)
def save_to_file(
    filename: str,
    content: str,
) -> str:
    """Сохранить текст в файл внутри output-директории.

    Args:
        filename: имя файла (только имя, без пути).
        content: содержимое файла.
    """
    # Защита от path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return f"Error: invalid filename '{filename}'. Use a simple filename without path separators."

    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    if not safe_name:
        return "Error: filename results in an empty safe name."

    filepath = DATA_DIR / safe_name
    try:
        filepath.write_text(content, encoding="utf-8")
        size = filepath.stat().st_size
        return f"Saved: {filepath.resolve()} ({size} bytes)"
    except OSError as e:
        return f"Error writing file: {e}"


def main():
    parser = argparse.ArgumentParser(description="Composer MCP Server")
    parser.add_argument("--port", type=int, default=8767, help="Port to listen on")
    args = parser.parse_args()
    print(f"Starting Composer MCP server on port {args.port}...")
    mcp.settings.port = args.port
    mcp.settings.host = "127.0.0.1"
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
