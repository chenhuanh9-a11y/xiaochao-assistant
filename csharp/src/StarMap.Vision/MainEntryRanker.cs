using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using StarMap.Core.Resources;

namespace StarMap.Vision;

public sealed record MainEntryResult(JsonArray Rows, JsonObject Diagnostics, JsonObject FrontGate, JsonArray Corners);

/// <summary>Front gate, main entrance proposals, current frame verification and inventory scope.</summary>
public sealed class MainEntryRanker
{
    private readonly EntryPassportCatalog catalog;
    private readonly ResourceCatalog resources;
    private readonly MainEntrySource[] sources;

    public MainEntryRanker(EntryPassportCatalog catalog, ResourceCatalog resources)
    {
        if (!string.Equals(Path.TrimEndingDirectorySeparator(catalog.ResourceRoot),Path.TrimEndingDirectorySeparator(resources.RootPath),
                OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            throw new ArgumentException("Entry index and structure provider must share a resource root.");
        this.catalog = catalog;
        this.resources = resources;
        sources = catalog.MainEntries.Select(MainEntrySource.Read).ToArray();
    }

    /// <summary>Independent current-frame main entrance search. Known map selectors constrain pose lookup.</summary>
    public MainEntryResult Rank(Mat frame, string floor, TimeSpan? budget = null, string? knownMapId = null,
        CancellationToken cancellation = default, bool enableFrontGate = true)
    {
        cancellation.ThrowIfCancellationRequested();
        using var pixels = frame.Clone();
        using var passport = new EntryPassportFrame(catalog,pixels);
        JsonObject? passportDiagnostics = null;
        var clock = Stopwatch.StartNew();
        using var entry = catalog.Mode == "nightmare" && floor == "1" && enableFrontGate || sources.Any(i => i.Floor == floor)
            ? EntryObservation.Capture(pixels) : null;
        List<Corner> query = entry == null ? [] : MainEntryGate.Corners(pixels,entry.Geometry);
        var result = new JsonArray();
        var (gate, allowed) = MainEntryGate.Select(pixels,entry,query,catalog, floor,
            enableFrontGate && knownMapId == null);
        var corners = new JsonArray(query.Select(n => (JsonNode)new JsonObject {
            ["point"]=MainEntrySource.PointJson(n.Point), ["rays"]=new JsonArray(n.Rays.Select(p=>(JsonNode)MainEntrySource.PointJson(p)).ToArray()),
            ["lengths"]=new JsonArray(n.Lengths.Select(x=>(JsonNode)JsonValue.Create(x)!).ToArray())
        }).ToArray());
        var records = new List<(MainEntrySource Item,JsonObject Row)>();
        var diagnostics = new JsonObject();
        var evaluated = new JsonArray();
        var limit = budget ?? TimeSpan.FromSeconds(1);
        if (!sources.Any(s=>s.Floor==floor))
        {
            diagnostics["complete"]=true;
            return Finish("entry-floor-not-indexed");
        }
        if (limit <= TimeSpan.Zero) { diagnostics["complete"]=false; return Finish("entry-budget-incomplete"); }
        diagnostics["queryChains"]=query.Count;
        if (query.Count < 3)
        {
            diagnostics["minimumQueryChains"]=3;
            return Finish("entry-visible-structure-insufficient");
        }
        bool complete = true;
        foreach (var item in sources)
        {
            cancellation.ThrowIfCancellationRequested();
            if (item.Floor != floor || knownMapId != null && item.MapId != knownMapId || allowed != null && !allowed.Contains(item.MapId)) continue;
            if (clock.Elapsed > limit) { complete=false; break; }
            evaluated.Add(item.MapId);
            var poses = item.Hypotheses(query,Math.Min(pixels.Rows,pixels.Cols),cancellation);
            if (poses.Count == 0) continue;
            var best = poses[0];
            records.Add((item,new JsonObject { ["mapId"]=item.MapId,["profileId"]=catalog.MapProfiles[item.MapId],["entryKind"]="main",
                ["material"]=JsonNode.Parse(item.Row.GetProperty("material").GetRawText()),
                ["score"]=(double)best.Pairs.Length,["hypothesis"]=best.ToJson(),
                ["poseAmbiguous"]=poses.Count>1 && poses[1].Pairs.Length >= best.Pairs.Length-1,
                ["sourceProduct"]=item.Row.GetProperty("sourceProduct").GetString() }));
        }
        records = records.OrderByDescending(r=>Numerics.Scalar(r.Row["score"]!)).ToList();
        var competitive = records.TakeWhile(r=>Numerics.Scalar(r.Row["score"]!) >= Math.Max(10,.65*Numerics.Scalar(records[0].Row["score"]!))).ToList();
        if (complete && competitive.Count is >0 and <=3)
            foreach (var (item,row) in competitive)
            {
                cancellation.ThrowIfCancellationRequested();
                if (clock.Elapsed > limit) { complete=false; break; }
                var reference = resources.Find(catalog.Mode,catalog.Season,catalog.MapProfiles[item.MapId],item.MapId,floor);
                var data = reference == null ? null : resources.LoadSharedStructure(reference).Value;
                if (data?.CleanMask == null)
                    row["verification"]=new JsonObject {["success"]=false,["reason"]="entry-resource-unavailable"};
                else
                    using (var product = StructureGeometry.Open(data))
                        row["verification"]=MainEntryVerification.Verify(pixels,entry!.Geometry,row["hypothesis"]!,product);
            }
        var accepted = competitive.Where(r=>r.Row["verification"]?["success"]?.GetValue<bool>() == true).ToList();
        var winner = complete && accepted.Count == 1 ? accepted[0].Row : null;
        foreach (var (_, row) in records)
        {
            var h=row["hypothesis"]!;
            bool pose=ReferenceEquals(row,winner) && !row["poseAmbiguous"]!.GetValue<bool>() && h["neighborChains"]!.GetValue<int>()>=6 &&
                Numerics.Scalar(row["score"]!)>=10 && (h["mainChains"]!.GetValue<int>()>=3 ||
                h["neighborChains"]!.GetValue<int>()>=8 && h["neighborExtentGrid"]!.AsArray().Min(v=>Numerics.Scalar(v!))>=4);
            row["poseVerified"]=pose;
            row["identityVerified"]=pose && knownMapId==null;
        }
        foreach(var pair in records.OrderByDescending(r=>r.Row["identityVerified"]!.GetValue<bool>())) result.Add(pair.Row);
        diagnostics["complete"]=complete;
        diagnostics["evaluatedMapIds"]=evaluated;
        return Finish(knownMapId!=null && result.Any(r=>r!["poseVerified"]!.GetValue<bool>()) ? "entry-known-identity-pose-verified" :
            result.Any(r=>r!["identityVerified"]!.GetValue<bool>()) ? "entry-identity-verified" :
            complete ? "entry-neighborhood-candidates" : "entry-budget-incomplete");

        MainEntryResult Finish(string reason)
        {
            diagnostics["reason"]=reason;
            var expected=catalog.PackFloors.Where(p=>p.Floor==floor).Select(p=>p.MapId).ToHashSet(StringComparer.Ordinal);
            var available=sources.Where(s=>s.Floor==floor).Select(s=>s.MapId).ToHashSet(StringComparer.Ordinal);
            var missing=expected.Except(available).Order(StringComparer.Ordinal).ToArray();
            diagnostics["entryInventoryComplete"]=missing.Length==0;
            diagnostics["entryInventoryExpectedMapIds"]=JsonSerializer.SerializeToNode(expected.Order(StringComparer.Ordinal));
            diagnostics["entryInventoryAvailableMapIds"]=JsonSerializer.SerializeToNode(expected.Intersect(available).Order(StringComparer.Ordinal));
            diagnostics["entryInventoryMissingMapIds"]=JsonSerializer.SerializeToNode(missing);
            diagnostics["entryInventoryUnexpectedMapIds"]=JsonSerializer.SerializeToNode(available.Except(expected).Order(StringComparer.Ordinal));
            diagnostics["entryInventoryScope"]="shared-pack-floor-manifest";
            foreach (var v in result)
            {
                var row=v!.AsObject();
                if (missing.Length>0)
                {
                    row["entryInventoryComplete"]=false;
                    row["entryInventoryMissingMapIds"]=JsonSerializer.SerializeToNode(missing);
                    row["entryInventoryIdentitySuppressed"]=row["identityVerified"]!.DeepClone();
                    row["identityVerified"]=false; row["poseVerified"]=false;
                }
                if (row["identityVerified"]!.GetValue<bool>())
                {
                    var checkRow = new JsonObject { ["mapId"]=row["mapId"]!.DeepClone(),["floor"]=floor,
                        ["profileId"]=row["profileId"]!.DeepClone(),["matrixSourceToClient"]=row["verification"]!["matrixSourceToClient"]!.DeepClone() };
                    passportDiagnostics ??= passport.Inspect(floor,cancellation:cancellation).Diagnostics;
                    var check=EntryPassportEvidence.CheckObserved(catalog,checkRow,passportDiagnostics["observations"]?.AsArray()??new JsonArray());
                    row["entryCrossCheck"]=check;
                    if (check["contradicted"]!.GetValue<bool>()) { row["identityVerified"]=false; row["poseVerified"]=false; }
                }
            }
            if(missing.Length>0) diagnostics["entryInventoryProposalOnly"]=true;
            return new(result,diagnostics,gate,corners);
        }
    }
}
