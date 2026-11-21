"""Разбор диалога: что покупатель хочет на конец разговора.

Это единственное место, где работает языковая модель. Она получает только реплики диалога и справочник городов
и возвращает строгую структуру: категории, город, бюджет, ограничения по характеристикам. Объявления каталога
в модель не передаются вовсе: подбор, цены и объяснения считает код по структурированным полям (см. picker.py),
поэтому текст объявления не может повлиять на ответ помощника.

Числа модель возвращает «как сказано» (значение и единица, вид сравнения), а в сантиметры и рубли их переводит
код: так ошибку легче заметить и нечего перепутать при счёте.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import llm

DATA = Path(__file__).resolve().parent.parent / "data"
CATEGORIES = ("table", "chair", "lamp")
OPS = ("<=", "<", ">=", ">", "=")
API_TEXT_LIMIT = 32768   # байт UTF-8 на все тексты одного вызова модели (инструкция и диалог вместе)


def cities() -> list[dict]:
    return json.loads((DATA / "cities.json").read_text(encoding="utf-8"))


@dataclass
class Needs:
    """Условия покупателя. problem_*: почему поле нельзя считать известным (тогда о нём спрашиваем)."""
    categories: list[str] = field(default_factory=list)
    problem_categories: str = ""        # "", "missing", "unsupported"
    unsupported: list[str] = field(default_factory=list)
    city_id: str = ""
    city_said: str = ""
    problem_city: str = ""              # "", "missing", "unknown", "several", "another"
    budget_rub: int | None = None
    item_order: dict = field(default_factory=dict)      # категория -> "cheap" или "expensive": пожелание по цене товара
    price_limits: dict = field(default_factory=dict)    # категория -> [(сравнение, рубли)]: границы цены одного товара
    expensive: bool = False             # покупатель просит самое дорогое: варианты идут от дорогого к дешёвому
    wants_other: bool = False           # показанные варианты не подошли: нужны другие, а не те же самые
    no_limit: bool = False              # покупатель сам отказался от предела по сумме: это его решение, а не подстановка
    problem_budget: str = ""            # "", "missing", "unclear"
    width_cm: list[tuple[str, float]] = field(default_factory=list)
    kelvin: list[tuple[str, float]] = field(default_factory=list)
    adjustable: bool | None = None


def schema(city_ids: list[str]) -> dict:
    limit = {"type": "object", "additionalProperties": False, "required": ["op", "value", "unit"], "properties": {
        "op": {"type": "string", "enum": list(OPS)}, "value": {"type": "number"}, "unit": {"type": "string"}}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["categories", "other_items", "what_is_unclear", "wants_other", "price_order", "table_price", "chair_price",
                     "lamp_price", "item_price_limits", "city_said", "city_id", "budget_said", "budget_value",
                     "budget_unit", "table_width", "chair_adjustable", "lamp_color_temperature"],
        "properties": {
            "categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}},
            "other_items": {"type": "array", "items": {"type": "string"}},
            "what_is_unclear": {"type": "boolean"},
            "wants_other": {"type": "string", "enum": ["yes", "no"]},
            "price_order": {"type": "string", "enum": ["cheap", "expensive"]},
            "table_price": {"type": "string", "enum": ["any", "cheap", "expensive"]},
            "chair_price": {"type": "string", "enum": ["any", "cheap", "expensive"]},
            "lamp_price": {"type": "string", "enum": ["any", "cheap", "expensive"]},
            "item_price_limits": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["item", "op", "value", "unit"], "properties": {
                    "item": {"type": "string", "enum": list(CATEGORIES)}, "op": {"type": "string", "enum": list(OPS)},
                    "value": {"type": "number"}, "unit": {"type": "string", "enum": ["rub", "thousand_rub"]}}}},
            "city_said": {"type": "string"},
            "city_id": {"type": "string", "enum": city_ids + ["other", "several", "another", "none"]},
            "budget_said": {"type": "string", "enum": ["exact", "unlimited", "vague", "none"]},
            "budget_value": {"type": "number", "minimum": 0},
            "budget_unit": {"type": "string", "enum": ["rub", "thousand_rub", "million_rub"]},
            "table_width": {"type": "array", "items": dict(limit, properties=dict(
                limit["properties"], unit={"type": "string", "enum": ["cm", "m", "mm"]}))},
            "chair_adjustable": {"type": "string", "enum": ["yes", "no", "any"]},
            "lamp_color_temperature": {"type": "array", "items": dict(limit, properties=dict(
                limit["properties"], unit={"type": "string", "enum": ["K"]}))},
        },
    }


def prompt(city_list: list[dict]) -> str:
    names = "; ".join("%s = %s" % (c["id"], c["name"]) for c in city_list)
    return """Ты разбираешь диалог покупателя с помощником магазина. Нужно вернуть условия покупателя, какими они стали \
к концу диалога. Отвечай только JSON по схеме. Ничего не подбирай и не советуй.

Как читать диалог:
- Учитывай все реплики. Если покупатель позже явно изменил условие, действует новое значение, остальные условия \
сохраняются. Короткий ответ на вопрос помощника («15 тысяч», «Азов») относится к тому, о чём помощник спросил.
- Бери только то, что покупатель сказал прямо. Ничего не додумывай и не подставляй значений по умолчанию.

categories: что покупатель хочет купить из поддерживаемого: table (стол), chair (кресло, стул, офисное кресло), \
lamp (лампа, светильник, настольная лампа). По одному товару каждой названной категории. Если покупатель отказался \
от товара («лампу не надо»), убери его.
other_items: всё, что он хочет купить, но чего нет среди трёх категорий (диван, шкаф, монитор). Пиши его словами.
Сюда идёт только то, чего покупатель всё ещё хочет к концу диалога. Если помощник уже ответил, что такого товара \
в каталоге нет, а покупатель после этого назвал, что ему нужно («нужен стол»), согласился («да», «ок», «давай») или \
просто продолжил без этого товара, считай, что от него отказались: в other_items его не включай. Оставляй его, только \
если покупатель после отказа снова прямо просит именно его («нет, нужен именно диван») или называет новый товар \
вне каталога.
what_is_unclear: true, если непонятно, что именно покупать («мебель для кабинета», «что-нибудь для работы»), \
и ни одной категории назвать нельзя.
wants_other: yes, только если помощник в предыдущей реплике уже показал варианты товаров, а покупатель в последней \
реплике ими недоволен или просит другие («не нравится», «не то», «другие», «ещё варианты», «покажи ещё», \
грубое слово в адрес показанного). Во всех остальных случаях no, в том числе когда вариантов ещё не показывали.
price_order: expensive, только если покупатель прямо просит самое дорогое («самый дорогой», «самые дорогие», \
«подороже», «покажи дорогие», «лучшее, что есть, цена не важна») и позже от этого не отказался. Иначе cheap. \
Слова о бюджете («до 15 тысяч», «бюджет не ограничен») сами по себе не expensive. Просьба «подешевле» возвращает cheap.
table_price, chair_price, lamp_price: пожелание по цене именно этого товара. expensive: «стол самый дорогой», \
«кресло подороже». cheap: «лампу самую дешёвую», «лампу попроще». any: про цену этого товара не сказано. Если \
пожелания названы для отдельных товаров, а не для всего комплекта, price_order оставь cheap.
item_price_limits: границы цены ОДНОГО товара, каждая отдельным элементом: item (table, chair или lamp), op по \
правилам сравнения ниже, value и unit (rub или thousand_rub). «Кресло от 3 тысяч» это chair, >=, 3, thousand_rub; \
«лампа не дороже 500 рублей» это lamp, <=, 500, rub. Цена одного товара не общий бюджет: budget_said от неё не \
зависит, и если общий бюджет не назван, он остаётся none. Исключение: в запросе один товар и одна сумма сверху \
(«лампа до 1000 рублей», «стол за 5 тысяч») это общий бюджет, а не граница цены. Если границ нет, пустой список.

city_said: город, который назвал покупатель, в именительном падеже («в Батайске» это «Батайск»); пустая строка, \
если не назвал.
city_id: идентификатор города из справочника (%s). other: назван город, которого нет в справочнике. several: названо \
несколько городов на выбор и не ясно, какой. none: город не назван. another: покупатель в последних репликах \
отказался от прежнего города или попросил искать в другом, но новый город не назвал («хочу в другом городе», \
«не в Азове», «давай другой город»); тогда в city_said оставь прежний город.

budget_said: exact, если назван общий предел в деньгах; vague, если о деньгах сказано без числа («недорого», \
«подешевле», «в разумных пределах») или названа вилка без верхнего предела; none, если о бюджете ничего нет. \
unlimited только если покупатель прямо сказал, что предела по деньгам нет («бюджет не ограничен», «любой бюджет», \
«цена не важна», «деньги не вопрос»). Просьба показать варианты без суммы («предложи варианты, я разберусь», \
«покажи, что есть», «не знаю») это не unlimited: none, а если о деньгах сказано расплывчато, vague. «Недорого» и \
«подешевле» всегда vague, даже в ответ на вопрос. Если позже покупатель назвал число, это exact.
budget_value и budget_unit: число и единица, как сказано. «15 тысяч», «15к», «15 т.р.» это 15 и thousand_rub; \
«15 000 ₽» это 15000 и rub; «полтора миллиона» это 1.5 и million_rub. Если budget_said не exact, ставь 0 и rub. \
Бюджет общий на весь комплект. Ноль рублей тоже точный бюджет.

table_width: ограничения ширины стола, каждое отдельным элементом: op, value и unit, как сказано (cm, m или mm).
lamp_color_temperature: ограничения цветовой температуры лампы в кельвинах, unit всегда K.
Вид сравнения op выбирай строго по словам:
- «до», «не более», «не больше», «не шире», «не выше», «максимум» это <=
- «от», «не менее», «не меньше», «не уже», «не ниже», «минимум» это >=
- «меньше», «уже», «ниже», «менее» это <
- «больше», «шире», «выше», «более» это >
- «ровно», «шириной 120», «на 4000 К» без слов сравнения это =
- «от A до B» это два элемента: >= A и <= B.
Слово «уже» перед шириной стола («стол уже 100 см», «уже метра») это сравнение «более узкий», а не наречие \
«уже есть»: op < и никогда не > и не >=. «Не уже 100 см» это >=.
Если ограничения нет, верни пустой список. Слова без числа («тёплый свет», «небольшой стол») ограничением не считай.

chair_adjustable: yes, если нужна регулировка высоты кресла; no, если нужно кресло без регулировки; any, если \
об этом не сказано.

Примеры.
Диалог: 1. Покупатель: Стол не шире метра, кресло и лампа. Таганрог. До 15 000 ₽ за всё.
Ответ: {"categories":["table","chair","lamp"],"other_items":[],"what_is_unclear":false,"wants_other":"no","price_order":"cheap","table_price":"any","chair_price":"any","lamp_price":"any","item_price_limits":[],"city_said":"Таганрог",\
"city_id":"taganrog","budget_said":"exact","budget_value":15000,"budget_unit":"rub",\
"table_width":[{"op":"<=","value":1,"unit":"m"}],"chair_adjustable":"any","lamp_color_temperature":[]}
Диалог: 1. Покупатель: Нужны кресло с регулировкой высоты и лампа теплее 3500 К, недорого. 2. Помощник: В каком \
городе и какой бюджет? 3. Покупатель: Ростов, 9 тысяч. И диван ещё.
Ответ: {"categories":["chair","lamp"],"other_items":["диван"],"what_is_unclear":false,"wants_other":"no","price_order":"cheap","table_price":"any","chair_price":"any","lamp_price":"any","item_price_limits":[],"city_said":"Ростов",\
"city_id":"rostov-na-donu","budget_said":"exact","budget_value":9,"budget_unit":"thousand_rub","table_width":[],\
"chair_adjustable":"yes","lamp_color_temperature":[{"op":"<","value":3500,"unit":"K"}]}
Диалог: 1. Покупатель: Хочу кресло от 3 тысяч, лампу самую дешёвую и стол самый дорогой
Ответ: {"categories":["table","chair","lamp"],"other_items":[],"what_is_unclear":false,"wants_other":"no",\
"price_order":"cheap","table_price":"expensive","chair_price":"any","lamp_price":"cheap",\
"item_price_limits":[{"item":"chair","op":">=","value":3,"unit":"thousand_rub"}],"city_said":"","city_id":"none",\
"budget_said":"none","budget_value":0,"budget_unit":"rub","table_width":[],"chair_adjustable":"any",\
"lamp_color_temperature":[]}
Диалог: 1. Покупатель: Хочу игровую приставку. 2. Помощник: В каталоге есть только столы, кресла и лампы, приставку \
подобрать не смогу. Что из этого вам нужно? В каком городе и какой бюджет? 3. Покупатель: Азов, нужна лампа
Ответ: {"categories":["lamp"],"other_items":[],"what_is_unclear":false,"wants_other":"no","price_order":"cheap","table_price":"any","chair_price":"any","lamp_price":"any","item_price_limits":[],"city_said":"Азов","city_id":"azov",\
"budget_said":"none","budget_value":0,"budget_unit":"rub","table_width":[],"chair_adjustable":"any",\
"lamp_color_temperature":[]}""" % names


def _clip(text: str, limit: int) -> str:
    """Обрезать по байтам UTF-8, не разрывая букву: предел API считается в байтах, а кириллица занимает по два."""
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", "ignore") + "…"


def transcript(messages: list[dict], room: int = API_TEXT_LIMIT) -> str:
    """Диалог одним текстом. room: сколько байт на него осталось после инструкции; место делится между репликами
    поровну, короткие реплики остаются целыми."""
    who = {"user": "Покупатель", "assistant": "Помощник"}
    each = max(200, (room - 400) // len(messages) - 40)
    lines = ["%d. %s: %s" % (n, who.get(m["role"], "Покупатель"), _clip(" ".join(m["text"].split()), each))
             for n, m in enumerate(messages, 1)]
    return "Диалог:\n" + "\n".join(lines) + "\n\nВерни условия покупателя на конец диалога."


def _limits(raw, scale: dict[str, float], low: float, high: float) -> list[tuple[str, float]]:
    """Ограничения из ответа модели в одну единицу. Заведомо невозможные значения отбрасываем: это ошибка разбора."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("op") not in OPS or isinstance(item.get("value"), bool):
            continue
        try:
            value = float(item["value"]) * scale.get(str(item.get("unit")), 1.0)
        except (TypeError, ValueError):
            continue
        if low <= value <= high and (item["op"], value) not in out:
            out.append((item["op"], value))
    return out


def normalize(raw: dict, city_list: list[dict]) -> Needs:
    """Ответ модели -> проверенные условия. Всё, в чём модель не уверена или что не сходится, становится вопросом."""
    need = Needs()
    need.categories = [c for c in CATEGORIES if c in (raw.get("categories") or [])]
    need.unsupported = [" ".join(str(x).split())[:40] for x in (raw.get("other_items") or []) if str(x).strip()][:5]
    if need.unsupported:
        need.problem_categories = "unsupported"
    elif not need.categories:
        need.problem_categories = "missing"

    need.expensive = raw.get("price_order") == "expensive"
    need.item_order = {c: raw.get(c + "_price") for c in need.categories if raw.get(c + "_price") in ("cheap", "expensive")}
    for limit in raw.get("item_price_limits") if isinstance(raw.get("item_price_limits"), list) else []:
        if isinstance(limit, dict) and limit.get("item") in need.categories:
            got = _limits([limit], {"rub": 1, "thousand_rub": 1000}, 0, 1_000_000_000)
            need.price_limits.setdefault(limit["item"], []).extend(g for g in got if g not in need.price_limits.get(limit["item"], []))
    need.wants_other = raw.get("wants_other") == "yes"
    need.city_said = " ".join(str(raw.get("city_said") or "").split())[:60]
    known = {c["id"] for c in city_list}
    city = raw.get("city_id")
    if city in known:
        need.city_id = city
    elif city in ("several", "another"):
        need.problem_city = city
    elif city == "other" or (city not in known and need.city_said and city != "none"):
        need.problem_city = "unknown"
    else:
        need.problem_city = "missing"

    said = raw.get("budget_said")
    scale = {"rub": 1, "thousand_rub": 1000, "million_rub": 1_000_000}.get(raw.get("budget_unit"), 1)
    value = raw.get("budget_value")
    if said == "exact" and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        need.budget_rub = int(round(value * scale))
    elif said == "unlimited":
        need.no_limit = True
    else:
        need.problem_budget = "unclear" if said == "vague" else "missing"

    need.width_cm = _limits(raw.get("table_width"), {"cm": 1, "m": 100, "mm": 0.1}, 1, 2000)
    need.kelvin = _limits(raw.get("lamp_color_temperature"), {"K": 1}, 500, 20000)
    need.adjustable = {"yes": True, "no": False}.get(raw.get("chair_adjustable"))
    return need


def needs_from_dialogue(messages: list[dict], deadline: float | None = None) -> Needs:
    city_list = cities()
    rules = prompt(city_list)
    raw = llm.ask([{"role": "system", "content": rules},
                   {"role": "user", "content": transcript(messages, API_TEXT_LIMIT - len(rules.encode("utf-8")))}],
                  schema([c["id"] for c in city_list]), "buyer_requirements", max_tokens=400, deadline=deadline)
    return normalize(raw, city_list)
