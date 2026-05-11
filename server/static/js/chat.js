import { consumeStream } from "./sse.js?v=23";

const MODEL_STORAGE_KEY = "ai_proxy_model_slug";
/** Выставляется на /tariffs при выборе модели — чат не подменяет её моделью из старого диалога. */
const MODEL_EXPLICIT_CHOICE_KEY = "ai_proxy_explicit_model";
const STT_STORAGE_KEY = "ai_proxy_stt_slug";
const TTS_VOICE_STORAGE_KEY = "ai_proxy_tts_voice";
const ALLOWED_TTS_VOICE_IDS = [
  "alloy",
  "echo",
  "fable",
  "onyx",
  "nova",
  "shimmer",
];

function readStoredTtsVoice() {
  try {
    const v = (localStorage.getItem(TTS_VOICE_STORAGE_KEY) || "").trim().toLowerCase();
    if (v && ALLOWED_TTS_VOICE_IDS.includes(v)) return v;
  } catch {
    /* ignore */
  }
  return "alloy";
}

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
  try {
    const r = await api("/api/auth/me");
    if (!r.ok) {
      return {
        guest: true,
        balance: null,
        yookassa_enabled: false,
        isAdmin: false,
        lastModelSlug: null,
      };
    }
    const j = await r.json().catch(() => null);
    if (!j || typeof j !== "object") {
      return {
        guest: true,
        balance: null,
        yookassa_enabled: false,
        isAdmin: false,
        lastModelSlug: null,
      };
    }
    return j;
  } catch {
    return {
      guest: true,
      balance: null,
      yookassa_enabled: false,
      isAdmin: false,
      lastModelSlug: null,
    };
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/**
 * Собираем бинарник из SSE (base64), подбираем MIME для <audio>.
 * — Готовые контейнеры (RIFF WAV, MP3, OGG, FLAC) не трогаем.
 * — Сиротский PCM16: OpenAI TTS ~24kHz mono; Lyria (Google) часто 48kHz stereo (см. OpenRouter).
 */
function pcm16ToWavBlob(pcm, sampleRate, numChannels) {
  const bitsPerSample = 16;
  const blockAlign = numChannels * (bitsPerSample / 8);
  const byteRate = sampleRate * blockAlign;
  let body = pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm);
  const frame = blockAlign;
  if (frame > 0 && body.byteLength % frame !== 0) {
    const trim = body.byteLength - (body.byteLength % frame);
    body = body.subarray(0, trim > 0 ? trim : body.byteLength);
  }
  const dataSize = body.byteLength;
  const headerSize = 44;
  const out = new Uint8Array(headerSize + dataSize);
  const v = new DataView(out.buffer);
  const wstr = (o, s) => {
    for (let i = 0; i < s.length; i++) out[o + i] = s.charCodeAt(i);
  };
  wstr(0, "RIFF");
  v.setUint32(4, 36 + dataSize, true);
  wstr(8, "WAVE");
  wstr(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, numChannels, true);
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, byteRate, true);
  v.setUint16(32, blockAlign, true);
  v.setUint16(34, bitsPerSample, true);
  wstr(36, "data");
  v.setUint32(40, dataSize, true);
  out.set(body, headerSize);
  return new Blob([out], { type: "audio/wav" });
}

function assistantResponseBytesToAudioBlob(bytes, opts = {}) {
  const slug = String(opts.modelSlug || "").toLowerCase();
  const lyria = slug.includes("lyria");
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (u8.length === 0) {
    return new Blob([], { type: "audio/wav" });
  }
  if (
    u8.length >= 12 &&
    u8[0] === 0x52 &&
    u8[1] === 0x49 &&
    u8[2] === 0x46 &&
    u8[3] === 0x46
  ) {
    return new Blob([u8], { type: "audio/wav" });
  }
  if (
    u8.length >= 3 &&
    u8[0] === 0x49 &&
    u8[1] === 0x44 &&
    u8[2] === 0x33
  ) {
    return new Blob([u8], { type: "audio/mpeg" });
  }
  if (u8.length >= 2 && u8[0] === 0xff && (u8[1] & 0xe0) === 0xe0) {
    return new Blob([u8], { type: "audio/mpeg" });
  }
  if (
    u8.length >= 4 &&
    u8[0] === 0x4f &&
    u8[1] === 0x67 &&
    u8[2] === 0x67 &&
    u8[3] === 0x53
  ) {
    return new Blob([u8], { type: "audio/ogg" });
  }
  if (
    u8.length >= 4 &&
    u8[0] === 0x66 &&
    u8[1] === 0x4c &&
    u8[2] === 0x61 &&
    u8[3] === 0x63
  ) {
    return new Blob([u8], { type: "audio/flac" });
  }
  if (lyria) {
    return pcm16ToWavBlob(u8, 48000, 2);
  }
  return pcm16ToWavBlob(u8, 24000, 1);
}

function renderMd(html) {
  if (typeof marked !== "undefined" && marked.parse) {
    return marked.parse(html, { breaks: true });
  }
  return escapeHtml(html).replace(/\n/g, "<br>");
}

/** Модель только STT (нет диалога chat completions) — вкладка «Транскрипция» в пикере. */
function isSttOnlyModel(m) {
  if (!m) return false;
  return (
    m.supportsTranscription === true &&
    m.supportsSpeech !== true &&
    m.supportsMusicGeneration !== true &&
    m.supportsImageGeneration !== true &&
    m.supportsVideoGeneration !== true
  );
}

/** Микрофон/аудиофайл: любая небесплатная модель (кроме видео). Диктовка идёт через STT, «речь в чате» — с supportsSpeech. */
function modelAllowsVoiceInput(m) {
  if (!m || m.supportsVideoGeneration === true) return false;
  if (m.isFree === true) return false;
  return true;
}

/** Группа модели для сетки: бесплатные > видео > изображения > речь в чате > транскрипция > музыка > текст */
function modelGroupId(m) {
  if (!m || typeof m !== "object") return "text";
  if (m.isFree === true) return "free";
  if (m.supportsVideoGeneration) return "video";
  if (m.supportsImageGeneration) return "image";
  if (m.supportsSpeech && m.supportsTranscription) return "speech";
  if (m.supportsSpeech && m.supportsMusicGeneration) {
    const s = String(m.slug || "");
    if (s.includes("lyria")) return "music";
    return "speech";
  }
  if (m.supportsSpeech) return "speech";
  if (m.supportsTranscription) return "transcription";
  if (m.supportsMusicGeneration) return "music";
  return "text";
}

/** Готовое аудио в ответ (Lyria и модели с речью+музыкой в API). Не путать с «(музыка в чате)» у Claude/GPT — там только текст. */
function modelProducesChatAudio(m) {
  if (!m || typeof m !== "object") return false;
  const s = String(m.slug || "").toLowerCase();
  if (s.includes("lyria")) return true;
  return m.supportsSpeech === true && m.supportsMusicGeneration === true;
}

/** Во вкладке «Музыка» выбрана модель без выхода звука — только лирика/промпты. */
function isTextOnlyMusicAssistModel(m) {
  if (!m || typeof m !== "object") return false;
  return (
    modelGroupId(m) === "music" &&
    m.supportsMusicGeneration === true &&
    !modelProducesChatAudio(m)
  );
}

const MODEL_GROUPS = [
  { id: "free", emoji: "🎁", title: "Бесплатные" },
  { id: "text", emoji: "💬", title: "Текст и чат" },
  { id: "image", emoji: "🖼", title: "Изображения" },
  { id: "video", emoji: "🎬", title: "Видео" },
  { id: "transcription", emoji: "📝", title: "Транскрипция" },
  { id: "speech", emoji: "🎙️", title: "Речь в чате" },
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
 * Понятные подсказки к ответам модели в потоке.
 * :free в slug и openrouter/free — правда общая очередь; is_free без :free в slug — только флаг в БД, текст нейтральнее.
 */
function humanizeOpenRouterStreamError(detail, opts = {}) {
  const isFree = opts.isFree === true;
  const slug = String(opts.modelSlug || "");
  const useFreeQueueStory =
    isFree &&
    (slug.includes(":free") ||
      slug === "openrouter/free" ||
      slug.startsWith("openrouter/free"));

  const m = String(detail || "").trim();
  const low = m.toLowerCase();

  const providerBlob = () => {
    if (useFreeQueueStory) {
      return (
        "Бесплатный канал: общая очередь или временная недоступность этой модели у провайдера. " +
        "Подождите минуту и повторите, выберите **Бесплатно (автовыбор)** или платную модель."
      );
    }
    if (isFree) {
      return (
        "Провайдер не вернул ответ (часто так бывает с **новыми** или **редкими** моделями). " +
        "Повторите запрос, смените модель или используйте **Бесплатно (автовыбор)**."
      );
    }
    return "Провайдер модели вернул ошибку (лимит, перегрузка или сбой канала). Попробуйте другую модель или повторите запрос позже.";
  };

  if (!m) {
    return useFreeQueueStory
      ? "Не удалось получить ответ по бесплатному каналу. Повторите позже или выберите платную модель."
      : isFree
        ? "Не удалось получить ответ. Повторите запрос или выберите другую модель."
        : "Ошибка при ответе модели.";
  }

  if (low.includes("provider returned error")) {
    return providerBlob();
  }
  if (
    low.includes("finish_reason=error") ||
    low.includes("ошибка провайдера") ||
    low.includes("provider") && low.includes("error")
  ) {
    return providerBlob();
  }
  if (low.includes("no endpoints found")) {
    if (useFreeQueueStory) {
      return "Для этой бесплатной модели сейчас нет свободных серверов. Выберите другую бесплатную модель, **Бесплатно (автовыбор)** или платную.";
    }
    if (isFree) {
      return "Для этой модели сейчас нет доступных серверов у провайдера. Выберите другую модель в списке.";
    }
    return "Для выбранной модели сейчас нет доступных серверов. Выберите другую модель в списке.";
  }
  if (low.includes("rate limit") || low.includes("too many requests")) {
    if (useFreeQueueStory) {
      return "Лимит запросов на бесплатном канале. Подождите или перейдите на платную модель.";
    }
    if (isFree) {
      return "Слишком много запросов для этого маршрута. Подождите или смените модель.";
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

  const cfg = await api("/api/config").then((r) => r.json().catch(() => ({})));
  const modelsRaw = await api("/api/models").then((r) => r.json().catch(() => null));
  const models = Array.isArray(modelsRaw)
    ? modelsRaw.filter(
        (x) =>
          x != null &&
          typeof x === "object" &&
          typeof x.slug === "string" &&
          String(x.slug).trim() !== "",
      )
    : [];
  const sttModels = models.filter((x) => x.supportsTranscription === true);
  const chatModels = models.filter((m) => m.supportsChat !== false);

  const balanceEl = document.getElementById("balance");
  const modelPickerEl = document.getElementById("model-picker");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("msg-input");
  const listEl = document.getElementById("messages");
  const errEl = document.getElementById("err");
  const fileEl = document.getElementById("file-input");
  /** С диска (Windows) часто пустой type или application/octet-stream — ориентируемся на расширение. */
  function fileLooksLikeUserAudio(f) {
    if (!f || typeof f !== "object") return false;
    const t = (f.type || "").toLowerCase();
    if (t.startsWith("audio/")) return true;
    const n = String(f.name || "").toLowerCase();
    return /\.(mp3|wav|ogg|opus|oga|webm|m4a|aac|flac|mp4)$/i.test(n);
  }
  const btnPay = document.getElementById("btn-pay");
  const videoPanelEl = document.getElementById("video-panel");
  const videoPromptEl = document.getElementById("video-prompt");
  const btnVideoEl = document.getElementById("btn-video");
  const threadListEl = document.getElementById("thread-list");
  const btnNewChat = document.getElementById("btn-new-chat");
  const chatSidebarEl = document.getElementById("chat-sidebar");
  const chatSidebarBackdrop = document.getElementById("chat-sidebar-backdrop");
  const btnChatSidebarOpen = document.getElementById("btn-chat-sidebar-open");
  const btnChatSidebarClose = document.getElementById("btn-chat-sidebar-close");

  const mqChatDrawer =
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia("(max-width: 900px)")
      : { matches: false };

  function isChatDrawerMode() {
    return Boolean(mqChatDrawer.matches);
  }

  function setChatSidebarOpen(open) {
    if (isGuest) return;
    if (!chatSidebarEl) return;
    if (!isChatDrawerMode()) {
      document.body.classList.remove("chat-sidebar-open");
      btnChatSidebarOpen?.setAttribute("aria-expanded", "false");
      chatSidebarBackdrop?.setAttribute("aria-hidden", "true");
      chatSidebarEl.removeAttribute("aria-hidden");
      return;
    }
    const on = Boolean(open);
    document.body.classList.toggle("chat-sidebar-open", on);
    chatSidebarBackdrop?.setAttribute("aria-hidden", on ? "false" : "true");
    btnChatSidebarOpen?.setAttribute("aria-expanded", on ? "true" : "false");
    chatSidebarEl.setAttribute("aria-hidden", on ? "false" : "true");
  }

  function onChatDrawerBreakpointChange() {
    if (!isChatDrawerMode()) setChatSidebarOpen(false);
  }

  if (typeof mqChatDrawer.addEventListener === "function") {
    mqChatDrawer.addEventListener("change", onChatDrawerBreakpointChange);
  } else if (typeof mqChatDrawer.addListener === "function") {
    mqChatDrawer.addListener(onChatDrawerBreakpointChange);
  }

  if (isGuest) {
    if (chatSidebarEl) chatSidebarEl.style.display = "none";
    if (chatSidebarBackdrop) chatSidebarBackdrop.style.display = "none";
    if (btnChatSidebarOpen) btnChatSidebarOpen.style.display = "none";
  } else {
    chatSidebarBackdrop?.addEventListener("click", () => setChatSidebarOpen(false));
    btnChatSidebarOpen?.addEventListener("click", () => {
      setChatSidebarOpen(!document.body.classList.contains("chat-sidebar-open"));
    });
    btnChatSidebarClose?.addEventListener("click", () => setChatSidebarOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (document.body.classList.contains("chat-sidebar-open")) setChatSidebarOpen(false);
    });
    setChatSidebarOpen(false);
  }

  if (!isGuest && cfg && cfg.yookassaEnabled && btnPay) {
    const shopId = cfg.yookassaShopId ? String(cfg.yookassaShopId).trim() : "";
    // Не подставлять только publicAppUrl из .env: при www / другом алиасе домена редирект с ЮKassa
    // уходит на «канонический» хост без cookie → гость → sync-simplepay не вызывается, баланс не растёт.
    const base = (typeof window !== "undefined" ? window.location.origin : "").replace(
      /\/$/,
      "",
    );

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

      const shopIdInput = form?.querySelector('[name="shopId"]');
      if (shopIdInput) shopIdInput.value = shopId;

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
  } else if (btnPay) {
    btnPay.style.display = "none";
  }

  if (new URLSearchParams(location.search).get("openPay") === "1") {
    const u = new URL(location.href);
    u.searchParams.delete("openPay");
    history.replaceState(
      {},
      "",
      u.pathname + (u.searchParams.toString() ? `?${u.searchParams.toString()}` : "") + u.hash,
    );
    const sid = cfg?.yookassaShopId ? String(cfg.yookassaShopId).trim() : "";
    if (
      !isGuest &&
      cfg &&
      cfg.yookassaEnabled &&
      sid &&
      document.getElementById("pay-modal")
    ) {
      queueMicrotask(() => {
        openPayModal();
        loadYooKassaConstructOnce().catch((e) => console.error("[yookassa/script]", e));
      });
    }
  }

  function refreshBalance(b) {
    if (isGuest || balanceEl == null) return;
    const n = Number(b);
    if (Number.isNaN(n)) return;
    balanceEl.textContent = `${n.toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    })} ₽`;
  }
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
        for (let i = 0; i < 8; i++) {
          const sr = await api("/api/payments/sync-simplepay", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ orderId: oid }),
          });
          const sd = await sr.json().catch(() => ({}));
          lastSd = sd;
          if (sd.credited === true || sd.alreadyDone === true) break;
          await new Promise((r) => setTimeout(r, 2500));
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
      } else if (treatAsSuccess && oid && cfg.yookassaEnabled && isGuest) {
        errEl.textContent =
          "Оплата прошла, но вы не авторизованы на этом адресе — баланс не обновился. Зайдите на сайт с того же домена, на котором входили перед оплатой (например с www или без), войдите и обновите страницу. При необходимости укажите в поддержке номер платежа из кабинета ЮKassa.";
        errEl.style.display = "block";
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

  /** Запоминание последней модели на сервере (аккаунт). */
  let persistLastModelTimer = null;
  function schedulePersistLastModel(slug) {
    if (isGuest) return;
    if (!slug || !chatModels.some((x) => x.slug === slug)) return;
    clearTimeout(persistLastModelTimer);
    persistLastModelTimer = setTimeout(async () => {
      try {
        await api("/api/auth/me", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lastModelSlug: slug }),
        });
      } catch (e) {
        console.warn("[auth] lastModelSlug", e);
      }
    }, 400);
  }

  let selectedSlug = chatModels[0]?.slug ?? "";
  /** true: не открывать последний чат при старте — уважать ?model= или выбор с тарифов. */
  let honorExplicitModelChoice = false;
  /* Сначала ?model= (переход с /tariffs), иначе последний выбор из сессии. */
  try {
    const qp = new URLSearchParams(window.location.search);
    const qModel = qp.get("model");
    if (qModel && chatModels.some((x) => x.slug === qModel)) {
      selectedSlug = qModel;
      sessionStorage.setItem(MODEL_STORAGE_KEY, qModel);
      honorExplicitModelChoice = true;
      const u = new URL(window.location.href);
      u.searchParams.delete("model");
      const qs = u.searchParams.toString();
      history.replaceState({}, "", u.pathname + (qs ? `?${qs}` : "") + u.hash);
      if (!isGuest) schedulePersistLastModel(qModel);
    } else {
      const savedSlug = sessionStorage.getItem(MODEL_STORAGE_KEY);
      let hasSavedSlug = false;
      if (savedSlug && chatModels.some((x) => x.slug === savedSlug)) {
        selectedSlug = savedSlug;
        hasSavedSlug = true;
      }
      if (isGuest && !hasSavedSlug) {
        const freeSlug = chatModels.find((x) => x.slug === "openrouter/free")?.slug;
        if (freeSlug) selectedSlug = freeSlug;
      }
      if (
        !isGuest &&
        typeof me.lastModelSlug === "string" &&
        me.lastModelSlug.trim() &&
        chatModels.some((x) => x.slug === me.lastModelSlug)
      ) {
        selectedSlug = me.lastModelSlug.trim();
        sessionStorage.setItem(MODEL_STORAGE_KEY, selectedSlug);
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
      const on = btn.dataset.modelId === slug;
      btn.classList.toggle("model-card--selected", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
    const sel = chatModels.find((x) => x.slug === slug);
    if (sel) activateModelTab(modelGroupId(sel));
    const cards = modelPickerEl.querySelectorAll(".model-card");
    let picked = null;
    for (const c of cards) {
      if (c.dataset.modelId === slug) {
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
    if (!chatModels.some((x) => x.slug === slug)) return;
    selectedSlug = slug;
    sessionStorage.setItem(MODEL_STORAGE_KEY, slug);
    selectModelVisual(slug);
    updateHints();
    schedulePersistLastModel(slug);
  }

  if (modelPickerEl) {
    const buckets = { free: [], text: [], image: [], video: [], transcription: [], speech: [], music: [] };
    for (const m of chatModels) {
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
        const sm = chatModels.find((x) => x.slug === selectedSlug);
        if (sm) return modelGroupId(sm);
        return groupsWithModels[0].id;
      })();

      function buildModelButton(m) {
        const letter = (m.displayName || "?").trim().charAt(0).toUpperCase();
        const hue = hueFromSlug(m.slug);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "model-card";
        btn.dataset.modelId = m.slug;
        btn.setAttribute("role", "radio");
        btn.setAttribute("aria-checked", "false");
        const desc =
          typeof m.descriptionRu === "string" && m.descriptionRu.trim()
            ? m.descriptionRu.trim()
            : `${m.provider}. Детали и цены — в разделе «Все модели».`;
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

        const titleRow = document.createElement("span");
        titleRow.className = "model-card-title-row";
        const provEl = document.createElement("span");
        provEl.className = "model-card-title-provider";
        provEl.textContent = m.provider;
        titleRow.appendChild(provEl);
        const sepEl = document.createElement("span");
        sepEl.className = "model-card-title-sep";
        sepEl.setAttribute("aria-hidden", "true");
        sepEl.textContent = " · ";
        titleRow.appendChild(sepEl);
        const nameEl = document.createElement("span");
        nameEl.className = "model-card-title-name";
        nameEl.textContent = m.displayName;
        titleRow.appendChild(nameEl);
        if (m.isFree === true) {
          const fb = document.createElement("span");
          fb.className = "model-card-free-badge";
          fb.textContent = "Free";
          titleRow.appendChild(fb);
        }

        const dc = document.createElement("span");
        dc.className = "model-card-desc";
        dc.textContent = desc;

        body.appendChild(titleRow);
        body.appendChild(dc);
        btn.appendChild(iconWrap);
        btn.appendChild(body);
        return btn;
      }

      groupsWithModels.forEach((g) => {
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

        if (g.id === "music") {
          const audioMs = list.filter((m) => modelProducesChatAudio(m));
          const textOnlyMs = list.filter((m) => !modelProducesChatAudio(m));
          const row = document.createElement("div");
          row.className = "model-scroll-row";
          row.setAttribute("role", "group");
          row.setAttribute(
            "aria-label",
            "Музыка: сначала генерация аудио, затем текстовые модели",
          );
          for (const m of audioMs) {
            row.appendChild(buildModelButton(m));
          }
          for (const m of textOnlyMs) {
            row.appendChild(buildModelButton(m));
          }
          panel.appendChild(row);
        } else {
          const row = document.createElement("div");
          row.className = "model-scroll-row";
          row.setAttribute("role", "group");
          row.setAttribute("aria-label", g.title);

          for (const m of list) {
            row.appendChild(buildModelButton(m));
          }
          panel.appendChild(row);
        }
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
        const slug = btn.dataset.modelId;
        if (slug) void selectModelPick(slug);
      });
    }
  }

  const imgGenHintEl = document.getElementById("img-gen-hint");
  const sttModeHintEl = document.getElementById("stt-mode-hint");
  const musicTextHintEl = document.getElementById("music-text-hint");
  const videoModeHintEl = document.getElementById("video-mode-hint");
  function updateHints() {
    const m = selectedModel();
    if (!m) return;
    const vid = m.supportsVideoGeneration === true;
    if (imgGenHintEl) {
      imgGenHintEl.style.display =
        m.supportsImageGeneration === true && m.supportsVideoGeneration !== true
          ? "block"
          : "none";
    }
    if (sttModeHintEl) {
      sttModeHintEl.style.display = !vid && isSttOnlyModel(m) ? "block" : "none";
    }
    if (musicTextHintEl) {
      musicTextHintEl.style.display =
        !vid && isTextOnlyMusicAssistModel(m) ? "block" : "none";
    }
    if (videoModeHintEl) {
      videoModeHintEl.style.display = vid ? "block" : "none";
    }
    const bm = document.getElementById("btn-mic");
    if (bm) {
      const allowVi = modelAllowsVoiceInput(m);
      bm.style.display = allowVi ? "" : "none";
      bm.disabled = !allowVi;
      bm.title = allowVi
        ? "Удерживайте — запись; отпустите — отправка: при модели «Речь в чате» аудио уходит в модель, иначе — распознавание речи (платно)."
        : m?.isFree === true
          ? "Голос: доступен только на платных моделях"
          : "Голос недоступен в этом режиме";
    }
    const at = document.getElementById("btn-attach");
    if (at) {
      const allowAtt = m.isFree !== true;
      at.style.display = allowAtt ? "" : "none";
      at.disabled = !allowAtt;
      at.title = allowAtt
        ? "Прикрепить изображение или аудио"
        : "Вложения доступны только на платных моделях";
    }
    if (videoPanelEl && formEl) {
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
  const chatHistory = [];
  /** Пока идёт генерация видео — не переключать диалог/модель. */
  let videoGenInProgress = false;

  function selectedModel() {
    const found = chatModels.find((x) => x.slug === selectedSlug);
    if (found) return found;
    if (selectedSlug) {
      console.warn(
        "[model] выбранный идентификатор модели не найден в списке, сброс на первую модель:",
        selectedSlug,
      );
    }
    return chatModels[0] ?? null;
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

  /** Текст и/или image_url из content сообщения (после отправки и из истории). */
  function appendUserMessageDisplay(content) {
    const wrap = document.createElement("div");
    wrap.className = "msg user";
    const bubble = document.createElement("div");
    bubble.className = "bubble bubble--user-content";

    if (typeof content === "string") {
      const p = document.createElement("p");
      p.style.margin = "0";
      p.style.whiteSpace = "pre-wrap";
      p.textContent = content;
      bubble.appendChild(p);
    } else if (Array.isArray(content)) {
      let hasImage = false;
      for (const part of content) {
        const u =
          part?.type === "image_url" &&
          part.image_url &&
          typeof part.image_url.url === "string"
            ? part.image_url.url
            : "";
        if (!u) continue;
        hasImage = true;
        const fig = document.createElement("p");
        fig.className = "user-attached-img-wrap";
        const img = document.createElement("img");
        img.src = u;
        img.className = "user-attached-image";
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        fig.appendChild(img);
        bubble.appendChild(fig);
      }
      for (const part of content) {
        if (part?.type === "text" && typeof part.text === "string" && part.text) {
          const para = document.createElement("p");
          para.className = hasImage ? "user-msg-text-after-media" : "";
          para.style.margin = "0";
          para.style.whiteSpace = "pre-wrap";
          para.textContent = part.text;
          bubble.appendChild(para);
        }
      }
      if (!bubble.childNodes.length) {
        const p = document.createElement("p");
        p.style.margin = "0";
        p.textContent = userContentPreview(content);
        bubble.appendChild(p);
      }
    } else {
      const p = document.createElement("p");
      p.style.margin = "0";
      p.textContent = "[сообщение]";
      bubble.appendChild(p);
    }

    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
    listEl.scrollTop = listEl.scrollHeight;
  }

  function appendUserVoiceBubble(opts) {
    const src =
      opts && opts.source === "file" ? "file" : "mic";
    const fn =
      opts && typeof opts.fileName === "string" ? opts.fileName.trim() : "";
    const transcript =
      opts && typeof opts.transcript === "string"
        ? opts.transcript.trim()
        : "";
    const spendRaw =
      opts && typeof opts.spendNote === "string"
        ? opts.spendNote.trim()
        : "";
    const metaLabel =
      src === "file"
        ? fn
          ? fn.length > 28
            ? `${fn.slice(0, 25)}…`
            : fn
          : "Файл"
        : "Микр.";

    const wrap = document.createElement("div");
    wrap.className = "msg user";
    wrap.dataset.voiceBubble = "1";
    const bubble = document.createElement("div");
    bubble.className = "bubble bubble--voice";
    bubble.setAttribute("role", "group");

    if (transcript) {
      const preview =
        transcript.length > 160 ? `${transcript.slice(0, 157)}…` : transcript;
      bubble.setAttribute("aria-label", `Распознано: ${preview}`);
      const tr = document.createElement("div");
      tr.className = "user-voice-transcript user-voice-transcript--lead";
      tr.setAttribute("data-role", "voice-transcript");
      tr.textContent = transcript;
      bubble.appendChild(tr);

      const foot = document.createElement("div");
      foot.className = "user-voice-foot";
      const icon = document.createElement("span");
      icon.className = "user-voice-meta-compact";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = src === "file" ? "📎" : "🎤";
      const lab = document.createElement("span");
      lab.className = "user-voice-meta-compact-label";
      lab.textContent = metaLabel;
      foot.appendChild(icon);
      foot.appendChild(lab);
      if (spendRaw) {
        foot.appendChild(document.createTextNode(" · "));
        const sp = document.createElement("span");
        sp.className = "voice-spend-inline";
        sp.textContent = spendRaw;
        foot.appendChild(sp);
      }
      bubble.appendChild(foot);
    } else {
      bubble.setAttribute(
        "aria-label",
        src === "file" ? (fn ? `Аудио ${fn}` : "Аудио") : "Голос",
      );
      const row = document.createElement("div");
      row.className = "user-voice-compact-only";
      const ic = document.createElement("span");
      ic.className = "user-voice-meta-compact";
      ic.setAttribute("aria-hidden", "true");
      ic.textContent = src === "file" ? "📎" : "🎤";
      const lb = document.createElement("span");
      lb.className = "user-voice-compact-label";
      lb.textContent = metaLabel;
      row.appendChild(ic);
      row.appendChild(lb);
      bubble.appendChild(row);
      if (spendRaw) {
        const line = document.createElement("div");
        line.className = "voice-spend-line voice-spend-line--user";
        line.textContent = spendRaw;
        bubble.appendChild(line);
      }
    }

    wrap.appendChild(bubble);
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
    const audioBlock = document.createElement("div");
    audioBlock.className = "assistant-audio-block";
    audioBlock.style.display = "none";
    audioBlock.setAttribute("aria-label", "Голосовой ответ");
    const audioLabel = document.createElement("div");
    audioLabel.className = "assistant-audio-label";
    audioLabel.textContent = "Голосовой ответ";
    const audioOut = document.createElement("audio");
    audioOut.className = "assistant-audio";
    audioOut.controls = true;
    audioOut.preload = "none";
    audioOut.setAttribute("aria-label", "Прослушать ответ");
    audioBlock.appendChild(audioLabel);
    audioBlock.appendChild(audioOut);
    const cost = document.createElement("div");
    cost.className = "cost";
    bubble.appendChild(typing);
    bubble.appendChild(md);
    bubble.appendChild(audioBlock);
    bubble.appendChild(cost);
    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
    listEl.scrollTop = listEl.scrollHeight;
    return { md, cost, typing, audioOut, audioBlock };
  }

  let currentConversationId = null;

  function modelDisplayName(slug) {
    const m = models.find((x) => x.slug === slug);
    return m ? m.displayName : slug || "—";
  }

  function messagesForRequest() {
    return chatHistory.map((m) => ({ role: m.role, content: m.content }));
  }

  function deriveTitleFromHistory() {
    for (const m of chatHistory) {
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
      if (content.some((x) => x?.type === "input_audio")) return "🎤 Голосовое сообщение";
      const t = content.find((x) => x?.type === "text");
      return String(t?.text ?? "").trim() || "[изображение]";
    }
    return "[сообщение]";
  }

  function voiceMetaForPersist(meta) {
    if (!meta || meta.voiceMessage !== true) return undefined;
    const o = { voiceMessage: true };
    if (meta.audioSource === "file" || meta.audioSource === "mic")
      o.audioSource = meta.audioSource;
    if (typeof meta.fileName === "string" && meta.fileName.trim())
      o.fileName = meta.fileName.trim();
    if (typeof meta.voiceTranscript === "string" && meta.voiceTranscript.trim())
      o.voiceTranscript = meta.voiceTranscript.trim();
    return o;
  }

  function videoRequestMetaForPersist(meta) {
    if (!meta || meta.videoRequest !== true) return undefined;
    const o = { videoRequest: true };
    if (typeof meta.modelSlug === "string" && meta.modelSlug.trim())
      o.modelSlug = meta.modelSlug.trim();
    if (typeof meta.prompt === "string") o.prompt = meta.prompt;
    return o;
  }

  function videoAssistantMetaForPersist(meta) {
    if (!meta || meta.videoGeneration !== true) return undefined;
    const o = { videoGeneration: true };
    if (meta.videoError === true) o.videoError = true;
    if (typeof meta.jobId === "string" && meta.jobId.trim())
      o.jobId = meta.jobId.trim();
    if (typeof meta.modelSlug === "string" && meta.modelSlug.trim())
      o.modelSlug = meta.modelSlug.trim();
    if (meta.costRub != null && String(meta.costRub).trim() !== "")
      o.costRub = String(meta.costRub);
    return o;
  }

  function pickPersistMeta(meta, role) {
    return (
      voiceMetaForPersist(meta) ||
      videoRequestMetaForPersist(meta) ||
      (role === "assistant" ? videoAssistantMetaForPersist(meta) : undefined)
    );
  }

  function sanitizeMessagesForPersist(msgs) {
    return msgs.map((m) => {
      const { meta, ...rest } = m;
      let msg = rest;
      if (msg.role !== "user" || !Array.isArray(msg.content)) {
        const o = { ...msg };
        const pm = pickPersistMeta(meta, msg.role);
        if (pm) o.meta = pm;
        return o;
      }
      const filtered = msg.content.filter((p) => p?.type !== "input_audio");
      if (filtered.length === msg.content.length) {
        const o = { ...msg };
        const pm = pickPersistMeta(meta, msg.role);
        if (pm) o.meta = pm;
        return o;
      }
      if (filtered.length > 0) {
        const o = { ...msg, content: filtered };
        const pm = pickPersistMeta(meta, msg.role);
        if (pm) o.meta = pm;
        return o;
      }
      const o = {
        ...msg,
        content: [{ type: "text", text: "🎤 Голосовое сообщение" }],
      };
      const pm = pickPersistMeta(meta, msg.role);
      if (pm) o.meta = pm;
      return o;
    });
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
      if (html.includes(u)) continue;
      const esc = escapeHtml(u);
      html += `<p class="gen-img-wrap"><img src="${esc}" alt="" class="gen-image" loading="lazy" decoding="async" /></p>`;
    }
    md.innerHTML = html;
    bubble.appendChild(md);
    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
  }

  function appendVideoUserBubbleFromSaved(msg) {
    const c = msg.content;
    const line =
      typeof c === "string" && c.trim()
        ? c
        : `🎬 Видео (${modelDisplayName(msg.meta?.modelSlug)})\n${String(msg.meta?.prompt ?? "")}`;
    const wrap = document.createElement("div");
    wrap.className = "msg user";
    wrap.innerHTML = `<div class="bubble"><p style="margin:0;white-space:pre-wrap">${escapeHtml(
      line,
    )}</p></div>`;
    listEl.appendChild(wrap);
  }

  function renderAssistantVideoFromSaved(msg) {
    const meta = msg.meta || {};
    const jobId = typeof meta.jobId === "string" ? meta.jobId.trim() : "";
    const errFlag = meta.videoError === true;
    const hasVideo = jobId && !errFlag;

    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const statusP = document.createElement("p");
    statusP.className = "hint assistant-video-status";
    statusP.style.margin = "0 0 0.5rem";
    statusP.textContent =
      typeof msg.content === "string" && msg.content.trim()
        ? msg.content.trim()
        : hasVideo
          ? "Видео готово."
          : "Ошибка";

    const debugPre = document.createElement("pre");
    debugPre.className = "video-debug video-debug--inline";
    debugPre.style.display = "none";
    const vid = document.createElement("video");
    vid.className = "assistant-video";
    vid.controls = true;
    vid.setAttribute("playsinline", "");
    vid.preload = "metadata";
    if (hasVideo) {
      vid.src = `/api/v1/video/jobs/${encodeURIComponent(jobId)}/content?index=0`;
      vid.style.display = "block";
      vid.addEventListener(
        "loadedmetadata",
        () => {
          const dur = vid.duration;
          if (vid.src && isFinite(dur) && dur > 0) {
            statusP.textContent = `Готово (${Math.round(dur)} с)`;
          }
        },
        { once: true },
      );
      vid.addEventListener(
        "error",
        () => {
          statusP.textContent = "Не удалось загрузить видео (ссылка могла устареть).";
        },
        { once: true },
      );
    } else {
      vid.style.display = "none";
    }

    const costRow = document.createElement("div");
    costRow.className = "cost";
    const raw = meta.costRub;
    if (raw != null && raw !== "") {
      const rub = Number(String(raw).replace(",", "."));
      if (!Number.isNaN(rub)) {
        costRow.textContent = `Списано: ${rub.toLocaleString("ru-RU", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 4,
        })} ₽`;
      }
    }

    bubble.appendChild(statusP);
    bubble.appendChild(debugPre);
    bubble.appendChild(vid);
    bubble.appendChild(costRow);
    wrap.appendChild(bubble);
    listEl.appendChild(wrap);
  }

  function renderChatFromHistory() {
    listEl.innerHTML = "";
    for (const msg of chatHistory) {
      if (msg.role === "user") {
        if (msg.meta && msg.meta.voiceMessage === true) {
          appendUserVoiceBubble({
            source: msg.meta.audioSource === "file" ? "file" : "mic",
            fileName: msg.meta.fileName || "",
            transcript: msg.meta.voiceTranscript || "",
          });
        } else if (msg.meta && msg.meta.videoRequest === true) {
          appendVideoUserBubbleFromSaved(msg);
        } else {
          const c = msg.content;
          if (typeof c === "string") appendUserBubble(c);
          else if (
            Array.isArray(c) &&
            c.some((x) => x?.type === "image_url" && x?.image_url?.url)
          )
            appendUserMessageDisplay(c);
          else appendUserBubble(userContentPreview(c));
        }
      } else if (msg.role === "assistant") {
        if (msg.meta && msg.meta.videoGeneration === true) {
          renderAssistantVideoFromSaved(msg);
        } else {
          renderAssistantFromSaved(msg);
        }
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
    if (chatHistory.length === 0) return;
    try {
      const messages = JSON.parse(
        JSON.stringify(sanitizeMessagesForPersist(chatHistory)),
      );
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
    if (chatHistory.length === 0) {
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
    if (streaming || transcribing || videoGenInProgress || !id) return;
    if (currentConversationId && currentConversationId !== id) {
      await abandonOrPersistCurrent();
    }
    const r = await api(`/api/v1/conversations/${encodeURIComponent(id)}`);
    if (!r.ok) return;
    const t = await r.json();
    currentConversationId = t.id;
    chatHistory.length = 0;
    for (const m of t.messages || []) {
      chatHistory.push(m);
    }
    if (t.modelSlug && chatModels.some((x) => x.slug === t.modelSlug)) {
      setSelectedSlug(t.modelSlug);
    }
    renderChatFromHistory();
    updateHints();
    await refreshSidebarList();
  }

  async function newChat() {
    if (streaming || transcribing || videoGenInProgress) return;
    await abandonOrPersistCurrent();
    currentConversationId = null;
    chatHistory.length = 0;
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
      chatHistory.length = 0;
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
      chatHistory.length = 0;
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
    if (!chatModels.some((x) => x.slug === slug)) return;
    if (slug === selectedSlug) return;
    if (videoGenInProgress) return;
    await abandonOrPersistCurrent();
    selectedSlug = slug;
    sessionStorage.setItem(MODEL_STORAGE_KEY, slug);
    currentConversationId = null;
    chatHistory.length = 0;
    listEl.innerHTML = "";
    errEl.style.display = "none";
    errEl.textContent = "";
    selectModelVisual(slug);
    updateHints();
    schedulePersistLastModel(slug);
    await refreshSidebarList();
  }

  let streaming = false;
  let transcribing = false;

  function setComposerDisabled(flag) {
    if (inputEl) inputEl.disabled = flag;
    const mod = selectedModel();
    const allowVoice = mod ? modelAllowsVoiceInput(mod) : false;
    const allowAttach = mod && mod.isFree !== true;
    const bm = document.getElementById("btn-mic");
    const at = document.getElementById("btn-attach");
    const sub = formEl?.querySelector('button[type="submit"]');
    if (bm) bm.disabled = flag || !allowVoice;
    if (at) at.disabled = flag || !allowAttach;
    if (sub) sub.disabled = flag;
  }

  function readStoredSttSlug() {
    try {
      const s = (localStorage.getItem(STT_STORAGE_KEY) || "").trim();
      if (s && sttModels.some((x) => x.slug === s)) return s;
    } catch {
      /* ignore */
    }
    return null;
  }

  function resolveTranscribeSlug() {
    const fromStore = readStoredSttSlug();
    if (fromStore) return fromStore;
    const m = selectedModel();
    if (m?.supportsTranscription === true) return m.slug;
    const w = sttModels.find((x) => x.slug === "openai/whisper-1");
    if (w) return w.slug;
    if (sttModels.length) return sttModels[0].slug;
    return "openai/whisper-1";
  }

  async function runTranscribeToChat(blob, filename, audioSource) {
    const src = audioSource === "file" ? "file" : "mic";
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    if (transcribing || streaming) return;
    if (!sttModels.length) {
      errEl.textContent = "Нет моделей распознавания в каталоге.";
      errEl.style.display = "block";
      return;
    }
    transcribing = true;
    setComposerDisabled(true);
    setSidebarBusy(true);
    errEl.textContent = "";
    errEl.style.display = "none";
    try {
      const fd = new FormData();
      fd.append("audio", blob, filename || "audio.bin");
      fd.append("sttModel", resolveTranscribeSlug());
      const htmlLang = (document.documentElement.lang || "ru").trim().toLowerCase();
      const lang = /^[a-z]{2}/.test(htmlLang) ? htmlLang.slice(0, 2) : "ru";
      fd.append("language", lang);
      const res = await api("/api/v1/transcribe", { method: "POST", body: fd });
      if (res.status === 401) {
        errEl.textContent =
          "Войдите в аккаунт — для выбранной модели распознавания нужны вход и баланс.";
        errEl.style.display = "block";
        return;
      }
      if (res.status === 402) {
        let msg = "Недостаточно средств на балансе.";
        try {
          const j = await res.json();
          const d = j.detail;
          if (d && typeof d === "object" && d.estimatedMinRub != null) {
            msg = `На балансе не хватает (ориентир ~${Number(
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
        return;
      }
      if (!res.ok) {
        try {
          const j = await res.json();
          const d = j.detail;
          errEl.textContent =
            typeof d === "string" ? d : "Не удалось распознать аудио.";
        } catch {
          errEl.textContent = "Ошибка распознавания.";
        }
        errEl.style.display = "block";
        return;
      }
      const data = await res.json();
      const text = typeof data.text === "string" ? data.text.trim() : "";
      let spendNote = "";
      if (mod.isFree !== true && typeof data.costRub === "string" && data.costRub) {
        const rub = Number(data.costRub.replace(",", "."));
        if (!Number.isNaN(rub)) {
          spendNote = `Расп. ${rub.toLocaleString("ru-RU", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} ₽`;
        }
      }
      if (!text) {
        errEl.textContent = "Пустой результат распознавания.";
        errEl.style.display = "block";
        return;
      }
      errEl.textContent = "";
      errEl.style.display = "none";
      await ensureConversation();
      const safeName =
        typeof filename === "string" && filename.trim() ? filename.trim() : "";
      const voiceMeta = {
        voiceMessage: true,
        audioSource: src,
        voiceTranscript: text,
        ...(safeName ? { fileName: safeName } : {}),
      };
      chatHistory.push({
        role: "user",
        content: [{ type: "text", text }],
        meta: voiceMeta,
      });
      appendUserVoiceBubble({
        source: src,
        fileName: safeName,
        transcript: text,
        spendNote:
          typeof spendNote === "string" && spendNote.trim() ? spendNote.trim() : "",
      });

      const me2 = await api("/api/auth/me").then((r) => r.json());
      if (!me2.guest && me2.balance != null) refreshBalance(me2.balance);

      if (isSttOnlyModel(mod)) {
        await persistThread();
        return;
      }

      try {
        errEl.textContent = "";
        errEl.style.display = "none";
        streaming = true;
        setSidebarBusy(true);
        const shell = appendAssistantShell();
        const res = await api("/api/v1/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            modelSlug: selectedModel().slug,
            messages: messagesForRequest(),
            stream: true,
            ttsVoice: readStoredTtsVoice(),
          }),
        });
        await handleMessagesResponse(res, shell, {});
      } catch (e) {
        console.error("[chat] transcribe→chat", e);
        errEl.textContent = "Не удалось получить ответ модели.";
        errEl.style.display = "block";
        await persistThread();
      } finally {
        streaming = false;
      }
    } catch (e) {
      console.error("[chat] transcribe", e);
      errEl.textContent = "Сеть или сервер недоступны.";
      errEl.style.display = "block";
    } finally {
      transcribing = false;
      setComposerDisabled(false);
      setSidebarBusy(false);
    }
  }

  async function handleMessagesResponse(res, shell, streamOpts) {
    const opts = streamOpts && typeof streamOpts === "object" ? streamOpts : {};
    const voiceInput = opts.voiceInput === true;
    const paidVoiceNote = opts.paidVoiceNote === true;
    const { md, cost, typing, audioOut, audioBlock } = shell;
    let acc = "";
    const imageUrls = [];
    const audioParts = [];
    function genImageAppendLine(url) {
      const u = escapeHtml(url);
      return `<p class="gen-img-wrap"><img src="${u}" alt="Сгенерированное изображение" class="gen-image" loading="lazy" decoding="async" /></p>`;
    }
    function renderAssistantHtml() {
      let html = renderMd(acc);
      for (const u of imageUrls) {
        if (typeof u !== "string" || !u) continue;
        /* Провайдер часто шлёт картинку в delta.images и дублирует ту же ссылку в markdown — не рисуем дважды */
        if (html.includes(u)) continue;
        html += genImageAppendLine(u);
      }
      return html;
    }

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
        const n = Number(rub);
        if (Number.isNaN(n)) return;
        const formatted = n.toLocaleString("ru-RU", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 4,
        });
        if (voiceInput && paidVoiceNote) {
          cost.textContent = `Списано: ${formatted} ₽ (голос + ответ)`;
        } else {
          cost.textContent = `Запрос: ${formatted} ₽`;
        }
      },
      (detail) => {
        const mod = selectedModel();
        const raw = typeof detail === "string" ? detail : "";
        console.warn("[chat] stream error (OpenRouter)", raw);
        const msg = humanizeOpenRouterStreamError(raw, {
          isFree: mod?.isFree === true,
          modelSlug: mod?.slug,
        });
        const tail =
          raw.trim() && msg !== raw.trim() && !msg.includes(raw.trim())
            ? `\n\nТекст провайдера: ${raw.length > 420 ? `${raw.slice(0, 417)}…` : raw}`
            : "";
        errEl.textContent = `${msg}${tail}`;
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
      undefined,
      (aud) => {
        if (aud?.data) audioParts.push(aud.data);
      },
    );

    if (audioOut && audioParts.length > 0) {
      try {
        const b64 = audioParts.join("");
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const prevUrl = audioOut.dataset.blobUrl;
        if (prevUrl) {
          try {
            URL.revokeObjectURL(prevUrl);
          } catch {
            /* ignore */
          }
        }
        const slug = String(selectedModel()?.slug || "");
        if (bytes.length === 0) {
          console.warn("[chat] assistant audio: пустые данные");
        } else {
          const blobA = assistantResponseBytesToAudioBlob(bytes, {
            modelSlug: slug,
          });
          const url = URL.createObjectURL(blobA);
          audioOut.dataset.blobUrl = url;
          audioOut.src = url;
          audioOut.preload = "auto";
          try {
            audioOut.load();
          } catch {
            /* ignore */
          }
          if (audioBlock) {
            audioBlock.style.display = "block";
            const lab = audioBlock.querySelector(".assistant-audio-label");
            if (lab && slug.toLowerCase().includes("lyria")) {
              lab.textContent = "Сгенерированное аудио";
            }
          }
        }
      } catch (e) {
        console.warn("[chat] assistant audio decode", e);
      }
    }

    const asst = { role: "assistant", content: acc };
    if (imageUrls.length) asst.imageUrls = imageUrls;
    chatHistory.push(asst);
    typing.remove();

    const me2 = await api("/api/auth/me").then((r) => r.json());
    if (!me2.guest && me2.balance != null) refreshBalance(me2.balance);
    await persistThread();
  }

  async function sendVoiceMessage(blob, filename, audioSource = "mic") {
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    if (isSttOnlyModel(mod)) {
      await runTranscribeToChat(blob, filename || "voice.webm", audioSource);
      return;
    }
    if (mod.supportsSpeech) {
      if (mod.isFree !== true && isGuest) {
        errEl.textContent =
          "Платные модели доступны после входа. Нажмите «Войти» в шапке.";
        errEl.style.display = "block";
        return;
      }
      try {
        errEl.textContent = "";
        errEl.style.display = "none";
        streaming = true;
        setSidebarBusy(true);
        const priorMessages = messagesForRequest();
        await ensureConversation();
        const srcSpeech = audioSource === "file" ? "file" : "mic";
        const fnSpeech =
          typeof filename === "string" && filename.trim() ? filename.trim() : "";
        const userLineSpeech =
          srcSpeech === "file"
            ? `📎 ${fnSpeech || "файл"}`
            : "🎤 голос";
        chatHistory.push({
          role: "user",
          content: [{ type: "text", text: userLineSpeech }],
          meta: {
            voiceMessage: true,
            audioSource: srcSpeech,
            ...(fnSpeech ? { fileName: fnSpeech } : {}),
          },
        });
        appendUserVoiceBubble({
          source: srcSpeech,
          fileName: fnSpeech,
        });
        const fd = new FormData();
        fd.append("audio", blob, filename || "voice.webm");
        fd.append("modelSlug", mod.slug);
        fd.append("stream", "true");
        fd.append("messages", JSON.stringify(priorMessages));
        fd.append("voiceHint", "Ответь на голосовое сообщение.");
        fd.append("ttsVoice", readStoredTtsVoice());
        const shell = appendAssistantShell();
        const res = await api("/api/v1/messages", { method: "POST", body: fd });
        await handleMessagesResponse(res, shell, {
          voiceInput: true,
          paidVoiceNote: mod.isFree !== true,
        });
      } catch (e) {
        console.error("[chat] sendVoiceMessage", e);
        errEl.textContent = "Ошибка отправки голоса.";
        errEl.style.display = "block";
        await persistThread();
      } finally {
        streaming = false;
        setSidebarBusy(false);
      }
      return;
    }
    await runTranscribeToChat(blob, filename || "voice.webm", audioSource);
  }

  async function runChat() {
    const m0 = selectedModel();
    if (!m0 || m0.supportsVideoGeneration === true) {
      console.warn("[chat] runChat отменён: для видеомодели чат отключён");
      return;
    }
    if (isSttOnlyModel(m0)) {
      errEl.textContent =
        "Выбрана модель только для распознавания речи. Переключитесь на модель с диалогом (вкладка «Текст и чат» и др.), чтобы отправить сообщение.";
      errEl.style.display = "block";
      return;
    }
    try {
      errEl.textContent = "";
      errEl.style.display = "none";
      streaming = true;
      setSidebarBusy(true);
      const shell = appendAssistantShell();
      const res = await api("/api/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          modelSlug: selectedModel().slug,
          messages: messagesForRequest(),
          stream: true,
          ttsVoice: readStoredTtsVoice(),
        }),
      });
      await handleMessagesResponse(res, shell, {});
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
      void newChat().finally(() => setChatSidebarOpen(false));
    });
  }
  if (threadListEl) {
    threadListEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".sidebar-thread-item");
      if (!btn || !threadListEl.contains(btn)) return;
      const id = btn.dataset.threadId;
      if (id && id !== currentConversationId) {
        void openThread(id).finally(() => setChatSidebarOpen(false));
      } else if (id) {
        setChatSidebarOpen(false);
      }
    });
  }

  if (btnVideoEl && videoPromptEl && listEl) {
    function appendVideoUserPromptLine(prompt, modelSlug) {
      const name = modelDisplayName(modelSlug);
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      wrap.innerHTML = `<div class="bubble"><p style="margin:0;white-space:pre-wrap">${escapeHtml(
        `🎬 Видео (${name})\n${prompt}`,
      )}</p></div>`;
      listEl.appendChild(wrap);
      listEl.scrollTop = listEl.scrollHeight;
    }

    function appendVideoAssistantJobUi() {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const statusP = document.createElement("p");
      statusP.className = "hint assistant-video-status";
      statusP.style.margin = "0 0 0.5rem";
      const debugPre = document.createElement("pre");
      debugPre.className = "video-debug video-debug--inline";
      debugPre.style.display = "none";
      debugPre.setAttribute("aria-live", "polite");
      const vid = document.createElement("video");
      vid.className = "assistant-video";
      vid.controls = true;
      vid.setAttribute("playsinline", "");
      vid.preload = "metadata";
      vid.style.display = "none";
      const costRow = document.createElement("div");
      costRow.className = "cost";
      bubble.appendChild(statusP);
      bubble.appendChild(debugPre);
      bubble.appendChild(vid);
      bubble.appendChild(costRow);
      wrap.appendChild(bubble);
      listEl.appendChild(wrap);
      listEl.scrollTop = listEl.scrollHeight;
      return {
        wrap,
        statusEl: statusP,
        videoEl: vid,
        debugEl: debugPre,
        costEl: costRow,
      };
    }

    function applyVideoCostLine(ui, st) {
      if (!ui?.costEl || !st || typeof st !== "object") return;
      const raw = st.costRub;
      if (raw == null || raw === "") return;
      const rub = Number(String(raw).replace(",", "."));
      if (Number.isNaN(rub)) return;
      ui.costEl.textContent = `Списано: ${rub.toLocaleString("ru-RU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      })} ₽`;
      listEl.scrollTop = listEl.scrollHeight;
    }

    function summarizeVideoTerminalFailure(st) {
      if (!st || typeof st !== "object")
        return "Генерация видео завершилась ошибкой.";
      const s = String(st.status || "").toLowerCase();
      if (s === "cancelled") return "Генерация видео отменена.";
      if (s === "expired") return "Время ожидания видео истекло.";
      const err =
        typeof st.error === "string"
          ? st.error.trim()
          : st.error != null
            ? String(st.error).trim()
            : "";
      const low = err.toLowerCase();
      if (
        low.includes("copyright") ||
        low.includes("copyright restrictions") ||
        (low.includes("restrictions") && low.includes("output video"))
      ) {
        return "Видео не создано: отказ провайдера (в т.ч. из‑за политики авторских прав). Такое бывает и с нейтральным промптом — смените формулировку сцены или видео-модель.";
      }
      if (err) {
        const short = err.length > 260 ? `${err.slice(0, 257)}…` : err;
        return `Генерация не удалась: ${short}`;
      }
      return "Генерация видео завершилась ошибкой.";
    }

    function applyVideoTerminalFailureUi(ui, st) {
      const summary = summarizeVideoTerminalFailure(st);
      ui.statusEl.textContent = summary;
      const raw =
        typeof st?.error === "string"
          ? st.error.trim()
          : st?.error != null && typeof st.error !== "object"
            ? String(st.error).trim()
            : "";
      if (raw) {
        ui.statusEl.setAttribute("title", raw);
        ui.debugEl.style.display = "block";
        ui.debugEl.textContent =
          raw.length > 1400 ? `${raw.slice(0, 1397)}…` : raw;
      } else {
        ui.statusEl.removeAttribute("title");
        try {
          const fallback = JSON.stringify(st, null, 2);
          if (fallback && fallback !== "{}") {
            ui.debugEl.style.display = "block";
            ui.debugEl.textContent =
              fallback.length > 1400 ? `${fallback.slice(0, 1397)}…` : fallback;
          } else {
            ui.debugEl.style.display = "none";
            ui.debugEl.textContent = "";
          }
        } catch {
          ui.debugEl.style.display = "none";
          ui.debugEl.textContent = "";
        }
      }
      listEl.scrollTop = listEl.scrollHeight;
    }

  async function runVideoGeneration() {
      if (videoGenInProgress) return;
      const prompt = videoPromptEl.value.trim();
      if (!prompt) return;
      const mod = selectedModel();
      if (!mod?.slug) {
        errEl.textContent = "Модель не выбрана.";
        errEl.style.display = "block";
        return;
      }
      if (mod.supportsVideoGeneration !== true) {
        errEl.textContent =
          "Выберите модель генерации видео (вкладка «Видео» в списке моделей).";
        errEl.style.display = "block";
        return;
      }
      if (isGuest) {
        errEl.textContent =
          "Войдите в аккаунт с пополненным балансом, чтобы создавать видео.";
        errEl.style.display = "block";
        return;
      }

      await ensureConversation();
      if (!currentConversationId) {
        errEl.textContent = "Не удалось сохранить диалог.";
        errEl.style.display = "block";
        return;
      }

      const userLine = `🎬 Видео (${modelDisplayName(mod.slug)})\n${prompt}`;
      chatHistory.push({
        role: "user",
        content: userLine,
        meta: {
          videoRequest: true,
          modelSlug: mod.slug,
          prompt,
        },
      });
      await persistThread();

      videoGenInProgress = true;
      btnVideoEl.disabled = true;
      videoPromptEl.value = "";
      videoPromptEl.disabled = true;
      setSidebarBusy(true);
      errEl.style.display = "none";
      errEl.textContent = "";

      appendVideoUserPromptLine(prompt, mod.slug);
      const ui = appendVideoAssistantJobUi();

      const errVideoMeta = {
        videoGeneration: true,
        videoError: true,
        modelSlug: mod.slug,
      };

      function releaseVideoJob() {
        videoGenInProgress = false;
        btnVideoEl.disabled = false;
        videoPromptEl.disabled = false;
        setSidebarBusy(false);
      }

      async function pushVideoAssistantRow(contentStr, metaObj) {
        chatHistory.push({
          role: "assistant",
          content: contentStr,
          meta: metaObj,
        });
        await persistThread();
      }

      function videoClear() {
        ui.statusEl.removeAttribute("title");
        ui.debugEl.style.display = "none";
        ui.debugEl.textContent = "";
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
        ui.statusEl.textContent = "Ошибка";
        ui.statusEl.setAttribute("title", line);
        ui.debugEl.style.display = "block";
        ui.debugEl.textContent = line;
        listEl.scrollTop = listEl.scrollHeight;
      }

      ui.videoEl.addEventListener(
        "loadedmetadata",
        () => {
          const dur = ui.videoEl.duration;
          if (ui.videoEl.src && isFinite(dur) && dur > 0) {
            ui.statusEl.textContent = `Готово (${Math.round(dur)} с)`;
            videoClear();
          } else {
            ui.statusEl.textContent = "Готово";
            videoClear();
          }
          listEl.scrollTop = listEl.scrollHeight;
          releaseVideoJob();
        },
        { once: true },
      );

      ui.videoEl.addEventListener(
        "error",
        () => {
          const src = ui.videoEl.currentSrc || ui.videoEl.src || "";
          const me = ui.videoEl.error;
          videoFail("video-element", {
            src,
            mediaErrorCode: me?.code,
            mediaErrorMessage: me?.message,
          });
          releaseVideoJob();
        },
        { once: true },
      );

      try {
        ui.statusEl.textContent = "Запрос отправлен. Ожидайте…";
        listEl.scrollTop = listEl.scrollHeight;
        ui.videoEl.style.display = "none";
        ui.videoEl.removeAttribute("src");

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
          await pushVideoAssistantRow(
            "Не удалось обработать ответ сервера.",
            errVideoMeta,
          );
          releaseVideoJob();
          return;
        }
        if (r.status === 402) {
          ui.statusEl.textContent = "Недостаточно средств.";
          await pushVideoAssistantRow(
            "Недостаточно средств на балансе.",
            errVideoMeta,
          );
          releaseVideoJob();
          return;
        }
        if (!r.ok) {
          if (r.status === 401) {
            ui.statusEl.textContent = "Войдите, чтобы создавать платное видео.";
            await pushVideoAssistantRow(
              "Нужен вход в аккаунт для платной генерации видео.",
              errVideoMeta,
            );
            releaseVideoJob();
            return;
          }
          videoFail("POST HTTP error", { status: r.status, body: j });
          await pushVideoAssistantRow(
            "Не удалось создать задачу видео.",
            errVideoMeta,
          );
          releaseVideoJob();
          return;
        }
        if (j.error && !j.id) {
          videoFail("POST 2xx but error in body", j);
          await pushVideoAssistantRow(
            "Сервер отклонил запрос на видео.",
            errVideoMeta,
          );
          releaseVideoJob();
          return;
        }
        const jobId = j.id;
        if (!jobId) {
          videoFail("missing job id", j);
          await pushVideoAssistantRow(
            "Не получен идентификатор задачи видео.",
            errVideoMeta,
          );
          releaseVideoJob();
          return;
        }
        try {
          console.info("[video] job created, polling", String(jobId));
        } catch {
          /* ignore */
        }
        ui.statusEl.textContent = "Запрос принят, создаём видео…";
        listEl.scrollTop = listEl.scrollHeight;

        const terminalBad = new Set(["failed", "cancelled", "expired"]);
        const VIDEO_STEP_RU = {
          queued: "в очереди",
          pending: "ожидание",
          processing: "генерация",
          running: "генерация",
          started: "генерация",
          in_progress: "генерация",
          completed: "готово",
          failed: "ошибка",
          cancelled: "отменено",
          expired: "истекло",
        };
        const poll = async () => {
          try {
            const pr = await api(
              `/api/v1/video/jobs/${encodeURIComponent(jobId)}`,
            );
            let st;
            try {
              st = await pr.json();
            } catch (e) {
              videoFail("poll JSON parse", { status: pr.status, err: String(e) });
              await pushVideoAssistantRow(
                "Не удалось проверить статус видео.",
                errVideoMeta,
              );
              releaseVideoJob();
              return;
            }
            if (!pr.ok) {
              videoFail("poll HTTP error", { status: pr.status, body: st });
              await pushVideoAssistantRow(
                "Ошибка при проверке статуса видео.",
                errVideoMeta,
              );
              releaseVideoJob();
              return;
            }
            const status = st.status;
            const step =
              VIDEO_STEP_RU[String(status || "").toLowerCase()] || status || "…";
            ui.statusEl.textContent = `Этап: ${step}`;
            listEl.scrollTop = listEl.scrollHeight;
            if (terminalBad.has(status)) {
              applyVideoTerminalFailureUi(ui, st);
              const summary = summarizeVideoTerminalFailure(st);
              await pushVideoAssistantRow(summary, errVideoMeta);
              releaseVideoJob();
              return;
            }
            if (status === "completed") {
              applyVideoCostLine(ui, st);
              const hasFile =
                Array.isArray(st.unsigned_urls) && st.unsigned_urls.length > 0;
              const okMeta = {
                videoGeneration: true,
                jobId: String(jobId),
                modelSlug: mod.slug,
              };
              if (st.costRub != null && String(st.costRub).trim() !== "")
                okMeta.costRub = String(st.costRub);
              if (hasFile) {
                await pushVideoAssistantRow("Видео готово.", okMeta);
                const streamUrl = `/api/v1/video/jobs/${encodeURIComponent(jobId)}/content?index=0`;
                ui.videoEl.src = streamUrl;
                ui.videoEl.style.display = "block";
                ui.videoEl.load();
              } else {
                videoFail("completed without unsigned_urls", st);
                await pushVideoAssistantRow(
                  "Видео в ответе не найдено.",
                  errVideoMeta,
                );
                releaseVideoJob();
              }
              const me3 = await api("/api/auth/me").then((res) => res.json());
              if (!me3.guest && me3.balance != null) refreshBalance(me3.balance);
              listEl.scrollTop = listEl.scrollHeight;
              return;
            }
            setTimeout(poll, 5000);
          } catch (e) {
            videoFail("poll uncaught", e);
            await pushVideoAssistantRow(
              "Сбой при опросе статуса видео.",
              errVideoMeta,
            );
            releaseVideoJob();
          }
        };
        setTimeout(poll, 4000);
      } catch (e) {
        videoFail("handler uncaught", e);
        await pushVideoAssistantRow("Сбой при генерации видео.", errVideoMeta);
        releaseVideoJob();
      }
    }

    btnVideoEl.addEventListener("click", () => {
      void runVideoGeneration();
    });
    videoPromptEl.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      e.preventDefault();
      void runVideoGeneration();
    });
  }

  const btnMic = document.getElementById("btn-mic");
  let mediaRecorder = null;
  let recStream = null;
  let recChunks = [];
  let micUpListener = null;

  function removeMicUpListener() {
    if (micUpListener) {
      window.removeEventListener("pointerup", micUpListener, true);
      micUpListener = null;
    }
  }

  if (btnMic) {
    btnMic.addEventListener("pointerdown", async (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      if (streaming || transcribing || videoGenInProgress) return;
      const mod = selectedModel();
      if (!mod || mod.supportsVideoGeneration === true) return;
      if (mediaRecorder && mediaRecorder.state === "recording") return;
      if (!modelAllowsVoiceInput(mod)) {
        errEl.textContent =
          mod.isFree === true
            ? "Голос с микрофона доступен только на платных моделях — переключитесь с вкладки «Бесплатные»."
            : "Голос для этой модели недоступен (например, режим видео).";
        errEl.style.display = "block";
        return;
      }
      errEl.textContent = "";
      errEl.style.display = "none";
      try {
        recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recChunks = [];
        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm";
        mediaRecorder = new MediaRecorder(recStream, { mimeType: mime });
        mediaRecorder.ondataavailable = (ev) => {
          if (ev.data.size > 0) recChunks.push(ev.data);
        };
        mediaRecorder.onstop = async () => {
          removeMicUpListener();
          const mimeType = mediaRecorder?.mimeType || "audio/webm";
          recStream?.getTracks().forEach((t) => t.stop());
          recStream = null;
          btnMic.classList.remove("btn-mic--recording");
          btnMic.setAttribute("aria-pressed", "false");
          const blob = new Blob(recChunks, { type: mimeType });
          recChunks = [];
          mediaRecorder = null;
          if (blob.size < 32) return;
          await sendVoiceMessage(blob, "voice.webm", "mic");
        };
        mediaRecorder.start();
        btnMic.classList.add("btn-mic--recording");
        btnMic.setAttribute("aria-pressed", "true");
        try {
          btnMic.setPointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
        micUpListener = () => {
          if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
          }
        };
        window.addEventListener("pointerup", micUpListener, true);
      } catch {
        removeMicUpListener();
        errEl.textContent = "Не удалось получить доступ к микрофону.";
        errEl.style.display = "block";
        recStream?.getTracks().forEach((t) => t.stop());
        recStream = null;
        mediaRecorder = null;
      }
    });
  }

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (streaming || transcribing || videoGenInProgress) return;
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    if (isSttOnlyModel(mod)) {
      errEl.textContent =
        "Выбрана модель только для распознавания речи. Переключитесь на модель с диалогом, чтобы отправить сообщение.";
      errEl.style.display = "block";
      return;
    }
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
    chatHistory.push({ role: "user", content: text });
    appendUserBubble(text);
    await runChat();
  });

  fileEl.addEventListener("change", async () => {
    const f = fileEl.files?.[0];
    fileEl.value = "";
    if (!f) return;
    if (streaming || transcribing || videoGenInProgress) return;
    const mod = selectedModel();
    if (!mod || mod.supportsVideoGeneration === true) return;
    if (mod.isFree === true) {
      errEl.textContent =
        "Вложения (изображения и аудио) доступны только на платных моделях — переключитесь с вкладки «Бесплатные».";
      errEl.style.display = "block";
      return;
    }
    if (fileLooksLikeUserAudio(f)) {
      if (!modelAllowsVoiceInput(mod)) {
        errEl.textContent =
          mod.isFree === true
            ? "Аудиофайл можно отправить только на платной модели (не вкладка «Бесплатные»)."
            : "Аудио для этой модели недоступно. Выберите модель с речью или вкладку «Транскрипция».";
        errEl.style.display = "block";
        return;
      }
      if (streaming || transcribing || videoGenInProgress) return;
      if (mod.isFree !== true && isGuest) {
        errEl.textContent =
          "Платные модели доступны после входа. Нажмите «Войти» в шапке.";
        errEl.style.display = "block";
        return;
      }
      await sendVoiceMessage(f, f.name, "file");
      return;
    }
    if (!f.type.startsWith("image/")) {
      errEl.textContent = "Вложение: изображение или аудиофайл.";
      errEl.style.display = "block";
      return;
    }
    if (!mod.supportsVision) {
      errEl.textContent = "Эта модель не анализирует изображения. Выберите модель с поддержкой vision.";
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
    chatHistory.push({ role: "user", content: userContent });
    appendUserMessageDisplay(userContent);
    await runChat();
  });
}

main().catch((e) => {
  console.error("[chat] main failed", e);
  const mp = document.getElementById("model-picker");
  if (mp) {
    mp.innerHTML =
      '<p class="model-picker-empty">Не удалось загрузить интерфейс чата. Обновите страницу. Подробности — в консоли (F12).</p>';
  }
});
