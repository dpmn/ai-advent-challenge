# Runbook: двухуровневый разбор дня 45

Уровень 1 (классификатор) работает на ноутбуке и ничего не стоит. Уровень 2 —
`Qwen3-14B` на арендованной ВМ immers.cloud, поднятый **сервером** дня 43
и проброшенный ssh-туннелем. Наружу порт не смотрит.

Порядок такой, что ВМ арендуется в последний момент: всё, что можно проверить
бесплатно, проверяется до включения счётчика.

---

## 0. До аренды (бесплатно, локально)

```bash
cd week-09/day-45

# обучение классификатора и подбор порога на 400 обучающих примерах
../../.venv/bin/python scripts/classifier.py --train --calibrate --preview

# весь конвейер на заглушке вместо живой модели: ответы из дампа дня 41,
# время ответа подставлено. Проверяется, что скрипт цел, а не метрики.
../../.venv/bin/python scripts/pipeline.py --mock
../../.venv/bin/python scripts/pipeline.py --policy all-big --mock
../../.venv/bin/python scripts/report.py --suffix _mock
```

Заглушка отвечает на главный вопрос до аренды: какая доля товаров вообще дойдёт
до большой модели. Если наверх уходит почти всё, ставить первую ступень
бессмысленно, и это надо знать до того, как включён счётчик.

---

## 1. Поднять ВМ

Конфигурация та же, что в дне 43: **RTX 4090 24 ГБ**, 86,74 ₽/ч, образ голый.
`Qwen3-14B` в 4-bit NF4 занимает около 23 ГБ.

Ключ и логин — как в предыдущие дни (`ubuntu`, ключ из `week-09/day-42/immers/`).

```bash
KEY=../day-42/immers/day-42-072344-dpmn.pem
SSH="ssh -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=no"

scp -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none \
  ../day-43/scripts/bootstrap.sh ubuntu@<IP>:~/
$SSH ubuntu@<IP> 'chmod +x ~/bootstrap.sh && setsid nohup ~/bootstrap.sh </dev/null >~/bootstrap.log 2>&1 &'
```

Следить: `grep -E "^=== " ~/bootstrap.log`. Ориентир по дню 43 — около
2,5 минут от чистого образа до скачанных весов.

---

## 2. Забросить сервер и запустить его

Сервер и промпт берутся из дня 43 без правок — иначе ответы большой модели
нельзя будет сравнивать с прошлыми днями. Раскладка на ВМ плоская.

```bash
scp -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none \
  ../day-43/scripts/serve_big.py ../day-41/scripts/schema.py ../day-41/schema.json \
  ubuntu@<IP>:~/day-45/

$SSH ubuntu@<IP> \
  'cd ~/day-45 && setsid nohup .venv/bin/python serve_big.py --model ~/qwen3-14b \
   </dev/null >~/serve.log 2>&1 &'
```

Готовность: `grep "модель готова" ~/serve.log`. Загрузка 4-bit занимает около минуты.

---

## 3. Туннель

Сервер слушает loopback ВМ. На ноутбук порт приходит туннелем:

```bash
ssh -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none -N -L 8000:127.0.0.1:8000 ubuntu@<IP>
```

Проверка с ноутбука:

```bash
curl -s http://127.0.0.1:8000/health
```

Ожидаемо: JSON с `"ok": true`, `"quantization": "nf4"`, `"thinking": false`.

---

## 4. Прогон в кадре

30 товаров поровну по сложности, живые обращения к большой модели:

```bash
../../.venv/bin/python scripts/pipeline.py --demo
```

По каждому товару печатается ответ классификатора с уверенностью, решение
(«уверен» или «спрашиваем Qwen3-14B»), ответ большой модели и сверка с эталоном.
В конце — сводка с метриками, которые требует задание.

---

## 5. За кадром: полная сотня и опорные замеры

```bash
# два уровня на всех 100 примерах
../../.venv/bin/python scripts/pipeline.py

# верхняя граница: всё уходит к большой модели (100 вызовов)
../../.venv/bin/python scripts/pipeline.py --policy all-big

# нижняя граница: всё решает классификатор (обращений к модели нет)
../../.venv/bin/python scripts/pipeline.py --policy all-small
```

---

## 6. Забрать итог и погасить ВМ

Отчёт считается локально, GPU для него не нужна:

```bash
../../.venv/bin/python scripts/report.py
```

**ВМ гасится сразу после последнего прогона.** Тариф считается за время аренды
целиком, включая простой во время записи видео, поэтому запускать её стоит
непосредственно перед съёмкой.
