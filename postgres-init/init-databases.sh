#!/bin/bash
# Создание изолированных БД для каждого сервиса кластера.
# Срабатывает один раз на пустом volume postgres-data (docker-entrypoint-initdb.d).
set -e

echo "Инициализация БД сервисов (одна БД на сервис)..."
for db in "${USER_SERVICE_DB:-user-service}" "${COMPANY_SERVICE_DB:-company-service}"; do
  if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -tc "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1; then
    echo "CREATE DATABASE $db;"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -c "CREATE DATABASE \"$db\""
  else
    echo "БД $db уже существует — пропускаем"
  fi
done

echo "Инициализация БД завершена"
