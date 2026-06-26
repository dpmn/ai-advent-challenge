"""
MCP-сервер Analyzer — инструменты анализа текста.

Предоставляет:
  - extract_keywords  — извлечение ключевых слов из текста
  - generate_report   — оборачивание контента в структурированный markdown-отчёт

Запуск:
  python mcp_servers/analyzer_mcp/server.py [--port PORT]
"""

import argparse
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("analyzer")

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "dare", "ought", "used", "this", "that", "these", "those", "it",
    "its", "they", "them", "their", "he", "she", "his", "her", "we",
    "us", "our", "you", "your", "i", "me", "my", "not", "no", "nor",
    "so", "very", "just", "about", "up", "down", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "such", "only", "own",
    "same", "too", "also", "if", "into", "than", "that", "which",
    "who", "whom", "what", "after", "before", "between", "through",
    "during", "because", "while", "since", "until", "although", "though",
    "while", "whereas", "except", "but", "не", "и", "в", "на", "с",
    "по", "для", "от", "до", "из", "за", "о", "об", "при", "к",
    "у", "во", "без", "через", "над", "под", "перед", "между",
    "чтобы", "что", "как", "так", "это", "его", "ее", "их", "мы",
    "вы", "они", "я", "ты", "он", "она", "но", "а", "да", "или",
    "если", "когда", "потому", "поэтому", "однако", "ведь", "вот",
    "уже", "еще", "был", "была", "было", "были", "есть", "будет",
    "будут", "все", "весь", "эта", "эти", "тот", "та", "те",
}


@mcp.tool(
    description="Extract the most frequent keywords from a text. "
                "Filters out common stop words and punctuation. "
                "Returns a numbered list of keywords with their frequencies.",
)
def extract_keywords(
    text: str,
    max_keywords: int = 10,
) -> str:
    """Извлекает ключевые слова из текста.

    Args:
        text: исходный текст для анализа.
        max_keywords: количество ключевых слов (по умолчанию 10).
    """
    if not text.strip():
        return "No text provided."

    # Нормализация
    lower = text.lower()
    # Убираем пунктуацию
    clean = re.sub(r'[^\w\s]', ' ', lower)
    # Убираем цифры
    clean = re.sub(r'\d+', ' ', clean)
    tokens = clean.split()

    # Фильтруем стоп-слова и короткие слова (< 3 символов)
    words = [w for w in tokens if w not in STOP_WORDS and len(w) >= 3]

    if not words:
        return "No meaningful keywords found."

    counter = Counter(words)
    most_common = counter.most_common(max_keywords)

    total = sum(counter.values())
    lines = [
        f"Top {len(most_common)} keywords (from {total} total words after filtering):",
        "",
    ]
    for i, (word, count) in enumerate(most_common, 1):
        pct = round(count / total * 100, 1)
        lines.append(f"  {i}. {word} — {count} occurrence(s) ({pct}%)")

    return "\n".join(lines)


@mcp.tool(
    description="Generate a structured markdown report from a title and content. "
                "Includes a timestamp, title as H1, content as sections, "
                "and metadata footer. Returns the full markdown text.",
)
def generate_report(
    title: str,
    content: str,
) -> str:
    """Создаёт структурированный markdown-отчёт.

    Args:
        title: заголовок отчёта.
        content: основное содержимое отчёта.
    """
    if not title.strip():
        title = "Untitled Report"
    if not content.strip():
        content = "No content provided."

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    word_count = len(content.split())
    char_count = len(content)

    lines = [
        f"# {title.strip()}",
        "",
        f"*Generated: {timestamp}*",
        "",
        "---",
        "",
        content.strip(),
        "",
        "---",
        "",
        "### Report Metadata",
        "",
        f"- **Title:** {title.strip()}",
        f"- **Generated:** {timestamp}",
        f"- **Word count:** {word_count}",
        f"- **Character count:** {char_count}",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Analyzer MCP Server")
    parser.add_argument("--port", type=int, default=8768, help="Port to listen on")
    args = parser.parse_args()
    print(f"Starting Analyzer MCP server on port {args.port}...")
    mcp.settings.port = args.port
    mcp.settings.host = "127.0.0.1"
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
