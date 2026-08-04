import { consumeStream } from "./sse.js?v=27";
import { persistIdentityLinksFromMe } from "./identity-links.js?v=4";

const MODEL_STORAGE_KEY = "ai_proxy_model_slug";
/** Выставляется на /tariffs при выборе модели — чат не подменяет её моделью из старого диалога. */
const MODEL_EXPLICIT_CHOICE_KEY = "ai_proxy_explicit_model";
const STT_STORAGE_KEY = "ai_proxy_stt_slug";
/** Гость: диалог в sessionStorage — не пропадает при переходе на «Тарифы» и обратно. */
const GUEST_CHAT_SESSION_KEY = "ai_proxy_guest_chat_v1";
const GUEST_CHAT_SESSION_MAX_BYTES = 450000;
const TTS_VOICE_STORAGE_KEY = "ai_proxy_tts_voice";
const ALLOWED_TTS_VOICE_IDS = [
  "alloy",
  "echo",
  "fable",
  "onyx",
  "nova",
  "shimmer",
];

const CHAT_SIDEBAR_WIDTH_LS_KEY = "ai_proxy_chat_sidebar_px";
/** Совпадает со старым именем cookie — удаляем cookie после переноса в localStorage. */
const CHAT_SIDEBAR_WIDTH_COOKIE_LEGACY = "ai_proxy_chat_sidebar_px";

function migrateSidebarWidthCookieToLsOnce() {
  try {
    if (typeof localStorage === "undefined" || typeof document === "undefined") return;
    if (localStorage.getItem(CHAT_SIDEBAR_WIDTH_LS_KEY) != null) return;
    const esc = CHAT_SIDEBAR_WIDTH_COOKIE_LEGACY.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = document.cookie.match(new RegExp("(?:^|; )" + esc + "=([^;]*)"));
    if (!m) return;
    const n = parseInt(decodeURIComponent(m[1]), 10);
    if (!Number.isFinite(n)) return;
    localStorage.setItem(CHAT_SIDEBAR_WIDTH_LS_KEY, String(Math.round(n)));
    document.cookie = CHAT_SIDEBAR_WIDTH_COOKIE_LEGACY + "=;path=/;max-age=0";
  } catch {
    /* ignore */
  }
}

function readChatSidebarWidthStored() {
  migrateSidebarWidthCookieToLsOnce();
  try {
    const raw = (localStorage.getItem(CHAT_SIDEBAR_WIDTH_LS_KEY) || "").trim();
    if (raw === "") return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function writeChatSidebarWidthStored(px) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(CHAT_SIDEBAR_WIDTH_LS_KEY, String(Math.round(px)));
  } catch {
    /* приватный режим / quota */
  }
}

const CHAT_SIDEBAR_MIN_PX = 200;
const CHAT_SIDEBAR_MAX_PX = 560;

function clampChatSidebarWidthPx(px, innerWidth) {
  const iw =
    typeof innerWidth === "number" && Number.isFinite(innerWidth)
      ? innerWidth
      : typeof window !== "undefined"
        ? window.innerWidth
        : 1024;
  const cap = Math.min(CHAT_SIDEBAR_MAX_PX, Math.floor(iw * 0.44));
  const floor = CHAT_SIDEBAR_MIN_PX;
  const top = Math.max(floor, cap);
  const x = Math.round(px);
  if (x < floor) return floor;
  if (x > top) return top;
  return x;
}

/** Возвращает применённую ширину (px). */
function applyChatSidebarWidthPx(px, innerWidth) {
  const w = clampChatSidebarWidthPx(px, innerWidth);
  document.documentElement.style.setProperty("--chat-sidebar-width", `${w}px`);
  return w;
}

function defaultChatSidebarWidthPx() {
  const fz = parseFloat(
    typeof window !== "undefined" ? getComputedStyle(document.documentElement).fontSize || "16" : "16",
  );
  return Math.round((Number.isFinite(fz) ? fz : 16) * 17);
}

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

/** Значение из GET /api/config (YOOKASSA_SHOP_ID). Секрет API (YOOKASSA_SECRET_KEY) только на сервере — в форму и в страницу не попадает. */
let yookassaShopIdFromConfig = "";

function applyYookassaShopIdToForm() {
  const form = document.getElementById("yookassa-simple-form");
  if (!form || !yookassaShopIdFromConfig) return;
  const inp = form.querySelector('[name="shopId"]');
  if (inp) inp.value = yookassaShopIdFromConfig;
}

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
  applyYookassaShopIdToForm();
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
 * — Сиротский PCM16: OpenAI TTS ~24kHz mono (см. OpenRouter / GPT Audio).
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

function formatRubShort(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return "";
  return n.toLocaleString("ru-RU", { maximumFractionDigits: 4 });
}

/** Одна строка для карточки в пикере (как на странице тарифов, короче). */
function modelCardPricingLine(m) {
  if (!m || typeof m !== "object") return "";
  if (m.isFree === true) return "0 ₽ · лимиты сервиса";

  if (m.supportsVideoGeneration === true) {
    const rub = Number(m.inputPricePerMn);
    if (rub > 0) return `ориентир ~${formatRubShort(rub)} ₽/с`;
    return "₽/с по готовому ролику — см. тарифы";
  }

  if (isSttOnlyModel(m)) {
    const rub = Number(m.inputPricePerMn);
    if (rub > 0) return `ориентир ~${formatRubShort(rub)} ₽/мин аудио`;
    return "распознавание · тариф провайдера";
  }

  const ins = formatRubShort(m.inputPricePerMn);
  const outs = formatRubShort(m.outputPricePerMn);
  const fixedN = Number(m.fixedPrice);
  const fixed = Number.isFinite(fixedN) && fixedN > 0 ? formatRubShort(fixedN) : "";

  if (ins || outs) {
    return `вх ${ins || "—"} · вых ${outs || "—"} ₽/1M`;
  }
  return "стоимость по факту usage";
}

/** Аудиофайл: не режим видео (кнопка скрепки скрыта на бесплатных — до проверки не доходит). */
function modelAllowsVoiceAttachment(m) {
  if (!m || m.supportsVideoGeneration === true) return false;
  return true;
}

/** Кнопка микрофона (запись): только платные не-видео модели. */
function modelAllowsMicrophone(m) {
  if (!m || m.supportsVideoGeneration === true) return false;
  if (m.isFree === true) return false;
  return true;
}

/** Озвучка ответа в чате (GPT Audio на OpenRouter): не «голос на входе — текст». */
function modelProducesSpokenChatAudio(m) {
  if (!m || typeof m !== "object") return false;
  const s = String(m.slug || "").toLowerCase();
  return m.supportsSpeech === true && s.includes("gpt-audio");
}

/** Сеть/провайдер; без внутренних деталей */
const MSG_CHAT_NO_UPSTREAM =
  "Сейчас нет связи с провайдером модели. Повторите запрос позже. Если ошибка повторяется, напишите в поддержку — раздел «Контакты».";
const HINT_SUPPORT_IF_REPEATS =
  " Если ошибка повторяется, напишите в поддержку — раздел «Контакты».";
/** Ответ/отказ пришёл от внешнего провайдера модели — общее пояснение для пользователя */
const MSG_CHAT_PROVIDER_CONTACTS =
  "Источник ответа — провайдер модели, не сбой II Proxy. Если нужно разобрать ситуацию — раздел «Контакты».";

/**
 * Понятные подсказки к ответам модели в потоке.
 * :free в slug — бесплатный маршрут OpenRouter (очередь/лимиты); is_free без :free в slug — только флаг в БД.
 */
function humanizeOpenRouterStreamError(detail, opts = {}) {
  const isFree = opts.isFree === true;
  const slug = String(opts.modelSlug || "");
  const useFreeQueueStory =
    isFree &&
    slug.includes(":free");

  const m = String(detail || "").trim();
  const low = m.toLowerCase();

  // Устаревшие серверные тексты — без упоминания внутренней инфраструктуры
  if (
    m.includes("Нет соединения с OpenRouter") ||
    m.includes("OPENROUTER_HTTPX_TRUST_ENV") ||
    m.includes("попробуйте в .env") ||
    low.includes("upstream unavailable")
  ) {
    return MSG_CHAT_NO_UPSTREAM;
  }

  if (
    low.includes("нет устойчивой связи с провайдером моделей") &&
    low.includes("контакты")
  ) {
    return m.trim();
  }

  if (
    low.includes("user location is not supported") ||
    low.includes("location is not supported for the api")
  ) {
    return (
      "Google отклоняет запрос: для вашего региона (или IP) доступ к этому API запрещён (часть моделей Google через OpenRouter). " +
      "Это ограничение провайдера Google, не II Proxy. Попробуйте сеть в поддерживаемом регионе или другую модель (например GPT Audio — озвучка текста). " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }

  if (
    low.includes("unavailable for free") ||
    (low.includes("paid version is available") && low.includes("slug instead"))
  ) {
    return (
      "Бесплатный маршрут этой модели у провайдера отключён. Выберите другую бесплатную модель или платную на странице «Модели и тарифы». " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }

  const providerBlob = () => {
    if (useFreeQueueStory) {
      return (
        "Бесплатный канал: общая очередь или временная недоступность этой модели у провайдера. " +
        "Подождите минуту и повторите или выберите другую бесплатную модель из списка (или платную). " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    if (isFree) {
      return (
        "Провайдер не вернул ответ (часто так бывает с новыми или редкими моделями). " +
        "Повторите запрос, смените модель или выберите другую бесплатную из списка. " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    return (
      "Провайдер модели вернул ошибку (лимит, перегрузка или отказ канала). Попробуйте другую модель или повторите запрос позже. " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  };

  if (!m) {
    return useFreeQueueStory
      ? "Не удалось получить ответ по бесплатному канале. Повторите позже или выберите платную модель. " +
          MSG_CHAT_PROVIDER_CONTACTS
      : isFree
        ? "Не удалось получить ответ. Повторите запрос или выберите другую модель. " +
          MSG_CHAT_PROVIDER_CONTACTS
        : "Не удалось получить ответ модели. " + MSG_CHAT_PROVIDER_CONTACTS;
  }

  if (low.includes("provider returned error")) {
    return providerBlob();
  }
  if (
    low.includes("finish_reason=error") ||
    low.includes("ошибка провайдера") ||
    (low.includes("provider") && low.includes("error"))
  ) {
    return providerBlob();
  }
  if (low.includes("no endpoints found")) {
    if (useFreeQueueStory) {
      return (
        "Для этой бесплатной модели сейчас нет свободных серверов. Выберите другую бесплатную модель из списка или платную. " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    if (isFree) {
      return (
        "Для этой модели сейчас нет доступных серверов у провайдера. Выберите другую модель в списке. " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    return (
      "Для выбранной модели сейчас нет доступных серверов. Выберите другую модель в списке. " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }
  if (low.includes("rate limit") || low.includes("too many requests")) {
    if (useFreeQueueStory) {
      return (
        "Лимит запросов на бесплатном канале. Подождите или перейдите на платную модель. " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    if (isFree) {
      return (
        "Слишком много запросов для этого маршрута. Подождите или смените модель. " +
        MSG_CHAT_PROVIDER_CONTACTS
      );
    }
    return (
      "Слишком много запросов. Подождите немного или смените модель. " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }
  if (
    low.includes("requires more credits") ||
    low.includes("fewer max_tokens") ||
    low.includes("openrouter.ai/settings") ||
    low.includes("can only afford")
  ) {
    return (
      "Провайдер (OpenRouter) отклонил запрос из‑за резерва токенов или лимита аккаунта у них — это не баланс II Proxy. " +
      "Обычно помогает повтор запроса после нашего обновления или выбор другой модели. " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }
  if (low.includes("context length") || low.includes("maximum context")) {
    return (
      "Превышен допустимый размер контекста. Начните новый чат или сократите историю. " +
      MSG_CHAT_PROVIDER_CONTACTS
    );
  }
  return (
    "Провайдер модели вернул техническое сообщение об ошибке (подробности в консоли браузера, F12). " +
    MSG_CHAT_PROVIDER_CONTACTS
  );
}

/**
 * Разделитель «история ↔ чат»: перетаскивание, клавиши, ширина в localStorage на клиенте.
 */
function bindChatSidebarResizer(sidebarEl, resizerEl, isDrawerMode) {
  if (!sidebarEl || !resizerEl || typeof isDrawerMode !== "function") return;

  applyChatSidebarWidthPx(readChatSidebarWidthStored() ?? defaultChatSidebarWidthPx());

  let dragging = false;
  let startX = 0;
  let startW = 0;
  let activePid = /** @type {number | null} */ (null);

  function endDrag() {
    if (!dragging) return;
    dragging = false;
    resizerEl.classList.remove("is-dragging");
    if (activePid != null) {
      try {
        resizerEl.releasePointerCapture(activePid);
      } catch {
        /* ignore */
      }
      activePid = null;
    }
    writeChatSidebarWidthStored(sidebarEl.getBoundingClientRect().width);
  }

  resizerEl.addEventListener("pointerdown", (e) => {
    if (isDrawerMode()) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startW = sidebarEl.getBoundingClientRect().width;
    activePid = e.pointerId;
    resizerEl.classList.add("is-dragging");
    try {
      resizerEl.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  });

  resizerEl.addEventListener("pointermove", (e) => {
    if (!dragging || isDrawerMode()) return;
    applyChatSidebarWidthPx(startW + (e.clientX - startX));
  });

  resizerEl.addEventListener("pointerup", endDrag);
  resizerEl.addEventListener("pointercancel", endDrag);

  resizerEl.addEventListener("keydown", (e) => {
    if (isDrawerMode()) return;
    const cur = sidebarEl.getBoundingClientRect().width;
    let tgt = cur;
    if (e.key === "ArrowLeft") tgt = cur - 12;
    else if (e.key === "ArrowRight") tgt = cur + 12;
    else if (e.key === "Home") tgt = CHAT_SIDEBAR_MIN_PX;
    else if (e.key === "End")
      tgt = clampChatSidebarWidthPx(CHAT_SIDEBAR_MAX_PX, window.innerWidth);
    else return;
    e.preventDefault();
    const w = applyChatSidebarWidthPx(tgt);
    writeChatSidebarWidthStored(w);
  });

  let rzT = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(rzT);
    rzT = window.setTimeout(() => {
      if (isDrawerMode()) return;
      const raw = sidebarEl.getBoundingClientRect().width;
      const w = applyChatSidebarWidthPx(raw);
      if (Math.abs(w - raw) > 1.5) writeChatSidebarWidthStored(w);
    }, 120);
  });
}

async function main() {
  const [me, cfgRaw, modelsRaw] = await Promise.all([
    loadMe(),
    api("/api/config").then((r) => r.json().catch(() => ({}))),
    api("/api/models").then((r) => r.json().catch(() => null)),
  ]);
  persistIdentityLinksFromMe(me);
  const isGuest = me.guest === true;
  const cfg = cfgRaw && typeof cfgRaw === "object" ? cfgRaw : {};
  yookassaShopIdFromConfig = cfg.yookassaShopId ? String(cfg.yookassaShopId).trim() : "";

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
  const chatModels = models.filter(
    (m) =>
      m.supportsChat !== false &&
      !(m.supportsMusicGeneration === true && !modelProducesSpokenChatAudio(m)),
  );

  const balanceEl = document.getElementById("balance");
  const balanceWrapEl = document.getElementById("balance-wrap");
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
  /** Когда браузер не выставил MIME (часто с диска под Windows). */
  function fileLooksLikeImage(f) {
    if (!f || typeof f !== "object") return false;
    const t = (f.type || "").toLowerCase();
    if (t.startsWith("image/")) return true;
    const n = String(f.name || "").toLowerCase();
    return /\.(png|jpe?g|gif|webp|bmp|svg|heic|avif|ico)$/i.test(n);
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
  const chatSidebarResizerEl = document.getElementById("chat-sidebar-resizer");

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
    if (!isChatDrawerMode()) {
      setChatSidebarOpen(false);
      if (!isGuest && chatSidebarEl) {
        applyChatSidebarWidthPx(readChatSidebarWidthStored() ?? defaultChatSidebarWidthPx());
      }
    }
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
    if (chatSidebarEl && chatSidebarResizerEl) {
      chatSidebarResizerEl.removeAttribute("hidden");
      bindChatSidebarResizer(chatSidebarEl, chatSidebarResizerEl, isChatDrawerMode);
    } else if (chatSidebarEl) {
      applyChatSidebarWidthPx(readChatSidebarWidthStored() ?? defaultChatSidebarWidthPx());
    }
    setChatSidebarOpen(false);
  }

  if (!isGuest && cfg && cfg.yookassaEnabled && btnPay) {
    const shopId = yookassaShopIdFromConfig;
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

      applyYookassaShopIdToForm();

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
    const sid = yookassaShopIdFromConfig;
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
    if (balanceWrapEl) balanceWrapEl.title = `Баланс: ${balanceEl.textContent}`;
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

  /** Первая в списке «Бесплатные» (сортировка как у вкладки): по displayName, ru. */
  function firstFreeChatModelSlug(list) {
    const freeModels = list.filter((x) => x.isFree === true);
    freeModels.sort((a, b) =>
      String(a.displayName || "").localeCompare(String(b.displayName || ""), "ru"),
    );
    return freeModels[0]?.slug ?? null;
  }

  /** Без сохранённого выбора: первая бесплатная в каталоге (та же сортировка, что вкладка «Бесплатные»), иначе первая модель чата. */
  function defaultChatModelSlugOrFallback() {
    return firstFreeChatModelSlug(chatModels) ?? chatModels[0]?.slug ?? "";
  }

  let selectedSlug = defaultChatModelSlugOrFallback();
  /** У аккаунта в БД уже есть last_chat_model_slug. */
  let hadServerLastModel = false;
  /** true: не открывать последний чат при старте — уважать ?model= или выбор с тарифов. */
  let honorExplicitModelChoice = false;
  /* Сначала ?model= (переход с /tariffs), иначе lastModelSlug аккаунта, иначе бесплатная. */
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
      let remembered = false;

      /** Сохранённый выбор аккаунта важнее локальной сессии. */
      hadServerLastModel =
        !isGuest &&
        typeof me.lastModelSlug === "string" &&
        me.lastModelSlug.trim() &&
        chatModels.some((x) => x.slug === me.lastModelSlug.trim());

      if (hadServerLastModel) {
        selectedSlug = me.lastModelSlug.trim();
        sessionStorage.setItem(MODEL_STORAGE_KEY, selectedSlug);
        remembered = true;
      }

      /** Гость: помнить локальный выбор. Аккаунт без lastModelSlug — сразу бесплатная. */
      if (!remembered && isGuest) {
        const savedSlug = sessionStorage.getItem(MODEL_STORAGE_KEY);
        if (savedSlug && chatModels.some((x) => x.slug === savedSlug)) {
          selectedSlug = savedSlug;
          remembered = true;
        }
      }

      if (!remembered) {
        const d = defaultChatModelSlugOrFallback();
        if (d) {
          selectedSlug = d;
          sessionStorage.setItem(MODEL_STORAGE_KEY, d);
        }
      }

      if (!isGuest && !hadServerLastModel && selectedSlug) {
        schedulePersistLastModel(selectedSlug);
      }

      if (sessionStorage.getItem(MODEL_EXPLICIT_CHOICE_KEY) === "1") {
        honorExplicitModelChoice = true;
        sessionStorage.removeItem(MODEL_EXPLICIT_CHOICE_KEY);
      }
    }
  } catch {
    /* ignore */
  }

  function selectModelVisual(slug) {
    if (!modelPickerEl) return;
    if (!chatModels.length) {
      modelPickerEl.innerHTML =
        '<p class="model-picker-empty">Нет доступных моделей.</p>';
      return;
    }
    const m =
      chatModels.find((x) => x.slug === slug) ||
      chatModels.find((x) => x.slug === selectedSlug) ||
      chatModels[0];
    const name = (m && m.displayName) || slug || "—";
    const provider = (m && m.provider) || "";
    const priceLine = m ? modelCardPricingLine(m) : "";
    const freeBadge =
      m && m.isFree === true
        ? '<span class="model-card-free-badge">Free</span>'
        : "";
    const metaParts = [];
    if (provider) metaParts.push(escapeHtml(provider));
    if (priceLine) metaParts.push(escapeHtml(priceLine));
    modelPickerEl.innerHTML =
      '<div class="current-model-bar">' +
      '<div class="current-model-bar__info">' +
      '<span class="current-model-bar__label">Текущая модель</span>' +
      '<div class="current-model-bar__title">' +
      '<span class="current-model-bar__name">' +
      escapeHtml(name) +
      "</span>" +
      freeBadge +
      "</div>" +
      (metaParts.length
        ? '<span class="current-model-bar__meta">' + metaParts.join(" · ") + "</span>"
        : "") +
      "</div>" +
      '<a class="btn btn-primary current-model-bar__choose" href="/tariffs">Выбрать модель</a>' +
      "</div>";
  }

  function setSelectedSlug(slug) {
    if (!chatModels.some((x) => x.slug === slug)) return;
    selectedSlug = slug;
    sessionStorage.setItem(MODEL_STORAGE_KEY, slug);
    selectModelVisual(slug);
    updateHints();
    schedulePersistLastModel(slug);
  }

  const sttModeHintEl = document.getElementById("stt-mode-hint");
  const videoModeHintEl = document.getElementById("video-mode-hint");
  function updateHints() {
    const m = selectedModel();
    if (!m) return;
    const vid = m.supportsVideoGeneration === true;
    if (sttModeHintEl) {
      sttModeHintEl.style.display = !vid && isSttOnlyModel(m) ? "block" : "none";
    }
    if (videoModeHintEl) {
      videoModeHintEl.style.display = vid ? "block" : "none";
    }
    const bm = document.getElementById("btn-mic");
    if (bm) {
      const allowMic = modelAllowsMicrophone(m);
      bm.style.display = allowMic ? "" : "none";
      bm.disabled = !allowMic;
      bm.title = allowMic
        ? "Удерживайте — запись; отпустите — отправка: при модели «Голос» аудио уходит в модель, иначе — распознавание речи."
        : m?.isFree === true
          ? "На бесплатных моделях нет микрофона и вложений — выберите платную модель."
          : "Голос недоступен в этом режиме";
    }
    const at = document.getElementById("btn-attach");
    if (at) {
      const allowAtt = m.supportsVideoGeneration !== true && m.isFree !== true;
      at.style.display = allowAtt ? "" : "none";
      at.disabled = !allowAtt;
      at.title = allowAtt
        ? "Прикрепить изображение или аудио"
        : m?.isFree === true
          ? "Вложения только на платных моделях"
          : "Вложения недоступны в режиме видео";
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
  let streaming = false;
  let transcribing = false;

  function selectedModel() {
    const found = chatModels.find((x) => x.slug === selectedSlug);
    if (found) return found;
    if (selectedSlug) {
      console.warn(
        "[model] выбранный идентификатор модели не найден в списке, сброс на первую модель:",
        selectedSlug,
      );
    }
    const fb = defaultChatModelSlugOrFallback();
    return chatModels.find((x) => x.slug === fb) ?? chatModels[0] ?? null;
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
    typing.className = "typing typing--loading";
    typing.textContent = "Формирую ответ…";
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
  /** Slug строки диалога в БД: если модель уже убрана из каталога, не подменяем PATCH-ем текущим селектом. */
  let threadPersistModelSlug = null;

  function threadModelSlugForPersist() {
    if (!threadPersistModelSlug)
      return selectedModel()?.slug ?? selectedSlug;
    const inCatalog = chatModels.some((x) => x.slug === threadPersistModelSlug);
    if (!inCatalog) return threadPersistModelSlug;
    return selectedModel()?.slug ?? threadPersistModelSlug ?? selectedSlug;
  }

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

  let guestChatSaveTimer = 0;
  function saveGuestChatSession() {
    if (!isGuest) return;
    try {
      if (chatHistory.length === 0) {
        sessionStorage.removeItem(GUEST_CHAT_SESSION_KEY);
        return;
      }
      const messages = JSON.parse(
        JSON.stringify(sanitizeMessagesForPersist(chatHistory)),
      );
      const payload = {
        v: 1,
        modelSlug: selectedSlug || "",
        messages,
        at: Date.now(),
      };
      const raw = JSON.stringify(payload);
      if (raw.length > GUEST_CHAT_SESSION_MAX_BYTES) return;
      sessionStorage.setItem(GUEST_CHAT_SESSION_KEY, raw);
    } catch {
      /* quota / приватный режим */
    }
  }
  function scheduleGuestChatSave() {
    if (!isGuest) return;
    window.clearTimeout(guestChatSaveTimer);
    guestChatSaveTimer = window.setTimeout(() => {
      guestChatSaveTimer = 0;
      saveGuestChatSession();
    }, 320);
  }
  function tryRestoreGuestChatSession() {
    if (!isGuest) return;
    try {
      const raw = sessionStorage.getItem(GUEST_CHAT_SESSION_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (
        !data ||
        data.v !== 1 ||
        !Array.isArray(data.messages) ||
        data.messages.length === 0
      ) {
        return;
      }
      chatHistory.length = 0;
      for (const m of data.messages) chatHistory.push(m);
      const slug =
        typeof data.modelSlug === "string" && data.modelSlug.trim()
          ? data.modelSlug.trim()
          : "";
      if (slug && chatModels.some((x) => x.slug === slug)) {
        setSelectedSlug(slug);
      }
      renderChatFromHistory();
      listEl.scrollTop = listEl.scrollHeight;
    } catch {
      try {
        sessionStorage.removeItem(GUEST_CHAT_SESSION_KEY);
      } catch {
        /* ignore */
      }
    }
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
            modelSlug: threadModelSlugForPersist(),
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
      threadPersistModelSlug = null;
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
    threadPersistModelSlug = null;
  }

  async function openThread(id, opts) {
    const applyThreadModel = opts?.applyThreadModel !== false;
    if (isGuest) return;
    if (streaming || transcribing || videoGenInProgress || !id) return;
    if (currentConversationId && currentConversationId !== id) {
      await abandonOrPersistCurrent();
    }
    const r = await api(`/api/v1/conversations/${encodeURIComponent(id)}`);
    if (!r.ok) return;
    const t = await r.json();
    currentConversationId = t.id;
    threadPersistModelSlug =
      typeof t.modelSlug === "string" && t.modelSlug.trim() ? t.modelSlug.trim() : null;
    chatHistory.length = 0;
    for (const m of t.messages || []) {
      chatHistory.push(m);
    }
    if (
      applyThreadModel &&
      threadPersistModelSlug &&
      chatModels.some((x) => x.slug === threadPersistModelSlug)
    ) {
      setSelectedSlug(threadPersistModelSlug);
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
    if (isGuest) {
      try {
        sessionStorage.removeItem(GUEST_CHAT_SESSION_KEY);
      } catch {
        /* ignore */
      }
    }
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
    threadPersistModelSlug =
      selectedModel()?.slug ?? (selectedSlug && selectedSlug.trim() ? selectedSlug.trim() : null);
    await refreshSidebarList();
  }

  async function initConversations() {
    if (isGuest) {
      currentConversationId = null;
      threadPersistModelSlug = null;
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
      threadPersistModelSlug = null;
      chatHistory.length = 0;
      listEl.innerHTML = "";
      errEl.style.display = "none";
      errEl.textContent = "";
      await refreshSidebarList();
      return;
    }
    if (!Array.isArray(list) || list.length === 0) {
      currentConversationId = null;
      threadPersistModelSlug = null;
      await refreshSidebarList();
      return;
    }
    /** Последний диалог открываем, но модель не трогаем: lastModelSlug или бесплатная по умолчанию. */
    await openThread(list[0].id, { applyThreadModel: false });
  }

  await initConversations();

  function setComposerDisabled(flag) {
    if (inputEl) inputEl.disabled = flag;
    const mod = selectedModel();
    const allowVoice = mod ? modelAllowsMicrophone(mod) : false;
    const allowAttach =
      mod && mod.supportsVideoGeneration !== true && mod.isFree !== true;
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
        scheduleGuestChatSave();
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
      errEl.textContent = "Сеть или сервер недоступны." + HINT_SUPPORT_IF_REPEATS;
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

    function flushStreamMdNow() {
      md.innerHTML = renderAssistantHtml();
      listEl.scrollTop = listEl.scrollHeight;
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
      let errText = "Ошибка запроса.";
      const d = j.detail;
      if (j.error === "provider_error" && typeof d === "string" && d.trim()) {
        const mod = selectedModel();
        errText = humanizeOpenRouterStreamError(d, {
          isFree: mod?.isFree === true,
          modelSlug: mod?.slug,
        });
      } else if (typeof d === "string" && d.trim()) {
        errText = d;
      }
      errEl.textContent = errText;
      errEl.style.display = "block";
      typing.remove();
      await persistThread();
      return;
    }

    await consumeStream(
      res.body.getReader(),
      (delta) => {
        acc += delta;
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
        console.warn("[chat] stream error", raw);
        const msg = humanizeOpenRouterStreamError(raw, {
          isFree: mod?.isFree === true,
          modelSlug: mod?.slug,
        });
        const suppressRawTail = msg.includes("«Контакты»");
        const tail =
          !suppressRawTail &&
          raw.trim() &&
          msg !== raw.trim() &&
          !msg.includes(raw.trim())
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
      },
      undefined,
      (aud) => {
        if (aud && typeof aud === "object" && aud._replaceAudio === true) {
          audioParts.length = 0;
        }
        if (aud && typeof aud === "object" && aud.data) {
          audioParts.push(aud.data);
        } else if (typeof aud === "string" && aud) {
          audioParts.push(aud);
        }
      },
    );

    typing.remove();
    flushStreamMdNow();

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
          }
        }
      } catch (e) {
        console.warn("[chat] assistant audio decode", e);
      }
    }

    const asst = { role: "assistant", content: acc };
    if (imageUrls.length) asst.imageUrls = imageUrls;
    chatHistory.push(asst);

    const me2 = await api("/api/auth/me").then((r) => r.json());
    if (!me2.guest && me2.balance != null) refreshBalance(me2.balance);
    void persistThread().catch((e) => console.warn("[chat] persistThread", e));
    scheduleGuestChatSave();
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
          if (r.status === 503 && typeof j.detail === "string" && j.detail.trim()) {
            ui.statusEl.textContent = "Провайдер недоступен.";
            await pushVideoAssistantRow(j.detail.trim(), errVideoMeta);
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
      if (!modelAllowsMicrophone(mod)) {
        errEl.textContent =
          mod.isFree === true
            ? "Микрофон и вложения на бесплатных моделях недоступны — выберите платную модель."
            : "Голос недоступен в этом режиме (например, выбрана модель для видео).";
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
        "Вложения доступны только на платных моделях — переключитесь с вкладки «Бесплатные».";
      errEl.style.display = "block";
      return;
    }
    if (fileLooksLikeUserAudio(f)) {
      if (!modelAllowsVoiceAttachment(mod)) {
        errEl.textContent =
          "Аудио для этой модели недоступно. Выберите другую модель или вкладку «Транскрипция».";
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
    const looksImage = f.type.startsWith("image/") || fileLooksLikeImage(f);
    if (looksImage) {
      if (!mod.supportsVision) {
        errEl.textContent =
          "Эта модель не анализирует изображения. Выберите модель с поддержкой vision.";
        errEl.style.display = "block";
        return;
      }
      if (mod.isFree !== true && isGuest) {
        errEl.textContent =
          "Платные модели доступны после входа. Нажмите «Войти» в шапке.";
        errEl.style.display = "block";
        return;
      }
      let b64;
      try {
        b64 = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(String(r.result));
          r.onerror = () => rej(new Error("read"));
          r.readAsDataURL(f);
        });
      } catch {
        errEl.textContent =
          "Не удалось прочитать файл. Проверьте доступ к файлу или выберите другое изображение.";
        errEl.style.display = "block";
        return;
      }
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
      return;
    }
    errEl.textContent =
      "Формат не поддерживается. Можно прикрепить изображение (например PNG, JPEG, WebP, GIF) или аудио (MP3, WAV, WebM и др.). PDF, документы и архивы сюда не загружаются — скопируйте текст в поле сообщения.";
    errEl.style.display = "block";
  });

  tryRestoreGuestChatSession();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveGuestChatSession();
  });
  window.addEventListener("pagehide", () => saveGuestChatSession());
}

main().catch((e) => {
  console.error("[chat] main failed", e);
  const mp = document.getElementById("model-picker");
  if (mp) {
    mp.innerHTML =
      '<p class="model-picker-empty">Не удалось загрузить интерфейс чата. Обновите страницу. Подробности — в консоли (F12).</p>';
  }
});
