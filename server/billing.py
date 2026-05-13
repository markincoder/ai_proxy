from decimal import Decimal
from typing import Any, Optional

from .models import AiModel
from .pricing_rub import openrouter_usd_to_balance_rub, rub_price_ceil_2

# Ориентир выхода для проверки баланса до запроса (покрывает типичный ответ; факт после ответа может быть выше).
_OUTPUT_RESERVE_TOKENS = 2048
_OUTPUT_RESERVE_IMAGE_GEN = 8192
# Бюджет токенов на одно вложенное изображение (vision), грубая верхняя оценка.
_VISION_IMAGE_TOKEN_BUDGET = 2500
# Резерв выхода для озвучки / аудио в ответе (длинный completion).
_OUTPUT_RESERVE_MUSIC = 8192


def estimate_min_spend_rub(model: AiModel, messages: list[dict[str, Any]]) -> Decimal:
    """Нижняя оценка стоимости одного ответа до вызова OpenRouter (вход + резерв выхода)."""
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
    elif model.supports_music_generation or model.supports_speech:
        out_n = _OUTPUT_RESERVE_MUSIC
    else:
        out_n = _OUTPUT_RESERVE_TOKENS
    in_cost = Decimal(model.input_price_per_mn) * inp_tokens / Decimal(1_000_000)
    out_cost = Decimal(model.output_price_per_mn) * out_n / Decimal(1_000_000)
    est = in_cost + out_cost
    if model.fixed_price is not None:
        est = max(est, Decimal(model.fixed_price))
    return rub_price_ceil_2(est)


def estimate_embedding_spend_rub(model: AiModel, prompt_token_estimate: int) -> Decimal:
    """Оценка до запроса: только входные токены (эмбеддинги без completion)."""
    n = max(int(prompt_token_estimate), 1)
    in_cost = Decimal(model.input_price_per_mn) * n / Decimal(1_000_000)
    total = in_cost
    if model.fixed_price is not None:
        total = max(total, Decimal(model.fixed_price))
    return rub_price_ceil_2(total)


def compute_embedding_spend_rub(model: AiModel, usage: Optional[dict[str, Any]]) -> Decimal:
    u = usage or {}
    raw_cost = u.get("cost")
    if raw_cost is not None:
        try:
            usd = Decimal(str(raw_cost))
            if usd > 0:
                total = openrouter_usd_to_balance_rub(usd)
                if model.fixed_price is not None:
                    return rub_price_ceil_2(max(total, Decimal(model.fixed_price)))
                return total
        except Exception:
            pass
    inp = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    if inp <= 0 and u.get("total_tokens") is not None:
        inp = int(u.get("total_tokens") or 0)
    in_cost = Decimal(model.input_price_per_mn) * inp / Decimal(1_000_000)
    total = rub_price_ceil_2(in_cost)
    if model.fixed_price is not None:
        return rub_price_ceil_2(max(total, Decimal(model.fixed_price)))
    return total


def compute_spend_rub(
    model: AiModel,
    usage: Optional[dict[str, Any]],
) -> Decimal:
    u = usage or {}
    # OpenRouter иногда отдаёт итог в USD в usage.cost — тот же пересчёт, что для видео и сидов цен.
    raw_cost = u.get("cost")
    if raw_cost is not None:
        try:
            usd = Decimal(str(raw_cost))
            if usd > 0:
                total = openrouter_usd_to_balance_rub(usd)
                if model.fixed_price is not None:
                    return rub_price_ceil_2(max(total, Decimal(model.fixed_price)))
                return total
        except Exception:
            pass

    inp = int(u.get("prompt_tokens") or 0)
    out = int(u.get("completion_tokens") or 0)

    in_cost = Decimal(model.input_price_per_mn) * inp / Decimal(1_000_000)
    out_cost = Decimal(model.output_price_per_mn) * out / Decimal(1_000_000)
    total = in_cost + out_cost

    if model.fixed_price is not None:
        return rub_price_ceil_2(max(total, Decimal(model.fixed_price)))
    return rub_price_ceil_2(total)
