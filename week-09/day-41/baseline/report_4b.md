# Baseline цели тюна: Qwen3-4B (4-bit NF4, enable_thinking=False, n=100)

Примеров: **100**

| # | метрика | значение |
|---|---|---|
| 1 | JSON-валидность (чистый ответ) | 100.0% |
|   | JSON-валидность (с извлечением из обёрток) | 100.0% |
| 2 | Schema compliance | 100.0% |
| 3 | Vocab compliance | 80.0% |
| 4 | Per-field exact match (среднее) | 81.1% |
| 5 | Unit normalization (n=20) | 90.0% |
| 6 | Hallucination rate | 1.0% |
| 7 | Средняя длина ответа | 166 символов |

## Per-field exact match по полям

| поле | точность |
|---|---|
| `purpose` | 62.0% |
| `form` | 67.0% |
| `category` | 73.0% |
| `line` | 73.0% |
| `brand` | 81.0% |
| `shade` | 91.0% |
| `is_set` | 91.0% |
| `volume` | 96.0% |
| `pack_count` | 96.0% |

## Точность `volume` по классам сложности

| класс | точность |
|---|---|
| `truncated` | 75.0% |
| `dirty_unit` | 80.0% |
| `shade_code` | 83.3% |
| `set_like` | 90.0% |
| `multi_volume` | 100.0% |
| `pack` | 100.0% |
| `quoted` | 100.0% |
| `simple` | 100.0% |
