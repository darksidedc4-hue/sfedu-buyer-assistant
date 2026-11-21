#!/usr/bin/env bash
# Показ в браузере: ./web.sh, затем откройте http://127.0.0.1:8080  (порт и каталог: --port, --catalog)
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo "Нужен Python 3.9 или новее."; exit 1; }
if [ ! -f .env ] && [ -z "${LLM_API_KEY:-}" ]; then
  echo "Нет ключа модели: скопируйте .env.example в .env и впишите ключ, либо задайте переменные LLM_*."
  exit 1
fi
exec python3 server/serve.py "$@"
