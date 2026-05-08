from decimal import Decimal
from typing import Any, Optional

from .models import AiModel

# Ориентир выхода для проверки баланса до запроса (покрывает типичный ответ; факт после ответа может быть выше).
_OUTPUT_RESERVE_TOKENS = 2048
_OUTPUT_RESERVE_IMAGE_GEN = 8192
# Бюджет токенов на одно вложенное изображение (vision), грубая верхняя оценка.
_VISION_IMAGE_TOKEN_BUDGET = 2500
# Резерв выхода для музыки/аудио (длинный ответ modaudio).
_OUTPUT_RESERVE_MUSIC = 8192


def estimate_min_spend_rub(model: AiModel, messages: list[dict[str, Any]]) -> Decimal:
    """Нижняя оценка стоимости одного ответа до вызова OpenRouter (вход + резерв выхода)."""
    if model.is_free:
        return Decimal("0")
    total_chars = 0
    image_parts = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                t = part.get("type")
                if t == "text":
                    total_chars += len(str(part.get("text") or ""))
                elif t in ("image_url", "input_image"):
                    image_parts += 1
    inp_tokens = max(int(total_chars / 4) + image_parts * _VISION_IMAGE_TOKEN_BUDGET, 100)
    if model.supports_image_generation:
        out_n = _OUTPUT_RESERVE_IMAGE_GEN
    elif model.supports_music_generation:
        out_n = _OUTPUT_RESERVE_MUSIC
    else:
        out_n = _OUTPUT_RESERVE_TOKENS
    in_cost = Decimal(model.input_price_per_mn) * inp_tokens / Decimal(1_000_000)
    out_cost = Decimal(model.output_price_per_mn) * out_n / Decimal(1_000_000)
    est = in_cost + out_cost
    if model.fixed_price is not None:
        est = max(est, Decimal(model.fixed_price))
    return est


def compute_spend_rub(
    model: AiModel,
    usage: Optional[dict[str, Any]],
) -> Decimal:
    u = usage or {}
    inp = int(u.get("prompt_tokens") or 0)
    out = int(u.get("completion_tokens") or 0)

    in_cost = Decimal(model.input_price_per_mn) * inp / Decimal(1_000_000)
    out_cost = Decimal(model.output_price_per_mn) * out / Decimal(1_000_000)
    total = in_cost + out_cost

    if model.fixed_price is not None:
        return max(total, Decimal(model.fixed_price))
    return total
