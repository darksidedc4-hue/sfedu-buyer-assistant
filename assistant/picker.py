"""Подбор комплекта по каталогу. Здесь нет модели: только структурированные поля объявлений и арифметика.

Источник фактов по контракту: category, city_id, price_rub и перечисленные характеристики в attributes.
Названия и описания объявлений в подборе не участвуют совсем, поэтому вставленные в них инструкции ни на что
не влияют; подходящее по данным объявление при этом остаётся в подборке.
"""
from __future__ import annotations

import itertools
import operator

from .extract import CATEGORIES, Needs

COMPARE = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt, "=": operator.eq}
ATTRIBUTE = {"table": "width_cm", "lamp": "color_temperature_k"}   # числовая характеристика категории
PER_CATEGORY = 8      # столько самых дешёвых подходящих товаров каждой категории идёт в перебор комплектов
PER_WIDE = 15         # когда просят самое дорогое: столько с дешёвого и столько с дорогого края, чтобы найти и самый
                      # дорогой комплект вообще, и самый дорогой из тех, что укладываются в бюджет


def _number(value):
    """Число из каталога; bool числом не считаем, всё прочее значит «неизвестно»."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def limits_of(category: str, need: Needs) -> list[tuple[str, float]]:
    return need.width_cm if category == "table" else need.kelvin if category == "lamp" else []


def order_of(category: str, need: Needs) -> str:
    """Что покупатель хочет по цене этого товара: пожелание для товара, а без него общее для комплекта."""
    return need.item_order.get(category) or ("expensive" if need.expensive else "cheap")


def mixed(need: Needs) -> bool:
    """Пожелания по цене названы для отдельных товаров, а не одно на весь комплект."""
    return any(c in need.item_order for c in need.categories)


def score(kit: list[dict], need: Needs) -> int:
    """Чем меньше, тем лучше комплект отвечает пожеланиям: где просили подороже, цена идёт в плюс комплекту,
    где подешевле (и где ничего не просили) в минус."""
    return sum(-i["price_rub"] if order_of(i["category"], need) == "expensive" else i["price_rub"] for i in kit)


def fits(item: dict, need: Needs) -> bool:
    """Подходит ли объявление под ограничения своей категории. Ограничена характеристика, а значения в объявлении
    нет: соответствие не подтверждено, объявление не подходит. False и 0 это значения, а не «нет данных»."""
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    category = item.get("category")
    price = _number(item.get("price_rub"))
    if not all(price is not None and COMPARE[op](price, bound) for op, bound in need.price_limits.get(category, [])):
        return False
    if category == "chair":
        if need.adjustable is None:
            return True
        return isinstance(attrs.get("adjustable_height"), bool) and attrs["adjustable_height"] is need.adjustable
    limits = limits_of(category, need)
    if not limits:
        return True
    value = _number(attrs.get(ATTRIBUTE.get(category, "")))
    return value is not None and all(COMPARE[op](value, bound) for op, bound in limits)


def valid(item) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] and item.get("category") in CATEGORIES
            and isinstance(item.get("price_rub"), int) and not isinstance(item.get("price_rub"), bool) and item["price_rub"] > 0)


def candidates(catalog: list, need: Needs, city_id: str) -> dict[str, list[dict]]:
    """Подходящие товары города по каждой запрошенной категории, от дешёвых к дорогим."""
    out = {c: [] for c in need.categories}
    for item in catalog:
        if valid(item) and item["category"] in out and item.get("city_id") == city_id and fits(item, need):
            out[item["category"]].append(item)
    for items in out.values():
        items.sort(key=lambda i: (i["price_rub"], i["id"]))
    return out


def kits(catalog: list, need: Needs, city_id: str | None = None, shown: frozenset = frozenset()) -> list[list[dict]]:
    """Все комплекты (по одному товару каждой запрошенной категории) из самых дешёвых подходящих товаров,
    от дешёвого к дорогому. Бюджет здесь не учитывается: его проверяет вызывающий. shown: ID уже показанных
    товаров; они не занимают мест среди самых дешёвых, иначе до остальных товаров очередь не дойдёт."""
    found = candidates(catalog, need, city_id or need.city_id)
    if not found or any(not items for items in found.values()):
        return []
    def near(category):
        items = found[category]
        fresh, old = [i for i in items if i["id"] not in shown], [i for i in items if i["id"] in shown]
        if order_of(category, need) == "expensive":
            fresh = fresh if len(fresh) <= 2 * PER_WIDE else fresh[:PER_WIDE] + fresh[-PER_WIDE:]
            return fresh + old[-PER_CATEGORY:]
        return fresh[:PER_CATEGORY] + old[:PER_CATEGORY]
    combos = itertools.product(*(near(c) for c in need.categories))
    return sorted((list(k) for k in combos), key=lambda k: (sum(i["price_rub"] for i in k), [i["id"] for i in k]))


def total(kit: list[dict]) -> int:
    return sum(i["price_rub"] for i in kit)


def options(catalog: list, need: Needs, count: int = 3, shown: frozenset = frozenset()) -> list[list[dict]]:
    """До трёх комплектов в бюджете. Первый самый дешёвый. Каждый следующий выбирается так, чтобы в нём было как
    можно больше товаров, которых не было в предыдущих вариантах (при равенстве берём дешевле): три комплекта,
    отличающиеся одной лампой, покупателю мало что дают.

    shown: ID товаров, которые покупателю уже показывали и которые ему не подошли. Комплект целиком из них
    не предлагаем, а среди остальных сначала идут те, где нового больше."""
    pool = [k for k in kits(catalog, need, shown=shown) if need.no_limit or total(k) <= need.budget_rub]
    old = {i["id"] for k in pool for i in k if i["id"] in shown}
    pool = [k for k in pool if any(i["id"] not in old for i in k)]
    # лучший по пожеланиям идёт первым; без пожеланий это просто самый дешёвый
    pool.sort(key=lambda k: (score(k, need), [i["id"] for i in k]))
    chosen, used = [], set(old)
    while pool and len(chosen) < count:
        best = max(pool, key=lambda k: (len({i["id"] for i in k} - used), -score(k, need))) if chosen or old else pool[0]
        chosen.append(best)
        used |= {i["id"] for i in best}
        pool.remove(best)
    return chosen


def why_nothing(catalog: list, need: Needs, city_ids: list[str]) -> dict:
    """Почему комплекта нет: каких товаров не хватает в городе, либо на сколько не хватает бюджета, и где или
    за сколько комплект собрать можно. Только факты каталога, решение остаётся за покупателем."""
    in_city = {c: 0 for c in need.categories}
    for item in catalog:
        if valid(item) and item["category"] in in_city and item.get("city_id") == need.city_id:
            in_city[item["category"]] += 1
    found = candidates(catalog, need, need.city_id)
    absent = [c for c in need.categories if not in_city[c]]
    unfit = [c for c in need.categories if in_city[c] and not found[c]]
    cheapest = kits(catalog, need)
    elsewhere = {}
    for city in city_ids:
        if city != need.city_id:
            other = kits(catalog, need, city)
            if other and (need.no_limit or total(other[0]) <= need.budget_rub):
                elsewhere[city] = total(other[0])
    # что в городе есть на самом деле по ограниченной характеристике: покупателю легче решить, чем поступиться
    have = {}
    for category in unfit:
        values = [_number((i.get("attributes") or {}).get(ATTRIBUTE.get(category, ""))) for i in catalog
                  if valid(i) and i["category"] == category and i.get("city_id") == need.city_id]
        values = [v for v in values if v is not None]
        if values:
            have[category] = (min(values), max(values))
    return {"absent": absent, "unfit": unfit, "cheapest": total(cheapest[0]) if cheapest else None, "elsewhere": elsewhere,
            "have": have}
