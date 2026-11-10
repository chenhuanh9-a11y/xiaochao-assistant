using System.Text.Json;
using System.Text.Json.Nodes;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Core.Selection;

namespace StarMap.Vision;

public sealed partial class StructureIndexFilter : IDisposable
{
    private readonly ResourceCatalog _resources;
    private readonly StructureCatalog _catalog;
    private readonly JsonElement _index;
    private readonly JsonElement _entries;
    private readonly StructureGateAnchorLocator _locator;
    private readonly object _sync = new();
    private readonly Dictionary<string, PoolData> _cache = new(StringComparer.Ordinal);
    private readonly Dictionary<string, PoolJob> _pending = new(StringComparer.Ordinal);
    private readonly LinkedList<string> _cacheOrder = new();
    private readonly LinkedList<string> _pendingOrder = new();
    private bool _disposed;

    public StructureIndexFilter(ResourceCatalog resources, StructureCatalog catalog, string assetDirectory)
    {
        if (!catalog.IsBoundTo(resources)) throw new ArgumentException("structure-index-catalog-owner-mismatch", nameof(catalog));
        _resources = resources; _catalog = catalog;
        using var document = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(assetDirectory, "s1_entrance_coordinate_index.json")));
        _index = document.RootElement.Clone();
        if (_index.ValueKind != JsonValueKind.Object || !_index.TryGetProperty("schema", out var schema)
            || schema.ValueKind != JsonValueKind.String || schema.GetString() != "s1-common-entrance-coordinate-index-v1")
            throw new InvalidDataException("coordinate-index-schema");
        if (!_index.TryGetProperty("maps", out var maps) || maps.ValueKind != JsonValueKind.Object
            || !maps.TryGetProperty(catalog.Mode, out _entries) || _entries.ValueKind != JsonValueKind.Object
            || catalog.MapIds.Any(id => !_entries.TryGetProperty(id, out _)))
            throw new InvalidDataException("coordinate-index-catalog-mismatch");
        if (!_index.TryGetProperty("glyphTemplateSha256", out var glyph) || glyph.ValueKind != JsonValueKind.String)
            throw new InvalidDataException("coordinate-index-glyph-missing");
        _locator = new StructureGateAnchorLocator(assetDirectory);
        if (glyph.GetString() != _locator.TemplateSha256)
        {
            _locator.Dispose(); throw new InvalidDataException("coordinate-index-glyph-mismatch");
        }
    }

    public StructureGateAnchor DetectAnchor(Mat frame, Func<bool>? cancelled = null)
    {
        lock (_sync) { ObjectDisposedException.ThrowIf(_disposed, this); return _locator.Detect(frame, cancelled); }
    }

    public StructureGateAnchor DetectAnchor(StructureFramePreparation preparation,Func<bool>? cancelled=null) =>
        DetectAnchor(preparation.Frame,cancelled);

    /// <summary>Native clean-mask pixels to client pixels for exactly the report's common feasible pose.
    /// The caller must still validate this matrix against that same frame before committing a pose.</summary>
    public double[][]? ReferenceToClient(JsonObject report, string mapId, string floor)
    {
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (report["coordinateVerified"]?.GetValue<bool>() != true || report["floor"]?.GetValue<string>() != floor
                || report["mode"]?.GetValue<string>() != _catalog.Mode || report["resourceKey"]?.GetValue<string>() != _catalog.ResourceKey
                || report["evaluatedMapIds"] is not JsonArray ids || !ids.Any(v => v?.GetValue<string>() == mapId)
                || !_entries.TryGetProperty(mapId, out var map) || !map.TryGetProperty(floor, out var entry)
                || report["matrixEntranceRendererToClient"] is not JsonArray matrix) return null;
            var common = Numerics.Matrix(matrix);
            var image = ReadMatrix(entry.GetProperty("imageToEntranceXZ"));
            // xzToRenderer @ imageToEntranceXZ has positive pixel Y.
            double tx = image[0][2], ty = -image[1][2];
            return [[common[0][0], common[0][1], common[0][0]*tx+common[0][1]*ty+common[0][2]],
                [common[1][0], common[1][1], common[1][0]*tx+common[1][1]*ty+common[1][2]], [0,0,1]];
        }
    }

    private PoolData? Pool(string[] ids, string floor, Func<bool> cancelled)
    {
        string key = _catalog.ResourceKey + "\n" + floor + "\n" + string.Join('\n', ids);
        if (_cache.TryGetValue(key, out var cached))
        {
            Touch(_cacheOrder, key); return cancelled() ? null : cached;
        }
        if (cancelled()) return null;
        if (!_pending.TryGetValue(key, out var job))
        {
            while (_pending.Count + _cache.Count >= 3)
            {
                if (_pendingOrder.First is { } partial) { _pending.Remove(partial.Value); _pendingOrder.RemoveFirst(); }
                else if (_cacheOrder.First is { } complete) { _cache.Remove(complete.Value); _cacheOrder.RemoveFirst(); }
            }
            job = new PoolJob(); _pending.Add(key, job);
        }
        Touch(_pendingOrder, key);
        for (int i = job.Items.Count; i < ids.Length; i++)
        {
            if (cancelled()) return null;
            string id = ids[i]; var entry = _entries.GetProperty(id).GetProperty(floor);
            var catalogEntry = _resources.Find(_catalog.Mode, _catalog.Season, "shared-structure", id, floor);
            if (catalogEntry is null) throw new InvalidDataException("coordinate-reference-missing:" + id);
            var reference = _resources.LoadSharedStructure(catalogEntry).Value;
            if (reference?.CleanMask is not { } clean) throw new InvalidDataException("coordinate-reference-missing:" + id);
            var meta = reference.Metadata;
            var geometry = meta.GetProperty("sourceGeometry");
            var declaredShape = entry.GetProperty("shapeHW").EnumerateArray().Select(n => n.GetInt32()).ToArray();
            if (meta.GetProperty("sourceVersion").GetString() != _index.GetProperty("sourceVersion").GetString()
                || geometry.GetProperty("sourceCatalogSha256").GetString() != entry.GetProperty("sourceCatalogSha256").GetString()
                || geometry.GetProperty("rendererMetadataSha256").GetString() != entry.GetProperty("sourceRendererMetadataSha256").GetString()
                || !clean.Shape.SequenceEqual(declaredShape)) throw new InvalidDataException("coordinate-reference-binding-mismatch:" + id);
            var matrix = ReadMatrix(entry.GetProperty("imageToEntranceXZ"));
            if (matrix[0][0] != 1 || matrix[0][1] != 0 || matrix[1][0] != 0 || matrix[1][1] != -1
                || matrix[2][0] != 0 || matrix[2][1] != 0 || matrix[2][2] != 1)
                throw new InvalidDataException("coordinate-index-axes");
            var origin = new Point2d(matrix[0][2], -matrix[1][2]);
            if (!double.IsFinite(origin.X) || !double.IsFinite(origin.Y) || origin.X != Math.Round(origin.X) || origin.Y != Math.Round(origin.Y))
                throw new InvalidDataException("noninteger-native-origin");
            job.Items.Add(new(id, clean.Shape[1], clean.Shape[0], clean.Bytes.Span.ToArray().Select(b => b > 0 ? (byte)1 : (byte)0).ToArray(), origin));
        }
        if (cancelled()) return null;
        if (job.Width == 0)
        {
            job.Origin = new(job.Items.Min(i => i.Origin.X), job.Items.Min(i => i.Origin.Y));
            job.Width = checked((int)(job.Items.Max(i => i.Origin.X + i.Width) - job.Origin.X));
            job.Height = checked((int)(job.Items.Max(i => i.Origin.Y + i.Height) - job.Origin.Y));
        }
        for (int i = job.Fields.Count; i < job.Items.Count; i++)
        {
            if (cancelled()) return null;
            var item = job.Items[i];
            using var canvas = new Mat(job.Height, job.Width, MatType.CV_8UC1, Scalar.All(0));
            using (var mask = Geometry.FromBytes(item.Width, item.Height, item.Mask))
            using (var region = new Mat(canvas, new Rect((int)(item.Origin.X-job.Origin.X), (int)(item.Origin.Y-job.Origin.Y), item.Width,item.Height))) mask.CopyTo(region);
            using var edge = new Mat(); using var kernel = Geometry.Kernel(3);
            Cv2.MorphologyEx(canvas, edge, MorphTypes.Gradient, kernel);
            using var inverse = new Mat(); Cv2.Threshold(edge, inverse, 0, 1, ThresholdTypes.BinaryInv);
            using var field = new Mat(); Cv2.DistanceTransform(inverse, field, DistanceTypes.L2, DistanceTransformMasks.Mask3);
            // Commit each immutable layer atomically; retain completed layers on cancellation.
            job.Masks.Add(Geometry.Bytes(canvas)); job.Edges.Add(Geometry.Bytes(edge)); job.Fields.Add(Geometry.Floats(field));
        }
        if (cancelled()) return null;
        if (job.Common is null)
        {
            job.Common = (byte[])job.Masks[0].Clone(); job.Shared = (byte[])job.Edges[0].Clone(); job.Union = (byte[])job.Masks[0].Clone();
        }
        while (job.MergeIndex < job.Masks.Count)
        {
            if (cancelled()) return null;
            int i = job.MergeIndex;
            for (int p = 0; p < job.Common.Length; p++)
            {
                job.Common[p] = Math.Min(job.Common[p],job.Masks[i][p]); job.Shared![p] = Math.Min(job.Shared[p],job.Edges[i][p]);
                job.Union![p] = Math.Max(job.Union[p],job.Masks[i][p]);
            }
            job.MergeIndex++;
        }
        if (cancelled()) return null;
        if (job.Prepared is null)
        {
            using var common = Geometry.FromBytes(job.Width,job.Height,job.Common);
            job.Prepared = FastStructure.Prepare(common);
        }
        if (cancelled()) return null;
        if (job.Near is null)
        {
            using var shared = Geometry.FromBytes(job.Width,job.Height,job.Shared!); using var near = new Mat(); using var kernel = Geometry.Kernel(3);
            Cv2.Dilate(shared,near,kernel); job.Near = Geometry.Bytes(near);
        }
        while (job.ChainIndex < job.Prepared.Chains.Length)
        {
            if (cancelled()) return null;
            int i = job.ChainIndex; var chain = job.Prepared.Chains[i]; bool good = true;
            foreach (int endpoint in new[] { 0,2 })
            {
                double dx = chain[endpoint][0]-chain[1][0], dy = chain[endpoint][1]-chain[1][1];
                int n = (int)Math.Ceiling(Math.Sqrt(dx*dx+dy*dy));
                foreach (double t in Linspace(0,1,n+1))
                {
                    int x = Numerics.Rint(chain[1][0]+t*dx), y = Numerics.Rint(chain[1][1]+t*dy);
                    if (job.Near[y*job.Width+x] == 0) { good = false; break; }
                }
                if (!good) break;
            }
            if (good) job.Keep.Add(i); job.ChainIndex++;
        }
        if (cancelled()) return null;
        if (job.Distance is null)
        {
            using var shared = Geometry.FromBytes(job.Width,job.Height,job.Shared!); using var inverse = new Mat();
            Cv2.Threshold(shared,inverse,0,1,ThresholdTypes.BinaryInv); using var field = new Mat();
            Cv2.DistanceTransform(inverse,field,DistanceTypes.L2,DistanceTransformMasks.Mask3);
            job.Distance = Geometry.Floats(field).Select(v => (ushort)Math.Round(Math.Min(v,16383.75)*4,MidpointRounding.ToEven)).ToArray();
        }
        if (floor == "-1")
        {
            while (job.UnionMap < job.Masks.Count)
            {
                if (cancelled()) return null;
                if (job.UnionNodes is null)
                {
                    using var mask = Geometry.FromBytes(job.Width,job.Height,job.Masks[job.UnionMap]);
                    job.UnionNodes = Geometry.Contour(mask); job.UnionNode = 0;
                }
                while (job.UnionNode < job.UnionNodes.Count)
                {
                    if (cancelled()) return null;
                    int i = job.UnionNode; var node = job.UnionNodes[i]; int[] indices = [node.Previous,i,node.Next];
                    if (indices.All(j => job.UnionNodes[j].Valid))
                    {
                        var chain = indices.Select(j => new[] { job.UnionNodes[j].Point.X+job.Origin.X,job.UnionNodes[j].Point.Y+job.Origin.Y }).ToArray();
                        // Source contour vertices are exact integer coordinates, so structural equality is exact here.
                        var chainKey = (chain[0][0],chain[0][1],chain[1][0],chain[1][1],chain[2][0],chain[2][1]);
                        if (job.UnionSeen.Add(chainKey)) { job.UnionChains.Add(chain); job.UnionRays.Add(node.Rays.Select(p => new[]{p.X,p.Y}).ToArray()); }
                    }
                    job.UnionNode++;
                }
                job.UnionNodes = null; job.UnionMap++;
            }
        }
        if (cancelled()) return null;
        if (job.Difference is null)
        {
            var pixels = job.Union!.Select((b,i) => b != job.Common[i] ? (byte)1 : (byte)0).ToArray();
            using var difference = Geometry.FromBytes(job.Width,job.Height,pixels); using var kernel = Geometry.Kernel(31);
            Cv2.Dilate(difference,difference,kernel); job.Difference = Geometry.Bytes(difference);
        }
        var prepared = job.Prepared with { Distance = job.Distance, Chains = job.Keep.Select(i => job.Prepared.Chains[i]).ToArray(), Rays = job.Keep.Select(i => job.Prepared.Rays[i]).ToArray() };
        var data = new PoolData(ids,floor,job.Origin,job.Width,job.Height,prepared,job.Fields.ToArray(),job.UnionChains.ToArray(),job.UnionRays.ToArray(),job.Difference);
        _cache.Add(key,data); Touch(_cacheOrder,key); _pending.Remove(key); _pendingOrder.Remove(key);
        return cancelled() ? null : data;
    }

    private static double[][] ReadMatrix(JsonElement matrix)
    {
        var rows = matrix.EnumerateArray().Select(row => row.EnumerateArray().Select(n => n.GetDouble()).ToArray()).ToArray();
        if (rows.Length != 3 || rows.Any(row => row.Length != 3 || row.Any(v => !double.IsFinite(v)))) throw new InvalidDataException("coordinate-index-matrix-invalid");
        return rows;
    }
    private static void Touch(LinkedList<string> order, string key) { order.Remove(key); order.AddLast(key); }
    private sealed record PoolItem(string Id, int Width, int Height, byte[] Mask, Point2d Origin);
    private sealed record PoolData(string[] Ids,string Floor,Point2d Origin,int Width,int Height,FastPrepared Prepared,float[][] Fields,double[][][] UnionChains,double[][][] UnionRays,byte[] Difference);
    private sealed class PoolJob
    {
        public readonly List<PoolItem> Items = [];
        public readonly List<byte[]> Masks = [], Edges = [];
        public readonly List<float[]> Fields = [];
        public readonly List<int> Keep = [];
        public readonly List<double[][]> UnionChains = [], UnionRays = [];
        public readonly HashSet<(double,double,double,double,double,double)> UnionSeen = [];
        public int Width,Height,ChainIndex,UnionMap,UnionNode,MergeIndex = 1;
        public Point2d Origin;
        public byte[]? Common,Shared,Union,Near,Difference;
        public ushort[]? Distance;
        public FastPrepared? Prepared;
        public List<Corner>? UnionNodes;
    }

    public void Dispose()
    {
        lock (_sync)
        {
            if (_disposed) return; _disposed = true;
            _locator.Dispose(); _cache.Clear(); _pending.Clear(); _cacheOrder.Clear(); _pendingOrder.Clear();
        }
    }
}
