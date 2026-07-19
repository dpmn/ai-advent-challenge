#!/usr/bin/env bash
# PreToolUse/Bash hook: блокирует `git commit`, если в git-индексе есть секреты.
# Гарантия структурная (программная проверка индекса), а не промпт-запрет —
# принцип проекта, урок PR #23 (см. skill docent-rules). Маски секретов держим
# в синхроне с docent files.py и .gitignore.
set -uo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

# Реагируем только на коммиты (ловим и компаунды: `git add . && git commit`).
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

# Что реально в индексе. Вне git-репо / пустой индекс — пропускаем.
staged=$(git diff --cached --name-only 2>/dev/null) || exit 0
[ -z "$staged" ] && exit 0

bad=""
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$(basename "$f")" in
    .env|.env.*|*.key|*.pem|*.p12|*.pfx|id_rsa|id_rsa.*|credentials.json)
      bad="${bad}  - ${f}"$'\n' ;;
  esac
done <<< "$staged"

[ -z "$bad" ] && exit 0

reason="Коммит заблокирован (guard-secrets): в git-индексе секреты:"$'\n'"${bad}Убери их из индекса: git restore --staged <файл>, и проверь .gitignore."
jq -n --arg r "$reason" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
