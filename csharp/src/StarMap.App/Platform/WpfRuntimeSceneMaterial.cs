using System.IO;
using System.Windows.Media.Imaging;
using StarMap.Core.Display;
using StarMap.Runtime;

namespace StarMap.App.Platform;

/// <summary>Frozen resident floor bitmaps only. The fresh packet supplies all view geometry and permission.</summary>
internal sealed class WpfRuntimeSceneMaterial
{
    internal required object Owner { get; init; }
    public required BitmapSource Bitmap { get; init; }
    internal required IReadOnlyList<WpfSceneLayer> Layers { get; init; }
    internal required IReadOnlyDictionary<string,string> Unavailable { get; init; }
    internal static object OwnerOf(RuntimeRenderPacket packet) => (object?)packet.Scene?.Sources??packet.Material.Pixels;
    internal bool Matches(RuntimeRenderPacket packet) => ReferenceEquals(Owner,OwnerOf(packet))
        && (packet.Scene is not { } scene || Layers.Any(l=>l.Floor==scene.SelectedFloor));

    internal static WpfRuntimeSceneMaterial Prepare(RuntimeRenderPacket packet)
    {
        if(packet.Scene is not { } scene)
        {
            var bitmap=WpfDisplayMaterialConsumer.PrepareMaterialReady(packet.Material);
            return new(){Owner=packet.Material.Pixels,Bitmap=bitmap,Layers=[],Unavailable=new Dictionary<string,string>()};
        }
        var layers=new List<WpfSceneLayer>();var unavailable=new Dictionary<string,string>();BitmapSource? current=null;
        foreach(var floor in scene.Sources.Floors.Values)
        {
            try
            {
                var bitmap=WpfDisplayMaterialConsumer.PrepareMaterialReady(floor.Material);
                layers.Add(new(floor.Floor,bitmap,Matrix(floor.DisplayToCommon)));
                if(floor.Floor==scene.SelectedFloor)current=bitmap;
            }
            catch(Exception error){unavailable[floor.Floor]=error.GetType().Name;}
        }
        if(current is null)throw new InvalidDataException("Current scene floor bitmap unavailable.");
        return new(){Owner=scene.Sources,Bitmap=current,Layers=layers.AsReadOnly(),Unavailable=unavailable};
    }
    internal WpfAlignedOverlayPreparedFrame Frame(WpfAlignedOverlayTicket ticket,RuntimeRenderPacket packet,double opacity)
    {
        if(!Matches(packet))throw new InvalidDataException("Scene material belongs to another packet source.");
        var g=packet.Client!.Geometry;var client=new WpfOverlayClientGeometry(g.Left,g.Top,g.Width,g.Height,g.Dpi);
        return packet.Scene is { } scene
            ? WpfAlignedOverlaySurface.PrepareScene(ticket,this,Layers,scene.SelectedFloor,Matrix(scene.CommonToClient),client,opacity)
            : WpfAlignedOverlaySurface.PrepareAffine(ticket,Bitmap,Matrix(packet.Material.Bridge.DisplayToClient!.Value),client,opacity);
    }
    internal static WpfOverlayHomography Matrix(DisplayAffine value)=>new(value.M00,value.M01,value.M02,value.M10,value.M11,value.M12,0,0,1);
}
