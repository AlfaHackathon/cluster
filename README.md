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
cp .env.example .env   # заполнить GITHUB_TOKEN, AGENT_POSTGRES_PASSWORD и LLM_*
docker compose up -d --build
```

Репозитории `AuthGateway`, `AuthService`, `UserService`, `AgentService` должны
лежать рядом с `cluster`: compose собирает их из соседних каталогов.
Для интеграции AgentService изменения AuthGateway и cluster подготовлены в
ветках `codex/agent-service-sse`, основанных на `dev`.

AgentService запускает миграции при старте и ждёт готовности собственной БД.
Её данные хранятся в отдельном volume `agent-postgres-data`. Порт AgentService
не публикуется на хост: клиент обращается через Gateway, который выставляет identity.

## Переменные (.env)

- `GITHUB_TOKEN` — PAT для NuGet GitHub Packages;
- `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE` — настройки токенов (одинаковы у гейтвея и auth-service);
- `SERVICES_ADDRESS`, `AUTH_SERVICE_ADDRESS`, `AGENT_SERVICE_ADDRESS` — downstream гейтвея;
- `POSTGRES_DB/USER/PASSWORD`;
- `INTERNAL_SECRET` — секрет внутренних вызовов user-service <-> auth-service;
- `SWAGGER_ENABLED` — включить/выключить свагер в сервисах.
- `AGENT_POSTGRES_PASSWORD` — отдельный URL-safe пароль БД AgentService (латинские буквы/цифры/`-`/`_`);
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` — endpoint, ключ и модель AgentService;
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
docker compose --env-file .env.example config --quiet
```

При нестандартных issuer/audience задайте также `JWT_ISSUER` и `JWT_AUDIENCE`.
Проверяются identity, чужие чаты, query string, публичный streamUrl, SSE,
идемпотентность отправки, повторное чтение, история и abort.

Локально 20.10.2026 прошли 3 теста Gateway и 2 сквозных теста на реальных
nginx 1.30.6, YARP 2.3.0, AgentService, PostgreSQL 18.6 и модели gpt-6-luna.
SSE пришёл 15 порциями за ~7 секунд, то есть до завершения всего ответа.
Docker compose проверен на корректность конфигурации; весь compose не запускался:
локальный Docker Desktop не поднимает engine. Для проверки использованы нативные
процессы и тот же nginx location (заменены только адреса, порт и Docker DNS).
Пакет `AlfaHackaton.Auth` 1.3.0 собран из исходников организации в изолированный
локальный NuGet feed: у доступного GitHub credential нет `read:packages`.

## Тестовые пользователи (сид)

| Email | Пароль |
|---|---|
| ivan.ivanov@example.com | qwerty123 |
| maria.petrova@example.com | password123 |
| petr.sidorov@example.com | 12345678 |

Логин: `POST :80/auth-service/login`, регистрация: `POST :80/auth-service/register`.
