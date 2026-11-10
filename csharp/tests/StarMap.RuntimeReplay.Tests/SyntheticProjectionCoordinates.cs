using System.Reflection;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Display;
using StarMap.Runtime;

/// <summary>Relation data-only fixture. Actual native coordinate/resource files and solvers stay unchanged.</summary>
internal static class SyntheticProjectionCoordinates
{
    internal static void Install(LiveRuntime runtime,string resources,string mode)
    {
        string relative="references/nightmare-canonical-coordinate-pack.json";
        if(mode=="difficult")relative=JsonNode.Parse(File.ReadAllBytes(Path.Combine(resources,"difficult-native-manifest.json")))!["runtimeInterfaces"]!["coordinatePack"]!.GetValue<string>();
        var document=JsonNode.Parse(File.ReadAllBytes(Path.Combine(resources,relative)))!.AsObject();var maps=new JsonObject();
        foreach(var map in document["maps"]!.AsObject().Select(pair=>pair.Key))
        {
            var pairs=new JsonArray();
            foreach(var (from,to,dx,dy) in new[]{("1","2",7,-3),("2","1",-7,3)})pairs.Add(new JsonObject {
                ["fromFloor"]=from,["toFloor"]=to,["currentFrameSearchSeedAllowed"]=true,["directProjectionAllowed"]=false,
                ["zeroExplorationDisplayAllowed"]=false,["liveGameVerified"]=false,["reviewStatus"]="player-visual-pass-current-frame-required",
                ["targetToSourceMatrix"]=JsonSerializer.SerializeToNode(new[]{new[]{1d,0d,dx},new[]{0d,1d,dy},new[]{0d,0d,1d}}),
                ["evidence"]=new JsonObject {["directProjectionWithheldReason"]="explicitly synthetic test relation; no native review"}});
            maps[map]=new JsonObject {["pairs"]=pairs};
        }
        document["canonicalCrossFloorSearchCandidates"]=maps;
        var adapter=new AuthorCoordinateAdapter(JsonSerializer.SerializeToElement(document));
        var bound=runtime.GetType().GetField("_resources",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(runtime)!;
        bound.GetType().GetField("coordinates",BindingFlags.NonPublic|BindingFlags.Instance)!.SetValue(bound,new Lazy<AuthorCoordinateAdapter>(()=>adapter));
    }
}
