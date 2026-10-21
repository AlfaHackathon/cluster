# Cluster

Docker-состав всего кластера AlfaHackathon:

```
клиент -> nginx (:80) -> auth-gateway (:8080) -> auth-service (:8082) -> user-service (:8081) -> postgres (:5433)
```

| Сервис | Порт наружу | Swagger |
|---|---|---|
| nginx | 80 | нет |
| auth-gateway | 8080 | нет |
| auth-service | 8082 | `/auth-service/swagger` (через gateway) |
| user-service | 8081 | `/user-service/swagger` (через gateway) |
| postgres | 5433 | нет (5432 занят на хосте) |

## Запуск

```bash
cp .env.example .env   # вписать GITHUB_TOKEN (read:packages) - нужен для restore пакетов AlfaHackaton.*
docker compose up -d --build
```

## Переменные (.env)

- `GITHUB_TOKEN` — PAT для NuGet GitHub Packages;
- `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE` — настройки токенов (одинаковы у гейтвея и auth-service);
- `SERVICES_ADDRESS`, `AUTH_SERVICE_ADDRESS` — downstream гейтвея;
- `POSTGRES_DB/USER/PASSWORD`;
- `INTERNAL_SECRET` — секрет внутренних вызовов user-service <-> auth-service;
- `SWAGGER_ENABLED` — включить/выключить свагер в сервисах.

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
