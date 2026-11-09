namespace StarMap.Core.Settings;

/// <summary>
/// An explicitly selected, isolated data directory for the C# experiment.
/// This type intentionally has no default path and never discovers or falls
/// back to a Python installation or user-data directory.
/// </summary>
public sealed class ExperimentalDataRoot
{
    public ExperimentalDataRoot(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (!System.IO.Path.IsPathFullyQualified(path))
            throw new ArgumentException("The experimental data root must be an absolute path.", nameof(path));
        Path = System.IO.Path.TrimEndingDirectorySeparator(System.IO.Path.GetFullPath(path));
        if (Path.Length == 0)
            throw new ArgumentException("An explicit experimental data root is required.", nameof(path));
    }

    public string Path { get; }
    public string UiSettingsPath => System.IO.Path.Combine(Path, "ui-settings.json");
    public string CustomGalleriesPath => System.IO.Path.Combine(Path, "custom-galleries.json");
}
