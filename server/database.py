from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy import types as sqltypes
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from . import models as _models  # noqa: F401 — регистрация всех таблиц в Base.metadata
from .models import AiModel, Base, ChatThread

ROOT = Path(__file__).resolve().parent


def _normalize_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:") and not url.startswith("file:"):
        return url
    if url.startswith("file:"):
        raw = url[5:]
        while raw.startswith("/"):
            raw = raw[1:]
        p = Path(raw)
        if not p.is_absolute():
            p = (ROOT / raw).resolve()
        return f"sqlite:///{p.as_posix()}"
    # sqlite:///./data/app.db
    rest = url.replace("sqlite:///", "", 1)
    p = Path(rest)
    if not p.is_absolute():
        p = (ROOT / rest).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p.as_posix()}"


_settings = get_settings()
_db_url = _normalize_sqlite_url(_settings.database_url) if "sqlite" in _settings.database_url or _settings.database_url.startswith("file:") else _settings.database_url

engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if _db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _caps(
    *,
    vision: bool = True,
    image_gen: bool = False,
    music: bool = False,
    video: bool = False,
    speech: bool = False,
    transcription: bool = False,
    code: bool = True,
) -> dict[str, bool]:
    """Возможности для сидов: vision=анализ чужих картинок на входе; image_gen=создание картинок."""
    return {
        "supports_vision": vision,
        "supports_image_generation": image_gen,
        "supports_music_generation": music,
        "supports_video_generation": video,
        "supports_speech": speech,
        "supports_transcription": transcription,
        "supports_coding": code,
    }


# Сиды: slug OpenRouter + тарифы в ₽ / 1M токенов.
# Для моделей с ценами в API: USD за токен (поле pricing OpenRouter) * 1e6 * OPENROUTER_USD_RUB * PRICING_MARKUP_MULT
# (по умолчанию 100 * 3). Обновление БД: python scripts/sync_openrouter_prices.py
# Модели без token pricing в API (видео/часть аудио) — ориентиры ниже задаются вручную.
# Для видео/STT/Lyria см. scripts/sync_openrouter_prices.py (семантика полей input и fixed_price там описана).

DEFAULT_MODEL_SPECS: list[dict[str, object]] = [
    {
        # Маршрутизатор: сам выбирает доступную бесплатную модель (устойчивее отдельных :free slug).
        "slug": "openrouter/free",
        "display_name": "Бесплатно (автовыбор)",
        # При вставке в БД подставляется OPENROUTER_APP_TITLE (см. _provider_from_seed_spec).
        "provider": "Сервис",
        "description_ru": "Нулевая стоимость: подбираем доступную бесплатную модель под запрос. Надёжнее, чем одна устаревшая :free модель.",
        "pricing_note_ru": "0 ₽. Очередь и лимиты на стороне провайдера. Цифры «за 1M токенов» в базе для этой строки не участвуют в расчёте.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(vision=True),
    },
    {
        "slug": "meta-llama/llama-3.3-70b-instruct:free",
        "display_name": "Llama 3.3 70B Instruct (free)",
        "provider": "Meta",
        "description_ru": "Открытая Llama 3.3 70B на бесплатном канале (очередь и лимиты провайдера).",
        "pricing_note_ru": "0 ₽. Качество и доступность зависят от загрузки :free.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "google/gemma-4-31b-it:free",
        "display_name": "Gemma 4 31B IT (free)",
        "provider": "Google",
        "description_ru": "Gemma 4 31B instruction-tuned; бесплатный доступ.",
        "pricing_note_ru": "0 ₽. Бесплатный канал.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "qwen/qwen3-coder:free",
        "display_name": "Qwen3 Coder (free)",
        "provider": "Qwen",
        "description_ru": "Qwen3 Coder на :free — код и технические задачи без платы за токены.",
        "pricing_note_ru": "0 ₽. :free канал.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(vision=False, code=True),
    },
    {
        "slug": "openai/gpt-oss-20b:free",
        "display_name": "GPT-OSS 20B (free)",
        "provider": "OpenAI",
        "description_ru": "Компактная открытая модель OpenAI в бесплатном режиме маршрутизации.",
        "pricing_note_ru": "0 ₽. :free канал.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "openai/gpt-oss-120b:free",
        "display_name": "GPT-OSS 120B (free)",
        "provider": "OpenAI",
        "description_ru": "Крупная открытая модель семейства GPT-OSS на бесплатном маршруте.",
        "pricing_note_ru": "0 ₽. :free канал, лимиты на стороне провайдера.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "google/gemma-4-26b-a4b-it:free",
        "display_name": "Gemma 4 26B A4B IT (free)",
        "provider": "Google",
        "description_ru": "Лёгкая Gemma 4 26B multimodal (текст + картинки/видео на входе) в :free.",
        "pricing_note_ru": "0 ₽. Бесплатная маршрутизация.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "inclusionai/ring-2.6-1t:free",
        "display_name": "Ring 2.6 1T (free)",
        "provider": "Inclusion AI",
        "description_ru": "Ring 2.6 — мощная открытая модель большого масштаба на :free (экспериментально, зависит от загрузки).",
        "pricing_note_ru": "0 ₽. Бесплатный сегмент каталога.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "nousresearch/hermes-3-llama-3.1-405b:free",
        "display_name": "Hermes 3 Llama 3.1 405B (free)",
        "provider": "NousResearch",
        "description_ru": "Сверхбольшая Hermes поверх Llama 3.1 405B — лучшие :free возможности для сложных задач при доступности.",
        "pricing_note_ru": "0 ₽. На :free возможны высокая задержка и отказы при пиках.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "qwen/qwen3-next-80b-a3b-instruct:free",
        "display_name": "Qwen3 Next 80B A3B Instruct (free)",
        "provider": "Qwen",
        "description_ru": "Сбалансированный Qwen3 Next на :free: рассуждения, текст, технические ответы.",
        "pricing_note_ru": "0 ₽. :free доступ.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "minimax/minimax-m2.5:free",
        "display_name": "MiniMax M2.5 (free)",
        "provider": "MiniMax",
        "description_ru": "Агентская модель MiniMax M2.5 на бесплатном канале (когда доступна провайдером).",
        "pricing_note_ru": "0 ₽. :free лимиты.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(vision=False, code=True),
    },
    {
        "slug": "nvidia/nemotron-3-super-120b-a12b:free",
        "display_name": "NVIDIA Nemotron 3 Super 120B (free)",
        "provider": "NVIDIA",
        "description_ru": "Nemotron 3 Super — сильный открытый MoE на :free для длинного контекста и аналитики.",
        "pricing_note_ru": "0 ₽. Бесплатный маршрут.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "z-ai/glm-4.5-air:free",
        "display_name": "GLM 4.5 Air (free)",
        "provider": "Z.AI",
        "description_ru": "Лёгкий GLM 4.5 Air на :free — быстрые ответы и повседневные задачи.",
        "pricing_note_ru": "0 ₽. :free.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(),
    },
    {
        "slug": "poolside/laguna-m.1:free",
        "display_name": "Poolside Laguna M.1 (free)",
        "provider": "Poolside",
        "description_ru": "Специализированная модель для кода и инженерных сценариев на :free.",
        "pricing_note_ru": "0 ₽. :free.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        "is_free": True,
        **_caps(vision=False, code=True),
    },
    {
        "slug": "openai/gpt-5.4",
        "display_name": "GPT-5.4",
        "provider": "OpenAI",
        "description_ru": "Флагман OpenAI: сложные задачи, длинный контекст, код и структурированные ответы.",
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-4o",
        "display_name": "GPT-4o",
        "provider": "OpenAI",
        "description_ru": "Универсальная мультимодальная модель: текст, изображения, быстрые ответы.",
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "anthropic/claude-3.7-sonnet",
        "display_name": "Claude 3.7 Sonnet",
        "provider": "Anthropic",
        "description_ru": "Сбалансированная Sonnet: анализ документов, эссе, аккуратные формулировки.",
        "input_price_per_mn": Decimal("900"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "anthropic/claude-sonnet-4.6",
        "display_name": "Claude Sonnet 4.6",
        "provider": "Anthropic",
        "description_ru": "Новое поколение Sonnet: сложная логика, код, длинные диалоги.",
        "input_price_per_mn": Decimal("900"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "google/gemini-3-flash-preview",
        "display_name": "Gemini 3 Flash",
        "provider": "Google",
        "description_ru": "Быстрый предпросмотр Gemini 3: повседневные запросы и работа с картинками.",
        "input_price_per_mn": Decimal("150"),
        "output_price_per_mn": Decimal("900"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "google/gemini-2.5-flash",
        "display_name": "Gemini 2.5 Flash",
        "provider": "Google",
        "description_ru": "Экономичный и быстрый Gemini для чата, суммаризации и поиска по смыслу.",
        "input_price_per_mn": Decimal("90"),
        "output_price_per_mn": Decimal("750"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "google/gemini-2.5-pro",
        "display_name": "Gemini 2.5 Pro",
        "provider": "Google",
        "description_ru": "Продвинутый Gemini для сложных рассуждений и мультимодальных задач.",
        "input_price_per_mn": Decimal("375"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "google/gemini-2.5-flash-image",
        "display_name": "Gemini 2.5 Flash Image",
        "provider": "Google",
        "description_ru": "Генерация и правка изображений по тексту внутри диалога.",
        "pricing_note_ru": (
            "Картинка в ответе чата: списание по токенам (промпт + выход с изображением). "
            "Итог сильно зависит от размера/разрешения картинки. "
            "Ставки ₽/1M токенов ниже — ориентир для текстовой части; изображение увеличивает выходные токены."
        ),
        "input_price_per_mn": Decimal("90"),
        "output_price_per_mn": Decimal("750"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "openai/gpt-5-image-mini",
        "display_name": "GPT-5 Image Mini",
        "provider": "OpenAI",
        "description_ru": "Компактная модель OpenAI для картинок в ответах чата.",
        "pricing_note_ru": (
            "Как и у других image‑чатов: оплата по фактическим токенам, выход с картинкой дороже «просто текста». "
            "Цифры в таблице — базовый ориентир за 1M токенов."
        ),
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("600"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "openai/gpt-5-image",
        "display_name": "GPT-5 Image",
        "provider": "OpenAI",
        "description_ru": "Флагманская картинка в чате (линейка GPT‑5 Image; замена FLUX Pro в каталоге).",
        "pricing_note_ru": (
            "Как у других image‑моделей: оплата по токенам; выход с изображением дороже текста. "
            "Цены в таблице обновляются по тарифам провайдера и правилам наценки сервиса."
        ),
        "input_price_per_mn": Decimal("3000"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "google/gemini-3-pro-image-preview",
        "display_name": "Gemini 3 Pro Image",
        "provider": "Google",
        "description_ru": "Продвинутая картинка в диалоге (предпросмотр Gemini 3; замена FLUX Flex).",
        "pricing_note_ru": (
            "Мультимодальный тариф (текст/изображение/аудио). Ориентир ₽/1M по полям prompt/completion в API."
        ),
        "input_price_per_mn": Decimal("600"),
        "output_price_per_mn": Decimal("3600"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "openai/gpt-5.4-image-2",
        "display_name": "GPT-5.4 Image 2",
        "provider": "OpenAI",
        "description_ru": "Флагманское изображение в ответе чата (актуальный ряд GPT‑5.4 Image).",
        "pricing_note_ru": "Как у других image‑чатов: выход с картинкой сильно влияет на списание; цифры в таблице — ориентир из тарифа провайдера с учётом наценки сервиса.",
        "input_price_per_mn": Decimal("2400"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "google/gemini-3.1-flash-image-preview",
        "display_name": "Gemini 3.1 Flash Image",
        "provider": "Google",
        "description_ru": "Быстрая генерация и правка картинок в диалоге (предпросмотр Gemini 3.1).",
        "pricing_note_ru": "Image в чате: списание по токенам; цифры в таблице — ориентир из актуального тарифа.",
        "input_price_per_mn": Decimal("150"),
        "output_price_per_mn": Decimal("900"),
        "fixed_price": None,
        **_caps(image_gen=True),
    },
    {
        "slug": "openrouter/auto",
        "display_name": "Авто (подбор модели)",
        "provider": "Сервис",
        "description_ru": "Автоматически подбирается платная модель под запрос (в ответе возможны текст и изображения).",
        "pricing_note_ru": (
            "Стоимость зависит от фактически выбранной модели; фиксированной цены за токен нет. "
            "Итог — по фактическому списанию после ответа; цифры в таблице при наличии тарифа обновляются автоматически."
        ),
        "input_price_per_mn": Decimal("300"),
        "output_price_per_mn": Decimal("1200"),
        "fixed_price": None,
        **_caps(image_gen=True, vision=True, code=False),
    },
    {
        "slug": "kwaivgi/kling-v3.0-pro",
        "display_name": "Kling Video 3.0 Pro",
        "provider": "Kwaivgi",
        "description_ru": "Премиальное видео по тексту или картинке; качество выше уровня Standard.",
        "pricing_note_ru": (
            "Оплата за каждую секунду готового ролика (не по токенам чата). "
            "Вариант со звуком дороже, чем без; длительность и настройки ролика влияют на сумму. "
            "Списание на балансе в рублях — по фактическому результату после генерации. Обычная длина клипа 3–15 с."
        ),
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "kwaivgi/kling-v3.0-std",
        "display_name": "Kling Video 3.0 Standard",
        "provider": "Kwaivgi",
        "description_ru": "Kling 3.0 стандартный уровень: текст или картинка → видео.",
        "pricing_note_ru": (
            "Тариф за секунды готового видео. Слой со звуком дороже, чем без; итог на балансе в рублях — "
            "по фактическому списанию после генерации. Ориентир ₽ за секунду см. в таблице тарифов."
        ),
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "google/veo-3.1-fast",
        "display_name": "Veo 3.1 Fast",
        "provider": "Google",
        "description_ru": "Быстрая генерация видео Veo 3.1: баланс качества и скорости (Fast).",
        "pricing_note_ru": (
            "Списание по длительности готового ролика. Ставка за секунду сильно зависит от разрешения и от того, "
            "есть ли звук (выше разрешение и звук — дороже). Точная сумма — по фактическому списанию; ориентир ₽/с — в таблице тарифов."
        ),
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "minimax/hailuo-2.3",
        "display_name": "MiniMax Hailuo 2.3",
        "provider": "MiniMax",
        "description_ru": "Hailuo 2.3 — видео по промпту (MiniMax).",
        "pricing_note_ru": (
            "Оплата за секунды готового ролика. Итог на балансе в рублях — по фактическому списанию после генерации."
        ),
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "alibaba/wan-2.7",
        "display_name": "Wan 2.7",
        "provider": "Alibaba",
        "description_ru": "Alibaba Wan 2.7 — текст или референс → видео.",
        "pricing_note_ru": (
            "Оплата за секунды готового ролика. Сумма на балансе в рублях указывается по факту после генерации; ориентир в таблице тарифов."
        ),
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "kwaivgi/kling-video-o1",
        "display_name": "Kling Video O1",
        "provider": "Kwaivgi",
        "description_ru": "Kling O1 для кинооптики и контроля кадров (клипы 5 или 10 с).",
        "pricing_note_ru": "Оплата за секунды готового ролика; фактическая сумма — после завершения генерации.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "google/veo-3.1-lite",
        "display_name": "Veo 3.1 Lite",
        "provider": "Google",
        "description_ru": "Облегчённый Veo 3.1: ниже ставка за секунду, чем полный Fast/Pro-профиль при приемлемом качестве.",
        "pricing_note_ru": "Оплата за видео по длительности результата; точная сумма — по фактическому списанию после генерации.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "google/veo-3.1",
        "display_name": "Veo 3.1",
        "provider": "Google",
        "description_ru": "Флагман Veo 3.1 без суффикса Fast — максимальное качество, выше ставка за секунду выходного видео.",
        "pricing_note_ru": "Списание по фактической стоимости у провайдера после готового ролика (разрешение и звук влияют на цену за секунду).",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "alibaba/wan-2.6",
        "display_name": "Wan 2.6",
        "provider": "Alibaba",
        "description_ru": "Обновление Wan перед 2.7: референс и текст → видео с расширенным контролем сцены.",
        "pricing_note_ru": "Оплата за длительность готового ролика; итог — по фактическому списанию после генерации.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "openai/sora-2-pro",
        "display_name": "Sora 2 Pro",
        "provider": "OpenAI",
        "description_ru": "Топ видеогенерации OpenAI Sora: киношное качество, высокая цена за секунду.",
        "pricing_note_ru": "Ставка за секунду зависит от разрешения (например 720p и 1080p); итог — по фактическому списанию после генерации.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "bytedance/seedance-2.0",
        "display_name": "Seedance 2.0",
        "provider": "ByteDance",
        "description_ru": "ByteDance Seedance поколение 2.0 для динамичных сцен и стилизованной анимации.",
        "pricing_note_ru": "У провайдера счёт может идти через video-токены; на балансе — по фактической сумме после готового ролика.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "bytedance/seedance-2.0-fast",
        "display_name": "Seedance 2.0 Fast",
        "provider": "ByteDance",
        "description_ru": "Ускоренный Seedance 2.0 — быстрые наброски и превью сцен под меньший бюджет.",
        "pricing_note_ru": "Провайдер может считать по video-токенам; итог на балансе — после завершения генерации.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "bytedance/seedance-1-5-pro",
        "display_name": "Seedance 1.5 Pro",
        "provider": "ByteDance",
        "description_ru": "Премиальный Seedance 1.5: стабильные персонажи и управляемые движения камеры.",
        "pricing_note_ru": "Стоимость — по фактическому списанию после готового ролика.",
        "input_price_per_mn": Decimal("0"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, video=True, code=False),
    },
    {
        "slug": "deepseek/deepseek-v3.2",
        "display_name": "DeepSeek V3.2",
        "provider": "DeepSeek",
        "description_ru": "Сильная модель для математики, кода и пошаговых рассуждений.",
        "input_price_per_mn": Decimal("75.60"),
        "output_price_per_mn": Decimal("113.40"),
        "fixed_price": None,
        **_caps(vision=False),
    },
    {
        "slug": "qwen/qwen3.6-plus",
        "display_name": "Qwen 3.6 Plus",
        "provider": "Qwen",
        "description_ru": "Универсальная модель Alibaba: текст, анализ, мультиязычность.",
        "input_price_per_mn": Decimal("97.50"),
        "output_price_per_mn": Decimal("585"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "qwen/qwen3-coder-next",
        "display_name": "Qwen3 Coder",
        "provider": "Qwen",
        "description_ru": "Специализация на программировании, рефакторинге и объяснении кода.",
        "input_price_per_mn": Decimal("33"),
        "output_price_per_mn": Decimal("240"),
        "fixed_price": None,
        **_caps(vision=False, code=True),
    },
    {
        "slug": "meta-llama/llama-4-maverick",
        "display_name": "Llama 4 Maverick",
        "provider": "Meta",
        "description_ru": "Открытая мощная модель Meta для сложных диалогов и творческих задач.",
        "input_price_per_mn": Decimal("45"),
        "output_price_per_mn": Decimal("180"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "mistralai/mistral-large-2512",
        "display_name": "Mistral Large 3",
        "provider": "Mistral",
        "description_ru": "Флагман Mistral для бизнес-текстов, анализа и европейских языков.",
        "input_price_per_mn": Decimal("150"),
        "output_price_per_mn": Decimal("450"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "anthropic/claude-opus-4.7",
        "display_name": "Claude Opus 4.7",
        "provider": "Anthropic",
        "description_ru": "Топовый Claude Opus: максимальное качество рассуждений, код, документы, мультимодальный вход.",
        "input_price_per_mn": Decimal("1500"),
        "output_price_per_mn": Decimal("7500"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "anthropic/claude-opus-4.6",
        "display_name": "Claude Opus 4.6",
        "provider": "Anthropic",
        "description_ru": "Флагман Opus перед 4.7: сложные проекты, юридические и научные тексты.",
        "input_price_per_mn": Decimal("1200"),
        "output_price_per_mn": Decimal("6000"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "anthropic/claude-sonnet-4.5",
        "display_name": "Claude Sonnet 4.5",
        "provider": "Anthropic",
        "description_ru": "Улучшенный Sonnet: быстрее Opus, сильнее в коде и длинных задачах, чем 4 серия.",
        "input_price_per_mn": Decimal("900"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-5.5",
        "display_name": "GPT-5.5",
        "provider": "OpenAI",
        "description_ru": "Следующий флагманский ряд GPT-5 после 5.4: сложная логика, инструменты, изображения и файлы на входе.",
        "input_price_per_mn": Decimal("900"),
        "output_price_per_mn": Decimal("5400"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-5.5-pro",
        "display_name": "GPT-5.5 Pro",
        "provider": "OpenAI",
        "description_ru": "Pro-вариант GPT-5.5 для тяжёлых промышленных сценариев и более глубокой обработки.",
        "input_price_per_mn": Decimal("2700"),
        "output_price_per_mn": Decimal("8100"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-5.4-pro",
        "display_name": "GPT-5.4 Pro",
        "provider": "OpenAI",
        "description_ru": "Усиленный GPT-5.4 для энтерпрайза: максимальный запас качества перед mini/nano.",
        "input_price_per_mn": Decimal("1800"),
        "output_price_per_mn": Decimal("7200"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-5.4-mini",
        "display_name": "GPT-5.4 Mini",
        "provider": "OpenAI",
        "description_ru": "Урезанный GPT-5.4: экономия на токенах при достаточно высокой точности для большинства задач.",
        "input_price_per_mn": Decimal("112.5"),
        "output_price_per_mn": Decimal("450"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "deepseek/deepseek-r1-0528",
        "display_name": "DeepSeek R1 (reasoning)",
        "provider": "DeepSeek",
        "description_ru": "Модель с явным рассуждением (chain-of-thought): математика, физика, конкурентные задачи.",
        "input_price_per_mn": Decimal("180"),
        "output_price_per_mn": Decimal("720"),
        "fixed_price": None,
        **_caps(vision=False, code=True),
    },
    {
        "slug": "x-ai/grok-4",
        "display_name": "Grok 4",
        "provider": "xAI",
        "description_ru": "Флагман xAI с доступом к свежей информации (по возможностям провайдера) и файлам.",
        "input_price_per_mn": Decimal("600"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "x-ai/grok-4-fast",
        "display_name": "Grok 4 Fast",
        "provider": "xAI",
        "description_ru": "Ускоренный Grok 4 для интерактивных приложений без лишней задержки.",
        "input_price_per_mn": Decimal("375"),
        "output_price_per_mn": Decimal("2250"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "moonshotai/kimi-k2.6",
        "display_name": "Kimi K2.6",
        "provider": "Moonshot AI",
        "description_ru": "Сильная мультиязычная модель серии Kimi для длинных контекстов и ассистируемой работы.",
        "input_price_per_mn": Decimal("285"),
        "output_price_per_mn": Decimal("1140"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "z-ai/glm-4.6",
        "display_name": "GLM 4.6",
        "provider": "Z.AI",
        "description_ru": "Компактный и мощный GLM для китайско-английского бизнес-контура и технических задач.",
        "input_price_per_mn": Decimal("45"),
        "output_price_per_mn": Decimal("180"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "google/gemini-3.1-pro-preview",
        "display_name": "Gemini 3.1 Pro (preview)",
        "provider": "Google",
        "description_ru": "Тяжёлый предпросмотр Gemini 3.1 для сложного анализа, мультимодальности и кода.",
        "input_price_per_mn": Decimal("450"),
        "output_price_per_mn": Decimal("3150"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "mistralai/mistral-medium-3-5",
        "display_name": "Mistral Medium 3.5",
        "provider": "Mistral",
        "description_ru": "Свежее обновление Medium: качество между Small и frontier, текст и vision.",
        "input_price_per_mn": Decimal("120"),
        "output_price_per_mn": Decimal("540"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "perplexity/sonar-pro",
        "display_name": "Sonar Pro (Perplexity)",
        "provider": "Perplexity",
        "description_ru": "Поиско-ориентированный режим Sonar с углублённым ответом (поиск в сети — при доступности).",
        "pricing_note_ru": "Может взиматься надбавка за web-поиск; итог — по фактическому списанию после запроса.",
        "input_price_per_mn": Decimal("225"),
        "output_price_per_mn": Decimal("900"),
        "fixed_price": None,
        **_caps(),
    },
    {
        "slug": "openai/gpt-audio",
        "display_name": "GPT Audio",
        "provider": "OpenAI",
        "description_ru": "Работа с речью и звуком: ответы с учётом аудио-режима. Для синтеза речи и звука здесь доступны модели OpenAI Audio и Google Lyria (отдельные провайдеры голоса недоступны в этом приложении).",
        "pricing_note_ru": (
            "Аудио/речь: тариф может включать длительность выходного звука и отличаться от чисто текстового чата. "
            "Ниже — ориентиры в ₽/1M токенов при наличии токенового usage."
        ),
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(vision=False, speech=True, music=True, code=True),
    },
    {
        "slug": "openai/gpt-audio-mini",
        "display_name": "GPT Audio Mini",
        "provider": "OpenAI",
        "description_ru": "Компактная модель OpenAI для ответов с аудио (речь/звук).",
        "pricing_note_ru": (
            "Как у GPT Audio: учитывайте отдельные ставки на аудио у провайдера. "
            "Цифры в таблице — ориентир по полям prompt/completion в API и наценке сервиса."
        ),
        "input_price_per_mn": Decimal("180"),
        "output_price_per_mn": Decimal("720"),
        "fixed_price": None,
        **_caps(vision=False, speech=True, music=True, code=True),
    },
    {
        "slug": "openai/gpt-4o-audio-preview",
        "display_name": "GPT-4o Audio",
        "provider": "OpenAI",
        "description_ru": "Полноразмерный GPT-4o с аудиовыходом (предпросмотр OpenAI).",
        "pricing_note_ru": (
            "В API указаны отдельные ставки для аудио-токенов; итог может быть выше текстового чата. "
            "Ориентир ниже по prompt/completion."
        ),
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(vision=False, speech=True, music=True, code=True),
    },
    {
        "slug": "mistralai/voxtral-small-24b-2507",
        "display_name": "Voxtral Small 24B",
        "provider": "Mistral",
        "description_ru": "Модель с уклоном в голос: аудио на входе, ответ текстом (мультимодальная речь в чате).",
        "pricing_note_ru": "Ориентир ₽/1M из актуального тарифа; голос увеличивает «эквивалент» входных токенов.",
        "input_price_per_mn": Decimal("30"),
        "output_price_per_mn": Decimal("90"),
        "fixed_price": None,
        **_caps(vision=False, speech=True, code=True),
    },
    {
        "slug": "google/gemini-2.5-flash-lite",
        "display_name": "Gemini 2.5 Flash Lite",
        "provider": "Google",
        "description_ru": "Лёгкий Flash: текст, картинки, файл, аудио и видео на входе — ответ текстом; удобно для голосовых и мультимодальных сценариев.",
        "pricing_note_ru": "Аудио и видео на входе считаются по тарифам Gemini у провайдера.",
        "input_price_per_mn": Decimal("30"),
        "output_price_per_mn": Decimal("120"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "google/gemini-3.1-flash-lite-preview",
        "display_name": "Gemini 3.1 Flash Lite (preview)",
        "provider": "Google",
        "description_ru": "Предпросмотр лёгкой Gemini 3.1: аудио/видео/файлы на входе, ответ текстом — удобный голосовой сценарий.",
        "pricing_note_ru": "Аудиодорожка на входе тарифицируется по полям audio в тарифе провайдера и наценке сервиса.",
        "input_price_per_mn": Decimal("22.5"),
        "output_price_per_mn": Decimal("90"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "google/gemini-3.1-flash-lite",
        "display_name": "Gemini 3.1 Flash Lite",
        "provider": "Google",
        "description_ru": "Стабильный идентификатор облегчённой Gemini 3.1 Flash с мультимодальным входом (в т.ч. речь).",
        "pricing_note_ru": "См. ставки на audio и image в тарифе при загрузке голоса и вложений.",
        "input_price_per_mn": Decimal("22.5"),
        "output_price_per_mn": Decimal("90"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "google/gemini-2.5-flash-lite-preview-09-2025",
        "display_name": "Gemini 2.5 Flash Lite (preview Sep 2025)",
        "provider": "Google",
        "description_ru": "Экономичный превью-билд Flash Lite с поддержкой аудио/файлов как у старшей линейки.",
        "pricing_note_ru": "Выход текстовый; голос на входе считается через мультимодальные тарифы.",
        "input_price_per_mn": Decimal("36"),
        "output_price_per_mn": Decimal("45"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "google/gemini-2.0-flash-001",
        "display_name": "Gemini 2.0 Flash 001",
        "provider": "Google",
        "description_ru": "Проверенный Gemini 2.0 Flash в стабильном идентификаторе для мультимодального ассистента.",
        "pricing_note_ru": "Аудио и видео на входе — по тарифу Gemini 2.0 Flash у провайдера.",
        "input_price_per_mn": Decimal("15"),
        "output_price_per_mn": Decimal("60"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "xiaomi/mimo-v2.5",
        "display_name": "Xiaomi MiMo 2.5",
        "provider": "Xiaomi",
        "description_ru": "Открытая мультимодальная серия MiMo: аудио и видео на входе для богатых сценариев ассистента.",
        "pricing_note_ru": "При наличии ставок для аудио в тарифе таблица обновится по API провайдера.",
        "input_price_per_mn": Decimal("18"),
        "output_price_per_mn": Decimal("72"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "xiaomi/mimo-v2-omni",
        "display_name": "Xiaomi MiMo 2 Omni",
        "provider": "Xiaomi",
        "description_ru": "Omni-вариант MiMo для одновременной работы с речью и визуальным контекстом.",
        "pricing_note_ru": "Сложный мультимодальный тариф; итог зависит от фактического использования и вложений.",
        "input_price_per_mn": Decimal("24"),
        "output_price_per_mn": Decimal("96"),
        "fixed_price": None,
        **_caps(vision=True, speech=True, code=True),
    },
    {
        "slug": "google/lyria-3-pro-preview",
        "display_name": "Lyria 3 Pro",
        "provider": "Google",
        "description_ru": "Генерация музыки и аудио (Google Lyria 3 Pro) с аудиовыходом.",
        "pricing_note_ru": (
            "Для Lyria часто нет простого token pricing в API — ставки ниже заданы вручную как ориентир. "
            "Если по токенам в ответе суммы нет (или она нулевая), списывается минимальная фиксированная сумма за запрос (`fixed_price`), "
            "чтобы запрос не проходил бесплатно."
        ),
        "input_price_per_mn": Decimal("300"),
        "output_price_per_mn": Decimal("6000"),
        "fixed_price": Decimal("35"),
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "google/lyria-3-clip-preview",
        "display_name": "Lyria 3 Clip",
        "provider": "Google",
        "description_ru": "Короткие аудиофрагменты / клипы на базе Lyria 3 (предпросмотр).",
        "pricing_note_ru": (
            "Как у Lyria 3 Pro: ориентир ₽/1M; при отсутствии токенов в usage — минимум за запрос (`fixed_price`)."
        ),
        "input_price_per_mn": Decimal("250"),
        "output_price_per_mn": Decimal("4500"),
        "fixed_price": Decimal("22"),
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "anthropic/claude-haiku-4.5",
        "display_name": "Claude Haiku 4.5 (музыка в чате)",
        "provider": "Anthropic",
        "description_ru": "Быстрый Claude для **текстовой** работы с музыкой: тексты песен, аранжировки словами, разбор жанров (без аудиовыхода).",
        "pricing_note_ru": "Те же токены chat completions, что у обычного Haiku; в категории «Музыка» для удобства подборки.",
        "input_price_per_mn": Decimal("300"),
        "output_price_per_mn": Decimal("1500"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "openai/gpt-4o-mini",
        "display_name": "GPT-4o mini (музыка в чате)",
        "provider": "OpenAI",
        "description_ru": "Компактный GPT‑4o: сценарии, битбокс-текстом, описание звука, структура трека (**только текст**).",
        "pricing_note_ru": "Оплата как у обычного gpt-4o-mini в чате.",
        "input_price_per_mn": Decimal("45"),
        "output_price_per_mn": Decimal("180"),
        "fixed_price": None,
        **_caps(vision=True, music=True, code=True),
    },
    {
        "slug": "mistralai/mistral-small-3.2-24b-instruct",
        "display_name": "Mistral Small 3.2 (музыка в чате)",
        "provider": "Mistral",
        "description_ru": "Небольшая Mistral для текстовых музыкальных задач: промпты для Lyria, куплеты, метафоры (**без генерации аудио**).",
        "pricing_note_ru": "Токены чата; ориентир из актуального тарифа.",
        "input_price_per_mn": Decimal("22.5"),
        "output_price_per_mn": Decimal("60"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "anthropic/claude-3.5-haiku",
        "display_name": "Claude 3.5 Haiku (музыка в чате)",
        "provider": "Anthropic",
        "description_ru": "Быстрый Claude 3.5 Haiku для правок текстов песен, ударных схем и жанровых подсказок (только текст).",
        "pricing_note_ru": "Оплата по токенам chat completions как у обычного Haiku.",
        "input_price_per_mn": Decimal("240"),
        "output_price_per_mn": Decimal("1200"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "google/gemma-3-27b-it",
        "display_name": "Gemma 3 27B IT (музыка в чате)",
        "provider": "Google",
        "description_ru": "Открытая Gemma 27B с vision — удобно для обложек промптов и лирики (аудиона выхода нет).",
        "pricing_note_ru": "Токены чата; таблица обновляется по тарифам провайдера.",
        "input_price_per_mn": Decimal("15"),
        "output_price_per_mn": Decimal("60"),
        "fixed_price": None,
        **_caps(vision=True, music=True, code=True),
    },
    {
        "slug": "openai/gpt-4.1-nano",
        "display_name": "GPT-4.1 nano (музыка в чате)",
        "provider": "OpenAI",
        "description_ru": "Самый дешёвый слой GPT-4.1 для черновиков текстов песен и микротюнинга промптов под Lyria.",
        "pricing_note_ru": "Счёт по токенам как у обычного gpt-4.1-nano.",
        "input_price_per_mn": Decimal("15"),
        "output_price_per_mn": Decimal("60"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "meta-llama/llama-3.3-70b-instruct",
        "display_name": "Llama 3.3 70B Instruct (музыка в чате)",
        "provider": "Meta",
        "description_ru": "Открытая Llama 70B для текстовой работы с альбомами (описания жанров, куплеты без аудиовыхода).",
        "pricing_note_ru": "Платный slug (не :free); см. pricing в каталоге.",
        "input_price_per_mn": Decimal("72"),
        "output_price_per_mn": Decimal("72"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "deepseek/deepseek-chat-v3.1",
        "display_name": "DeepSeek Chat V3.1 (музыка в чате)",
        "provider": "DeepSeek",
        "description_ru": "Универсальный чат DeepSeek для структурирования текстов песен и анализа гармонии словами.",
        "pricing_note_ru": "Оплата по токенам; см. актуальные ставки в таблице.",
        "input_price_per_mn": Decimal("30"),
        "output_price_per_mn": Decimal("120"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "mistralai/ministral-8b-2512",
        "display_name": "Ministral 8B (музыка в чате)",
        "provider": "Mistral",
        "description_ru": "Ультралёгкая Mistral для быстрых итераций текстов вокала и бэков (только текст).",
        "pricing_note_ru": "Минимальный чек на 1M токенов среди линейки Mistral.",
        "input_price_per_mn": Decimal("7.5"),
        "output_price_per_mn": Decimal("22.5"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "perplexity/sonar",
        "display_name": "Sonar (Perplexity; музыка в чате)",
        "provider": "Perplexity",
        "description_ru": "Базовый Sonar с поиском: факты про артистов, релизы, историю жанров перед генерацией промптов Lyria.",
        "pricing_note_ru": "Учитывайте плату за вызов поиска помимо токенов, если включён web.",
        "input_price_per_mn": Decimal("15"),
        "output_price_per_mn": Decimal("60"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "openai/gpt-5-nano",
        "display_name": "GPT-5 nano (музыка в чате)",
        "provider": "OpenAI",
        "description_ru": "Самый экономичный GPT-5 класса для черновиков микротекстов, тегов стиля и JSON-структур промптов под Lyria.",
        "pricing_note_ru": "Счёт как у обычного gpt-5-nano в тарифной таблице.",
        "input_price_per_mn": Decimal("75"),
        "output_price_per_mn": Decimal("300"),
        "fixed_price": None,
        **_caps(vision=False, music=True, code=True),
    },
    {
        "slug": "openai/whisper-1",
        "display_name": "Whisper (транскрипция)",
        "provider": "OpenAI",
        "description_ru": (
            "Распознавание речи в текст через отдельный API транскрипции. "
            "Голосовой ввод в чате на сервере по умолчанию использует этот режим."
        ),
        "pricing_note_ru": (
            "Отделён от токенов чата: единицы оплаты задаёт провайдер. "
            "Ниже — ориентир в ₽; уточняйте по фактическому списанию."
        ),
        "input_price_per_mn": Decimal("90"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "openai/gpt-4o-transcribe",
        "display_name": "GPT-4o Transcribe",
        "provider": "OpenAI",
        "description_ru": "Высококачественная транскрипция в режиме распознавания речи (отдельный API).",
        "pricing_note_ru": "Стоимость по правилам STT у провайдера; цифры в таблице — ориентир.",
        "input_price_per_mn": Decimal("750"),
        "output_price_per_mn": Decimal("3000"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "openai/gpt-4o-mini-transcribe",
        "display_name": "GPT-4o mini Transcribe",
        "provider": "OpenAI",
        "description_ru": "Более экономичная транскрипция на базе GPT‑4o mini (тот же API транскрипций).",
        "pricing_note_ru": "Режим транскрипции; ориентир из актуального тарифа.",
        "input_price_per_mn": Decimal("375"),
        "output_price_per_mn": Decimal("1500"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "openai/whisper-large-v3",
        "display_name": "Whisper Large v3",
        "provider": "OpenAI",
        "description_ru": "Whisper Large v3 — максимальное качество распознавания (transcription API).",
        "pricing_note_ru": "Для STT в тарифах часто не «₽ за 1M токенов чата» — ориентируйтесь на фактическое списание.",
        "input_price_per_mn": Decimal("120"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "openai/whisper-large-v3-turbo",
        "display_name": "Whisper Large v3 Turbo",
        "provider": "OpenAI",
        "description_ru": "Ускоренный Whisper Large v3 для длинных файлов.",
        "pricing_note_ru": "STT; детали — в тарифе провайдера.",
        "input_price_per_mn": Decimal("80"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "google/chirp-3",
        "display_name": "Google Chirp 3 (транскрипция)",
        "provider": "Google",
        "description_ru": "Google Chirp 3 для распознавания речи (транскрипция).",
        "pricing_note_ru": "Тариф STT от Google; цифры в таблице — ориентир.",
        "input_price_per_mn": Decimal("150"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "amazon/nova-micro-v1",
        "display_name": "Amazon Nova Micro (STT)",
        "provider": "Amazon",
        "description_ru": "Минимальный по стоимости Amazon Nova для речи в текст (при доступности модели у провайдера).",
        "pricing_note_ru": "Проверьте доступность Amazon STT для вашего ключа; ниже — ориентир в таблице тарифов.",
        "input_price_per_mn": Decimal("60"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "amazon/nova-lite-v1",
        "display_name": "Amazon Nova Lite (STT)",
        "provider": "Amazon",
        "description_ru": "Баланс качества и цены Amazon Nova Lite для распознавания речи.",
        "pricing_note_ru": "При ошибке 404/400 смените модель на Whisper/Chirp или проверьте совместимость ключа.",
        "input_price_per_mn": Decimal("90"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "amazon/nova-pro-v1",
        "display_name": "Amazon Nova Pro (STT)",
        "provider": "Amazon",
        "description_ru": "Про-уровень Amazon Nova для качественного распознавания речи.",
        "pricing_note_ru": "Цифры в таблице — ориентир; точный тариф — у провайдера AWS/Nova.",
        "input_price_per_mn": Decimal("120"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "amazon/nova-premier-v1",
        "display_name": "Amazon Nova Premier (STT)",
        "provider": "Amazon",
        "description_ru": "Премиальный STT Amazon Nova (при поддержке маршрутизацией).",
        "pricing_note_ru": "Сверяйте с фактическим списанием; ориентир в таблице обновляется при обновлении тарифов.",
        "input_price_per_mn": Decimal("150"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "amazon/nova-2-lite-v1",
        "display_name": "Amazon Nova 2 Lite (STT)",
        "provider": "Amazon",
        "description_ru": "Новая экономичная линейка Nova 2 Lite для быстрых расшифровок.",
        "pricing_note_ru": "Отдельный API транскрипции; при ошибке выберите другую модель распознавания.",
        "input_price_per_mn": Decimal("90"),
        "output_price_per_mn": Decimal("0"),
        "fixed_price": None,
        **_caps(vision=False, transcription=True, code=False),
    },
    {
        "slug": "minimax/minimax-m2",
        "display_name": "MiniMax M2",
        "provider": "MiniMax",
        "description_ru": "Универсальная модель MiniMax (замена устаревшего music-2.0).",
        "pricing_note_ru": "Не только музыка: общий чат и мультимодальность в рамках возможностей модели.",
        "input_price_per_mn": Decimal("76.50"),
        "output_price_per_mn": Decimal("300"),
        "fixed_price": None,
        **_caps(),
    },
]

_FREE_ROUTER_SLUG = "openrouter/free"

# Удаляются из ai_models при init_db; диалоги с этим model_slug перепривязываются на запасной slug.
_REMOVED_OPENROUTER_SLUGS: frozenset[str] = frozenset(
    {
        "openai/sora-2",
        "bytedance/seedance-1.0-pro",
        "google/lyria-2.5-flash",
        "black-forest-labs/flux.2-pro",
        "black-forest-labs/flux.2-flex",
        "minimax/music-2.0",
    }
)
_THREAD_MODEL_FALLBACK_SLUG = "openai/gpt-4o"


def _purge_removed_models_and_rewire_threads(db: Session) -> None:
    for slug in _REMOVED_OPENROUTER_SLUGS:
        row = db.query(AiModel).filter(AiModel.slug == slug).first()
        if row:
            db.delete(row)
    (
        db.query(ChatThread)
        .filter(ChatThread.model_slug.in_(_REMOVED_OPENROUTER_SLUGS))
        .update({ChatThread.model_slug: _THREAD_MODEL_FALLBACK_SLUG}, synchronize_session=False)
    )


def _provider_from_seed_spec(s: dict[str, object]) -> str:
    """Для `openrouter/free` в БД хранится название приложения из OPENROUTER_APP_TITLE."""
    if str(s["slug"]) == _FREE_ROUTER_SLUG:
        return str(get_settings().openrouter_app_title)
    return str(s["provider"])


def _migrate_ai_model_columns(engine) -> None:
    """Добавляет новые колонки возможностей в ai_models (SQLite/Postgres)."""
    insp = inspect(engine)
    if not insp.has_table("ai_models"):
        return
    cols = {c["name"] for c in insp.get_columns("ai_models")}
    dialect = engine.dialect.name

    def sql_bool(b: bool) -> str:
        if dialect == "sqlite":
            return "1" if b else "0"
        return "true" if b else "false"

    additions = [
        ("supports_image_generation", False),
        ("supports_music_generation", False),
        ("supports_video_generation", False),
        ("supports_speech", False),
        ("supports_transcription", False),
        ("supports_coding", True),
        ("is_free", False),
    ]
    with engine.begin() as conn:
        for col, default in additions:
            if col in cols:
                continue
            d = sql_bool(default)
            conn.execute(text(f"ALTER TABLE ai_models ADD COLUMN {col} BOOLEAN DEFAULT {d}"))
        if "description_ru" not in cols:
            if dialect == "sqlite":
                conn.execute(text("ALTER TABLE ai_models ADD COLUMN description_ru TEXT"))
            else:
                conn.execute(text("ALTER TABLE ai_models ADD COLUMN description_ru TEXT"))
        if "pricing_note_ru" not in cols:
            conn.execute(text("ALTER TABLE ai_models ADD COLUMN pricing_note_ru TEXT"))


def _migrate_user_oauth_columns(engine) -> None:
    """Добавляет колонки users: OAuth, is_admin."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        if "vk_user_id" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN vk_user_id VARCHAR(32)"))
        if "yandex_user_id" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN yandex_user_id VARCHAR(64)"))
        if "is_admin" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0"))

    insp = inspect(engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("users")}
    with engine.begin() as conn:
        if "ix_users_vk_user_id" not in idx_names:
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_user_id ON users (vk_user_id)")
            )
        if "ix_users_yandex_user_id" not in idx_names:
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_yandex_user_id ON users (yandex_user_id)")
            )


def _migrate_user_is_admin_to_integer(engine) -> None:
    """PostgreSQL: старая колонка BOOLEAN → INTEGER 0/1. SQLite уже хранит числа."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    if engine.dialect.name != "postgresql":
        return
    col = next((c for c in insp.get_columns("users") if c["name"] == "is_admin"), None)
    if not col or not isinstance(col["type"], sqltypes.Boolean):
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ALTER COLUMN is_admin DROP DEFAULT"))
        conn.execute(
            text(
                "ALTER TABLE users ALTER COLUMN is_admin TYPE INTEGER USING (COALESCE(is_admin::integer, 0))"
            )
        )
        conn.execute(text("ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT 0"))


def _normalize_user_is_admin_to_01(engine) -> None:
    """Только 0 и 1: прочие значения и NULL приводим к 0."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users SET is_admin = 0 WHERE is_admin IS NULL "
                "OR (is_admin <> 0 AND is_admin <> 1)"
            )
        )


def _sync_model_capabilities_from_specs(db: Session) -> None:
    """Подставляет технические флаги из DEFAULT_MODEL_SPECS для известных slug (после обновления кода).

    Описания, pricing_note и is_free не перезаписываем — их меняет админка в БД.
    """
    by_slug = {str(s["slug"]): s for s in DEFAULT_MODEL_SPECS}
    for row in db.query(AiModel).all():
        s = by_slug.get(row.slug)
        if not s:
            continue
        row.supports_vision = bool(s["supports_vision"])
        row.supports_image_generation = bool(s["supports_image_generation"])
        row.supports_music_generation = bool(s["supports_music_generation"])
        row.supports_video_generation = bool(s["supports_video_generation"])
        row.supports_speech = bool(s.get("supports_speech", False))
        row.supports_transcription = bool(s.get("supports_transcription", False))
        row.supports_coding = bool(s["supports_coding"])


_LEGACY_FREE_SLUGS = (
    "meta-llama/llama-3.3-8b-instruct:free",
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-3-12b-it:free",
)


def _merge_legacy_free_models_into_openrouter(db: Session) -> None:
    """Старые отдельные :free slug → один `openrouter/free` без нарушения UNIQUE(slug)."""
    target_slug = "openrouter/free"
    spec = next((s for s in DEFAULT_MODEL_SPECS if str(s["slug"]) == target_slug), None)
    legacy_rows = [
        r
        for s in _LEGACY_FREE_SLUGS
        if (r := db.query(AiModel).filter(AiModel.slug == s).first()) is not None
    ]
    existing_target = db.query(AiModel).filter(AiModel.slug == target_slug).first()
    if existing_target:
        for r in legacy_rows:
            if r.id != existing_target.id:
                db.delete(r)
        return
    if not legacy_rows:
        return
    keep = legacy_rows[0]
    keep.slug = target_slug
    if spec:
        keep.display_name = str(spec["display_name"])
        keep.provider = _provider_from_seed_spec(spec)
        keep.input_price_per_mn = spec["input_price_per_mn"]  # type: ignore[assignment]
        keep.output_price_per_mn = spec["output_price_per_mn"]  # type: ignore[assignment]
        keep.is_free = bool(spec.get("is_free", False))
        keep.supports_vision = bool(spec["supports_vision"])
        keep.supports_image_generation = bool(spec["supports_image_generation"])
        keep.supports_music_generation = bool(spec["supports_music_generation"])
        keep.supports_video_generation = bool(spec["supports_video_generation"])
        keep.supports_speech = bool(spec.get("supports_speech", False))
        keep.supports_transcription = bool(spec.get("supports_transcription", False))
        keep.supports_coding = bool(spec["supports_coding"])
        dr = spec.get("description_ru")
        keep.description_ru = str(dr) if dr is not None else None
        if spec and "pricing_note_ru" in spec:
            pn = spec.get("pricing_note_ru")
            keep.pricing_note_ru = str(pn) if pn is not None else None
    for r in legacy_rows[1:]:
        db.delete(r)


def _migrate_deprecated_model_slugs(db: Session) -> None:
    """OpenRouter периодически снимает старые slug с маршрутизации; подменяем на актуальные."""
    _merge_legacy_free_models_into_openrouter(db)
    replacements: dict[str, dict[str, object]] = {
        "anthropic/claude-3.5-sonnet": {
            "slug": "anthropic/claude-3.7-sonnet",
            "display_name": "Claude 3.7 Sonnet",
            "input_price_per_mn": Decimal("900"),
            "output_price_per_mn": Decimal("4500"),
        },
        "deepseek/deepseek-chat-v3.1": {
            "slug": "deepseek/deepseek-v3.2",
            "display_name": "DeepSeek V3.2",
            "input_price_per_mn": Decimal("75.60"),
            "output_price_per_mn": Decimal("113.40"),
        },
        "openai/gpt-4o": {
            "slug": "openai/gpt-5.4",
            "display_name": "GPT-5.4",
            "input_price_per_mn": Decimal("750"),
            "output_price_per_mn": Decimal("4500"),
        },
        "google/gemini-2.0-flash-001": {
            "slug": "google/gemini-3-flash-preview",
            "display_name": "Gemini 3 Flash",
            "input_price_per_mn": Decimal("150"),
            "output_price_per_mn": Decimal("900"),
        },
    }
    for old_slug, fields in replacements.items():
        row = db.query(AiModel).filter(AiModel.slug == old_slug).first()
        if not row:
            continue
        new_slug = str(fields["slug"])
        conflict = db.query(AiModel).filter(AiModel.slug == new_slug).first()
        if conflict and conflict.id != row.id:
            db.delete(row)
            continue
        row.slug = new_slug
        row.display_name = str(fields["display_name"])
        row.input_price_per_mn = fields["input_price_per_mn"]  # type: ignore[assignment]
        row.output_price_per_mn = fields["output_price_per_mn"]  # type: ignore[assignment]
        if "provider" in fields:
            row.provider = str(fields["provider"])


def _ensure_default_models(db: Session) -> None:
    """Добавляет модели из DEFAULT_MODEL_SPECS, если их ещё нет (например после обновления кода)."""
    known = {row[0] for row in db.query(AiModel.slug).all()}
    for s in DEFAULT_MODEL_SPECS:
        slug = str(s["slug"])
        if slug in known:
            continue
        db.add(
            AiModel(
                slug=slug,
                display_name=str(s["display_name"]),
                provider=_provider_from_seed_spec(s),
                input_price_per_mn=s["input_price_per_mn"],  # type: ignore[arg-type]
                output_price_per_mn=s["output_price_per_mn"],  # type: ignore[arg-type]
                fixed_price=s["fixed_price"],  # type: ignore[arg-type]
                is_active=True,
                supports_vision=bool(s["supports_vision"]),
                supports_image_generation=bool(s["supports_image_generation"]),
                supports_music_generation=bool(s["supports_music_generation"]),
                supports_video_generation=bool(s["supports_video_generation"]),
                supports_speech=bool(s.get("supports_speech", False)),
                supports_transcription=bool(s.get("supports_transcription", False)),
                supports_coding=bool(s["supports_coding"]),
                is_free=bool(s.get("is_free", False)),
                description_ru=str(s["description_ru"])
                if s.get("description_ru") is not None
                else None,
                pricing_note_ru=str(s["pricing_note_ru"])
                if s.get("pricing_note_ru") is not None
                else None,
            )
        )


def _sync_lyria_minimum_fixed_price_from_specs(db: Session) -> None:
    """Если Lyria в БД без fixed_price, подставляем из сидов (иначе при пустом usage списание 0 ₽)."""
    by_slug = {str(s["slug"]): s for s in DEFAULT_MODEL_SPECS}
    for slug in ("google/lyria-3-pro-preview", "google/lyria-3-clip-preview"):
        spec = by_slug.get(slug)
        if not spec:
            continue
        fp = spec.get("fixed_price")
        if fp is None:
            continue
        row = db.query(AiModel).filter(AiModel.slug == slug).first()
        if not row:
            continue
        if row.fixed_price is None or row.fixed_price == 0:
            row.fixed_price = fp  # type: ignore[assignment]


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_ai_model_columns(engine)
    _migrate_user_oauth_columns(engine)
    _migrate_user_is_admin_to_integer(engine)
    _normalize_user_is_admin_to_01(engine)
    with SessionLocal() as db:
        if db.query(AiModel).count() == 0:
            seed_models(db)
        _migrate_deprecated_model_slugs(db)
        db.flush()
        _ensure_default_models(db)
        _sync_lyria_minimum_fixed_price_from_specs(db)
        _purge_removed_models_and_rewire_threads(db)
        _sync_model_capabilities_from_specs(db)
        db.commit()


def seed_models(db: Session) -> None:
    for s in DEFAULT_MODEL_SPECS:
        db.add(
            AiModel(
                slug=str(s["slug"]),
                display_name=str(s["display_name"]),
                provider=_provider_from_seed_spec(s),
                input_price_per_mn=s["input_price_per_mn"],  # type: ignore[arg-type]
                output_price_per_mn=s["output_price_per_mn"],  # type: ignore[arg-type]
                fixed_price=s["fixed_price"],  # type: ignore[arg-type]
                is_active=True,
                supports_vision=bool(s["supports_vision"]),
                supports_image_generation=bool(s["supports_image_generation"]),
                supports_music_generation=bool(s["supports_music_generation"]),
                supports_video_generation=bool(s["supports_video_generation"]),
                supports_speech=bool(s.get("supports_speech", False)),
                supports_transcription=bool(s.get("supports_transcription", False)),
                supports_coding=bool(s["supports_coding"]),
                is_free=bool(s.get("is_free", False)),
                description_ru=str(s["description_ru"])
                if s.get("description_ru") is not None
                else None,
                pricing_note_ru=str(s["pricing_note_ru"])
                if s.get("pricing_note_ru") is not None
                else None,
            )
        )
