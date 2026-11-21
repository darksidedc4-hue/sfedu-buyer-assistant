"""Ответ помощника по контракту: уточнение, варианты комплекта или объяснение, почему комплекта нет.

Тексты собираются из данных каталога и условий покупателя, без модели: в объяснении нет ничего, чего нет в каталоге.
"""
from __future__ import annotations

from . import picker
from .extract import Needs

NAME = {"table": "стол", "chair": "кресло", "lamp": "лампа"}
ACC = {"table": "стол", "chair": "кресло", "lamp": "лампу"}          # что подобрать
GEN_PL = {"table": "столов", "chair": "кресел", "lamp": "ламп"}      # нет чего


def rub(n: int) -> str:
    return "{:,}".format(int(n)).replace(",", " ") + " ₽"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else ("%.1f" % value).replace(".", ",")


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " и " + parts[-1]


def limit_text(category: str, limits: list[tuple[str, float]]) -> str:
    """Ограничение словами покупателя: «не шире 100 см», «теплее 3500 К» и так далее."""
    words = {"table": {"<=": "не шире", "<": "уже", ">=": "не уже", ">": "шире", "=": "шириной ровно"},
             "lamp": {"<=": "не выше", "<": "ниже", ">=": "не ниже", ">": "выше", "=": "ровно"}}[category]
    unit = " см" if category == "table" else " К"
    return " и ".join(words[op] + " " + _num(value) + unit for op, value in limits)


def conditions(need: Needs) -> list[str]:
    out = []
    if "table" in need.categories and need.width_cm:
        out.append("стол " + limit_text("table", need.width_cm))
    if "chair" in need.categories and need.adjustable is not None:
        out.append("кресло " + ("с регулировкой высоты" if need.adjustable else "без регулировки высоты"))
    if "lamp" in need.categories and need.kelvin:
        out.append("лампа с цветовой температурой " + limit_text("lamp", need.kelvin))
    return out


def item_text(item: dict) -> str:
    attrs = item.get("attributes") or {}
    title = " ".join(str(item.get("title") or "").split())[:70]
    facts = []
    if item["category"] == "table" and picker._number(attrs.get("width_cm")) is not None:
        facts.append("ширина %s см" % _num(attrs["width_cm"]))
    if item["category"] == "chair" and isinstance(attrs.get("adjustable_height"), bool):
        facts.append("регулировка высоты есть" if attrs["adjustable_height"] else "без регулировки высоты")
    if item["category"] == "lamp" and picker._number(attrs.get("color_temperature_k")) is not None:
        facts.append("%s К" % _num(attrs["color_temperature_k"]))
    facts.append(rub(item["price_rub"]))
    return "%s «%s» (%s)" % (NAME[item["category"]], title, ", ".join(facts))


def clarify(need: Needs, city_names: dict[str, str], budget_again: bool = False, floor: int | None = None) -> dict:
    """Вопросы обо всех недостающих основных условиях разом, по одному на поле.

    budget_again: о бюджете уже спрашивали в прошлой реплике. Подставлять сумму за покупателя контракт запрещает,
    поэтому вопрос остаётся, но объясняет причину и даёт ориентир floor: цену самого дешёвого комплекта в каталоге."""
    cities = _join(list(city_names.values()))
    questions = []
    if need.problem_categories == "unsupported":
        extra = _join(["«%s»" % x for x in need.unsupported])
        keep = (" Оставить в подборе только %s?" % _join([ACC[c] for c in need.categories])) if need.categories else \
            " Что из этого вам нужно?"
        questions.append({"field": "categories", "text": "В каталоге есть только столы, кресла и лампы, поэтому %s подобрать "
                          "не смогу.%s" % (extra, keep)})
    elif need.problem_categories:
        questions.append({"field": "categories", "text": "Что нужно подобрать: стол, кресло, лампу или несколько из них?"})
    if need.problem_city == "unknown":
        questions.append({"field": "city_id", "text": "Города «%s» нет в моём справочнике. Комплект собирается для самовывоза "
                          "в одном городе: %s. Какой из них вам подходит?" % (need.city_said or "?", cities)})
    elif need.problem_city == "several":
        questions.append({"field": "city_id", "text": "Комплект собирается для самовывоза в одном городе. В каком из них "
                          "забирать: %s?" % cities})
    elif need.problem_city:
        questions.append({"field": "city_id", "text": "В каком городе вы заберёте покупку: %s?" % cities})
    if need.problem_budget:
        what = _join([ACC[c] for c in need.categories]) if need.categories else "весь комплект"
        if budget_again:
            text = "Без суммы подобрать не могу: бюджет за вас я не выбираю, иначе могу предложить то, что вам не по карману."
            if floor is not None:
                text += " Для ориентира: самый дешёвый подходящий комплект (%s) в городе %s стоит %s." % (
                    _join([NAME[c] for c in need.categories]), city_names.get(need.city_id, need.city_id), rub(floor))
            text += " Назовите верхний предел одним числом, например «%s»." % rub(floor if floor is not None else 10000)
        else:
            lead = "Чтобы уложиться в сумму, мне нужно число. " if need.problem_budget == "unclear" else ""
            text = lead + "Какой общий бюджет на %s, в рублях?" % what
        questions.append({"field": "budget_rub", "text": text})
    return {"status": "clarify", "questions": questions}


def ok(need: Needs, kits: list[list[dict]], city_names: dict[str, str]) -> dict:
    city = city_names.get(need.city_id, need.city_id)
    rules = conditions(need)
    options = []
    for n, kit in enumerate(kits):
        sum_rub = picker.total(kit)
        lead = "Самый дешёвый подходящий комплект" if n == 0 else "Другой вариант, на %s дороже первого" % rub(
            sum_rub - picker.total(kits[0])) if sum_rub > picker.total(kits[0]) else "Вариант за те же деньги"
        text = "%s, город %s: %s. Итого %s при бюджете %s" % (lead, city, "; ".join(item_text(i) for i in kit), rub(sum_rub),
                                                              rub(need.budget_rub))
        text += ", остаётся %s." % rub(need.budget_rub - sum_rub) if need.budget_rub > sum_rub else ", ровно в бюджет."
        if rules:
            text += " Ваши условия соблюдены: %s." % "; ".join(rules)
        options.append({"listing_ids": [i["id"] for i in kit], "total_rub": sum_rub, "explanation": text})
    return {"status": "ok", "options": options}


def no_match(need: Needs, why: dict, city_names: dict[str, str]) -> dict:
    city = city_names.get(need.city_id, need.city_id)
    wanted = _join([ACC[c] for c in need.categories])
    rules = conditions(need)
    parts = ["Собрать комплект (%s) в городе %s в пределах %s не получается." % (wanted, city, rub(need.budget_rub))]
    if why["absent"]:
        parts.append("В этом городе в каталоге нет %s." % _join([GEN_PL[c] for c in why["absent"]]))
    if why["unfit"]:
        by = {"table": "стола " + limit_text("table", need.width_cm) if need.width_cm else "",
              "chair": "кресла " + ("с регулировкой высоты" if need.adjustable else "без регулировки высоты"),
              "lamp": "лампы с цветовой температурой " + limit_text("lamp", need.kelvin) if need.kelvin else ""}
        parts.append("Здесь нет %s." % _join([by[c] for c in why["unfit"]]))
        for category, (low, high) in why.get("have", {}).items():
            what = "столы шириной" if category == "table" else "лампы с цветовой температурой"
            unit = "см" if category == "table" else "К"
            parts.append("В наличии %s %s." % (what, ("%s %s" % (_num(low), unit)) if low == high else
                                               "от %s до %s %s" % (_num(low), _num(high), unit)))
    if why["cheapest"] is not None and why["cheapest"] > need.budget_rub:
        parts.append("Самый дешёвый комплект под ваши условия стоит %s, это на %s больше бюджета."
                     % (rub(why["cheapest"]), rub(why["cheapest"] - need.budget_rub)))
    tips = []
    if why["cheapest"] is not None and why["cheapest"] > need.budget_rub:
        tips.append("поднять бюджет до %s" % rub(why["cheapest"]))
    if why["elsewhere"]:
        tips.append("забрать в другом городе: " + _join(["%s (от %s)" % (city_names.get(c, c), rub(p))
                                                         for c, p in sorted(why["elsewhere"].items(), key=lambda x: x[1])]))
    if rules and (why["unfit"] or why["cheapest"] is None or why["cheapest"] > need.budget_rub):
        tips.append("смягчить условие (%s)" % "; ".join(rules))
    if len(need.categories) > 1:
        tips.append("убрать что-то из комплекта")
    parts.append("Что можно изменить: %s. Скажите, как поступим." % "; ".join(tips) if tips else
                 "Скажите, какое условие можно изменить.")
    return {"status": "no_match", "message": " ".join(parts)}
