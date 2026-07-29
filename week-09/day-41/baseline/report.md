# Baseline: Qwen3-14B без тюна (4-bit NF4, enable_thinking=False)

Примеров: **10**

| # | метрика | значение |
|---|---|---|
| 1 | JSON-валидность (чистый ответ) | 100.0% |
|   | JSON-валидность (с извлечением из обёрток) | 100.0% |
| 2 | Schema compliance | 100.0% |
| 3 | Vocab compliance | 90.0% |
| 4 | Per-field exact match (среднее) | 76.7% |
| 5 | Unit normalization (n=5) | 100.0% |
| 6 | Hallucination rate | 0.0% |
| 7 | Средняя длина ответа | 163 символов |

## Per-field exact match по полям

| поле | точность |
|---|---|
| `line` | 50.0% |
| `purpose` | 60.0% |
| `brand` | 70.0% |
| `form` | 70.0% |
| `category` | 80.0% |
| `shade` | 80.0% |
| `is_set` | 80.0% |
| `volume` | 100.0% |
| `pack_count` | 100.0% |

## Точность `volume` по классам сложности

| класс | точность |
|---|---|
| `dirty_unit` | 100.0% |
| `multi_volume` | 100.0% |
| `pack` | 100.0% |
| `quoted` | 100.0% |
| `set_like` | 100.0% |
| `shade_code` | 100.0% |
| `simple` | 100.0% |
| `truncated` | 100.0% |
