using System.Text.Json;
using StarMap.Core.Frames;
using StarMap.Core.Recognition;
using StarMap.Core.Reliability;
using StarMap.Core.Session;
using StarMap.Core.Resources;

if (args is ["--structure", var structureRoot, var selectedMode, var selectedSeason, var selectedProfile, var selectedMap, var selectedFloor])
{
    var catalog = ResourceCatalog.Load(structureRoot);
    var entry = catalog.Find(selectedMode, selectedSeason, selectedProfile, selectedMap, selectedFloor);
    var loaded = entry is null ? null : catalog.LoadSharedStructure(entry);
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        success = loaded?.Success == true,
        code = entry is null ? "resource-binding-missing" : loaded?.Error?.Code,
        shape = loaded?.Value?.ValidationMask.Shape,
        cleanAvailable = loaded?.Value?.CleanMask is not null,
        nativeWallsAvailable = loaded?.Value?.NativeWalls is not null,
        scope = "Exact resource binding and typed arrays; not recognition or pose acceptance"
    }));
    return loaded?.Success == true ? 0 : 1;
}

if (args is ["--catalog", var resourceRoot])
{
    var catalog = ResourceCatalog.Load(resourceRoot);
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        scope = "Read-only resource declarations; array loading, coordinate equivalence and recognition are separate checks",
        entries = catalog.Entries.Count,
        issues = catalog.Issues.GroupBy(issue => issue.Code).Select(group => new { code = group.Key, count = group.Count() }),
        bindings = catalog.Entries.Select(entry => new { entry.StableKey, entry.Mode, entry.Season, entry.Profile, entry.MapId, entry.Floor, entry.StructureKind, status = entry.StructureStatus.ToString(), entry.SourceVersion, entry.StructureVersionKey, entry.BaseSnapshot })
    }, new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

// Phase-1 parity CLI. Stdout is a single JSON object. It never prints the
// frame path, a window title, a username, or another program's identity.
var cancel = false;
string? frame = null;
for (int i = 0; i < args.Length; i++)
{
    if (args[i] == "--cancel")
    {
        cancel = true;
    }
    else if (args[i] == "--frame" && i + 1 < args.Length)
    {
        frame = args[++i];
    }
    else
    {
        Console.Error.WriteLine("usage: StarMap.Cli --frame <recorded-frame-directory> [--cancel]");
        return 2;
    }
}
if (string.IsNullOrWhiteSpace(frame))
{
    Console.Error.WriteLine("usage: StarMap.Cli --frame <recorded-frame-directory> [--cancel]");
    return 2;
}

RecordedFrame recorded;
try
{
    recorded = RecordedFrame.Load(frame);
}
catch (InvalidOperationException error)
{
    Console.Error.WriteLine(error.Message);
    return 1;
}

var session = new SessionSequencer();
var gate = new ResultGate(session);
SliceExecution execution;
try
{
    execution = SliceRunner.Run(recorded, session, gate, cancel);
}
catch (InvalidOperationException error)
{
    Console.Error.WriteLine(error.Message);
    return 1;
}

var result = execution.Result;
var payload = new Dictionary<string, object?>
{
    ["slice"] = "fast-structure-grid-v1",
    ["frameId"] = recorded.Id,
    ["reason"] = result.Reason,
    ["gatePointCount"] = result.GatePointCount,
    ["coarsePointCount"] = result.Reason == GridSlice.GridResult ? result.HashedPoints.Length : 0,
    ["rowCount"] = result.Rows.Length,
    ["pointsSha256"] = SliceCanonical.PointsSha256(result),
    ["structureSha256"] = SliceCanonical.StructureSha256(result),
    ["rows"] = result.Rows.Select(row => new Dictionary<string, object>
    {
        ["score"] = row.Score,
        ["scale"] = row.Scale,
        ["x"] = row.X,
        ["y"] = row.Y,
    }).ToArray(),
    ["cancelled"] = result.Cancelled,
    ["handshake"] = execution.Handshake.Reason,
    ["protocolVersion"] = execution.Handshake.ProtocolVersion,
    ["sessionId"] = execution.Ticket.SessionId,
    ["generation"] = execution.Ticket.Generation,
    ["sequence"] = execution.Ticket.Sequence,
    ["disposition"] = execution.Disposition.ToString(),
    ["computeMs"] = execution.ComputeMilliseconds,
};
var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
Console.WriteLine(json);
return execution.Disposition == ResultDisposition.Accepted ? 0 : 3;
