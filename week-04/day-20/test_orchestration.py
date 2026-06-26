"""
Оркестрационный тест: кросс-серверный пайплайн composer + analyzer.

Флоу (5 шагов, 2 MCP-сервера):
  1. composer__fetch_data("apod")          — получить NASA APOD
  2. composer__summarize_text(text, 50)    — сжать до 50 слов
  3. analyzer__extract_keywords(text, 5)   — извлечь ключевые слова
  4. analyzer__generate_report(title, ...) — сформировать markdown-отчёт
  5. composer__save_to_file("report.md")   — сохранить результат

При недоступности NASA API использует fallback-текст (цепочка не прерывается).

Запуск:
  python week-04/day-20/test_orchestration.py
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agents.mcp_manager import McpServerManager

APOD_FALLBACK = (
    "Title: Milky Way Over Mountains\n"
    "Date: 2025-06-26\n"
    "Description: A stunning view of the Milky Way galaxy arching over "
    "a mountain range. The image captures thousands of stars in remarkable "
    "detail against the dark sky. Photographers spent weeks planning this shot."
)


def _start_server(script_path: str, port: int) -> subprocess.Popen:
    """Запускает MCP-сервер как фоновый процесс, ждёт готовности."""
    proc = subprocess.Popen(
        [sys.executable, script_path, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    return proc


def test_direct_chain():
    """Часть A: прямая цепочка вызовов через McpServerManager (без LLM).

    Проверяет:
    - Подключение к обоим серверам
    - Наличие всех 5 инструментов
    - Последовательный вызов каждого инструмента с передачей результата
    - Сохранение итогового файла
    """
    print("=" * 60)
    print("A. Direct tool chain (McpServerManager)")
    print("=" * 60)

    manager = McpServerManager()
    info1 = manager.connect_server("composer")
    print(f"  composer: {info1}")
    info2 = manager.connect_server("analyzer")
    print(f"  analyzer: {info2}")

    tools = manager.get_openai_tools()
    tool_names = {t["function"]["name"] for t in tools}
    print(f"  Tools ({len(tools)}): {sorted(tool_names)}")

    assert "composer__fetch_data" in tool_names
    assert "composer__summarize_text" in tool_names
    assert "composer__save_to_file" in tool_names
    assert "analyzer__extract_keywords" in tool_names
    assert "analyzer__generate_report" in tool_names

    # Step 1 — fetch_data (APOD)
    print("\n  [1/5] composer__fetch_data('apod')")
    apod = manager.execute_tool("composer__fetch_data", {"source": "apod"})
    if "Title:" not in apod:
        print(f"    NASA unavailable, using fallback ({apod[:80]})")
        apod = APOD_FALLBACK
    title_line = apod.split("\n")[0]
    print(f"    → {title_line}")

    # Step 2 — summarize_text
    print("  [2/5] composer__summarize_text(max_words=50)")
    summary = manager.execute_tool("composer__summarize_text", {
        "text": apod,
        "max_words": 50,
    })
    assert "Title:" in summary, f"Summarize failed: {summary[:100]}"
    wc = len(summary.split())
    print(f"    → {wc} words: {summary[:120]}...")

    # Step 3 — extract_keywords
    print("  [3/5] analyzer__extract_keywords(max_keywords=5)")
    keywords = manager.execute_tool("analyzer__extract_keywords", {
        "text": summary,
        "max_keywords": 5,
    })
    assert "Top" in keywords, f"Keywords failed: {keywords[:100]}"
    for line in keywords.split("\n"):
        s = line.strip()
        if s and (s[0].isdigit() or s.startswith("Top")):
            print(f"    {s}")

    # Step 4 — generate_report
    print("  [4/5] analyzer__generate_report('Space Report')")
    report = manager.execute_tool("analyzer__generate_report", {
        "title": "Space Report",
        "content": f"{summary}\n\n{keywords}",
    })
    assert "# Space Report" in report
    print(f"    → {len(report)} chars")

    # Step 5 — save_to_file
    print("  [5/5] composer__save_to_file('space_report.md')")
    result = manager.execute_tool("composer__save_to_file", {
        "filename": "space_report.md",
        "content": report,
    })
    assert "Saved:" in result
    print(f"    → {result}")

    manager.disconnect_all()
    print("\n  ✓ A: 5/5 direct MCP calls passed")
    return True


def test_llm_orchestration():
    """Часть B: LLM-оркестрация через JarvisAgent.

    Проверяет, что LLM самостоятельно выбирает и последовательно вызывает
    инструменты с двух разных MCP-серверов в рамках одного диалога.
    """
    print("\n" + "=" * 60)
    print("B. LLM-driven orchestration (JarvisAgent)")
    print("=" * 60)

    from agents.jarvis import JarvisAgent

    agent = JarvisAgent(model="Qwen/Qwen3-Coder-Next")
    agent.mcp_enabled = True
    agent.mcp_max_iterations = 15
    statuses = agent.mcp_manager.connect_all_enabled()
    print(f"  Connections: {statuses}")

    # Даём LLM fallback-текст на случай недоступности NASA
    prompt = (
        "Perform a multi-step analysis pipeline. "
        "Step 1: call composer__fetch_data with source='apod' to get APOD data. "
        "If that fails, use this fallback text instead:\n"
        f"{APOD_FALLBACK}\n\n"
        "Step 2: call composer__summarize_text to summarize to 30 words.\n"
        "Step 3: call analyzer__extract_keywords with max_keywords=5.\n"
        "Step 4: call analyzer__generate_report with title 'Space Report'.\n"
        "Step 5: call composer__save_to_file with filename 'space_report.md'.\n\n"
        "Execute all steps and report the results."
    )
    print(f"  Prompt sent ({len(prompt.split())} words)\n")
    response = agent.chat(prompt)

    # Печатаем финальный ответ (обрезаем до 600 символов)
    print(f"  Agent response:")
    for line in response.split("\n")[-15:]:
        print(f"    {line}")

    # Проверяем, что все инструменты из разных серверов вызваны
    # Смотрим conversation_history (command-записи содержат имена инструментов)
    history_str = "\n".join(
        m.get("content", "") for m in agent.conversation_history
    )
    required_tools = [
        "composer__fetch_data",
        "composer__summarize_text",
        "analyzer__extract_keywords",
        "analyzer__generate_report",
        "composer__save_to_file",
    ]
    for tool in required_tools:
        assert tool in history_str, f"Tool {tool} was not called by LLM"
    print("\n  ✓ B: LLM orchestration passed")
    return True


def main():
    project_root = Path(__file__).parent.parent.parent
    composer_script = project_root / "mcp_servers" / "composer_mcp" / "server.py"
    analyzer_script = project_root / "mcp_servers" / "analyzer_mcp" / "server.py"

    print("Starting MCP servers...")
    proc_c = _start_server(str(composer_script), 8767)
    proc_a = _start_server(str(analyzer_script), 8768)
    print("  composer (8767) + analyzer (8768) started\n")

    exit_code = 0
    try:
        test_direct_chain()
        test_llm_orchestration()
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED  ✅")
        print("=" * 60)
    except Exception as e:
        print(f"\n  ❌ {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        proc_c.terminate()
        proc_a.terminate()
        proc_c.wait()
        proc_a.wait()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
