"""Популярные модели для избранного (пересечение с каталогом при первом сохранении в БД).
Должно совпадать с DEFAULT_FAVORITE_SLUGS в static/js/model-favorites.js."""

# fmt: off
DEFAULT_FAVORITE_MODEL_SLUGS: tuple[str, ...] = (
    "openai/gpt-4o",
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4",
    "openai/gpt-5.5",
    "deepseek/deepseek-chat-v3.1",
)
# fmt: on

MAX_USER_MODEL_FAVORITES: int = 128
