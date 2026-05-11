"""Сравнение slug из локальных каталогов с GET https://openrouter.ai/api/v1/models (состав id).

Публичный JSON `/models` у OpenRouter обычно перечисляет в основном completions-модели; идентификаторы
embeddings могут быть доступны через Embeddings API, но отсутствовать в этом списке — это ожидаемо,
а не признак недоступности slug.
"""

from __future__ import annotations

from typing import Any

from .config import get_settings
from .database import get_default_embedding_specs, get_default_model_specs
from .openrouter_price_sync import (
    LYRIA_USD_PER_MEDIA_UNIT,
    TRANSCRIPTION_USD_PER_MINUTE_FALLBACK,
    VIDEO_USD_PER_SEC,
    fetch_models_map,
)


def _exempt_openrouter_slug_slugs() -> frozenset[str]:
    s = get_settings()
    out = {
        str(s.openrouter_free_router_slug),
        "openrouter/auto",
    }
    out.update(VIDEO_USD_PER_SEC.keys())
    out.update(TRANSCRIPTION_USD_PER_MINUTE_FALLBACK.keys())
    out.update(LYRIA_USD_PER_MEDIA_UNIT.keys())
    return frozenset(out)


def check_catalog_vs_openrouter() -> dict[str, Any]:
    """
    Slug чат-каталога: «не в каталоге OR», если их нет среди ``id`` в ответе ``/models`` (и не exempt).

    Эмбеддинги: тот же механический список «нет в теле GET /models» — только справочно; на флаг
    catalogLooksAligned не влияет (см. embeddingsCatalog.publicModelsIncomplete).
    """
    models_map = fetch_models_map()
    or_ids = frozenset(models_map.keys())
    exempt = _exempt_openrouter_slug_slugs()

    chat_missing: list[str] = []
    chat_checked = 0
    for spec in get_default_model_specs():
        if spec.get("is_free"):
            continue
        slug = str(spec["slug"])
        if slug in exempt:
            continue
        chat_checked += 1
        if slug not in or_ids:
            chat_missing.append(slug)

    emb_missing: list[str] = []
    emb_checked = 0
    for spec in get_default_embedding_specs():
        if spec.get("is_free"):
            continue
        slug = str(spec["slug"])
        if slug in exempt:
            continue
        emb_checked += 1
        if slug not in or_ids:
            emb_missing.append(slug)

    return {
        "openRouterSlugCount": len(or_ids),
        "chatCatalog": {
            "slugsCompared": chat_checked,
            "slugsMissingOnPublicModelsEndpoint": chat_missing,
            # Совместимость со старым фронтом / скриптами
            "slugsMissingOnOpenRouter": chat_missing,
        },
        "embeddingsCatalog": {
            "slugsCompared": emb_checked,
            "slugsMissingOnPublicModelsEndpoint": emb_missing,
            "slugsMissingOnOpenRouter": emb_missing,
            "publicModelsIncomplete": True,
            "noteRu": (
                "Ответ GET …/models у OpenRouter чаще всего не содержит строки только для эмбеддингов "
                "(их нет среди этих id — норма). Доступность slug для Embeddings API на сайте OpenRouter "
                "с этим не связана."
            ),
        },
        # Только чат-каталог: именно его цены синхронизируются из полей pricing в этом JSON.
        "catalogLooksAligned": len(chat_missing) == 0,
    }
