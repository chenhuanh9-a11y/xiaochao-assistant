using System.Diagnostics;
using System.Text.Json.Nodes;
using OpenCvSharp;

namespace StarMap.Vision.Steady;

public static partial class Idvb0059Steady
{
    sealed record Seed(double X, double Y, int Hits);
    sealed record GridPeak(int X, int Y, int Hits);
    sealed class Candidate(double scale, Point2d offset, double score, int probes, int rawScore)
    {
        public double Scale = scale, WeightedScore = score, GlobalScore;
        public Point2d Offset = offset;
        public int Probes = probes, RawScore = rawScore, TotalValidPoints, HitPoints, ValidPartitions, PassedPartitions;
        public int[] PartitionPoints = new int[4], PartitionHits = new int[4];
        public bool SpatiallyConsistent;
        public JsonObject Json() => new() { ["scale"] = Scale, ["offset"] = Vec(Offset), ["weightedScore"] = WeightedScore,
            ["probes"] = Probes, ["rawScore"] = RawScore, ["globalScore"] = GlobalScore, ["totalValidPoints"] = TotalValidPoints,
            ["hitPoints"] = HitPoints, ["validPartitions"] = ValidPartitions, ["passedPartitions"] = PassedPartitions,
            ["partitionPoints"] = Ints(PartitionPoints), ["partitionHits"] = Ints(PartitionHits), ["spatiallyConsistent"] = SpatiallyConsistent };
    }
    sealed class BootstrapResult(bool accepted, string reason, JsonObject diagnostics)
    {
        public bool Accepted = accepted;
        public string Reason = reason;
        public JsonObject Diagnostics = diagnostics;
        public int TestedScaleHypotheses = 1;
        public double? Scale, Confidence;
        public double[][]? Matrix;
    }
    static double Now() => Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency;
    static bool Cancelled(Func<bool>? cancelled) => cancelled?.Invoke() == true;
    static Point2d P(Point p) => new(p.X,p.Y);
    static Point2d Add(Point2d a,Point2d b) => new(a.X+b.X,a.Y+b.Y);
    static Point2d Sub(Point2d a,Point2d b) => new(a.X-b.X,a.Y-b.Y);
    static Point2d Mul(Point2d a,double s) => new(a.X*s,a.Y*s);
    static double Distance(Point2d a,Point2d b) => Numerics.Norm(Sub(a,b));
    static JsonArray Vec(Point2d p) => new(p.X,p.Y);
    static JsonArray Ints(IEnumerable<int> values) => new(values.Select(v => (JsonNode?)JsonValue.Create(v)).ToArray());
    static JsonArray Doubles(IEnumerable<double> values) => new(values.Select(v => (JsonNode?)JsonValue.Create(v)).ToArray());
    static JsonArray MatrixJson(double[][] values) => new(values.Select(v => (JsonNode)Doubles(v)).ToArray());
    static bool Lookup(byte[] mask,int width,int height,int x,int y) => x >= 0 && y >= 0 && x < width && y < height && mask[y*width+x] != 0;
    static int FloorDiv(int value,int divisor) => (int)Math.Floor((double)value/divisor);

    static (List<Seed> Seeds,JsonObject Diagnostics) CoarseGrid(Point[] points,Prepared prepared,double scale,Func<bool>? cancelled,Point origin)
    {
        var scaled = points.Select(p => new Point(Numerics.Rint(p.X/scale),Numerics.Rint(p.Y/scale))).ToArray();
        int n = scaled.Length, misses = Math.Min(n-1,Math.Max(0,n-(int)Math.Ceiling(MinScore*n)));
        var sortedX = scaled.Select(p => p.X).Order().ToArray(); var sortedY = scaled.Select(p => p.Y).Order().ToArray();
        int loX = -sortedX[misses]-2, loY = -sortedY[misses]-2;
        int hiX = prepared.Width+1-sortedX[n-1-misses], hiY = prepared.Height+1-sortedY[n-1-misses];
        if (hiX < loX || hiY < loY) return ([],new() { ["gridCells"] = 0 });
        int nx = (hiX-loX)/CoarseStride+1, ny = (hiY-loY)/CoarseStride+1;
        var scores = new int[checked(nx*ny)];
        for (int index = 0; index < scaled.Length; index++)
        {
            if (index%16 == 0 && Cancelled(cancelled)) return ([],new() { ["cancelled"] = true,["gridCells"] = scores.Length });
            var p = scaled[index];
            int x0 = Math.Max(0,FloorDiv(-p.X-loX+CoarseStride-1,CoarseStride));
            int y0 = Math.Max(0,FloorDiv(-p.Y-loY+CoarseStride-1,CoarseStride));
            int x1 = Math.Min(nx,FloorDiv(prepared.Width-1-p.X-loX,CoarseStride)+1);
            int y1 = Math.Min(ny,FloorDiv(prepared.Height-1-p.Y-loY,CoarseStride)+1);
            for (int y = y0; y < y1; y++)
            {
                int fieldOffset = (loY+y*CoarseStride+p.Y)*prepared.Width+loX+p.X;
                int scoreOffset = y*nx;
                for (int x = x0; x < x1; x++) scores[scoreOffset+x] += prepared.K3[fieldOffset+x*CoarseStride];
            }
        }
        int count = Math.Min(32,scores.Length);
        int threshold = Math.Max(5,scores.Order().ElementAt(scores.Length-count));
        var ids = Enumerable.Range(0,scores.Length).Where(i => scores[i] >= threshold).OrderByDescending(i => scores[i]).ThenBy(i => i).Take(32).ToArray();
        if (ids.Length == 0) return ([],new() { ["gridCells"] = scores.Length });
        GridPeak Polish(int dx,int dy,int score)
        {
            var winner = new GridPeak(dx,dy,score); int best = -1;
            for (int y = Math.Max(loY,dy-2); y <= Math.Min(hiY,dy+2); y++)
                for (int x = Math.Max(loX,dx-2); x <= Math.Min(hiX,dx+2); x++)
                {
                    int votes = 0; foreach (var p in scaled) if (Lookup(prepared.K3,prepared.Width,prepared.Height,p.X+x,p.Y+y)) votes++;
                    if (votes > best) { best = votes; if (votes > score) winner = new(x,y,votes); }
                }
            return winner;
        }
        var polished = ids.Select(i => Polish(loX+(i%nx)*CoarseStride,loY+(i/nx)*CoarseStride,scores[i])).OrderByDescending(p => p.Hits).ToArray();
        var peaks = new List<GridPeak>();
        foreach (var row in polished) if (peaks.All(p => Math.Abs(row.X-p.X) > 2 || Math.Abs(row.Y-p.Y) > 2)) peaks.Add(row);
        var best = peaks[0]; double distinct2 = Math.Pow(MinDistinct/scale,2);
        double D2(GridPeak a,GridPeak b) => Math.Pow(a.X-b.X,2)+Math.Pow(a.Y-b.Y,2);
        var runners = peaks.Skip(1).Where(p => D2(p,best) >= distinct2).Take(2).ToList();
        if (runners.Count == 0)
        {
            int winner = 0, hits = 0;
            for (int i = 0; i < scores.Length; i++)
            {
                int x = loX+(i%nx)*CoarseStride, y = loY+(i/nx)*CoarseStride;
                int value = Math.Pow(x-best.X,2)+Math.Pow(y-best.Y,2) >= distinct2 ? scores[i] : 0;
                if (value > hits) { winner = i; hits = value; }
            }
            if (hits >= 3)
            {
                var row = Polish(loX+(winner%nx)*CoarseStride,loY+(winner/nx)*CoarseStride,hits);
                if (D2(row,best) >= distinct2) runners.Add(row);
            }
        }
        var seeds = new List<GridPeak> { best }; seeds.AddRange(runners);
        int initial = seeds.Count; seeds.AddRange(peaks.Skip(1).Where(p => !runners.Contains(p)));
        return (seeds.Select(p => new Seed(origin.X-p.X*scale,origin.Y-p.Y*scale,p.Hits)).ToList(),new() {
            ["gridCells"] = scores.Length,["coarsePeakScore"] = best.Hits,["initialCandidates"] = initial,["retainedCandidates"] = peaks.Count });
    }

    static double Weighted(Point2d[] points,Prepared prepared,double scale,Point2d translation)
    {
        int n3=0,n5=0;
        foreach (var p in points)
        {
            int x=Numerics.Rint((p.X-translation.X)/scale), y=Numerics.Rint((p.Y-translation.Y)/scale);
            if (Lookup(prepared.K3,prepared.Width,prepared.Height,x,y)) n3++;
            if (Lookup(prepared.K5,prepared.Width,prepared.Height,x,y)) n5++;
        }
        return (n5+2*n3)/(3.0*points.Length);
    }

    static Candidate RefineLocked(Point2d[] points,Prepared prepared,double scale,Seed seed)
    {
        Point2d initial = new(seed.X,seed.Y), best = initial;
        double score = Weighted(points,prepared,scale,best);
        for (int x=-6;x<=6;x+=2) for (int y=-6;y<=6;y+=2)
        {
            if (x==0 && y==0) continue;
            var offset=Add(initial,new(x,y)); double value=Weighted(points,prepared,scale,offset);
            if (value > score) { score=value;best=offset; }
        }
        initial=best; score=Weighted(points,prepared,scale,best);
        foreach (double x in new[] {-1.5,0,1.5}) foreach (double y in new[] {-1.5,0,1.5})
        {
            if (x==0 && y==0) continue;
            var offset=Add(initial,new(x,y)); double value=Weighted(points,prepared,scale,offset);
            if (value > score) { score=value;best=offset; }
        }
        return new(scale,best,score,56,seed.Hits);
    }

    static Candidate RefineUnlocked(Point2d[] points,Prepared prepared,double seedScale,Seed seed,CurrentObservation observation)
    {
        var center=new Point2d(observation.Origin.X+observation.Width/2.0,observation.Origin.Y+observation.Height/2.0);
        var anchor=Mul(Sub(center,new(seed.X,seed.Y)),1/seedScale);
        double bestScale=seedScale,bestScore=Weighted(points,prepared,seedScale,new(seed.X,seed.Y));
        Point2d bestOffset=new(seed.X,seed.Y); int probes=0;
        void Scan(IEnumerable<(double Scale,Point2d Offset)> source)
        {
            var rows=source.ToArray(); probes+=rows.Length;
            double score=double.NegativeInfinity; (double Scale,Point2d Offset) winner=default;
            foreach(var row in rows) { double value=Weighted(points,prepared,row.Scale,row.Offset); if(value>score){score=value;winner=row;} }
            if(score>bestScore){bestScore=score;bestScale=winner.Scale;bestOffset=winner.Offset;}
        }
        Scan(from ds in new[] {-.020,-.015,0,.015,.020} let s=seedScale+ds where s>=.35 && s<=2.50
             from dx in Enumerable.Range(0,7).Select(i => -6+2*i) from dy in Enumerable.Range(0,7).Select(i => -6+2*i)
             select (s,Add(Sub(center,Mul(anchor,s)),new(dx,dy))));
        anchor=Mul(Sub(center,bestOffset),1/bestScale);
        Scan(from ds in new[] {-.020,-.010,-.005,.005,.010,.020} let s=bestScale+ds where s>=.35 && s<=2.50
             select (s,Sub(center,Mul(anchor,s))));
        anchor=Mul(Sub(center,bestOffset),1/bestScale);
        Scan(from ds in new[] {-.005,0,.005} from dx in new[] {-1.5,0,1.5} from dy in new[] {-1.5,0,1.5}
             where ds!=0 || dx!=0 || dy!=0 let s=bestScale+ds select (s,Add(Sub(center,Mul(anchor,s)),new(dx,dy))));
        return new(bestScale,bestOffset,bestScore,probes,seed.Hits);
    }

    static Candidate Spatial(CurrentObservation observation,Prepared prepared,Candidate candidate)
    {
        int n=0,hits=0; var total=new int[4];var counts=new int[4];
        foreach(var p in observation.Points)
        {
            if(observation.Valid[p.Y*observation.Width+p.X]<128)continue;
            int part=(p.X>=observation.Width/2?1:0)+2*(p.Y>=observation.Height/2?1:0);total[part]++;n++;
            int x=Numerics.Rint((p.X+observation.Origin.X-candidate.Offset.X)/candidate.Scale);
            int y=Numerics.Rint((p.Y+observation.Origin.Y-candidate.Offset.Y)/candidate.Scale);
            if(Lookup(prepared.K5,prepared.Width,prepared.Height,x,y)){counts[part]++;hits++;}
        }
        var ratios=Enumerable.Range(0,4).Select(i => (double)counts[i]/Math.Max(1,total[i])).ToArray();
        int valid=total.Count(t=>t>=5),passed=Enumerable.Range(0,4).Count(i=>total[i]>=5&&ratios[i]>=.50);
        double global=(double)hits/Math.Max(1,n);int largest=Array.IndexOf(total,total.Max());
        bool dominant=total[largest]>=n*.60&&ratios[largest]>=.50&&global>=.50&&hits>=20;
        candidate.GlobalScore=global;candidate.TotalValidPoints=n;candidate.HitPoints=hits;candidate.ValidPartitions=valid;candidate.PassedPartitions=passed;
        candidate.PartitionPoints=total;candidate.PartitionHits=counts;
        candidate.SpatiallyConsistent=passed>=2||(valid>0&&passed==valid&&global>=.50)||dominant||(global>=.55&&hits>=25&&passed>=1);
        return candidate;
    }

    static (double Pitch,double Ratio) DominantPitch(double[] signal,double minimum=12,double maximum=double.PositiveInfinity,bool interpolate=true)
    {
        int n=signal.Length;if(n<40)return(0,0);
        double mean=signal.Average();var centered=signal.Select(v=>v-mean).ToArray();double variance=centered.Sum(v=>v*v);
        if(variance<1e-9)return(0,0);
        var raw=new double[n/2-12];
        for(int lag=12;lag<n/2;lag++){double sum=0;for(int i=0;i<n-lag;i++)sum+=centered[i]*centered[i+lag];raw[lag-12]=sum/variance;}
        var admitted=Enumerable.Range(0,raw.Length).Where(i=>i+12>=minimum&&i+12<=maximum).ToArray();if(admitted.Length==0)return(0,0);
        int index=admitted[0];foreach(int i in admitted)if(raw[i]>raw[index])index=i;
        double peak=raw[index];int bestLag=index+12;if(peak<=.05)return(0,0);
        int start=Math.Max(13,(int)Math.Ceiling(minimum)),end=double.IsFinite(maximum)?Math.Min(n/2-1,(int)Math.Floor(maximum)):n/2-1;
        for(int lag=start;lag<Math.Min(bestLag-1,end);lag++)
        {
            int i=lag-12;if(i<=0||i>=raw.Length-1)continue;double ratio=(double)bestLag/lag;double rounded=Math.Round(ratio,MidpointRounding.ToEven);
            if(raw[i]>raw[i-1]&&raw[i]>raw[i+1]&&raw[i]>=.80*peak&&rounded>=2&&Math.Abs(ratio-rounded)<.15)
            {bestLag=lag;index=i;peak=raw[i];break;}
        }
        double median=raw.Select(Math.Abs).Order().ElementAt(raw.Length/2),pitch=bestLag;
        if(interpolate&&index>0&&index<raw.Length-1)
        {
            double a=raw[index-1],b=raw[index],c=raw[index+1],denominator=2*(2*b-a-c);
            if(b>a&&b>c&&denominator>1e-9){double delta=(c-a)/denominator;if(Math.Abs(delta)<=.5)pitch+=delta;}
        }
        return(pitch,peak/Math.Max(.01,median));
    }

    static BootstrapResult Bootstrap(CurrentObservation observation,Prepared prepared,double scale,Func<bool>? cancelled,bool lockScale=true)
    {
        double started=Now();var timing=new JsonObject();
        var diagnostics=new JsonObject { ["algorithm"]="idvb0059-fast-bootstrap",["scaleLocked"]=lockScale,["maxSparsePoints"]=MaxSparsePoints,
            ["minimumApertureMargin"]=MinMargin,["minimumDistinctDistance"]=MinDistinct,["timingMs"]=timing };
        BootstrapResult Finish(string reason,Candidate? best=null,Candidate? runner=null)
        {
            timing["total"]=Now()-started;if(best is not null)diagnostics["best"]=best.Json();if(runner is not null)diagnostics["runnerUp"]=runner.Json();
            var result=new BootstrapResult(reason.Length==0,reason,diagnostics){Scale=best?.Scale??scale};
            if(result.Accepted){result.Matrix=[[best!.Scale,0,best.Offset.X],[0,best.Scale,best.Offset.Y]];result.Confidence=best.WeightedScore;}
            return result;
        }
        if(Cancelled(cancelled))return Finish("Cancelled");
        diagnostics["scale"]=scale;diagnostics["referenceBinding"]=prepared.Binding.DeepClone();diagnostics["referenceRepresentation"]=Representation;
        diagnostics["sparsePointCount"]=observation.Points.Length;diagnostics["observedEdgePixels"]=observation.Observed.Count(p=>p!=0);
        diagnostics["proposalEdgePixels"]=observation.Proposal.Count(p=>p!=0);diagnostics["observationBounds"]=new JsonArray(observation.Origin.X,observation.Origin.Y,observation.Width,observation.Height);
        if(Cancelled(cancelled))return Finish("Cancelled");if(observation.Points.Length==0)return Finish("NoCurrentFrameStructuralPoints");
        double mark=Now();var (seeds,grid)=CoarseGrid(observation.Points,prepared,scale,cancelled,observation.Origin);
        foreach(var row in grid)diagnostics[row.Key]=row.Value?.DeepClone();timing["translation"]=Now()-mark;
        if(Cancelled(cancelled))return Finish("Cancelled");if(seeds.Count==0)return Finish("TranslationNoCandidatesFound");
        mark=Now();int initial=grid["initialCandidates"]!.GetValue<int>();
        var points=observation.Points.Select(p=>new Point2d(p.X+observation.Origin.X,p.Y+observation.Origin.Y)).ToArray();
        List<Candidate> Refine(IEnumerable<Seed> rows)=>rows.Select(seed=>Spatial(observation,prepared,lockScale?RefineLocked(points,prepared,scale,seed):RefineUnlocked(points,prepared,scale,seed,observation))).ToList();
        var candidates=Refine(seeds.Take(initial));var best=candidates[0];
        var rivals=candidates.Skip(1).Where(c=>Distance(c.Offset,best.Offset)>=MinDistinct).ToList();
        if(rivals.Count==0&&best.SpatiallyConsistent&&Math.Max(best.WeightedScore,best.GlobalScore)>=MinScore*.85)
        {
            var retained=Refine(seeds.Skip(initial));foreach(var c in retained){candidates.Add(c);if(Distance(c.Offset,best.Offset)>=MinDistinct)rivals.Add(c);}
        }
        var runner=rivals.OrderByDescending(c=>c.GlobalScore).FirstOrDefault();
        if(lockScale&&runner is not null&&runner.SpatiallyConsistent&&runner.GlobalScore>best.GlobalScore)(best,runner)=(runner,best);
        timing["refineAndVerify"]=Now()-mark;diagnostics["refinedCandidates"]=candidates.Count;
        if(Cancelled(cancelled))return Finish("Cancelled");
        if(best.Scale<MinScale||best.Scale>MaxScale)return Finish("RefinedScaleOutOfRange",best,runner);
        if(runner is null)return Finish("NoDistinctRefinedRunnerUp",best);
        double margin=best.GlobalScore-runner.GlobalScore,required=MinScore-Math.Clamp((margin-MinMargin)/.14,0,1)*.09;
        diagnostics["apertureMargin"]=margin;diagnostics["requiredVerificationScore"]=required;diagnostics["distinctRunnerUpDistance"]=Distance(best.Offset,runner.Offset);
        if(margin<MinMargin)return Finish("ApertureMarginBelowThreshold",best,runner);
        if(best.WeightedScore<required)return Finish("VerificationScoreBelowThreshold",best,runner);
        if(!best.SpatiallyConsistent)return Finish("SpatialPartitionsBelowThreshold",best,runner);
        double loX=(observation.Origin.X-best.Offset.X)/best.Scale,loY=(observation.Origin.Y-best.Offset.Y)/best.Scale;
        double hiX=(observation.Origin.X+observation.Width-best.Offset.X)/best.Scale,hiY=(observation.Origin.Y+observation.Height-best.Offset.Y)/best.Scale;
        double overlapX=Math.Min(hiX,prepared.Width)-Math.Max(loX,0),overlapY=Math.Min(hiY,prepared.Height)-Math.Max(loY,0);
        diagnostics["overlapSourcePixels"]=new JsonArray(overlapX,overlapY);
        if(overlapX<50||overlapY<50)return Finish("TransformedBoundsOutOfBounds",best,runner);
        return Finish("",best,runner);
    }

    static BootstrapResult FreshScaleSolve(CurrentObservation observation,Prepared prepared,Func<bool>? cancelled)
    {
        double started=Now();var info=new JsonObject { ["referencePitch"]=prepared.Pitch,["referencePeakRatio"]=prepared.PitchRatio };
        string reason="";double pitch=0;
        if(prepared.Pitch<=5||prepared.PitchRatio<2)reason="PreparedFloorReferenceIneligible";
        else if(observation.Observed.Count(p=>p!=0)<300)reason="ObservationEdgePixelsBelowThreshold";
        else
        {
            var signal=new double[observation.Width];foreach(var p in Nonzero(observation.Proposal,observation.Width))signal[p.X]++;
            var estimate=DominantPitch(signal,prepared.Pitch*MinScale,prepared.Pitch*MaxScale);pitch=estimate.Pitch;double ratio=Math.Min(estimate.Ratio,prepared.PitchRatio);
            info["livePitch"]=pitch;info["peakRatio"]=ratio;
            if(ratio<2||pitch<=5)reason="ScalePeakRatioBelowThreshold";
            else if(pitch/prepared.Pitch<MinScale||pitch/prepared.Pitch>MaxScale)reason="EstimatedScaleOutOfRange";
        }
        info["milliseconds"]=Now()-started;
        if(reason.Length>0)return new(false,reason,new JsonObject { ["independentScale"]=info,["timingMs"]=new JsonObject { ["total"]=info["milliseconds"]!.DeepClone() } });
        double seed=pitch/prepared.Pitch;var result=Bootstrap(observation,prepared,seed,cancelled,false);info["seed"]=seed;result.Diagnostics["independentScale"]=info;
        if(result.Diagnostics["best"] is JsonObject best&&Math.Abs(best["scale"]!.GetValue<double>()-seed)>=.044&&!Cancelled(cancelled))
        {
            double bestScale=best["scale"]!.GetValue<double>(),next=bestScale+Math.CopySign(.03,bestScale-seed);
            if(next>=MinScale&&next<=MaxScale)
            {
                var expanded=Bootstrap(observation,prepared,next,cancelled,true);result.TestedScaleHypotheses+=expanded.TestedScaleHypotheses;
                result.Diagnostics["expandedScale"]=new JsonObject { ["scale"]=next,["accepted"]=expanded.Accepted,["reason"]=expanded.Reason };
                if(expanded.Accepted&&(!result.Accepted||expanded.Confidence>result.Confidence+.015))
                {expanded.Diagnostics["independentScale"]=info.DeepClone();expanded.Diagnostics["expandedFromScale"]=seed;expanded.TestedScaleHypotheses=result.TestedScaleHypotheses;result=expanded;}
            }
        }
        return result;
    }
}
