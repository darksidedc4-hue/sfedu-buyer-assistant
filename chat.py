#!/usr/bin/env python3
"""Консольный диалог с помощником для показа на защите: python3 chat.py [--catalog data/catalog.json]

Каждая реплика обрабатывается так же, как в ./run: вся история уходит на разбор заново, ответ помощника
добавляется в историю текстом. Команды: «заново» начинает диалог с начала, пустая строка или «выход» завершает.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant import main as app  # noqa: E402
from assistant.llm import LLMError  # noqa: E402


def as_text(result: dict) -> str:
    """Ответ по контракту -> то, что помощник сказал бы человеку (это же попадает в историю диалога)."""
    if result["status"] == "clarify":
        return " ".join(q["text"] for q in result["questions"])
    if result["status"] == "no_match":
        return result["message"]
    return "\n".join("%d) %s" % (n, o["explanation"]) for n, o in enumerate(result["options"], 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Консольный диалог с ИИ-помощником покупателя")
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parent / "data" / "catalog.json")
    parser.add_argument("--json", action="store_true", help="показывать и сырой ответ по контракту")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    print("Помощник покупателя. Напишите, что нужно: например «стол не шире метра и лампа, Таганрог, до 8 тысяч».")
    messages = []
    while True:
        try:
            text = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() in ("выход", "exit", "quit"):
            break
        if text.lower() == "заново":
            messages = []
            print("Начинаем заново.")
            continue
        messages.append({"role": "user", "text": text})
        try:
            result = app.answer(messages, catalog)
        except LLMError as e:
            print("Модель недоступна: %s" % e)
            messages.pop()
            continue
        said = as_text(result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\nПомощник: " + said)
        messages.append({"role": "assistant", "text": said})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
