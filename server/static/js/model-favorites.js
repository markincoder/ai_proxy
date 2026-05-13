/**
 * Избранные модели: localStorage + для авторизованных — сервер (БД).
 * Первый визит гостя: если ключа нет — сидируем популярные slug из каталога.
 * Первый GET /api/auth/me/model-favorites для пользователя: сервер сидирует те же популярные в каталоге.
 */

export const FAVORITES_LS_KEY = "ai_proxy_favorite_slugs_v1";

/** Ориентир по спросу; в UI попадут только slug, реально есть в GET /api/models */
export const DEFAULT_FAVORITE_SLUGS = [
  "openai/gpt-4o",
  "google/gemini-2.5-flash",
  "anthropic/claude-sonnet-4",
  "openai/gpt-5.5",
  "deepseek/deepseek-chat-v3.1",
];

/** После loadMe: true — писать на сервер при переключении звезды. */
let serverFavoriteSync = false;

export function setModelFavoritesServerSync(on) {
  serverFavoriteSync = Boolean(on);
}

function parseStoredSet(raw) {
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object" || !Array.isArray(o.slugs)) return null;
    return new Set(o.slugs.map((x) => String(x).trim()).filter(Boolean));
  } catch {
    return null;
  }
}

export function readFavoriteSlugSet() {
  try {
    const raw = localStorage.getItem(FAVORITES_LS_KEY);
    if (raw == null) return null;
    return parseStoredSet(raw);
  } catch {
    return null;
  }
}

export function writeFavoriteSlugSet(set) {
  try {
    const slugs = [...set];
    localStorage.setItem(FAVORITES_LS_KEY, JSON.stringify({ v: 1, slugs }));
  } catch {
    /* quota / приватный режим */
  }
}

function setsEqual(arr1, arr2) {
  const s1 = new Set(arr1);
  const s2 = new Set(arr2);
  if (s1.size !== s2.size) return false;
  for (const x of s1) {
    if (!s2.has(x)) return false;
  }
  return true;
}

async function pushFavoriteSlugsToServer(catalogSlugs) {
  if (!serverFavoriteSync) return;
  const cat = new Set((catalogSlugs || []).map((x) => String(x).trim()).filter(Boolean));
  const cur = readFavoriteSlugSet();
  if (!cur) return;
  const slugs = [...cur].filter((x) => cat.has(x));
  try {
    const r = await fetch("/api/auth/me/model-favorites", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slugs }),
    });
    if (r.status === 401) {
      serverFavoriteSync = false;
    }
  } catch {
    /* ignore */
  }
}

/**
 * Загрузить избранное с сервера в localStorage; объединить с локальными звёздами (гость → вход).
 */
export async function hydrateModelFavoritesFromServer(catalogSlugs) {
  if (!serverFavoriteSync) return;
  const cat = new Set((catalogSlugs || []).map((s) => String(s).trim()).filter(Boolean));
  const localSet = readFavoriteSlugSet() || new Set();

  let serverList = [];
  try {
    const r = await fetch("/api/auth/me/model-favorites", {
      credentials: "include",
    });
    if (r.status === 401) {
      serverFavoriteSync = false;
      return;
    }
    if (!r.ok) return;
    const data = await r.json();
    if (!data || !Array.isArray(data.slugs)) return;
    serverList = data.slugs
      .map((x) => String(x).trim())
      .filter((s) => cat.has(s));
  } catch {
    return;
  }

  const merged = new Set(serverList);
  for (const s of localSet) {
    if (cat.has(s)) merged.add(s);
  }
  const mergedArr = [...merged];
  writeFavoriteSlugSet(new Set(mergedArr));

  if (!setsEqual(mergedArr, serverList)) {
    try {
      const r2 = await fetch("/api/auth/me/model-favorites", {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slugs: mergedArr }),
      });
      if (r2.status === 401) {
        serverFavoriteSync = false;
      }
    } catch {
      /* ignore */
    }
  }
}

/**
 * Множество избранного, пересечённое с каталогом.
 * Если localStorage пуст (первый заход гостя) — записать дефолты ∩ каталог.
 */
export function getFavoriteSetForCatalog(catalogSlugs) {
  const cat = new Set((catalogSlugs || []).map((s) => String(s).trim()).filter(Boolean));
  try {
    let raw = localStorage.getItem(FAVORITES_LS_KEY);
    if (raw === null) {
      const seeded = new Set();
      for (const s of DEFAULT_FAVORITE_SLUGS) {
        if (cat.has(s)) seeded.add(s);
      }
      writeFavoriteSlugSet(seeded);
      return new Set([...seeded].filter((s) => cat.has(s)));
    }
    const parsed = parseStoredSet(raw);
    const base = parsed || new Set();
    return new Set([...base].filter((s) => cat.has(s)));
  } catch {
    return new Set();
  }
}

export function isSlugFavorite(slug, catalogSlugs) {
  const s = String(slug || "").trim();
  if (!s) return false;
  const set = getFavoriteSetForCatalog(catalogSlugs);
  return set.has(s);
}

/** @returns {boolean} новое состояние «в избранном» */
export function toggleFavoriteSlug(slug, catalogSlugs) {
  const s = String(slug || "").trim();
  if (!s) return false;
  const cat = new Set((catalogSlugs || []).map((x) => String(x).trim()).filter(Boolean));
  if (!cat.has(s)) return false;
  let set = readFavoriteSlugSet();
  if (set === null) set = getFavoriteSetForCatalog([...cat]);
  else set = new Set([...set].filter((x) => cat.has(x)));
  if (set.has(s)) set.delete(s);
  else set.add(s);
  writeFavoriteSlugSet(set);
  void pushFavoriteSlugsToServer(catalogSlugs);
  return set.has(s);
}

export function syncFavoriteStarButton(btn, active) {
  if (!btn) return;
  btn.classList.toggle("model-favorite-star--active", active);
  btn.setAttribute("aria-pressed", active ? "true" : "false");
  btn.setAttribute(
    "aria-label",
    active ? "Убрать из избранного" : "Добавить в избранное",
  );
  const ch = btn.querySelector(".model-favorite-star__icon");
  if (ch) ch.textContent = active ? "★" : "☆";
}
