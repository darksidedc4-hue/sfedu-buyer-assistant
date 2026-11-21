// Страница помощника по Авито: всё, что видит человек. Открывается по нажатию на значок расширения.
// Бот в Telegram нужен один раз, чтобы выдать код активации; дальше поиск идёт здесь. Думает по-прежнему сервер
// бота (разбор просьбы, вопросы, отбор объявлений), а страницы Авито открывает фоновая часть расширения (sw.js).
// Пока поиск идёт, страница спрашивает сервер раз в две секунды. Пока человек отмечает ответы на вопросы,
// экран не перерисовывается: отметки живут только здесь.
// Слежка за ценой: под результатами поиска человек называет свою цену, дальше сервер сам раз в несколько минут
// просит фоновую часть проверить Авито. Находки показывает вкладка «Слежка» здесь же, а на значке расширения
// горит число новых (его ставит sw.js); в Telegram о находках ничего не приходит.
//
// Продажа: человек называет вещь, сервер с помощником готовят объявление в форме Авито в соседней вкладке
// (её заполняет sw.js), здесь виден ход, вопросы помощника и итог. Публикует объявление человек.
//
// Эта же страница работает как сайт (glie.ru): там вместо интерфейсов расширения стоит прослойка web.js, а признак
// сайта это window.OUIPN_WEB. Страницы Авито читает только расширение, сайт управляет тем же помощником.
//
// Движение. Страница перерисовывается по опросу, поэтому анимация появления вешается только на то, чего на экране
// ещё не было (см. render): иначе карточки вздрагивали бы каждые две секунды. Ход поиска правится на месте
// (updateBusy), чтобы маршрут и мерцание заготовок не начинались заново. После действия человека экран меняется
// плавным переходом (show с признаком smooth): карточки переезжают на новые места, удалённая гаснет.

const $ = (id) => document.getElementById(id);
const LOGIN_URL = "https://www.avito.ru/#login?authsrc=h";
const SORTS = [["cheap", "Выгоднее", "Самое выгодное"], ["fast", "Быстрее", "Самое быстрое"], ["best", "Баланс", "Лучший баланс"]];
const EMPTY = { cheap: "Подходящих объявлений нет.", fast: "Сроков доставки Авито не показал.", best: "Сроков доставки Авито не показал." };
const EVERY = [[1, "1 мин"], [2, "2 мин"], [5, "5 мин"], [15, "15 мин"], [30, "30 мин"], [60, "час"]];
const OFTEN = "Авито может чаще просить проверку «вы не робот»";
// Остановки маршрута поиска и состояния сервера, которые к ним относятся
const STOPS = ["Просьба", "Уточнение", "Поиск", "Отбор", "Проверка"];
const STAGE = { plan: 0, recon: 1, ask: 1, search: 2, sift: 3, details: 4, check: 4 };
const svg = (d) => '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>';
const ICON = {
  about: svg("M4 7h16M4 12h16M4 17h10"),
  ship: svg("M2 7h11v9H2zM13 10h4.5L21 13.5V16h-8zM6.5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"),
  who: svg("M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0"),
};
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
const WEB = !!window.OUIPN_WEB;

// Вид, в котором страница открыта сейчас: вкладка, боковая панель или выпадающее окошко (см. applyView в sw.js)
const HERE = new URLSearchParams(location.search).get("v") || "tab";
document.documentElement.classList.add(HERE);
// окно браузера узнаём заранее: боковую панель Chrome разрешает открыть только сразу по нажатию, без ожиданий
let winId = null;
chrome.windows.getCurrent().then((w) => { winId = w.id; });

let cfg = null;        // адрес сервера и ключ из кода активации
let state = null;      // последний ответ сервера
let sort = "cheap";
let answers = {};
let timer = null;
let every = 15;        // частота проверки в форме новой слежки, минут
let busyJob = null;    // поиск, чей ход сейчас на экране и правится на месте
let tab = "search";    // открытый раздел поиска по Авито: search, watch или sell
let mode = "avito";    // режим сверху: hack (подбор из каталога кейса хакатона) или avito
let hasBuyer = false;  // решение кейса на сервере есть: тогда показываем переключатель режима
let buyerTalk = [];    // диалог раздела «Подбор»: реплики человека и ответы решения кейса
let seenBefore = null; // когда человек прошлый раз смотрел слежки: находки позже этого помечены «новое», пока раздел открыт
let marking = false;
let landed = false;    // первый ответ сервера после открытия страницы уже был
let reached = { id: null, n: 0 };   // до какой остановки маршрута дошёл поиск

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// разряды и знак рубля держатся неразрывным пробелом: цена не переносится на две строки
function group(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
}

function rub(n) {
  return group(n) + "\u00a0₽";
}

function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

function digits(v) {
  return parseInt(String(v).replace(/\D/g, ""), 10) || 0;
}

// имя для плавного переезда карточки: только буквы, цифры и дефис
function vt(prefix, id) {
  return prefix + "-" + String(id).replace(/[^\w-]/g, "");
}

// На сайте без кода активации открыт только поиск: он идёт на отдельные открытые адреса сервера.
function guest() {
  return WEB && !cfg;
}

async function api(method, path, body) {
  if (guest() && !["state", "search", "answer", "stop"].includes(path)) throw new Error("Для этого нужен код активации.");
  const r = await fetch(guest() ? location.origin + "/app/ext/avito/open/" + path : cfg.u + "ext/avito/" + path, {
    method,
    headers: guest() ? { "Content-Type": "application/json" } : { "X-Avito-Ext": cfg.t, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 403) throw new Error("Код активации больше не действует: возьмите новый в боте.");
  if (!r.ok) throw new Error(data.error || "Не получилось, попробуйте ещё раз.");
  return data;
}

// --- движение ---

// Заменить содержимое, если оно изменилось. Появление анимируется только у того, чего раньше не было:
// у каждой карточки свой data-key, по нему и узнаём новое.
function render(el, html) {
  if (el._html === html) return;
  const old = new Set(Array.from(el.querySelectorAll("[data-key]"), (n) => n.dataset.key));
  el._html = html;
  el.innerHTML = html;
  let n = 0;
  el.querySelectorAll("[data-key]").forEach((node) => {
    if (old.has(node.dataset.key)) return;
    node.classList.add("enter");
    node.style.setProperty("--i", Math.min(n++, 8));
  });
}

// проиграть короткую анимацию ещё раз (нажатие, ошибка, смена подписи)
function replay(el, cls) {
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
}

// ползунок переключателя встаёт под выбранную кнопку
function thumbs() {
  document.querySelectorAll(".seg").forEach((seg) => {
    const on = seg.querySelector("button.on"), thumb = seg.querySelector(".thumb");
    if (!on || !thumb || !on.offsetWidth) return;
    thumb.style.width = on.offsetWidth + "px";
    thumb.style.transform = "translateX(" + on.offsetLeft + "px)";
  });
}

let toastTimer = null;
function toast(text, bad) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5500 : 3200);
}

// строка под шапкой: есть ли связь с сервером
function status(text, kind) {
  $("statusText").textContent = text;
  $("status").className = "status" + (kind ? " " + kind : "");
}

// --- что показать ---

function meta(icon, text, cls) {
  return '<span class="meta' + (cls ? " " + cls : "") + '">' + (icon ? ICON[icon] : "") + "<span>" + esc(text) + "</span></span>";
}

// насколько цена объявления ниже или выше рынка (медианы цен подходящих объявлений без доставки)
function vsMarket(price, median) {
  if (!median) return "";
  const d = Math.round((median - price) / median * 100);
  if (d >= 1) return '<small class="vs low">на ' + d + " % ниже рынка</small>";
  if (d <= -1) return '<small class="vs">на ' + -d + " % выше рынка</small>";
  return '<small class="vs">по рынку</small>';
}

function adCard(i, n, median) {
  let ship = i.days != null ? "доставка" + (i.to ? " в " + i.to : "") + " от " + i.days + " " + plural(i.days, "дня", "дней", "дней") : "срок доставки не указан";
  ship += i.delivery == null ? "" : i.delivery ? ", " + rub(i.delivery) : ", бесплатно";
  const who = [i.seller, i.rating ? "★ " + i.rating + (i.reviews ? " (" + i.reviews + ")" : "") : "", i.kind, i.place].filter(Boolean).join(" · ");
  const about = i.about || i.state || "";
  const top = n === 0;
  return '<a class="card ad' + (top ? " top" : "") + '" data-key="' + vt("ad", i.id) + '" style="view-transition-name:' + vt("ad", i.id) +
    '" href="' + esc(i.url) + '" target="_blank" rel="noopener">' +
    '<span class="rank">' + (n + 1) + "</span>" +
    '<span class="body">' + (top ? '<span class="tag">' + SORTS.find((x) => x[0] === sort)[2] + "</span>" : "") +
    '<span class="name">' + esc(i.title) + "</span>" +
    (about ? meta("about", about) : "") + meta("ship", ship) +
    (who ? '<span class="meta">' + ICON.who + "<span>" + esc(who) +
      (i.sid ? ' · <button class="mini" type="button" data-act="block" data-sid="' + esc(i.sid) + '" data-name="' + esc(i.seller || "") +
        '">скрыть продавца</button>' : "") + "</span></span>" : "") +
    (i.note ? meta("", "«" + i.note + "»", "note") : "") + "</span>" +
    '<span class="sum"><b>' + rub(i.price + (i.delivery || 0)) + "</b>" +
    (i.delivery ? "<small>" + rub(i.price) + " + доставка</small>" : i.delivery === 0 ? "<small>доставка бесплатно</small>" : "") +
    vsMarket(i.price, median) + "</span></a>";
}

function askCards(j) {
  return '<div class="card" data-key="ask"><h2>Уточните, что важно</h2>' +
    '<p class="hint">Это характеристики, которые Авито предлагает для такого товара. Можно выбрать несколько; не выбрано значит не важно.</p></div>' +
    j.questions.map((q, n) =>
      '<div class="card" data-key="q' + n + '"><h2>' + esc(q.key) + '</h2><div class="chips">' + q.options.map((o) =>
        '<button type="button" data-act="pick" data-k="' + esc(q.key) + '" data-o="' + esc(o) + '"' +
        ((answers[q.key] || []).includes(o) ? ' class="on"' : "") + ">" + esc(o) + "</button>").join("") + "</div></div>").join("") +
    '<div class="row" data-key="ask-go"><button class="btn" data-act="answer">Искать</button>' +
    '<button class="btn ghost" data-act="stop">Отменить поиск</button></div>';
}

// До какой остановки маршрута дошёл поиск. Назад не идём: после ответов на вопросы сервер снова «разбирает просьбу»,
// но для человека это уже пройденное.
function stage(j) {
  if (reached.id !== j.id) reached = { id: j.id, n: 0 };
  reached.n = Math.max(reached.n, STAGE[j.state] || 0);
  return reached.n;
}

// где ищем: в названном городе или, если город не указан, по всем регионам
function where(city) {
  return city ? "доставка в " + city : "по всем регионам";
}

function busyMeta(j) {
  return "«" + j.text + "», " + where(j.city) + (j.found ? ". Объявлений найдено: " + j.found : "");
}

function busyHtml(j) {
  const n = stage(j);
  return '<div class="card" data-key="busy" data-job="' + esc(j.id) + '"><div class="route" id="route" style="--p:' + n + '"><div class="rail"><i></i></div><ol>' +
    STOPS.map((s, k) => "<li" + (k < n ? ' class="done"' : k === n ? ' class="now"' : "") + ">" + s + "</li>").join("") + "</ol></div>" +
    '<h2 id="pNote">' + esc(j.note || "Ищу") + "</h2>" +
    '<p class="hint" id="pMeta">' + esc(busyMeta(j)) + "</p>" +
    '<p class="hint" id="pWarn">' + esc(j.warn || "") + "</p>" +
    '<button class="btn ghost" data-act="stop">Остановить</button></div>';
}

// Ход поиска правится на месте: маршрут доезжает до новой остановки, подпись сменяется, ничего не мигает.
function updateBusy(j) {
  const n = stage(j);
  $("route").style.setProperty("--p", n);
  $("route").querySelectorAll("li").forEach((li, k) => { li.className = k < n ? "done" : k === n ? "now" : ""; });
  const note = j.note || "Ищу";
  if ($("pNote").textContent !== note) {
    $("pNote").textContent = note;
    replay($("pNote"), "tick");
  }
  $("pMeta").textContent = busyMeta(j);
  $("pWarn").textContent = j.warn || "";
}

// Возвращает две части: шапку поиска и список объявлений. Между ними на странице стоит форма слежки за ценой.
function outHtml(j) {
  if (!j) return ["", ""];
  const warn = j.warn ? '<p class="hint">' + esc(j.warn) + "</p>" : "";
  // остановил сам человек: это не ошибка, красным не пугаем
  if (j.state === "error" && j.note === "Поиск остановлен.") {
    return ['<div class="card" data-key="err"><span class="pill warn"><i class="dot"></i>Поиск остановлен</span>' +
      '<p class="hint">«' + esc(j.text) + "». Можно искать заново.</p></div>", ""];
  }
  if (j.state === "error") return ['<div class="card" data-key="err"><span class="pill bad"><i class="dot"></i>Не получилось</span><p>' + esc(j.note) + "</p></div>", ""];
  if (j.state === "ask") return [askCards(j) + warn, ""];
  if (j.state !== "done") return [busyHtml(j), '<div class="ghost-card" data-key="g1"></div><div class="ghost-card" data-key="g2"></div>'];
  const ids = j.lists[sort] || [];
  const wishes = Object.keys(j.answers || {}).map((k) => k + ": " + j.answers[k].join(", ")).join("; ");
  const all = Object.values(j.items);
  const priced = all.some((i) => i.delivery != null);
  // Авито считает доставку до адреса из профиля человека, а не до города поиска: говорим, если они разные
  const to = (all.find((i) => i.to) || {}).to;
  const other = to && to.toLowerCase().replace(/ё/g, "е") !== j.city.toLowerCase().replace(/ё/g, "е");
  const notes = (other ? "Авито считает доставку до адреса из вашего профиля: " + to + ". " : "") +
    (sort !== "fast" && !priced ? "Цену доставки Авито показывает только вошедшему, поэтому выгода посчитана по цене товара." : "");
  return ['<div class="card" data-key="res"><p>«' + esc(j.text) + "», " + esc(where(j.city)) + (wishes ? ". " + esc(wishes) : "") + ".</p>" +
    '<p class="stats"><span><b>' + j.found + "</b>подошло</span>" + (j.dropped ? "<span><b>" + j.dropped + "</b>отсеяно</span>" : "") +
    (j.rest ? "<span><b>" + j.rest + "</b>не открывал</span>" : "") +
    (j.median ? "<span><b>" + rub(j.median) + "</b>рынок</span>" : "") + "</p>" +
    (j.median ? '<p class="hint">Рынок: медианная цена подходящих объявлений без доставки (' + j.sample + " шт.)" +
      (j.private ? ", только частные продавцы." : ".") + "</p>" : "") +
    (notes ? '<p class="hint">' + esc(notes) + "</p>" : "") +
    '<div class="seg wide" role="group" aria-label="Порядок списка"><i class="thumb" aria-hidden="true"></i>' + SORTS.map((x) =>
      '<button type="button" data-act="sort" data-s="' + x[0] + '"' + (x[0] === sort ? ' class="on"' : "") + ">" + x[1] + "</button>").join("") + "</div></div>" + warn,
    ids.length ? ids.map((id, n) => adCard(j.items[id], n, j.median)).join("") : '<p class="hint" data-key="empty">' + EMPTY[sort] + "</p>"];
}

// --- слежка за ценой ---

// Самое выгодное из найденного: цена вместе с доставкой. От неё человек отталкивается, называя свою цену.
function market(j) {
  const best = j.items[(j.lists.cheap || [])[0]];
  return best ? { sum: best.price + (best.delivery || 0), priced: best.delivery != null } : null;
}

function marketHint(j) {
  const m = market(j);
  if (!m) return "Сейчас подходящих объявлений нет.";
  return "Сейчас самое выгодное из подходящих: " + rub(m.sum) + (m.priced ? " вместе с доставкой." : " (цену доставки Авито не показал).");
}

// Подсказка под полем цены: насколько названная цена ниже того, что есть сейчас
function gapHint() {
  const n = digits($("target").value);
  const m = state && state.job && state.job.state === "done" ? market(state.job) : null;
  let text = "", good = false;
  if (n && m) {
    const diff = m.sum - n;
    good = diff > 0;
    text = good ? "На " + rub(diff) + " (" + Math.max(1, Math.round(diff / m.sum * 100)) + " %) дешевле самого выгодного сейчас."
      : "Это не дешевле того, что уже есть в списке ниже.";
  }
  $("watchGap").textContent = text;
  $("watchGap").classList.toggle("good", good);
}

function ago(now, t) {
  const m = Math.max(0, Math.round((now - t) / 60));
  if (m < 1) return "только что";
  if (m < 60) return m + " мин назад";
  const h = Math.round(m / 60);
  return h < 48 ? h + " ч назад" : Math.round(h / 24) + " дн назад";
}

// Шкала слежки: ваша цена, цена рынка на день постановки и находки точками. Подписи не вылезают за края:
// каждая сдвинута влево на ту же долю, на которой стоит.
function gauge(w) {
  if (!w.market) return "";
  const hits = (w.found || []).map((h) => h.price + (h.delivery || 0));
  const all = [w.target, w.market].concat(hits);
  const lo = Math.min.apply(null, all) * 0.95, hi = Math.max.apply(null, all) * 1.04;
  const at = (v) => Math.round((v - lo) / (hi - lo) * 1000) / 10;
  const t = at(w.target), m = at(w.market);
  const cap = (cls, pos, text) => '<span class="cap ' + cls + '" style="left:' + pos + "%;transform:translateX(-" + pos + '%)">' + text + "</span>";
  return '<div class="gauge">' + cap("t", t, "ваша цена <b>" + rub(w.target) + "</b>") +
    '<div class="bar"><i class="gap" style="left:' + Math.min(t, m) + "%;width:" + Math.abs(m - t) + '%"></i>' +
    '<i class="mkt" style="left:' + m + '%"></i><i class="you" style="left:' + t + '%"></i>' +
    hits.map((v) => '<i class="hit" style="left:' + at(v) + '%"></i>').join("") + "</div>" +
    cap("m", m, "было на рынке <b>" + rub(w.market) + "</b>") + "</div>";
}

function watchCard(w, now) {
  const wishes = Object.keys(w.answers || {}).map((k) => k + ": " + w.answers[k].join(", ")).join("; ");
  const pill = w.paused ? ["warn", "На паузе"] : w.note ? ["bad", "Не проверилась"] : ["ok", "Следит"];
  const hits = (w.found || []).map((h) => {
    const sum = h.price + (h.delivery || 0);
    const fresh = h.at > (seenBefore || 0);
    return '<a' + (fresh ? ' class="new"' : "") + ' data-key="h-' + esc(w.id) + "-" + esc(h.id) + "-" + sum + '" href="' + esc(h.url) +
      '" target="_blank" rel="noopener"><b>' + rub(sum) + "</b>" +
      "<span>" + esc(h.title) + (h.delivery == null ? " · без цены доставки" : "") +
      (h.was ? " · <i>подешевело с " + rub(h.was) + "</i>" : "") +
      (w.median && h.price < w.median ? " · <i>на " + rub(w.median - h.price) + " ниже рынка</i>" : "") + "</span>" +
      (fresh ? "<em>новое</em>" : "") + "<small>" + ago(now, h.at) + "</small></a>";
  }).join("");
  // подходят по характеристикам, но чуть дороже цели: слежка открывает их поимённо и ловит снижение цены
  const nearAll = w.near || [];
  const near = nearAll.slice(0, 3).map((n) =>
    '<a data-key="n-' + esc(w.id) + "-" + esc(n.id) + '" href="' + esc(n.url) + '" target="_blank" rel="noopener"><b>' +
    rub(n.price + (n.delivery || 0)) + "</b><span>" + esc(n.title) + "</span></a>").join("");
  const all = nearAll.concat(w.found || []);
  const faved = all.filter((e) => e.fav === true).length, failed = all.filter((e) => e.fav === "no").length;
  const extras = [w.private ? "Только частные продавцы." : "",
    w.fav ? "В избранном Авито: " + faved + (failed ? ", не удалось добавить: " + failed : "") + "." : ""].filter(Boolean).join(" ");
  return '<div class="card watch' + (w.paused ? " paused" : "") + '" data-key="' + vt("w", w.id) + '" style="view-transition-name:' + vt("w", w.id) + '">' +
    '<div class="row"><span class="pill ' + pill[0] + '"><i class="dot"></i>' + pill[1] + "</span>" +
    '<span class="hint">' + (w.checked ? "проверено " + ago(now, w.checked) : "первая проверка вот-вот") + "</span></div>" +
    '<p class="name">«' + esc(w.text) + "»</p>" +
    '<p class="hint">' + (wishes ? esc(wishes) + ". " : "") + (w.city ? "Доставка в " + esc(w.city) : "Поиск по всем регионам") + ".</p>" +
    (w.note && !w.paused ? '<p class="hint">' + esc(w.note) + "</p>" : "") +
    gauge(w) +
    '<div class="row"><label for="w' + esc(w.id) + '">Жду цену с доставкой до, ₽</label>' +
    '<input id="w' + esc(w.id) + '" class="price" type="text" inputmode="numeric" maxlength="11" autocomplete="off" data-w="' + esc(w.id) +
    '" value="' + group(w.target) + '"></div>' +
    '<div class="chips small">' + EVERY.map((x) =>
      '<button type="button" data-act="wevery" data-w="' + esc(w.id) + '" data-e="' + x[0] + '"' + (x[0] === w.every ? ' class="on"' : "") +
      ">" + x[1] + "</button>").join("") + "</div>" +
    (hits ? '<div class="hits"><span class="hint">Нашлось по вашей цене:</span>' + hits + "</div>" : "") +
    (near ? '<div class="hits near"><span class="hint">Чуть дороже вашей цены, слежу за снижением' +
      (nearAll.length > 3 ? " (всего " + nearAll.length + ")" : "") + ":</span>" + near + "</div>" : "") +
    (extras ? '<p class="hint">' + extras + "</p>" : "") +
    '<div class="row actions"><button class="btn ghost" type="button" data-act="wpause" data-w="' + esc(w.id) + '" data-p="' + (w.paused ? "" : "1") + '">' +
    (w.paused ? "Продолжить" : "Пауза") + "</button>" +
    '<button class="link" type="button" data-act="wdel" data-w="' + esc(w.id) + '">Удалить слежку</button></div></div>';
}

function watchesHtml(s) {
  const ws = s.watches || [];
  if (!ws.length) return "";
  return ws.map((w) => watchCard(w, s.now)).join("") +
    '<p class="hint" data-key="watches-note">Находки появляются здесь, на значке расширения горит число новых. ' +
    "Проверяет ваш браузер: пока Chrome закрыт, слежки стоят.</p>";
}

// --- продажа ---

function sellHtml(v) {
  if (!v) return "";
  if (v.state === "error") {
    const mine = v.note === "Подготовка остановлена.";
    return '<div class="card" data-key="sell-end"><span class="pill ' + (mine ? "warn" : "bad") + '"><i class="dot"></i>' +
      (mine ? "Подготовка остановлена" : "Не получилось") + "</span>" + (mine ? "" : "<p>" + esc(v.note) + "</p>") +
      '<p class="hint">«' + esc(v.name) + "». То, что помощник успел заполнить, осталось во вкладке Авито.</p></div>";
  }
  if (v.state === "ask") {
    const q = v.question || { key: "", options: [] };
    return '<div class="card" data-key="sell-ask"><h2>' + esc(q.key) + "</h2>" +
      '<p class="hint">Помощник этого не знает и выдумывать не будет. Выберите вариант или напишите свой ответ.</p>' +
      (q.options.length ? '<div class="chips">' + q.options.map((o) =>
        '<button type="button" data-act="sellAnswer" data-v="' + esc(o) + '">' + esc(o) + "</button>").join("") + "</div>" : "") +
      '<input id="sellAnswerText" type="text" maxlength="200" autocomplete="off" placeholder="Свой ответ">' +
      '<div class="row"><button class="btn" type="button" data-act="sellAnswer">Ответить</button>' +
      '<button class="btn ghost" type="button" data-act="sellStop">Остановить</button></div></div>';
  }
  if (v.state === "ready") {
    return '<div class="card" data-key="sell-ready"><span class="pill ok"><i class="dot"></i>Объявление заполнено</span>' +
      "<p>«" + esc(v.name) + "». Проверьте его во вкладке Авито: название, описание, цену, адрес. Если всё верно, разместите сами.</p>" +
      (v.priced && !v.price ? '<p class="hint">Цену вы не называли: помощник поставил ' + rub(v.priced) + " по похожим объявлениям. Поменяйте, если хотите.</p>" : "") +
      (v.warn ? '<p class="hint">' + esc(v.warn) + "</p>" : "") +
      '<button class="btn" type="button" data-act="sellShow">Показать вкладку Авито</button></div>';
  }
  return '<div class="card" data-key="sell-busy"><h2 id="sNote">' + esc(v.note || "Готовлю объявление") + '</h2><div class="work-bar"></div>' +
    '<p class="hint">«' + esc(v.name) + "». Форма заполняется во вкладке Авито, её лучше не трогать, пока помощник работает" +
    (v.steps ? ". Шагов сделано: " + v.steps : "") + ".</p>" +
    '<button class="btn ghost" type="button" data-act="sellStop">Остановить</button></div>';
}

function blockedHtml(s) {
  const list = s.blocked || [];
  if (!list.length) return "";
  return '<div class="card" data-key="blocked"><h2>Скрытые продавцы</h2>' +
    '<p class="hint">Их объявления не попадают в результаты и находки. Нажмите на продавца, чтобы вернуть.</p><div class="chips small">' +
    list.map((b) => '<button type="button" data-act="unblock" data-sid="' + esc(b.id) + '">' + esc(b.name || "без имени") + " ×</button>").join("") +
    "</div></div>";
}

// --- разделы: поиск и слежка ---

// число новых находок на вкладке «Слежка»; пока она открыта, считать нечего
function paintCount() {
  const n = tab === "watch" || !state ? 0 : state.fresh || 0;
  $("tabCount").hidden = !n;
  $("tabCount").textContent = n;
}

// Человек смотрит слежки: серверу сообщаем, что находки увидены, значок расширения гаснет.
// Пометки «новое» при этом остаются на экране, пока раздел открыт (их держит seenBefore).
async function markSeen() {
  if (marking) return;
  marking = true;
  try {
    const s = await api("POST", "watch", { seen: true });
    state.fresh = s.fresh;
    state.seen = s.seen;
    chrome.action.setBadgeText({ text: "" });
  } catch (e) { /* не получилось сейчас, получится при следующем опросе */ }
  marking = false;
}

function hack() {
  return hasBuyer && mode === "hack";
}

// Что видно при текущем режиме. «Хакатон» работает без кода активации, поиску по Авито код нужен.
function layout() {
  $("modes").hidden = !hasBuyer;
  document.querySelectorAll("#modes button").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === mode);
    b.setAttribute("aria-selected", String(b.dataset.mode === mode));
  });
  document.documentElement.classList.toggle("guest", guest());
  $("pairBox").hidden = !!cfg || hack();
  $("work").hidden = !cfg && !hack() && !guest();
  document.querySelector(".foot").hidden = !cfg;
  if (hack()) $("webNote").hidden = true;
  if (!cfg) status(hack() ? "Режим «Хакатон» открыт без входа" : guest() ? "Поиск открыт без входа" : "Не активировано", hack() || guest() ? "ok" : "");
  setTab(guest() ? "search" : tab);
}

function setTab(name) {
  tab = name === "watch" || name === "sell" ? name : "search";
  $("tabs").hidden = hack();
  $("tabSearch").hidden = hack() || tab !== "search";
  $("tabWatch").hidden = hack() || tab !== "watch";
  $("tabSell").hidden = hack() || tab !== "sell";
  $("tabBuyer").hidden = !hack();
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tab === tab);
    b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  });
  replay($(hack() ? "tabBuyer" : { watch: "tabWatch", sell: "tabSell" }[tab] || "tabSearch"), "enter");
  chrome.storage.local.set({ tab });
  if (state) {
    if (tab === "watch") seenBefore = state.seen || 0;
    draw();
  }
  thumbs();
}

function loginHtml(j) {
  // Вошедшему Авито показывает цену и срок доставки до его адреса, без входа только общие слова
  if (!j || j.login !== false) return "";
  return '<div class="card warned" data-key="login"><span class="pill warn"><i class="dot"></i>Вы не вошли в Авито</span>' +
    "<p>Войдите в Авито в этом браузере: вошедшему он показывает цену и срок доставки до вашего адреса, и списки будут точнее. " +
    "Вход вы делаете сами, помощник только откроет страницу.</p>" +
    '<button class="btn ghost" data-act="login">Открыть вход в Авито</button></div>';
}

function draw() {
  const s = state, j = s.job;
  if (seenBefore === null) seenBefore = s.seen || 0;
  render($("login"), loginHtml(j));
  const busy = !!j && !["done", "error", "ask"].includes(j.state);
  if (j && j.state === "ask") stage(j);
  if (busy && busyJob === j.id && $("pNote")) {
    updateBusy(j);
  } else {
    const parts = outHtml(j);
    render($("out"), parts[0]);
    render($("list"), parts[1]);
    busyJob = busy ? j.id : null;
  }
  const done = !!j && j.state === "done";
  $("watchBox").hidden = !done;
  if (done) {
    $("watchHint").textContent = marketHint(j);
    gapHint();
  }
  // пока человек набирает цену в карточке слежки, список не перерисовываем: набранное пропало бы
  const typing = document.activeElement && document.activeElement.tagName === "INPUT" && $("watches").contains(document.activeElement);
  if (!typing) render($("watches"), watchesHtml(s));
  render($("blocked"), blockedHtml(s));
  $("tgBox").checked = !!s.tg;
  // на сайте подсказываем, если расширение, которое читает Авито, сейчас не работает
  $("webNote").hidden = !WEB || !!s.ext || hack();
  // режим «Хакатон» есть только там, где на сервере лежит решение кейса
  if (hasBuyer !== !!s.buyer) {
    hasBuyer = !!s.buyer;
    layout();
  }
  // пока человек набирает свой ответ на вопрос помощника, карточку не перерисовываем
  const answering = document.activeElement && document.activeElement.id === "sellAnswerText";
  if (!answering) render($("sellOut"), sellHtml(s.sell));
  $("watchEmpty").hidden = (s.watches || []).length > 0;
  paintCount();
  if (tab === "watch" && s.fresh > 0 && document.visibilityState === "visible") markSeen();
  if (!$("city").value && document.activeElement !== $("city")) $("city").value = s.city || "";
  thumbs();
}

// Показать ответ сервера. smooth: экран меняется после действия человека, старое плавно переходит в новое.
function show(s, smooth) {
  state = s;
  if (smooth && !REDUCE && document.startViewTransition) {
    const t = document.startViewTransition(draw);
    t.ready.catch(() => {});
    t.finished.catch(() => {});
  } else {
    draw();
  }
}

// --- опрос сервера ---

async function poll() {
  clearTimeout(timer);
  if (!cfg && !(guest() && mode === "avito")) return;
  let next = 5000;
  try {
    const s = await api("GET", "state");
    const asking = s.job && s.job.state === "ask" && state && state.job && state.job.state === "ask" && state.job.id === s.job.id;
    if (!asking) show(s);
    // открыли помощника, а слежка что-то нашла: сразу показываем находки
    if (!landed && s.fresh > 0 && tab !== "watch") setTab("watch");
    landed = true;
    status(guest() ? "Поиск открыт без входа" : "Связь с сервером есть", "ok");
    const busy = (s.job && s.job.state !== "done" && s.job.state !== "error") ||
      (s.sell && s.sell.state !== "ready" && s.sell.state !== "error");
    next = busy ? 2000 : 15000;
  } catch (e) {
    status(e.message, "bad");
  }
  timer = setTimeout(poll, next);
}

// Действие человека: запрос, плавная смена экрана, короткое сообщение о сделанном (ok) или об ошибке.
async function send(button, path, body, ok) {
  if (button) button.disabled = true;
  try {
    show(await api("POST", path, body), true);
    if (ok) toast(ok);
  } catch (e) {
    toast(e.message, true);
  }
  if (button) button.disabled = false;
  poll();
}

// --- действия ---

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  answers = {};
  send($("go"), "search", { text: $("text").value.trim(), city: $("city").value.trim(), private: $("private").checked });
});

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act;
  if (act === "pick") {
    const list = answers[el.dataset.k] = answers[el.dataset.k] || [];
    const at = list.indexOf(el.dataset.o);
    if (at >= 0) list.splice(at, 1); else list.push(el.dataset.o);
    el.classList.toggle("on", at < 0);
    replay(el, "pop");
  } else if (act === "answer") {
    send(el, "answer", { job: state.job.id, answers });
  } else if (act === "stop") {
    send(el, "stop", { job: state.job.id });
  } else if (act === "sort") {
    sort = el.dataset.s;
    show(state, true);
  } else if (act === "login") {
    chrome.tabs.create({ url: LOGIN_URL });
  } else if (act === "tab") {
    setTab(el.dataset.tab);
  } else if (act === "sellAnswer") {
    const typed = $("sellAnswerText") ? $("sellAnswerText").value.trim() : "";
    send(el, "sell", { answer: el.dataset.v || typed });
  } else if (act === "sellStop") {
    send(el, "sell", { stop: true });
  } else if (act === "sellShow") {
    send(null, "sell", { show: true });
  } else if (act === "block") {
    e.preventDefault();   // кнопка стоит внутри карточки-ссылки: объявление при этом не открываем
    send(null, "seller", { id: el.dataset.sid, name: el.dataset.name }, "Продавец скрыт. Вернуть можно внизу вкладки «Поиск»");
  } else if (act === "unblock") {
    send(null, "seller", { unblock: el.dataset.sid }, "Продавец снова виден");
  } else if (act === "wevery") {
    // выбор виден сразу, не дожидаясь ответа сервера
    el.parentNode.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === el));
    replay(el, "pop");
    send(null, "watch", { id: el.dataset.w, every: Number(el.dataset.e) },
      "Проверка раз в " + el.textContent.trim() + (Number(el.dataset.e) <= 2 ? ". " + OFTEN : ""));
  } else if (act === "wpause") {
    send(el, "watch", { id: el.dataset.w, paused: !!el.dataset.p }, el.dataset.p ? "Слежка на паузе" : "Слежка снова идёт");
  } else if (act === "wdel") {
    // удаление в два нажатия: слежку с её находками не вернуть
    if (!el.dataset.sure) {
      el.dataset.sure = "1";
      el.textContent = "Точно удалить?";
      el.classList.add("sure");
      return;
    }
    send(el, "watch", { id: el.dataset.w, delete: true }, "Слежка удалена");
  }
});

// готовый пример под полем: текст встаёт в поле, курсор в конец
document.addEventListener("click", (e) => {
  const b = e.target.closest(".tries button");
  if (!b) return;
  const field = $(b.parentNode.dataset.fill);
  field.value = b.textContent.trim();
  field.focus();
  field.setSelectionRange(field.value.length, field.value.length);
});

// круг от места нажатия на кнопке
document.addEventListener("pointerdown", (e) => {
  const b = e.target.closest(".btn, .chips button, .seg button");
  if (!b || b.disabled || REDUCE || b.closest(".tries")) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2, dot = document.createElement("span");
  dot.className = "ripple";
  dot.style.cssText = "width:" + d + "px;height:" + d + "px;left:" + (e.clientX - r.left - d / 2) + "px;top:" + (e.clientY - r.top - d / 2) + "px";
  b.appendChild(dot);
  dot.addEventListener("animationend", () => dot.remove());
});

// Новая слежка из результатов поиска
$("every").addEventListener("click", (e) => {
  const b = e.target.closest("[data-every]");
  if (!b) return;
  every = Number(b.dataset.every);
  document.querySelectorAll("#every button").forEach((x) => x.classList.toggle("on", x === b));
  replay(b, "pop");
  $("everyHint").hidden = every > 2;
});

$("tgBox").addEventListener("change", () => {
  const on = $("tgBox").checked;
  send(null, "settings", { tg: on }, on ? "Находки будут приходить и в Telegram" : "В Telegram о находках больше не пишу");
});

// цена набирается с пробелами между тысячами: 32 000
function tidyPrice(el) {
  const n = digits(el.value);
  el.value = n ? group(n) : "";
}

$("target").addEventListener("input", () => {
  tidyPrice($("target"));
  $("watchMsg").textContent = "";
  gapHint();
});

// Цена нужна целиком в рублях. «32» вместо «32 000» ловим по рынку: слежка за ценой в двадцать раз ниже
// рыночной ничего не найдёт, а человек будет ждать.
function priceBad(target, sum) {
  if (!target) return "Напишите вашу цену цифрами, например 32 000.";
  if (sum && target * 20 < sum) return "Цена нужна целиком в рублях: например " + group(Math.round(sum * 0.9 / 100) * 100) + ".";
  return "";
}

$("watch").addEventListener("click", async () => {
  const target = digits($("target").value);
  const m = market(state.job);
  // ошибку пишем рядом с кнопкой: сообщение наверху страницы отсюда не видно
  $("watchMsg").textContent = priceBad(target, m && m.sum);
  if ($("watchMsg").textContent) {
    replay($("target"), "shake");
    return;
  }
  $("watch").disabled = true;
  try {
    show(await api("POST", "watch", { job: state.job.id, target, every, fav: $("favBox").checked }), true);
    $("target").value = "";
    gapHint();
    toast("Слежка поставлена, первая проверка через несколько секунд");
    setTab("watch");
    window.scrollTo({ top: 0, behavior: REDUCE ? "auto" : "smooth" });
  } catch (e) {
    $("watchMsg").textContent = e.message;
    replay($("target"), "shake");
  }
  $("watch").disabled = false;
  poll();
});

$("watches").addEventListener("input", (e) => {
  const el = e.target.closest("input[data-w]");
  if (el) tidyPrice(el);
});

// Своя цена в карточке слежки: меняется по Enter или уходу из поля
$("watches").addEventListener("change", (e) => {
  const el = e.target.closest("input[data-w]");
  if (!el) return;
  const w = (state.watches || []).find((x) => x.id === el.dataset.w);
  const target = digits(el.value);
  if (!w || target === w.target) return;
  const bad = priceBad(target, w.market);
  if (bad) {
    toast(bad, true);
    replay(el, "shake");
    return;
  }
  el.blur();
  send(null, "watch", { id: w.id, target }, "Теперь жду цену до " + rub(target));
});

$("pair").addEventListener("click", async () => {
  let got;
  try {
    const raw = $("code").value.trim().split(":").pop().replace(/-/g, "+").replace(/_/g, "/");
    got = JSON.parse(atob(raw));
    if (!/^https:\/\/[^/]+\/.*\/$/.test(got.u) || !got.t) throw new Error();
    // сайт ходит к серверу через свой же адрес: так браузер не считает запрос чужим
    if (WEB) got.u = location.origin + "/app/";
  } catch (e) {
    $("pairMsg").textContent = "Это не похоже на код из бота.";
    replay($("code"), "shake");
    return;
  }
  // Расширению нужен доступ к адресу сервера: Chrome спросит разрешение один раз.
  // Firefox не выдаёт доступ к сайтам при установке: просим сразу и сервер, и Авито
  const ok = await chrome.permissions.request({ origins: [new URL(got.u).origin + "/*", "https://www.avito.ru/*"] });
  if (!ok) {
    $("pairMsg").textContent = "Без разрешения расширение не сможет связаться с сервером.";
    return;
  }
  await chrome.storage.local.set({ cfg: got, status: null });
  chrome.runtime.sendMessage("kick");
  start();
});

async function showView() {
  const { view = "tab" } = await chrome.storage.local.get("view");
  // боковой панели в Firefox нет
  if (!chrome.sidePanel) document.querySelector('#views [data-view="side"]').hidden = true;
  document.querySelectorAll("#views button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  thumbs();
}

// Смена вида: запоминаем, объясняем значку (sw.js) и сразу открываем помощника в новом виде, а этот закрываем.
$("views").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  const view = b.dataset.view;
  let opened = view === HERE;
  if (view === "side" && !opened) {
    try {
      await chrome.sidePanel.open({ windowId: winId });
      opened = true;
    } catch (err) { /* не открылась сама: откроется по значку */ }
  }
  await chrome.storage.local.set({ view });
  await chrome.runtime.sendMessage("view");
  showView();
  if (view === "tab" && !opened) {
    await chrome.tabs.create({ url: chrome.runtime.getURL("app.html") });
    opened = true;
  }
  if (view === "popup" && !opened) {
    try {
      await chrome.action.openPopup();
      opened = true;
    } catch (err) { /* Chrome не дал открыть окошко без нажатия на значок */ }
  }
  if (view === HERE) return;
  if (!opened) {
    toast("Готово: теперь помощник открывается по значку расширения");
    return;
  }
  if (HERE === "tab") {
    const me = await chrome.tabs.getCurrent();
    if (me) chrome.tabs.remove(me.id);
  } else {
    window.close();
  }
});

// --- продажа: форма и фотографии ---

// Фотографии уменьшаются до 1600 точек по длинной стороне и складываются во временное хранилище расширения:
// оттуда их возьмёт фоновая часть, когда помощник дойдёт до поля фотографий в форме Авито.
async function shrink(file) {
  const img = await createImageBitmap(file);
  const k = Math.min(1, 1600 / Math.max(img.width, img.height));
  const canvas = new OffscreenCanvas(Math.round(img.width * k), Math.round(img.height * k));
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.85 });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 8192) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
  return { name: file.name.replace(/\.[^.]*$/, "") + ".jpg", type: "image/jpeg", b64: btoa(bin) };
}

let photoCount = 0;
$("sellPick").addEventListener("click", () => $("sellFiles").click());
$("sellFiles").addEventListener("change", async () => {
  const files = Array.from($("sellFiles").files).slice(0, 8);
  $("sellPhotoNote").textContent = files.length ? "Готовлю фото…" : "";
  try {
    const photos = [];
    for (const f of files) photos.push(await shrink(f));
    await chrome.storage.session.set({ sellPhotos: photos });
    photoCount = photos.length;
    $("sellPhotoNote").textContent = photoCount ? "Фото: " + photoCount : "";
  } catch (e) {
    photoCount = 0;
    $("sellPhotoNote").textContent = "Фото не прочитались, попробуйте другие";
  }
});

$("sellPrice").addEventListener("input", () => tidyPrice($("sellPrice")));

$("sellForm").addEventListener("submit", (e) => {
  e.preventDefault();
  send($("sellGo"), "sell", { name: $("sellName").value.trim(), details: $("sellDetails").value.trim(),
                              price: digits($("sellPrice").value) || null, photos: photoCount });
});

// --- подбор: диалог с решением кейса хакатона ---
// Сервер каждый раз получает всю историю, как того требует контракт кейса: решение ничего не помнит между вызовами.
// Ответ помощника уходит в историю текстом (вопросы, объяснения или сообщение), так же делает проверяющий.

const KIND = { table: "Стол", chair: "Кресло", lamp: "Лампа" };

function buyerText(r) {
  if (r.status === "clarify") return r.questions.map((q) => q.text).join(" ");
  if (r.status === "no_match") return r.message;
  return r.options.map((o, n) => (n + 1) + ") " + o.explanation).join("\n");
}

function buyerFacts(i) {
  const a = i.attributes || {};
  if (i.category === "table" && typeof a.width_cm === "number") return "ширина " + a.width_cm + " см";
  if (i.category === "chair" && typeof a.adjustable_height === "boolean") return a.adjustable_height ? "с регулировкой высоты" : "без регулировки";
  if (i.category === "lamp" && typeof a.color_temperature_k === "number") return a.color_temperature_k + " К";
  return "";
}

// Как помощник понял запрос: видно, что из слов получилась структура, по которой идёт подбор.
function buyerGot(t, n) {
  if (!t.understood || !t.understood.length) return "";
  return '<div class="got" data-key="g' + n + '"><span>Понял так</span>' + t.understood.filter((g) => !g.off).map((g) =>
    '<i class="' + (g.known ? "" : "ask") + '"><b>' + esc(g.label) + "</b> " + esc(g.text) + "</i>").join("") + "</div>";
}

function buyerAnswer(t, n) {
  return buyerGot(t, n) + buyerCards(t, n);
}

function buyerCards(t, n) {
  const r = t.result;
  // посторонний вопрос при известных условиях: говорим, что на такое не отвечаем, и не повторяем прежние варианты
  const off = (t.understood || []).find((g) => g.off);
  if (off && r.status === "ok") {
    return '<div class="card" data-key="b' + n + '"><span class="pill warn"><i class="dot"></i>Не по теме</span><p>' + esc(off.note) +
      "</p><p class=\"hint\">Ваш прежний запрос в силе. Напишите «покажи варианты», и я выведу их снова, или измените условия.</p></div>";
  }
  const json = $("buyerJson").checked ? '<pre class="json">' + esc(JSON.stringify(r, null, 2)) + "</pre>" : "";
  const took = t.took ? '<p class="hint">Ответ за ' + String(t.took).replace(".", ",") + " с</p>" : "";
  if (r.status === "clarify") {
    return '<div class="card" data-key="b' + n + '"><span class="pill warn"><i class="dot"></i>Нужно уточнить</span>' +
      r.questions.map((q) => "<p>" + esc(q.text) + "</p>").join("") + took + json + "</div>";
  }
  if (r.status === "no_match") {
    return '<div class="card" data-key="b' + n + '"><span class="pill bad"><i class="dot"></i>Комплекта нет</span><p>' + esc(r.message) + "</p>" +
      took + json + "</div>";
  }
  // чем вариант отличается от первого: цена и какие товары другие
  const first = r.options[0], base = new Set(first.listing_ids);
  return r.options.map((o, k) => {
    const more = o.total_rub - first.total_rub;
    // пометка «другой» нужна, только когда отличается часть комплекта; иначе пишем об этом один раз
    const changed = o.listing_ids.filter((id) => !base.has(id)).length, part = k > 0 && changed > 0 && changed < o.listing_ids.length;
    const rich = (t.understood || []).some((g) => g.text === "самое дорогое");
    const wishes = (t.understood || []).some((g) => g.label === "Пожелание" && /подороже|подешевле/.test(g.text));
    const note = k === 0 ? (wishes ? "по вашим пожеланиям" : rich ? "самый дорогой" : "самый дешёвый") :
      (more > 0 ? "дороже на " + rub(more) : more < 0 ? "дешевле на " + rub(-more) : "за те же деньги") +
      (o.listing_ids.length > 1 && changed === o.listing_ids.length ? ", все товары другие" : "");
    return '<div class="card kit" data-key="b' + n + "-" + k + '"><div class="head"><span class="pill ok"><i class="dot"></i>Вариант ' + (k + 1) +
    '</span><span class="note">' + note + "</span><b>" + rub(o.total_rub) + "</b></div><ul>" + o.listing_ids.map((id) => {
      const i = t.items[id] || {};
      const facts = buyerFacts(i);
      const other = part && !base.has(id) ? '<em class="other">другой</em>' : "";
      return "<li><span><b>" + (KIND[i.category] || "Товар") + "</b> " + esc(i.title || id) + (facts ? ", " + facts : "") + other + "</span><span>" +
        (i.price_rub ? rub(i.price_rub) : "") + "</span></li>";
    }).join("") + '</ul><p class="hint">' + esc(o.explanation) + "</p>" + (k === r.options.length - 1 ? took + json : "") + "</div>";
  }).join("");
}

// Запрос раздела «Подбор». На сайте он открыт и без кода активации (для жюри хакатона): тогда идёт на открытый адрес.
async function buyerAsk(body) {
  if (cfg) return api("POST", "buyer", body);
  const r = await fetch(location.origin + "/app/ext/avito/buyer/open", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Не получилось, попробуйте ещё раз.");
  return data;
}

function paintBuyer(wait) {
  // пока диалога нет, на экране заголовок и примеры; с первой реплики поле ввода уходит вниз, как в чате
  const talking = buyerTalk.length > 0 || !!wait;
  $("tabBuyer").classList.toggle("talking", talking);
  $("buyerText").placeholder = talking ? "Ответьте или уточните…" : "Например: стол и лампа в Азове до 7 тысяч…";
  render($("buyerLog"), buyerTalk.map((t, n) => t.role === "user"
    ? '<p class="said" data-key="u' + n + '">' + esc(t.text) + "</p>" : buyerAnswer(t, n)).join("") +
    (wait ? '<div class="card" data-key="bw"><h2>Разбираю запрос</h2><div class="work-bar"></div></div>' : ""));
}

$("buyerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("buyerText").value.trim();
  if (!text || $("buyerGo").disabled) return;
  if (buyerTalk.length >= 19) {
    toast("Диалог длинный: начните заново", true);
    return;
  }
  buyerTalk.push({ role: "user", text });
  $("buyerText").value = "";
  $("buyerGo").disabled = true;
  paintBuyer(true);
  $("buyerForm").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "end" });
  try {
    const got = await buyerAsk({ messages: buyerTalk.map((t) => ({ role: t.role, text: t.text })) });
    buyerTalk.push({ role: "assistant", text: buyerText(got.result), result: got.result, items: got.items || {}, took: got.took,
      understood: got.understood || [] });
  } catch (err) {
    buyerTalk.pop();
    $("buyerText").value = text;
    toast(err.message, true);
  }
  $("buyerGo").disabled = false;
  paintBuyer(false);
  $("buyerForm").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "end" });
});

// Enter отправляет, Shift+Enter переносит строку
$("buyerText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("buyerForm").requestSubmit();
  }
});

$("buyerNew").addEventListener("click", () => {
  buyerTalk = [];
  paintBuyer(false);
  $("buyerText").focus();
});

$("buyerJson").addEventListener("change", () => paintBuyer(false));

// «без магазинов» запоминается между запусками
$("private").addEventListener("change", () => chrome.storage.local.set({ onlyPrivate: $("private").checked }));

$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (b) setTab(b.dataset.tab);
});

// тема страницы: как в системе, светлая или тёмная (саму тему ставит theme.js)
function paintTheme() {
  const now = window.pageTheme ? window.pageTheme.get() : "auto";
  document.querySelectorAll("#themes button").forEach((b) => {
    b.classList.toggle("on", b.dataset.theme === now);
    b.setAttribute("aria-pressed", String(b.dataset.theme === now));
  });
  thumbs();
}
$("themes").addEventListener("click", (e) => {
  const b = e.target.closest("[data-theme]");
  if (!b || !window.pageTheme) return;
  window.pageTheme.set(b.dataset.theme);
  paintTheme();
});
paintTheme();

$("modes").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mode]");
  if (!b || b.dataset.mode === mode) return;
  mode = b.dataset.mode;
  chrome.storage.local.set({ mode });
  layout();
  poll();
});

$("unpair").addEventListener("click", async () => {
  await chrome.storage.local.remove(["cfg", "status"]);
  chrome.action.setBadgeText({ text: "" });
  start();
});

// шрифты приходят чуть позже разметки и меняют ширину кнопок: ползунки ставим заново
document.fonts.ready.then(thumbs);
window.addEventListener("resize", thumbs);

async function start() {
  clearTimeout(timer);
  const kept = await chrome.storage.local.get(["cfg", "tab", "onlyPrivate", "mode"]);
  cfg = kept.cfg || null;
  if (WEB) {
    // вид (окно, сбоку, выпадающее) есть только у расширения
    document.querySelector(".foot .row").hidden = true;
    $("unpair").textContent = "Выйти на этом устройстве";
  }
  $("private").checked = !!kept.onlyPrivate;
  // на сайте «Хакатон» открыт и без кода активации; после входа есть ли он, скажет сервер
  hasBuyer = WEB && !cfg;
  mode = kept.mode === "hack" || kept.mode === "avito" ? kept.mode : cfg ? "avito" : "hack";
  tab = kept.tab;
  layout();
  showView();
  if (!cfg) {
    poll();        // гостю на сайте открыт поиск: опрос идёт, пока выбран «Поиск по Авито»
    return;
  }
  // фоновую часть Chrome усыпляет: будим её, иначе страницы Авито открывать некому
  chrome.runtime.sendMessage("kick").catch(() => {});
  poll();
}

start();
