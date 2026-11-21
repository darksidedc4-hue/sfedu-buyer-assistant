// Тема страницы: как в системе, светлая или тёмная. Подключается в <head> раньше всего остального, чтобы страница
// сразу рисовалась в нужной теме. Выбор лежит в localStorage: он нужен до первой отрисовки, а хранилище расширения
// отвечает не сразу. Светлая тема это класс light на <html>; о смене сообщает событие themechange (его слушает фон).
(() => {
  const system = window.matchMedia("(prefers-color-scheme: light)");
  const get = () => {
    try { return localStorage.getItem("theme") || "auto"; } catch (e) { return "auto"; }
  };
  const apply = () => {
    const choice = get();
    document.documentElement.classList.toggle("light", choice === "light" || (choice !== "dark" && system.matches));
    document.dispatchEvent(new Event("themechange"));
  };
  window.pageTheme = {
    get,
    set(choice) {
      try { localStorage.setItem("theme", choice); } catch (e) { /* без хранилища выбор живёт до закрытия страницы */ }
      if (choice === "light" || choice === "dark") document.documentElement.classList.toggle("light", choice === "light");
      apply();
    },
  };
  system.addEventListener("change", apply);
  apply();
})();
