"""Точка входа по контракту кейса: ./run --catalog /путь/catalog.json < request.json > response.json

Один запуск обрабатывает один запрос. В stdout уходит ровно один JSON-объект ответа, всё остальное в stderr.
Коды завершения: 0 для любого из трёх статусов; ненулевой, если не прочитались входные данные или недоступна модель
(сбой сервиса нельзя выдавать за no_match).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import extract, picker, reply  # noqa: E402
from assistant.llm import LLMError  # noqa: E402

TIME_LIMIT = 105   # секунд на всё; по контракту 120, запас оставляем на запуск и вывод


def read_request(raw: bytes) -> list[dict]:
    data = json.loads(raw.decode("utf-8-sig"))
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not messages:
        raise ValueError("в запросе нет messages")
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("text"), str) or not m["text"].strip():
            raise ValueError("сообщение не по схеме")
    return messages


def answer(messages: list[dict], catalog: list, deadline: float | None = None) -> dict:
    """Ответ на диалог: модель разбирает условия, остальное делает код."""
    need = extract.needs_from_dialogue(messages, deadline)
    print("условия: %s" % need, file=sys.stderr)
    city_names = {c["id"]: c["name"] for c in extract.cities()}
    # пока не известно хоть одно основное условие, комплект не подбираем и ничего не подставляем по умолчанию
    if need.problem_categories or need.problem_city or need.problem_budget:
        # о бюджете уже спрашивали отдельно, а числа так и нет: тот же вопрос слово в слово покупателю не помогает.
        # Если в прошлой реплике спрашивали сразу о нескольких условиях, человек просто отвечает по очереди.
        said = [m["text"] for m in messages if m["role"] == "assistant"]
        again = bool(said) and reply.asked_budget_only(said[-1])
        floor = None
        if again and need.categories and need.city_id and not need.problem_categories:
            cheapest = picker.kits(catalog, need)
            floor = picker.total(cheapest[0]) if cheapest else None
        return reply.clarify(need, city_names, again, floor)
    # показанное не подошло: предлагаем следующее, а не тот же список. Что показано, видно из истории диалога.
    shown = reply.shown_ids(messages, catalog) if need.wants_other else frozenset()
    kits = picker.options(catalog, need, shown=shown)
    if kits:
        return reply.ok(need, kits, city_names, bool(shown))
    if shown and picker.options(catalog, need):
        return reply.no_more(need, city_names)
    return reply.no_match(need, picker.why_nothing(catalog, need, list(city_names)), city_names)


def main() -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="ИИ-помощник покупателя: один запрос из stdin, один ответ в stdout")
    parser.add_argument("--catalog", required=True, type=Path, help="путь к catalog.json")
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
        if not isinstance(catalog, list):
            raise ValueError("каталог не массив")
        messages = read_request(sys.stdin.buffer.read())
    except (OSError, ValueError) as e:
        print("ошибка входных данных: %s" % e, file=sys.stderr)
        return 2
    try:
        result = answer(messages, catalog, started + TIME_LIMIT)
    except LLMError as e:
        print("ошибка модели: %s" % e, file=sys.stderr)
        return 3
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
