using OpenCvSharp;
using System.Text.Json;
using System.Text.Json.Nodes;
using StarMap.Core.Identity;

namespace StarMap.Vision;

internal static class MainEntryGate
{
    internal static List<Corner> Corners(Mat frame,Observation observation)
    {
        using var kernel=Geometry.Kernel(5);
        using var tube=new Mat();
        Cv2.Dilate(Geometry.Observed(frame,observation),tube,kernel);
        int width=tube.Cols,height=tube.Rows;
        var pixels=Geometry.Bytes(tube);
        return Geometry.Nodes(observation).Where(n=>n.Valid && Supported(n,0) && Supported(n,1)).ToList();
        bool Supported(Corner corner,int side)
        {
            int end=Math.Min(12,(int)corner.Lengths[side]);
            if(end<=2) return false;
            for(int i=2;i<end;i++)
            {
                var p=corner.Point+corner.Rays[side]*i;
                if(pixels[Math.Clamp(Numerics.Rint(p.Y),0,height-1)*width+Math.Clamp(Numerics.Rint(p.X),0,width-1)]==0) return false;
            }
            return true;
        }
    }

    internal static (JsonObject Report,HashSet<string>? Allowed) Select(Mat frame,EntryObservation? entry,List<Corner> nodes,
        EntryPassportCatalog catalog,string floor,bool enabled)
    {
        var report=new JsonObject { ["automaticGateDetection"]=true,["applied"]=false };
        if(!enabled || catalog.Mode!="nightmare" || floor!="1")
        { report["reason"]="unsupported-mode-or-floor"; return (report,null); }
        report["reason"]="entrance-not-observed";
        var states=new JsonObject();
        report["states"]=states;
        if(entry != null)
        {
            var anchors=Anchors(nodes);
            if(anchors.Count==0)
                foreach(var bounds in entry.OtherBounds.Take(3))
                {
                    using var patch=new Mat(frame,bounds);
                    using var mask=Geometry.ColorMask(patch);
                    using var kernel=Geometry.Kernel(3);
                    var query=new Mat();
                    Cv2.Dilate(mask,query,kernel);
                    using var local=new Observation(patch,query,new Rect(0,0,patch.Cols,patch.Rows),new());
                    var other=Corners(patch,local); var measured=Anchors(other);
                    if(measured.Count==0) continue;
                    anchors=measured;
                    report["includedDisconnectedEntrance"]=true;
                    report["observationOrigin"]=new JsonArray(bounds.X,bounds.Y);
                    if(anchors.Count==1) Measure(patch,local,other,anchors[0]);
                    break;
                }
            if(anchors.Count==1 && !report.ContainsKey("includedDisconnectedEntrance")) Measure(frame,entry.Geometry,nodes,anchors[0]);
            report["anchorCount"]=anchors.Count;
        }
        var ids=catalog.MapProfiles.Keys.ToHashSet(StringComparer.Ordinal);
        report["registeredCount"]=ids.Count; report["selectedCount"]=ids.Count;
        var opened=states.Where(p=>p.Value!.ToString()=="open").Select(p=>p.Key).ToHashSet();
        var closed=states.Where(p=>p.Value!.ToString()=="closed").Select(p=>p.Key).ToHashSet();
        if(opened.Count==0 || closed.Count>1) return (report,null);
        var allowed=new HashSet<string>(ids,StringComparer.Ordinal);
        foreach(var item in catalog.MainEntries)
        {
            if(item.GetProperty("floor").GetString()!="1" || !item.TryGetProperty("entryRoomSource",out var room) ||
                room.ValueKind!=JsonValueKind.Object || !room.TryGetProperty("rotation",out var rotation) ||
                !rotation.TryGetInt32(out int r) || r!=1 || !room.TryGetProperty("roomConfigId",out var kind) || !kind.TryGetInt32(out int id)) continue;
            string[]? dirs=id switch {10201=>["top","left","right"],10202=>["left","right"],10203=>["top","left"],10204=>["top","right"],_=>null};
            if(dirs!=null && (!opened.IsSubsetOf(dirs) || closed.Overlaps(dirs))) allowed.Remove(item.GetProperty("mapId").GetString()!);
        }
        if(allowed.Count==0){report["reason"]="no-compatible-layout";return(report,null);}
        report["applied"]=allowed.Count<ids.Count; report["selectedCount"]=allowed.Count;
        report["selectedMapIds"]=JsonSerializer.SerializeToNode(allowed.Order(StringComparer.Ordinal));
        return(report,allowed.Count<ids.Count?allowed:null);

        void Measure(Mat pixels,Observation obs,List<Corner> points,(Point2d Center,double Unit) anchor)
        {
            report["reason"]="current-frame-entrance-ports";
            report["anchor"]=new JsonObject { ["center"]=MainEntrySource.PointJson(anchor.Center),["unit"]=anchor.Unit };
            var evidence=new JsonObject(); report["evidence"]=evidence;
            var query=Geometry.Bytes(obs.Query); var edges=Geometry.Bytes(Geometry.Observed(pixels,obs)); var bgr=Geometry.Bytes(pixels);
            int w=pixels.Cols,h=pixels.Rows;
            foreach(var (name,offset,normal) in new[] {
                ("left",new Point2d(-1.5,-1.5),new Point2d(-1,0)),("right",new Point2d(1.5,-1.5),new Point2d(1,0)),
                ("top",new Point2d(0,-4),new Point2d(0,-1)) })
            {
                double unit=anchor.Unit;
                var port=anchor.Center+offset*unit; var tangent=new Point2d(normal.Y,normal.X);
                int total=0,inside=0,outside=0,wall=0;
                bool clipped=false;
                for(int i=0;i<=10;i++)
                {
                    var p=port+tangent*(Lin(-.28,.28,i)*unit);
                    int x=Numerics.Rint(p.X),y=Numerics.Rint(p.Y);
                    bool occupied=false;
                    for(int j=0;j<=10;j++)
                    {
                        var q=p+normal*(Lin(-.3,.3,j)*unit); int xx=Numerics.Rint(q.X),yy=Numerics.Rint(q.Y);
                        if(xx<3||yy<3||xx>=w-3||yy>=h-3){clipped=true;break;}
                        int value=query[yy*w+xx];
                        total+=value; if(j<3) inside+=value; if(j>=8) outside+=value;
                    }
                    if(clipped) break;
                    for(int yy=y-3;yy<=y+3;yy++) for(int xx=x-3;xx<=x+3;xx++) occupied|=edges[yy*w+xx]!=0;
                    if(occupied) wall++;
                }
                if(clipped){states[name]="unknown";continue;}
                double interior=inside/33d,exterior=outside/33d,support=wall/11d;
                string state=total/121d>=.9 && Math.Min(interior,exterior)>=.9 ? "open" :
                    interior>=.9 && exterior<=.1 && support>=.9 ? "closed" : "unknown";
                if(state=="open")
                {
                    bool Neck(double x,double y,Point2d a,Point2d b) => points.Any(n=>
                        Numerics.Norm(n.Point-(anchor.Center+new Point2d(x,y)*unit))<=3 && Rays(n,a,b));
                    if(name=="left" && !Neck(-1.5,-2,new(0,-1),new(-1,0)) ||
                       name=="right" && !Neck(1.5,-2,new(1,0),new(0,-1)) ||
                       name=="top" && !(Neck(-.5,-4,new(0,-1),new(-1,0))&&Neck(.5,-4,new(1,0),new(0,-1)))) state="unknown";
                }
                double ridge=0;
                if(state=="open")
                {
                    int radius=Math.Max(3,Numerics.Rint(unit*.12));
                    for(int cross=-radius;cross<=radius;cross++)
                    {
                        int count=0;
                        for(int i=0;i<=10;i++)
                        {
                            var p=port+tangent*(Lin(-.28,.28,i)*unit);
                            double Gray(int d)
                            {
                                var q=p+normal*d;int idx=(Math.Clamp(Numerics.Rint(q.Y),0,h-1)*w+Math.Clamp(Numerics.Rint(q.X),0,w-1))*3;
                                return bgr[idx]*.114+bgr[idx+1]*.587+bgr[idx+2]*.299;
                            }
                            double center=Gray(cross);
                            if(Math.Min(center-Gray(cross-3),center-Gray(cross+3))>=4) count++;
                        }
                        ridge=Math.Max(ridge,count/11d);
                    }
                    if(ridge>=.8) state="unknown";
                }
                states[name]=state;
                evidence[name]=new JsonObject{["interior"]=interior,["exterior"]=exterior,["wallSupport"]=support,["internalWallSupport"]=ridge};
            }
        }
    }

    private static double Lin(double start,double end,int i)=>i==10 ? end : start+(end-start)/10*i;
    private static bool Rays(Corner n,Point2d a,Point2d b) => Numerics.Norm(n.Rays[0]-a)<=.09 && Numerics.Norm(n.Rays[1]-b)<=.09;
    private static List<(Point2d Center,double Unit)> Anchors(List<Corner> nodes)
    {
        var result=new List<(Point2d,double)>();
        foreach(var left in nodes.Where(n=>Rays(n,new(0,-1),new(1,0))))
            foreach(var right in nodes.Where(n=>Rays(n,new(-1,0),new(0,-1))))
            {
                double width=right.Point.X-left.Point.X,unit=width/1.5;
                if(unit<16 || Math.Abs(right.Point.Y-left.Point.Y)>2 || Math.Max(left.Lengths[1],right.Lengths[0])>width+2 ||
                    left.Lengths[1]+right.Lengths[0]<width*.8 || Math.Max(Math.Abs(left.Lengths[0]/unit-1),Math.Abs(right.Lengths[1]/unit-1))>.12) continue;
                bool Shoulder(Point2d point,Point2d a,Point2d b)=>nodes.Any(n=>Numerics.Norm(n.Point-point)<=2 &&
                    Rays(n,a,b) && n.Lengths.Min()>=unit*.4);
                if(Shoulder(left.Point+left.Rays[0]*left.Lengths[0],new(-1,0),new(0,1)) &&
                   Shoulder(right.Point+right.Rays[1]*right.Lengths[1],new(0,1),new(1,0)))
                    result.Add(((left.Point+right.Point)*.5,unit));
            }
        return result;
    }
}
