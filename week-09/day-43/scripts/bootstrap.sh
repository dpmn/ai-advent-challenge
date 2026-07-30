#!/bin/bash
# Разворачивает окружение сильной модели на чистой ВМ immers.cloud.
# Запускается детач-процессом (setsid), пишет всё в ~/bootstrap.log.
# Урок дня 41: `nohup ... &` внутри `ssh host 'cmd'` умирает вместе с сессией.
#
# Отличие от дня 42: здесь модель поднимается сервером и живёт, пока идёт
# живой прогон routing-а, — поэтому в конце ничего не считается, только
# готовится окружение.
set -x

echo "=== START $(date -Is) ==="

# python3-dev и build-essential — без них падает сборка нативных расширений.
# ninja-build не нужен: он требовался Triton'у под vLLM, а мы идём
# через transformers напрямую.
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-dev build-essential curl
echo "=== APT DONE $(date -Is) ==="

# uv вместо pip: резолв и скачивание параллельные. В дне 41 pip съел 1,5 часа
# при цене 86,74 ₽/час, в дне 42 uv уложился в 41 секунду.
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
echo "=== UV DONE $(date -Is) ==="

mkdir -p "$HOME/day-43"
cd "$HOME/day-43" || exit 1

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
