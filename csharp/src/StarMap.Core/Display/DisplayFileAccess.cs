using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace StarMap.Core.Display;

/// <summary>Existing author-reader no-link requirement, including retained snapshot rechecks.</summary>
internal static class DisplayFileAccess
{
    internal static void RequireRegular(string path)
    {
        for(string? current=Path.GetFullPath(path);current is not null;current=Path.GetDirectoryName(current))
            if((File.Exists(current)||Directory.Exists(current))&&(File.GetAttributes(current)&FileAttributes.ReparsePoint)!=0)
                throw new InvalidDataException("author-resource-link-not-allowed");
        if(!File.Exists(path))throw new FileNotFoundException("author-resource-file-missing",path);
        if(OperatingSystem.IsWindows())
        {
            using var handle=File.OpenHandle(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete);
            if(!GetFileInformationByHandle(handle,out var info)||info.Links!=1)throw new InvalidDataException("author-resource-hard-link-not-allowed");
        }
    }
    [StructLayout(LayoutKind.Sequential)] private struct Information
    {public uint Attributes;public System.Runtime.InteropServices.ComTypes.FILETIME Creation,Access,Write;
        public uint Volume,SizeHigh,SizeLow,Links,IndexHigh,IndexLow;}
    [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Information information);
}
