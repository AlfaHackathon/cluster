using System.Globalization;
using System.Resources;

namespace LegacyStateMigration.Resources;

internal static class MigrationSql
{
    private static readonly ResourceManager Manager = new(typeof(MigrationSql));
    internal static string Get(string name) => Manager.GetString(name, CultureInfo.InvariantCulture)
        ?? throw new InvalidOperationException("Missing migration SQL resource: " + name);
    // Only identifiers obtained from the fixed application table allowlist and schema metadata are substituted.
    internal static string Format(string name, params object[] identifiers) => string.Format(CultureInfo.InvariantCulture, Get(name), identifiers);
}
