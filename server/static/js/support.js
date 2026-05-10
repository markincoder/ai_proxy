(function () {
  const api = (path, opts) => fetch(path, { credentials: "include", ...opts });
  const state = {
    captchaToken: null,
    activeTicketId: null,
    isGuest: true,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function showMsg(text, isErr) {
    const el = $("support-msg");
    if (!el) return;
    el.style.display = "block";
    el.textContent = text;
    el.className = "login-local-msg" + (isErr ? " login-local-msg--err" : " login-local-msg--ok");
  }

  function clearMsg() {
    const el = $("support-msg");
    if (!el) return;
    el.style.display = "none";
    el.textContent = "";
  }

  function showAuthHint(text) {
    const el = $("support-auth-hint");
    if (!el) return;
    if (!text) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.style.display = "block";
    el.textContent = text;
  }

  function formatApiError(detail) {
    if (typeof detail === "string") return detail;
    if (!Array.isArray(detail)) return "";
    const parts = [];
    detail.forEach((err) => {
      if (!err || typeof err !== "object") return;
      const loc = Array.isArray(err.loc) ? err.loc : [];
      const field = loc.includes("subject")
        ? "Тема"
        : loc.includes("message")
          ? "Сообщение"
          : loc.includes("captchaToken") || loc.includes("captchaAnswer")
            ? "Капча"
            : null;
      if (err.type === "string_too_short" && err.ctx?.min_length) {
        parts.push(`${field || "Поле"}: минимум ${err.ctx.min_length} символа`);
        return;
      }
      if (err.type === "missing") {
        parts.push(field ? `${field}: обязательное поле` : "Обязательное поле");
        return;
      }
      if (typeof err.msg === "string") {
        parts.push(field ? `${field}: ${err.msg}` : err.msg);
      }
    });
    return parts.filter(Boolean).join(". ");
  }

  function setNavUnreadBadge(count) {
    const link = $("nav-contact");
    if (!link) return;
    const existing = link.querySelector(".nav-link-badge");
    if (!count || count <= 0) {
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
    link.appendChild(span);
  }

  function formatDate(raw) {
    if (!raw) return "—";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return raw;
    return d.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
  }

  async function loadMe() {
    try {
      const r = await api("/api/auth/me");
      if (!r.ok) return { guest: true };
      const j = await r.json().catch(() => null);
      return j && typeof j === "object" ? j : { guest: true };
    } catch {
      return { guest: true };
    }
  }

  async function refreshCaptcha() {
    const q = $("support-captcha-question");
    if (!q) return;
    q.textContent = "Загрузка…";
    try {
      const r = await api("/api/support/captcha");
      if (!r.ok) throw new Error("captcha");
      const j = await r.json();
      state.captchaToken = j.token;
      q.textContent = j.question;
    } catch {
      q.textContent = "Ошибка";
    }
  }

  function renderTickets(list) {
    const wrap = $("support-list");
    const empty = $("support-list-empty");
    if (!wrap || !empty) return;
    wrap.innerHTML = "";
    if (!Array.isArray(list) || list.length === 0) {
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";
    list.forEach((t) => {
      const card = document.createElement("div");
      card.className = "support-item";
      const header = document.createElement("div");
      header.className = "support-item-header";
      const title = document.createElement("div");
      title.className = "support-item-title";
      title.textContent = t.subject || "Без темы";
      header.appendChild(title);
      if (t.userUnreadCount > 0) {
        const badge = document.createElement("span");
        badge.className = "support-unread-badge";
        badge.textContent = String(t.userUnreadCount);
        header.appendChild(badge);
      }
      const meta = document.createElement("div");
      meta.className = "support-item-meta";
      meta.textContent = `Обновлено: ${formatDate(t.updatedAt)}`;
      const preview = document.createElement("div");
      preview.className = "support-item-preview";
      preview.textContent = t.lastMessagePreview || "Сообщений пока нет.";
      const actions = document.createElement("div");
      actions.className = "admin-actions";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn";
      btn.textContent = "Открыть";
      btn.addEventListener("click", () => openTicket(t.id));
      actions.appendChild(btn);

      card.appendChild(header);
      card.appendChild(meta);
      card.appendChild(preview);
      card.appendChild(actions);
      wrap.appendChild(card);
    });
  }

  function renderMessages(messages) {
    const wrap = $("support-thread-messages");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!Array.isArray(messages) || messages.length === 0) {
      wrap.textContent = "Сообщений пока нет.";
      return;
    }
    messages.forEach((m) => {
      const item = document.createElement("div");
      item.className = "support-message";
      if (m.senderRole === "admin") {
        item.classList.add("support-message--admin");
      }
      const meta = document.createElement("div");
      meta.className = "support-message-meta";
      meta.textContent = `${m.senderRole === "admin" ? "Администратор" : "Вы"} · ${formatDate(m.createdAt)}`;
      const body = document.createElement("div");
      body.textContent = m.body || "";
      item.appendChild(meta);
      item.appendChild(body);
      wrap.appendChild(item);
    });
  }

  async function loadTickets() {
    const empty = $("support-list-empty");
    if (state.isGuest) {
      if (empty) {
        empty.style.display = "block";
        empty.textContent = "Войдите в аккаунт, чтобы видеть историю обращений.";
      }
      return;
    }
    try {
      const r = await api("/api/support/tickets");
      if (!r.ok) throw new Error("tickets");
      const list = await r.json();
      renderTickets(list);
    } catch {
      if (empty) {
        empty.style.display = "block";
        empty.textContent = "Не удалось загрузить обращения.";
      }
    }
  }

  async function refreshUnreadBadge() {
    if (state.isGuest) return;
    try {
      const r = await api("/api/support/unread");
      if (!r.ok) return;
      const j = await r.json().catch(() => null);
      const n = Number(j && j.unreadCount);
      setNavUnreadBadge(Number.isNaN(n) ? 0 : n);
    } catch {
      /* ignore */
    }
  }

  async function openTicket(id) {
    const panel = $("support-thread");
    if (!panel) return;
    panel.hidden = false;
    $("support-thread-subject").textContent = "Загрузка…";
    $("support-thread-meta").textContent = "";
    try {
      const r = await api(`/api/support/tickets/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error("ticket");
      const t = await r.json();
      state.activeTicketId = t.id;
      $("support-thread-subject").textContent = t.subject || "Обращение";
      $("support-thread-meta").textContent = `ID: ${t.id} · Обновлено: ${formatDate(t.updatedAt)}`;
      renderMessages(t.messages || []);
      await loadTickets();
      await refreshUnreadBadge();
    } catch {
      showMsg("Не удалось открыть обращение.", true);
    }
  }

  function wireActions() {
    const refreshBtn = $("support-captcha-refresh");
    refreshBtn?.addEventListener("click", () => refreshCaptcha());

    $("support-thread-close")?.addEventListener("click", () => {
      const panel = $("support-thread");
      if (panel) panel.hidden = true;
      state.activeTicketId = null;
    });

    $("support-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearMsg();
      if (state.isGuest) {
        showMsg("Войдите в аккаунт, чтобы отправлять обращения.", true);
        return;
      }
      const subject = $("support-subject").value.trim();
      const message = $("support-message").value.trim();
      const captchaAnswer = $("support-captcha-answer").value.trim();
      if (!subject || !message || !captchaAnswer || !state.captchaToken) {
        showMsg("Заполните тему, сообщение и капчу.", true);
        return;
      }
      const payload = {
        subject,
        message,
        captchaToken: state.captchaToken,
        captchaAnswer,
      };
      try {
        const r = await api("/api/support/tickets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          const msg = formatApiError(j.detail) || "Не удалось отправить обращение.";
          showMsg(msg, true);
          await refreshCaptcha();
          return;
        }
        const ticket = await r.json();
        $("support-subject").value = "";
        $("support-message").value = "";
        $("support-captcha-answer").value = "";
        showMsg("Обращение отправлено. Мы ответим в ближайшее время.", false);
        await refreshCaptcha();
        await loadTickets();
      } catch {
        showMsg("Ошибка отправки. Попробуйте ещё раз.", true);
        await refreshCaptcha();
      }
    });

    $("support-reply-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearMsg();
      if (!state.activeTicketId) {
        showMsg("Выберите обращение для ответа.", true);
        return;
      }
      const text = $("support-reply-message").value.trim();
      if (!text) {
        showMsg("Введите ответ.", true);
        return;
      }
      try {
        const r = await api(`/api/support/tickets/${encodeURIComponent(state.activeTicketId)}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          const msg = formatApiError(j.detail) || "Не удалось отправить ответ.";
          showMsg(msg, true);
          return;
        }
        $("support-reply-message").value = "";
        await openTicket(state.activeTicketId);
      } catch {
        showMsg("Не удалось отправить ответ.", true);
      }
    });
  }

  async function boot() {
    const me = await loadMe();
    state.isGuest = me.guest === true;
    if (state.isGuest) {
      showAuthHint("Войдите в аккаунт, чтобы отправлять обращения и видеть ответы.");
      const form = $("support-form");
      form?.querySelectorAll("input, textarea, button").forEach((el) => {
        el.disabled = true;
      });
      const replyForm = $("support-reply-form");
      replyForm?.querySelectorAll("textarea, button").forEach((el) => {
        el.disabled = true;
      });
    } else {
      showAuthHint("");
    }
    await refreshCaptcha();
    await loadTickets();
    await refreshUnreadBadge();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      wireActions();
      boot();
    });
  } else {
    wireActions();
    boot();
  }
})();
