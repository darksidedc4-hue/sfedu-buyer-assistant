"""Вызов языковой модели через API организаторов (подмножество Chat Completions).

Подключение берётся из переменных окружения LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_USER_AGENT: проверяющий может
подставить свои. Для локальной работы те же переменные читаются из файла .env в корне репозитория, если в окружении
их нет. Ключ в исходники и Git не попадает.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_USER_AGENT")
CALL_TIMEOUT = 60      # секунд на один вызов: весь запуск run по контракту должен уложиться в 120
TRIES = 3              # всего попыток; повторяем только то, что может пройти со второго раза (429, 5xx, обрыв)


class LLMError(Exception):
    """Модель недоступна или ответила не по форме: по контракту это ошибка запуска, а не no_match."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def settings() -> dict:
    found = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                found[key.strip()] = value.strip().strip('"').strip("'")
    found.update({k: os.environ[k] for k in KEYS if os.environ.get(k)})
    missing = [k for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not found.get(k)]
    if missing:
        raise LLMError("не заданы переменные окружения: " + ", ".join(missing))
    found.setdefault("LLM_USER_AGENT", "buyer-assistant/1.0")
    return found


def ask(messages: list[dict], schema: dict, name: str, max_tokens: int = 400, deadline: float | None = None) -> dict:
    """Один разбор: сообщения и JSON-схема ответа -> объект по этой схеме. deadline: момент (time.monotonic),
    после которого новые попытки не начинаем."""
    conf = settings()
    body = json.dumps({
        "model": conf["LLM_MODEL"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(conf["LLM_BASE_URL"].rstrip("/") + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + conf["LLM_API_KEY"],
        "Content-Type": "application/json",
        # без своего User-Agent workers.dev отвечает 403 ещё до шлюза
        "User-Agent": conf["LLM_USER_AGENT"],
    })
    opener = urllib.request.build_opener(_NoRedirect)
    problem = "нет попыток"
    for attempt in range(1, TRIES + 1):
        wait = 2.0
        try:
            with opener.open(request, timeout=CALL_TIMEOUT) as response:
                raw = response.read(1_000_000)
            try:
                content = json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"]
                answer = json.loads(content)
                if not isinstance(answer, dict):
                    raise ValueError("ответ не объект")
                return answer
            except (ValueError, KeyError, IndexError, TypeError) as e:
                problem = "модель ответила не по форме: %s" % e
        except urllib.error.HTTPError as e:
            text = e.read(2000).decode("utf-8", "replace").replace(conf["LLM_API_KEY"], "[ключ]")
            problem = "HTTP %s: %s" % (e.code, " ".join(text.split())[:300])
            if e.code not in (429, 500, 502, 503, 504):
                raise LLMError(problem)   # неверный ключ, срок, параметры: повтор не поможет
            try:
                wait = min(float(e.headers.get("Retry-After") or wait), 15.0)
            except ValueError:
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            problem = "нет связи с API модели: %s" % e
        print("модель: попытка %d не удалась: %s" % (attempt, problem), file=sys.stderr)
        if attempt == TRIES or (deadline is not None and time.monotonic() + wait + CALL_TIMEOUT / 2 > deadline):
            break
        time.sleep(wait)
    raise LLMError(problem)
