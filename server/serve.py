"""Сервер показа: отдаёт веб-страницу из папки client и отвечает на её запросы тем же решением, что и ./run.

    python3 server/serve.py                  # затем откройте http://127.0.0.1:8080
    python3 server/serve.py --port 9000 --catalog /путь/catalog.json

Нужны только Python 3.9+ и ключ модели (файл .env в корне или переменные LLM_*, как для ./run). Сторонних
библиотек нет. Сервер нужен для показа диалога в браузере; проверка по контракту идёт через ./run и от него
не зависит. Страница обращается к одному адресу: POST /app/ext/avito/buyer/open с историей диалога.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant import main as app  # noqa: E402
from assistant.llm import LLMError  # noqa: E402

CLIENT = ROOT / "client"
MAX_BODY = 200_000          # байт на запрос: 20 реплик с запасом
TIME_LIMIT = 100            # секунд на ответ, как у ./run


def reply(messages: list, catalog: list) -> dict:
    """Ответ странице: результат по контракту, карточки выбранных товаров и то, как понят запрос."""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20 or not all(
            isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str) and m["text"].strip()
            for m in messages):
        raise ValueError("Нужна история диалога: от 1 до 20 сообщений с полями role и text.")
    started = time.monotonic()
    result, understood = app.explain([{"role": m["role"], "text": m["text"][:4000]} for m in messages], catalog, started + TIME_LIMIT)
    ids = {x for o in result.get("options", []) for x in o.get("listing_ids", [])}
    items = {i["id"]: {k: i.get(k) for k in ("category", "title", "price_rub", "city_id", "attributes")}
             for i in catalog if isinstance(i, dict) and i.get("id") in ids}
    return {"result": result, "items": items, "understood": understood, "took": round(time.monotonic() - started, 1)}


class Handler(BaseHTTPRequestHandler):
    catalog: list = []

    def send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/app/"):
            return self.send_json(404, {"error": "На этом сервере работает только режим «Хакатон»."})
        target = (CLIENT / (path.lstrip("/") or "index.html")).resolve()
        if CLIENT.resolve() not in target.parents or not target.is_file():      # за пределы папки client не выходим
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/app/ext/avito/buyer/open":
            return self.send_json(404, {"error": "На этом сервере работает только режим «Хакатон»."})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BODY:
                raise ValueError("Пустой или слишком большой запрос.")
            data = json.loads(self.rfile.read(size).decode("utf-8-sig"))
            answer = reply(data.get("messages") if isinstance(data, dict) else None, self.catalog)
        except ValueError as e:
            return self.send_json(400, {"error": str(e)[:200]})
        except LLMError as e:
            return self.send_json(502, {"error": "Модель не ответила: %s" % str(e)[:160]})
        self.send_json(200, answer)

    def log_message(self, fmt: str, *args) -> None:
        print("%s %s" % (self.address_string(), fmt % args), file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сервер показа ИИ-помощника покупателя")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.json")
    args = parser.parse_args()
    Handler.catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    if not (CLIENT / "index.html").is_file():
        print("Нет папки client со страницей.", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Откройте http://%s:%d  (остановить: Ctrl+C)" % (args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
