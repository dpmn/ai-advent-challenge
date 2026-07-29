# Baseline: Qwen3-14B без тюна (4-bit NF4)

Примеров: **10**

| # | метрика | значение |
|---|---|---|
| 1 | JSON-валидность (чистый ответ) | 0.0% |
|   | JSON-валидность (с извлечением из обёрток) | 60.0% |
| 2 | Schema compliance | 60.0% |
| 3 | Vocab compliance | 50.0% |
| 4 | Per-field exact match (среднее) | 53.3% |
| 5 | Unit normalization (n=5) | 40.0% |
| 6 | Hallucination rate | 0.0% |
| 7 | Средняя длина ответа | 1865 символов |

## Per-field exact match по полям

| поле | точность |
|---|---|
| `line` | 40.0% |
| `form` | 40.0% |
| `purpose` | 50.0% |
| `shade` | 50.0% |
| `category` | 60.0% |
| `brand` | 60.0% |
| `volume` | 60.0% |
| `pack_count` | 60.0% |
| `is_set` | 60.0% |

## Точность `volume` по классам сложности

| класс | точность |
|---|---|
| `dirty_unit` | 0.0% |
| `truncated` | 0.0% |
| `multi_volume` | 50.0% |
| `set_like` | 50.0% |
| `shade_code` | 50.0% |
| `pack` | 66.7% |
| `quoted` | 100.0% |
| `simple` | 100.0% |
