using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;
using static StarMap.Core.Identity.NativeIdentityJson;

namespace StarMap.Vision;

internal sealed class S0SourceStructureAdvisory
{
    private readonly EntryPassportCatalog catalog;
    private readonly JsonObject? featureStore;
    internal S0SourceStructureAdvisory(EntryPassportCatalog catalog)
    {
        this.catalog=catalog;
        if(catalog.Mode!="nightmare") return;
        // NightmareSourceCandidateStore loads its declaration while preparing
        // the engine, before the per-frame shared request budget begins.
        try
        {
            var index=JsonNode.Parse(File.ReadAllBytes(Path.Combine(catalog.ResourceRoot,"references","nightmare-source-candidate-index.json")))!.AsObject();
            if(Number(index["schemaVersion"])!=1) return;
            var keys=new HashSet<(string,string,string)>();
            foreach(var row in Array(index["entries"]))
            {
                if(row is not JsonObject entry || !entry.ContainsKey("sourceGroup") || !entry.ContainsKey("variant") || !entry.ContainsKey("sourceFloor") ||
                    !keys.Add((Text(row,"sourceGroup"),Text(row,"variant"),Text(row,"sourceFloor")))) return;
            }
            featureStore=index["featureStore"]?.DeepClone() as JsonObject;
        }
        catch(Exception error) when(error is IOException or System.Text.Json.JsonException or InvalidOperationException or FormatException)
        { /* Original store keeps no feature config after a malformed/read-failed index. */ }
    }
    internal JsonObject Observe(TimeSpan remaining)
    {
        var clock=Stopwatch.StartNew();
        JsonObject Finish(string reason,bool complete,JsonObject? status=null)
        {
            var row=new JsonObject { ["reason"]=reason,["complete"]=complete,["evaluatedEntryCount"]=0,["candidates"]=new JsonArray(),["elapsedMs"]=clock.Elapsed.TotalMilliseconds };
            if(status is not null) row["featureStatus"]=status; return row;
        }
        // NativeDifficultPack does not install the NightmareSourceCandidateStore.
        if(catalog.Mode!="nightmare") return Finish("source-candidate-store-unavailable",true);
        if(remaining.TotalSeconds<.04) return Finish("shared-request-budget-exhausted",false);
        if(featureStore is not { } config)
            return Finish("source-feature-cache-unavailable",true,new() { ["attempted"]=true,["ready"]=false,["reason"]="feature-store-unavailable" });
        // A genuinely installed future store cannot be disguised as missing.
        // Its SIFT/held-out numerical producer is a separate explicit capability.
        return Finish("source-feature-implementation-unavailable",false,new()
        { ["attempted"]=true,["ready"]=false,["reason"]="source-sift-heldout-implementation-unavailable",["configuredStore"]=config.DeepClone(),["runtimeOpenCVVersion"]=Cv2.GetVersionString() });
    }
}
