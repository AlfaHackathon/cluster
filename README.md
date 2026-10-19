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
