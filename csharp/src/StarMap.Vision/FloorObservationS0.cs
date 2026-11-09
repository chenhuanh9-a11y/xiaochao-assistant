using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Floors;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

internal static class FloorObservationS0
{
    private static readonly double[] Default=[.75,.15,.18,.45],Expanded=[.55,.04,.435,.89];
    private static JsonObject Unread(string reason)=>new(){["selectedFloor"]=null,["reason"]=reason,["confidence"]=0d};
    internal static JsonObject Read(Mat frame,FloorContract contract,JsonObject? settings)
    {
        var first=Locate(Default,"auto-located",false);
        first["season"]=contract.Season;first["mode"]=contract.Mode;first["layout"]="vertical-floor-read-only";first["controlGeometryVerified"]=false;
        string choice=settings?["floorControlMode"]?.ToString()??"auto";
        if(choice=="fixed"){first["floorSource"]="fixed";return first;}
        string source="fixed";
        JsonObject? regionRead=null;
        if(choice=="region")
        {
            var box=SavedRegion(settings?["floorControlRegion"], frame.Size());
            if(box==null)source="fixed-region-unavailable";
            else
            {
                regionRead=Locate(box,"region-highlight-read",false);
                if(regionRead["selectedFloor"]!=null || S(regionRead,"reason")=="columns-ambiguous")
                {
                    var result=regionRead.DeepClone().AsObject();result["regionRead"]=regionRead;result["floorSource"]="region";
                    result["season"]=contract.Season;result["mode"]=contract.Mode;return result;
                }
                source="fixed-region-unread";
            }
        }
        if(regionRead!=null)first["regionRead"]=regionRead;
        if(first["selectedFloor"]!=null || S(first,"reason")=="columns-ambiguous"){first["floorSource"]=source;return first;}
        var auto=Locate(Expanded,"auto-located",true);
        if(auto["selectedFloor"]==null){first["floorSource"]=source+"-auto-unread";first["autoRead"]=auto;return first;}
        var recovered=auto.DeepClone().AsObject();recovered["autoRead"]=auto;recovered["floorSource"]="auto-located";
        recovered["season"]=contract.Season;recovered["mode"]=contract.Mode;if(regionRead!=null)recovered["regionRead"]=regionRead.DeepClone();
        return recovered;

        JsonObject Locate(double[] box,string reason,bool expanded)
        {
            int w=frame.Cols,h=frame.Rows;var roi=Region(new Rect(0,0,w,h),box[0],box[1],box[2],box[3]).Intersect(new(0,0,w,h));
            JsonObject read;
            if(roi.Width<24 || roi.Height<10)read=Unread("region-too-small");
            else
            {
                using var crop=new Mat(frame,roi);using var gray=Gray(crop);
                read=Contrast(gray,roi,frame.Size(),contract,reason);
            }
            if(!expanded){read["controlSearchRegion"]=J(box);return read;}
            read["controlSearchBounds"]=J(new[]{roi.X,roi.Y,roi.Right,roi.Bottom});
            if(read["selectedFloor"]!=null)
            {
                var b=Numbers(read["tileBounds"]!);var ys=read["centerPoints"]!.AsArray().Select(p=>N(p![1])).ToArray();
                double l=Math.Max(0,b[0]-.3*b[2]),r=Math.Min(1,b[0]+1.3*b[2]),t=Math.Max(0,ys.Min()-.7*b[3]),bo=Math.Min(1,ys.Max()+.7*b[3]);
                read["controlSearchRegion"]=J(new[]{l,t,r-l,bo-t});
            }
            return read;
        }
    }

    private static double[]? SavedRegion(JsonNode? setting, Size clientSize)
    {
        if (setting is not JsonObject saved) return null;
        var box = RegionValues(saved["rect"], 4, integer: false);
        var dims = RegionValues(saved["clientSize"], 2, integer: true);
        if (box == null || dims == null || dims[0] <= 0 || dims[1] <= 0) return null;
        if (box[0] < 0 || box[1] < 0 || box[2] < .01 || box[3] < .005 ||
            box[0] + box[2] > 1.0001 || box[1] + box[3] > 1.0001) return null;
        double aspectDifference = (clientSize.Width / (double)clientSize.Height) / (dims[0] / dims[1]) - 1;
        return Math.Abs(aspectDifference) <= .02 ? box : null;
    }

    private static double[]? RegionValues(JsonNode? sequence, int count, bool integer)
    {
        // The original unpacks each JSON iterable. Arrays yield values, objects yield keys,
        // and strings yield characters. A non-iterable or a wrong length invalidates the region.
        IEnumerable<JsonNode?> items = sequence switch
        {
            JsonArray a => a,
            JsonObject o => o.Select(kv => JsonValue.Create(kv.Key)),
            JsonValue s when s.TryGetValue<string>(out var text) => text.Select(c => JsonValue.Create(c.ToString())),
            _ => []
        };
        var values = new double[count];
        int index = 0;
        foreach (var item in items)
        {
            if (index == count || !TryRegionNumber(item, integer, out values[index])) return null;
            index++;
        }
        return index == count ? values : null;
    }

    private static bool TryRegionNumber(JsonNode? item, bool integer, out double number)
    {
        number = 0;
        if (item is not JsonValue value) return false;
        switch (value.GetValueKind())
        {
            case JsonValueKind.True:
                number = 1;
                return true;
            case JsonValueKind.False:
                return true;
            case JsonValueKind.Number:
                if (!double.TryParse(value.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out number) ||
                    !double.IsFinite(number)) return false;
                if (integer) number = Math.Truncate(number);
                return true;
            case JsonValueKind.String:
                string text = value.GetValue<string>().Trim();
                for (int i = 0; i < text.Length; i++)
                {
                    if (text[i] == '_' && (i == 0 || i + 1 == text.Length ||
                        !char.IsDigit(text[i - 1]) || !char.IsDigit(text[i + 1]))) return false;
                }
                text = text.Replace("_", "");
                if (integer)
                {
                    if (!System.Numerics.BigInteger.TryParse(text, NumberStyles.AllowLeadingSign,
                            CultureInfo.InvariantCulture, out var dimension)) return false;
                    number = (double)dimension;
                    return double.IsFinite(number);
                }
                return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out number) &&
                    double.IsFinite(number);
            default:
                return false;
        }
    }

    private static JsonObject Contrast(Mat gray,Rect roi,Size canvas,FloorContract contract,string reason)
    {
        var original=Vertical(gray,roi,canvas,contract,reason);
        if(S(original,"reason")=="columns-ambiguous")return original;
        var accepted=new List<(JsonObject Row,double Gain)>();if(original["selectedFloor"]!=null)accepted.Add((original,1));
        foreach(double gain in new[]{2d/3,1.5})
        {
            using var exposed=Expose(gray,gain);var row=Vertical(exposed,roi,canvas,contract,reason);
            bool conflict=S(row,"reason")=="columns-ambiguous";
            if(row["selectedFloor"]!=null)
            {
                foreach(var (old,_) in accepted)
                {
                    var ob=Numbers(old["tileBounds"]!);var b=Numbers(row["tileBounds"]!);
                    var oa=old["centerPoints"]!.AsArray();var a=row["centerPoints"]!.AsArray();
                    conflict|=old["selectedFloor"]!.GetValue<int>()!=row["selectedFloor"]!.GetValue<int>() ||
                        oa.Zip(a).Any(p=>Math.Abs(N(p.First![0])-N(p.Second![0]))>Math.Max(ob[2],b[2])*.25 ||
                            Math.Abs(N(p.First![1])-N(p.Second![1]))>Math.Max(ob[3],b[3])*.25);
                }
                accepted.Add((row,gain));
            }
            if(conflict){var failed=Unread("columns-ambiguous");failed["controlExposure"]=gain;return failed;}
        }
        if(accepted.Count==0)return original;
        var (result,exposure)=accepted[0];
        if(exposure!=1){result["controlExposure"]=exposure;result["controlBackground"]=Background(gray,result,roi,canvas);}
        return result;
    }

    private static JsonObject Vertical(Mat gray,Rect roi,Size canvas,FloorContract contract,string reason)
    {
        using var transposed=new Mat();Cv2.Transpose(gray,transposed);
        using var mask=Mask(transposed,62);using var opened=new Mat();
        using var kernel=Geometry.Kernel(Math.Max(3,R(canvas.Width*.0036)|1));Cv2.MorphologyEx(mask,opened,MorphTypes.Open,kernel);
        var tiles=Components(opened).Where(b=>b.H>=canvas.Width*.010 && b.H<=canvas.Width*.06 && b.W/(double)b.H is >=.7 and <=2.8 &&
            Dense(b)).OrderByDescending(b=>b.Area).Take(8);
        JsonObject? first=null, accepted=null;
        foreach(var tile in tiles)
        {
            double center=tile.X+tile.W/2d,pitch=tile.W*.94;
            var (before,cl)=Walk(-1);var (after,cr)=Walk(1);int total=contract.ColumnOrder.Count;
            if(before+after+1!=total)
            {
                if(first==null){first=Unread(before+after+1>total?"too-many-buttons":"buttons-incomplete");first["left"]=before;first["right"]=after;
                    first["clippedLeft"]=cl;first["clippedRight"]=cr;}continue;
            }
            var band=new List<byte>();
            Append(Math.Max(0,(int)(center-(before+.5)*pitch)),tile.X);
            Append(tile.X+tile.W,Math.Min(transposed.Cols,(int)(center+(after+.5)*pitch)+1));
            if(band.Count>0 && Quantile(band.ToArray(),.5)>75){first??=Unread("light-background");continue;}
            using var selected=new Mat(transposed,new Rect(tile.X,tile.Y,tile.W,tile.H));
            double fill=Quantile(Geometry.Bytes(selected),.5);
            int fw=canvas.Width,fh=canvas.Height;
            double[] bounds=[(roi.X+tile.Y)/(double)fw,(roi.Y+tile.X)/(double)fh,tile.H/(double)fw,tile.W/(double)fh];
            double[][] points=Enumerable.Range(0,total).Select(i=>new[]{bounds[0]+bounds[2]/2,(roi.Y+center+(i-before)*pitch)/fh}).ToArray();
            var evidence=new JsonArray();var scores=new List<double>();
            foreach(var (floor,p) in contract.ColumnOrder.Zip(points))
            {
                double cx=p[0]*fw-roi.X,cy=p[1]*fh-roi.Y,tw=bounds[2]*fw,th=bounds[3]*fh;
                var r=Corners((int)(cx-tw*.35),(int)(cy-th*.30),(int)(cx+tw*.35),(int)(cy+th*.30));
                if(r.Width==0||r.Height==0||r.Intersect(new(0,0,gray.Cols,gray.Rows))!=r)break;
                using var patch=new Mat(gray,r);var match=FloorObservationGlyphs.Classify(patch,floor==contract.ColumnOrder[before]);
                if(match==null||match.Label!=floor)break;
                scores.Add(match.Score);evidence.Add(new JsonObject{["floor"]=floor,["negative"]=floor<0,["digitAspect"]=match.Aspect,["score"]=match.Score});
            }
            if(scores.Count!=total||scores.Min()<.45||scores.Count(x=>x>=.70)<total-1||scores.Average()<.70)
            {first??=Unread("vertical-labels-unverified");continue;}
            int current=contract.ColumnOrder[before];
            var read=new JsonObject{["selectedFloor"]=current,["floorName"]=contract.IdFor(current),["reason"]=reason,["confidence"]=Math.Clamp((fill-62)/30,0,1),
                ["controlAxis"]="vertical",["observedOrder"]=J(contract.ColumnOrder),["tileBounds"]=J(bounds),["centerPoints"]=J(points),
                ["axisPitch"]=pitch/fh,["before"]=before,["after"]=after,["labelEvidence"]=evidence,["controlGeometryVerified"]=false};
            var background=Background(gray,read,roi,canvas);
            if(background==null || fill-background<12){first??=Unread("highlight-not-found");continue;}
            if(accepted!=null)return Unread("columns-ambiguous");
            accepted=read;

            (int Found,bool Clipped) Walk(int direction)
            {
                int n=0;
                for(int step=1;step<=contract.ColumnOrder.Count;step++)
                {
                    var present=Labelled(transposed,center+direction*step*pitch,tile);
                    if(present!=true)return(n,present==null);n++;
                }
                return(n,false);
            }
            void Append(int l,int r)
            {
                if(r<=l)return;using var block=new Mat(transposed,new Rect(l,tile.Y,r-l,tile.H));band.AddRange(Geometry.Bytes(block));
            }
        }
        return accepted??first??Unread("highlight-not-found");
        bool Dense(Blob tile){using var box=new Mat(mask,new Rect(tile.X,tile.Y,tile.W,tile.H));return Cv2.Mean(box).Val0>=.6;}
    }

    private static bool? Labelled(Mat gray,double center,Blob tile)
    {
        double half=tile.W/2d;if(center-half*.6<0||center+half*.6>gray.Cols)return null;
        foreach(double shift in new[]{0,-.15,.15,-.3,.3})
        {
            double x=center+shift*tile.W;int l=R(x-half*.6),r=R(x+half*.6);
            if(l<0||r>gray.Cols||r<=l||l<tile.X+tile.W&&r>tile.X)continue;
            using var patch=new Mat(gray,new Rect(l,tile.Y,r-l,tile.H));var pixels=Geometry.Bytes(patch);
            double mid=Quantile(pixels,.5),ink=Quantile(pixels,.99);
            if(ink-mid<24||pixels.Count(v=>v>62&&v<=140)/(double)pixels.Length>=.12)continue;
            double threshold=Math.Max(mid+12,Math.Min(140,mid+(ink-mid)*.5));
            using var mask=Mask(patch,threshold);
            if(Components(mask).Any(b=>.16*tile.W<=b.W&&b.W<=.40*tile.W&&.035*tile.H<=b.H&&b.H<=.30*tile.H&&b.Area>=.002*tile.W*tile.H))return true;
        }
        return false;
    }

    private static double? Background(Mat gray,JsonObject read,Rect roi,Size canvas)
    {
        var b=Numbers(read["tileBounds"]!);int fw=canvas.Width,fh=canvas.Height;
        int l=Math.Max(0,(int)(b[0]*fw-roi.X)),r=Math.Min(gray.Cols,(int)Math.Ceiling((b[0]+b[2])*fw-roi.X));
        var ys=read["centerPoints"]!.AsArray().Select(p=>N(p![1])*fh-roi.Y).ToArray();
        int top=Math.Max(0,(int)(ys.Min()-b[3]*fh/2)),bottom=Math.Min(gray.Rows,(int)Math.Ceiling(ys.Max()+b[3]*fh/2));
        if(r<=l||bottom<=top)return null;
        using var band=new Mat(gray,new Rect(l,top,r-l,bottom-top));var src=Geometry.Bytes(band);var samples=new List<byte>(src.Length);
        for(int y=top;y<bottom;y++)if(y<b[1]*fh-roi.Y||y>=(b[1]+b[3])*fh-roi.Y)
            samples.AddRange(src.AsSpan((y-top)*(r-l),r-l).ToArray());
        return samples.Count==0?null:Quantile(samples.ToArray(),.5);
    }
}
