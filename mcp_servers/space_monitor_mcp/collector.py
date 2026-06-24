"""
Фоновый сборщик данных с NASA API.

Циклически обходит N последних дат, собирает APOD и NEO,
складывает в SQLite. Работает в отдельном потоке.
"""

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_dotenv_path = Path(__file__).parent.parent.parent / ".env"
if _dotenv_path.exists():
    load_dotenv(dotenv_path=str(_dotenv_path))

import os

NASA_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
NASA_BASE = "https://api.nasa.gov"

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = str(DATA_DIR / "monitor.db")


def _nasa_get(path: str, params: dict, retries: int = 2, delay: float = 2.0, timeout: int = 10) -> dict:
    """GET-запрос к NASA API с retry."""
    params["api_key"] = NASA_API_KEY
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{NASA_BASE}{path}?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                raise RuntimeError(f"NASA API rate limited (429): {body[:200]}")
            if e.code in (503, 502, 504) and attempt < retries:
                last_error = RuntimeError(f"NASA API HTTP {e.code}: {body[:200]}")
                time.sleep(delay)
                continue
            raise RuntimeError(f"NASA API HTTP {e.code}: {body[:500]}")
        except urllib.error.URLError as e:
            if attempt < retries:
                last_error = RuntimeError(f"NASA API connection: {e.reason}")
                time.sleep(delay)
                continue
            raise RuntimeError(f"NASA API connection: {e.reason}")
    raise last_error or RuntimeError("NASA API request failed")


def _apod_summary(data: dict) -> str:
    """Короткое описание APOD для строки summary в БД."""
    title = data.get("title", "N/A")
    d = data.get("date", "N/A")
    return f"APOD {d}: {title}"


def _neo_summary(data: dict) -> str:
    """Короткое описание NEO-feed для строки summary в БД."""
    neo_data = data.get("near_earth_objects", {})
    total = 0
    hazardous = 0
    for day_asts in neo_data.values():
        total += len(day_asts)
        for ast in day_asts:
            if ast.get("is_potentially_hazardous_asteroid", False):
                hazardous += 1
    return f"NEO: {total} objects, {hazardous} potentially hazardous"


class BackgroundCollector:
    """Фоновый сборщик NASA-данных с циклическим обходом дат.

    Для APOD: на каждом тике берёт date.today() - counter % 30 дней.
    Для NEO: 7-дневное окно, сдвигаемое на counter % 30 дней назад.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval: int = 60
        self._sources: list[str] = []
        self._counter: int = 0
        self._total_collected: int = 0
        self._last_error: Optional[str] = None
        self._started_at: Optional[str] = None

    # ── Управление ──────────────────────────────────────────────────

    def start(self, interval: int, sources: list[str]) -> str:
        """Запускает фоновый сбор данных."""
        if self.is_running():
            return "Monitor is already running. Call stop() first."

        self._interval = interval
        self._sources = sources
        self._counter = 0
        self._total_collected = 0
        self._last_error = None
        self._started_at = datetime.utcnow().isoformat() + "Z"
        self._stop.clear()

        self._init_db()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        src_list = ", ".join(sources)
        return f"Monitor started: interval={interval}s, sources=[{src_list}]"

    def stop(self) -> str:
        """Останавливает фоновый сбор."""
        if not self.is_running():
            past = self._total_collected
            self._counter = 0
            self._total_collected = 0
            self._started_at = None
            return f"No active monitor (last run collected {past} entries)."

        self._stop.set()
        self._thread.join(timeout=self._interval + 5)
        self._thread = None
        total = self._total_collected
        self._counter = 0
        self._total_collected = 0
        self._started_at = None
        return f"Monitor stopped. Total collected: {total} entries."

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── БД ──────────────────────────────────────────────────────────

    @staticmethod
    def _init_db():
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                data TEXT NOT NULL,
                summary TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _save(self, source: str, raw_data: dict, summary: str):
        ts = datetime.utcnow().isoformat() + "Z"
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO collections (ts, source, data, summary) VALUES (?, ?, ?, ?)",
                (ts, source, json.dumps(raw_data), summary),
            )
            conn.commit()
            conn.close()
            self._total_collected += 1
        except Exception as e:
            self._last_error = f"DB write error: {e}"

    # ── Циклический сбор ────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            self._collect_tick()
            self._counter += 1
            time.sleep(self._interval)

    def _collect_tick(self):
        for source in self._sources:
            if self._stop.is_set():
                return
            try:
                data, summary = self._fetch(source)
                self._save(source, data, summary)
            except RuntimeError as e:
                self._last_error = f"[{source}] {e}"
            except Exception as e:
                self._last_error = f"[{source}] unexpected: {e}"

    def _fetch(self, source: str) -> tuple[dict, str]:
        if source == "apod":
            return self._fetch_apod()
        elif source == "neo":
            return self._fetch_neo()
        else:
            raise ValueError(f"Unknown source: {source}")

    def _fetch_apod(self) -> tuple[dict, str]:
        """APOD с циклическим смещением даты на counter % 30 дней назад."""
        days_back = self._counter % 30
        target_date = date.today() - timedelta(days=days_back)
        data = _nasa_get("/planetary/apod", {"date": target_date.isoformat()})
        return data, _apod_summary(data)

    def _fetch_neo(self) -> tuple[dict, str]:
        """NEO с 7-дневным окном, сдвинутым на counter % 30 дней назад."""
        days_back = self._counter % 30
        start = date.today() - timedelta(days=days_back + 7)
        end = start + timedelta(days=7)
        data = _nasa_get("/neo/rest/v1/feed", {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })
        return data, _neo_summary(data)

    # ── Статус / агрегация ──────────────────────────────────────────

    def get_status(self) -> str:
        """Текущее состояние монитора и статистика БД."""
        running = self.is_running()
        lines = [f"Running: {'yes' if running else 'no'}"]
        if self._started_at:
            lines.append(f"Started at: {self._started_at}")
        if running:
            lines.append(f"Interval: {self._interval}s")
            lines.append(f"Sources: {', '.join(self._sources)}")
            lines.append(f"Ticks completed: {self._counter}")
        lines.append(f"Total collections: {self._total_collected}")

        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(ts), 'never') FROM collections"
            ).fetchone()
            conn.close()
            lines.append(f"DB records: {row[0]}")
            lines.append(f"Last write: {row[1]}")
        except Exception as e:
            lines.append(f"DB error: {e}")

        if self._last_error and running:
            lines.append(f"Last error: {self._last_error}")

        return "\n".join(lines)

    def get_aggregated_summary(self, sources: Optional[list[str]] = None) -> str:
        """Агрегированный отчёт по собранным данным.

        Группирует по источнику, возвращает количество и последние записи.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
        except Exception as e:
            return f"DB error: {e}"

        src_filter = sources or self._sources or ["apod", "neo"]
        parts: list[str] = []

        for src in src_filter:
            rows = conn.execute(
                "SELECT COUNT(*), COALESCE(MIN(ts),''), COALESCE(MAX(ts),'') "
                "FROM collections WHERE source=?",
                (src,),
            ).fetchone()
            count = rows[0]
            first_ts = rows[1]
            last_ts = rows[2]

            if count == 0:
                parts.append(f"  {src}: no data collected yet")
                continue

            parts.append(f"  {src}: {count} entries ({first_ts[:10]} .. {last_ts[:10]})")

            # последние 5 summary
            recent = conn.execute(
                "SELECT summary FROM collections WHERE source=? "
                "ORDER BY ts DESC LIMIT 5",
                (src,),
            ).fetchall()
            for s in recent:
                if s[0]:
                    parts.append(f"    • {s[0]}")

        if not parts:
            conn.close()
            return "No data collected yet."

        total = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        conn.close()

        header = f"Space Monitor Summary — {total} total records"
        return header + "\n" + "\n".join(parts)
