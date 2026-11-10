using System.Reflection;
using System.Text.Json;
using StarMap.Core.Resources;
using StarMap.Runtime;

// Host-side interleaving and display-declaration faults only; no solver or Runtime state replacement.
internal sealed class ProjectionAuthorMaterialHold:IDisposable
{
    private readonly ManualResetEventSlim entered=new(),release=new();private readonly Thread owner;
    private bool expired;
    internal ProjectionAuthorMaterialHold(LiveRuntime runtime)
    {
        var bound=Field(runtime,"_resources")!;var materials=bound.GetType().GetProperty("DisplayMaterials")!.GetValue(bound)!;
        object gate=Field(materials,"pixelSync")!;
        owner=new Thread(()=>{lock(gate){entered.Set();if(!release.Wait(TimeSpan.FromSeconds(20)))expired=true;}}){IsBackground=true};
        owner.Start();if(!entered.Wait(TimeSpan.FromSeconds(5)))throw new TimeoutException("material lock was not acquired");
    }
    internal bool Held=>owner.IsAlive && entered.IsSet && !release.IsSet;
    internal void Release()=>release.Set();
    public void Dispose(){release.Set();if(!owner.Join(TimeSpan.FromSeconds(5)) || expired)throw new TimeoutException("material lock owner did not retire normally");entered.Dispose();release.Dispose();}
    private static object? Field(object value,string name)=>value.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(value);
}

internal sealed class ProjectionAuthorCoordinateFault:IDisposable
{
    private readonly ResourceCatalogEntry entry;private readonly FieldInfo metadata;private readonly object? previous;
    internal ProjectionAuthorCoordinateFault(LiveRuntime runtime,string mode,string map)
    {
        var bound=runtime.GetType().GetField("_resources",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(runtime)!;
        var catalog=(ResourceCatalog)bound.GetType().GetProperty("Catalog")!.GetValue(bound)!;
        var passports=bound.GetType().GetProperty("Passports")!.GetValue(bound)!;
        var profiles=(IReadOnlyDictionary<string,string>)passports.GetType().GetProperty("MapProfiles")!.GetValue(passports)!;
        entry=catalog.Find(mode,"s0",profiles[map],map,"2")!;
        metadata=typeof(ResourceCatalogEntry).GetField("<CoordinateMetadata>k__BackingField",BindingFlags.Instance|BindingFlags.NonPublic)!;
        previous=metadata.GetValue(entry);
        metadata.SetValue(entry,JsonSerializer.SerializeToElement(new {scope="explicit-display-declaration-mismatch-fixture"}));
    }
    public void Dispose()=>metadata.SetValue(entry,previous);
}
