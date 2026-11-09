using System.Text.Json.Nodes;
using static StarMap.Core.Selection.StructureEvidence;

namespace StarMap.Core.Selection;

public sealed record ObservationTicket(string SessionId, string RoundKey, long Generation, string Floor);

/// <summary>Bounded round-owned observations. All returned evidence is detached from session-owned state.</summary>
public sealed class StructureEvidenceSession
{
    private readonly object _sync = new();
    private readonly StructureCatalog _catalog;
    private readonly string _roundKey, _sessionId = Guid.NewGuid().ToString("N");
    private readonly int _maximum;
    private readonly List<History> _history = [], _priority = [];
    private long _generation;
    private long? _committedGeneration;
    private JsonObject? _current;
    private string? _floor;
    private ObservationTicket? _ticket;
    private bool _ended;
    private sealed record History(string Floor, string Frame, string[] Supported, bool Complete);

    public StructureEvidenceSession(StructureCatalog catalog, string roundKey, int maxObservations = 12)
    {
        if (string.IsNullOrEmpty(roundKey) || maxObservations is < 1 or > 64) throw new ArgumentException("s1-structure-session-invalid");
        _catalog = catalog; _roundKey = roundKey; _maximum = maxObservations;
    }
    public long Generation { get { lock (_sync) return _generation; } }
    public ObservationTicket BeginObservation(string floor)
    {
        lock (_sync)
        {
            if (_ended || !_catalog.Floors.Contains(floor)) throw new InvalidOperationException("s1-structure-session-unavailable");
            _generation++; _current = null; _floor = floor;
            return _ticket = new(_sessionId, _roundKey, _generation, floor);
        }
    }

    public bool Commit(JsonObject? report, ObservationTicket? ticket)
    {
        lock (_sync)
        {
            if (report is null || _ended || ticket is null || ticket != _ticket || _generation == _committedGeneration) return false;
            string? status = Text(report["status"]), frame = Text(report["frameKey"]), source = Text(report["floorSource"]);
            int seq = 1;
            if (report.ContainsKey("updateSeq") && (report["updateSeq"] is not JsonValue seqValue || !seqValue.TryGetValue<int>(out seq))) return false;
            string[] statuses = ["partial", "complete", "budget-unresolved", "cancelled", "preferred-material-committed", "first-material-committed", "subset-unresolved"];
            if (report["schemaVersion"] is not JsonValue schema || !schema.TryGetValue<int>(out int version) || version != 1
                || Text(report["floor"]) != _floor || Text(report["season"]) != _catalog.Season || Text(report["mode"]) != _catalog.Mode
                || Text(report["resourceKey"]) != _catalog.ResourceKey || !(StringArray(report["expectedMapIds"])?.SequenceEqual(_catalog.MapIds) ?? false)
                || _floor is null || !_catalog.Floors.Contains(_floor) || source is not ("operator-declared" or "current-frame-verified")
                || Text(report["observedFloor"]) != (source == "current-frame-verified" ? _floor : "")
                || !statuses.Contains(status) || seq < 1 || status == "partial" && !report.ContainsKey("updateSeq")
                || (status == "complete" ? !True(report["scanComplete"]) : !False(report["scanComplete"])) || frame?.Length != 64) return false;
            var supported = StringArray(report["supportedMapIds"]); var checkedIds = StringArray(report["checkedMapIds"]);
            if (supported is null || checkedIds is null || supported.Distinct().Count() != supported.Length || checkedIds.Distinct().Count() != checkedIds.Length
                || supported.Except(checkedIds).Any() || checkedIds.Except(_catalog.MapIds).Any()
                || !(StringArray(report["unscannedMapIds"])?.SequenceEqual(_catalog.MapIds.Except(checkedIds)) ?? false)
                || !(StringArray(report["unknownMapIds"])?.SequenceEqual(_catalog.MapIds.Except(supported)) ?? false)
                || report["excludedMapIds"] is not JsonArray { Count: 0 }
                || new[] { "competitionComplete", "uniqueIdentityVerified", "identityHardGatePassed", "automaticSelectionAuthorized", "liveAuthorization" }.Any(k => !False(report[k]))
                || report["poses"] is not JsonObject poses || !poses.Select(p => p.Key).ToHashSet().SetEquals(supported)
                || status == "complete" && checkedIds.Length != _catalog.MapIds.Count
                || status == "preferred-material-committed" && (checkedIds.Length != 1 || !supported.SequenceEqual(checkedIds)
                    || !True(report["scanSkipped"]) || Text(report["candidateReuse"]) != "preferred-current-frame")
                || status == "first-material-committed" && (supported.Length == 0 || checkedIds.Length == 0
                    || !True(report["scanSkipped"]) || Text(report["candidateReuse"]) != "first-current-frame")
                || status == "cancelled" && supported.Length != 0 || supported.Any(id => !VerifiedPose(poses[id]))) return false;
            if (_current is { } previous)
            {
                int oldSeq = previous["updateSeq"]?.GetValue<int>() ?? 1;
                var previousChecked = StringArray(previous["checkedMapIds"])!; var previousSupported = StringArray(previous["supportedMapIds"])!;
                if (seq <= oldSeq || frame != Text(previous["frameKey"]) || source != Text(previous["floorSource"])
                    || Text(report["observedFloor"]) != Text(previous["observedFloor"])
                    || !checkedIds.Take(previousChecked.Length).SequenceEqual(previousChecked)
                    || status != "cancelled" && previousSupported.Except(supported).Any()) return false;
                if (status != "cancelled" && previousSupported.Any(id => !JsonNode.DeepEquals(previous["poses"]![id], poses[id]))) return false;
            }
            _current = (JsonObject)report.DeepClone();
            if (status != "partial") _committedGeneration = _generation;
            if (supported.Length > 0 && status != "cancelled")
            {
                var old = _priority.FirstOrDefault(h => h.Floor == _floor && h.Frame == frame);
                var combined = supported.Concat(old?.Supported ?? []).ToHashSet();
                Upsert(_priority, new(_floor, frame!, _catalog.MapIds.Where(combined.Contains).ToArray(), false));
            }
            if (supported.Length > 0 && status is "complete" or "budget-unresolved" or "preferred-material-committed" or "first-material-committed" or "subset-unresolved")
                Upsert(_history, new(_floor, frame!, supported, status == "complete"));
            return true;
        }
    }

    private void Upsert(List<History> history, History row)
    {
        history.RemoveAll(h => h.Floor == row.Floor && h.Frame == row.Frame); history.Add(row);
        if (history.Count > _maximum) history.RemoveAt(0);
    }
    public JsonObject ChooseCandidate(string mapId, ObservationTicket? ticket)
    {
        lock (_sync)
        {
            if (_ended || ticket != _ticket || _current is null || !(StringArray(_current["supportedMapIds"])?.Contains(mapId) ?? false))
                return new() { ["success"] = false, ["reason"] = "s1-structure-candidate-not-current" };
            return new() { ["success"] = true, ["mapId"] = mapId, ["floor"] = _floor, ["season"] = _catalog.Season,
                ["mode"] = _catalog.Mode, ["resourceKey"] = _catalog.ResourceKey, ["frameKey"] = Text(_current["frameKey"]),
                ["generation"] = _generation, ["pose"] = _current["poses"]![mapId]!.DeepClone(), ["identityKind"] = "candidate",
                ["requiresManualConfirmation"] = true, ["groupId"] = null, ["identityVerified"] = false,
                ["displayAuthorized"] = false, ["liveAuthorization"] = false };
        }
    }
    public IReadOnlyList<string> PrioritySupported(string? floor)
    {
        lock (_sync)
        {
            if (_ended || floor is null || !_catalog.Floors.Contains(floor)) return Array.Empty<string>();
            return _priority.AsEnumerable().Reverse().Where(h => h.Floor == floor).SelectMany(h => h.Supported).Distinct().ToArray();
        }
    }
    public IReadOnlyList<string> PriorityOrder(string floor)
    {
        lock (_sync) { var hints = PrioritySupported(floor); return hints.Concat(_catalog.MapIds.Except(hints)).ToArray(); }
    }
    public JsonObject Snapshot()
    {
        lock (_sync)
        {
            var common = _history.Count == 0 ? new HashSet<string>() : _history[0].Supported.ToHashSet();
            foreach (var row in _history.Skip(1)) common.IntersectWith(row.Supported);
            var counts = new JsonObject(); foreach (string id in _catalog.MapIds) counts[id] = _history.Count(h => h.Supported.Contains(id));
            return new() { ["roundKey"] = _roundKey, ["generation"] = _generation, ["observationCount"] = _history.Count,
                ["floors"] = Strings(_history.Select(h => h.Floor).Distinct().Order(StringComparer.Ordinal)), ["supportedOnEveryObservation"] = Strings(common.Order(StringComparer.Ordinal)),
                ["priorityHintObservationCount"] = _priority.Count, ["priorityHintMapIds"] = Strings(PrioritySupported(_floor)), ["priorityHintsOnly"] = true,
                ["supportCounts"] = counts, ["conflictingSupport"] = _history.Count > 0 && common.Count == 0,
                ["currentSupportedMapIds"] = _current?["supportedMapIds"]?.DeepClone() ?? new JsonArray(),
                ["currentScanComplete"] = True(_current?["scanComplete"]), ["currentStatus"] = Text(_current?["status"]) ?? "",
                ["candidateReuse"] = Text(_current?["candidateReuse"]) ?? "none", ["scanSkipped"] = True(_current?["scanSkipped"]),
                ["currentUpdateSeq"] = _current?["updateSeq"]?.DeepClone() ?? JsonValue.Create(_current is null ? 0 : 1),
                ["currentUnscannedMapIds"] = _current?["unscannedMapIds"]?.DeepClone() ?? new JsonArray(),
                ["possibleMapIds"] = Strings(_catalog.MapIds), ["excludedMapIds"] = new JsonArray(), ["registeredGroup"] = null,
                ["uniqueIdentityVerified"] = false, ["liveAuthorization"] = false };
        }
    }
    public bool WithdrawMaterial(ObservationTicket? ticket)
    {
        lock (_sync) { if (ticket != _ticket || ticket is null || _ended) return false; _current = null; _committedGeneration = _generation; return true; }
    }
    public void RevokeCurrent() { lock (_sync) { _generation++; _current = null; _floor = null; _ticket = null; } }
    public void EndRound()
    {
        lock (_sync) { _generation++; _history.Clear(); _priority.Clear(); _ticket = null; _current = null; _floor = null; _ended = true; }
    }
}
