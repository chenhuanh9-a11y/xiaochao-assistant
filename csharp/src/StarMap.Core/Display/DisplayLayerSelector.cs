using System.Collections.ObjectModel;
using System.Text.Json;
using StarMap.Core.Resources;

namespace StarMap.Core.Display;

public sealed record DisplayCustomRequest(ResourceCatalog Catalog,ResourceCatalogEntry Entry,string PreferredProfileId,
    string VariantId,DisplayAffine ReferenceToClient,DisplayBridgeResult ReferenceBridge);
public sealed record DisplayVariantOption(string Id,string Name,bool Available,string Reason);
public sealed record DisplaySelectionResult(bool Prepared,string Reason,string PreferredProfileId,string EffectiveProfileId,
    string AuthorStatus,bool Fallback,string FallbackReason,string? VariantId,DisplayBridgeResult? Bridge,
    string DisplayKind,string DisplayLabel,IReadOnlyList<string> CapabilityGaps,IReadOnlyDictionary<string,string> ProviderErrors)
{
    public bool AuthorizesMapIdentity=>false;
    public bool AuthorizesStructurePose=>false;
    public bool AuthorizesCrossFloorProjection=>false;
    public bool IsCurrent()=>Prepared&&Bridge?.IsCurrent()==true;
}

/// <summary>Original shared display intent/effective policy. No identity, fitting, token or pixel writer.</summary>
public sealed class DisplayLayerSelector
{
    private readonly IDisplayRasterValidator raster;
    private readonly DisplayBridgeResolver resolver;
    public DisplayLayerSelector(IDisplayRasterValidator raster){this.raster=raster;resolver=new(raster);}
    public DisplaySelectionResult Select(ResourceCatalog catalog,ResourceCatalogEntry entry,string preferredProfile,
        DisplayAffine referenceToClient,string variant="variant-01",Func<DisplayCustomRequest,DisplayBridgeResult?>? customProvider=null)
    {
        var files=new DisplayBridgeResolver.ResourceReads(catalog.RootPath);DisplayAuthorInventory? inventory=null;
        try
        {
            var reference=resolver.ResolveDisplayReference(catalog,entry,"shared-structure",referenceToClient,files,neutral:true);
            if(!reference.Accepted)return Failure("display-reference-unavailable",reference.Reason);
            var custom=customProvider?.Invoke(new(catalog,entry,preferredProfile,variant,referenceToClient,reference));
            if(custom is not null)
            {
                if(!custom.Accepted||custom.BindingKey!=entry.StableKey||custom.Reference is null||custom.ReferenceToCanonical is null
                    ||custom.CanonicalPackId!=reference.CanonicalPackId||!custom.ReferenceToCanonical.Value.AlmostEquals(reference.ReferenceToCanonical!.Value))
                    return Failure("custom-display-unavailable","custom-display-binding-mismatch");
                return Complete(custom,custom.Profile,"none","",null,"custom-gallery","自定义图集");
            }
            if(preferredProfile.StartsWith("custom:",StringComparison.Ordinal))return Failure("custom-display-unavailable","custom-display-producer-unavailable");
            inventory=DisplayAuthorInventory.Load(catalog,entry,files,raster);
            string effective=Effective(entry,preferredProfile,inventory);
            bool coffin=DisplayAuthorInventory.IsCoffin(entry,preferredProfile);
            if(coffin)effective=CoffinAvailable(entry,preferredProfile,variant,inventory,reference)?preferredProfile:"shared-structure";
            var preferredBinding=inventory.Binding(preferredProfile,entry.MapId,entry.Floor);
            string status=!inventory.Declared(preferredProfile,entry.MapId,entry.Floor)&&preferredBinding is null?"missing":
                preferredBinding is {Usable:false}?"registration-failed":"available";
            string failure=effective!=preferredProfile?"selected-author-layer-unavailable":"";
            var binding=inventory.Binding(effective,entry.MapId,entry.Floor);
            bool available=binding is not null?binding.Usable:coffin&&effective==preferredProfile||inventory.AuthorError is null;
            if(effective!="shared-structure")
            {
                var layer=inventory.FindLayer(effective,entry.MapId,entry.Floor,coffin?variant:null);
                // prepare_display_layer tests both adapters before entering its
                // bitmap-loading try block. An absent/rejected declaration is
                // binding-unavailable, not a decode/bridge exception.
                bool sourceBound=inventory.CoordinateAdapter.TryProfileToCanonical("shared-structure",entry.MapId,entry.Floor,out _,out _);
                bool displayBound=binding is {Usable:true}
                    ||inventory.CoordinateAdapter.TryProfileToCanonical(effective,entry.MapId,entry.Floor,out _,out _);
                if(inventory.Profiles.TryGetValue(effective,out var profile)&&profile.Aligned&&available&&layer is not null&&sourceBound&&displayBound)
                {
                    var chosen=resolver.ResolveDisplayReference(catalog,entry,effective,referenceToClient,files,coffin?variant:"variant-01");
                    if(chosen.Accepted)return Complete(chosen,effective,"none",failure,coffin?variant:null,"author-route",effective);
                    failure="author-display-load-or-bridge-error";
                }
                else if(!available)failure="author-registration-or-version-unavailable";
                else if(failure.Length==0)failure="author-display-binding-unavailable";
            }
            if(status=="missing"&&entry.Season=="s0"&&entry.Mode=="nightmare")
            {
                var atlas=resolver.ResolveDisplayReference(catalog,entry,"base-map",referenceToClient,files);
                if(atlas.Accepted)return Complete(atlas,"shared-structure",status,"selected-author-layer-missing",null,"base-map","基础地图（暂无作者路线）");
            }
            else if(status=="available")status=available?"load-error":"registration-failed";
            if(entry.Season=="s1")
            {
                var native=resolver.ResolveDisplayReference(catalog,entry,"shared-structure",referenceToClient,files);
                if(native.Accepted)return Complete(native,"shared-structure",status,failure,null,"game-native","S1 原生渲染地图");
                failure=native.Reason.StartsWith("s1-resource-not-uniquely-inventoried:assets/game-native/",StringComparison.Ordinal)
                    ?"s1-native-display-not-installed":"s1-native-display-invalid-binding-or-image";
            }
            return Complete(reference,"shared-structure",status,failure.Length>0?failure:"selected-author-layer-missing",null,"shared-structure",
                inventory.AuthorError=="author-structure-version-or-base-snapshot-mismatch"?"结构底图（作者路线版本不匹配）":"结构底图（作者路线不可用）");
        }
        catch(Exception error)when(DisplayAuthorInventory.ReadError(error)){return Failure(error.Message);}

        DisplaySelectionResult Failure(string reason,string? gap=null)=>new(false,reason,preferredProfile,"shared-structure","none",false,"",null,null,"","",
            gap is null?Array.Empty<string>():Array.AsReadOnly(new[]{gap}),new ReadOnlyDictionary<string,string>(inventory?.Errors??new()));
        DisplaySelectionResult Complete(DisplayBridgeResult bridge,string effective,string status,string fallbackReason,string? actualVariant,string kind,string label)
        {
            var resources=(bridge.Resources??[]).Concat(files.Snapshots).GroupBy(r=>r.Path,StringComparer.OrdinalIgnoreCase).Select(group=>
            {
                var first=group.First();if(group.Any(r=>r.Length!=first.Length||r.Sha256!=first.Sha256))throw new InvalidDataException("display-resources-changed-during-selection");
                return first with {RequiresRegularSingleLink=group.Any(r=>r.RequiresRegularSingleLink)};
            }).ToArray();
            bridge=bridge with {Resources=Array.AsReadOnly(resources)};
            if(!bridge.IsCurrent())return Failure("display-resources-changed-during-selection");
            string actual=preferredProfile.StartsWith("custom:",StringComparison.Ordinal)?"shared-structure":effective;
            return new(true,"prepared",preferredProfile,actual,status,preferredProfile!=actual&&!preferredProfile.StartsWith("custom:",StringComparison.Ordinal),fallbackReason,
                actualVariant,bridge,kind,label,Array.Empty<string>(),new ReadOnlyDictionary<string,string>(inventory?.Errors??new()));
        }
    }
    public IReadOnlyList<DisplayVariantOption> CoffinVariants(ResourceCatalog catalog,ResourceCatalogEntry entry,string profile)
    {
        if(!DisplayAuthorInventory.IsCoffin(entry,profile))return Array.Empty<DisplayVariantOption>();
        var files=new DisplayBridgeResolver.ResourceReads(catalog.RootPath);
        try
        {
            var inventory=DisplayAuthorInventory.Load(catalog,entry,files,raster);var reference=resolver.ResolveDisplayReference(catalog,entry,"shared-structure",DisplayAffine.Identity,neutral:true);
            return Array.AsReadOnly(new[]{"variant-01","variant-02"}.Select((id,i)=>new DisplayVariantOption(id,inventory.Profiles.GetValueOrDefault(profile)?.Layers.FirstOrDefault(l=>l.Map==entry.MapId&&l.Variant==id)?.Name is {Length:>0} name?name:i==0?"一号路线":"二号路线",
                CoffinAvailable(entry,profile,id,inventory,reference),CoffinReason(entry,profile,id,inventory,reference))).ToArray());
        }
        catch(Exception error)when(DisplayAuthorInventory.ReadError(error))
        {return Array.AsReadOnly(new[]{new DisplayVariantOption("variant-01","一号路线",false,error.Message),new DisplayVariantOption("variant-02","二号路线",false,error.Message)});}
    }
    private static string Effective(ResourceCatalogEntry entry,string preferred,DisplayAuthorInventory inventory)
    {
        bool native=entry.Season!="s0"||entry.Mode=="difficult";
        string display=inventory.OptionalProfiles.Contains(preferred)?preferred:native?"zhan10-speed":string.IsNullOrEmpty(preferred)?"wang-qingxin":preferred;
        if(inventory.OptionalProfiles.Contains(display))
        {
            if(inventory.Binding(display,entry.MapId,entry.Floor) is {Usable:true})return display;
            if(DisplayAuthorInventory.Generic(display))return "shared-structure";
            return LegacyAvailable(entry,inventory)?"zhan10-speed":"shared-structure";
        }
        if(display!="suju"&&!(native&&display=="zhan10-speed"))return "shared-structure";
        return inventory.Profiles.TryGetValue(display,out var profile)&&profile.RuntimeEnabled&&inventory.FindLayer(display,entry.MapId,entry.Floor) is not null?display:"shared-structure";
    }
    private static bool LegacyAvailable(ResourceCatalogEntry entry,DisplayAuthorInventory inventory)=>entry.StructureKind=="legacy-clean"
        &&inventory.FindLayer("zhan10-speed",entry.MapId,entry.Floor) is not null
        &&inventory.CoordinateAdapter.TryProfileToCanonical("shared-structure",entry.MapId,entry.Floor,out _,out _)
        &&inventory.CoordinateAdapter.TryProfileToCanonical("zhan10-speed",entry.MapId,entry.Floor,out _,out _);
    private static bool CoffinAvailable(ResourceCatalogEntry entry,string profile,string variant,DisplayAuthorInventory inventory,DisplayBridgeResult reference)=>
        CoffinReason(entry,profile,variant,inventory,reference).Length==0;
    private static string CoffinReason(ResourceCatalogEntry entry,string profile,string variant,DisplayAuthorInventory inventory,DisplayBridgeResult reference)
    {
        if(!inventory.Profiles.TryGetValue(profile,out var p)||!p.Aligned)return "当前资源未提供可显示的展十全棺路线";
        var layer=inventory.FindLayer(profile,entry.MapId,entry.Floor,variant);if(layer is null)return "此版本的当前楼层图片未安装";
        var binding=inventory.Binding(profile,entry.MapId,entry.Floor);
        if(binding is not null)return !DisplayAuthorInventory.PathEquals(layer.Path,binding.Path)?"已安装显示声明对应另一版本图片":!binding.Usable?"此版本的显示注册尚未确认":"";
        if(!reference.Accepted)return "缺少原有当前楼层坐标显示声明";
        var adapter=inventory.CoordinateAdapter;var row=adapter.Floor(profile,entry.MapId,entry.Floor);
        string? declared=J.String(J.Get(J.Get(row,"evidence"),"binding"),"variant");
        if(declared is not null&&declared!=variant)return "原坐标声明对应另一版本";
        return row is not null&&adapter.TryProfileToCanonical(profile,entry.MapId,entry.Floor,out _,out _)?"":"缺少原有当前楼层坐标显示声明";
    }
}
