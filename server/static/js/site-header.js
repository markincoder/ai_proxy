/**
 * Единая шапка сайта: логотип, меню, блок баланса/входа.
 * Активный пункт меню выставляется по location.pathname.
 */
(function () {
  const LINKS = [
    { href: "/news", label: "Новости" },
    { href: "/", label: "Чат" },
    { href: "/tariffs", label: "Тарифы" },
    { href: "/settings", label: "Настройки", id: "nav-settings" },
    { href: "/admin", label: "Админка", id: "nav-admin", adminOnly: true },
    { href: "/docs", label: "API" },
    { href: "/contact", label: "Контакты" },
  ];

  function normalizePath() {
    let p = location.pathname || "/";
    if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
    return p || "/";
  }

  function isNavActive(href, path) {
    if (href === "/") return path === "/";
    return path === href || path.startsWith(href + "/");
  }

  function inject() {
    const root = document.getElementById("site-header-root");
    if (!root) return;

    const path = normalizePath();
    const linksHtml = LINKS.map((item) => {
      const active = isNavActive(item.href, path) ? " active" : "";
      const idAttr = item.id ? ` id="${item.id}"` : "";
      const styleAttr = item.adminOnly ? ` style="display: none"` : "";
      return `<a href="${item.href}" class="nav-link${active}"${idAttr}${styleAttr}>${item.label}</a>`;
    }).join("");

    root.outerHTML = `<header class="top-nav" role="navigation" aria-label="Главное меню">
      <a href="/" class="brand">
        <span class="brand-mark" aria-hidden="true">II</span>
        <span>II Proxy</span>
      </a>
      <nav class="nav-links">${linksHtml}</nav>
      <div class="nav-actions">
        <span class="balance-pill" id="balance-wrap"
          >Баланс <strong id="balance">—</strong></span
        >
        <button type="button" class="btn btn-accent" id="btn-pay" style="display: none">Пополнить</button>
        <a href="/login" class="btn btn-primary" id="nav-login" style="display: none">Войти</a>
        <button type="button" class="btn" id="btn-logout" title="Завершить сессию">Выйти</button>
      </div>
    </header>`;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
