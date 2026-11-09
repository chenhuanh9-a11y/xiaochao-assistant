using System.Text.Json.Nodes;
using StarMap.Core.Identity;

namespace StarMap.Vision;

/// <summary>Consumes already verified entry rows. The caller retains ownership of session state.</summary>
public static class EntryIdentityDecision
{
    public static bool PassportFallbackAllowed(JsonObject? diagnostics) => diagnostics == null ||
        diagnostics["reason"]?.ToString() is "entry-no-reliable-exit" or "entry-passport-unavailable"
            or "entry-coverage-incomplete" or "entry-family-unresolved";

    public static JsonObject? VerifiedCandidate(EntryPassportCatalog catalog, JsonArray rows, JsonObject diagnostics, string floor)
    {
        if (!catalog.Floors.Contains(floor)) return null;
        bool first = diagnostics["selectionPolicy"]?.ToString() == "first-verified-entry";
        if (!first && (!(True(diagnostics,"exitObserved") && True(diagnostics,"entryObserved") &&
            True(diagnostics,"complete") && True(diagnostics,"verificationComplete") && True(diagnostics,"coverageComplete")) ||
            !IsBoolean(diagnostics["identityAmbiguous"],false))) return null;
        var accepted = new List<JsonObject>();
        foreach (var node in rows)
        {
            var row = node!.AsObject();
            var evidence = row["entryEvidence"];
            var check = row["verification"];
            if (!True(evidence,"directionCompatible") || !True(evidence,"localGeometryVerified") ||
                string.IsNullOrEmpty(evidence?["sourceEntryId"]?.ToString()) || !True(check,"success") ||
                !(True(check?["regions"],"regionalEdgeVerified") || first && check?["validationPolicy"]?.ToString() == "local-fit-and-author") ||
                !True(check?["residual"],"verified")) continue;
            if (row["mapId"]?.ToString() is not { } mapId || !catalog.MapProfiles.TryGetValue(mapId, out var profile) ||
                row["profileId"]?.ToString() != profile || row["floor"]?.ToString() != floor ||
                row["season"] is { } season && season.ToString() != catalog.Season ||
                row["mode"] is { } mode && mode.ToString() != catalog.Mode ||
                !FiniteMatrix(check!["matrixSourceToClient"])) return null;
            accepted.Add(row);
            if (first) break;
        }
        if (accepted.Count == 0 || accepted.Any(r => r["mapId"]!.ToString() != accepted[0]["mapId"]!.ToString())) return null;
        bool uniquePose = first || (accepted.Count == 1 && IsBoolean(diagnostics["poseAmbiguous"], false));
        var winner = accepted[0].DeepClone().AsObject();
        winner["identityVerified"] = true;
        winner["poseVerified"] = uniquePose;
        winner["identityOnly"] = !uniquePose;
        winner["entryPoseCandidates"] = new JsonArray(accepted.Select(r => r.DeepClone()).ToArray());
        winner["locatorKind"] = "side";
        if (first) winner["competitionComplete"] = false;
        winner["identityEvidence"] = first ? "first-verified-exit-anchored-entry" : "complete-unique-exit-anchored-entry";
        return winner;
    }

    private static bool FiniteMatrix(JsonNode? node)
    {
        if (node is not JsonArray { Count: 3 } matrix) return false;
        foreach (var part in matrix)
        {
            if (part is not JsonArray { Count: 3 } row) return false;
            foreach (var element in row)
            {
                if (element is not JsonValue || !double.TryParse(element.ToString(),
                    System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double value) ||
                    !double.IsFinite(value)) return false;
            }
        }
        return true;
    }

    private static bool True(JsonNode? obj, string key) => IsBoolean(obj?[key], true);
    private static bool IsBoolean(JsonNode? node, bool expected) =>
        node is JsonValue value && value.TryGetValue<bool>(out var boolean) && boolean == expected;
}
