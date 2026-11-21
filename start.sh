#!/usr/bin/env bash
# Диалог с помощником в консоли одним запуском: ./start.sh  (добавьте --json, чтобы видеть ответ по контракту)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
command -v python3 >/dev/null || { echo "Нужен Python 3.9 или новее."; exit 1; }
if [ ! -f .env ] && [ -z "${LLM_API_KEY:-}" ]; then
  echo "Нет ключа модели: скопируйте .env.example в .env и впишите ключ, либо задайте переменные LLM_*."
  exit 1
fi
exec python3 chat.py "$@" 2>/dev/null
