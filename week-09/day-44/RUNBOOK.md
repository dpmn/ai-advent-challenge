# Runbook: прогон рук дня 44 на арендованной ВМ

Все четыре руки гоняются на одной машине одной моделью — иначе сравнение мерило бы
не декомпозицию, а разницу железа. Ноутбук 1800 вызовов параллельно с работой
не тянет, поэтому прогон уезжает на immers.cloud.

Модель та же, что локально: `qwen3:0.6b`, Ollama, Q4_K_M. Рантайм не меняется.

---

## 0. До аренды (бесплатно, локально)

```bash
cd week-09/day-44

# арифметика отчёта на синтетике: настоящие ответы 14B из дня 41,
# выдуманный расход. Проверяется, что проценты и дельты считаются.
../../.venv/bin/python scripts/mock_run.py
../../.venv/bin/python scripts/report.py --suffix _mock
```

Ожидаемо: у руки, где испорчено каждое второе поле, пополевая точность ≈ половина
от потолка 14B (84,9%); у руки с каждым пятым — четыре пятых от него.

---

## 1. Поднять ВМ

**Много GPU не нужно.** `qwen3:0.6b` в Q4_K_M занимает 0,5 ГБ, `qwen3:4b` — 2,5 ГБ.
Против дней 42–43, где 14B требовала 24 ГБ, здесь хватит самой дешёвой карты
с 8 ГБ. Тариф в отчёте по умолчанию берётся как в дне 43 (86,74 ₽/ч) — если карта
другая, передай фактический тариф флагом `--rub-per-hour`.

Ориентир по времени: bootstrap ~3 минуты, полный прогон четырёх рук ~10–20 минут.

---

## 2. Забросить bootstrap и развернуть машину

```bash
scp -i immers/<ключ>.pem -o IdentitiesOnly=yes scripts/bootstrap.sh ubuntu@<IP>:~/
ssh -i immers/<ключ>.pem -o IdentitiesOnly=yes ubuntu@<IP> \
  'chmod +x ~/bootstrap.sh && setsid nohup ~/bootstrap.sh </dev/null >~/bootstrap.log 2>&1 &'
```

Следить: `grep -E "^=== " ~/bootstrap.log`. Готовность — строка `=== готово`.

---

## 3. Забросить код и данные

Раскладка на ВМ плоская: `common.py` ищет схему, датасеты и дамп сначала рядом
с собой, потом по дням.

```bash
scp -i immers/<ключ>.pem -o IdentitiesOnly=yes \
  scripts/common.py scripts/llm.py scripts/stages.py scripts/run.py scripts/report.py \
  ../day-41/scripts/schema.py ../day-41/scripts/score.py ../day-41/schema.json \
  ../day-41/datasets/eval.jsonl ../day-41/datasets/train.jsonl \
  ../day-41/baseline/responses100.jsonl \
  ubuntu@<IP>:~/day-44/
```

Проверка раскладки одной командой (моделей не трогает):

```bash
ssh -i immers/<ключ>.pem -o IdentitiesOnly=yes ubuntu@<IP> \
  'cd ~/day-44 && python3 -c "import sys; sys.path.insert(0,\".\"); import common; \
   print(len(common.load_eval()), common.EVAL_PATH, common.RESULTS_DIR)"'
```

Ожидаемо: `100`, оба пути внутри `~/day-44`.

---

## 4. Прогон

Сначала смоук на трёх примерах — он ловит опечатки в раскладке до того, как
включён счётчик на сорок минут:

```bash
ssh -i immers/<ключ>.pem -o IdentitiesOnly=yes ubuntu@<IP> \
  'cd ~/day-44 && python3 run.py --arm multi --limit 3 --suffix _smoke'
```

Полный прогон всех четырёх рук по 100 примерам eval:

```bash
ssh -i immers/<ключ>.pem -o IdentitiesOnly=yes ubuntu@<IP> \
  'cd ~/day-44 && setsid nohup python3 run.py --quiet </dev/null >~/run.log 2>&1 &'
```

Следить: `tail -f ~/run.log`. Порядок рук: `mono`, `multi`, `multi-lite`, `mono-x4`.

---

## 5. Забрать результаты и погасить ВМ

```bash
scp -i immers/<ключ>.pem -o IdentitiesOnly=yes \
  'ubuntu@<IP>:~/day-44/results/*' results/
scp -i immers/<ключ>.pem -o IdentitiesOnly=yes ubuntu@<IP>:~/run.log results/run.log
```

**ВМ гасится сразу после копирования.** Отчёт считается локально и офлайн:

```bash
../../.venv/bin/python scripts/report.py --rub-per-hour 86.74
```

Пересобрать отчёт по другому подмножеству рук можно в любой момент, GPU для этого
не нужна — все ответы уже сняты:

```bash
../../.venv/bin/python scripts/report.py --arms mono,multi --out results/report_ab.md
```
