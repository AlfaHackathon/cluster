# Перенос состояния SQLite в PostgreSQL

Офлайн-инструмент для уже существующих ContextService и InsightService. Production API
работают только с PostgreSQL через AlfaHackaton.DapperContext; SQLite нужен этому
инструменту исключительно как источник старого состояния.

1. Остановите запись в старый сервис и сохраните резервную копию SQLite вместе с WAL,
   если он присутствует. Не копируйте только основной файл работающей WAL-базы.
2. Создайте отдельную пустую PostgreSQL БД для каждого сервиса. Запустите новый API
   с его connection string для применения FluentMigrator migrations, затем остановите API.
3. Задайте `MIGRATION_POSTGRES` в окружении процесса для соответствующей целевой БД.
   Не сохраняйте строку с паролем в репозитории или отчёте.
4. Выполните перенос и независимую сверку:

```powershell
dotnet build cluster/tools/LegacyStateMigration -c Release
dotnet cluster/tools/LegacyStateMigration/bin/Release/net10.0/LegacyStateMigration.dll context C:/backup/context.sqlite --apply
dotnet cluster/tools/LegacyStateMigration/bin/Release/net10.0/LegacyStateMigration.dll context C:/backup/context.sqlite --verify
# Затем смените MIGRATION_POSTGRES на БД InsightService:
dotnet cluster/tools/LegacyStateMigration/bin/Release/net10.0/LegacyStateMigration.dll insight C:/backup/insight.sqlite --apply
dotnet cluster/tools/LegacyStateMigration/bin/Release/net10.0/LegacyStateMigration.dll insight C:/backup/insight.sqlite --verify
```

`--apply` принимает только пустые таблицы целевого сервиса. Источник открывается
ReadOnly с согласованным snapshot. Все таблицы импортируются в одной транзакции;
тот же advisory lock, что в API, исключает параллельную операцию сервиса.
Проверяются количество и SHA256 каждого набора строк, внешние ключи, затем
восстанавливаются sequence и выполняется commit. Payload JSON копируется как TEXT
без повторной сериализации. Отчёт содержит только имена таблиц, количества и хеши.

Context `known_ticks` вычисляется из `known_at` с точностью .NET 100 нс. Для старой
Insight схемы без `question_validity_events` события строятся из перенесённых
invalidations; новые исторические данные не выдумываются. Неизвестная таблица или
неожиданная колонка останавливает перенос и требует явного правила миграции.

`--verify` ничего не меняет: повторно сравнивает импортированные исходные таблицы.
Он проверяет состояние непосредственно после импорта; после рабочих изменений
сервисов расхождение с исходным SQLite ожидаемо. Автоматического переключения
рабочих сервисов и автоматического удаления SQLite нет.

Синтетическая проверка: `cluster/research/org-adoption-v2/run.py`. Она запускает опубликованные
старые API, наполняет SQLite через HTTP, переносит в PostgreSQL, сверяет прежние ответы
после перезапуска и повторяет сценарии на свежих БД. Дополнительно проверяет отказ
для непустой цели и полный rollback импорта с повреждённой ссылкой.
