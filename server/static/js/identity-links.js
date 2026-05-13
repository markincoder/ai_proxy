/**
 * На этой машине в localStorage копятся только VK ID и Яндекс ID прошлых входов.
 * При OAuth в URL передаются merge_vk / merge_yandex — сервер на callback дописывает второй id к той же строке users.
 */

export const IDENTITY_LINKS_LS_KEY = "ii_proxy_identity_links_v1";

function safeMerge(stored, incoming) {
  const out = { ...stored };
  if (incoming.vkUserId != null && String(incoming.vkUserId).trim() !== "") {
    out.vkUserId = String(incoming.vkUserId).trim();
  }
  if (incoming.yandexUserId != null && String(incoming.yandexUserId).trim() !== "") {
    out.yandexUserId = String(incoming.yandexUserId).trim();
  }
  delete out.username;
  return out;
}

export function persistIdentityLinksFromMe(me) {
  if (typeof localStorage === "undefined" || !me || me.guest === true) return;
  try {
    let prev = {};
    try {
      prev = JSON.parse(localStorage.getItem(IDENTITY_LINKS_LS_KEY) || "{}");
    } catch {
      prev = {};
    }
    const base = prev && typeof prev === "object" && !Array.isArray(prev) ? prev : {};
    delete base.username;
    const next = safeMerge(base, {
      vkUserId: me.vkUserId,
      yandexUserId: me.yandexUserId,
    });
    localStorage.setItem(IDENTITY_LINKS_LS_KEY, JSON.stringify(next));
  } catch {
    /* quota / приватный режим */
  }
}

/** opts: необязательные merge_* из формы (обычно только merge из LS через oauthStartUrl). */
export function oauthStartUrl(basePath, opts) {
  const path = String(basePath || "").trim() || "/api/auth/oauth/yandex/start";
  opts = opts && typeof opts === "object" ? opts : {};
  try {
    const raw = localStorage.getItem(IDENTITY_LINKS_LS_KEY);
    let o = {};
    try {
      o = raw ? JSON.parse(raw) : {};
    } catch {
      o = {};
    }
    const p = new URLSearchParams();
    if (o && typeof o === "object") {
      if (o.vkUserId) p.set("merge_vk", String(o.vkUserId).trim());
      if (o.yandexUserId) p.set("merge_yandex", String(o.yandexUserId).trim());
    }
    const qs = p.toString();
    return qs ? `${path}?${qs}` : path;
  } catch {
    return path;
  }
}
