# Проверка MVC и пакетов организации

`run.py` проверяет опубликованные ContextService/InsightService и реальные HTTP-клиенты
AgentService. Reasoner — детерминированная тестовая реализация; LLM и Kafka не вызываются.
Входы синтетические. Работающие сервисы и их данные не используются.

Проверка запускает четыре этапа:

1. Старые опубликованные API из `.scratch/org-adoption-baseline` наполняют SQLite через HTTP.
2. Новые API создают PostgreSQL-схемы через организационные миграции. Отдельный importer
   переносит SQLite, сверяет все исходные строки, затем новые API читают прежние ответы.
3. Новые API выполняют исходные сценарии на свежих PostgreSQL БД.
4. Повторный запуск новых API проверяет долговечность, историю и отсутствие нового reasoning.

Общие HTTP-сценарии взяты из `../architecture-refactor-v1`: hashes, provenance, scope/asOf,
исправление тарифа, duplicate receipts, stale writes, atomic batch rejection, empty receipts,
job leases/checkpoints и cursor CAS. `question_proof.py` дополнительно создаёт канонический
вопрос, зависимости и invalidation. Проверяется история с границей времени ровно 100 нс,
recipients и replay после переноса.

Для ошибок бизнес-правил новый этап проверяет пакетный `{errorMessage}`; SQLite baseline
сохраняет прежний `{code,message}`. HTTP-статусы проверяются в обоих этапах.

Importer отдельно проверяется повреждённым FK: ошибка должна откатить все строки.
Повторный импорт в непустую цель отклоняется. SHA256 исходных SQLite файлов до и после
переноса должен совпасть. Только созданные этим запуском случайно именованные БД удаляются.

Необходимы .NET 10, Node 24, Python 3.11+ и изолированный PostgreSQL. Текущий provisioning
использует `psql` в WSL Ubuntu, PostgreSQL loopback `55441`, role `postgres` с правом
CREATE DATABASE. Для другого порта есть `--postgres-port`. Production DSN не передаётся.

После `cluster/tools/Build-OrganizationPackages.ps1` укажите напечатанные им config и cache:

```powershell
$build = Get-Content .artifacts/org-packages/latest.json -Raw | ConvertFrom-Json
python cluster/research/org-adoption-v2/run.py --nuget-config $build.config --packages "$($build.runRoot)/packages" --package-manifest .artifacts/org-packages/latest.json
```

Без этих параметров restore использует штатные NuGet.config сервисов. Для старого этапа
нужен сохранённый до изменения кода snapshot `.scratch/org-adoption-baseline`; он не
создаётся автоматически из текущих исходников.

Каждый запуск сохраняет `runs/<UTC>/result.json`, publish/runtime logs, ответы HTTP,
отчёты миграции. В отчёте отдельно отмечается происхождение зависимостей: успешные
functional tests не означают, что бинарники скачаны из GitHub Packages. Source-built
пакеты проверяются по provenance отчёту сборочного скрипта. Неудачные запуски сохраняются.
