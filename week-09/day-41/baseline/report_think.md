# Контроль: та же машина, enable_thinking=True

Примеров: **10**

| # | метрика | значение |
|---|---|---|
| 1 | JSON-валидность (чистый ответ) | 0.0% |
|   | JSON-валидность (с извлечением из обёрток) | 70.0% |
| 2 | Schema compliance | 70.0% |
| 3 | Vocab compliance | 60.0% |
| 4 | Per-field exact match (среднее) | 58.9% |
| 5 | Unit normalization (n=5) | 60.0% |
| 6 | Hallucination rate | 0.0% |
| 7 | Средняя длина ответа | 2214 символов |

## Per-field exact match по полям

| поле | точность |
|---|---|
| `category` | 50.0% |
| `line` | 50.0% |
| `form` | 50.0% |
| `purpose` | 50.0% |
| `brand` | 60.0% |
| `shade` | 60.0% |
| `volume` | 70.0% |
| `pack_count` | 70.0% |
| `is_set` | 70.0% |

## Точность `volume` по классам сложности

| класс | точность |
|---|---|
| `dirty_unit` | 0.0% |
| `set_like` | 50.0% |
| `shade_code` | 50.0% |
| `multi_volume` | 75.0% |
| `pack` | 83.3% |
| `quoted` | 100.0% |
| `simple` | 100.0% |
| `truncated` | 100.0% |
