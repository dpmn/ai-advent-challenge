"""
MCP-сервер Space Monitor.

Предоставляет 4 инструмента для фонового сбора данных NASA (APOD, NEO):
  - monitor_start     — запустить сборщик
  - monitor_stop      — остановить сборщик
  - monitor_status    — статус + статистика БД
  - monitor_summary   — агрегированная сводка собранного

Запуск:
  python mcp_servers/space_monitor_mcp/server.py [--port PORT]
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from collector import BackgroundCollector

_dotenv_path = Path(__file__).parent.parent.parent / ".env"
if _dotenv_path.exists():
    load_dotenv(dotenv_path=str(_dotenv_path))

mcp = FastMCP("space-monitor")
_collector = BackgroundCollector()


@mcp.tool(
    description="Start the background space data collector. "
                "Collects NASA APOD and NEO data periodically into a local SQLite database. "
                "Each tick cycles through different dates (APOD: last 30 days, NEO: rolling 7-day windows). "
                "Default interval is 60 seconds. Sources can be 'apod' and/or 'neo'."
)
def monitor_start(interval_seconds: int = 60, sources: list[str] | None = None) -> str:
    """Запускает фоновый сбор данных NASA.

    Args:
        interval_seconds: интервал между сборами в секундах (по умолчанию 60).
        sources: список источников — "apod", "neo" (по умолчанию ["apod", "neo"]).
    """
    if sources is None:
        sources = ["apod", "neo"]
    valid = {"apod", "neo"}
    for s in sources:
        if s not in valid:
            return f"Invalid source '{s}'. Valid sources: {', '.join(sorted(valid))}"
    return _collector.start(interval_seconds, sources)


@mcp.tool(
    description="Stop the background space data collector. "
                "Returns the total number of entries collected during this session."
)
def monitor_stop() -> str:
    """Останавливает фоновый сбор данных."""
    return _collector.stop()


@mcp.tool(
    description="Show the current status of the space monitor: "
                "is it running, since when, interval, sources, number of ticks, "
                "total DB records, last write timestamp, and last error (if any)."
)
def monitor_status() -> str:
    """Показывает статус монитора и статистику БД."""
    return _collector.get_status()


@mcp.tool(
    description="Return an aggregated summary of all data collected so far. "
                "Groups by source (apod, neo), shows entry counts, date ranges, "
                "and the last 5 collected items with their summaries."
)
def monitor_summary() -> str:
    """Возвращает агрегированную сводку по собранным данным."""
    return _collector.get_aggregated_summary()


def main():
    parser = argparse.ArgumentParser(description="Space Monitor MCP Server")
    parser.add_argument("--port", type=int, default=8766, help="Port to listen on")
    args = parser.parse_args()
    print(f"Starting Space Monitor MCP server on port {args.port}...")
    mcp.settings.port = args.port
    mcp.settings.host = "127.0.0.1"
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
