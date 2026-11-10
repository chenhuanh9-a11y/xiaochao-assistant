using System.Text.Json;

namespace StarMap.Core.Floors;

/// <summary>Current-frame HUD hit testing only; no floor, pose or display grant.</summary>
public static class GameFloorControl
{
    public static int? Hit(double screenX,double screenY,IReadOnlyList<double> bounds,JsonElement read)
    {
        if(bounds.Count!=4 || !bounds.All(double.IsFinite) || !double.IsFinite(screenX) || !double.IsFinite(screenY))return null;
        var contract=FloorContract.Find(Text(read,"season"),Text(read,"mode"));
        string layout=Text(read,"layout");bool s0=layout=="localized-s0-vertical";
        if(contract is null || (!s0 && layout!="localized-floor-contract") || !True(read,"mapOpen")
            || !True(read,"controlGeometryVerified") || Text(read,"controlAxis")!="vertical" || s0 && contract.Season!="s0")return null;
        var order=s0?contract.ColumnOrder:contract.ObservedValues;
        double width=bounds[2]-bounds[0],height=bounds[3]-bounds[1];
        if(width<100 || height<100)return null;
        if(s0)
        {
            if(!Integer(Property(read,"selectedFloor"),out int selected) || !order.Contains(selected))return null;
            var visual=Property(read,"observedOrder");var size=Property(read,"controlFrameSize");
            if(visual.ValueKind!=JsonValueKind.Array || visual.GetArrayLength()!=order.Count || size.ValueKind!=JsonValueKind.Array || size.GetArrayLength()!=2)return null;
            for(int i=0;i<order.Count;i++)if(!Integer(visual[i],out int floor) || floor!=order[i])return null;
            if(!Integer(size[0],out int w) || !Integer(size[1],out int h) || w<100 || h<100 || Math.Abs(w-width)>1 || Math.Abs(h-height)>1)return null;
        }
        var rows=Property(read,"controlRects");if(rows.ValueKind!=JsonValueKind.Array || rows.GetArrayLength()!=order.Count)return null;
        double x=(screenX-bounds[0])/width,y=(screenY-bounds[1])/height;
        double[]? previous=null;int? hit=null;int hits=0;
        for(int i=0;i<order.Count;i++)
        {
            var row=rows[i];var rectangle=Property(row,"rect");
            if(!Integer(Property(row,"floor"),out int floor) || floor!=order[i] || rectangle.ValueKind!=JsonValueKind.Array || rectangle.GetArrayLength()!=4)return null;
            var r=new double[4];for(int j=0;j<4;j++)if(!Number(rectangle[j],out r[j]))return null;
            if(!(0<=r[0] && r[0]<r[2] && r[2]<=1 && 0<=r[1] && r[1]<r[3] && r[3]<=1))return null;
            if(previous is { } p && (s0?!(p[3]<r[1] && Math.Max(p[0],r[0])<Math.Min(p[2],r[2])):(p[1]+p[3])>=(r[1]+r[3])))return null;
            if(r[0]<=x && x<=r[2] && r[1]<=y && y<=r[3]){hit=floor;hits++;}
            previous=r;
        }
        return hits==1?hit:null;
    }
    static JsonElement Property(JsonElement value,string name)=>value.ValueKind==JsonValueKind.Object && value.TryGetProperty(name,out var item)?item:default;
    static string Text(JsonElement value,string name)=>Property(value,name) is {ValueKind:JsonValueKind.String} item?item.GetString()??"":"";
    static bool True(JsonElement value,string name)=>Property(value,name).ValueKind==JsonValueKind.True;
    static bool Integer(JsonElement value,out int number){number=0;return value.ValueKind==JsonValueKind.Number && value.TryGetInt32(out number);}
    static bool Number(JsonElement value,out double number){number=0;return value.ValueKind==JsonValueKind.Number && value.TryGetDouble(out number) && double.IsFinite(number);}
}
