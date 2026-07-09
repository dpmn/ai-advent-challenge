"""Бенчмарк локального RAG-пути (day-29): baseline vs optimized, сравнение квантов.

Гоняет фиксированный набор вопросов к базе знаний через локальный RAG
(индекс ragger/data_local/, эмбеддер nomic-embed-text) и сравнивает профили:

  - baseline  — путь day-28 как есть: OpenAI-совместимый /v1, полный промпт,
                чанки по 1200 символов, num_ctx серверный (32768);
  - optimized — нативный /api/chat: num_ctx 8192, temperature 0.0,
                компактный промпт под 7B, чанки по 1600 символов, keep_alive.

Запуск:
  python3 week-06/day-29/bench.py                            # обе конфигурации, дефолтная модель
  python3 week-06/day-29/bench.py --models qwen2.5-coder:7b,qwen2.5-coder:7b-instruct-q3_K_M
  python3 week-06/day-29/bench.py --profiles optimized --questions 2

Результаты: JSON в week-06/day-29/results/ + сводная таблица в stdout.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from ragger.answer import generate_answer
from ragger.ollama_client import ollama_chat
from ragger.search import DATA_DIR_LOCAL, RagPipeline

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Вопросы 1–5 покрыты локальным индексом (5-й — схемой БД: docs/database-schema.md
# добавлен в индекс в day-29). Последний — вне базы знаний:
# ожидаемый результат — честное "Я не знаю" (confidence=none).
QUESTIONS = [
    "Какие стратегии чанкинга используются в RAG-пайплайне?",
    "Какие режимы работы поддерживает RAG-пайплайн и чем они отличаются?",
    "Что такое MCP и какие MCP-серверы есть в проекте?",
    "Какие этапы проходит State Machine агента?",
    "Какие таблицы есть в SQLite базе данных jarvis_history и что в них хранится?",
    "Как задеплоить проект в Kubernetes?",
]

# Профиль optimized повторяет JarvisAgent._local_llm_profile() (день 29).
OPTIMIZED_PROFILE = {
    "transport": "ollama",
    "keep_alive": "30m",
    "gen_options": {"num_ctx": 8192, "temperature": 0.0, "num_predict": 1024},
    "verify_options": {"num_ctx": 8192, "temperature": 0.0, "num_predict": 5},
    "rerank_options": {"num_ctx": 8192, "temperature": 0.0, "num_predict": 256},
    "chunk_char_limit": 1600,
    "prompt_style": "compact",
}

PROFILES = {"baseline": None, "optimized": OPTIMIZED_PROFILE}


def warmup(model: str, profile: dict | None) -> dict:
    """Прогревает модель перед серией замеров, возвращает метрики загрузки.

    Загрузка модели (в т.ч. перезагрузка при смене num_ctx между профилями)
    происходит здесь, а не внутри первого замера.
    """
    options = None
    if profile:
        options = {"num_ctx": profile["gen_options"]["num_ctx"], "num_predict": 4}
    t0 = time.monotonic()
    _, metrics = ollama_chat("Скажи: ок", model=model, base_url=BASE_URL,
                             options=options, keep_alive="30m", timeout=300)
    metrics["wall_s"] = round(time.monotonic() - t0, 2)
    return metrics


def run_question(question: str, model: str, profile: dict | None, mode: str) -> dict:
    """Один замер: RAG-пайплайн + генерация ответа, возвращает метрики и ответ."""
    pipeline = RagPipeline(
        api_key="ollama",
        top_k_before=15,
        top_k_after=8,
        threshold=0.2,
        mode=mode,
        rerank_model=model,
        base_url=BASE_URL,
        data_dir=DATA_DIR_LOCAL,
        embed_api_key="ollama",
        embed_model="nomic-embed-text",
        embed_base_url=BASE_URL,
        embed_prefix="search_query: ",
        llm_profile=profile,
    )
    chunks = pipeline.run(question)

    t0 = time.monotonic()
    answer = generate_answer(
        query=question,
        chunks=chunks,
        api_key="ollama",
        model=model,
        base_url=BASE_URL,
        verify_model=model,
        llm_profile=profile,
    )
    generate_wall_s = time.monotonic() - t0

    timings = {k: round(v, 2) for k, v in pipeline._last_timings.items()}
    timings["generate_s"] = round(generate_wall_s, 2)
    return {
        "question": question,
        "chunks": len(chunks),
        "timings": timings,
        "total_s": round(sum(timings.values()), 2),
        "confidence": answer.confidence,
        "n_sources": len(answer.sources),
        "answer_len": len(answer.answer),
        "llm_metrics": answer.llm_metrics,
        "answer": answer.answer,
        "sources": answer.sources,
    }


def run_block(model: str, profile_name: str, questions: list[str], mode: str) -> dict:
    """Серия замеров для пары (модель, профиль): warmup + все вопросы."""
    profile = PROFILES[profile_name]
    print(f"\n=== {model} · {profile_name} · mode={mode} ===")
    print("warmup...", end=" ", flush=True)
    wm = warmup(model, profile)
    print(f"load {wm['load_s']}s, wall {wm['wall_s']}s")

    runs = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q[:60]}...", end=" ", flush=True)
        try:
            r = run_question(q, model, profile, mode)
        except Exception as e:
            print(f"FAILED: {e}")
            runs.append({"question": q, "error": str(e)})
            continue
        gen = (r["llm_metrics"] or {}).get("generate") or {}
        tok_s = f" · {gen['tok_s']} tok/s" if gen else ""
        print(f"{r['total_s']}s · confidence={r['confidence']} "
              f"· sources={r['n_sources']}{tok_s}")
        runs.append(r)

    return {
        "model": model,
        "profile": profile_name,
        "mode": mode,
        "base_url": BASE_URL,
        "warmup": wm,
        "runs": runs,
    }


def summarize(blocks: list[dict]) -> str:
    """Сводная таблица по блокам: среднее время, tok/s, confidence, источники."""
    lines = [
        "| model | profile | avg total, s | avg generate, s | avg tok/s | "
        "confidence | avg sources |",
        "|---|---|---|---|---|---|---|",
    ]
    for b in blocks:
        ok = [r for r in b["runs"] if "error" not in r]
        if not ok:
            lines.append(f"| {b['model']} | {b['profile']} | — | — | — | все упали | — |")
            continue
        avg_total = sum(r["total_s"] for r in ok) / len(ok)
        avg_gen = sum(r["timings"]["generate_s"] for r in ok) / len(ok)
        toks = [((r["llm_metrics"] or {}).get("generate") or {}).get("tok_s")
                for r in ok]
        toks = [t for t in toks if t]
        avg_tok = f"{sum(toks) / len(toks):.1f}" if toks else "—"
        conf = "/".join(r["confidence"] for r in ok)
        avg_src = sum(r["n_sources"] for r in ok) / len(ok)
        lines.append(
            f"| {b['model']} | {b['profile']} | {avg_total:.1f} | {avg_gen:.1f} "
            f"| {avg_tok} | {conf} | {avg_src:.1f} |"
        )
    return "\n".join(lines)


def main() -> None:
    """CLI: парсит аргументы, гоняет блоки замеров, пишет JSON и сводку."""
    parser = argparse.ArgumentParser(description="RAG bench day-29")
    parser.add_argument("--models", default="qwen2.5-coder:7b",
                        help="Модели Ollama через запятую")
    parser.add_argument("--profiles", default="baseline,optimized",
                        help="Профили через запятую: baseline,optimized")
    parser.add_argument("--questions", type=int, default=len(QUESTIONS),
                        help="Сколько вопросов гонять (с начала списка)")
    parser.add_argument("--mode", default="threshold",
                        choices=["threshold", "rerank", "hybrid"],
                        help="Режим RagPipeline")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    for p in profiles:
        if p not in PROFILES:
            sys.exit(f"Неизвестный профиль: {p} (доступны: {list(PROFILES)})")
    questions = QUESTIONS[: args.questions]

    blocks = []
    for model in models:
        for profile_name in profiles:
            blocks.append(run_block(model, profile_name, questions, args.mode))

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"bench-{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=2)

    print(f"\nРезультаты: {out}\n")
    print(summarize(blocks))


if __name__ == "__main__":
    main()
