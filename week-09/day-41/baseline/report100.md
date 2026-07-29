# Baseline на всём eval: Qwen3-14B без тюна (4-bit NF4, enable_thinking=False, n=100)

Примеров: **100**

| # | метрика | значение |
|---|---|---|
| 1 | JSON-валидность (чистый ответ) | 100.0% |
|   | JSON-валидность (с извлечением из обёрток) | 100.0% |
| 2 | Schema compliance | 100.0% |
| 3 | Vocab compliance | 85.0% |
| 4 | Per-field exact match (среднее) | 84.9% |
| 5 | Unit normalization (n=20) | 95.0% |
| 6 | Hallucination rate | 0.0% |
| 7 | Средняя длина ответа | 162 символов |

## Per-field exact match по полям

| поле | точность |
|---|---|
| `form` | 67.0% |
| `purpose` | 71.0% |
| `line` | 80.0% |
| `brand` | 83.0% |
| `category` | 85.0% |
| `shade` | 91.0% |
| `is_set` | 94.0% |
| `pack_count` | 96.0% |
| `volume` | 97.0% |

## Точность `volume` по классам сложности

| класс | точность |
|---|---|
| `truncated` | 75.0% |
| `shade_code` | 83.3% |
| `set_like` | 85.0% |
| `multi_volume` | 90.9% |
| `dirty_unit` | 100.0% |
| `pack` | 100.0% |
| `quoted` | 100.0% |
| `simple` | 100.0% |
