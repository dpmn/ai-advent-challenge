#!/usr/bin/env bash
# Разворачивает арендованную ВМ под прогон дня 44: Ollama и две модели.
#
# Отличие от дней 42–43 принципиальное: там на ВМ поднималась большая модель
# через transformers, здесь нужна ровно та же связка, что на ноутбуке, —
# Ollama с Q4_K_M. Менять рантайм нельзя: сравнение рук должно мерить
# декомпозицию, а не разницу квантований.
#
# Запуск на ВМ:
#   chmod +x ~/bootstrap.sh && setsid nohup ~/bootstrap.sh </dev/null >~/bootstrap.log 2>&1 &
# Следить:
#   grep -E "^=== " ~/bootstrap.log

set -euo pipefail

MODELS=("qwen3:0.6b" "qwen3:4b")
DAY_DIR="$HOME/day-44"

echo "=== 1. системные пакеты"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl python3 >/dev/null

echo "=== 2. установка Ollama"
if ! command -v ollama >/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
ollama --version

echo "=== 3. запуск сервера"
# Через setsid, а не systemd: на голых образах immers служба может не подняться,
# а зависимость от systemd тут не нужна — сервер живёт ровно на время прогона.
if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null; then
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_KEEP_ALIVE=60m \
        setsid nohup ollama serve </dev/null >"$HOME/ollama.log" 2>&1 &
fi
for _ in $(seq 1 60); do
    curl -sf --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null && break
    sleep 2
done
curl -sf --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null \
    || { echo "Ollama не поднялась, смотри ~/ollama.log"; exit 1; }

echo "=== 4. модели"
for model in "${MODELS[@]}"; do
    ollama pull "$model"
done
ollama list

echo "=== 5. каталоги"
mkdir -p "$DAY_DIR/results"

echo "=== 6. проверка GPU"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || echo "nvidia-smi недоступна"

echo "=== готово"
