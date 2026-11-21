// Сайт glie.ru: та же страница помощника, что в расширении (app.html, app.js, app.css берутся оттуда без копий),
// открытая в обычной вкладке любого браузера, в том числе на телефоне. Здесь только прослойка: страница написана
// под интерфейсы расширения (chrome.storage, chrome.tabs и другие), а на сайте их заменяют средства браузера.
//
// Что сайт умеет сам, а что через расширение. Страницы Авито читает и заполняет только расширение в Chrome
// человека: сайту чужая вкладка недоступна. Поэтому «Поиск», «Слежка» и «Продажа» с сайта управляют тем же
// помощником (тот же код активации), а работу делает Chrome с расширением, где бы он ни стоял. «Подбор» от
// расширения не зависит. Фотографии для объявления сайт передаёт расширению через сервер.
(function () {
  // Раньше на этом имени жила игра с веб-приложением. Её служебный скрипт и кэши могли остаться в браузере:
  // снимаем их при каждом открытии, новому сайту они не нужны (подробнее в sw.js рядом).
  try {
    if (navigator.serviceWorker) {
      navigator.serviceWorker.getRegistrations().then((list) => list.forEach((r) => r.unregister())).catch(() => {});
    }
    if (window.caches) caches.keys().then((keys) => keys.forEach((key) => caches.delete(key))).catch(() => {});
  } catch (e) { /* в закрытом режиме браузера этих средств может не быть */ }

  const PREFIX = "ouipn-avito:";
  const read = (key) => {
    try { return JSON.parse(localStorage.getItem(PREFIX + key)); } catch (e) { return null; }
  };
  const local = {
    async get(keys) {
      const out = {};
      for (const key of [].concat(keys || [])) {
        const value = read(key);
        if (value !== null && value !== undefined) out[key] = value;
      }
      return out;
    },
    async set(values) {
      for (const key of Object.keys(values)) localStorage.setItem(PREFIX + key, JSON.stringify(values[key]));
    },
    async remove(keys) {
      for (const key of [].concat(keys)) localStorage.removeItem(PREFIX + key);
    },
  };
  // Временное хранилище расширения на сайте не нужно, кроме одного: фотографии для объявления. Расширение на
  // другом конце заберёт их с сервера, когда помощник дойдёт до поля фотографий.
  const session = {
    async get() { return {}; },
    async set(values) {
      const cfg = read("cfg");
      if (!values.sellPhotos || !cfg) return;
      const r = await fetch(cfg.u + "ext/avito/photos", {
        method: "POST",
        headers: { "X-Avito-Ext": cfg.t, "Content-Type": "application/json" },
        body: JSON.stringify({ photos: values.sellPhotos }),
      });
      if (!r.ok) throw new Error("фото не передались");
    },
  };
  window.OUIPN_WEB = true;
  window.chrome = Object.assign(window.chrome || {}, {
    storage: { local, session },
    runtime: { sendMessage: async () => true, getURL: (path) => path },
    windows: { getCurrent: async () => ({ id: 0 }) },
    tabs: { create: (o) => window.open(o.url, "_blank", "noopener"), getCurrent: async () => null, remove() {} },
    permissions: { request: async () => true },
    sidePanel: { open: async () => {} },
    // на сайте вместо значка расширения число новых находок стоит в заголовке вкладки
    action: { openPopup: async () => {}, setBadgeText: (o) => { document.title = (o.text ? "(" + o.text + ") " : "") + "Помощник по Авито"; } },
  });
})();
