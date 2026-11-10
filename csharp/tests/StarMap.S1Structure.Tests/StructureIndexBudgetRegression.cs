using System.Reflection;
using System.Text.Json;
using StarMap.Vision;

/// <summary>Calls the real private domain solver with independently corresponding synthetic vertices.
/// This is a domain-unit regression, not frame extraction, runtime, or real-game acceptance.</summary>
internal static class StructureIndexBudgetRegression
{
    public static int Run(string output)
    {
        output = Path.GetFullPath(output);
        if (Directory.Exists(output)) throw new IOException("Use a new regression output directory.");
        Directory.CreateDirectory(output);
        var cases = new List<object>(); var failures = new List<string>();
        var method = typeof(StructureIndexFilter).GetMethod("DomainFromPairs",BindingFlags.NonPublic|BindingFlags.Static)
            ?? throw new MissingMethodException("StructureIndexFilter.DomainFromPairs");

        // A 120px square gives independent fixed-axis scale bounds [0.95,1.05].
        // Six unique chain pairs contain all four square extrema plus two interior turns.
        // Their exact shared source->observation translation leaves a wide feasible domain.
        var wide = Fixture(1);
        var witness = SquareDomainWitness(120);
        if (witness <= 20000) throw new InvalidDataException("Synthetic fixture must exceed the real 20,000 hypothesis cap.");
        var exhausted = Invoke(method,wide.Source,wide.Query,wide.Pairs,() => false);
        bool distinguished = exhausted.ExceptionType == "CoordinateBudgetExceededException";
        cases.Add(new { id="wide-valid-domain-must-distinguish-budget", passed=distinguished,
            independentFeasibleHypothesisCount=witness, sourceSpanPx=120, scaleBounds=new[]{.95,1.05},
            resultWasNull=exhausted.Result is null, exceptionType=exhausted.ExceptionType,
            scope="Actual DomainFromPairs; exact corresponding synthetic vertices, no map/runtime answer injection" });
        if (!distinguished) failures.Add("A feasible domain above the cap was indistinguishable from no feasible domain.");

        // A larger source span narrows the admitted scale interval and stays under the cap.
        var bounded = Fixture(10);
        var ordinary = Invoke(method,bounded.Source,bounded.Query,bounded.Pairs,() => false);
        int hypothesisCount = ordinary.Result?.GetType().GetProperty("Hypotheses")?.GetValue(ordinary.Result) is Array hypotheses ? hypotheses.Length : 0;
        bool ordinaryPassed = ordinary.ExceptionType is null && hypothesisCount is >0 and <=20000;
        cases.Add(new { id="bounded-valid-domain-remains-available", passed=ordinaryPassed, hypothesisCount, exceptionType=ordinary.ExceptionType });
        if (!ordinaryPassed) failures.Add("A bounded exact correspondence domain was lost.");

        var insufficient = Invoke(method,wide.Source,wide.Query,wide.Pairs.Take(5).ToList(),() => false);
        bool insufficientPassed = insufficient.Result is null && insufficient.ExceptionType is null;
        cases.Add(new { id="insufficient-correspondences-remain-ordinary-unknown", passed=insufficientPassed, exceptionType=insufficient.ExceptionType });
        if (!insufficientPassed) failures.Add("Insufficient correspondences were misclassified as budget exhaustion.");

        var cancelled = Invoke(method,wide.Source,wide.Query,wide.Pairs,() => true);
        bool cancellationPassed = cancelled.Result is null && cancelled.ExceptionType is null;
        cases.Add(new { id="cancelled-domain-remains-obsolete-not-budget", passed=cancellationPassed, exceptionType=cancelled.ExceptionType });
        if (!cancellationPassed) failures.Add("Cancellation was misclassified as budget exhaustion.");

        var summary = new { passed=failures.Count==0, cases, failures,
            scope="Domain-unit execution only. The public frame/resource Evaluate budget policy requires separate integration coverage; no whole-frame claim." };
        File.WriteAllText(Path.Combine(output,"summary.json"),JsonSerializer.Serialize(summary,new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine(JsonSerializer.Serialize(new { summary.passed, caseCount=cases.Count, failureCount=failures.Count }));
        return summary.passed ? 0 : 1;
    }

    private static (object? Result,string? ExceptionType) Invoke(MethodInfo method,float[][][] source,IReadOnlyList<FastObservedChain> query,
        List<(int Query,int Source)> pairs,Func<bool> cancelled)
    {
        try { return (method.Invoke(null,[source,query,pairs,cancelled]),null); }
        catch (TargetInvocationException error) when (error.InnerException is not null) { return (null,error.InnerException.GetType().Name); }
    }

    private static (float[][][] Source,FastObservedChain[] Query,List<(int Query,int Source)> Pairs) Fixture(int multiplier)
    {
        int[][][] coordinates =
        [
            [[0,20],[0,0],[20,0]], [[100,0],[120,0],[120,20]],
            [[120,100],[120,120],[100,120]], [[20,120],[0,120],[0,100]],
            [[40,40],[40,60],[60,60]], [[80,60],[80,80],[60,80]]
        ];
        var source = coordinates.Select(chain => chain.Select(point => new[]{(float)(point[0]*multiplier),(float)(point[1]*multiplier)}).ToArray()).ToArray();
        var query = source.Select(chain =>
        {
            var points = chain.Select(p => new[]{(double)p[0]+300,(double)p[1]+400}).ToArray();
            var rays = new[]{0,2}.Select(endpoint =>
            {
                double x=points[endpoint][0]-points[1][0],y=points[endpoint][1]-points[1][1],length=Math.Sqrt(x*x+y*y);
                return new[]{x/length,y/length};
            }).ToArray();
            return new FastObservedChain(points,rays);
        }).ToArray();
        return (source,query,Enumerable.Range(0,source.Length).Select(i=>(Query:i,Source:i)).ToList());
    }

    // Independent analytic square fixture: endpoint constraints alone leave translation
    // width 6 - span*abs(scale-1) on each axis. Count the source's .00025 sampling grid.
    // This is evidence that the input is feasible and above budget, not an expected pose
    // supplied to the solver or a reimplementation of its correspondence machinery.
    private static int SquareDomainWitness(double span)
    {
        double low=1-6/span,high=1+6/span,step=.00025,adjusted=(low+step)-low;
        int count=(int)Math.Ceiling((high+step*.5-low)/step),total=0;
        for(int i=0;i<count;i++)
        {
            double scale=low+i*adjusted,width=6-span*Math.Abs(scale-1);
            if(width<0)continue;
            int axisCount=Math.Max(2,(int)Math.Ceiling(width/.5)+1);
            total+=axisCount*axisCount;
        }
        return total;
    }
}
