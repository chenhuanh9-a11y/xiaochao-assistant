using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using StarMap.Vision;

internal static class IndexDistanceRegression
{
    readonly record struct H(double Scale,double X,double Y);
    static readonly MethodInfo Kernel=typeof(StructureIndexFilter).GetMethod("CandidateMinima",BindingFlags.Static|BindingFlags.NonPublic)!;
    static readonly Type Hypothesis=typeof(StructureIndexFilter).GetNestedType("Hypothesis",BindingFlags.NonPublic)!;
    internal static int Run(string output)
    {
        if(Directory.Exists(output))throw new IOException("Preserve prior evidence; supply a new output.");
        Directory.CreateDirectory(output);var checks=new List<object>();var timing=new List<object>();int exit=0;
        try
        {
            Verify("positive-negative-ToEven",[[.5f,0],[1.5f,0],[2.5f,0],[3.5f,0],[-.5f,0],[-1.5f,0]],
                [new(1,0,0)],4,2,[[1,2,3,4,5,6,7,8]],[1,3,3,10000,1,10000]);
            Verify("boundary-and-offset-zero",[[0,0],[MathF.BitDecrement(3.5f),0],[3.5f,0],[0,1.5f],[0,MathF.BitDecrement(1.5f)]],
                [new(1,0,0)],4,2,[[1,2,3,4,5,6,7,8]],[1,4,10000,10000,5]);
            Verify("outside-not-scaled",[[-4,0],[1,-1],[3,1]],[new(2,1,-1)],4,2,[[1,2,3,4,5,6,7,8]],[10000,2,12]);
            var threshold=Verify("exact-and-adjacent-threshold-eight",[[0,0],[.5f,0],[1,0]],[new(.5,0,0)],3,1,
                [[16,MathF.BitIncrement(16),MathF.BitDecrement(16)]]);
            Check(threshold[0].Select(v=>v>8).SequenceEqual([false,true,false]),"threshold8 strict comparison is preserved without widening");
            foreach(int count in new[]{0,1,511,512,513,1025})
            {
                var hypotheses=Enumerable.Repeat(new H(1,1000,1000),count).ToArray();
                if(count>0)hypotheses[^1]=new(1,0,0);
                var values=Verify("tail-"+count,[[0,0],[1,0]],hypotheses,3,1,[[9,7,1],[2,4,6]]);
                if(count>0)Check(values[0].SequenceEqual([9d,7d]),"last hypothesis is consumed across batch boundary "+count);
            }
            Verify("signed-zero-minimum-order",[[0,0]],[new(1,0,0),new(2,0,0)],1,1,
                [[BitConverter.Int32BitsToSingle(unchecked((int)0x80000000))]]);
            var cancellationH=Enumerable.Repeat(new H(1,0,0),513).ToArray();var packed=Pack(cancellationH);
            foreach(int stopAt in new[]{1,2,3,4,5,7,9})
            {
                int calls=0;var result=Invoke([[0,0]],packed,1,1,[[1],[2],[3]],()=>++calls>=stopAt);
                Check(result is null && calls==stopAt,"cancel at checkpoint "+stopAt+" exposes no partial map minima");
            }
            foreach(int stopAt in new[]{1,2,3})
            {
                int calls=0;var result=Invoke([[0,0]],packed,1,1,[[1]],()=>++calls>=stopAt);
                Check(result is null && calls==stopAt,"single-map cancel checkpoint "+stopAt+" exposes no partial minima");
            }
            int ordinal=0;
            foreach(var shape in new[]{(Maps:1,Hypotheses:18158),(Maps:3,Hypotheses:18158),(Maps:14,Hypotheses:2160)})
            {
                const int width=1600,height=1600;int order=ordinal++;
                var points=Enumerable.Range(0,500).Select(i=>new[]{(float)((i*631)%width),(float)((i*941)%height)}).ToArray();
                var hypotheses=Enumerable.Range(0,shape.Hypotheses).Select(i=>new H(.75+(i%31)*.00025,25+(i%47)*.25,35+(i%37)*.25)).ToArray();
                var fields=Enumerable.Range(0,shape.Maps).Select(m=>Enumerable.Range(0,width*height).Select(p=>(float)(((p/width)/11+(p%width)/7+m)%19)/3).ToArray()).ToArray();
                var native=Pack(hypotheses);
                // Warm both kernels on the same already-created immutable data.
                Equal(Reference(points,hypotheses,width,height,fields),Invoke(points,native,width,height,fields,()=>false)!,"warm-shape-"+shape.Maps);
                for(int repeat=0;repeat<6;repeat++)
                {
                    (double[][] Values,double Ms,long Allocated) old,actual;
                    if((repeat+order)%2==0){old=Measure(false);actual=Measure(true);}else{actual=Measure(true);old=Measure(false);}
                    Equal(old.Values,actual.Values,"shape-"+shape.Maps+"/"+repeat);
                    timing.Add(new{shape.Maps,shape.Hypotheses,points=points.Length,repeat,order=(repeat+order)%2==0?"old-first":"new-first",
                        oldMs=old.Ms,newMs=actual.Ms,oldAllocated=old.Allocated,newAllocated=actual.Allocated});
                    Write("timing.json",timing);
                }
                (double[][],double,long) Measure(bool current)
                {
                    long allocated=GC.GetAllocatedBytesForCurrentThread();var clock=Stopwatch.StartNew();
                    var values=current?Invoke(points,native,width,height,fields,()=>false)!:Reference(points,hypotheses,width,height,fields);
                    double ms=clock.Elapsed.TotalMilliseconds;return(values,ms,GC.GetAllocatedBytesForCurrentThread()-allocated);
                }
            }
        }
        catch(Exception error){exit=1;Write("error.json",new{error=error.ToString()});Console.Error.WriteLine(error);}
        Write("summary.json",new{exit,checks,timing,scope="Actual private production kernel versus original map/h/point loop plus independent literal controls. Bitwise minima equality, full hypotheses, no expected pose supplied. Synthetic performance shapes are not recorded/game/first-material timing; new measurements include reflection overhead."});
        return exit;

        void Check(bool ok,string name){checks.Add(new{name,passed=ok});Console.WriteLine((ok?"ok ":"FAIL ")+name);if(!ok)throw new InvalidDataException(name);}
        double[][] Verify(string name,float[][] points,H[] hypotheses,int width,int height,float[][] fields,double[]? literal=null)
        {
            var expected=Reference(points,hypotheses,width,height,fields);var actual=Invoke(points,Pack(hypotheses),width,height,fields,()=>false)!;
            Equal(expected,actual,name);
            if(literal is not null)Check(actual[0].SequenceEqual(literal),name+" independently specified values");
            if(fields.Length==1)
            {
                // The literal corner cases must exercise the actual shared
                // offset path as well as the single-map direct loop.
                var shared=Invoke(points,Pack(hypotheses),width,height,[fields[0],fields[0]],()=>false)!;
                Equal([expected[0],expected[0]],shared,name+" multi-map projection");
            }
            return actual;
        }
        void Equal(double[][] a,double[][] b,string name)=>Check(a.Length==b.Length && a.Zip(b).All(row=>row.First.Length==row.Second.Length
            && row.First.Zip(row.Second).All(v=>BitConverter.DoubleToInt64Bits(v.First)==BitConverter.DoubleToInt64Bits(v.Second))),name+" exact minima bits match original reduction");
        void Write(string name,object value)=>File.WriteAllText(Path.Combine(output,name),JsonSerializer.Serialize(value,new JsonSerializerOptions{WriteIndented=true,NumberHandling=System.Text.Json.Serialization.JsonNumberHandling.AllowNamedFloatingPointLiterals}));
    }
    static Array Pack(H[] hypotheses)
    {
        var result=Array.CreateInstance(Hypothesis,hypotheses.Length);
        for(int i=0;i<hypotheses.Length;i++)result.SetValue(Activator.CreateInstance(Hypothesis,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic,
            null,new object[]{hypotheses[i].Scale,hypotheses[i].X,hypotheses[i].Y},null),i);
        return result;
    }
    static double[][]? Invoke(float[][] points,Array hypotheses,int width,int height,float[][] fields,Func<bool> cancelled) =>
        (double[][]?)Kernel.Invoke(null,[points,hypotheses,width,height,fields,cancelled]);

    // Previous production map->batch->hypothesis->point loop, retained only in
    // this test. Manual literals above and original frame oracles are independent checks.
    static double[][] Reference(float[][] points,H[] hypotheses,int width,int height,float[][] fields)
    {
        var result=new double[fields.Length][];
        for(int map=0;map<fields.Length;map++)
        {
            var minimum=Enumerable.Repeat(double.PositiveInfinity,points.Length).ToArray();var field=fields[map];
            for(int start=0;start<hypotheses.Length;start+=512)
            {
                int stop=Math.Min(hypotheses.Length,start+512);
                for(int h=start;h<stop;h++)
                {
                    var hypothesis=hypotheses[h];
                    for(int p=0;p<points.Length;p++)
                    {
                        int x=(int)Math.Round((points[p][0]-hypothesis.X)/hypothesis.Scale,MidpointRounding.ToEven);
                        int y=(int)Math.Round((points[p][1]-hypothesis.Y)/hypothesis.Scale,MidpointRounding.ToEven);
                        double distance=x<0 || y<0 || x>=width || y>=height?10000:field[y*width+x]*hypothesis.Scale;
                        minimum[p]=Math.Min(minimum[p],distance);
                    }
                }
            }
            result[map]=minimum;
        }
        return result;
    }
}
