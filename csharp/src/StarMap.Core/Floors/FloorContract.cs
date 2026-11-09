namespace StarMap.Core.Floors;

/// <summary>Logical identities in a declared mode. A display slot is never a floor value.</summary>
public sealed class FloorContract
{
    private readonly (int Value,string Id)[] floors;
    public string Season { get; }
    public string Mode { get; }
    public IReadOnlyList<int> ObservedValues { get; }
    public IReadOnlyList<int> ColumnOrder { get; }
    private FloorContract(string season,string mode,params (int,string)[] floors)
    {
        Season=season; Mode=mode; this.floors=floors;
        ObservedValues=Array.AsReadOnly(floors.Select(f=>f.Item1).ToArray());
        ColumnOrder=Array.AsReadOnly((season=="s0"?ObservedValues.Reverse():ObservedValues).ToArray());
    }
    public static FloorContract? Find(string season,string mode) => (season,mode) switch {
        ("s0","nightmare")=>new(season,mode,(-1,"B1"),(1,"1"),(2,"2")),
        ("s0","difficult")=>new(season,mode,(1,"1"),(2,"2")),
        ("s1","nightmare" or "difficult" or "difficulty2")=>new(season,mode,(-1,"-1"),(-2,"-2"),(-3,"-3"),(-4,"-4")),
        _=>null };
    public string? IdFor(int? observed) => floors.FirstOrDefault(f=>f.Value==observed).Id;
    public int? ValueFor(string id) => floors.Where(f=>f.Id==id).Select(f=>(int?)f.Value).FirstOrDefault();
}
