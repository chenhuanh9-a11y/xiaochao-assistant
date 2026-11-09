using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Display;

namespace StarMap.Vision;

/// <summary>OpenCV decoding and the production independent static-corner reconstruction.</summary>
public sealed class OpenCvDisplayRasterValidator : IDisplayRasterValidator
{
    public DisplayRasterInfo Inspect(ReadOnlyMemory<byte> bytes,ReadOnlyMemory<byte>? cleanMask = null,int maskWidth = 0,int maskHeight = 0)
    {
        try
        {
            DisplayImageDecodeBudget.RequireWithinLimit(bytes.Span);
            using var image = Cv2.ImDecode(bytes.ToArray(),ImreadModes.Unchanged);
            if (image.Empty() || image.Depth() != MatType.CV_8U) throw new InvalidDataException("display-image-decode-invalid");
            int channels = image.Channels();
            if (channels is not (1 or 3 or 4)) throw new InvalidDataException("display-image-channels-invalid");
            int width = image.Cols, height = image.Rows;
            var data = Geometry.Bytes(image);
            bool visible = false;
            bool? footprint = cleanMask is null ? null : true;
            if (cleanMask is not null && (width != maskWidth || height != maskHeight
                || cleanMask.Value.Length != checked(maskWidth*maskHeight))) footprint = false;
            var mask = cleanMask?.ToArray();
            for (int i = 0; i < height*width; i++)
            {
                bool alpha = channels == 4 ? data[4*i+3] > 0 : true;
                visible |= alpha;
                if (footprint == true && mask is not null && alpha != (mask[i] != 0)) footprint = false;
            }
            return new(width,height,channels,visible,footprint);
        }
        catch (OpenCVException e) { throw new InvalidDataException("display-image-decode-invalid",e); }
    }

    public NativeCornerValidation ValidateNativeCorners(NativeCornerValidationRequest request)
    {
        try { return NativeCorners(request); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException or InvalidDataException
            or ArgumentException or OverflowException or OpenCVException or InvalidOperationException)
        { return new(false,["native-corner-reconstruction-invalid:"+e.Message]); }
    }

    private sealed record Node(Point2d Point,Point2d[] Rays,bool Valid);
    private sealed record Pair(int Source,int Target,int CellX,int CellY,bool Support);
    private static NativeCornerValidation NativeCorners(NativeCornerValidationRequest r)
    {
        var reasons = new List<string>();
        if (r.Profile == r.CanonicalProfile || Text(r.Evidence,"type") != "native-static-corner-registration")
            return new(false,["native-context-or-type-unavailable"]);
        var binding = JsonSerializer.SerializeToElement(new {profile=r.Profile,mapId=r.MapId,floor=r.Floor,variant=r.Variant,
            nativeLayout=r.NativeLayout,nativeFloor=r.NativeFloor});
        if (!Equal(Get(r.Evidence,"binding"),binding)) return new(false,["native-corner-binding-mismatch"]);
        var sources = JsonSerializer.SerializeToElement(new {
            authorResource=new {kind=r.Profile == r.DefaultProfile ? "nightmare-pack-layer" : "author-route-catalog",
                profile=r.Profile,mapId=r.MapId,floor=r.Floor,variant=r.Variant},
            nativeResource=new {kind="author-structure-native-mask",profile=r.CanonicalProfile,mapId=r.MapId,
                floor=r.Floor,variant=r.Variant,nativeLayout=r.NativeLayout,nativeFloor=r.NativeFloor,resourcePath=r.NativeRelativePath},
            canonicalGeometryReference=new {profile=r.CanonicalProfile,mapId=r.MapId,floor=r.Floor,role="independent-fixed-coordinate-chart"}});
        if (!Equal(Get(r.Evidence,"sources"),sources)) return new(false,["native-corner-source-binding-mismatch"]);
        var sourceBytes = ReadImage(r.AuthorPath,32*1024*1024);
        var nativeBytes = ReadImage(r.NativeMaskPath,32*1024*1024);
        if (nativeBytes.LongLength != r.NativeBytes) return new(false,["native-corner-native-byte-count-mismatch"]);
        DisplayImageDecodeBudget.RequireWithinLimit(sourceBytes);
        DisplayImageDecodeBudget.RequireWithinLimit(nativeBytes);
        using var author = Cv2.ImDecode(sourceBytes,ImreadModes.Unchanged);
        using var native = Cv2.ImDecode(nativeBytes,ImreadModes.Unchanged);
        if (author.Empty() || author.Depth() != MatType.CV_8U || author.Channels() is not (3 or 4)
            || native.Empty() || native.Type() != MatType.CV_8UC1 || native.Cols != r.NativeWidth || native.Rows != r.NativeHeight)
            return new(false,["native-corner-source-image-invalid"]);
        var nativePixels = Geometry.Bytes(native);
        if (nativePixels.Any(v => v is not (0 or 255))) return new(false,["native-corner-native-mask-not-binary"]);
        using var source = AuthorMask(author,r.Profile);
        using var target = new Mat();
        Cv2.Threshold(native,target,0,1,ThresholdTypes.Binary);
        var sn = Nodes(source); var tn = Nodes(target);
        var si = Enumerable.Range(0,sn.Count).Where(i => sn[i].Valid).ToArray();
        var ti = Enumerable.Range(0,tn.Count).Where(i => tn[i].Valid).ToArray();
        if (si.Length < 2 || ti.Length < 2) return new(false,["native-corner-reconstruction-unavailable"]);
        Rect sb = Cv2.BoundingRect(source), tb = Cv2.BoundingRect(target);
        if (sb.Width <= 0 || sb.Height <= 0 || tb.Width <= 0 || tb.Height <= 0)
            return new(false,["native-corner-reconstruction-unavailable"]);
        double sx=(double)tb.Width/sb.Width, sy=(double)tb.Height/sb.Height;
        var seed = new DisplayAffine(sx,0,tb.X-sx*sb.X,0,sy,tb.Y-sy*sb.Y);
        var nearest = new int[si.Length]; var reverse = new int[ti.Length];
        var first = Enumerable.Repeat(double.PositiveInfinity,si.Length).ToArray();
        var second = Enumerable.Repeat(double.PositiveInfinity,si.Length).ToArray();
        var reverseDistance = Enumerable.Repeat(double.PositiveInfinity,ti.Length).ToArray();
        for (int i=0;i<si.Length;i++)
        {
            var a=sn[si[i]];
            var predicted=seed.Apply(a.Point.X,a.Point.Y);
            for (int j=0;j<ti.Length;j++)
            {
                var b=tn[ti[j]];
                double distance=double.PositiveInfinity;
                if (Dot(a.Rays[0],b.Rays[0]) >= .95 && Dot(a.Rays[1],b.Rays[1]) >= .95)
                    distance=Norm(predicted.X-b.Point.X,predicted.Y-b.Point.Y);
                if (distance < first[i]) {second[i]=first[i];first[i]=distance;nearest[i]=j;}
                else if (distance < second[i]) second[i]=distance;
                if (distance < reverseDistance[j]) {reverseDistance[j]=distance;reverse[j]=i;}
            }
        }
        var pairs=new List<Pair>();
        for (int i=0;i<si.Length;i++)
        {
            if (reverse[nearest[i]] != i || !double.IsFinite(first[i]) || first[i] > 16 || second[i]-first[i] < 8) continue;
            var point=sn[si[i]].Point;
            int cx=Math.Min(3,(int)Math.Floor((point.X-sb.X)/sb.Width*4));
            int cy=Math.Min(3,(int)Math.Floor((point.Y-sb.Y)/sb.Height*4));
            pairs.Add(new(si[i],ti[nearest[i]],cx,cy,(cx+cy)%2==0));
        }
        var declared = Get(r.Evidence,"pairs");
        if (declared is not { ValueKind:JsonValueKind.Array } rows || rows.GetArrayLength()!=pairs.Count)
            return new(false,["native-corner-pairs-not-reconstructed"]);
        var expected=pairs.ToDictionary(p => (p.Source,p.Target));
        var actualKeys=new HashSet<(int,int)>();
        foreach(var row in rows.EnumerateArray())
        {
            if (!Int(row,"sourceNode",out int sourceId) || !Int(row,"gameNode",out int targetId)
                || !actualKeys.Add((sourceId,targetId)) || !expected.TryGetValue((sourceId,targetId),out var p))
                return new(false,["native-corner-pair-node-binding-invalid"]);
            var a=sn[p.Source]; var b=tn[p.Target];
            if (!Equal(Get(row,"sourcePoint"),JsonSerializer.SerializeToElement(new[]{a.Point.X,a.Point.Y}))
                || !Equal(Get(row,"gamePoint"),JsonSerializer.SerializeToElement(new[]{b.Point.X,b.Point.Y}))
                || !Equal(Get(row,"sourceRays"),Rays(a.Rays)) || !Equal(Get(row,"gameRays"),Rays(b.Rays))
                || Text(row,"partition") != (p.Support ? "support" : "holdout")
                || !Equal(Get(row,"cell"),JsonSerializer.SerializeToElement(new[]{p.CellX,p.CellY})))
                return new(false,["native-corner-pair-node-binding-invalid"]);
        }
        var support=pairs.Where(p=>p.Support).ToArray();var holdout=pairs.Where(p=>!p.Support).ToArray();
        if(support.Length<20||holdout.Length<20) reasons.Add("native-corner-support-or-holdout-insufficient");
        if (support.Length==0 || Math.Min(support.Max(p=>sn[p.Source].Point.X)-support.Min(p=>sn[p.Source].Point.X),
            support.Max(p=>sn[p.Source].Point.Y)-support.Min(p=>sn[p.Source].Point.Y))<=200)
            reasons.Add("native-corner-support-spread-insufficient");
        CheckCoverage(support,"support_coverage");CheckCoverage(holdout,"holdout_coverage");
        CheckMatrix(r.CandidateProfileToCanonical);CheckMatrix(r.PrimaryProfileToCanonical);CheckMatrix(r.PrimaryProfileToNative);
        if (Get(r.Evidence,"matrixProfileToCanonical") is { } matrixEvidence
            && (!DisplayAffine.TryRead(matrixEvidence,out var candidateEvidence) || candidateEvidence != r.CandidateProfileToCanonical))
            reasons.Add("native-corner-evidence-matrix-mismatch");
        double anisotropy=Math.Max(r.CandidateProfileToCanonical.M00,r.CandidateProfileToCanonical.M11)
            /Math.Min(r.CandidateProfileToCanonical.M00,r.CandidateProfileToCanonical.M11);
        if(!double.IsFinite(anisotropy)||anisotropy>1.13) reasons.Add("native-corner-matrix-anisotropy-exceeded");
        var chain=r.PrimaryProfileToCanonical*r.PrimaryProfileToNative.Inverse();
        var inverse=r.CandidateProfileToCanonical.Inverse();
        double supportP90=Residual(support),holdoutP90=Residual(holdout);
        if(supportP90>20.5||holdoutP90>20.5) reasons.Add("native-corner-reference-author-residual-exceeded");
        return new(reasons.Count==0,reasons,support.Length,holdout.Length,supportP90,holdoutP90);

        void CheckCoverage(Pair[] part,string name)
        {
            var quadrants=new HashSet<(int,int)>();var cells=new HashSet<(int,int)>();
            foreach(var p in part)
            {
                var point=sn[p.Source].Point;
                quadrants.Add((Math.Min(1,(int)Math.Floor(point.X/source.Cols*2)),Math.Min(1,(int)Math.Floor(point.Y/source.Rows*2))));
                cells.Add((Math.Min(3,(int)Math.Floor(point.X/source.Cols*4)),Math.Min(3,(int)Math.Floor(point.Y/source.Rows*4))));
            }
            if(quadrants.Count<3||cells.Count<6) reasons.Add("native-corner-"+name+"-insufficient");
        }
        double Residual(Pair[] part)
        {
            if(part.Length==0) throw new InvalidDataException("residual-stat-empty-or-shape");
            var authorResidual=new List<double>();var canonicalResidual=new List<double>();
            foreach(var p in part)
            {
                var a=sn[p.Source].Point;var b=tn[p.Target].Point;
                var canonical=chain.Apply(b.X,b.Y);var predicted=r.CandidateProfileToCanonical.Apply(a.X,a.Y);
                var restored=inverse.Apply(canonical.X,canonical.Y);
                authorResidual.Add(Norm(restored.X-a.X,restored.Y-a.Y));
                canonicalResidual.Add(Norm(predicted.X-canonical.X,predicted.Y-canonical.Y));
            }
            CheckStats(canonicalResidual);CheckStats(authorResidual);
            authorResidual.Sort();double index=(authorResidual.Count-1)*.9;int lo=(int)Math.Floor(index);int hi=(int)Math.Ceiling(index);
            return authorResidual[lo]+(authorResidual[hi]-authorResidual[lo])*(index-lo);
        }
    }

    private static Mat AuthorMask(Mat author,string profile)
    {
        using var bgr=new Mat();
        if(author.Channels()==4) Cv2.CvtColor(author,bgr,ColorConversionCodes.BGRA2BGR);else author.CopyTo(bgr);
        using var hsv=new Mat();Cv2.CvtColor(bgr,hsv,ColorConversionCodes.BGR2HSV);
        byte[] pixels=Geometry.Bytes(author),colors=Geometry.Bytes(hsv);
        var mask=new byte[author.Rows*author.Cols];
        bool zhan=profile is "zhan10-speed" or "zhan10-coffin";
        for(int i=0;i<mask.Length;i++)
        {
            int h=colors[3*i],s=colors[3*i+1],v=colors[3*i+2];
            if(zhan&&author.Channels()==4)
                mask[i]=(byte)(pixels[4*i+3]>=128 && s<140 && (pixels[4*i]!=0||pixels[4*i+1]!=0||pixels[4*i+2]!=0)?1:0);
            else mask[i]=(byte)(((s<=72||h>=4&&h<=32&&s>=22&&s<=175)&&v>=68&&v<=205)?1:0);
        }
        var result=Geometry.FromBytes(author.Cols,author.Rows,mask);
        if(zhan&&author.Channels()==4) return result;
        using(var kernel=Geometry.Kernel(3)) Cv2.MorphologyEx(result,result,MorphTypes.Open,kernel);
        mask=Geometry.Bytes(result);var annotations=new byte[mask.Length];
        for(int i=0;i<mask.Length;i++)
        {
            int h=colors[3*i],s=colors[3*i+1],v=colors[3*i+2];
            if(author.Channels()==4&&pixels[4*i+3]<8) mask[i]=0;
            bool annotation=s>=82&&v>=105;
            if(profile=="wang-qingxin"&&annotation&&h>=4&&h<=32) {mask[i]=1;annotation=false;}
            annotations[i]=(byte)(annotation?1:0);
        }
        using var ann=Geometry.FromBytes(author.Cols,author.Rows,annotations);
        using(var kernel=Geometry.Kernel(5)) Cv2.Dilate(ann,ann,kernel);
        annotations=Geometry.Bytes(ann);
        for(int i=0;i<mask.Length;i++)if(annotations[i]!=0)mask[i]=0;
        result.Dispose();return Geometry.FromBytes(author.Cols,author.Rows,mask);
    }
    private static List<Node> Nodes(Mat mask)
    {
        Cv2.FindContours(mask,out Point[][] contours,out _,RetrievalModes.List,ContourApproximationModes.ApproxSimple);
        var nodes=new List<Node>();
        foreach(var contour in contours)
        {
            var polygon=StructureContourApproximation.ApproximateClosed(contour,2);
            if(polygon.Length<4||Math.Abs(Cv2.ContourArea(polygon))<100)continue;
            for(int i=0;i<polygon.Length;i++)
            {
                var p=polygon[i];var prev=polygon[(i+polygon.Length-1)%polygon.Length];var next=polygon[(i+1)%polygon.Length];
                var a=new Point2d(prev.X-p.X,prev.Y-p.Y);var b=new Point2d(next.X-p.X,next.Y-p.Y);
                double la=Norm(a.X,a.Y),lb=Norm(b.X,b.Y);
                var ra=new Point2d(a.X/Math.Max(la,1e-9),a.Y/Math.Max(la,1e-9));var rb=new Point2d(b.X/Math.Max(lb,1e-9),b.Y/Math.Max(lb,1e-9));
                nodes.Add(new(new Point2d(p.X,p.Y),[ra,rb],Math.Min(la,lb)>=8&&Math.Abs(ra.X*rb.Y-ra.Y*rb.X)>.45));
            }
        }
        return nodes;
    }
    private static void CheckMatrix(DisplayAffine m)
    {
        if(!m.IsInvertible||Math.Abs(m.M01)>1e-8||Math.Abs(m.M10)>1e-8||m.M00<=0||m.M11<=0||m.Determinant<=1e-9)
            throw new InvalidDataException("native-corner-matrix-invalid");
    }
    private static void CheckStats(List<double> values)
    {
        if(values.Count==0||values.Any(x=>!double.IsFinite(x))||!double.IsFinite(Math.Sqrt(values.Sum(x=>x*x)/values.Count)))
            throw new InvalidDataException("native-corner-residual-stat-invalid");
    }
    private static byte[] ReadImage(string path,int max)
    {
        var info=new FileInfo(path);
        if(!info.Exists||info.Length<=0||info.Length>max)throw new InvalidDataException("native-corner-image-unavailable");
        var bytes=File.ReadAllBytes(path);
        if(bytes.LongLength!=info.Length)throw new InvalidDataException("native-corner-image-changed");
        return bytes;
    }
    private static double Dot(Point2d a,Point2d b)=>a.X*b.X+a.Y*b.Y;
    private static double Norm(double x,double y)=>Math.Sqrt(x*x+y*y);
    private static JsonElement Rays(Point2d[] rays)=>JsonSerializer.SerializeToElement(rays.Select(p=>new[]{p.X,p.Y}).ToArray());
    private static JsonElement? Get(JsonElement? e,string key)=>e is {ValueKind:JsonValueKind.Object} v&&v.TryGetProperty(key,out var p)?p:null;
    private static string? Text(JsonElement? e,string key)=>Get(e,key) is {ValueKind:JsonValueKind.String} p?p.GetString():null;
    private static bool Equal(JsonElement? a,JsonElement b)=>a is { } av&&JsonElement.DeepEquals(av,b);
    private static bool Int(JsonElement e,string key,out int value){value=0;return Get(e,key) is {ValueKind:JsonValueKind.Number} p&&p.TryGetInt32(out value);}
}
