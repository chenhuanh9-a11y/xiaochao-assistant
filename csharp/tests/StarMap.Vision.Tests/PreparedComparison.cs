using System.Globalization;
using StarMap.Core.Resources;
using StarMap.Vision;

namespace StarMap.Vision.Tests;

/// <summary>Offline source generation checked against the original bound Python prepared product.</summary>
internal static class PreparedComparison
{
    internal sealed record Result(bool Passed,int GeneratedWidth,int GeneratedHeight,int OriginalWidth,int OriginalHeight,
        double GeneratedPitch,double OriginalPitch,int GeneratedChains,int OriginalChains,
        int GeneratedRays,int OriginalRays,int DistanceValues,int DistanceDifferences,int ChainDifferences,int RayDifferences,
        string AllDifferences);

    internal static Result Run(string directory, SharedStructureData data, FastPrepared original)
    {
        var actual = FastStructure.Prepare(data);
        string csv = Path.Combine(directory,"prepare-differences.csv");
        using var differences = File.CreateText(csv);
        differences.WriteLine("field,index,point,axis,expected,actual");
        int distanceDiffs = 0;
        for (int i=0;i<Math.Max(actual.Distance.Length,original.Distance.Length);i++)
        {
            ushort? before = i < original.Distance.Length ? original.Distance[i] : null;
            ushort? after = i < actual.Distance.Length ? actual.Distance[i] : null;
            if (before == after) continue;
            differences.WriteLine(FormattableString.Invariant($"distance,{i},,,{before},{after}"));
            distanceDiffs++;
        }
        int chainDiffs = Vectors("chains",original.Chains,actual.Chains);
        int rayDiffs = Vectors("rays",original.Rays,actual.Rays);
        return new(original.Width==actual.Width && original.Height==actual.Height && original.Pitch==actual.Pitch &&
            chainDiffs==0 && rayDiffs==0 && distanceDiffs==0, actual.Width,actual.Height,original.Width,original.Height,
            actual.Pitch,original.Pitch,actual.Chains.Length,original.Chains.Length,actual.Rays.Length,original.Rays.Length,
            original.Distance.Length,distanceDiffs,chainDiffs,rayDiffs,Path.GetFileName(csv));

        int Vectors(string name,float[][][] expected,float[][][] generated)
        {
            int count=0;
            for (int i=0;i<Math.Max(expected.Length,generated.Length);i++)
            {
                float[][] before = i<expected.Length ? expected[i] : [], after = i<generated.Length ? generated[i] : [];
                for (int p=0;p<Math.Max(before.Length,after.Length);p++)
                {
                    float[] e = p<before.Length ? before[p] : [], a = p<after.Length ? after[p] : [];
                    for (int axis=0;axis<Math.Max(e.Length,a.Length);axis++)
                    {
                        float? left = axis<e.Length ? e[axis] : null, right = axis<a.Length ? a[axis] : null;
                        if (left == right) continue;
                        differences.WriteLine(string.Create(CultureInfo.InvariantCulture,$"{name},{i},{p},{axis},{left:R},{right:R}"));
                        count++;
                    }
                }
            }
            return count;
        }
    }
}
