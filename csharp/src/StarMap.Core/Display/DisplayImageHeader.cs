using System.Buffers.Binary;

namespace StarMap.Core.Display;

/// <summary>Installed generic packages inspect headers; selected pixels decode in the bridge consumer.</summary>
internal static class DisplayImageHeader
{
    internal static (int Width,int Height,int Channels) Read(byte[] bytes,string extension)
    {
        if(extension=="png")return Png(bytes);
        if(extension=="jpeg")return Jpeg(bytes);
        throw Invalid();
    }
    private static (int,int,int) Png(byte[] bytes)
    {
        if(bytes.Length<33||!bytes.AsSpan(0,8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10})
            ||!bytes.AsSpan(12,4).SequenceEqual("IHDR"u8)||BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(8,4))!=13
            ||bytes[24]!=8||bytes[25] is not (2 or 6)||bytes[26]!=0||bytes[27]!=0||bytes[28]>1)throw Invalid();
        int width=BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(16,4)),height=BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(20,4));
        bool data=false,animation=false,frameBeforeData=false;
        for(int offset=8;offset+12<=bytes.Length;)
        {
            int length=BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(offset,4));
            if(length<0||(long)offset+12+length>bytes.Length)throw Invalid();
            var type=bytes.AsSpan(offset+4,4);
            if(type.SequenceEqual("acTL"u8))
            {if(length!=8||BinaryPrimitives.ReadUInt32BigEndian(bytes.AsSpan(offset+8,4))!=1)throw Invalid();animation=true;}
            if(type.SequenceEqual("fcTL"u8)&&!data)frameBeforeData=true;
            if(type.SequenceEqual("IDAT"u8))data=true;
            // Pillow Image.open checks chunks before the compressed pixels;
            // IDAT decompression and pixel checks belong to the chosen layer.
            if(!data&&Crc(bytes.AsSpan(offset+4,length+4))!=BinaryPrimitives.ReadUInt32BigEndian(bytes.AsSpan(offset+8+length,4)))throw Invalid();
            offset+=length+12;
            if(type.SequenceEqual("IEND"u8))break;
        }
        if(!data||width<=0||height<=0||animation&&!frameBeforeData)throw Invalid();
        return(width,height,bytes[25]==6?4:3);
    }
    private static (int,int,int) Jpeg(byte[] bytes)
    {
        if(bytes.Length<4||bytes[0]!=255||bytes[1]!=216)throw Invalid();
        int offset=2,width=0,height=0,channels=0;
        while(offset<bytes.Length)
        {
            if(bytes[offset++]!=255)throw Invalid();
            while(offset<bytes.Length&&bytes[offset]==255)offset++;
            if(offset>=bytes.Length)throw Invalid();int marker=bytes[offset++];
            if(marker is 0 or 216 or 217)throw Invalid();
            if(marker is >=208 and <=215 or 1)continue;
            if(offset+2>bytes.Length)throw Invalid();int length=BinaryPrimitives.ReadUInt16BigEndian(bytes.AsSpan(offset,2));
            if(length<2||offset+length>bytes.Length)throw Invalid();
            if(marker is >=192 and <=207 && marker is not (196 or 200 or 204))
            {
                if(length<8||bytes[offset+2]!=8)throw Invalid();
                height=BinaryPrimitives.ReadUInt16BigEndian(bytes.AsSpan(offset+3,2));width=BinaryPrimitives.ReadUInt16BigEndian(bytes.AsSpan(offset+5,2));
                channels=bytes[offset+7];if(channels is not (1 or 3)||length!=8+3*channels)throw Invalid();
            }
            if(marker==218)
            {if(width<=0||height<=0||channels==0)throw Invalid();return(width,height,channels);}
            offset+=length;
        }
        throw Invalid();
    }
    private static uint Crc(ReadOnlySpan<byte> data)
    {
        uint crc=uint.MaxValue;
        foreach(byte value in data){crc^=value;for(int i=0;i<8;i++)crc=(crc>>1)^((crc&1)!=0?0xedb88320u:0);}
        return ~crc;
    }
    private static InvalidDataException Invalid()=>new("author-package-image-format-mismatch");
}
