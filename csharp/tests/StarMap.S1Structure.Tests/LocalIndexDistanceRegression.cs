using System.Text.Json;
using OpenCvSharp;
using StarMap.Vision;
using static StarMap.Vision.StructureIndexFilter;

internal static class LocalIndexDistanceRegression
{
    internal static int Run(string output)
    {
        if(Directory.Exists(output))throw new IOException("Preserve prior evidence; use a new output.");
        Directory.CreateDirectory(output);var checks=new List<object>();var failures=new List<string>();
        void Check(bool passed,string name){checks.Add(new{passed,name});if(!passed)throw new InvalidOperationException(name);}
        try
        {
            const int w=18,h=14,pad=96,canvasSize=256;
            using var first=new Mat(h,w,MatType.CV_8UC1,Scalar.All(0));
            Cv2.Rectangle(first,new Rect(0,0,12,10),Scalar.All(1),-1);
            Cv2.Rectangle(first,new Rect(4,3,4,4),Scalar.All(0),-1);
            using var second=new Mat(h,w,MatType.CV_8UC1,Scalar.All(0));
            Cv2.Rectangle(second,new Rect(7,4,11,10),Scalar.All(1),-1);
            using var third=new Mat(h,w,MatType.CV_8UC1,Scalar.All(1));
            var masks=new[]{first,second,third};
            byte[] Bytes(Mat mat){var bytes=new byte[checked((int)(mat.Total()*mat.ElemSize()))];System.Runtime.InteropServices.Marshal.Copy(mat.Data,bytes,0,bytes.Length);return bytes;}
            float[] Floats(Mat mat){var values=new float[checked((int)mat.Total())];System.Runtime.InteropServices.Marshal.Copy(mat.Data,values,0,values.Length);return values;}
            var sources=Enumerable.Range(0,9).Select(i=>new LocalSource("map"+i,null,w,h,[new(1,3),new(9,6)])).ToArray();
            var fields=masks.Select(mask=>PrepareLocalField(w,h,Bytes(mask))).ToArray();
            var origin=new Point(-9,-6);
            var points=(from y in new[]{-15f,-.5f,.5f,3.5f,8.5f,18.5f,30f}
                from x in new[]{-15f,-1.5f,-.5f,.5f,1.5f,8.5f,18.5f,30f}select new[]{x,y}).ToArray();
            // Independent dense oracle: place each original solid mask, take
            // its own gradient, then OR EDGES. Never gradient a union of solids.
            var dense=masks.Select(mask=>
            {
                using var union=new Mat(canvasSize,canvasSize,MatType.CV_8UC1,Scalar.All(0));
                foreach(var door in sources[0].Doors)
                {
                    using var canvas=new Mat(canvasSize,canvasSize,MatType.CV_8UC1,Scalar.All(0));
                    using(var region=new Mat(canvas,new Rect(-door.X-origin.X+pad,-door.Y-origin.Y+pad,w,h)))mask.CopyTo(region);
                    using var edge=new Mat();using var kernel=Cv2.GetStructuringElement(MorphShapes.Rect,new(3,3));
                    Cv2.MorphologyEx(canvas,edge,MorphTypes.Gradient,kernel);Cv2.BitwiseOr(union,edge,union);
                }
                using var inverse=new Mat();Cv2.Threshold(union,inverse,0,1,ThresholdTypes.BinaryInv);
                using var distance=new Mat();Cv2.DistanceTransform(inverse,distance,DistanceTypes.L2,DistanceTransformMasks.Mask3);
                return Floats(distance);
            }).ToArray();
            foreach(double radius in new[]{0d,.75,1.75,16})
            {
                var hypotheses=new[]{new Hypothesis(.5,-1.25,2.5),new(.75,3.5,-2.25),new(1.25,-2.5,1.75),new(2,.5,-.5)};
                var actual=CandidateLocalMinima(points,hypotheses,origin,sources,source=>fields[int.Parse(source.Id[3..])%3],()=>false,radius)!;
                var exhaustive=CandidateLocalMinima(points,hypotheses,origin,sources,source=>fields[int.Parse(source.Id[3..])%3],()=>false,radius,stopExplained:false)!;
                Check(actual.Minima.SelectMany(v=>v).Select(v=>v>8).SequenceEqual(exhaustive.Minima.SelectMany(v=>v).Select(v=>v>8)),
                    "short-circuit keeps exactly the exhaustive unavoidable-miss predicate at radius "+radius);
                Check(actual.FieldReads<exhaustive.FieldReads,"proved explained points reduce field reads at radius "+radius);
                double greatestExcess=0;int compared=0;
                for(int map=0;map<sources.Length;map++)
                for(int p=0;p<points.Length;p++)
                {
                    double expected=double.PositiveInfinity;
                    foreach(var item in hypotheses)
                    {
                        int lx=(int)Math.Round((points[p][0]-(item.X+radius))/item.Scale,MidpointRounding.ToEven);
                        int hx=(int)Math.Round((points[p][0]-(item.X-radius))/item.Scale,MidpointRounding.ToEven);
                        int ly=(int)Math.Round((points[p][1]-(item.Y+radius))/item.Scale,MidpointRounding.ToEven);
                        int hy=(int)Math.Round((points[p][1]-(item.Y-radius))/item.Scale,MidpointRounding.ToEven);
                        for(int y=ly;y<=hy;y++)for(int x=lx;x<=hx;x++)
                        {
                            if(x+pad<0||y+pad<0||x+pad>=canvasSize||y+pad>=canvasSize)throw new InvalidOperationException("Oracle canvas too small.");
                            expected=Math.Min(expected,dense[map%3][(y+pad)*canvasSize+x+pad]*item.Scale);
                        }
                    }
                    greatestExcess=Math.Max(greatestExcess,actual.Minima[map][p]-expected);compared++;
                }
                Check(greatestExcess<=.00002,$"native field/all doors/ToEven/box radius {radius}: lower bound never exceeds dense enumerated min ({compared} points)");
                Check(actual.UnknownReasons.All(reason=>reason is null),"complete fields remain available at radius "+radius);
            }
            var tail=Enumerable.Repeat(new Hypothesis(1,300,300),513).ToArray();tail[^1]=new(1,0,0);
            var counts=new int[sources.Length];
            var final=CandidateLocalMinima([[0,0],[.5f,.5f]],tail,origin,sources,source=>
                {int map=int.Parse(source.Id[3..]);counts[map]++;return fields[map%3];},()=>false)!;
            Check(counts.All(count=>count==1),"each native field is acquired once across every hypothesis batch and all door alternatives");
            var last=CandidateLocalMinima([[0,0],[.5f,.5f]],[tail[^1]],origin,sources,source=>fields[int.Parse(source.Id[3..])%3],()=>false)!;
            Check(final.Minima.SelectMany(v=>v).SequenceEqual(last.Minima.SelectMany(v=>v)),"the final hypothesis beyond batch 512 remains part of every candidate minimum");
            var missing=sources.ToArray();missing[0]=missing[0] with{UnknownReason="missing-floor"};
            var partial=CandidateLocalMinima([[0,0]],tail,origin,missing,source=>source.Id=="map1"?null:fields[0],()=>false)!;
            Check(partial.UnknownReasons[0]=="missing-floor"&&partial.UnknownReasons[1] is not null&&partial.UnknownReasons[2] is null,
                "missing reference and failed field acquisition stay per-candidate UNKNOWN without discarding a complete candidate");
            foreach(int stopAt in new[]{1,4,8,15})
            {
                int calls=0;
                var cancelled=CandidateLocalMinima([[0,0]],tail,origin,sources,_=>fields[0],()=>++calls>=stopAt);
                Check(cancelled is null&&calls==stopAt,"cancellation exposes no partial grouped result at checkpoint "+stopAt);
            }
        }
        catch(Exception error){failures.Add(error.ToString());}
        File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(new{passed=failures.Count==0,checkCount=checks.Count,checks,failures,
            scope="Actual OpenCV local field versus independent padded dense edge-union DT and full integer offset-box enumeration. This is a numerical regression, not live game or performance acceptance."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"Local distance regression: {checks.Count-failures.Count}/{checks.Count}; {output}");return failures.Count==0?0:1;
    }
}
