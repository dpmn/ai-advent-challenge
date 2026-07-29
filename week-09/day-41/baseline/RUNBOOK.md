# Runbook: снять baseline на immers.cloud

Что делаем: арендуем GPU на полчаса, прогоняем **базовую** `Qwen3-14B` (без тюна)
на 10 примерах из eval, забираем ответы, удаляем машину.

**Ориентир по деньгам: 80–120 ₽, около 2,5 часов.** Тарификация посекундная,
минимальный депозит 100 ₽.

Первая оценка в этом файле была «~25–40 ₽ за полчаса» и оказалась занижена втрое.
Реальность прогона 28.07.2026: **`pip install vllm` тянул зависимости около 1,5 часов**
(torch с CUDA 13 — это гигабайты колёс), скачивание весов ~15 минут, инициализация
движка ~3 минуты, сам замер — секунды. Считать надо по установке, а не по инференсу.

Отсюда практический вывод: **образ с предустановленным PyTorch экономит больше денег,
чем выбор карты подешевле.** Разница между A10 (36,6 ₽/ч) и RTX 4090 (86,7 ₽/ч) —
50 ₽/ч, а полтора часа на установку колёс стоят 55 ₽ на самой дешёвой карте.

> Точных названий кнопок в консоли immers.cloud не привожу — не проверял их лично.
> Логика везде одинаковая: создать ВМ → выбрать GPU и образ → получить IP → зайти по SSH.

---

## 0. Перед арендой (бесплатно, локально)

Убедись, что десятка отбирается и eval на месте:

```bash
cd week-09/day-41
../../.venv/bin/python scripts/baseline.py --dry-run
```

Выведет 10 отобранных названий с пометкой, за какую ловушку каждое взято.
К модели не обращается. Если тут ошибка — чинить надо до аренды, а не на счётчике.

---

## 1. Создать ВМ

- **GPU: A10, 24 ГБ** — 36,6 ₽/ч. Минимальная карта, на которой 14B помещается в 4 битах.
- **Образ:** любой с предустановленными CUDA и PyTorch (ищи «PyTorch», «CUDA», «ML»).
  Голый Ubuntu тоже сгодится, но добавит время на драйверы — то есть денег.
- **Диск:** не меньше 60 ГБ. Веса модели ~28 ГБ плюс кеш HuggingFace.

Записать публичный IP.

---

## 2. Подготовить машину

```bash
ssh -i ~/Downloads/<имя>.pem -o IdentitiesOnly=yes ubuntu@<IP>

sudo apt update
# python3-dev и ninja-build обязательны: без них vLLM падает на инициализации
# движка, см. раздел «Грабли» ниже. Ставить сразу, а не после двух падений.
sudo apt install -y python3-venv python3-dev build-essential ninja-build

mkdir day-41
cd day-41/
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -U vllm transformers accelerate bitsandbytes huggingface_hub

# Скачиваем веса заранее, чтобы видеть прогресс отдельно от запуска сервера
hf download Qwen/Qwen3-14B --local-dir qwen3-14b
```

Если `hf` не найден — та же команда старым именем: `huggingface-cli download ...`.

---

## 3. Поднять сервер

```bash
vllm serve qwen3-14b \
  --served-model-name Qwen/Qwen3-14B \
  --quantization bitsandbytes \
  --max-model-len 4096 \
  --enforce-eager \
  --port 8000
```

Ждать строчку про `Application startup complete`.

`--enforce-eager` здесь не перестраховка, а осознанный выбор. Без него vLLM
запускает `torch.compile`, Triton зовёт gcc собирать CUDA-утилиты и падает
на `fatal error: Python.h: No such file or directory` — заголовков Python
в образе нет, `python3-venv` их не приносит. Лечится либо этим флагом, либо
`sudo apt install -y python3-dev build-essential`. Для десяти запросов
компиляция всё равно не окупается: она экономит время на тысячах, а тут
только съела бы пару минут аренды.

Если vLLM ругается на квантование — в разных версиях флаг `--load-format bitsandbytes`
то обязателен, то выставляется сам. Попробуй с ним и без него.
**Не залипай тут дольше десяти минут** — переходи к запасному пути (шаг 6),
он не требует сервера вообще.

Признак, что квантование в порядке: в логе `Model loading took ~10 GiB`.
Если модель загрузилась, а упало позже — дело не в bitsandbytes.

---

## 4. Пробросить порт и снять baseline

В отдельном терминале **на своей машине**:

```bash
ssh -i ~/Downloads/<имя>.pem -N -L 8000:127.0.0.1:8000 ubuntu@<IP>
```

Ещё в одном, тоже локально:

```bash
cd week-09/day-41/

python scripts/baseline.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model Qwen/Qwen3-14B
```

Результат ляжет в `baseline/responses.jsonl`.

---

## 5. Посчитать метрики и погасить машину

```bash
python scripts/score.py \
  --predictions baseline/responses.jsonl \
  --out baseline/report.md \
  --title "Baseline: Qwen3-14B без тюна (4-bit NF4)"
```

**Сразу после этого удали ВМ в консоли.** Остановка не всегда снимает плату за диск.

---

## 6. Запасной путь, если vLLM не завёлся

Считаем прямо на ВМ через transformers, без сервера.

Локально выгрузить ту же десятку и отправить наверх:

```bash
../../.venv/bin/python scripts/baseline.py --dry-run --dump-picks baseline/picks.jsonl

scp -i ~/Downloads/<имя>.pem scripts/baseline_hf.py scripts/schema.py schema.json \
  baseline/picks.jsonl ubuntu@<IP>:~/day-41/
```

Выбор примеров детерминирован сидом, так что десятка та же, что пошла бы через vLLM.

На ВМ:

```bash
pip install transformers accelerate bitsandbytes
cd ~/day-41 && .venv/bin/python baseline_hf.py --model ~/day-41/qwen3-14b \
  --picks picks.jsonl --out responses.jsonl
```

Забрать и посчитать локально:

```bash
scp -i ~/Downloads/<имя>.pem ubuntu@<IP>:~/day-41/responses.jsonl baseline/responses.jsonl
../../.venv/bin/python scripts/score.py --predictions baseline/responses.jsonl \
  --out baseline/report.md --title "Baseline: Qwen3-14B без тюна (4-bit NF4)"
```

---

## Грабли (пройдены на живой ВМ 28.07.2026)

Всё это встретилось подряд на чистом образе. Порядок именно такой.

**1. `fatal error: Python.h: No such file or directory`**
Triton компилирует CUDA-утилиты через gcc, а заголовков Python в образе нет.
`python3-venv` их не приносит — нужен `python3-dev`.
Лечится: `sudo apt install -y python3-dev build-essential`.

**2. `--enforce-eager` эту ошибку НЕ лечит.**
Логично было бы: нет компиляции — нет Triton. Но Triton-драйвер инициализируется
при настройке сэмплера, независимо от компиляции графа. В логе это видно по строке
`Using FlashInfer for top-p & top-k sampling` перед падением. Флаг всё равно полезен
(экономит минуты на прогоне из 10 запросов), но проблему не снимает.

**3. `FileNotFoundError: [Errno 2] No such file or directory: 'ninja'`**
Следующий слой: Triton зовёт `ninja` для сборки. Ставить **системно**
(`sudo apt install -y ninja-build`), а не через `pip install ninja` в venv —
процесс, запущенный без активации venv, не увидит `.venv/bin/ninja` в `PATH`.

**4. `pkill -f "vllm serve"` убивает собственную SSH-сессию.**
Командная строка обёртки `bash -c` содержит ту же подстроку, и pkill попадает
по себе. Симптом: ssh молча обрывается, ничего не запускается.
Обходится классом символов: `pkill -f "[v]llm serve"`.

**5. Фоновый процесс не переживает закрытия SSH.**
`nohup ... &` внутри `ssh host 'команда'` умирает вместе с сессией.
Надёжно — положить запуск в скрипт и звать его:

```bash
cat > start.sh <<'EOF'
#!/bin/bash
export PATH=/home/ubuntu/day-41/.venv/bin:$PATH
cd /home/ubuntu/day-41
setsid nohup .venv/bin/vllm serve qwen3-14b \
  --served-model-name Qwen/Qwen3-14B --quantization bitsandbytes \
  --max-model-len 4096 --enforce-eager --port 8000 \
  </dev/null >/home/ubuntu/vllm.log 2>&1 &
echo "pid=$!"
EOF
chmod +x start.sh && ./start.sh
```

**Инициализация занимает около трёх минут** после старта процесса. Не считать
молчание падением: следить за `Application startup complete` в `~/vllm.log`.

**Память на A10 сходится с запасом:** веса 9,98 ГБ, KV-кэш 9,78 ГБ, пик активаций
0,47 ГБ из 22 ГБ доступных. Оперативки 15 ГБ при чекпоинте 28 ГБ — автопрефетч
отключается, но загрузка проходит.

---

## Главная грабля: режим размышлений (найдена 29.07.2026)

**`Qwen3-14B` — reasoning-модель.** Её chat-шаблон по умолчанию включает режим
размышлений, и модель выдаёт `<think>`-блок вместо ответа. `vllm serve` применяет
шаблон именно так, поэтому первый baseline показал 0% чистого JSON и 1865 символов
на ответ — и это записали как свойство модели.

Свойством это не было. Контрольный замер на тех же 10 примерах, одной машине
и одном стеке, где отличается только флаг, дал 100% чистого JSON и 163 символа.

При снятии baseline через vLLM режим размышлений надо гасить явно — иначе
меряется настройка шаблона, а не модель. Через `transformers` это
`apply_chat_template(..., enable_thinking=False)`, как в `baseline_hf.py`.
Через OpenAI-совместимый API vLLM — `chat_template_kwargs`:

```json
{"chat_template_kwargs": {"enable_thinking": false}}
```

Точное имя параметра зависит от версии vLLM — **проверять на одном запросе
до полного прогона**, а не после. Признак, что режим не погашен: ответ начинается
с `<think>`.

Общий урок шире этой задачи: замер, снятый одним стеком, нельзя выдавать за
свойство модели, пока не проверена конфигурация стека. Дешевле всего это ловится
контрольным прогоном двух веток подряд — здесь он занял пять минут и 7 ₽.

---

## Почему именно 4-bit NF4

Тюн пойдёт через QLoRA, то есть базовая модель там будет
квантована в NF4. Если baseline снять в bf16, а «после» мерить в 4 битах,
разницу от обучения будет не отделить от разницы квантования. Обе точки
замера держим в одном формате — он же и реальный деплой-таргет.
