# II Proxy

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
| `OPENROUTER_APP_TITLE` | Название приложения в заголовках OpenRouter (`X-Title`). |
| `YOOKASSA_ENABLED` | `true` — включены создание платежа и webhook; нужны `YOOKASSA_*`. `false` — кнопка пополнения скрыта. |
| `YOOKASSA_SHOP_ID` | Идентификатор магазина ЮKassa. |
| `YOOKASSA_SECRET_KEY` | Секретный ключ ЮKassa. |
| `NEXT_PUBLIC_APP_URL` | Публичный базовый URL приложения без завершающего слэша: редиректы после оплаты ЮKassa, **OAuth callback** (должен совпадать с URL у провайдера), а также заголовок **`HTTP-Referer`** в запросах к OpenRouter. |
| `YANDEX_OAUTH_CLIENT_ID` | Идентификатор приложения в [кабинете Yandex OAuth](https://oauth.yandex.ru/). Если задан вместе с секретом — на `/login` появляется кнопка «Яндекс ID». |
| `YANDEX_OAUTH_CLIENT_SECRET` | Секрет приложения Яндекса. |
| `VK_OAUTH_CLIENT_ID` | ID приложения VK (раздел мини-приложений / Standalone на [dev.vk.com](https://dev.vk.com/)). |
| `VK_OAUTH_CLIENT_SECRET` или `VK_OAUTH_SECRET_KEY` | Защищённый ключ приложения VK. |
| `OAUTH_NEW_USER_BALANCE` | Начальный баланс (₽) при **первом** входе через Яндекс или VK (по умолчанию `0`). |
| `OPENROUTER_USD_RUB` | Для скрипта `scripts/sync_openrouter_prices.py`: курс условных USD к ₽ при пересчёте цен из API (по умолчанию `100`, если не задано в окружении). |
| `PRICING_MARKUP_MULT` | Множитель наценки после пересчёта курса (по умолчанию `3`). |
| `INTERNAL_API_SECRET` | Общий секрет для вызовов API с других сервисов (например Telegram-бота) вместе с заголовком `X-User-Id`. |

**ЮKassa на сервере (не только `.env`).** Включите **`YOOKASSA_ENABLED=true`** (без этого оплата отключена), плюс **`YOOKASSA_SHOP_ID`** и **`YOOKASSA_SECRET_KEY`**. В [личном кабинете ЮKassa](https://yookassa.ru/) задайте **URL HTTP-уведомлений**: `https://<ваш-домен>/api/payments/webhook` — **без** хвостового слэша и с тем же хостом, что у сайта. Проверка: в браузере откройте `https://…/api/payments/webhook` — ожидается JSON вроде `{"ok":true,"paymentsWebhook":true}`; если был только обработчик POST, проверка URL в кабинете часто показывала **404**.  
У **Traefik** у `iiproxy-web` перечислены конкретные **Host** (`iiproxy.ru`, `www.iiproxy.ru`, `ii-proxy.ru`, `www.ii-proxy.ru`). Открытие сайта по **другому имени** или по **IP с TLS** даёт **404 на стороне Traefik** (маршрут не найден).  
После оплаты (Simple Pay) баланс подтягивается запросом **`/api/payments/sync-simplepay`** в браузере или через **webhook** на `/api/payments/webhook`. Если платёж прошёл, а баланс нет: чаще всего **редирект с ЮKassa ушёл не на тот хост**, где была сессия (например в `.env` указан `https://iiproxy.ru`, а вход был с `www` или с `ii-proxy.ru`) — cookie не совпали, пользователь «гость». В актуальном фронтенде возврат строится от **текущего адреса страницы**. Для уже совершённого платежа можно **войти на том же домене** и в Swagger вызвать `POST /api/payments/sync-simplepay` с телом `{"orderId":"UUID"}` (UUID из параметра `orderId` после оплаты или из кабинета по заказу).

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

### Запуск в Docker на сервере

На сервере нужны **Docker Engine** и **Docker Compose** (плагин `docker compose`).

1. **Клонирование и `.env`**  
   Типичная раскладка на сервере: рядом с `docker-compose.yml` — каталог **`iiproxy.ru`** с этим репозиторием (`Dockerfile`, `requirements.txt`, `server/`, …). Файл **`.env` создаётся в `iiproxy.ru/.env`** (не обязательно рядом с compose). Заполните как минимум `OPENROUTER_API_KEY`, `SESSION_SECRET`, `NEXT_PUBLIC_APP_URL` (публичный `https://…` без слэша в конце), ключи OAuth и при необходимости ЮKassa.  
   Для OAuth в кабинетах провайдеров укажите redirect URI с **реальным** доменом, как в разделе «Вход через Яндекс ID и VK ID».

2. **Данные**  
   В контейнере приложение использует каталог **`/app/data`**: SQLite (`app.db`), при первом старте — копия **`default_model_specs.json`** из образа, загрузки новостей — **`uploads/news`**. В `docker-compose.yml` этот путь смонтирован как **именованный том** `iiproxy_database`, данные переживают пересборку образа.

3. **Сборка и старт только сервиса II Proxy**  
   Команды выполняйте из **каталога, где лежит `docker-compose.yml`** (родительский каталог относительно `iiproxy.ru/`). По умолчанию compose ждёт проект в **`./iiproxy.ru`** — отдельно задавать путь не нужно.

   ```bash
   docker compose build iiproxy-web
   docker compose up -d iiproxy-web
   ```

   Файл `docker-compose.yml` описывает и другие сервисы (Traefik, WordPress и т.д.). Команды выше поднимают **только** `iiproxy-web`. Убедитесь, что:
   - создана **внешняя сеть** `web`, если её ещё нет:  
     `docker network create web`;
   - для сервисов вроде `sergeymarkin-web` пути к `.env` в compose не ломают запуск (или временно закомментируйте лишние сервисы на своей копии файла).

   Если каталог с кодом называется иначе или лежит не `./iiproxy.ru`, в **`.env` рядом с compose** или в shell задайте **`IIPROXY_APP_DIR`** (путь к корню репозитория II Proxy):

   ```bash
   export IIPROXY_APP_DIR=./мой-каталог
   docker compose build iiproxy-web
   docker compose up -d iiproxy-web
   ```

4. **TLS и домен**  
   В репозитории для `iiproxy-web` заданы labels **Traefik** (маршрут по хостам `iiproxy.ru`, `www.iiproxy.ru`, `ii-proxy.ru`, `www.ii-proxy.ru`, HTTPS, ACME). Должен работать контейнер **Traefik** из того же compose и переменная **`EMAIL`** для Let’s Encrypt в окружении compose. Подставьте свои хосты в labels при необходимости.  
   В секции `environment` сервиса **`NEXT_PUBLIC_APP_URL`** должен совпадать с основным публичным URL (сейчас в compose указан `https://iiproxy.ru`); иначе поправьте значение или уберите переопределение и задайте URL только в `.env`.

   Если браузер пишет про **недоверенный сертификат** после перехода на `https://…`: обычно это значит, что **Let’s Encrypt ещё не выпустил** сертификат (Traefik временно отдаёт свой). Проверьте: **`EMAIL`** задаётся при запуске compose; есть каталог **`./traefik`** с файлом **`acme.json`** и правами на запись (`chmod 600 traefik/acme.json`); DNS **A** всех доменных имён указывает на этот сервер; снаружи открыт **443** (нужен для `tlsChallenge`). Логи Traefik: `docker compose logs traefik` (строки `acme`, `certificate`). После изменений: `docker compose up -d traefik iiproxy-web`.

5. **Обновление**  
   В каталоге **`iiproxy.ru`**: `git pull`. Затем из каталога с **`docker-compose.yml`**:

   ```bash
   docker compose build iiproxy-web && docker compose up -d iiproxy-web
   ```

6. **Запуск без полного compose (только образ)**  
   Сборка из **корня репозитория** (`iiproxy.ru/` — там же `Dockerfile`):

   ```bash
   cd iiproxy.ru
   docker build -t iiproxy-web:local .
   docker run -d --name iiproxy-web --restart unless-stopped \
     -v iiproxy_database:/app/data \
     --env-file .env \
     -e DATABASE_URL=sqlite:////app/data/app.db \
     -p 8000:8000 \
     iiproxy-web:local
   ```

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

По умолчанию используется **SQLite**. Файл создаётся при старте приложения (таблицы и начальный набор моделей в `ai_models` подставляются автоматически, если таблица пустая). Путь к файлу задаётся через `DATABASE_URL`; для относительного пути каталог разрешается относительно корня репозитория, обычно это `data/app.db`.

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

**Видео (Veo, Sora, Seedance)** и прочие неактуальные идентификаторы: **записи в `ai_models`, которых нет в `data/default_model_specs.json`, при старте удаляются** (треды перепривязываются на `THREAD_MODEL_FALLBACK_SLUG`). Дополнительно через **`.env`** можно задать **`REMOVED_OPENROUTER_SLUGS`**, чтобы убрать slug без правки JSON. **Речь в чате:** **openai/gpt-audio**, **gpt-audio-mini**. **Музыка:** **google/lyria-3-*** (на OpenRouter доступны как preview-идентификаторы). **Транскрипция:** например **openai/whisper-1** (голосовой ввод в UI идёт через этот slug на сервере). Отдельного **ElevenLabs** в каталоге OpenRouter нет. Генерация картинок в чате: **openai/gpt-5-image** / **gpt-5-image-mini**, **google/gemini-2.5-flash-image** и др.
