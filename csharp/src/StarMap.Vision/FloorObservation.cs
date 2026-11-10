using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Floors;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

public enum S1FloorUse { Unavailable, ManualMap, StructureCandidate }

public sealed record FloorObservationResult(JsonObject MapObservation,JsonObject FloorRead,string? FloorId)
{
    [System.Text.Json.Serialization.JsonIgnore]
    public MapCloseControlObservation? CloseControl {get;init;}
    public bool? MapOpen => MapObservation["mapOpen"]?.GetValue<bool>();
    public int? SelectedFloor => FloorRead["selectedFloor"]?.GetValue<int>();
    public double? Confidence => FloorRead["confidence"] is {} c ? Numerics.Scalar(c):null;
    public bool AutomaticEligible => FloorRead["automaticFloorEligible"]?.GetValue<bool>() ??
        (MapOpen==true && FloorRead["controlGeometryVerified"]?.GetValue<bool>()==true && SelectedFloor!=null);
}

/// <summary>Pure current-client BGR observation. No previous-frame state, pose, input or identity writes.</summary>
public static class FloorObservation
{
    /// <summary>Build the existing immutable S0 font model during resource binding, before any map-open deadline.</summary>
    public static void PrepareResources(string season)
    {
        if (season == "s0") FloorObservationGlyphs.Prepare();
    }

    public static FloorObservationResult Observe(Mat frame,string season,string mode,JsonObject? settings=null,
        S1FloorUse s1Use=S1FloorUse.Unavailable,bool frameCurrent=true)
    {
        var contract=FloorContract.Find(season,mode);
        if(contract==null)
            return season=="s1" ? Missing("s1-floor-control-detector-unvalidated") :
                Missing("floor-contract-unavailable", confidence:null);
        if(season=="s1")
        {
            if(s1Use==S1FloorUse.Unavailable)return Missing("s1-floor-control-detector-unvalidated");
            var read=FloorObservationS1.Read(frame,season,mode,frameCurrent);
            if(s1Use==S1FloorUse.ManualMap)
            {
                read["rawSelectedFloor"]=read["selectedFloor"]?.DeepClone();read["floorSource"]="current-frame";
                if(!Flag(read,"automaticFloorEligible"))read["selectedFloor"]=null;
            }
            else
            {
                if(read["selectedFloor"] is {} value && (contract.IdFor(value.GetValue<int>())==null || !Flag(read,"observedFloorValidated") ||
                    !Flag(read,"automaticFloorEligible")||!Flag(read,"mapOpen")))
                {read["selectedFloor"]=null;read["reason"]="s1-floor-current-evidence-unvalidated";}
                read["controlAxis"]="vertical";read["floorSource"]="current-frame-structure-candidate";
            }
            read["controlGeometryVerified"]=false;
            string? floor=contract.IdFor(read["selectedFloor"]?.GetValue<int>());
            var map=read["mapObservation"]?.DeepClone().AsObject()??new();
            map["detected"]=Flag(read,"mapOpen");map["mapOpen"]=s1Use==S1FloorUse.ManualMap?read["mapOpen"]?.DeepClone():JsonValue.Create(Flag(read,"mapOpen"));
            map["floor"]=floor;map["layoutFamily"]="s1-vertical";if(s1Use==S1FloorUse.StructureCandidate)map["controlAxis"]="vertical";
            return new(map,read,floor){CloseControl=frameCurrent?MapCloseControl.Observe(frame):null};
        }
        if(frame.Empty()||frame.Type()!=MatType.CV_8UC3)throw new ArgumentException("Expected a complete BGR8 client frame.",nameof(frame));
        if(!frameCurrent)return Missing("stale-client-frame");
        var current=FloorObservationS0.Read(frame,contract,settings);
        var observed=MapOpenObservation.Detect(frame,current);
        CheckUiGeometry(frame,contract,current,observed);
        var bound=Bind(current,contract,frame.Size(),Flag(observed,"detected"));
        string? id=contract.IdFor(bound["selectedFloor"]?.GetValue<int>());
        observed["mapOpen"]=Flag(observed,"detected");observed["floor"]=id;observed["layoutFamily"]="s0-vertical";observed["controlAxis"]="vertical";
        return new(observed,bound,id){CloseControl=MapCloseControl.Observe(frame)};

        FloorObservationResult Missing(string reason, double? confidence=0d)
        {
            var read = new JsonObject {
                ["selectedFloor"]=null, ["reason"]=reason, ["season"]=season, ["mode"]=mode,
                ["controlAxis"]="vertical", ["controlGeometryVerified"]=false, ["floorSource"]="unavailable"
            };
            if(confidence.HasValue)read["confidence"]=confidence.Value;
            return new(new(){["detected"]=false,["mapOpen"]=null,["reason"]=reason},read,null);
        }
    }

    private static JsonObject Bind(JsonObject current,FloorContract contract,Size size,bool open)
    {
        var read=current.DeepClone().AsObject();read["season"]="s0";read["mode"]=contract.Mode;read["mapOpen"]=open;
        read["layout"]="vertical-floor-read-only";read["controlGeometryVerified"]=false;
        var order=contract.ColumnOrder;
        if(!open||read["selectedFloor"] is not JsonValue v||!v.TryGetValue<int>(out int selected)||!order.Contains(selected)||
            S(read,"controlAxis")!="vertical"||size.Width<100||size.Height<100||read["observedOrder"] is not JsonArray seq||
            !seq.Select(x=>x!.GetValue<int>()).SequenceEqual(order)|| read["centerPoints"] is not JsonArray points || points.Count!=order.Count ||
            read["labelEvidence"] is not JsonArray evidence || evidence.Count!=order.Count || read["tileBounds"] is not JsonArray tile)return Reject();
        var b=Numbers(tile);
        if(b.Length!=4||b.Any(n=>!double.IsFinite(n))||!(0<=b[0]&&b[0]<b[0]+b[2]&&b[0]+b[2]<=1&&0<=b[1]&&b[1]<b[1]+b[3]&&b[1]+b[3]<=1))return Reject();
        double[][] xy=points.Select(p=>Numbers(p!)).ToArray();int index=0;
        for(int i=0;i<order.Count;i++)
        {
            if(xy[i].Length!=2||xy[i].Any(n=>!double.IsFinite(n)||n<=0||n>=1)||Math.Abs(xy[i][0]-(b[0]+b[2]/2))>b[2]*.1 ||
                evidence[i]!["floor"]!.GetValue<int>()!=order[i]||evidence[i]!["negative"]!.GetValue<bool>()!=(order[i]<0))return Reject();
            if(order[i]==selected)index=i;
        }
        if(Math.Abs(xy[index][1]-(b[1]+b[3]/2))>b[3]*.1)return Reject();
        var gaps=xy.Zip(xy.Skip(1),(a,z)=>z[1]-a[1]).ToArray();if(gaps.Min()<=0||gaps.Max()>gaps.Min()*1.2)return Reject();
        double half=Math.Min(b[3]*.4,gaps.Min()*.4);var rects=new JsonArray();double[]? previous=null;
        for(int i=0;i<xy.Length;i++)
        {
            var p=xy[i];double[] r=[p[0]-b[2]*.4,p[1]-half,p[0]+b[2]*.4,p[1]+half];
            if(!(0<=r[0]&&r[0]<r[2]&&r[2]<=1&&0<=r[1]&&r[1]<r[3]&&r[3]<=1)||
                previous!=null && !(previous[3]<r[1]&&Math.Max(previous[0],r[0])<Math.Min(previous[2],r[2])))return Reject();
            rects.Add(new JsonObject{["floor"]=order[i],["rect"]=J(r)});previous=r;
        }
        read["layout"]="localized-s0-vertical";read["controlFrameSize"]=J(new[]{size.Width,size.Height});read["controlRects"]=rects;
        read["controlGeometryVerified"]=true;return read;
        JsonObject Reject(){read["selectedFloor"]=null;read["reason"]="vertical-control-geometry-unverified";return read;}
    }

    private static void CheckUiGeometry(Mat frame,FloorContract contract,JsonObject read,JsonObject observation)
    {
        bool tinted=S(observation,"detectionMode")=="damage-tint";
        if(read.ContainsKey("controlExposure")||tinted){observation["detected"]=false;observation["reason"]="map-evidence-insufficient";}
        if(Flag(observation,"detected")||!Flag(observation,"shapeAccepted"))return;
        var bound=Bind(read,contract,frame.Size(),true);if(!Flag(bound,"controlGeometryVerified"))return;
        var tile=Numbers(read["tileBounds"]!);int w=frame.Cols,h=frame.Rows;
        int l=Math.Max(0,(int)Math.Floor((tile[0]-tile[2]*.25)*w)),r=Math.Min(w,(int)Math.Ceiling((tile[0]+tile[2]*1.25)*w));
        using var independent=frame.Clone();using(var mask=new Mat(independent,new Rect(l,0,r-l,h)))mask.SetTo(Scalar.Black);
        foreach(double gain in new[]{1d,2d/3,1.5})
        {
            using var changed=gain==1?null:Expose(independent,gain);var probe=changed??independent;
            var ratios=MapOpenObservation.Ratios(probe,tinted);ratios.Remove("floorSwitch");
            if(MapOpenObservation.Anchors.Skip(1).Any(a=>N(ratios[a.Name])<a.Threshold))continue;
            double backdrop=MapOpenObservation.Backdrop(probe).Ratio;if(backdrop<N(observation["requiredBackdropRatio"]))continue;
            var view=Viewport(w,h);double floorRight=Math.Max(.92,(r-view.X)/(double)view.Width);if(floorRight>=.99)return;
            var geometry=MapOpenObservationShapes.Observe(frame,floorRight,540);if(!Flag(geometry,"verified"))return;
            observation["detected"]=true;observation["reason"]="map-interface-detected";observation["detectionMode"]="s0-signed-controls-ui-geometry";
            observation["independentAnchorRatios"]=ratios;observation["independentBackdropRatio"]=backdrop;observation["mapControlExposure"]=gain;
            observation["independentFloorExclusion"]=J(new[]{l,0,r,h});observation["mapUiGeometry"]=geometry;return;
        }
    }
}
