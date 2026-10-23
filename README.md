# Cluster

Docker-состав всего кластера AlfaHackathon:

```
клиент -> nginx (:80) -> auth-gateway (:8080)
                         ├─ auth-service (:8082)
                         ├─ user-service (:8081) -> postgres (:5433)
                         └─ agent-service (:3000, внутренняя сеть) -> agent-postgres (внутренняя сеть)
```

| Сервис | Порт наружу | Swagger |
|---|---|---|
| nginx | 80 | нет |
| auth-gateway | 8080 | нет |
| auth-service | 8082 | `/auth-service/swagger` (через gateway) |
| user-service | 8081 | `/user-service/swagger` (через gateway) |
| postgres | 5433 | нет (5432 занят на хосте) |
| agent-service | нет | нет; HTTP API `/agent-service/...` через nginx |
| agent-postgres | нет | нет |

## Запуск

```bash
cp .env.example .env   # заполнить GITHUB_TOKEN, AGENT_POSTGRES_PASSWORD и INTERNAL_SECRET
cp ../AgentService/.env.example ../AgentService/.env   # настройки LLM задаются здесь
# GitHub Packages токен для AuthGateway сохраните в игнорируемом .github-token.
docker compose -f docker-compose-dev.yaml up -d --build
```

Репозитории `AuthGateway`, `AuthService`, `UserService`, `CompanyService`, `AgentService` должны
лежать рядом с `cluster`: compose собирает их из соседних каталогов.
Базовая ветка для изменений — `master`. Для production используйте `docker-compose-prod.yaml`.

AgentService запускает миграции при старте и ждёт готовности собственной БД.
Её данные хранятся в отдельном volume `agent-postgres-data`. Порт AgentService
не публикуется на хост: клиент обращается через Gateway, который выставляет identity.

## Переменные (.env)

- `GITHUB_TOKEN` — PAT для NuGet GitHub Packages;
- `GITHUB_USERNAME` — GitHub login владельца PAT для сборки AuthGateway;
- `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE` — настройки токенов (одинаковы у гейтвея и auth-service);
- `SERVICES_ADDRESS`, `AUTH_SERVICE_ADDRESS`, `AGENT_SERVICE_ADDRESS` — downstream гейтвея;
- `POSTGRES_DB/USER/PASSWORD`;
- `INTERNAL_SECRET` — секрет внутренних вызовов user-service <-> auth-service;
- `SWAGGER_ENABLED` — включить/выключить свагер в сервисах.
- `AGENT_POSTGRES_PASSWORD` — отдельный URL-safe пароль БД AgentService (латинские буквы/цифры/`-`/`_`);
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` в `AgentService/.env` — endpoint, ключ и модель AgentService;
- `LLM_CONTEXT_WINDOW`, `LLM_MAX_TOKENS`, `LLM_REASONING` — необязательные настройки модели.

## Чаты и SSE

Все запросы сайта идут на тот же origin, например `/agent-service/chats` и
`/agent-service/agents/assistant/{chatId}`. Передавайте `Authorization: Bearer ...`
во всех запросах, включая SSE и повторные подключения. Gateway проверяет JWT,
заменяет переданные клиентом `X-User-Id`/`X-User-Role` и проксирует запрос в AgentService.

В nginx для `/agent-service/` отключены буферизация, кеш и gzip; URI, query string
и внешний Host сохраняются. YARP также отключает буферизацию и допускает час
без активности. Текущая конфигурация nginx обслуживает HTTP на порту 80.

Пример подключения сайта с `@flue/sdk@2.3.0` после создания чата:

```ts
const client = createFlueClient({
  url: `/agent-service/agents/assistant/${chat.id}`,
  headers: () => ({ Authorization: `Bearer ${getAccessToken()}` }),
});
const admission = await client.send({
  message: { kind: 'user', body: text },
  idempotencyKey: crypto.randomUUID(),
});
const observation = client.observe({ live: 'sse' });
const unsubscribe = observation.subscribe(() => {
  renderChat(observation.getSnapshot());
});
// При закрытии экрана: unsubscribe(); observation.close();
// Остановить ответ: await client.abort();
```

Подписка запускает чтение истории и SSE. `send()` принимает сообщение, `read(admission)`
ожидает готовый ответ через long polling; для обновляемого интерфейса используется
`observe({ live: 'sse' })`. У нового чата сначала отправьте первое сообщение:
до первого admission Flue ещё не создал историю разговора.

## Сквозная проверка

Нужны работающие nginx, AuthGateway и AgentService с PostgreSQL и моделью.
Тест использует SDK из соседнего `AgentService/node_modules` (`npm.cmd ci` в нём).
Запускайте только на тестовом стенде: он создаёт чаты и делает один запрос к модели.

```powershell
$env:AGENT_PROXY_URL = 'http://localhost/agent-service'
$env:JWT_SECRET = '<тот же ключ, что у Gateway>'
node --test tests/agent-sse.mjs
docker compose -f docker-compose-dev.yaml --env-file .env config --quiet
```

При нестандартных issuer/audience задайте также `JWT_ISSUER` и `JWT_AUDIENCE`.
Проверяются identity, чужие чаты, query string, публичный streamUrl, SSE,
идемпотентность отправки, повторное чтение, история и abort.

## Тестовые пользователи (сид)

| Email | Пароль |
|---|---|
| ivan.ivanov@example.com | qwerty123 |
| maria.petrova@example.com | password123 |
| petr.sidorov@example.com | 12345678 |

Логин: `POST :80/auth-service/login`, регистрация: `POST :80/auth-service/register`.

## AgentService в общем кластере

Оба compose-файла включают `agent-service` и отдельный `agent-postgres` с постоянным
volume `agent-postgres-data`. Порты этих контейнеров на хост не публикуются.
Задайте `AGENT_POSTGRES_PASSWORD` в `cluster/.env` (URL-safe пароль).
Скопируйте `AgentService/.env.example` в `AgentService/.env` и задайте реальные
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` и лимиты выбранной модели.
Для сборки AuthGateway сохраните GitHub Packages токен в `cluster/.github-token`
(права 600, файл игнорируется Git). Он передаётся как BuildKit secret.
Compose переопределяет DATABASE_URL на внутренний agent-postgres, PORT на 3000.
Без настроек модели AgentService не запустится.

```bash
docker compose -f docker-compose-prod.yaml config --quiet
docker compose -f docker-compose-prod.yaml up -d --build agent-service auth-gateway
docker compose -f docker-compose-prod.yaml exec nginx nginx -t
docker compose -f docker-compose-prod.yaml exec nginx nginx -s reload
```

Вход: `http://<host>/agent-service/…` → nginx → auth-gateway → agent-service:3000.
Префикс и query string сохраняются. SSE проходит без буферизации, таймаут потока — 1 час.
`GET /agent-service/health` и `/agent-service/ready` проверяют процесс и БД.
Операции `/agent-service/chats` требуют пользовательский Bearer-токен;
gateway устанавливает X-User-Id/X-User-Role, AgentService проверяет владельца чата.

Сквозная проверка находится в `tests/agent-sse.mjs`. При установленных зависимостях
в соседнем AgentService запустите её Node 24, передав JWT_SECRET/JWT_ISSUER/JWT_AUDIENCE
из конфигурации gateway и AGENT_PROXY_URL (по умолчанию http://localhost/agent-service).
Проверка создаёт тестовые чаты и делает один реальный запрос к настроенной модели.
Она проверяет идентификацию, владельца чата, SSE, replay, историю и abort.
