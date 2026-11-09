using System.Text.Json.Nodes;
using OpenCvSharp;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

internal static class FloorObservationS1
{
    private static readonly int[] Order=[-1,-2,-3,-4];
    private sealed record Label(double X,double Y,double CenterX,double Aspect,string Polarity,Rect Sign,Rect Digit);
    private static JsonObject Reason(string reason)=>new(){["reason"]=reason};

    internal static JsonObject Read(Mat frame,string season,string mode,bool current)
    {
        var result=new JsonObject{["season"]=season,["mode"]=mode,["reason"]="s1-floor-context-unsupported",["selectedFloor"]=null,
            ["observedFloorValidated"]=false,["automaticFloorEligible"]=false,["capability"]="s1-floor-only-manual-map-use",["floorUse"]="manual-map-only",
            ["controlAxis"]="vertical",["observedOrder"]=J(Order),["mapOpen"]=null,["mapObservation"]=null,
            ["centerPoints"]=new JsonArray(),["centers"]=new JsonArray(),["labelEvidence"]=new JsonArray()};
        if(!current)return Fail("s1-floor-stale-frame");
        if(frame.Type()!=MatType.CV_8UC3||frame.Empty())return Fail("s1-floor-frame-type");
        int w=frame.Cols,h=frame.Rows;
        if(w<1000||h<600)return Fail("s1-floor-frame-size");
        var map=MapOpenObservation.Detect(frame,new());bool open=Flag(map,"detected");result["mapObservation"]=map;result["mapOpen"]=open;
        string? independent=null;
        if(!open && (independent=Independent(frame,map,null,null))==null)return Fail("s1-floor-map-not-open");
        using var gray=Gray(frame);var column=ObserveColumn(gray,w,h);bool localized=S(column,"localization")=="signed-column-position-scale";
        if(!open||localized)
        {
            var b=column["controlBounds"] is JsonArray bounds?Numbers(bounds).Select(x=>(int)x).ToArray():null;
            independent=Independent(frame,map,b,localized?N(column["controlLocalScale"]):null);
            if(independent==null)return Fail(localized&&open?"s1-floor-localization-ui-unverified":"s1-floor-map-not-open");
        }
        if(!open)
        {
            if(column["selectedFloor"]==null)return Fail("s1-floor-map-not-open");
            map["detected"]=true;map["reason"]="s1-map-interface-signed-controls";map["detectionMode"]=independent;result["mapOpen"]=true;
        }
        if(column["selectedFloor"]==null)return Fail(S(column,"reason")!);
        Merge(result,column);result["automaticFloorEligible"]=true;result["observedFloorValidated"]=true;
        result["validationScope"]="source-image-replay";result["floorSource"]="s1-signed-pixel-column"; return result;
        JsonObject Fail(string why){result["reason"]=why;return result;}
    }

    private static string? Independent(Mat frame,JsonObject map,int[]? b,double? scale)
    {
        if(!Flag(map,"shapeAccepted")||map["requiredBackdropRatio"]==null||N(map["backdropRatio"])<N(map["requiredBackdropRatio"]))return null;
        var anchors=MapOpenObservation.Anchors.Skip(1).ToArray();
        if(anchors.Any(a=>N(map["ratios"]![a.Name])<a.Threshold))return null;
        var view=Viewport(frame.Cols,frame.Rows);
        using var check=frame.Clone();
        var roi=Corners(R(view.X+view.Width*.80),R(view.Y+view.Height*.12),R(view.X+view.Width*.92),R(view.Y+view.Height*.56));
        using(var exclusion=new Mat(check,roi))exclusion.SetTo(Scalar.Black);
        using var shapeImage=b==null?null:frame.Clone();
        if(b!=null)
        {
            var extra=Corners(b[0],b[1],b[2],b[3]);
            if(extra.Width<1||extra.Height<1||extra.Intersect(new(0,0,frame.Cols,frame.Rows))!=extra)return null;
            using var band=new Mat(check,extra);band.SetTo(Scalar.Black);
            using var omit=new Mat(shapeImage!,extra);omit.SetTo(Scalar.Black);
        }
        var ratios=MapOpenObservation.Ratios(check,false);
        if(anchors.Any(a=>N(ratios[a.Name])<a.Threshold))return null;
        var geometry=MapOpenObservationShapes.Observe(shapeImage??frame,.92,600);
        if(!Flag(geometry,"verified"))return null;
        if(scale is {} f && b!=null)
        {
            var shapes=geometry["shapes"]!;double gap=(N(shapes["plus"]!["center"]![0])-(b[0]+b[2])/2d)/f;
            if(gap<.025||gap>.16||N(shapes["close"]!["center"]![1])>b[1]+.075*f||b[3]>N(shapes["minus"]!["center"]![1])+.075*f)return null;
        }
        return "s1-signed-controls-ui-geometry";
    }

    private static JsonObject ObserveColumn(Mat gray,int w,int h)
    {
        var rect=Corners(R(w*.80),R(h*.12),R(w*.92),R(h*.56));
        var original=Fixed(gray,rect,w,h);
        if(original["selectedFloor"]!=null||S(original,"reason")=="s1-floor-column-ambiguous")return original;
        using var patch=new Mat(gray,rect);double level=Quantile(Geometry.Bytes(patch),.998);
        if(level is >=75 and <150)
        {
            using var exposed=Expose(gray,150/level);var retry=Fixed(exposed,rect,w,h);
            if(retry["selectedFloor"]!=null||S(retry,"reason")=="s1-floor-column-ambiguous")return retry;
        }
        return Joint(gray,w,h)??original;
    }
    private static JsonObject Fixed(Mat gray,Rect rect,int w,int h)
    {
        using var patch=new Mat(gray,rect);var labels=Labels(Parts(patch),w,rect.X,rect.Y);
        if(labels.Count is <4 or >16)return Reason("s1-floor-signed-labels-incomplete");
        JsonObject? accepted=null;
        for(int i=0;i<labels.Count-3;i++)for(int j=i+1;j<labels.Count-2;j++)for(int k=j+1;k<labels.Count-1;k++)for(int l=k+1;l<labels.Count;l++)
        {
            var rows=new[]{labels[i],labels[j],labels[k],labels[l]}.OrderBy(r=>r.Y).ToArray();
            var gaps=rows.Zip(rows.Skip(1),(a,b)=>b.Y-a.Y).ToArray();
            if(rows.Max(r=>r.X)-rows.Min(r=>r.X)>w*.003 || gaps.Min()<w*.035||gaps.Max()>w*.055||gaps.Max()-gaps.Min()>w*.004||
                rows[0].Aspect>.45||rows.Skip(1).Any(r=>r.Aspect<.35))continue;
            var read=Evidence(gray,rows,w,w,h);
            if(read==null)continue;
            if(accepted!=null)return Reason("s1-floor-column-ambiguous");accepted=read;
        }
        return accepted??Reason("s1-floor-column-unverified");
    }
    private static JsonObject? Joint(Mat gray,int w,int h)
    {
        var rect=Corners(R(.55*w),R(.04*h),R(.985*w),R(.93*h));int v=Viewport(w,h).Width;
        var scales=new[]{.5,.65,.8,1,1.25,1.6,2}.Select(x=>R(v*x)).Distinct().Order().ToArray();
        var accepted=new List<(Label[] Rows,JsonObject Evidence)>();
        foreach(double gain in new[]{1d,1.25,1.6,2})
        {
            using var adjusted=gain==1?null:Expose(gray,gain);var image=adjusted??gray;using var patch=new Mat(image,rect);
            var parts=Parts(patch);if(parts.Any(g=>g.Rows.Count>4096))continue;
            var all=new Dictionary<(Rect,Rect,string),Label>();
            foreach(var scale in scales)
            {
                var measured=Labels(parts,scale,rect.X,rect.Y);if(measured.Count>16)continue;
                foreach(var row in measured) all[(row.Sign,row.Digit,row.Polarity)]=row;
            }
            var labels=all.Values.ToList();if(labels.Count is <4 or >32)continue;int hypotheses=0;
            foreach(var first in labels.Where(r=>r.Aspect<=.45))foreach(var second in labels)
            {
                double pitch=second.Y-first.Y,s=pitch/.045;
                if(second.Aspect<.35||s<.4*v||s>2.2*v||Math.Abs(first.X-second.X)>.003*s)continue;
                var group=new List<Label>{first,second};
                for(int i=2;i<4;i++){var hit=labels.Where(r=>r.Aspect>=.35&&Math.Abs(r.X-first.X)<=.003*s&&Math.Abs(r.Y-first.Y-i*pitch)<=.004*s).ToArray();
                    if(hit.Length!=1)break;group.Add(hit[0]);}
                if(group.Count!=4)continue;var rows=group.ToArray();var gaps=rows.Zip(rows.Skip(1),(a,b)=>b.Y-a.Y).ToArray();
                if(gaps.Max()-gaps.Min()>.004*s||rows.Max(r=>r.Digit.Height)>1.35*rows.Min(r=>r.Digit.Height))continue;
                if(labels.Any(r=>!group.Contains(r)&&Math.Abs(r.X-first.X)<=.003*s&&(Math.Abs(r.Y-first.Y+pitch)<=.004*s||Math.Abs(r.Y-first.Y-4*pitch)<=.004*s)))continue;
                if(++hypotheses>32)return Reason("s1-floor-localization-budget");
                var box=Corners(Math.Max(0,R(rows.Min(r=>r.CenterX)-.025*s)), Math.Max(0,R(first.Y-.025*s)),
                    Math.Min(w,R(rows.Max(r=>r.CenterX)+.025*s)),Math.Min(h,R(rows[^1].Y+.025*s)));
                using var cut=new Mat(image,box);var verified=Labels(Parts(cut),s,box.X,box.Y).ToHashSet();
                if(rows.Any(r=>!verified.Contains(r)))continue;
                var evidence=Evidence(image,rows,s,w,h);if(evidence==null)continue;
                evidence["localization"]="signed-column-position-scale";evidence["controlLocalScale"]=s;
                evidence["controlBounds"]=J(new[]{box.X,box.Y,box.Right,box.Bottom});evidence["controlSearchBounds"]=J(new[]{rect.X,rect.Y,rect.Right,rect.Bottom});
                evidence["controlSearchRegion"]=J(new[]{box.X/(double)w,box.Y/(double)h,box.Width/(double)w,box.Height/(double)h});
                var similar=accepted.Where(old=>old.Rows.Zip(rows).All(p=>Math.Abs(p.First.CenterX/(double)w-p.Second.CenterX/(double)w)*w<=.003*s &&
                    Math.Abs(p.First.Y/h-p.Second.Y/h)*h<=.004*s)).ToArray();
                if(similar.Length==0){accepted.Add((rows,evidence));if(accepted.Count>1)return Reason("s1-floor-column-ambiguous");}
                else if(similar.Any(old=>old.Evidence["selectedFloor"]!.GetValue<int>()!=evidence["selectedFloor"]!.GetValue<int>()))return Reason("s1-floor-column-ambiguous");
            }
        }
        return accepted.Count==0?null:accepted[0].Evidence;
    }

    private static (string Polarity,List<Blob> Rows)[] Parts(Mat patch)
    {
        using var light=Mask(patch,110);using var dark=Mask(patch,64,false);
        return [("light",Components(light)),("dark",Components(dark))];
    }
    private static List<Label> Labels((string Polarity,List<Blob> Rows)[] parts,double scale,int ox,int oy)
    {
        var labels=new List<Label>();
        foreach(var (polarity,stats) in parts)
        {
            var digits=stats.Where(v=>scale*.008<=v.H&&v.H<=scale*.017&&scale*.0015<=v.W&&v.W<=scale*.010&&
                scale*scale*.000006<=v.Area&&v.Area<=scale*scale*.00010).ToArray();
            var signs=stats.Where(v=>scale*.004<=v.W&&v.W<=scale*.009&&v.H>=1&&v.H<=scale*.003&&v.W>=2.5*v.H).ToArray();
            var matches=new List<(int D,int S)>();int[] degreeD=new int[digits.Length],degreeS=new int[signs.Length];
            double gapLimit=scale*.006,yLimit=scale*.004,cellW=Math.Max(1,gapLimit),cellH=Math.Max(1,2*yLimit);
            var cells=signs.Select((sign,i)=>(sign,i)).ToLookup(p=>((int)Math.Floor((p.sign.X+p.sign.W)/cellW),
                (int)Math.Floor((p.sign.Y+p.sign.H/2d)/cellH)));
            for(int d=0;d<digits.Length;d++)
            {
                var a=digits[d];double cy=a.Y+a.H/2d;
                for(int gx=(int)Math.Floor((a.X-gapLimit)/cellW);gx<=(int)Math.Floor(a.X/cellW);gx++)
                    for(int gy=(int)Math.Floor((cy-yLimit)/cellH);gy<=(int)Math.Floor((cy+yLimit)/cellH);gy++)
                        foreach(var(b,i) in cells[(gx,gy)])
                        {
                            int gap=a.X-(b.X+b.W);
                            if(gap<0||gap>gapLimit||Math.Abs(b.Y+b.H/2d-cy)>yLimit)continue;
                            matches.Add((d,i));degreeD[d]++;degreeS[i]++;
                        }
            }
            foreach(var(d,i)in matches)if(degreeD[d]==1&&degreeS[i]==1)
            {
                var a=digits[d];var b=signs[i];
                labels.Add(new(ox+b.X+b.W/2d,oy+a.Y+a.H/2d,ox+(b.X+a.X+a.W)/2d,a.W/(double)a.H,polarity,
                    new(ox+b.X,oy+b.Y,b.W,b.H),new(ox+a.X,oy+a.Y,a.W,a.H)));
            }
        }
        return labels;
    }

    private static JsonObject? Evidence(Mat image,Label[] rows,double scale,int w,int h)
    {
        var evidence=new JsonArray();int selected=0;
        for(int i=0;i<4;i++)
        {
            var r=rows[i];var box=Corners(R(r.CenterX-scale*.014),R(r.Y-scale*.013),R(r.CenterX+scale*.014),R(r.Y+scale*.013));
            if(box.Width<1||box.Height<1||box.Intersect(new(0,0,w,h))!=box)return null;
            using var patch=new Mat(image,box);using var mask=Mask(patch,80);
            double fill=Cv2.CountNonZero(mask)/(double)(box.Width*box.Height);if(fill is >.20 and <.65)return null;
            if(fill>=.65){if(selected!=0)return null;selected=Order[i];}
            evidence.Add(new JsonObject{["floor"]=Order[i],["negative"]=true,["digitAspect"]=r.Aspect,["polarity"]=r.Polarity,
                ["highlightFill"]=fill,["signBounds"]=J(new[]{r.Sign.X,r.Sign.Y,r.Sign.Width,r.Sign.Height}),
                ["digitBounds"]=J(new[]{r.Digit.X,r.Digit.Y,r.Digit.Width,r.Digit.Height})});
        }
        if(selected==0)return null;
        var points=J(rows.Select(r=>new[]{r.CenterX/w,r.Y/h}))!;
        return new(){["selectedFloor"]=selected,["reason"]="s1-floor-observed",["labelEvidence"]=evidence,["centerPoints"]=points,
            ["centers"]=points.DeepClone(),["controlBounds"]=J(new[]{Math.Max(0,R(rows.Min(r=>r.CenterX)-.025*w)),Math.Max(0,R(rows[0].Y-.025*w)),
                Math.Min(w,R(rows.Max(r=>r.CenterX)+.025*w)),Math.Min(h,R(rows[^1].Y+.025*w))})};
    }
}
