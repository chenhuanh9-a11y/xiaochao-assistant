namespace StarMap.Core.Settings;

public sealed record DocumentLoadResult<T>(T? Document, Exception? Error)
    where T : class
{
    public bool Succeeded => Document is not null && Error is null;

    public static DocumentLoadResult<T> Success(T document) => new(document, null);
    public static DocumentLoadResult<T> Failure(Exception error) => new(null, error);
}

public sealed record DocumentSaveResult(bool Succeeded, Exception? Error)
{
    public static DocumentSaveResult Success() => new(true, null);
    public static DocumentSaveResult Failure(Exception error) => new(false, error);
}
