#!/bin/bash
# Снимает baseline на нескольких моделях подряд, чтобы выбрать кандидата
# под файнтюн замером, а не по чужим бенчмаркам.
#
# Меняется ТОЛЬКО модель: выборка (picks100.jsonl), промпт, квантование,
# батч и метрики — те же самые. Иначе разницу размера не отделить от
# разницы условий.
#
# Запускать НА ВМ детач-процессом:
#   setsid nohup ~/sweep_models.sh </dev/null >~/sweep.log 2>&1 &
set -u

cd /home/ubuntu/day-41 || exit 1
VENV=/home/ubuntu/day-42/.venv/bin
PY=$VENV/python
# Имя качалки менялось между версиями huggingface_hub: сначала
# huggingface-cli, потом hf. Берём тот, что есть в этом venv.
HF=$VENV/hf
[ -x "$HF" ] || HF=$VENV/huggingface-cli
export HF_XET_HIGH_PERFORMANCE=1

# Порядок от малого к большому: если место или время кончатся, успеют
# отработать самые интересные кандидаты. Qwen3-14B уже снята — она в списке
# последней и переиспользует скачанные веса.
MODELS=${*:-"Qwen/Qwen3-0.6B Qwen/Qwen3-1.7B Qwen/Qwen3-4B Qwen/Qwen3-8B"}

echo "=== SWEEP START $(date -Is) ==="
echo "модели: $MODELS"
df -h / | tail -1

for MODEL in $MODELS; do
  SLUG=$(echo "$MODEL" | tr '/' '_')
  DIR="/home/ubuntu/weights/$SLUG"
  OUT="responses_${SLUG}.jsonl"

  echo "=== MODEL $MODEL START $(date -Is) ==="

  if [ ! -f "$DIR/config.json" ]; then
    echo "--- качаю $MODEL"
    if ! "$HF" download "$MODEL" --local-dir "$DIR"; then
      echo "!!! SKIP $MODEL — скачать не удалось"
      echo "=== MODEL $MODEL FAILED $(date -Is) ==="
      continue
    fi
  else
    echo "--- веса уже на месте: $DIR"
  fi

  echo "--- прогон $MODEL"
  if ! $PY baseline_hf.py --model "$DIR" --picks picks100.jsonl \
       --batch-size 8 --out "$OUT"; then
    echo "!!! FAIL $MODEL — прогон упал"
    echo "=== MODEL $MODEL FAILED $(date -Is) ==="
    continue
  fi

  echo "=== MODEL $MODEL DONE $(date -Is) ==="
  du -sh "$DIR"
done

echo "=== SWEEP DONE $(date -Is) ==="
ls -la /home/ubuntu/day-41/responses_*.jsonl
df -h / | tail -1
