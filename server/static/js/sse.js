/** Разбор SSE OpenRouter + event:billing + user_transcript + delta.audio / modaudio */

/**
 * OpenRouter/OpenAI: delta.content может быть строкой или массивом частей
 * [{ type: "text", text: "…" }]. Браузер делает acc += [] → "[object Object]".
 */
function flattenOpenRouterDeltaContent(c) {
  if (c == null) return "";
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    const out = [];
    for (const p of c) {
      if (typeof p === "string") {
        out.push(p);
        continue;
      }
      if (p && typeof p === "object") {
        if (p.type === "text" && p.text != null) out.push(String(p.text));
        else if (typeof p.text === "string") out.push(p.text);
      }
    }
    return out.join("");
  }
  if (typeof c === "object" && c.text != null) return String(c.text);
  return "";
}

function extractOpenRouterErrorMessage(j) {
  const e = j?.error;
  if (e == null) return "";
  if (typeof e === "string") return e;
  if (typeof e === "object" && e != null) {
    const bits = [];
    if ("message" in e && e.message != null) bits.push(String(e.message));
    const meta = e.metadata;
    if (meta != null) {
      try {
        bits.push(typeof meta === "string" ? meta : JSON.stringify(meta));
      } catch {
        bits.push(String(meta));
      }
    }
    if ("code" in e && e.code != null && !bits.length) bits.push(String(e.code));
    if (bits.length) return bits.join(" · ");
  }
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

/**
 * Из мультичастных content (мультимодальные SSE) достаём только аудиочасти для base64-сборки.
 */
function emitAudioPartsFromMixedContent(parts, emit) {
  if (!Array.isArray(parts)) return;
  for (const p of parts) {
    if (!p || typeof p !== "object") continue;
    const t = p.type;
    if (t !== "audio" && t !== "output_audio") continue;
    const inner =
      typeof p.audio === "object"
        ? p.audio
        : typeof p.output_audio === "object"
          ? p.output_audio
          : p;
    if (inner?.data || typeof inner === "string") {
      emit(inner);
    }
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
  /** Уже брали chunked delta.audio — финальный choices[0].message.audio часто дублирует тот же трек целиком. */
  let hadStreamedDeltaAudio = false;

  function emitNormalizedAudio(candidate) {
    if (candidate == null || typeof onAudioDelta !== "function") return;
    let audd = candidate;
    if (typeof audd === "string") audd = { data: audd };
    if (audd && typeof audd === "object") {
      onAudioDelta(audd);
      if (typeof audd.data === "string" && audd.data.length > 0) {
        hadStreamedDeltaAudio = true;
      }
    }
  }

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
            console.warn("[OpenRouter SSE] error chunk", detail, j);
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
        emitAudioPartsFromMixedContent(delta?.content, emitNormalizedAudio);

        let audd = delta?.audio;
        if (typeof audd === "string") audd = { data: audd };
        if (!audd && delta?.output_audio != null) {
          const o = delta.output_audio;
          audd = typeof o === "string" ? { data: o } : o;
        }
        if (!audd && delta?.modaudio != null) {
          const m = delta.modaudio;
          audd = typeof m === "string" ? { data: m } : m;
        }
        if (audd) {
          emitNormalizedAudio(audd);
        }

        const msg0 = j.choices?.[0]?.message;
        emitAudioPartsFromMixedContent(msg0?.content, emitNormalizedAudio);
        if (msg0?.audio != null && !hadStreamedDeltaAudio) {
          emitNormalizedAudio(msg0.audio);
        }

        const text = flattenOpenRouterDeltaContent(delta?.content);
        if (text) {
          onDelta(text);
        } else if (
          audd &&
          typeof audd === "object" &&
          typeof audd.transcript === "string" &&
          audd.transcript
        ) {
          onDelta(audd.transcript);
        } else {
          const transcript =
            msg0?.audio &&
            typeof msg0.audio === "object" &&
            typeof msg0.audio.transcript === "string"
              ? msg0.audio.transcript
              : "";
          if (transcript) onDelta(transcript);
        }
      } catch {
        /* ignore */
      }
    }
  }
}
