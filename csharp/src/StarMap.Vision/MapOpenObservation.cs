using OpenCvSharp;
using System.Text.Json.Nodes;
using static StarMap.Vision.FloorPixels;

namespace StarMap.Vision;

internal static class MapOpenObservation
{
    internal static readonly (string Name,double X,double Y,double W,double H,double Threshold)[] Anchors=[
        ("floorSwitch",.75,.15,.18,.45,.025),("closeButton",.90,.04,.095,.24,.025),
        ("zoomControl",.91,.27,.085,.52,.018),("bottomControls",.78,.77,.21,.22,.025)];
    internal static JsonObject Ratios(Mat frame,bool tinted)
    {
        var bytes=Geometry.Bytes(frame);int w=frame.Cols,h=frame.Rows;
        var result=new JsonObject();var safe=Viewport(w,h);
        foreach(var a in Anchors)
        {
            var rect=Region(safe,a.X,a.Y,a.W,a.H);int count=0,matched=0;
            for(int y=rect.Y;y<rect.Bottom;y+=2) for(int x=rect.X;x<rect.Right;x+=2)
            {
                int p=3*(y*w+x),b=bytes[p],g=bytes[p+1],r=bytes[p+2],sum=b+g+r;
                int span=Math.Max(Math.Max(b,g),r)-Math.Min(Math.Min(b,g),r);
                if(sum>=(tinted?52:90)*3 && sum<=230*3 && span<=(tinted?90:55)) matched++;
                count++;
            }
            result[a.Name]=count==0?0:matched/(double)count;
        }
        return result;
    }

    internal static (double Ratio,double Dark,double Std) Backdrop(Mat frame)
    {
        int w=frame.Cols,h=frame.Rows;var rect=Region(new(0,0,w,h),.25,.14,.63,.66);
        var bytes=Geometry.Bytes(frame);int nx=(rect.Width+3)/4,ny=(rect.Height+3)/4,count=nx*ny,matched=0,i=0;
        if(count==0)return(0,0,255);
        var sampled=new byte[count*3];
        for(int y=rect.Y;y<rect.Bottom;y+=4) for(int x=rect.X;x<rect.Right;x+=4)
        {
            int p=3*(y*w+x),b=bytes[p],g=bytes[p+1],r=bytes[p+2],sum=b+g+r;
            if(sum>=14*3 && sum<=88*3 && b>=r-4 && b>=g-8) matched++;
            sampled[i++]=(byte)b;sampled[i++]=(byte)g;sampled[i++]=(byte)r;
        }
        using var packed=Mat.FromPixelData(ny,nx,MatType.CV_8UC3,sampled);
        using var gray=Gray(packed);
        var values=Geometry.Bytes(gray);
        double mean=values.Sum(v=>(long)v)/(double)count;
        double[] squares=values.Select(v=>(v-mean)*(v-mean)).ToArray();
        double ss=0;
        for(int k=0;k<count;k+=8192) ss+=Sum(squares.AsSpan(k,Math.Min(8192,count-k)));
        return(matched/(double)count,values.Count(v=>v<75)/(double)count,Math.Sqrt(ss/count));
    }

    private static double Sum(ReadOnlySpan<double> a)
    {
        if(a.Length<8){double s=-0d;foreach(var v in a)s+=v;return s;}
        if(a.Length>128){int n=a.Length/2/8*8;return Sum(a[..n])+Sum(a[n..]);}
        Span<double> t=stackalloc double[8];a[..8].CopyTo(t);int i=8;
        for(;i+7<a.Length;i+=8)for(int j=0;j<8;j++)t[j]+=a[i+j];
        double v0=((t[0]+t[1])+(t[2]+t[3]))+((t[4]+t[5])+(t[6]+t[7]));
        for(;i<a.Length;i++)v0+=a[i];return v0;
    }
    internal static JsonObject Detect(Mat frame,JsonObject floorRead)
    {
        int w=frame.Cols,h=frame.Rows;double aspect=w/(double)Math.Max(1,h);
        bool accepted=w>=1000 && aspect>=1.25;
        var result=new JsonObject{["frameWidth"]=w,["frameHeight"]=h,["aspectRatio"]=Math.Round(aspect,4),["shapeAccepted"]=accepted,
            ["layoutFamily"]=w<1000?"unsupported-small":aspect<1.25?"unsupported-narrow":aspect>=1.45?"wide":"narrow-4x3"};
        var neutral=accepted?Ratios(frame,false):new JsonObject();var tinted=new JsonObject();
        int cn=accepted?Anchors.Count(a=>N(neutral[a.Name])>=a.Threshold):0,ct=0;
        var (back,dark,std)=accepted?Backdrop(frame):(0d,0d,255d);
        bool flat=dark>=.84 && std<=22.5;
        if(accepted && cn<3 && back>=.80){tinted=Ratios(frame,true);ct=Anchors.Count(a=>N(tinted[a.Name])>=a.Threshold);}
        bool damage=back>=.80 && ct>=3;
        bool hasFloor=damage && S(floorRead,"controlAxis")=="vertical" && floorRead["selectedFloor"]?.GetValue<int>() is (-1 or 1 or 2) &&
            floorRead["labelEvidence"] is JsonArray{Count:>0};
        int matched=Math.Max(cn,damage?ct:0);double required=aspect<1.45?.55:.45;
        bool detected=accepted && flat && back>=required && (cn>=3 || damage&&hasFloor);
        result["reason"]=w<1000?"capture-shape-width":aspect<1.25?"capture-shape-aspect":detected?"map-interface-detected":!flat?"map-canvas-texture-mismatch":
            back<required?"map-backdrop-insufficient":matched<3?"map-anchors-insufficient":damage&&!hasFloor?"tinted-map-controls-unlocalized":"map-evidence-insufficient";
        result["detected"]=detected;result["matchedAnchors"]=matched;result["neutralMatchedAnchors"]=cn;result["tintedMatchedAnchors"]=ct;
        result["ratios"]=neutral;result["tintedRatios"]=tinted;result["backdropRatio"]=back;result["backdropTexture"]=new JsonObject{["darkRatio"]=dark,["grayStd"]=std};
        result["flatMapCanvas"]=flat;result["detectionMode"]=!accepted?"shape-rejected":damage?"damage-tint":"neutral";
        if(accepted)
        {
            result["tintedControlsLocalized"]=damage?JsonValue.Create(hasFloor):null;result["requiredBackdropRatio"]=required;
            result["decisionThresholds"]=new JsonObject{["anchorRatios"]=J(Anchors.ToDictionary(a=>a.Name,a=>a.Threshold)),
                ["darkRatioMin"]=.84,["grayStdMax"]=22.5,["strongBackdropMin"]=.80,["matchedAnchorsMin"]=3,["requiredBackdropRatio"]=required};
            result["clientCoordinatePolicies"]=new JsonObject{["ui-roi"]="safe-viewport",["floor-buttons"]="safe-viewport",
                ["map-canvas"]="full-client",["exit-icons"]="coordinate-adapter",["current-frame-pose"]="captured-client",["route-render"]="captured-client"};
        }
        return result;
    }
}
