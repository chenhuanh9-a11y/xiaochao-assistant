using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Vision;

internal static class MapCloseControlRegression
{
    internal static async Task Run(string oracle,string output,Action<bool,string> check)
    {
        var root=JsonNode.Parse(await File.ReadAllBytesAsync(oracle))!;var evidence=new JsonArray();var observedModes=new HashSet<string>();
        FloorObservation.PrepareResources("s0");
        foreach(var row in root["cases"]!.AsArray())
        {
            string name=row!["name"]!.GetValue<string>();
            using var pixels=Cv2.ImDecode(await File.ReadAllBytesAsync(row["image"]!.GetValue<string>()),ImreadModes.Color);
            var observed=MapCloseControl.Observe(pixels);
            MapCloseControlBounds? expected=row["rectangle"] is JsonArray rect
                ?new(rect[0]!.GetValue<int>(),rect[1]!.GetValue<int>(),rect[2]!.GetValue<int>(),rect[3]!.GetValue<int>()):null;
            check(observed.Bounds==expected && observed.Width==row["width"]!.GetValue<int>() && observed.Height==row["height"]!.GetValue<int>(),
                name+" unique visible X matches the actual original detector");
            foreach(var hit in row["hits"]!.AsArray())
            {
                var xy=hit!["point"]!.AsArray();var bounds=hit["bounds"]!.AsArray().Select(v=>v!.GetValue<double>()).ToArray();
                check(MapCloseControl.Hit(observed,xy[0]!.GetValue<double>(),xy[1]!.GetValue<double>(),bounds)==hit["expected"]!.GetValue<bool>(),
                    name+" original close hit/"+hit["name"]!.GetValue<string>());
            }
            if(row["season"]?.GetValue<string>() is { } season && row["mode"]?.GetValue<string>() is { } mode && observedModes.Add(season+"/"+mode))
            {
                var result=FloorObservation.Observe(pixels,season,mode,s1Use:S1FloorUse.StructureCandidate);
                check(result.CloseControl==observed,season+"/"+mode+" current production floor observation prepares the same independent close evidence");
                check(FloorObservation.Observe(pixels,season,mode,s1Use:S1FloorUse.StructureCandidate,frameCurrent:false).CloseControl is null,
                    season+"/"+mode+" explicitly stale production observation publishes no close evidence");
                if(season=="s1")
                    check(FloorObservation.Observe(pixels,season,mode,s1Use:S1FloorUse.ManualMap).CloseControl==observed,
                        season+"/"+mode+" manual observation prepares X without granting floor-control geometry");
            }
            evidence.Add(JsonSerializer.SerializeToNode(new {name,observed,hits=row["hits"]!.AsArray().Count}));
        }
        check(observedModes.Count==4,"all four recorded season/mode observation paths prepare close evidence");
        var missing=MapCloseControl.Observe(null);
        check(missing.Bounds is null && !MapCloseControl.Hit(missing,0,0,[0,0,100,100])
            && !MapCloseControl.Hit(new(100,100,new(1,1,10,10)),double.NaN,5,[0,0,100,100]),
            "missing pixels and nonfinite pointer cannot grant a close");
        await File.WriteAllTextAsync(Path.Combine(output,"map-close-detector.json"),evidence.ToJsonString(new(){WriteIndented=true}));
    }
}
