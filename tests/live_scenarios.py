"""Прогон сценариев через настоящую модель: проверяем и разбор условий, и итоговый ответ.

Запуск из корня репозитория:  python tests/live_scenarios.py [часть названия сценария]
Нужен ключ в окружении или в .env. Между вызовами пауза: у API предел 20 попыток в минуту.

Ответ проверяется независимо от кода подбора: условия сценария заданы руками, комплекты перебираются здесь же
простым перебором по каталогу.
"""
import itertools
import json
import operator
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant import main as app  # noqa: E402

CATALOG = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
OP = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt, "=": operator.eq}
KEY = {"table": "width_cm", "lamp": "color_temperature_k"}


def U(text):
    return {"role": "user", "text": text}


def A(text):
    return {"role": "assistant", "text": text}


def want(cats=(), city="", budget=None, width=(), kelvin=(), adj=None, ask=()):
    """Ожидаемые условия. ask: о каких полях помощник обязан спросить (тогда статус clarify)."""
    return {"cats": list(cats), "city": city, "budget": budget, "width": list(width), "kelvin": list(kelvin), "adj": adj, "ask": set(ask)}


ALL = ("table", "chair", "lamp")
SCENARIOS = [
    ("пример 1 из кейса", [U("Стол не шире метра, кресло и лампа. Таганрог. До 15 000 ₽ за всё.")],
     want(ALL, "taganrog", 15000, width=[("<=", 100)])),
    ("пример 2: нет бюджета", [U("Стол не шире метра, кресло и лампа. Таганрог.")], want(ALL, "taganrog", width=[("<=", 100)], ask=["budget_rub"])),
    ("стол шире 120", [U("Нужен стол шире 120 см в Ростове, бюджет 20 тысяч")], want(["table"], "rostov-na-donu", 20000, width=[(">", 120)])),
    ("кресло с регулировкой и лампа от 4000", [U("Кресло с регулировкой высоты и лампа от 4000 К, Азов, не больше 6 тысяч рублей")],
     want(["chair", "lamp"], "azov", 6000, kelvin=[(">=", 4000)], adj=True)),
    ("лампа меньше 3500", [U("Лампа с температурой меньше 3500 кельвинов, Таганрог, до 2000")], want(["lamp"], "taganrog", 2000, kelvin=[("<", 3500)])),
    ("интервал ширины и кресло без регулировки", [U("Стол от 100 до 140 сантиметров шириной и кресло без регулировки, Ростов-на-Дону, 12к")],
     want(["table", "chair"], "rostov-na-donu", 12000, width=[(">=", 100), ("<=", 140)], adj=False)),
    ("ответ на уточнение: город и бюджет", [U("Хочу стол и лампу"), A("В каком городе вы заберёте покупку и какой общий бюджет?"), U("Азов, 15 тысяч")],
     want(["table", "lamp"], "azov", 15000)),
    ("смена бюджета и отказ от лампы", [U("Стол, кресло, лампа, Таганрог, 10000"), A("Подобрал комплект за 4 900 ₽."),
                                        U("А давай бюджет 7 тысяч и без лампы")], want(["table", "chair"], "taganrog", 7000)),
    ("неподдерживаемая категория", [U("Нужен диван и стол в Таганроге до 30000")], want(["table"], "taganrog", 30000, ask=["categories"])),
    ("город вне справочника", [U("Стол и кресло, Батайск, 15000")], want(["table", "chair"], budget=15000, ask=["city_id"])),
    ("не сказано, что покупать", [U("Хочу обустроить рабочее место в Азове за 20 тысяч")], want(city="azov", budget=20000, ask=["categories"])),
    ("бюджет словами без числа", [U("Стол в Таганроге, недорого")], want(["table"], "taganrog", ask=["budget_rub"])),
    ("стол уже 90", [U("Стол уже 90 см, Ростов, 10 000 рублей")], want(["table"], "rostov-na-donu", 10000, width=[("<", 90)])),
    ("не уже 150 и лампа ровно 4000", [U("Стол не уже 150 см и лампа ровно 4000 К. Азов. Бюджет 25000.")],
     want(["table", "lamp"], "azov", 25000, width=[(">=", 150)], kelvin=[("=", 4000)])),
    ("метры с запятой и тыс. руб.", [U("Стол шириной до 1,2 м и кресло, Таганрог, 9 тыс. руб.")], want(["table", "chair"], "taganrog", 9000, width=[("<=", 120)])),
    ("точная ширина", [U("Стол шириной 120 см, Ростов, до 8000 ₽")], want(["table"], "rostov-na-donu", 8000, width=[("=", 120)])),
    ("бюджет ноль", [U("Стол в Азове, бюджет 0 рублей")], want(["table"], "azov", 0)),
    ("лампа от 2700 до 3000", [U("Лампа от 2700 до 3000 К, Таганрог, 1500 рублей")], want(["lamp"], "taganrog", 1500, kelvin=[(">=", 2700), ("<=", 3000)])),
    ("ничего не известно", [U("Привет, помоги выбрать")], want(ask=["categories", "city_id", "budget_rub"])),
    ("два города на выбор", [U("Стол и кресло в Таганроге или Ростове, 15000")], want(["table", "chair"], budget=15000, ask=["city_id"])),
    ("смена города", [U("Стол и лампа, Таганрог, 8000"), A("В Таганроге такого комплекта нет."), U("Тогда посмотри в Ростове")],
     want(["table", "lamp"], "rostov-na-donu", 8000)),
    ("стул и настольная лампа", [U("Нужен стул с регулировкой и настольная лампа, Азов, 5 тысяч")], want(["chair", "lamp"], "azov", 5000, adj=True)),
    ("стол не нужен", [U("Кресло, Таганрог, 5000. Стол не нужен.")], want(["chair"], "taganrog", 5000)),
    ("миллиметры", [U("Стол не шире 1100 мм, Ростов, 10 тысяч")], want(["table"], "rostov-na-donu", 10000, width=[("<=", 110)])),
    ("лампа выше 4000 и стол", [U("Лампа с температурой выше 4000 К и стол, Азов, до 12 000")], want(["table", "lamp"], "azov", 12000, kelvin=[(">", 4000)])),
    ("короткий ответ про бюджет", [U("Стол и кресло в Ростове"), A("Какой общий бюджет на стол и кресло, в рублях?"), U("15 тысяч")],
     want(["table", "chair"], "rostov-na-donu", 15000)),
    ("бюджет поднят после отказа", [U("Стол, Азов, 3000"), A("Самый дешёвый стол стоит дороже. Можно поднять бюджет."), U("Хорошо, пусть будет 6000")],
     want(["table"], "azov", 6000)),
    ("уложиться в сумму", [U("Кресло и лампа, Ростов-на-Дону, хочу уложиться в 7500")], want(["chair", "lamp"], "rostov-na-donu", 7500)),
    ("город в падеже и строчными", [U("лампу и кресло заберу в азове, на всё 4 тысячи")], want(["chair", "lamp"], "azov", 4000)),
    ("ограничение изменено позже", [U("Стол не шире 100 см, Таганрог, 9000"), A("Подобрал стол."), U("Нет, пусть будет не шире 80 сантиметров")],
     want(["table"], "taganrog", 9000, width=[("<=", 80)])),
    ("невыполнимое ограничение", [U("Лампа ниже 1500 К, Ростов, 5000 рублей")], want(["lamp"], "rostov-na-donu", 5000, kelvin=[("<", 1500)])),
    ("после отказа назвал поддерживаемое", [U("Хочу Плейстейшн 5"),
                                            A("В каталоге есть только столы, кресла и лампы, поэтому «Плейстейшн 5» подобрать не смогу. Что из этого вам нужно? "
                                              "В каком городе вы заберёте покупку: Таганрог, Ростов-на-Дону и Азов? Какой общий бюджет на весь комплект, в рублях?"),
                                            U("Таганрог, нужен стол")], want(["table"], "taganrog", ask=["budget_rub"])),
    ("согласился обойтись без дивана", [U("Нужен диван и стол в Таганроге до 30000"),
                                        A("В каталоге есть только столы, кресла и лампы, поэтому «диван» подобрать не смогу. Оставить в подборе только стол?"),
                                        U("Да, давай только стол")], want(["table"], "taganrog", 30000)),
    ("короткое согласие после отказа", [U("Кресло и телевизор, Азов, 8 тысяч"),
                                        A("В каталоге есть только столы, кресла и лампы, поэтому «телевизор» подобрать не смогу. Оставить в подборе только кресло?"),
                                        U("ок")], want(["chair"], "azov", 8000)),
    ("настаивает на неподдерживаемом", [U("Нужен диван и стол в Таганроге до 30000"),
                                        A("В каталоге есть только столы, кресла и лампы, поэтому «диван» подобрать не смогу. Оставить в подборе только стол?"),
                                        U("Нет, мне нужен именно диван")], want(["table"], "taganrog", 30000, ask=["categories"])),
    ("новый неподдерживаемый после отказа", [U("Нужен шкаф в Ростове за 10 тысяч"),
                                             A("В каталоге есть только столы, кресла и лампы, поэтому «шкаф» подобрать не смогу. Что из этого вам нужно?"),
                                             U("Тогда тумбочку")], want(city="rostov-na-donu", budget=10000, ask=["categories"])),
    ("город вне справочника, потом из справочника", [U("Стол и кресло, Батайск, 15000"),
                                                     A("Города «Батайск» нет в моём справочнике. Комплект собирается для самовывоза в одном городе: Таганрог, "
                                                       "Ростов-на-Дону и Азов. Какой из них вам подходит?"), U("Тогда Азов")],
     want(["table", "chair"], "azov", 15000)),
    ("бюджет без числа, потом число", [U("Лампа в Таганроге, недорого"), A("Чтобы уложиться в сумму, мне нужно число. Какой общий бюджет на лампу, в рублях?"),
                                       U("ну рублей 800")], want(["lamp"], "taganrog", 800)),
    ("длинный диалог с добавлением товара", [U("Нужен стол"), A("В каком городе и какой бюджет?"), U("Таганрог"), A("Какой общий бюджет на стол, в рублях?"),
                                              U("8 тысяч"), A("Подобрал стол за 2 700 ₽."), U("Добавь ещё кресло с регулировкой высоты, бюджет тогда 12 тысяч")],
     want(["table", "chair"], "taganrog", 12000, adj=True)),
]


def fits(item, w):
    attrs = item["attributes"]
    if item["category"] == "chair":
        return w["adj"] is None or (type(attrs.get("adjustable_height")) is bool and attrs["adjustable_height"] == w["adj"])
    limits = w["width"] if item["category"] == "table" else w["kelvin"]
    if not limits:
        return True
    value = attrs.get(KEY[item["category"]])
    return type(value) in (int, float) and all(OP[op](value, bound) for op, bound in limits)


def kit_exists(w):
    lists = [[i for i in CATALOG if i["category"] == c and i["city_id"] == w["city"] and fits(i, w)] for c in w["cats"]]
    return any(sum(i["price_rub"] for i in kit) <= w["budget"] for kit in itertools.product(*lists))


def check(result, w):
    """Список претензий к ответу; пустой, если ответ верен."""
    if w["ask"]:
        if result.get("status") != "clarify":
            return ["ждали clarify %s, получили %s" % (sorted(w["ask"]), result.get("status"))]
        fields = [q.get("field") for q in result.get("questions", [])]
        bad = [] if set(fields) == w["ask"] and len(fields) == len(set(fields)) else ["вопросы о %s, ждали %s" % (fields, sorted(w["ask"]))]
        return bad + ["пустой текст вопроса" for q in result["questions"] if not str(q.get("text", "")).strip()]
    if not kit_exists(w):
        ok = result.get("status") == "no_match" and str(result.get("message", "")).strip() and set(result) == {"status", "message"}
        return [] if ok else ["ждали no_match, получили %s" % result.get("status")]
    if result.get("status") != "ok":
        return ["ждали ok, получили %s: %s" % (result.get("status"), str(result)[:160])]
    by_id, bad, seen = {i["id"]: i for i in CATALOG}, [], set()
    if not 1 <= len(result.get("options", [])) <= 3:
        bad.append("вариантов %d" % len(result.get("options", [])))
    for opt in result.get("options", []):
        ids = opt.get("listing_ids", [])
        items = [by_id.get(x) for x in ids]
        if None in items or len(set(ids)) != len(ids):
            bad.append("неизвестные или повторные ID %s" % ids)
            continue
        if sorted(i["category"] for i in items) != sorted(w["cats"]):
            bad.append("состав %s вместо %s" % ([i["category"] for i in items], w["cats"]))
        if any(i["city_id"] != w["city"] for i in items) or not all(fits(i, w) for i in items):
            bad.append("товар не из того города или не по условиям: %s" % ids)
        if opt.get("total_rub") != sum(i["price_rub"] for i in items) or opt["total_rub"] > w["budget"]:
            bad.append("сумма %s неверна или выше бюджета" % opt.get("total_rub"))
        if not str(opt.get("explanation", "")).strip() or set(opt) != {"listing_ids", "total_rub", "explanation"}:
            bad.append("нет объяснения или лишние поля")
        if frozenset(ids) in seen:
            bad.append("варианты повторяются")
        seen.add(frozenset(ids))
    return bad


def main():
    pick = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = 0
    for name, messages, w in SCENARIOS:
        if pick not in name:
            continue
        started = time.monotonic()
        try:
            result = app.answer(messages, CATALOG)
            bad = check(result, w)
        except Exception as e:   # сценарий не должен останавливать прогон
            result, bad = {}, ["сбой: %s" % e]
        took = time.monotonic() - started
        print("%s  %-42s %4.1f с  %s" % ("ok  " if not bad else "ПЛОХО", name, took, result.get("status", "")))
        for b in bad:
            print("        " + b)
        failed += bool(bad)
        time.sleep(max(0.0, 3.2 - took))
    print("\nне прошло: %d" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
