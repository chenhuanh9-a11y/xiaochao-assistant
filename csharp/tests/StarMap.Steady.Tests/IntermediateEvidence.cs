using System.Buffers.Binary;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using OpenCvSharp;
using StarMap.Core.Resources;
using StarMap.Vision.Steady;

internal static class IntermediateEvidence
{
    sealed record Npy(string Dtype,int[] Shape,byte[] Data);
    static string Text(JsonNode row,string key)=>row[key]?.GetValue<string>()??throw new InvalidDataException("Missing "+key);
    static JsonArray Shape(params int[] shape)=>new(shape.Select(v=>(JsonNode?)JsonValue.Create(v)).ToArray());

    internal static JsonObject Prepared(SharedStructureData reference,JsonObject expected,string output)
    {
        var actual=Idvb0059Steady.InspectPrepared(reference);var arrays=new JsonArray();
        arrays.Add(Check("edge",Text(expected,"edge"),new("|u1",[actual.Height,actual.Width],actual.Edge),output));
        arrays.Add(Check("k3",Text(expected,"k3"),new("|u1",[actual.Height,actual.Width],actual.K3),output));
        arrays.Add(Check("k5",Text(expected,"k5"),new("|u1",[actual.Height,actual.Width],actual.K5),output));
        arrays.Add(Check("distance",Text(expected,"distance"),new("<f4",[actual.Height,actual.Width],FloatBytes(actual.Distance)),output));
        arrays.Add(Check("reference_points",Text(expected,"reference_points"),Points(actual.ReferencePoints),output));
        double pitch=expected["pitch"]!.GetValue<double>(),ratio=expected["pitchRatio"]!.GetValue<double>();
        bool shape=JsonNode.DeepEquals(expected["shape"],Shape(actual.Height,actual.Width));
        return new JsonObject { ["passed"]=shape&&arrays.All(row=>row!["bitwiseEqual"]!.GetValue<bool>()),["shapeMatches"]=shape,
            ["arrays"]=arrays,["pitch"]=new JsonObject { ["expected"]=pitch,["actual"]=actual.Pitch,["difference"]=actual.Pitch-pitch,
                ["bitwiseEqual"]=BitConverter.DoubleToInt64Bits(pitch)==BitConverter.DoubleToInt64Bits(actual.Pitch) },
            ["pitchRatio"]=new JsonObject { ["expected"]=ratio,["actual"]=actual.PitchRatio,["difference"]=actual.PitchRatio-ratio,
                ["bitwiseEqual"]=BitConverter.DoubleToInt64Bits(ratio)==BitConverter.DoubleToInt64Bits(actual.PitchRatio) },
            ["scope"]="Original np.save arrays vs managed copies; byte/point/distance arrays require exact payload bits. Pitch/ratio report numeric delta separately." };
    }

    internal static JsonObject Observation(Mat frame,JsonObject expected,string output)
    {
        var actual=Idvb0059Steady.InspectObservation(frame);var arrays=new JsonArray();
        arrays.Add(Check("points",Text(expected,"points"),Points(actual.Points),output));
        arrays.Add(Check("observed_edges",Text(expected,"observed_edges"),new("|u1",[actual.Height,actual.Width],actual.Observed),output));
        arrays.Add(Check("proposal_edges",Text(expected,"proposal_edges"),new("|u1",[actual.Height,actual.Width],actual.Proposal),output));
        arrays.Add(Check("valid_mask",Text(expected,"valid_mask"),new("|u1",[actual.Height,actual.Width],actual.Valid),output));
        bool shape=JsonNode.DeepEquals(expected["shape"],Shape(actual.Height,actual.Width));
        bool origin=JsonNode.DeepEquals(expected["origin"],Shape(actual.Origin.X,actual.Origin.Y));
        return new JsonObject { ["passed"]=shape&&origin&&arrays.All(row=>row!["bitwiseEqual"]!.GetValue<bool>()),["shapeMatches"]=shape,
            ["originMatches"]=origin,["actualShape"]=Shape(actual.Height,actual.Width),["actualOrigin"]=Shape(actual.Origin.X,actual.Origin.Y),["arrays"]=arrays,
            ["scope"]="Separate same-frame extraction for intermediate evidence only; solver's sameFrameExtractionCount remains its own extraction count." };
    }

    static Npy Points(Point[] points)
    {
        var bytes=new byte[checked(points.Length*8)];
        for(int i=0;i<points.Length;i++){BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(8*i,4),points[i].X);BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(8*i+4,4),points[i].Y);}
        return new("<i4",[points.Length,2],bytes);
    }
    static byte[] FloatBytes(float[] values)
    {
        var bytes=new byte[checked(values.Length*4)];
        for(int i=0;i<values.Length;i++)BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(i*4,4),BitConverter.SingleToInt32Bits(values[i]));
        return bytes;
    }
    static JsonObject Check(string name,string path,Npy actual,string output)
    {
        var expected=Read(path);string written=Path.Combine(output,"actual-"+name+".npy");Write(written,actual);
        bool shape=expected.Shape.SequenceEqual(actual.Shape),dtype=expected.Dtype==actual.Dtype;
        int itemSize=actual.Dtype=="|u1"?1:4;
        int mismatches=0,first=-1;double maximum=0;
        int n=Math.Min(expected.Data.Length,actual.Data.Length)/itemSize;
        for(int i=0;i<n;i++)
        {
            var e=expected.Data.AsSpan(i*itemSize,itemSize);var a=actual.Data.AsSpan(i*itemSize,itemSize);
            if(e.SequenceEqual(a))continue;mismatches++;if(first<0)first=i;
            double delta=actual.Dtype=="<f4"?Math.Abs(BitConverter.Int32BitsToSingle(BinaryPrimitives.ReadInt32LittleEndian(e))-BitConverter.Int32BitsToSingle(BinaryPrimitives.ReadInt32LittleEndian(a))):
                actual.Dtype=="<i4"?Math.Abs((double)BinaryPrimitives.ReadInt32LittleEndian(e)-BinaryPrimitives.ReadInt32LittleEndian(a)):Math.Abs(e[0]-a[0]);
            maximum=Math.Max(maximum,delta);
        }
        bool exact=shape&&dtype&&expected.Data.Length==actual.Data.Length&&mismatches==0;
        return new JsonObject { ["name"]=name,["source"]=path,["actual"]=written,["bitwiseEqual"]=exact,["shapeMatches"]=shape,["dtypeMatches"]=dtype,
            ["expectedShape"]=Shape(expected.Shape),["actualShape"]=Shape(actual.Shape),["mismatchedElements"]=mismatches,["firstMismatchElement"]=first,
            ["maximumAbsoluteDifference"]=maximum,["expectedBytes"]=expected.Data.Length,["actualBytes"]=actual.Data.Length };
    }

    // Strict subset of the unmodified np.save oracle files, bounded and read-only. No Python is launched.
    static Npy Read(string path)
    {
        using var stream=File.OpenRead(path);if(stream.Length>512L*1024*1024)throw new InvalidDataException("NPY evidence too large");
        Span<byte> prefix=stackalloc byte[8];stream.ReadExactly(prefix);
        if(!prefix[..6].SequenceEqual(new byte[] {0x93,(byte)'N',(byte)'U',(byte)'M',(byte)'P',(byte)'Y'}))throw new InvalidDataException("NPY magic");
        int headerLength;
        if(prefix[6]==1){Span<byte> length=stackalloc byte[2];stream.ReadExactly(length);headerLength=BinaryPrimitives.ReadUInt16LittleEndian(length);}
        else if(prefix[6] is 2 or 3){Span<byte> length=stackalloc byte[4];stream.ReadExactly(length);headerLength=checked((int)BinaryPrimitives.ReadUInt32LittleEndian(length));}
        else throw new InvalidDataException("NPY version");
        if(headerLength<=0||headerLength>1024*1024)throw new InvalidDataException("NPY header length");
        var headerBytes=new byte[headerLength];stream.ReadExactly(headerBytes);string header=Encoding.UTF8.GetString(headerBytes);
        string dtype=Regex.Match(header,"['\"]descr['\"]\\s*:\\s*['\"](?<type>[^'\"]+)['\"]").Groups["type"].Value;
        if(dtype is not ("|u1" or "<i4" or "<f4")||!Regex.IsMatch(header,"['\"]fortran_order['\"]\\s*:\\s*False"))throw new InvalidDataException("NPY evidence dtype/order");
        string rawShape=Regex.Match(header,"['\"]shape['\"]\\s*:\\s*\\((?<shape>[^)]*)\\)").Groups["shape"].Value;
        int[] shape=rawShape.Split(',',StringSplitOptions.RemoveEmptyEntries|StringSplitOptions.TrimEntries).Select(int.Parse).ToArray();
        if(shape.Length is <1 or >2||shape.Any(v=>v<0||v>100_000_000))throw new InvalidDataException("NPY evidence shape");
        long count=1;foreach(int dimension in shape)count=checked(count*dimension);
        long bytes=checked(count*(dtype=="|u1"?1:4));if(bytes>512L*1024*1024||bytes!=stream.Length-stream.Position)throw new InvalidDataException("NPY payload length");
        var payload=new byte[(int)bytes];stream.ReadExactly(payload);return new(dtype,shape,payload);
    }
    static void Write(string path,Npy value)
    {
        string dimensions=string.Join(", ",value.Shape)+(value.Shape.Length==1?",":"");
        string header=$"{{'descr': '{value.Dtype}', 'fortran_order': False, 'shape': ({dimensions}), }}";
        int padding=(16-(10+Encoding.ASCII.GetByteCount(header)+1)%16)%16;header+=new string(' ',padding)+"\n";
        using var stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read);
        stream.Write(new byte[] {0x93,(byte)'N',(byte)'U',(byte)'M',(byte)'P',(byte)'Y',1,0});
        Span<byte> size=stackalloc byte[2];BinaryPrimitives.WriteUInt16LittleEndian(size,checked((ushort)header.Length));stream.Write(size);
        stream.Write(Encoding.ASCII.GetBytes(header));stream.Write(value.Data);
    }
}
