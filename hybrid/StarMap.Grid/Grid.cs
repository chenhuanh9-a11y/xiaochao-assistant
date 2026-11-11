using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;

// Candidate-grid numerical kernel carried from the historical protocol-2
// experiment. Python still owns refinement, final pose, validation and
// acceptance. This service only proposes candidates.
static class Grid
{
    public static (object[] Rows, Dictionary<string, double> Stages) Search(
        JsonNode doc,
        ushort[] field,
        Func<bool>? shouldCancel = null,
        Action<string, int>? progress = null)
    {
        var clock = Stopwatch.StartNew();
        double previous = 0;
        var stages = new Dictionary<string, double>();

        void ObserveCancellation()
        {
            if (shouldCancel?.Invoke() == true)
                throw new OperationCanceledException("Grid search cancelled.");
        }

        void Stage(string name)
        {
            double now = clock.Elapsed.TotalMilliseconds;
            stages[name] = now - previous;
            previous = now;
        }

        ObserveCancellation();
        float[][] points = doc["points"]!.Deserialize<float[][]>()!;
        double scale = doc["scale"]!.GetValue<double>();
        int width = doc["width"]!.GetValue<int>(), height = doc["height"]!.GetValue<int>();
        bool cancelled = doc["cancelled"]!.GetValue<bool>();
        if (points.Length == 0)
            throw new IndexOutOfRangeException("Production _grid requires nonempty points.");

        ObserveCancellation();
        double[] tx, ty;
        if (doc["prior"] is not null)
        {
            var prior = doc["prior"]!.Deserialize<double[][]>()!;
            tx = Arange(-32, 32.1, 4).Select(v => prior[0][2] + v).ToArray();
            ty = Arange(-32, 32.1, 4).Select(v => prior[1][2] + v).ToArray();
        }
        else
        {
            var xs = points.Select(p => (float)(p[0] / (float)scale)).OrderBy(v => v).ToArray();
            var ys = points.Select(p => (float)(p[1] / (float)scale)).OrderBy(v => v).ToArray();
            float mx = Median(xs), my = Median(ys);
            double stride = Math.Max(3, 5 / scale);
            var ax = Arange(-mx, width - (double)mx + stride, stride, true);
            var ay = Arange(-my, height - (double)my + stride, stride, true);
            if ((long)ax.Length * ay.Length > 350000)
                return ([], stages);
            tx = ax.Select(v => -scale * v).ToArray();
            ty = ay.Select(v => -scale * v).ToArray();
        }

        Stage("axisGenerationMs");
        ObserveCancellation();
        int count = points.Length, nx = tx.Length, ny = ty.Length;
        float scale32 = (float)scale;
        int[][] xx = Enumerable.Range(0, count).Select(_ => new int[nx]).ToArray();
        int[][] yy = Enumerable.Range(0, count).Select(_ => new int[ny]).ToArray();
        for (int p = 0; p < count; p++)
        {
            ObserveCancellation();
            for (int x = 0; x < nx; x++)
                xx[p][x] = (int)Math.Round((points[p][0] - tx[x]) / scale32, MidpointRounding.ToEven);
            for (int y = 0; y < ny; y++)
                yy[p][y] = (int)Math.Round((points[p][1] - ty[y]) / scale32, MidpointRounding.ToEven);
        }

        int limit3 = -1, limit6 = -1;
        for (int i = 0; i < 65536; i++)
        {
            if ((i & 1023) == 0) ObserveCancellation();
            float distance = (i * scale32) * .25f;
            if (distance <= 3) limit3 = i;
            if (distance <= 6) limit6 = i;
        }

        Stage("quantizationMs");
        uint[] totals = new uint[nx * ny];
        for (int p = 0; p < count; p++)
        {
            ObserveCancellation();
            if (cancelled)
                return ([], stages);
            for (int y = 0; y < ny; y++)
            {
                if ((y & 31) == 0) ObserveCancellation();
                int fy = yy[p][y];
                if (fy < 0 || fy >= height) continue;
                int row = y * nx, fieldRow = fy * width;
                for (int x = 0; x < nx; x++)
                {
                    int fx = xx[p][x];
                    if (fx < 0 || fx >= width) continue;
                    ushort distance = field[fieldRow + fx];
                    totals[row + x] += (uint)((distance <= limit3 ? 2 : 0) + (distance <= limit6 ? 1 : 0));
                }
            }
            if (p == 0) progress?.Invoke("allPoseScores", 1);
            ObserveCancellation();
        }

        ObserveCancellation();
        float[] values = totals.Select(v => (float)(v / (3.0 * count))).ToArray();
        Stage("allPoseScoresMs");
        var rows = new List<object>();
        for (int k = 0; k < 8; k++)
        {
            ObserveCancellation();
            int best = 0;
            for (int i = 1; i < values.Length; i++)
                if (values[i] > values[best]) best = i;
            if (values[best] < 0) break;
            double bx = tx[best % nx], by = ty[best / nx];
            rows.Add(new object[] { (double)values[best], scale, new double[] { bx, by } });
            for (int y = 0; y < ny; y++)
                for (int x = 0; x < nx; x++)
                {
                    double dx = tx[x] - bx, dy = ty[y] - by;
                    if (dx * dx + dy * dy < 144) values[y * nx + x] = -1;
                }
        }

        Stage("basinSuppressionMs");
        ObserveCancellation();
        return (rows.ToArray(), stages);
    }

    static float Median(float[] sorted)
    {
        int n = sorted.Length;
        if (n % 2 == 1) return sorted[n / 2];

        // np.quantile(..., .5) uses NumPy's float32 linear interpolation:
        // for gamma == .5, _lerp takes the b - (b-a)*(1-gamma) branch.
        // The algebraically equivalent (a+b)*.5 rounds differently for some
        // float32 pairs and shifts the global candidate grid.
        float a = sorted[n / 2 - 1], b = sorted[n / 2];
        return b - (b - a) * .5f;
    }

    static double[] Arange(double start, double stop, double step, bool startWasFloat32 = false)
    {
        int n = Math.Max(0, (int)Math.Ceiling((stop - start) / step));
        // NumPy's arange output is double here, but its delta starts from the
        // float32 median used to derive the global search bounds.
        double actualStep = startWasFloat32
            ? (float)((float)((float)start + (float)step) - (float)start)
            : (start + step) - start;
        return Enumerable.Range(0, n)
            .Select(i => i == 0 ? start : start + i * actualStep)
            .ToArray();
    }
}
