import { consumeStream } from "./sse.js?v=19";

const MODEL_STORAGE_KEY = "ai_proxy_model_slug";
/** Выставляется на /tariffs при выборе модели — чат не подменяет её моделью из старого диалога. */
const MODEL_EXPLICIT_CHOICE_KEY = "ai_proxy_explicit_model";

const YOOKASSA_CONSTRUCT_JS =
  "https://yookassa.ru/integration/simplepay/js/yookassa_construct_form.js?v=1.34.0";

function loadYooKassaConstructOnce() {
  if (window.__aiProxyYookassaConstructLoaded) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = YOOKASSA_CONSTRUCT_JS;
    s.async = true;
    s.onload = () => {
      window.__aiProxyYookassaConstructLoaded = true;
      resolve();
    };
    s.onerror = () => reject(new Error("yookassa_construct_form.js"));
    document.body.appendChild(s);
  });
}

function closePayModal() {
  const modal = document.getElementById("pay-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
}

function openPayModal() {
  const modal = document.getElementById("pay-modal");
  const errElPay = document.getElementById("pay-modal-err");
  const form = document.getElementById("yookassa-simple-form");
  if (!modal || !errElPay || !form) return;
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  errElPay.style.display = "none";
  errElPay.textContent = "";
  const sumInput = form.querySelector('[name="sum"]');
  if (sumInput) sumInput.focus();
}

const api = (path, opts = {}) =>
  fetch(path, { credentials: "include", ...opts });

/**
 * Логи видео: Error в JSON даёт "{}" — разворачиваем; дублируем в error + info;
 * последняя запись — в window.__AI_PROXY_VIDEO_DEBUG (удобно смотреть в консоли: copy(window.__AI_PROXY_VIDEO_DEBUG)).
 */
function logVideo(label, data) {
  let payload = data;
  if (data instanceof Error) {
    payload = { name: data.name, message: data.message, stack: data.stack };
  }
  const row = { t: new Date().toISOString(), label, payload };
  try {
    window.__AI_PROXY_VIDEO_DEBUG = row;
  } catch {
    /* ignore */
  }
  try {
    console.error("[video]", label, payload);
  } catch {
    /* консоль может быть недоступна (встроенный браузер и т.п.) */
  }
  try {
    console.info("[video]", label, payload);
  } catch {
    /* ignore */
  }
}

async function loadMe() {
  const r = await api("/api/auth/me");
  if (!r.ok) {
    return { guest: true, balance: null, yookassa_enabled: false, isAdmin: false };
  }
  return r.json();
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function renderMd(html) {
  if (typeof marked !== "undefined" && marked.parse) {
    return marked.parse(html, { breaks: true });
  }
  return escapeHtml(html).replace(/\n/g, "<br>");
}

/** Группа модели для сетки: бесплатные > видео > изображения > музыка > текст */
function modelGroupId(m) {
  if (m.isFree === true) return "free";
  if (m.supportsVideoGeneration) return "video";
  if (m.supportsImageGeneration) return "image";
  if (m.supportsMusicGeneration) return "music";
  return "text";
}

const MODEL_GROUPS = [
  { id: "free", emoji: "🎁", title: "Бесплатные" },
  { id: "text", emoji: "💬", title: "Текст и чат" },
  { id: "image", emoji: "🖼", title: "Изображения" },
  { id: "video", emoji: "🎬", title: "Видео" },
  { id: "music", emoji: "🎵", title: "Музыка" },
];

function hueFromSlug(slug) {
  let h = 0;
  for (let i = 0; i < slug.length; i++) h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  return h % 360;
}

/**
 * Иконки брендов через публичный favicon (стабильнее, чем прямой путь к simple-icons — без 404 из‑за версии/имени).
 */
function brandFaviconDomain(slug) {
  if (!slug) return null;
  if (slug.startsWith("openrouter/")) return "openrouter.ai";
  if (slug.startsWith("openai/")) return "openai.com";
  if (slug.startsWith("anthropic/")) return "anthropic.com";
  if (slug.startsWith("google/")) return "google.com";
  if (slug.startsWith("meta-llama/")) return "meta.com";
  if (slug.startsWith("mistralai/")) return "mistral.ai";
  if (slug.startsWith("deepseek/")) return "deepseek.com";
  if (slug.startsWith("qwen/")) return "alibaba.com";
  if (slug.startsWith("bytedance/")) return "bytedance.com";
  return null;
}

function modelBrandIconUrl(slug) {
  const host = brandFaviconDomain(slug);
  return host
    ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`
    : null;
}

/**
 * Понятные подсказки к ответам OpenRouter в потоке.
 * Для :free чаще бывает «Provider returned error» — общая очередь и лимиты у провайдера.
 */
function humanizeOpenRouterStreamError(detail, opts = {}) {
  const isFree = opts.isFree === true;
  const m = String(detail || "").trim();
  if (!m) {
    return isFree
      ? "Не удалось получить ответ по бесплатному каналу. Повторите позже или выберите платную модель."
      : "Ошибка при ответе модели.";
  }
  const low = m.toLowerCase();
  if (low.includes("provider returned error")) {
    if (isFree) {
      return "На бесплатных моделях OpenRouter ответ идёт через общую очередь: провайдер временно недоступен или перегружен. Подождите минуту, повторите запрос или переключитесь на платную модель.";
    }
    return "Провайдер модели вернул ошибку (лимит, перегрузка или сбой канала). Попробуйте другую модель или повторите запрос позже.";
  }
  if (low.includes("no endpoints found")) {
    if (isFree) {
      return "Для этой бесплатной модели сейчас нет свободных серверов. Выберите другую :free модель или платную.";
    }
    return "Для выбранной модели сейчас нет доступных серверов. Выберите другую модель в списке.";
  }
  if (low.includes("rate limit") || low.includes("too many requests")) {
    if (isFree) {
      return "Лимит запросов на бесплатном канале. Подождите или перейдите на платную модель.";
    }
    return "Слишком много запросов. Подождите немного или смените модель.";
  }
  if (low.includes("context length") || low.includes("maximum context")) {
    return "Превышен допустимый размер контекста. Начните новый чат или сократите историю.";
  }
  return m.length > 280 ? `${m.slice(0, 277)}…` : m;
}

async function main() {
  const me = await loadMe();
  const isGuest = me.guest === true;

  const cfg = await api("/api/config").then((r) => r.json());
  const models = await api("/api/models").then((r) => r.json());

  const balanceEl = document.getElementById("balance");
  const modelPickerEl = document.getElementById("model-picker");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("msg-input");
  const listEl = document.getElementById("messages");
  const errEl = document.getElementById("err");
  const fileEl = document.getElementById("file-input");
  const btnPay = document.getElementById("btn-pay");
  const videoPanelEl = document.getElementById("video-panel");
  const videoPromptEl = document.getElementById("video-prompt");
  const videoStatusEl = document.getElementById("video-status");
  const videoPlayerEl = document.getElementById("video-player");
  const btnVideoEl = document.getElementById("btn-video");
  const threadListEl = document.getElementById("thread-list");
  const btnNewChat = document.getElementById("btn-new-chat");
  const btnLogout = document.getElementById("btn-logout");
  const navLogin = document.getElementById("nav-login");
  const balanceWrap = document.getElementById("balance-wrap");

  if (isGuest) {
    if (navLogin) navLogin.style.removeProperty("display");
    if (btnLogout) btnLogout.style.display = "none";
    if (balanceWrap) balanceWrap.style.display = "none";
    if (btnPay) btnPay.style.display = "none";
    const sb = document.getElementById("chat-sidebar");
    if (sb) sb.style.display = "none";
  } else {
    if (navLogin) navLogin.style.display = "none";
    if (btnLogout) btnLogout.style.removeProperty("display");
    if (balanceWrap) balanceWrap.style.removeProperty("display");
  }
  if (me.isAdmin) {
    const navAdmin = document.getElementById("nav-admin");
    if (navAdmin) navAdmin.style.removeProperty("display");
  }

  if (navLogin) {
    try {
      navLogin.href =
        "/login?next=" +
        encodeURIComponent(location.pathname + location.search);
    } catch {
      navLogin.href = "/login";
    }
  }

  if (btnLogout) {
    btnLogout.onclick = async () => {
      await api("/api/auth/logout", { method: "POST" });
      location.href = "/login";
    };
  }

  if (!isGuest && cfg.yookassaEnabled) {
    const shopId = cfg.yookassaShopId ? String(cfg.yookassaShopId).trim() : "";
    const baseRaw = cfg.publicAppUrl
      ? String(cfg.publicAppUrl).trim()
      : window.location.origin;
    const base = baseRaw.replace(/\/$/, "");

    if (shopId) {
      btnPay.style.display = "";
      btnPay.onclick = async () => {
        openPayModal();
        try {
          await loadYooKassaConstructOnce();
        } catch (e) {
          console.error("[yookassa/script]", e);
        }
      };

      const payModal = document.getElementById("pay-modal");
      const form = document.getElementById("yookassa-simple-form");
      const errElPay = document.getElementById("pay-modal-err");
      let payPrepareBusy = false;

      form?.addEventListener(
        "submit",
        async (e) => {
          if (!form || !errElPay) return;
          if (form.dataset.yookassaOrderReady === "1") {
            delete form.dataset.yookassaOrderReady;
            return;
          }
          e.preventDefault();
          e.stopImmediatePropagation();
          if (payPrepareBusy) return;

          const sumInput = form.querySelector('[name="sum"]');
          const raw = String(sumInput?.value ?? "")
            .trim()
            .replace(",", ".");
          if (!raw || !/^\d+(\.\d{1,2})?$/.test(raw)) {
            errElPay.textContent =
              "Введите сумму (например 100 или 99.50).";
            errElPay.style.display = "block";
            return;
          }

          errElPay.style.display = "none";
          payPrepareBusy = true;
          const payBtn = form.querySelector(".ym-btn-pay");
          if (payBtn) payBtn.disabled = true;
          try {
            const r = await api("/api/payments/simplepay-order", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ amountRub: raw }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) {
              errElPay.textContent =
                typeof d.detail === "string"
                  ? d.detail
                  : "Не удалось создать заказ.";
              errElPay.style.display = "block";
              return;
            }

            form.querySelector('[name="shopSuccessURL"]').value =
              `${base}/?payment=return&result=success&orderId=${encodeURIComponent(d.orderId || "")}`;
            form.querySelector('[name="shopFailURL"]').value =
              `${base}/?payment=return&result=fail&orderId=${encodeURIComponent(d.orderId || "")}`;
            form.querySelector('[name="shopId"]').value = d.shopId || shopId;
            form.querySelector('[name="customerNumber"]').value = d.orderId || "";
            if (sumInput) sumInput.value = raw;
            const priceH = form.querySelector('.ym-product input[name="price"]');
            if (priceH) priceH.value = raw;
            const priceSpan = form.querySelector(".ym-product-price");
            if (priceSpan) priceSpan.setAttribute("data-price", raw);

            form.dataset.yookassaOrderReady = "1";
            try {
              if (d.orderId) {
                sessionStorage.setItem("ai_proxy_last_pay_order", String(d.orderId));
              }
            } catch {
              /* ignore */
            }
            form.requestSubmit();
          } catch (err) {
            console.error("[yookassa/simplepay]", err);
            errElPay.textContent = "Ошибка сети. Повторите попытку.";
            errElPay.style.display = "block";
          } finally {
            payPrepareBusy = false;
            if (payBtn) payBtn.disabled = false;
          }
        },
        true,
      );

      form.querySelector('[name="shopId"]').value = shopId;

      payModal?.querySelectorAll("[data-pay-modal-close]").forEach((el) => {
        el.addEventListener("click", () => closePayModal());
      });
      payModal?.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closePayModal();
      });
    } else {
      btnPay.style.display = "";
      btnPay.onclick = () => {
        alert("Укажите YOOKASSA_SHOP_ID в .env и перезапустите сервер.");
      };
    }
  } else {
    btnPay.style.display = "none";
  }

  function refreshBalance(b) {
    if (isGuest || balanceEl == null) return;
    balanceEl.textContent = `${Number(b).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    })} ₽`;
  }
  if (!isGuest && me.balance != null) refreshBalance(me.balance);

  try {
    const qp = new URLSearchParams(window.location.search);
    if (qp.get("payment") === "return") {
      let oid = qp.get("orderId");
      if (!oid) {
        try {
          oid = sessionStorage.getItem("ai_proxy_last_pay_order") || "";
        } catch {
          oid = "";
        }
      }
      const treatAsSuccess = qp.get("result") !== "fail";
      const explicitFail = qp.get("result") === "fail";

      {
        const u = new URL(window.location.href);
        u.searchParams.delete("payment");
        u.searchParams.delete("result");
        u.searchParams.delete("orderId");
        const qs = u.searchParams.toString();
        history.replaceState(
          {},
          "",
          u.pathname + (qs ? `?${qs}` : "") + u.hash,
        );
      }

      if (treatAsSuccess && oid && cfg.yookassaEnabled && !isGuest) {
        let lastSd = {};
        for (let i = 0; i < 5; i++) {
          const sr = await api("/api/payments/sync-simplepay", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ orderId: oid }),
          });
          const sd = await sr.json().catch(() => ({}));
          lastSd = sd;
          if (sd.credited === true || sd.alreadyDone === true) break;
          await new Promise((r) => setTimeout(r, 2000));
        }
        if (
          lastSd.matchedPayment === true &&
          lastSd.credited !== true &&
          lastSd.alreadyDone !== true
        ) {
          errEl.textContent =
            "Платёж в ЮKassa найден, но баланс не изменён (несовпадение суммы с заказом или ошибка). Если деньги списались — сохраните чек и обратитесь в поддержку.";
          errEl.style.display = "block";
        }
      }
      try {
        sessionStorage.removeItem("ai_proxy_last_pay_order");
      } catch {
        /* ignore */
      }
      const meRet = await api("/api/auth/me").then((r) => r.json());
      if (!meRet.guest && meRet.balance != null) refreshBalance(meRet.balance);
      if (explicitFail) {
        errEl.textContent =
          "Оплата не завершена. Если деньги списались, подождите минуту и обновите страницу.";
        errEl.style.display = "block";
      }
    }
  } catch {
    /* ignore */
  }

  let selectedSlug = models[0]?.slug ?? "";
  /** true: не открывать последний чат при старте — уважать ?model= или выбор с тарифов. */
  let honorExplicitModelChoice = false;
  /* Сначала ?model= (переход с /tariffs), иначе последний выбор из сессии. */
  try {
    const qp = new URLSearchParams(window.location.search);
    const qModel = qp.get("model");
    if (qModel && models.some((x) => x.slug === qModel)) {
      selectedSlug = qModel;
      sessionStorage.setItem(MODEL_STORAGE_KEY, qModel);
      honorExplicitModelChoice = true;
      const u = new URL(window.location.href);
      u.searchParams.delete("model");
      const qs = u.searchParams.toString();
      history.replaceState({}, "", u.pathname + (qs ? `?${qs}` : "") + u.hash);
    } else {
      const savedSlug = sessionStorage.getItem(MODEL_STORAGE_KEY);
      if (savedSlug && models.some((x) => x.slug === savedSlug)) {
        selectedSlug = savedSlug;
      }
      if (sessionStorage.getItem(MODEL_EXPLICIT_CHOICE_KEY) === "1") {
        honorExplicitModelChoice = true;
        sessionStorage.removeItem(MODEL_EXPLICIT_CHOICE_KEY);
      }
    }
  } catch {
    /* ignore */
  }

  /** Переключение вкладки категории (после построения селектора). */
  let activateModelTab = () => {};

  function selectModelVisual(slug) {
    if (!modelPickerEl) return;
    modelPickerEl.querySelectorAll(".model-card").forEach((btn) => {
      const on = btn.dataset.slug === slug;
      btn.classList.toggle("model-card--selected", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
    const sel = models.find((x) => x.slug === slug);
    if (sel) activateModelTab(modelGroupId(sel));
    const cards = modelPickerEl.querySelectorAll(".model-card");
    let picked = null;
    for (const c of cards) {
      if (c.dataset.slug === slug) {
        picked = c;
        break;
      }
    }
    if (picked) {
      requestAnimationFrame(() => {
        try {
          picked.scrollIntoView({
            behavior: "smooth",
            inline: "center",
            block: "nearest",
          });
        } catch {
          picked.scrollIntoView();
        }
      });
    }
  }

  function setSelectedSlug(slug) {
    if (!models.some((x) => x.slug === slug)) return;
    selectedSlug = slug;
    sessionStorage.setItem(MODEL_STORAGE_KEY, slug);
    selectModelVisual(slug);
    updateHints();
  }

  if (modelPickerEl) {
    const buckets = { free: [], text: [], image: [], video: [], music: [] };
    for (const m of models) {
      buckets[modelGroupId(m)].push(m);
    }
    for (const k of Object.keys(buckets)) {
      buckets[k].sort((a, b) =>
        String(a.displayName).localeCompare(String(b.displayName), "ru"),
      );
    }

    const groupsWithModels = MODEL_GROUPS.filter((g) => buckets[g.id].length > 0);
    if (!groupsWithModels.length) {
      modelPickerEl.innerHTML =
        '<p class="model-picker-empty">Нет доступных моделей.</p>';
    } else {
      const tabBar = document.createElement("div");
      tabBar.className = "model-tabs";
      tabBar.setAttribute("role", "tablist");
      tabBar.setAttribute("aria-label", "Категории моделей");

      const panelWrap = document.createElement("div");
      panelWrap.className = "model-tab-panels";

      const initialGid = (() => {
        const sm = models.find((x) => x.slug === selectedSlug);
        if (sm) return modelGroupId(sm);
        return groupsWithModels[0].id;
      })();

      function buildModelButton(m) {
        const letter = (m.displayName || "?").trim().charAt(0).toUpperCase();
        const hue = hueFromSlug(m.slug);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "model-card";
        btn.dataset.slug = m.slug;
        btn.setAttribute("role", "radio");
        btn.setAttribute("aria-checked", "false");
        const desc =
          typeof m.descriptionRu === "string" && m.descriptionRu.trim()
            ? m.descriptionRu.trim()
            : `${m.provider}. Модель через OpenRouter.`;
        btn.setAttribute(
          "title",
          `${m.displayName} — ${m.provider} (${m.slug})`,
        );

        const iconWrap = document.createElement("span");
        iconWrap.className = "model-card-icon";
        const iconUrl = modelBrandIconUrl(m.slug);
        if (iconUrl) {
          const img = document.createElement("img");
          img.src = iconUrl;
          img.alt = "";
          img.width = 32;
          img.height = 32;
          img.loading = "lazy";
          img.decoding = "async";
          img.className = "model-card-icon-img";
          img.addEventListener("error", () => {
            img.remove();
            const fallback = document.createElement("span");
            fallback.className = "model-card-avatar model-card-avatar--sm";
            fallback.style.setProperty("--hue", String(hue));
            fallback.textContent = letter;
            iconWrap.appendChild(fallback);
          });
          iconWrap.appendChild(img);
        } else {
          const av = document.createElement("span");
          av.className = "model-card-avatar model-card-avatar--sm";
          av.style.setProperty("--hue", String(hue));
          av.textContent = letter;
          iconWrap.appendChild(av);
        }

        const body = document.createElement("span");
        body.className = "model-card-body";

        const nm = document.createElement("span");
        nm.className = "model-card-name";
        nm.textContent = m.displayName;

        const dc = document.createElement("span");
        dc.className = "model-card-desc";
        dc.textContent = desc;

        const meta = document.createElement("span");
        meta.className = "model-card-meta";
        meta.textContent = m.provider;
        if (m.isFree === true) {
          const fb = document.createElement("span");
          fb.className = "model-card-free-badge";
          fb.textContent = "Free";
          meta.appendChild(document.createTextNode(" · "));
          meta.appendChild(fb);
        }

        body.appendChild(nm);
        body.appendChild(dc);
        body.appendChild(meta);
        btn.appendChild(iconWrap);
        btn.appendChild(body);
        return btn;
      }

      groupsWithModels.forEach((g, i) => {
        const list = buckets[g.id];
        const tabId = `model-tab-${g.id}`;
        const panelId = `model-panel-${g.id}`;
        const isInitial = g.id === initialGid;

        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = "model-tab" + (isInitial ? " model-tab--active" : "");
        tab.id = tabId;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", isInitial ? "true" : "false");
        tab.setAttribute("aria-controls", panelId);
        tab.setAttribute("tabindex", isInitial ? "0" : "-1");
        tab.dataset.group = g.id;
        tab.innerHTML = `<span class="model-tab-emoji" aria-hidden="true">${g.emoji}</span><span class="model-tab-label">${escapeHtml(g.title)}</span>`;

        const panel = document.createElement("div");
        panel.className = "model-tab-panel";
        panel.id = panelId;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tabId);
        panel.hidden = !isInitial;
        panel.dataset.group = g.id;

        const row = document.createElement("div");
        row.className = "model-scroll-row";
        row.setAttribute("role", "group");
        row.setAttribute("aria-label", g.title);

        for (const m of list) {
          row.appendChild(buildModelButton(m));
        }
        panel.appendChild(row);
        tabBar.appendChild(tab);
        panelWrap.appendChild(panel);
      });

      modelPickerEl.innerHTML = "";
      modelPickerEl.appendChild(tabBar);
      modelPickerEl.appendChild(panelWrap);

      activateModelTab = (gid) => {
        tabBar.querySelectorAll(".model-tab").forEach((t) => {
          const on = t.dataset.group === gid;
          t.classList.toggle("model-tab--active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.setAttribute("tabindex", on ? "0" : "-1");
        });
        panelWrap.querySelectorAll(".model-tab-panel").forEach((p) => {
          p.hidden = p.dataset.group !== gid;
        });
      };

      tabBar.addEventListener("click", (e) => {
        const t = e.target.closest(".model-tab");
        if (!t || !tabBar.contains(t)) return;
        const gid = t.dataset.group;
        if (gid) activateModelTab(gid);
      });

      tabBar.addEventListener("keydown", (e) => {
        const tabs = [...tabBar.querySelectorAll(".model-tab")];
        const ix = tabs.indexOf(document.activeElement);
        if (ix < 0) return;
        let next = ix;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          next = (ix + 1) % tabs.length;
          e.preventDefault();
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          next = (ix - 1 + tabs.length) % tabs.length;
          e.preventDefault();
        } else {
          return;
        }
        tabs[next].focus();
        const gid = tabs[next].dataset.group;
        if (gid) activateModelTab(gid);
      });

      modelPickerEl.addEventListener("click", (e) => {
        const btn = e.target.closest(".model-card");
        if (!btn || !modelPickerEl.contains(btn)) return;
        const slug = btn.dataset.slug;
        if (slug) void selectModelPick(slug);
      });
    }
  }

  const imgGenHintEl = document.getElementById("img-gen-hint");
  function updateHints() {
    const m = selectedModel();
    if (!m) return;
    if (imgGenHintEl) {
      imgGenHintEl.style.display =
        m.supportsImageGeneration === true && m.supportsVideoGeneration !== true
          ? "block"
          : "none";
    }
    if (videoPanelEl && formEl) {
      const vid = m.supportsVideoGeneration === true;
      videoPanelEl.style.display = vid ? "block" : "none";
      formEl.style.display = vid ? "none" : "";
      if (vid) {
        formEl.setAttribute("inert", "");
        const ae = document.activeElement;
        if (ae && formEl.contains(ae)) ae.blur();
      } else {
        formEl.removeAttribute("inert");
      }
    }
  }
  const history = [];

  function selectedModel() {
    const found = models.find((x) => x.slug === selectedSlug);
    if (found) return found;
    if (selectedSlug) {
      console.warn(
        "[model] выбранный slug не найден в списке, сброс на первую модель:",
        selectedSlug,
      );
    }
    return models[0] ?? null;
  }

  updateHints();
  selectModelVisual(selectedSlug);
  sessionStorage.setItem(MODEL_STORAGE_KEY, selectedSlug);

  function appendUserBubble(text) {
    const wrap = document.createElement("div");
    wrap.className = "msg user";
    wrap.innerHTML = `<div class="bubble"><p style="margin:0;white-space:pre-wrap">${escapeHtml(
      text,
    )}</p></div>`;
    listEl.appendChild(wrap);
    listEl.scrollTop = listEl.scrollHeight;
  }

  function appendAssistantShell() {
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const typing = document.createElement("span");
    typing.className = "typing";
    typing.textContent = "Печатает…";
    const md = document.createElement("div");
    md.className = "md";
    const cost = document.createElement("div");
    cost.className = "cost";
    bubble.appendChild(typing);
    bubble.appendChild(md);
    bubble.appendChild(cost);
    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
    listEl.scrollTop = listEl.scrollHeight;
    return { md, cost, typing };
  }

  let currentConversationId = null;

  function modelDisplayName(slug) {
    const m = models.find((x) => x.slug === slug);
    return m ? m.displayName : slug || "—";
  }

  function messagesForRequest() {
    return history.map((m) => ({ role: m.role, content: m.content }));
  }

  function deriveTitleFromHistory() {
    for (const m of history) {
      if (m.role !== "user") continue;
      const c = m.content;
      let line = "";
      if (typeof c === "string") line = c.trim();
      else if (Array.isArray(c)) {
        const t = c.find((x) => x?.type === "text");
        line = String(t?.text ?? "").trim();
      }
      if (!line) continue;
      return line.length > 80 ? `${line.slice(0, 77)}…` : line;
    }
    return "Новый чат";
  }

  function userContentPreview(content) {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      const t = content.find((x) => x?.type === "text");
      return String(t?.text ?? "").trim() || "[изображение]";
    }
    return "[сообщение]";
  }

  function renderAssistantFromSaved(msg) {
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const md = document.createElement("div");
    md.className = "md";
    let html = renderMd(msg.content || "");
    const urls = Array.isArray(msg.imageUrls) ? msg.imageUrls : [];
    for (const u of urls) {
      if (typeof u !== "string" || !u) continue;
      html += `<p class="gen-img-wrap"><img src="${escapeHtml(u)}" alt="" class="gen-image" loading="lazy" decoding="async" /></p>`;
    }
    md.innerHTML = html;
    bubble.appendChild(md);
    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
  }

  function renderChatFromHistory() {
    listEl.innerHTML = "";
    for (const msg of history) {
      if (msg.role === "user") {
        appendUserBubble(userContentPreview(msg.content));
      } else if (msg.role === "assistant") {
        renderAssistantFromSaved(msg);
      }
    }
    listEl.scrollTop = listEl.scrollHeight;
  }

  function setSidebarBusy(busy) {
    if (btnNewChat) btnNewChat.disabled = busy;
    if (threadListEl) {
      threadListEl.querySelectorAll(".sidebar-thread-item").forEach((el) => {
        el.disabled = busy;
      });
    }
  }

  async function refreshSidebarList() {
    if (!threadListEl) return;
    if (isGuest) {
      threadListEl.innerHTML = "";
      return;
    }
    const r = await api("/api/v1/conversations");
    if (!r.ok) return;
    const rows = await r.json();
    threadListEl.innerHTML = "";
    for (const row of rows) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "sidebar-thread-item";
      btn.dataset.threadId = row.id;
      const title = document.createElement("span");
      title.className = "sidebar-thread-title";
      title.textContent = row.title || "Новый чат";
      const meta = document.createElement("span");
      meta.className = "sidebar-thread-model";
      meta.textContent = modelDisplayName(row.modelSlug);
      btn.appendChild(title);
      btn.appendChild(meta);
      if (row.id === currentConversationId) {
        btn.classList.add("sidebar-thread-item--active");
      }
      li.appendChild(btn);
      threadListEl.appendChild(li);
    }
  }

  async function persistThread() {
    if (isGuest) return;
    if (!currentConversationId) return;
    if (history.length === 0) return;
    try {
      const messages = JSON.parse(JSON.stringify(history));
      const title = deriveTitleFromHistory();
      await api(
        `/api/v1/conversations/${encodeURIComponent(currentConversationId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            modelSlug: selectedSlug,
            title,
            messages,
          }),
        },
      );
      await refreshSidebarList();
    } catch (e) {
      console.warn("[conversations] persist failed", e);
    }
  }

  async function abandonOrPersistCurrent() {
    if (isGuest) {
      currentConversationId = null;
      return;
    }
    if (!currentConversationId) return;
    if (history.length === 0) {
      await api(`/api/v1/conversations/${encodeURIComponent(currentConversationId)}`, {
        method: "DELETE",
      });
    } else {
      await persistThread();
    }
    currentConversationId = null;
  }

  async function openThread(id) {
    if (isGuest) return;
    if (streaming || !id) return;
    if (currentConversationId && currentConversationId !== id) {
      await abandonOrPersistCurrent();
    }
    const r = await api(`/api/v1/conversations/${encodeURIComponent(id)}`);
    if (!r.ok) return;
    const t = await r.json();
    currentConversationId = t.id;
    history.length = 0;
    for (const m of t.messages || []) {
      history.push(m);
    }
    if (t.modelSlug && models.some((x) => x.slug === t.modelSlug)) {
      setSelectedSlug(t.modelSlug);
    }
    renderChatFromHistory();
    updateHints();
    await refreshSidebarList();
  }

  async function newChat() {
    if (streaming) return;
    await abandonOrPersistCurrent();
    currentConversationId = null;
    history.length = 0;
    listEl.innerHTML = "";
    errEl.style.display = "none";
    errEl.textContent = "";
    await refreshSidebarList();
  }

  async function ensureConversation() {
    if (isGuest) return;
    if (currentConversationId) return;
    const slug = selectedModel()?.slug ?? selectedSlug;
    if (!slug) return;
    const r = await api("/api/v1/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelSlug: slug }),
    });
    if (!r.ok) return;
    const created = await r.json();
    currentConversationId = created.id;
    await refreshSidebarList();
  }

  async function initConversations() {
    if (isGuest) {
      currentConversationId = null;
      history.length = 0;
      listEl.innerHTML = "";
      errEl.style.display = "none";
      errEl.textContent = "";
      await refreshSidebarList();
      return;
    }
    const r = await api("/api/v1/conversations");
    if (!r.ok) return;
    const list = await r.json();
    if (honorExplicitModelChoice) {
      currentConversationId = null;
      history.length = 0;
      listEl.innerHTML = "";
      errEl.style.display = "none";
      errEl.textContent = "";
      await refreshSidebarList();
      return;
    }
    if (!Array.isArray(list) || list.length === 0) {
      currentConversationId = null;
      await refreshSidebarList();
      return;
    }
    await openThread(list[0].id);
  }

  async function selectModelPick(slug) {
    if (!models.some((x) => x.slug === slug)) return;
    if (slug === selectedSlug) return;
    await abandonOrPersistCurrent();
    selectedSlug = slug;
    sessionStorage.setItem(MODEL_STORAGE_KEY, slug);
    currentConversationId = null;
    history.length = 0;
    listEl.innerHTML = "";
    errEl.style.display = "none";
    errEl.textContent = "";
    selectModelVisual(slug);
    updateHints();
    await refreshSidebarList();
  }

  let streaming = false;

  async function runChat() {
    const m0 = selectedModel();
    if (!m0 || m0.supportsVideoGeneration === true) {
      console.warn("[chat] runChat отменён: для видеомодели чат отключён");
      return;
    }
    try {
      errEl.textContent = "";
      errEl.style.display = "none";
      streaming = true;
      setSidebarBusy(true);
      const { md, cost, typing } = appendAssistantShell();
      let acc = "";
      const imageUrls = [];
      function renderAssistantHtml() {
        let html = renderMd(acc);
        for (const u of imageUrls) {
          html += `<p class="gen-img-wrap"><img src="${u}" alt="Сгенерированное изображение" class="gen-image" loading="lazy" decoding="async" /></p>`;
        }
        return html;
      }

      const res = await api("/api/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          modelSlug: selectedModel().slug,
          messages: messagesForRequest(),
          stream: true,
        }),
      });

      if (res.status === 401) {
        let msg = "Войдите в аккаунт, чтобы пользоваться платными моделями.";
        try {
          const j = await res.json();
          if (j.detail && j.detail !== "LOGIN_REQUIRED") msg = "Требуется вход.";
        } catch {
          /* use default */
        }
        errEl.textContent = msg;
        errEl.style.display = "block";
        typing.remove();
        await persistThread();
        return;
      }
      if (res.status === 402) {
        let msg = "Недостаточно средств на балансе для этого запроса.";
        try {
          const j = await res.json();
          const d = j.detail;
          if (d && typeof d === "object" && d.estimatedMinRub != null) {
            msg = `На балансе не хватает на расчётный минимум (~${Number(
              d.estimatedMinRub,
            ).toLocaleString("ru-RU", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 4,
            })} ₽). Пополните баланс.`;
          }
        } catch {
          /* use default */
        }
        errEl.textContent = msg;
        errEl.style.display = "block";
        typing.remove();
        await persistThread();
        return;
      }
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        console.error("[chat /messages]", res.status, j);
        errEl.textContent = "Ошибка";
        errEl.style.display = "block";
        typing.remove();
        await persistThread();
        return;
      }

      await consumeStream(
        res.body.getReader(),
        (delta) => {
          acc += delta;
          typing.remove();
          md.innerHTML = renderAssistantHtml();
          listEl.scrollTop = listEl.scrollHeight;
        },
        (rub) => {
          cost.textContent = `Стоимость запроса: ${Number(rub).toLocaleString("ru-RU", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 4,
          })} ₽`;
        },
        (detail) => {
          const mod = selectedModel();
          const msg = humanizeOpenRouterStreamError(
            typeof detail === "string" ? detail : "",
            { isFree: mod?.isFree === true },
          );
          errEl.textContent = msg;
          errEl.style.display = "block";
          typing.remove();
        },
        (images) => {
          for (const item of images || []) {
            const u = item?.image_url?.url;
            if (typeof u === "string" && u && !imageUrls.includes(u)) imageUrls.push(u);
          }
          typing.remove();
          md.innerHTML = renderAssistantHtml();
          listEl.scrollTop = listEl.scrollHeight;
        },
      );

      const asst = { role: "assistant", content: acc };
      if (imageUrls.length) asst.imageUrls = imageUrls;
      history.push(asst);
      typing.remove();

      const me2 = await api("/api/auth/me").then((r) => r.json());
      if (!me2.guest && me2.balance != null) refreshBalance(me2.balance);
      await persistThread();
    } catch (e) {
      console.error("[chat] runChat", e);
      errEl.textContent = "Ошибка";
      errEl.style.display = "block";
      await persistThread();
    } finally {
      streaming = false;
      setSidebarBusy(false);
    }
  }

  await initConversations();

  if (btnNewChat) {
    btnNewChat.addEventListener("click", () => {
      void newChat();
    });
  }
  if (threadListEl) {
    threadListEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".sidebar-thread-item");
      if (!btn || !threadListEl.contains(btn)) return;
      const id = btn.dataset.threadId;
      if (id && id !== currentConversationId) void openThread(id);
    });
  }

  if (btnVideoEl && videoPromptEl && videoStatusEl && videoPlayerEl) {
    const videoDebugEl = document.getElementById("video-debug");

    function videoClear() {
      videoStatusEl.removeAttribute("title");
      if (videoDebugEl) {
        videoDebugEl.style.display = "none";
        videoDebugEl.textContent = "";
      }
    }

    function videoFail(reason, data) {
      logVideo(reason, data);
      let line = reason;
      try {
        const p =
          data instanceof Error
            ? { name: data.name, message: data.message }
            : data;
        const s = typeof p === "string" ? p : JSON.stringify(p);
        line = `${reason}: ${s.slice(0, 1800)}`;
      } catch {
        line = String(reason);
      }
      videoStatusEl.textContent = "Ошибка";
      videoStatusEl.setAttribute("title", line);
      if (videoDebugEl) {
        videoDebugEl.style.display = "block";
        videoDebugEl.textContent = line;
      }
    }

    videoPlayerEl.addEventListener("loadedmetadata", () => {
      const dur = videoPlayerEl.duration;
      if (!videoPlayerEl.src || !isFinite(dur) || dur <= 0) return;
      videoStatusEl.textContent = `Готово (${Math.round(dur)} с)`;
      videoClear();
    });

    videoPlayerEl.addEventListener("error", () => {
      const src = videoPlayerEl.currentSrc || videoPlayerEl.src || "";
      const me = videoPlayerEl.error;
      videoFail("video-element", {
        src,
        mediaErrorCode: me?.code,
        mediaErrorMessage: me?.message,
      });
    });

    btnVideoEl.onclick = async () => {
      try {
        const prompt = videoPromptEl.value.trim();
        if (!prompt) return;
        const mod = selectedModel();
        if (!mod?.slug) {
          videoFail("no model", { modelsLen: models?.length });
          return;
        }
        if (mod.supportsVideoGeneration !== true) {
          videoFail("not a video model", { slug: mod.slug });
          return;
        }
        if (mod.isFree !== true && isGuest) {
          videoStatusEl.textContent =
            "Платное видео — после входа (кнопка «Войти» в шапке).";
          return;
        }
        errEl.style.display = "none";
        errEl.textContent = "";
        videoClear();
        videoStatusEl.textContent = "Отправка запроса…";
        videoPlayerEl.style.display = "none";
        videoPlayerEl.removeAttribute("src");
        const r = await api("/api/v1/video/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ modelSlug: mod.slug, prompt }),
        });
        let j;
        try {
          j = await r.json();
        } catch (e) {
          videoFail("POST JSON parse", { status: r.status, err: String(e) });
          return;
        }
        if (r.status === 402) {
          videoStatusEl.textContent = "Недостаточно средств.";
          return;
        }
        if (!r.ok) {
          if (r.status === 401) {
            videoStatusEl.textContent = "Войдите, чтобы создавать платное видео.";
            return;
          }
          videoFail("POST HTTP error", { status: r.status, body: j });
          return;
        }
        if (j.error && !j.id) {
          videoFail("POST 2xx but error in body", j);
          return;
        }
        const jobId = j.id;
        if (!jobId) {
          videoFail("missing job id", j);
          return;
        }
        try {
          console.info("[video] job created, polling", String(jobId));
        } catch {
          /* ignore */
        }
        const terminalBad = new Set(["failed", "cancelled", "expired"]);
        const poll = async () => {
          try {
            const pr = await api(`/api/v1/video/jobs/${encodeURIComponent(jobId)}`);
            let st;
            try {
              st = await pr.json();
            } catch (e) {
              videoFail("poll JSON parse", { status: pr.status, err: String(e) });
              return;
            }
            if (!pr.ok) {
              videoFail("poll HTTP error", { status: pr.status, body: st });
              return;
            }
            const status = st.status;
            videoStatusEl.textContent = `Статус: ${status || "…"}`;
            if (terminalBad.has(status)) {
              videoFail("job terminal error", st);
              return;
            }
            if (status === "completed") {
              const hasFile =
                Array.isArray(st.unsigned_urls) && st.unsigned_urls.length > 0;
              if (hasFile) {
                /* Прямой URL на openrouter.ai в <video> не работает: нет Bearer. Тянем через наш прокси. */
                const streamUrl = `/api/v1/video/jobs/${encodeURIComponent(jobId)}/content?index=0`;
                videoPlayerEl.src = streamUrl;
                videoPlayerEl.style.display = "block";
                videoPlayerEl.load();
              } else {
                videoFail("completed without unsigned_urls", st);
              }
              const me3 = await api("/api/auth/me").then((res) => res.json());
              if (!me3.guest && me3.balance != null) refreshBalance(me3.balance);
              return;
            }
            setTimeout(poll, 5000);
          } catch (e) {
            videoFail("poll uncaught", e);
          }
        };
        setTimeout(poll, 4000);
      } catch (e) {
        videoFail("handler uncaught", e);
      }
    };
  }

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (streaming) return;
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    const text = inputEl.value.trim();
    if (!text) return;
    if (mod.isFree !== true && isGuest) {
      errEl.textContent =
        "Платные модели доступны после входа. Нажмите «Войти» в шапке.";
      errEl.style.display = "block";
      return;
    }
    inputEl.value = "";

    await ensureConversation();
    history.push({ role: "user", content: text });
    appendUserBubble(text);
    await runChat();
  });

  fileEl.addEventListener("change", async () => {
    const f = fileEl.files?.[0];
    fileEl.value = "";
    if (!f || !f.type.startsWith("image/")) return;
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    if (!mod.supportsVision) {
      console.error("[chat] model without vision", mod.slug);
      errEl.textContent = "Ошибка";
      errEl.style.display = "block";
      return;
    }
    if (mod.isFree !== true && isGuest) {
      errEl.textContent =
        "Платные модели доступны после входа. Нажмите «Войти» в шапке.";
      errEl.style.display = "block";
      return;
    }
    const b64 = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result));
      r.onerror = rej;
      r.readAsDataURL(f);
    });
    const text = inputEl.value.trim() || "Опиши изображение.";
    inputEl.value = "";

    await ensureConversation();
    const userContent = [
      { type: "text", text },
      { type: "image_url", image_url: { url: b64 } },
    ];
    history.push({ role: "user", content: userContent });
    appendUserBubble(`${text} [изображение]`);
    await runChat();
  });
}

main();
