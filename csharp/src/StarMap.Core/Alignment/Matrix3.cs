using System.Collections;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Alignment;

/// <summary>Immutable finite full 3x3 matrix. No affine, determinant or axis restriction.</summary>
public sealed class Matrix3
{
    private readonly double[] values;
    public Matrix3(IEnumerable<IEnumerable<double>> rows)
    {
        var parsed=rows.Select(r=>r.ToArray()).ToArray();
        if(parsed.Length!=3||parsed.Any(r=>r.Length!=3)||parsed.SelectMany(r=>r).Any(v=>!double.IsFinite(v)))
            throw new ArgumentException("A finite 3x3 matrix is required.",nameof(rows));
        values=parsed.SelectMany(r=>r).ToArray();
    }
    public double this[int row,int column]=>values[checked(row*3+column)];
    public static Matrix3 Identity=>new(new[]{new[]{1d,0,0},new[]{0d,1,0},new[]{0d,0,1}});
    public double[][] ToArray()=>Enumerable.Range(0,3).Select(r=>values.AsSpan(r*3,3).ToArray()).ToArray();
    public static Matrix3? TryRead(JsonElement value)
    {
        try {return new(value.EnumerateArray().Select(r=>r.EnumerateArray().Select(n=>n.GetDouble())));}
        catch(Exception e) when(e is ArgumentException or InvalidOperationException or FormatException){return null;}
    }
    public static Matrix3? Multiply(Matrix3 a,Matrix3 b)
    {
        var rows=new double[3][];
        for(int r=0;r<3;r++) {rows[r]=new double[3];for(int c=0;c<3;c++)
            {double v=a[r,0]*b[0,c]+a[r,1]*b[1,c]+a[r,2]*b[2,c];if(!double.IsFinite(v))return null;rows[r][c]=v;}}
        return new(rows);
    }
    public Matrix3? Inverse()
    {
        var a=new double[3,6];
        for(int r=0;r<3;r++)for(int c=0;c<3;c++){a[r,c]=this[r,c];a[r,c+3]=r==c?1:0;}
        for(int column=0;column<3;column++)
        {
            int pivot=column;
            for(int r=column+1;r<3;r++)if(Math.Abs(a[r,column])>Math.Abs(a[pivot,column]))pivot=r;
            if(a[pivot,column]==0)return null;
            if(pivot!=column)for(int c=0;c<6;c++)(a[pivot,c],a[column,c])=(a[column,c],a[pivot,c]);
            double divisor=a[column,column];
            for(int c=0;c<6;c++)a[column,c]/=divisor;
            for(int r=0;r<3;r++)if(r!=column){double factor=a[r,column];for(int c=0;c<6;c++)a[r,c]-=factor*a[column,c];}
        }
        var rows=new double[3][];
        for(int r=0;r<3;r++){rows[r]=new double[3];for(int c=0;c<3;c++){double v=a[r,c+3];if(!double.IsFinite(v))return null;rows[r][c]=v;}}
        return new(rows);
    }
    public Matrix3 WithTranslation(double dx,double dy)
    {
        if(!double.IsFinite(dx)||!double.IsFinite(dy))throw new ArgumentException("translation must be finite");
        var a=ToArray();a[0][2]+=dx;a[1][2]+=dy;return new(a);
    }
    public bool LinearAllClose(Matrix3 b,double atol=1e-5,double rtol=1e-5)
    {for(int r=0;r<2;r++)for(int c=0;c<2;c++)if(Math.Abs(this[r,c]-b[r,c])>atol+rtol*Math.Abs(b[r,c]))return false;return true;}
    internal static double LinearNorm(Matrix3 a,Matrix3? b=null)
    {double sum=0;for(int r=0;r<2;r++)for(int c=0;c<2;c++){double d=a[r,c]-(b?[r,c]??0);sum+=d*d;}return Math.Sqrt(sum);}
}

/// <summary>Exact int/string cycle identity: numeric 1 and string "1" are different cycles.</summary>
public readonly record struct AlignmentCycleId
{
    public long? Number {get;}
    public string? Text {get;}
    public AlignmentCycleId(long value){Number=value;Text=null;}
    public AlignmentCycleId(string value){Text=value??throw new ArgumentNullException(nameof(value));Number=null;}
    public object Value=>Number is { } n?n:Text!;
    public static implicit operator AlignmentCycleId(int n)=>new((long)n);
    public static implicit operator AlignmentCycleId(long n)=>new(n);
    public static implicit operator AlignmentCycleId(string s)=>new(s);
    public override string ToString()=>Number?.ToString(CultureInfo.InvariantCulture)??Text??"";
}

/// <summary>
/// Evidence copies own dictionaries, arrays, JSON and nested Memory/ReadOnlyMemory/ArraySegment buffers.
/// Reference-free values and the immutable Matrix3/AlignmentCycleId are supported; other reference-containing structs fail explicitly.
/// Other serializable reference objects retain the existing owned JsonElement representation, never a live object reference.
/// </summary>
public static class AlignmentData
{
    private static readonly ConcurrentDictionary<Type,bool> ReferenceFreeTypes=new();
    private static readonly ConcurrentDictionary<Type,Func<object,object>> ValueCopiers=new();
    private static readonly ConcurrentDictionary<Type,Func<object,Array>> BufferReaders=new();
    public static object? Copy(object? value)=>value switch
    {
        null=>null,Matrix3 or AlignmentCycleId=>value,JsonElement e=>e.Clone(),JsonNode n=>n.DeepClone(),
        IReadOnlyDictionary<string,object?> d=>CopyMap(d),
        IDictionary d=>d.Cast<DictionaryEntry>().ToDictionary(p=>Convert.ToString(p.Key,CultureInfo.InvariantCulture)!,p=>Copy(p.Value)),
        Array a=>CopyArray(a),string=>value,ValueType=>ValueCopiers.GetOrAdd(value.GetType(),CreateValueCopier)(value),
        IEnumerable e=>e.Cast<object?>().Select(Copy).ToArray(),
        _=>JsonSerializer.SerializeToElement(value).Clone()
    };
    public static Dictionary<string,object?> CopyMap(IReadOnlyDictionary<string,object?>? source)=>
        source?.ToDictionary(p=>p.Key,p=>Copy(p.Value),StringComparer.Ordinal)??new(StringComparer.Ordinal);
    public static IReadOnlyDictionary<string,object?> ReadOnly(IReadOnlyDictionary<string,object?> source)=>new ReadOnlyDictionary<string,object?>(CopyMap(source));
    private static Array CopyArray(Array a)
    {
        var copy=(Array)a.Clone();
        var elementType=a.GetType().GetElementType()!;
        // Numeric/reference-free arrays keep one bulk clone. A value-type buffer can still contain mutable references.
        if(!CopyStableType(elementType))
        {
            void Visit(int dimension,int[] indices){for(int offset=0;offset<a.GetLength(dimension);offset++){indices[dimension]=a.GetLowerBound(dimension)+offset;
                if(dimension==a.Rank-1)
                {
                    var owned=Copy(a.GetValue(indices));
                    try{copy.SetValue(owned,indices);}
                    catch(Exception e)when(e is InvalidCastException or ArgumentException)
                    {throw new NotSupportedException($"Copied evidence cannot retain array element type {elementType}.",e);}
                }
                else Visit(dimension+1,indices);}}
            Visit(0,new int[a.Rank]);
        }
        return copy;
    }
    private static bool CopyStableType(Type type)=>type==typeof(string)||type==typeof(Matrix3)||type==typeof(AlignmentCycleId)
        ||(type.IsValueType&&ReferenceFreeTypes.GetOrAdd(type,static t=>
            (bool)typeof(AlignmentData).GetMethod(nameof(ReferenceFree),BindingFlags.NonPublic|BindingFlags.Static)!.MakeGenericMethod(t).Invoke(null,null)!));
    private static bool ReferenceFree<T>()=>!RuntimeHelpers.IsReferenceOrContainsReferences<T>();
    private static bool BufferType(Type type)=>type.IsGenericType&&(type.GetGenericTypeDefinition()==typeof(Memory<>)
        ||type.GetGenericTypeDefinition()==typeof(ReadOnlyMemory<>)||type.GetGenericTypeDefinition()==typeof(ArraySegment<>));
    private static Func<object,object> CreateValueCopier(Type type)
    {
        if(BufferType(type))return typeof(AlignmentData).GetMethod(nameof(CopyBuffer),BindingFlags.NonPublic|BindingFlags.Static)!
            .MakeGenericMethod(type.GetGenericArguments()[0]).CreateDelegate<Func<object,object>>();
        if(CopyStableType(type))return static value=>RuntimeHelpers.GetObjectValue(value)!;
        throw new NotSupportedException($"Evidence value type {type} contains references. Use supported dictionaries, arrays, JSON or typed memory buffers.");
    }
    // Reflection is restricted to these three known buffer shapes and the runtime's reference-layout query, cached by type.
    // Arbitrary structs are never traversed by reflection or silently retained as immutable.
    private static object CopyBuffer<T>(object value)
    {
        // Keep branch returns boxed independently: a switch expression can infer ReadOnlyMemory<T>
        // as the common type and apply implicit conversions to the writable/segment branches.
        switch(value)
        {
            case Memory<T> writable:return new Memory<T>(CopyBufferElements(writable.ToArray()));
            case ReadOnlyMemory<T> readOnly:return new ReadOnlyMemory<T>(CopyBufferElements(readOnly.ToArray()));
            case ArraySegment<T> segment:return segment.Array is null?default(ArraySegment<T>):
                new ArraySegment<T>((T[])CopyArray(segment.Array),segment.Offset,segment.Count);
            default:throw new NotSupportedException("Unsupported evidence buffer type.");
        }
    }
    private static T[] CopyBufferElements<T>(T[] elements)
    {
        if(!CopyStableType(typeof(T)))for(int i=0;i<elements.Length;i++)
        {
            var owned=Copy(elements[i]);
            elements[i]=owned is null?default!:owned is T element?element:
                throw new NotSupportedException($"Copied evidence cannot retain buffer element type {typeof(T)}.");
        }
        return elements;
    }
    private static Array BufferArray<T>(object value)=>value switch
    {Memory<T> buffer=>buffer.ToArray(),ReadOnlyMemory<T> buffer=>buffer.ToArray(),ArraySegment<T> segment=>segment.ToArray(),
        _=>throw new NotSupportedException("Unsupported evidence buffer type.")};
    public static JsonNode? ToJson(object? value)
    {
        if(value is null)return null;
        if(value is JsonNode n)return n.DeepClone();
        if(value is JsonElement element)return JsonNode.Parse(element.GetRawText());
        if(value is Matrix3 m)return ToJson(m.ToArray());
        if(value is AlignmentCycleId cycle)return ToJson(cycle.Value);
        if(BufferType(value.GetType()))return ToJson(BufferReaders.GetOrAdd(value.GetType(),static type=>
            typeof(AlignmentData).GetMethod(nameof(BufferArray),BindingFlags.NonPublic|BindingFlags.Static)!
                .MakeGenericMethod(type.GetGenericArguments()[0]).CreateDelegate<Func<object,Array>>())(value));
        if(value is IReadOnlyDictionary<string,object?> map){var o=new JsonObject();foreach(var p in map)o[p.Key]=ToJson(p.Value);return o;}
        if(value is IDictionary dict){var o=new JsonObject();foreach(DictionaryEntry p in dict)o[Convert.ToString(p.Key,CultureInfo.InvariantCulture)!]=ToJson(p.Value);return o;}
        if(value is Array a && a.Rank>1)
        {
            JsonArray Build(int dimension,int[] indices){var result=new JsonArray();for(int offset=0;offset<a.GetLength(dimension);offset++){indices[dimension]=a.GetLowerBound(dimension)+offset;result.Add(dimension==a.Rank-1?ToJson(a.GetValue(indices)):Build(dimension+1,indices));}return result;}
            return Build(0,new int[a.Rank]);
        }
        if(value is IEnumerable items && value is not string){var list=new JsonArray();foreach(var item in items)list.Add(ToJson(item));return list;}
        return JsonSerializer.SerializeToNode(value);
    }
    public static Dictionary<string,object?> FromJson(JsonElement e)=>e.EnumerateObject().ToDictionary(p=>p.Name,p=>Plain(p.Value),StringComparer.Ordinal);
    private static object? Plain(JsonElement e)=>e.ValueKind switch
    {JsonValueKind.Object=>FromJson(e),JsonValueKind.Array=>e.EnumerateArray().Select(Plain).ToArray(),JsonValueKind.String=>e.GetString(),
        JsonValueKind.True=>true,JsonValueKind.False=>false,JsonValueKind.Number=>e.TryGetInt64(out var n)?(object)n:e.GetDouble(),_=>null};
    internal static bool True(IReadOnlyDictionary<string,object?> d,string key)=>d.TryGetValue(key,out var v)&&v is true;
    internal static object? Get(IReadOnlyDictionary<string,object?> d,string key)=>d.GetValueOrDefault(key);
    internal static bool Truth(object? v)=>v switch {null=>false,bool b=>b,string s=>s.Length>0,JsonElement j=>Truth(Plain(j)),
        byte n=>n!=0,int n=>n!=0,long n=>n!=0,double n=>n!=0,float n=>n!=0,ICollection c=>c.Count>0,_=>true};
    internal static string String(object? value)
    {
        string text=Convert.ToString(value,CultureInfo.InvariantCulture)??"";
        return (value is double or float) && !text.Contains('.')&&!text.Contains('E')&&!text.Contains('e')?text+".0":text;
    }
}
