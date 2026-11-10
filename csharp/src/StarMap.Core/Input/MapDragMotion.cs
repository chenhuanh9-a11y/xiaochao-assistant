using System.Text.Json;

namespace StarMap.Core.Input;

/// <summary>Physical-client map interaction policy, including calibrated leave/reentry without catch-up.</summary>
public sealed class MapDragMotion
{
    readonly double left, top, width, height;
    readonly double[]? boundary;
    double lastX, lastY, totalX, totalY;
    bool previousAllowed;
    public bool Active { get; private set; }

    public static double[]? ReadBoundary(JsonElement? value)
    {
        if(value is not {ValueKind:JsonValueKind.Array} array || array.GetArrayLength()!=4)return null;
        var result=new double[4];int i=0;
        foreach(var item in array.EnumerateArray())
            if(item.ValueKind!=JsonValueKind.Number || !item.TryGetDouble(out result[i++]))return null;
        return result.All(double.IsFinite) && result[0]>=0 && result[1]>=0 && result[2]<=1 && result[3]<=1
            && result[2]-result[0]>=.15 && result[3]-result[1]>=.15 ? result : null;
    }

    public MapDragMotion(double x,double y,double left,double top,double width,double height,JsonElement? calibratedBoundary=null)
    {
        this.left=left;this.top=top;this.width=width;this.height=height;
        boundary=ReadBoundary(calibratedBoundary);lastX=x;lastY=y;
        Active=previousAllowed=Allowed(x,y);
    }

    bool Allowed(double x,double y)
    {
        if(!double.IsFinite(x) || !double.IsFinite(y) || !double.IsFinite(left) || !double.IsFinite(top)
            || !double.IsFinite(width) || !double.IsFinite(height) || width<100 || height<100)return false;
        x-=left;y-=top;
        return boundary is { } b ? x>=width*b[0] && x<width*b[2] && y>=height*b[1] && y<height*b[3]
            : x>width*.08 && x<width*.94 && y>height*.13 && y<height*.94;
    }

    public MapDragStep? Consume(string phase,double x,double y)
    {
        if(!Active)return null;
        if(phase=="cancel"){Active=false;return new(0,0,totalX,totalY,true);}
        if(phase is not ("move" or "up") || !double.IsFinite(x) || !double.IsFinite(y))return null;
        bool allowed=Allowed(x,y), record=boundary is null || previousAllowed && allowed;
        double dx=record?x-lastX:0,dy=record?y-lastY:0;
        lastX=x;lastY=y;previousAllowed=allowed;totalX+=dx;totalY+=dy;
        if(phase=="up")Active=false;
        return new(dx,dy,totalX,totalY,!Active);
    }
}

public sealed record MapDragStep(double DeltaX,double DeltaY,double TotalX,double TotalY,bool Finished);
