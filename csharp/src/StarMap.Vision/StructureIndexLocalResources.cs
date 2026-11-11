using System.Text.Json;
using OpenCvSharp;
using StarMap.Core.Resources;

namespace StarMap.Vision;

public sealed partial class StructureIndexFilter
{
    private readonly Dictionary<string,LocalField> _localFields=new(StringComparer.Ordinal);
    private readonly LinkedList<string> _localFieldOrder=new();

    private PoolData? LocalPool(string[] ids,string floor,Func<bool> cancelled)
    {
        string key=_catalog.ResourceKey+"\n"+floor+"\n"+string.Join('\n',ids);
        if(_cache.TryGetValue(key,out var hit)){Touch(_cacheOrder,key);return cancelled()?null:hit;}
        var sources=new List<LocalSource>();var chains=new List<double[][]>();var rays=new List<double[][]>();
        var seen=new HashSet<(double,double,double,double,double,double)>();
        foreach(string id in ids)
        {
            if(cancelled())return null;
            LocalSource Unknown(string reason)=>new(id,null,0,0,[],reason);
            if(_s0Markers is null){sources.Add(Unknown("coordinate-markers-unavailable"));continue;}
            try
            {
                var entry=_catalog.ReferenceFor(_resources,id,floor);
                if(entry is null){sources.Add(Unknown("coordinate-reference-missing"));continue;}
                var reference=_loadReference(entry).Value;
                if(reference?.CleanMask is not { } clean){sources.Add(Unknown("coordinate-reference-missing"));continue;}
                var markers=_s0Markers.Resolve(entry,reference);
                if(!markers.CoverageComplete){sources.Add(Unknown(markers.Reason));continue;}
                var space=CommonFloorSpace.Read(entry,reference,out _);
                if(space is null||space.ReferenceToCommon.M00!=128d/60||space.ReferenceToCommon.M11!=128d/60
                    ||space.ReferenceToCommon.M01!=0||space.ReferenceToCommon.M10!=0)
                {sources.Add(Unknown("coordinate-reference-units-unavailable"));continue;}
                if(markers.Points.Any(p=>p.X!=Math.Round(p.X)||p.Y!=Math.Round(p.Y)||Math.Abs(p.X)>int.MaxValue||Math.Abs(p.Y)>int.MaxValue))
                {sources.Add(Unknown("coordinate-noninteger-marker-unavailable"));continue;}
                var doors=markers.Points.Select(p=>new Point((int)p.X,(int)p.Y)).Distinct().ToArray();
                var source=new LocalSource(id,entry,clean.Shape[1],clean.Shape[0],doors);
                var nativeChains=new List<(double[][] Chain,double[][] Rays)>();
                using var mask=Geometry.FromBytes(source.Width,source.Height,clean.Bytes.ToArray());
                if(Cv2.CountNonZero(mask)==0){sources.Add(Unknown("coordinate-reference-empty"));continue;}
                using var halo=new Mat();Cv2.CopyMakeBorder(mask,halo,1,1,1,1,BorderTypes.Constant,Scalar.All(0));
                var nodes=Geometry.Contour(halo);
                foreach(var door in doors)
                for(int i=0;i<nodes.Count;i++)
                {
                    if(cancelled())return null;
                    var node=nodes[i];int[] vertices=[node.Previous,i,node.Next];
                    if(!vertices.All(j=>nodes[j].Valid))continue;
                    var chain=vertices.Select(j=>new[]{nodes[j].Point.X-1-door.X,nodes[j].Point.Y-1-door.Y}).ToArray();
                    nativeChains.Add((chain,node.Rays.Select(p=>new[]{p.X,p.Y}).ToArray()));
                }
                sources.Add(source);
                foreach(var pair in nativeChains)
                {
                    var chain=pair.Chain;
                    if(!seen.Add((chain[0][0],chain[0][1],chain[1][0],chain[1][1],chain[2][0],chain[2][1])))continue;
                    chains.Add(chain);rays.Add(pair.Rays);
                }
            }
            catch(Exception error)when(error is IOException or UnauthorizedAccessException or InvalidDataException or JsonException
                or ArgumentException or InvalidOperationException or KeyNotFoundException or OverflowException)
            {sources.Add(Unknown("coordinate-reference-unavailable"));}
        }
        if(cancelled())return null;
        var known=sources.Where(source=>source.UnknownReason is null).ToArray();
        int x=known.Length==0?0:known.Min(source=>source.Doors.Min(door=>-door.X));
        int y=known.Length==0?0:known.Min(source=>source.Doors.Min(door=>-door.Y));
        int width=known.Length==0?0:checked(known.Max(source=>source.Doors.Max(door=>source.Width-door.X))-x);
        int height=known.Length==0?0:checked(known.Max(source=>source.Doors.Max(door=>source.Height-door.Y))-y);
        var data=new PoolData(ids,floor,new(x,y),width,height,null,[],chains.ToArray(),rays.ToArray(),[],sources.ToArray());
        while(_cache.Count>=3&&_cacheOrder.First is { } oldest){_cache.Remove(oldest.Value);_cacheOrder.RemoveFirst();}
        _cache.Add(key,data);Touch(_cacheOrder,key);return cancelled()?null:data;
    }

    private LocalField? AcquireLocalField(LocalSource source)
    {
        if(source.Entry is not { } entry)return null;
        string key=entry.StableKey;
        if(_localFields.TryGetValue(key,out var hit)){Touch(_localFieldOrder,key);return hit;}
        var reference=_loadReference(entry).Value;
        if(reference?.CleanMask is not { } clean||clean.Shape[0]!=source.Height||clean.Shape[1]!=source.Width)return null;
        var field=PrepareLocalField(source.Width,source.Height,clean.Bytes.ToArray());
        while(_localFields.Count>=4&&_localFieldOrder.First is { } oldest){_localFields.Remove(oldest.Value);_localFieldOrder.RemoveFirst();}
        _localFields.Add(key,field);Touch(_localFieldOrder,key);return field;
    }
}
