# AI Proxy

Веб-приложение для доступа к текстовым и мультимодальным моделям через [OpenRouter](https://openrouter.ai/): учёт токенов, внутренний баланс в рублях, вход через **Яндекс ID** и **VK ID** (OAuth2), пополнение через ЮKassa. Фронтенд — статические HTML/CSS/JS, бэкенд — **FastAPI**, база — **SQLite** по умолчанию.

## Требования

- **Python 3.10+**
- Ключ API OpenRouter (для запросов к моделям)

## Установка

1. Клонируйте репозиторий и перейдите в каталог проекта.

2. Создайте виртуальное окружение (рекомендуется):

   ```bash
   python -m venv .venv
   ```

   Активация:

   - Windows (PowerShell): `.venv\Scripts\Activate.ps1`
   - Linux/macOS: `source .venv/bin/activate`

3. Установите зависимости:

   ```bash
   pip install -r requirements.txt
   ```

4. Создайте файл `.env` в **корне проекта** (рядом с `requirements.txt`):

   ```bash
   copy .env.example .env
   ```

   На Linux/macOS: `cp .env.example .env`

   Приложение читает `.env` из корня или из каталога `server/` (если положить файл туда).

## Переменные `.env`

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | Строка подключения SQLAlchemy. По умолчанию SQLite: файл БД создаётся автоматически при первом запуске (см. раздел «База данных»). |
| `SESSION_SECRET` или `NEXTAUTH_SECRET` | Секрет для подписи cookie-сессии. В продакшене задайте длинную случайную строку (например: `openssl rand -base64 32`). |
| `OPENROUTER_API_KEY` | Ключ с [openrouter.ai/keys](https://openrouter.ai/keys). **Обязателен** для чата. |
| `OPENROUTER_SITE_URL` | URL сайта для заголовка OpenRouter (часто `http://localhost:8000` в разработке). |
| `OPENROUTER_APP_TITLE` | Название приложения в заголовках OpenRouter. |
| `YOOKASSA_ENABLED` | `true` — включены создание платежа и webhook; нужны `YOOKASSA_*`. `false` — кнопка пополнения скрыта. |
| `YOOKASSA_SHOP_ID` | Идентификатор магазина ЮKassa. |
| `YOOKASSA_SECRET_KEY` | Секретный ключ ЮKassa. |
| `NEXT_PUBLIC_APP_URL` | Публичный базовый URL приложения без завершающего слэша: редиректы после оплаты ЮKassa и **OAuth callback** должны совпадать с зарегистрированными у провайдера. |
| `YANDEX_OAUTH_CLIENT_ID` | Идентификатор приложения в [кабинете Yandex OAuth](https://oauth.yandex.ru/). Если задан вместе с секретом — на `/login` появляется кнопка «Яндекс ID». |
| `YANDEX_OAUTH_CLIENT_SECRET` | Секрет приложения Яндекса. |
| `VK_OAUTH_CLIENT_ID` | ID приложения VK (раздел мини-приложений / Standalone на [dev.vk.com](https://dev.vk.com/)). |
| `VK_OAUTH_CLIENT_SECRET` или `VK_OAUTH_SECRET_KEY` | Защищённый ключ приложения VK. |
| `OAUTH_NEW_USER_BALANCE` | Начальный баланс (₽) при **первом** входе через Яндекс или VK (по умолчанию `0`). |
| `OPENROUTER_USD_RUB` | Для скрипта `scripts/sync_openrouter_prices.py`: курс условных USD к ₽ при пересчёте цен из API (по умолчанию `100`, если не задано в окружении). |
| `PRICING_MARKUP_MULT` | Множитель наценки после пересчёта курса (по умолчанию `3`). |
| `INTERNAL_API_SECRET` | Общий секрет для вызовов API с других сервисов (например Telegram-бота) вместе с заголовком `X-User-Id`. |

Минимальный набор для локальной проверки без оплат:

- `OPENROUTER_API_KEY`
- `SESSION_SECRET` (или оставить значение по умолчанию только для dev)
- `YANDEX_OAUTH_*` и/или `VK_OAUTH_*` для входа на `/login`

## Запуск

Из **корня репозитория** (где лежит `requirements.txt`):

```bash
python -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте в браузере:

- [http://127.0.0.1:8000](http://127.0.0.1:8000) — чат  
- [http://127.0.0.1:8000/login](http://127.0.0.1:8000/login) — вход  
- [http://127.0.0.1:8000/tariffs](http://127.0.0.1:8000/tariffs) — список моделей и цен  

Войдите через **Яндекс ID** или **VK ID**, если в `.env` заданы соответствующие переменные OAuth.

### Администраторы

Права админки (`/admin`, `/api/admin/*`) задаются полем **`users.is_admin`**: только **`0`** (нет) или **`1`** (админ). Войдите через OAuth, возьмите **`id`** из ответа `GET /api/auth/me` (или из таблицы `users`) и выполните:

```sql
UPDATE users SET is_admin = 1 WHERE id = '<uuid-пользователя>';
```

Для SQLite можно открыть файл БД из `DATABASE_URL` любым клиентом; для PostgreSQL — через `psql` или панель хостинга.

## Вход через Яндекс ID и VK ID

Реализован стандартный **OAuth 2.0 authorization code**: старт → экран провайдера → редирект на callback этого приложения → cookie-сессия.

1. Укажите **`NEXT_PUBLIC_APP_URL`** так же, как реальный URL сайта (включая `https` в продакшене).
2. В кабинете Яндекса и VK зарегистрируйте redirect URI **точно** так:
   - `https://<ваш-домен>/api/auth/oauth/yandex/callback`
   - `https://<ваш-домен>/api/auth/oauth/vk/callback`
   https://id.vk.ru/about/business/go/accounts/356823/apps
   https://oauth.yandex.ru/client/c4f0da23a3784fba9fbec1162183b1ad
3. Пропишите `YANDEX_OAUTH_*` и/или `VK_OAUTH_*` в **сохранённый** файл `.env` в корне репозитория и перезапустите процесс uvicorn. После этого откройте `/login` с жёстким обновлением (Ctrl+F5).

На `/login` VK и Яндекс ведут себя одинаково: только кнопка и переход на OAuth (без виджета One Tap). В [кабинете VK ID](https://id.vk.com/) в списке доверенных redirect URI укажите **полный** путь **`https://<домен>/api/auth/oauth/vk/callback`**. Если на экране VK появляется «Ошибка загрузки», проверьте этот URI и откройте сайт в приватном окне (блокировщики/сеть иногда мешают; сервер использует `id.vk.com`, PKCE).

Чтобы выдать доступ к админке, выставьте **`is_admin`** в БД — см. раздел «Администраторы» выше.

## База данных

По умолчанию используется **SQLite**. Файл создаётся при старте приложения (таблицы и начальный набор моделей в `ai_models` подставляются автоматически, если таблица пустая). Путь к файлу задаётся через `DATABASE_URL`; для относительного пути каталог разрешается относительно пакета `server`, обычно это `server/data/app.db`.

Для продакшена можно указать PostgreSQL в `DATABASE_URL` в формате SQLAlchemy и при необходимости скорректировать модели/миграции под вашу среду.

## API для интеграций

Универсальная точка чата: `POST /api/v1/messages` (JSON или `multipart/form-data` с полем `audio` для голоса).

Вызов «от имени сервиса» (бот, скрипт): заголовки `X-Internal-Secret: <INTERNAL_API_SECRET>` и `X-User-Id: <id пользователя в БД>`.

Справка по маршрутам доступна после запуска: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) (Swagger UI).

## Тарифы ₽ / 1M токенов (вход / выход)

Ориентир для моделей из [каталога OpenRouter](https://openrouter.ai/models): API `GET https://openrouter.ai/api/v1/models` отдаёт поле `pricing.prompt` и `pricing.completion` (**USD за один токен**). В прокси хранится **₽ за 1M токенов**:

`RUB_per_1M = (USD_per_token * 1_000_000) * OPENROUTER_USD_RUB * PRICING_MARKUP_MULT`

По умолчанию `OPENROUTER_USD_RUB=100`, `PRICING_MARKUP_MULT=3` (задаются в `.env` для скрипта синхронизации).

Обновить цены в **таблице `ai_models`** из актуального API (из корня репозитория):

```bash
python scripts/sync_openrouter_prices.py
```

`--dry-run` — только печать без записи в БД. Модели **без** числового token pricing в API (часть видео, исчезнувшие slug) в скрипте пропускаются — для них цены в `server/database.py` заданы вручную как ориентир.

В каталоге и админке у каждой строки `ai_models` задаются флаги типа: **видео** (отдельный API `POST /videos`), **транскрипция** (`/audio/transcriptions`, в чате не выбирается), **речь в чате** (модели с аудиовыходом в completions), **музыка** (например Lyria). Чат и тарифы группируют карточки по этим признакам.

**Видео (Veo, Sora, Seedance)** и устаревшие slug без маршрутизации в OpenRouter удаляются при старте (`_REMOVED_OPENROUTER_SLUGS` в `server/database.py`). **Речь в чате:** **openai/gpt-audio**, **gpt-audio-mini**, **gpt-4o-audio-preview**. **Музыка:** **google/lyria-3-***. **Транскрипция:** например **openai/whisper-1** (голосовой ввод в UI идёт через этот slug на сервере). Отдельного **ElevenLabs** в каталоге OpenRouter нет. Генерация картинок: **FLUX** заменены на **openai/gpt-5-image** и **google/gemini-3-pro-image-preview**.
