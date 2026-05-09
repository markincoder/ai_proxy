/** Разбор SSE OpenRouter + event:billing + user_transcript + delta.audio */

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

/**
 * @param onUserTranscript — multipart голос: сервер присылает распознанный текст до стрима ответа
 * @param onAudioDelta — { data?: string, transcript?: string } чанки base64 аудио (голосовой ответ модели)
 */
export async function consumeStream(
  reader,
  onDelta,
  onBilling,
  onStreamError,
  onImages,
  onUserTranscript,
  onAudioDelta,
) {
  const dec = new TextDecoder();
  let carry = "";
  let expectBilling = false;
  let expectUserTranscript = false;
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
        expectUserTranscript = false;
        continue;
      }
      if (line === "event: user_transcript") {
        expectUserTranscript = true;
        expectBilling = false;
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
      if (expectUserTranscript && line.startsWith("data: ")) {
        expectUserTranscript = false;
        try {
          const j = JSON.parse(line.slice(6));
          if (j.text != null && typeof onUserTranscript === "function") {
            onUserTranscript(String(j.text));
          }
        } catch {
          /* ignore */
        }
        continue;
      }
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (payload === "[DONE]") continue;
      try {
        const j = JSON.parse(payload);
        if (j.error) {
          const detail = extractOpenRouterErrorMessage(j) || "Ошибка";
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
        const audd = delta?.audio;
        if (audd && typeof onAudioDelta === "function") {
          onAudioDelta(audd);
        }
        const d = delta?.content;
        if (d) onDelta(d);
      } catch {
        /* ignore */
      }
    }
  }
}
