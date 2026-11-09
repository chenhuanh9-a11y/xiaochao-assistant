using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Identity;

namespace StarMap.Vision;

/// <summary>The production side-structure evidence consumer. It owns no session/display state.</summary>
public static class SideEntryDecision
{
    public static JsonObject? VerifiedStructureCandidate(EntryPassportCatalog catalog,JsonArray rows,JsonObject diagnostics,string floor,
        JsonArray mainRows,Size frameSize,bool speedFirst=false)
    {
        if(!catalog.Floors.Contains(floor)) return null;
        var all=rows.Select(r=>r!.AsObject()).ToList();
        if(speedFirst)
        {
            var first=all.OrderByDescending(Score).FirstOrDefault(Valid);
            return first==null?null:Winner(first,true,"first-verified-side-structure-and-current-author");
        }
        if(!True(diagnostics,"complete") || !True(diagnostics,"verificationComplete") || all.Count==0) return null;
        double top=all.Max(Score);
        var competitors=all.Where(r=>Score(r)>=Math.Max(4,top-1)).ToList();
        if(diagnostics["competitiveHypotheses"]?.GetValue<int>()!=competitors.Count) return null;
        var remaining=competitors.Where(r=>!True(r["entryCrossCheck"],"contradicted")).ToList();
        if(remaining.Count==competitors.Count)
        { if(True(diagnostics,"identityAmbiguous")) return null; }
        else
        {
            competitors=remaining;
            if(competitors.Where(r=>True(r["verification"],"success")).Select(r=>(r["mapId"]!.ToString(),r["floor"]!.ToString())).Distinct().Count()>1) return null;
        }
        if(competitors.Count>1)
        {
            if(competitors.Select(r=>(r["mapId"]!.ToString(),r["floor"]!.ToString())).Distinct().Count()!=1 ||
                competitors.Any(r=>!True(r,"competitionComplete") || !Valid(r))) return null;
            if(mainRows.Any(r=>True(r!["verification"],"success") && r["mapId"]!.ToString()!=competitors[0]["mapId"]!.ToString() && !True(r["entryCrossCheck"],"contradicted"))) return null;
            return Winner(competitors[0],false,"complete-same-map-side-competition-pose-unresolved");
        }
        if(competitors.Count!=1) return null;
        var candidate=competitors[0];
        if(!True(candidate,"competitionComplete") || True(candidate,"poseAmbiguous") || !Valid(candidate)) return null;
        var matrix=Numerics.Matrix(candidate["verification"]!["matrixSourceToClient"]!.AsArray());
        Point2d[] extent=[new(0,0),new(frameSize.Width,0),new(0,frameSize.Height),new(frameSize.Width,frameSize.Height)];
        foreach(var main in mainRows)
        {
            if(True(main!["entryCrossCheck"],"contradicted") || !True(main["verification"],"success")) continue;
            if(main["mapId"]!.ToString()!=candidate["mapId"]!.ToString() || !Finite(main["verification"]!["matrixSourceToClient"])) return null;
            var other=Numerics.Matrix(main["verification"]!["matrixSourceToClient"]!.AsArray());
            if(!TryRelative(matrix,other,out var relative) || extent.Any(p=>Numerics.Norm(Project(p,relative)-p)>3)) return null;
        }
        return Winner(candidate,true,"complete-unique-side-structure-and-current-author");

        bool Valid(JsonObject row)=>row["floor"]?.ToString()==floor && row["mapId"] is { } id && catalog.MapProfiles.TryGetValue(id.ToString(),out var profile) && row["profileId"]?.ToString()==profile &&
            row["hypothesis"]?["chainCount"]?.GetValue<int>()>=6 && row["hypothesis"]?["extentGrid"] is JsonArray {Count:2} e && e.Min(v=>Numerics.Scalar(v!))>=2 &&
            True(row["verification"],"success") && True(row["verification"]?["residual"],"verified") && Finite(row["verification"]?["matrixSourceToClient"]);
    }

    public static bool EquivalentObservedPoses(JsonArray rows,IReadOnlyList<Point2d> points)
    {
        if(rows.Count==0 || points.Count==0 || rows.Select(r=>r!["mapId"]!.ToString()).Distinct().Count()!=1) return false;
        if(!Finite(rows[0]!["verification"]?["matrixSourceToClient"])) return false;
        var first=Numerics.Matrix(rows[0]!["verification"]!["matrixSourceToClient"]!.AsArray());
        foreach(var row in rows.Skip(1))
        {
            if(!Finite(row!["verification"]?["matrixSourceToClient"])) return false;
            var matrix=Numerics.Matrix(row["verification"]!["matrixSourceToClient"]!.AsArray());
            if(!TryRelative(matrix,first,out var relative) || points.Any(p=>Numerics.Norm(Project(p,relative)-p)>3)) return false;
        }
        return true;
    }
    private static bool TryRelative(double[][] left,double[][] domain,out double[][] result)
    {
        using var m=new Mat(3,3,MatType.CV_64FC1); using var inv=new Mat();
        for(int y=0;y<3;y++) for(int x=0;x<3;x++) m.Set(y,x,domain[y][x]);
        if(Cv2.Invert(m,inv,DecompTypes.LU)==0) { result=[]; return false; }
        result=Enumerable.Range(0,3).Select(y=>Enumerable.Range(0,3).Select(x=>Enumerable.Range(0,3).Sum(k=>left[y][k]*inv.At<double>(k,x))).ToArray()).ToArray();
        return result.SelectMany(r=>r).All(double.IsFinite);
    }
    private static JsonObject Winner(JsonObject candidate,bool pose,string evidence)
    {
        var result=candidate.DeepClone().AsObject(); result["identityVerified"]=true; result["poseVerified"]=pose; result["identityOnly"]=!pose;
        result["locatorKind"]="side"; result["identityEvidence"]=evidence; return result;
    }
    private static bool Finite(JsonNode? node)=>node is JsonArray {Count:3} rows && rows.All(row=>row is JsonArray {Count:3} r && r.All(v=>v!=null && double.TryParse(v.ToString(),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out double n) && double.IsFinite(n)));
    private static Point2d Project(Point2d point,double[][] m)=>new(point.X*m[0][0]+point.Y*m[0][1]+m[0][2],point.X*m[1][0]+point.Y*m[1][1]+m[1][2]);
    private static double Score(JsonObject row)=>Numerics.Scalar(row["score"]!);
    private static bool True(JsonNode? row,string key)=>row?[key]?.GetValue<bool>()==true;
}
