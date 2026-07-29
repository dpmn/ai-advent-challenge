#!/bin/bash
# Разворачивает окружение для прогона дня 42 на чистой ВМ.
# Запускается детач-процессом (setsid), пишет всё в ~/bootstrap.log.
# Урок дня 41: `nohup ... &` внутри `ssh host 'cmd'` умирает вместе с сессией.
set -x

echo "=== START $(date -Is) ==="

# python3-dev и build-essential — без них падает сборка нативных расширений.
# ninja-build здесь не нужен: он был нужен Triton'у под vLLM, а мы идём
# через transformers напрямую.
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-dev build-essential curl
echo "=== APT DONE $(date -Is) ==="

# uv вместо pip: резолв и скачивание параллельные, на колёсах масштаба torch
# разница измеряется десятками минут, а стоит машина 86,74 ₽/час.
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
echo "=== UV DONE $(date -Is) ==="

mkdir -p "$HOME/day-42"
cd "$HOME/day-42" || exit 1

uv venv --python 3.12
# hf_transfer — многопоточная качалка для HF, веса 14B это ~28 ГБ.
uv pip install torch transformers accelerate bitsandbytes huggingface_hub hf_transfer
echo "=== PIP DONE $(date -Is) ==="

.venv/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
echo "=== TORCH CHECK DONE $(date -Is) ==="

export HF_HUB_ENABLE_HF_TRANSFER=1
.venv/bin/hf download Qwen/Qwen3-14B --local-dir "$HOME/qwen3-14b"
echo "=== WEIGHTS DONE $(date -Is) ==="

du -sh "$HOME/qwen3-14b"
echo "=== ALL DONE $(date -Is) ==="
