"""
Учёт стоимости запросов LLM Gateway (день 48).

Прайс не зашивается в код: Cloud.ru отдаёт цены прямо в `/v1/models`, в поле
`metadata` каждой модели (`prompt_tokens_cost`, `generated_tokens_cost`).
Гейтвей забирает их при старте и кладёт в `gateway/pricing.json` — дальше файл
работает как кеш на случай, если апстрим недоступен.

Единица измерения: рубли за 1 000 000 токенов. В документации Cloud.ru прямого
подтверждения единицы найти не удалось, значение выведено по порядку величины
(Qwen3-30B-A3B — 13.9, Claude Opus — 2946 при известном соотношении цен этих
моделей). Поэтому в ответе и в логе рядом со стоимостью всегда едет поле
`price_source`: видно, посчитано по живому прайсу, по кешу или не посчитано вовсе.
Ollama считается бесплатной — она крутится локально.
"""

import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

_GATEWAY_DIR = Path(__file__).parent.resolve()
_PRICING_FILE = _GATEWAY_DIR / "pricing.json"

# Цены в прайсе Cloud.ru даны за такое количество токенов.
TOKENS_PER_PRICE_UNIT = 1_000_000


class Pricing:
    """Прайс моделей: загрузка из API апстрима с кешем в файле."""

    def __init__(self, pricing_file: Optional[Path] = None):
        self.path = Path(pricing_file) if pricing_file else _PRICING_FILE
        self.models: dict[str, dict] = {}
        self.source = "empty"
        self.fetched_at: Optional[str] = None

    def load_cached(self) -> bool:
        """Читает прайс из файла-кеша. Возвращает True, если что-то прочитано."""
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[GATEWAY][COST] Не удалось прочитать {self.path.name}: {e}")
            return False
        self.models = data.get("models", {})
        self.fetched_at = data.get("fetched_at")
        self.source = "cache" if self.models else "empty"
        return bool(self.models)

    def refresh(self, base_url: str, api_key: str, timeout: int = 20) -> bool:
        """Тянет актуальные цены из `GET {base_url}/models` и пишет кеш."""
        if not api_key:
            return False
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[GATEWAY][COST] Прайс не получен из API: {e}")
            return False

        models: dict[str, dict] = {}
        for item in payload.get("data", []):
            meta = item.get("metadata") or {}
            prompt_cost = meta.get("prompt_tokens_cost")
            output_cost = meta.get("generated_tokens_cost")
            if prompt_cost is None and output_cost is None:
                continue
            models[item["id"]] = {
                "prompt": float(prompt_cost or 0),
                "completion": float(output_cost or 0),
            }
        if not models:
            return False

        self.models = models
        self.source = "api"
        self.fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save()
        return True

    def _save(self) -> None:
        """Пишет прайс в файл-кеш. Ошибки записи не пробрасывает."""
        data = {
            "unit": "RUB per 1M tokens (единица выведена по порядку величины, "
                    "документацией Cloud.ru не подтверждена)",
            "fetched_at": self.fetched_at,
            "models": self.models,
        }
        try:
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            print(f"[GATEWAY][COST] Не удалось сохранить прайс: {e}")

    def price_of(self, model: str) -> Optional[dict]:
        """Возвращает цены модели или None, если модели нет в прайсе."""
        return self.models.get(model)


def estimate(pricing: Pricing, model: str, usage: dict, upstream: str = "cloud") -> dict:
    """Считает стоимость запроса по usage из ответа апстрима.

    Args:
        pricing: загруженный прайс.
        model: идентификатор модели.
        usage: блок usage ответа (prompt_tokens, completion_tokens, total_tokens).
        upstream: "cloud" или "local"; локальные модели считаются бесплатными.

    Returns:
        dict с токенами, стоимостью в рублях и источником цены.
    """
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or (prompt_tokens + completion_tokens))

    result = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_rub": 0.0,
        "price_source": pricing.source,
        "currency": "RUB",
    }

    if upstream == "local":
        result["price_source"] = "local (бесплатно)"
        return result

    price = pricing.price_of(model)
    if not price:
        result["price_source"] = "нет в прайсе"
        return result

    cost = (
        prompt_tokens * price["prompt"] + completion_tokens * price["completion"]
    ) / TOKENS_PER_PRICE_UNIT
    result["cost_rub"] = round(cost, 6)
    result["price_per_1m"] = price
    return result
