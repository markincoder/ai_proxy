/** Разбор SSE OpenRouter + event:billing в конце. onImages — чанки с delta.images (генерация картинок). */

function extractOpenRouterErrorMessage(j) {
  const e = j?.error;
  if (e == null) return "";
  if (typeof e === "string") return e;
  if (typeof e === "object" && e != null) {
    if ("message" in e && e.message != null) return String(e.message);
    if ("code" in e && e.code != null) return String(e.code);
  }
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

export async function consumeStream(reader, onDelta, onBilling, onStreamError, onImages) {
  const dec = new TextDecoder();
  let carry = "";
  let expectBilling = false;
  /** Не дублировать onStreamError на каждый повторяющийся чанк с error. */
  let streamErrorReported = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    carry += dec.decode(value, { stream: true });
    const parts = carry.split("\n");
    carry = parts.pop() ?? "";
    for (const raw of parts) {
      const line = raw.replace(/\r$/, "");
      if (line === "event: billing") {
        expectBilling = true;
        continue;
      }
      if (expectBilling && line.startsWith("data: ")) {
        try {
          const j = JSON.parse(line.slice(6));
          if (j.costRub != null) onBilling(String(j.costRub));
        } catch {
          /* ignore */
        }
        expectBilling = false;
        continue;
      }
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (payload === "[DONE]") continue;
      try {
        const j = JSON.parse(payload);
        if (j.error) {
          const detail = extractOpenRouterErrorMessage(j) || "Ошибка";
          /* В консоль не спамим: текст уже в UI. Полный объект: sessionStorage.debugOpenRouter = "1" */
          try {
            if (typeof sessionStorage !== "undefined" && sessionStorage.debugOpenRouter) {
              console.warn("[OpenRouter SSE]", detail, j);
            }
          } catch {
            /* ignore */
          }
          if (typeof onStreamError === "function" && !streamErrorReported) {
            streamErrorReported = true;
            onStreamError(detail);
          }
          continue;
        }
        const fr = j.choices?.[0]?.finish_reason;
        if (fr === "error" && typeof onStreamError === "function" && !streamErrorReported) {
          try {
            if (typeof sessionStorage !== "undefined" && sessionStorage.debugOpenRouter) {
              console.warn("[OpenRouter SSE] finish_reason=error", j);
            }
          } catch {
            /* ignore */
          }
          streamErrorReported = true;
          onStreamError("Ошибка провайдера (finish_reason=error)");
          continue;
        }
        const delta = j.choices?.[0]?.delta;
        const imgs = delta?.images;
        if (Array.isArray(imgs) && imgs.length && typeof onImages === "function") {
          onImages(imgs);
        }
        const d = delta?.content;
        if (d) onDelta(d);
      } catch {
        /* ignore */
      }
    }
  }
}
