using System.Data.Common;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using LegacyStateMigration.Resources;
using Microsoft.Data.Sqlite;
using Npgsql;
using NpgsqlTypes;

// This offline tool copies canonical state; production services never open SQLite.
// Connection strings are environment-only and never included in reports or errors.
if (args.Length != 3 || args[0] is not ("context" or "insight") || args[2] is not ("--apply" or "--verify"))
{
    Console.Error.WriteLine("Usage: LegacyStateMigration context|insight source.sqlite --apply|--verify; set MIGRATION_POSTGRES");
    return 2;
}

var service = args[0];
var apply = args[2] == "--apply";
var tables = service == "context"
    ? new[] { "material_versions", "material_companies", "knowledge_revisions", "context_changes", "search_projection" }
    : new[] { "effects", "effect_revisions", "questions", "question_revisions", "analysis_receipts", "question_dependencies",
        "question_invalidations", "question_invalidation_times", "question_validity_events", "news_jobs", "news_job_events", "source_cursor" };
var results = new List<object>();
try
{
    var path = Path.GetFullPath(args[1]);
    if (!File.Exists(path)) throw new InvalidOperationException("Source file does not exist.");
    var destination = Environment.GetEnvironmentVariable("MIGRATION_POSTGRES");
    if (string.IsNullOrWhiteSpace(destination)) throw new InvalidOperationException("MIGRATION_POSTGRES is required.");
    await using var source = new SqliteConnection(new SqliteConnectionStringBuilder
        { DataSource = path, Mode = SqliteOpenMode.ReadOnly, Pooling = false }.ToString());
    await source.OpenAsync();
    await using var snapshot = source.BeginTransaction(deferred: true);
    var sourceTables = new HashSet<string>(StringComparer.Ordinal);
    await using (var query = source.CreateCommand())
    {
        query.Transaction = snapshot;
        query.CommandText = MigrationSql.Get("SourceTables");
        await using var reader = await query.ExecuteReaderAsync();
        while (await reader.ReadAsync()) sourceTables.Add(reader.GetString(0));
    }
    if (!sourceTables.Contains(tables[0]) || sourceTables.Except(tables, StringComparer.Ordinal).Any())
        throw new InvalidOperationException("Source is not the expected service schema; unknown or missing canonical tables.");
    await using var target = new NpgsqlConnection(destination);
    await target.OpenAsync();
    await using var transaction = await target.BeginTransactionAsync();
    await using (var command = new NpgsqlCommand(MigrationSql.Get("Lock"), target, transaction))
    {
        command.Parameters.AddWithValue("key", service == "context" ? 319264381L : 714103201L);
        await command.ExecuteNonQueryAsync();
    }
    foreach (var table in tables)
    {
        var targetColumns = await Columns(target, transaction, table);
        if (targetColumns.Count == 0) throw new InvalidOperationException("Target migrations must be applied before import: " + table);
        if (apply)
        {
            await using var count = new NpgsqlCommand(MigrationSql.Format("Count", Quote(table)), target, transaction);
            if ((long)(await count.ExecuteScalarAsync())! != 0)
                throw new InvalidOperationException("Target must be empty; existing state will not be overwritten: " + table);
        }
    }
    foreach (var table in tables.Where(sourceTables.Contains))
    {
        var names = new List<string>();
        await using (var query = source.CreateCommand())
        {
            query.Transaction = snapshot;
            query.CommandText = MigrationSql.Get("SourceColumns");
            query.Parameters.AddWithValue("$table", table);
            await using var reader = await query.ExecuteReaderAsync();
            while (await reader.ReadAsync()) names.Add(reader.GetString(0));
        }
        var types = await Columns(target, transaction, table);
        if (names.Any(name => !types.ContainsKey(name)))
            throw new InvalidOperationException("Target lacks a source column: " + table);
        var deriveTicks = service == "context" && table == "context_changes" && !names.Contains("known_ticks");
        if (types.Keys.Except(names).Any(name => !(deriveTicks && name == "known_ticks")))
            throw new InvalidOperationException("Unexpected target column requires an explicit migration rule: " + table);
        var outputNames = deriveTicks ? names.Append("known_ticks").ToArray() : names.ToArray();
        var expected = new List<string>();
        await using (var query = source.CreateCommand())
        {
            query.Transaction = snapshot;
            query.CommandText = MigrationSql.Format("Read", string.Join(',', names.Select(Quote)), Quote(table));
            await using var reader = await query.ExecuteReaderAsync();
            await using var insert = new NpgsqlCommand(MigrationSql.Format("Insert", Quote(table), string.Join(',', outputNames.Select(Quote)),
                string.Join(',', outputNames.Select((_, i) => "@p" + i))), target, transaction);
            for (var i = 0; i < outputNames.Length; i++) insert.Parameters.Add(new NpgsqlParameter("p" + i, Type(types[outputNames[i]])));
            while (await reader.ReadAsync())
            {
                var values = Enumerable.Range(0, names.Count).Select(i => reader.IsDBNull(i) ? null : reader.GetValue(i)).ToList();
                if (deriveTicks) values.Add(DateTimeOffset.Parse((string)values[names.IndexOf("known_at")]!, CultureInfo.InvariantCulture).UtcTicks);
                expected.Add(RowHash(values));
                if (!apply) continue;
                for (var i = 0; i < values.Count; i++)
                {
                    object? value = values[i];
                    if (value is not null && types[outputNames[i]] == "integer") value = Convert.ToInt32(value, CultureInfo.InvariantCulture);
                    insert.Parameters[i].Value = value ?? DBNull.Value;
                }
                await insert.ExecuteNonQueryAsync();
            }
        }
        var actual = new List<string>();
        await using (var query = new NpgsqlCommand(MigrationSql.Format("Read", string.Join(',', outputNames.Select(Quote)), Quote(table)), target, transaction))
        {
            await using var reader = await query.ExecuteReaderAsync();
            while (await reader.ReadAsync()) actual.Add(RowHash(Enumerable.Range(0, outputNames.Length).Select(i => reader.IsDBNull(i) ? null : reader.GetValue(i))));
        }
        var expectedHash = TableHash(expected);
        var actualHash = TableHash(actual);
        if (expected.Count != actual.Count || expectedHash != actualHash)
            throw new InvalidOperationException("Row count or content checksum mismatch: " + table);
        results.Add(new { table, rows = expected.Count, sha256 = expectedHash, derivedKnownTicks = deriveTicks });
    }
    if (apply)
    {
        if (service == "insight" && !sourceTables.Contains("question_validity_events"))
        {
            await using var backfill = new NpgsqlCommand(MigrationSql.Get("BackfillValidity"), target, transaction);
            results.Add(new { derivedValidityEvents = await backfill.ExecuteNonQueryAsync() });
        }
        await using (var check = new NpgsqlCommand(MigrationSql.Get("CheckConstraints"), target, transaction))
            await check.ExecuteNonQueryAsync();
        var sequenceTable = service == "context" ? "context_changes" : "question_validity_events";
        var sequenceColumn = service == "context" ? "sequence" : "event_id";
        await using var name = new NpgsqlCommand(MigrationSql.Get("Sequence"), target, transaction);
        name.Parameters.AddWithValue("table", sequenceTable);
        name.Parameters.AddWithValue("column", sequenceColumn);
        var sequence = await name.ExecuteScalarAsync() as string;
        if (sequence is null) throw new InvalidOperationException("Missing identity sequence: " + sequenceTable);
        await using var maximum = new NpgsqlCommand(MigrationSql.Format("Maximum", Quote(sequenceColumn), Quote(sequenceTable)), target, transaction);
        var value = Convert.ToInt64(await maximum.ExecuteScalarAsync(), CultureInfo.InvariantCulture);
        await using var reset = new NpgsqlCommand(MigrationSql.Get("SetSequence"), target, transaction);
        reset.Parameters.AddWithValue("sequence", sequence);
        reset.Parameters.AddWithValue("value", Math.Max(1, value));
        reset.Parameters.AddWithValue("called", value > 0);
        await reset.ExecuteNonQueryAsync();
        await transaction.CommitAsync();
    }
    else await transaction.RollbackAsync();
    Console.WriteLine(JsonSerializer.Serialize(new { status = "pass", service, mode = apply ? "apply" : "verify", sourceOpenedReadOnly = true, tables = results }));
    return 0;
}
catch (Exception exception)
{
    // Never print driver exception details: they may contain connection details or canonical payloads.
    var reason = exception is InvalidOperationException ? exception.Message : exception.GetType().Name;
    Console.Error.WriteLine(JsonSerializer.Serialize(new { status = "failed", service, reason, sourceOpenedReadOnly = true }));
    return 1;
}

static string Quote(string value) => '"' + value.Replace("\"", "\"\"") + '"';
static string RowHash(IEnumerable<object?> values) => Convert.ToHexStringLower(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(values)));
static string TableHash(List<string> hashes) => Convert.ToHexStringLower(SHA256.HashData(Encoding.ASCII.GetBytes(string.Join('\n', hashes.Order(StringComparer.Ordinal)))));
static NpgsqlDbType Type(string type) => type switch
{
    "text" or "character varying" => NpgsqlDbType.Text,
    "bigint" => NpgsqlDbType.Bigint,
    "integer" => NpgsqlDbType.Integer,
    _ => throw new InvalidOperationException("Unsupported target column type: " + type)
};
static async Task<Dictionary<string, string>> Columns(NpgsqlConnection connection, NpgsqlTransaction transaction, string table)
{
    await using var query = new NpgsqlCommand(MigrationSql.Get("TargetColumns"), connection, transaction);
    query.Parameters.AddWithValue("table", table);
    await using var reader = await query.ExecuteReaderAsync();
    var columns = new Dictionary<string, string>(StringComparer.Ordinal);
    while (await reader.ReadAsync()) columns.Add(reader.GetString(0), reader.GetString(1));
    return columns;
}
