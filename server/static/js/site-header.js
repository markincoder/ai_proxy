/**
 * Единая шапка сайта: логотип, меню, блок баланса/входа.
 * Активный пункт меню выставляется по location.pathname.
 */
(function () {
  const LINKS = [
    { href: "/news", label: "Новости" },
    { href: "/", label: "Чат", navClass: "nav-link--chat" },
    { href: "/tariffs", label: "Модели и тарифы" },
    { href: "/settings", label: "Настройки", id: "nav-settings", loggedInOnly: true },
    { href: "/contact", label: "Контакты", id: "nav-contact" },
    { href: "/developers", label: "Разработчикам" },
    { href: "/admin", label: "Админка", id: "nav-admin", adminOnly: true },
  ];

  function normalizePath() {
    let p = location.pathname || "/";
    if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
    return p || "/";
  }

  /**
   * Один активный пункт: при совпадении префиксов выигрывает самый длинный href
   * (исключает двойную подсветку «Чат» + другой раздел на некоторых путях).
   */
  function activeNavHrefForPath(path) {
    let best = null;
    let bestLen = -1;
    for (const item of LINKS) {
      const href = item.href;
      if (href === "/") {
        if (path === "/" && 1 > bestLen) {
          best = href;
          bestLen = 1;
        }
        continue;
      }
      if (path === href || path.startsWith(href + "/")) {
        const len = href.length;
        if (len > bestLen) {
          best = href;
          bestLen = len;
        }
      }
    }
    return best;
  }

  function inject() {
    const root = document.getElementById("site-header-root");
    if (!root) return;

    const path = normalizePath();
    const activeHref = activeNavHrefForPath(path);
    const linksHtml = LINKS.map((item) => {
      const active = item.href === activeHref ? " active" : "";
      const extraClass = item.navClass ? ` ${item.navClass}` : "";
      const idAttr = item.id ? ` id="${item.id}"` : "";
      const styleAttr =
        item.adminOnly || item.loggedInOnly ? ` style="display: none"` : "";
      return `<a href="${item.href}" class="nav-link${active}${extraClass}"${idAttr}${styleAttr}>${item.label}</a>`;
    }).join("");

    root.outerHTML = `<header class="top-nav" role="navigation" aria-label="Главное меню">
      <a href="/" class="brand">
        <span class="brand-mark" aria-hidden="true">II</span>
        <span>Proxy</span>
      </a>
      <nav class="nav-links">${linksHtml}</nav>
      <div class="nav-actions">
        <span class="balance-pill" id="balance-wrap" hidden
          ><span class="balance-pill-pref" aria-hidden="true">Баланс&nbsp;</span><strong id="balance">—</strong></span
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
