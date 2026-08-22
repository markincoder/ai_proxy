/**
 * Общая шапка: бренд, ссылки, баланс, пополнение, вход/выход (все страницы).
 */
(function () {
  const api = (path, opts) => fetch(path, { credentials: "include", ...opts });

  /** После входа остаёмся в чате; новости открываются из меню. */
  async function maybeRedirectToUnreadNews() {
    return false;
  }

  async function loadMe() {
    try {
      const r = await api("/api/auth/me");
      if (!r.ok) return { guest: true, balance: null, isAdmin: false };
      const j = await r.json().catch(() => null);
      return j && typeof j === "object" ? j : { guest: true, balance: null, isAdmin: false };
    } catch {
      return { guest: true, balance: null, isAdmin: false };
    }
  }

  async function loadSupportUnread() {
    try {
      const r = await api("/api/support/unread");
      if (!r.ok) return 0;
      const j = await r.json().catch(() => null);
      if (!j || typeof j !== "object") return 0;
      const n = Number(j.unreadCount);
      return Number.isNaN(n) ? 0 : n;
    } catch {
      return 0;
    }
  }

  function setNavUnreadBadge(el, count) {
    if (!el) return;
    const existing = el.querySelector(".nav-link-badge");
    if (count <= 0) {
      if (existing) existing.remove();
      return;
    }
    if (existing) {
      existing.textContent = String(count);
      return;
    }
    const span = document.createElement("span");
    span.className = "nav-link-badge";
    span.textContent = String(count);
    span.setAttribute("aria-label", `Непрочитанных ответов: ${count}`);
    el.appendChild(span);
  }

  function formatBalance(raw) {
    if (raw == null || String(raw).trim() === "") {
      return `${Number(0).toLocaleString("ru-RU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      })} ₽`;
    }
    const n = Number(raw);
    if (Number.isNaN(n)) {
      return `${Number(0).toLocaleString("ru-RU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      })} ₽`;
    }
    return `${n.toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    })} ₽`;
  }

  async function initAppNav() {
    const me = await loadMe();

    const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    // Документы оферты должны читаться до галочки на /consent — иначе страница
    // вспыхивает и app-nav возвращает на форму согласия (похожа на логин).
    const termsGateAllow =
      path === "/consent" || path === "/terms" || path === "/privacy";
    if (me && me.guest !== true && me.termsAccepted === false) {
      if (!termsGateAllow) {
        let dest = location.pathname + location.search;
        if (path === "/login") dest = "/";
        location.replace("/consent?next=" + encodeURIComponent(dest));
        return;
      }
    }

    const redirected = await maybeRedirectToUnreadNews();
    if (redirected) return;
    if (me && me.guest !== true) {
      try {
        const m = await import("/static/js/identity-links.js?v=4");
        m.persistIdentityLinksFromMe(me);
      } catch {
        /* ignore */
      }
    }
    const cfg = await api("/api/config").then((r) => r.json().catch(() => ({})));
    const isGuest = me.guest === true;

    const balanceEl = document.getElementById("balance");
    const balanceWrap = document.getElementById("balance-wrap");
    const btnPay = document.getElementById("btn-pay");
    const navLogin = document.getElementById("nav-login");
    const btnLogout = document.getElementById("btn-logout");
    const navAdmin = document.getElementById("nav-admin");
    const navSettings = document.getElementById("nav-settings");

    if (navLogin) {
      try {
        navLogin.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      } catch {
        navLogin.href = "/login";
      }
    }

    if (isGuest) {
      if (navLogin) {
        if (location.pathname === "/login") {
          navLogin.style.display = "none";
        } else {
          navLogin.style.removeProperty("display");
        }
      }
      if (btnLogout) btnLogout.style.display = "none";
      if (balanceWrap) balanceWrap.setAttribute("hidden", "");
      if (btnPay) btnPay.style.display = "none";
      if (navSettings) navSettings.style.display = "none";
    } else {
      if (navLogin) navLogin.style.display = "none";
      if (btnLogout) btnLogout.style.removeProperty("display");
      if (balanceWrap) balanceWrap.removeAttribute("hidden");
      if (balanceEl) balanceEl.textContent = formatBalance(me.balance);
      if (balanceWrap && balanceEl) {
        balanceWrap.title = `Баланс: ${balanceEl.textContent}`;
      }
      if (navSettings) navSettings.style.removeProperty("display");
    }

    if (me.isAdmin && navAdmin) navAdmin.style.removeProperty("display");

    const navContact = document.getElementById("nav-contact");
    if (!isGuest && navContact) {
      const unread = await loadSupportUnread();
      setNavUnreadBadge(navContact, unread);
    }

    if (btnLogout) {
      btnLogout.onclick = async () => {
        await api("/api/auth/logout", { method: "POST" });
        location.href = "/login";
      };
    }

    const onChatPage = !!document.getElementById("pay-modal");

    if (btnPay && !onChatPage) {
      if (isGuest || !cfg || !cfg.yookassaEnabled) {
        btnPay.style.display = "none";
      } else {
        const shopId = cfg.yookassaShopId ? String(cfg.yookassaShopId).trim() : "";
        btnPay.style.display = "";
        if (shopId) {
          btnPay.onclick = () => {
            location.href = "/?openPay=1";
          };
        } else {
          btnPay.onclick = () => {
            alert("Укажите YOOKASSA_SHOP_ID в .env и перезапустите сервер.");
          };
        }
      }
    }

    try {
      window.__II_PROXY_ME = me;
      window.__II_PROXY_CONFIG = cfg;
    } catch {
      /* ignore */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAppNav);
  } else {
    initAppNav();
  }
})();
