using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace StarMap.Core.Settings;

/// <summary>
/// Lossless JSON settings document with typed views of fields used by the
/// existing Qt client. Unknown and currently unsupported properties remain in
/// the backing object and survive an unrelated edit.
/// </summary>
public sealed class SettingsDocument
{
    public const string FileName = "ui-settings.json";

    public static readonly string[] ActionOrder =
    [
        "toggle-panel", "candidate-prev", "candidate-next", "confirm-round",
        "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round",
        "toggle-round", "manual-map-picker"
    ];

    private static readonly string[] FreshActions = ["F9", "F6", "F7", "F8", "", "", "F11", "", "F10", ""];
    private static readonly string[] LegacyActions = ["F5", "F6", "F7", "F8", "F9", "F10", "F11", "", "", ""];
    private static readonly string[] LayoutV2Modules = ["display", "mode", "manual_map", "recognition", "round"];
    private static readonly string[] LayoutV1Modules = ["display", "map", "recognition", "round"];
    private static readonly string[] LayoutDisplayKeys = ["alignedOpacity", "referenceOpacity", "referencePosition"];
    private static readonly Dictionary<string, JsonNode?> Defaults = CreateDefaults();

    private readonly object _sync = new();
    private readonly SemaphoreSlim _saveGate = new(1, 1);
    private JsonObject _values;
    private string? _boundRoot;
    private byte[]? _expectedBytes;
    private bool _hasLoadedFile;

    /// <summary>Creates a detached document from a JSON object; it is not saved until SaveAsync is called.</summary>
    public SettingsDocument(JsonObject settings)
        : this(settings, hasLoadedFile: true, boundRoot: null, expectedBytes: null)
    {
    }

    private SettingsDocument(JsonObject settings, bool hasLoadedFile, string? boundRoot, byte[]? expectedBytes)
    {
        ArgumentNullException.ThrowIfNull(settings);
        _values = (JsonObject)settings.DeepClone();
        _hasLoadedFile = hasLoadedFile;
        _boundRoot = boundRoot;
        _expectedBytes = expectedBytes?.ToArray();
    }

    /// <summary>A detached JSON snapshot. Mutations must go through Set so known fields are validated.</summary>
    public JsonObject Values
    {
        get { lock (_sync) return (JsonObject)_values.DeepClone(); }
    }

    public string GameMode => ReadString("gameMode", "nightmare", IsKnownModeForCurrentSeason);
    public string GameSeason => ReadString("gameSeason", "s0", IsSeasonId);
    public string MapSelectionMode => ReadString("mapSelectionMode", "auto", value => value is "auto" or "manual");
    public string RouteProfileId => ReadString("routeProfileId", "wang-qingxin", IsProfileId);
    public string Zhan10CoffinVariantId => ReadString("zhan10CoffinVariantId", "variant-01", IsProfileId);
    public string Hotkey { get { lock (_sync) return ReadMapKey(_values); } }
    public IReadOnlyList<string> ActionHotkeys { get { lock (_sync) return ReadActions(_values, out _); } }
    public bool AutomaticFailureEvidence => ReadBoolean("automaticFailureEvidence", true);
    public bool ReferenceSheetVisible => ReadBoolean("referenceSheetVisible", true);
    public bool SheetVisibilityEnabled => ReadBoolean("sheetVisibilityEnabled", true);
    public bool OrbVisible => ReadBoolean("orbVisible", true);
    public bool QtControlsVisible => ReadBoolean("qtControlsVisible", true);
    public bool ReduceMotion => ReadBoolean("reduceMotion", false);
    public bool UiSoundEnabled => ReadBoolean("uiSoundEnabled", false);
    public bool DifficultGridEnabled => ReadBoolean("difficultGridEnabled", false);
    public bool OnboardingCompleted => ReadBoolean("onboardingCompleted", false);
    public bool RoutePointNumbers => ReadBoolean("routePointNumbers", false);
    public double SheetOpacity => ReadRange("sheetOpacity", 0.55, 0, 1);
    public double ReferenceOpacity => ReadReferenceOpacity();
    public double InterfaceOpacity => ReadRange("interfaceOpacity", 0.82, 0.65, 0.95);
    public double SheetScale => ReadRange("sheetScale", 1, 0.5, 1.5);
    public JsonObject GameControlLayout => ReadLayout();
    public WindowPosition? SheetPosition => ReadPosition("sheetPosition");
    public WindowPosition? CompactPosition => ReadPosition("compactPosition");
    public string SheetPositionSpace => ReadString("sheetPositionSpace", "unknown",
        value => value is "unknown" or "qt-logical-v1" or "legacy-physical-v1");
    public string FloorControlMode => ReadString("floorControlMode", "auto",
        value => value is "auto" or "region" or "fixed");
    public JsonObject ModeRouteProfiles => ReadStringMap("modeRouteProfiles", ["nightmare", "difficult"]);
    public JsonObject ContextRouteProfiles => ReadStringMap("contextRouteProfiles",
        ["s0:nightmare", "s0:difficult", "s1:nightmare", "s1:difficult", "s1:difficulty2"]);
    public JsonObject? FloorControlRegion => ReadFloorRegion();

    public static SettingsDocument CreateNew() => new(new JsonObject(), hasLoadedFile: false, boundRoot: null, expectedBytes: null);

    public static async Task<DocumentLoadResult<SettingsDocument>> LoadAsync(
        ExperimentalDataRoot root,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(root);
        try
        {
            JsonDocumentFile.EnsureNotReparsePoint(root.Path);
            var bytes = await JsonDocumentFile.ReadOptionalAsync(root.UiSettingsPath, cancellationToken).ConfigureAwait(false);
            var values = bytes is null ? new JsonObject() : JsonDocumentFile.ParseObject(bytes);
            return DocumentLoadResult<SettingsDocument>.Success(new SettingsDocument(
                values, hasLoadedFile: bytes is not null, boundRoot: root.Path, expectedBytes: bytes));
        }
        catch (Exception error) when (IsDataError(error))
        {
            return DocumentLoadResult<SettingsDocument>.Failure(error);
        }
    }

    /// <summary>
    /// Explicitly copies a user-selected JSON file into an empty experimental
    /// root, then adopts only the verified copy. The selected source is read-only.
    /// </summary>
    public static async Task<DocumentLoadResult<SettingsDocument>> ImportAsync(
        ExperimentalDataRoot root,
        string sourcePath,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(root);
        ArgumentException.ThrowIfNullOrWhiteSpace(sourcePath);
        string? temporary = null;
        try
        {
            var sourceBytes = await File.ReadAllBytesAsync(sourcePath, cancellationToken).ConfigureAwait(false);
            _ = JsonDocumentFile.ParseObject(sourceBytes);
            Directory.CreateDirectory(root.Path);
            JsonDocumentFile.EnsureNotReparsePoint(root.Path);
            JsonDocumentFile.EnsureNotReparsePoint(root.UiSettingsPath);
            if (File.Exists(root.UiSettingsPath))
                throw new IOException("The experimental copy already exists. Load or choose another experiment root.");

            temporary = Path.Combine(root.Path, $".{FileName}.{Guid.NewGuid():N}.import.tmp");
            await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                             FileShare.None, 4096, FileOptions.Asynchronous | FileOptions.WriteThrough))
            {
                await stream.WriteAsync(sourceBytes, cancellationToken).ConfigureAwait(false);
                await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
                stream.Flush(flushToDisk: true);
            }

            var stagedBytes = await File.ReadAllBytesAsync(temporary, cancellationToken).ConfigureAwait(false);
            if (!stagedBytes.AsSpan().SequenceEqual(sourceBytes))
                throw new IOException("The staged settings copy does not match the selected source.");
            _ = JsonDocumentFile.ParseObject(stagedBytes);

            JsonDocumentFile.EnsureNotReparsePoint(root.UiSettingsPath);
            File.Move(temporary, root.UiSettingsPath);
            temporary = null;

            JsonDocumentFile.EnsureNotReparsePoint(root.UiSettingsPath);
            var copiedBytes = await File.ReadAllBytesAsync(root.UiSettingsPath, cancellationToken).ConfigureAwait(false);
            if (!copiedBytes.AsSpan().SequenceEqual(sourceBytes))
                throw new IOException("The copied settings file did not match the selected source.");
            var copiedValues = JsonDocumentFile.ParseObject(copiedBytes);
            return DocumentLoadResult<SettingsDocument>.Success(new SettingsDocument(
                copiedValues, hasLoadedFile: true, boundRoot: root.Path, expectedBytes: copiedBytes));
        }
        catch (Exception error) when (IsDataError(error))
        {
            return DocumentLoadResult<SettingsDocument>.Failure(error);
        }
        finally
        {
            if (temporary is not null && File.Exists(temporary))
            {
                JsonDocumentFile.EnsureNotReparsePoint(temporary);
                File.Delete(temporary);
            }
        }
    }

    public JsonNode? Get(string key)
    {
        ArgumentNullException.ThrowIfNull(key);
        lock (_sync)
            return _values[key]?.DeepClone();
    }

    public bool GetBoolean(string key)
    {
        if (!Defaults.TryGetValue(key, out var defaultNode) || defaultNode is not JsonValue defaultValue
            || !TryGetBoolean(defaultValue, out var fallback))
            throw new ArgumentException($"'{key}' is not a boolean setting.", nameof(key));
        return ReadBoolean(key, fallback);
    }

    public void Set(string key, JsonNode? value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(key);
        SetMany(new Dictionary<string, JsonNode?> { [key] = value });
    }

    /// <summary>Validate the final combination and apply all explicit edits together.</summary>
    public void SetMany(IReadOnlyDictionary<string, JsonNode?> edits)
    {
        ArgumentNullException.ThrowIfNull(edits);
        lock (_sync)
        {
            if (edits.Count == 0)
                return;
            var candidate = (JsonObject)_values.DeepClone();
            string[]? actionEdits = null;
            foreach (var (key, value) in edits)
            {
                ArgumentException.ThrowIfNullOrWhiteSpace(key);
                var copy = value?.DeepClone();
                if (key == "actionHotkeys")
                    actionEdits = ParseActionEdits(copy);
                else if (key == "hotkey")
                    candidate[key] = HotkeyBindings.Parse(copy).Name;
                else
                {
                    ValidateKnownValue(key, copy);
                    candidate[key] = copy;
                }
            }
            if (edits.ContainsKey("gameSeason") || edits.ContainsKey("gameMode"))
            {
                // If its partner already has an unreadable raw value, the
                // caller can repair both together. Unrelated edits preserve it.
                if (candidate.TryGetPropertyValue("gameMode", out var rawMode))
                    ValidateKnownValue("gameMode", rawMode);
                if (candidate.TryGetPropertyValue("gameSeason", out var rawSeason))
                    ValidateKnownValue("gameSeason", rawSeason);
                if (!IsModeAllowedForSeason(ReadStoredMode(candidate), ReadStoredSeason(candidate)))
                    throw Invalid("gameMode", "difficulty2 is a retained identity only for season s1");
            }

            if (actionEdits is not null || edits.ContainsKey("hotkey"))
            {
                var previousActions = ReadActions(_values, out var actionIssue);
                ValidateHotkeys(ReadMapKey(candidate), actionEdits ?? previousActions, _values["actionHotkeys"],
                    protectStoredTail: actionEdits is null);
                if (actionEdits is not null)
                    candidate["actionHotkeys"] = MergeActionEdits(_values["actionHotkeys"], previousActions,
                        actionEdits, repairAll: actionIssue is not null);
            }
            _values = candidate;
        }
    }

    public void Set<T>(string key, T value)
    {
        if (key is "sheetPosition" or "compactPosition" && value is WindowPosition position)
        {
            Set(key, new JsonArray(position.X, position.Y));
            return;
        }
        Set(key, JsonSerializer.SerializeToNode(value));
    }

    public IReadOnlyDictionary<string, string> GetValidationIssues()
    {
        var issues = new Dictionary<string, string>(StringComparer.Ordinal);
        lock (_sync)
        {
            foreach (var pair in _values)
            {
                try
                {
                    if (pair.Key == "hotkey")
                        HotkeyBindings.Parse(pair.Value);
                    else if (pair.Key == "actionHotkeys")
                    {
                        ReadActions(_values, out var issue);
                        if (issue is not null)
                            issues[pair.Key] = issue;
                    }
                    else
                        ValidateKnownValue(pair.Key, pair.Value);
                }
                catch (ArgumentException error)
                {
                    issues[pair.Key] = error.Message;
                }
            }

            var season = ReadStoredSeason(_values);
            var mode = ReadStoredMode(_values);
            if (!IsModeAllowedForSeason(mode, season))
                issues["gameMode"] = "difficulty2 is a retained identity only for season s1";
            try
            {
                ValidateHotkeys(ReadMapKey(_values), ReadActions(_values, out _), _values["actionHotkeys"]);
            }
            catch (ArgumentException error)
            {
                issues["hotkey/actionHotkeys"] = error.Message;
            }
        }
        return issues;
    }

    public async Task<DocumentSaveResult> SaveAsync(
        ExperimentalDataRoot root,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(root);
        await _saveGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            byte[] payload;
            byte[]? expected;
            lock (_sync)
            {
                if (_boundRoot is not null && !PathEquals(_boundRoot, root.Path))
                    throw new IOException("This settings document belongs to a different experimental data root.");
                var snapshot = _values;
                if (!_hasLoadedFile && !snapshot.ContainsKey("actionHotkeys"))
                {
                    snapshot = (JsonObject)snapshot.DeepClone();
                    snapshot["actionHotkeys"] = Defaults["actionHotkeys"]!.DeepClone();
                }
                payload = JsonDocumentFile.Serialize(snapshot);
                expected = _hasLoadedFile ? _expectedBytes?.ToArray() : null;
            }

            var saved = await JsonDocumentFile.WriteAtomicallyAsync(
                root, root.UiSettingsPath, payload, expected, cancellationToken).ConfigureAwait(false);
            lock (_sync)
            {
                _boundRoot = root.Path;
                _expectedBytes = saved;
                // An edit may have arrived during I/O. Retain it when adopting
                // the new file's previously implicit default.
                if (!_hasLoadedFile && !_values.ContainsKey("actionHotkeys"))
                    _values["actionHotkeys"] = Defaults["actionHotkeys"]!.DeepClone();
                _hasLoadedFile = true;
            }
            return DocumentSaveResult.Success();
        }
        catch (Exception error) when (IsDataError(error))
        {
            return DocumentSaveResult.Failure(error);
        }
        finally
        {
            _saveGate.Release();
        }
    }

    private string ReadString(string key, string fallback, Func<string, bool> valid)
    {
        lock (_sync)
        {
            if (_values[key] is JsonValue value && TryGetString(value, out var text) && valid(text))
                return text;
            return fallback;
        }
    }

    private bool IsKnownModeForCurrentSeason(string value)
    {
        var season = ReadStoredSeason(_values);
        return IsModeAllowedForSeason(value, season);
    }

    private static bool IsSeasonId(string value)
        => value is "s0" or "s1";

    private static string ReadStoredSeason(JsonObject settings)
        => settings["gameSeason"] is JsonValue value && TryGetString(value, out var season) && IsSeasonId(season)
            ? season
            : "s0";

    private static string ReadStoredMode(JsonObject settings)
    {
        if (settings["gameMode"] is not JsonValue value || !TryGetString(value, out var mode)
            || mode is not ("nightmare" or "difficult" or "difficulty2"))
            return "nightmare";
        return mode;
    }

    private static bool IsModeAllowedForSeason(string mode, string season)
        => mode is "nightmare" or "difficult" || (season == "s1" && mode == "difficulty2");

    private static bool IsProfileId(string value)
        => value.Length is > 0 and <= 128 && !value.Any(char.IsControl);

    private double ReadRange(string key, double fallback, double minimum, double maximum)
    {
        lock (_sync)
            return _values[key] is JsonValue value && TryGetNumber(value, out var number)
                   && double.IsFinite(number) && number >= minimum && number <= maximum
                ? number
                : fallback;
    }

    private double ReadReferenceOpacity()
    {
        lock (_sync)
        {
            if (!_values.ContainsKey("referenceOpacity"))
                return ReadRange("sheetOpacity", 0.55, 0, 1);
            return _values["referenceOpacity"] is JsonValue value && TryGetNumber(value, out var number)
                   && double.IsFinite(number) && number >= 0 && number <= 1
                ? number
                : 0.55;
        }
    }

    private bool ReadBoolean(string key, bool fallback)
    {
        lock (_sync)
        {
            if (!_values.TryGetPropertyValue(key, out var node))
                return fallback;
            if (node is JsonValue value && TryGetBoolean(value, out var actual))
                return actual;
            // The Python reader intentionally disables this evidence producer
            // when its stored boolean is malformed, rather than enabling it.
            return key == "automaticFailureEvidence" ? false : fallback;
        }
    }

    private static string ReadMapKey(JsonObject values)
    {
        if (values.TryGetPropertyValue("hotkey", out var stored))
        {
            try { return HotkeyBindings.Parse(stored).Name; }
            catch (ArgumentException) { }
        }
        return "G";
    }

    private string[] ReadActions(JsonObject values, out string? issue)
    {
        issue = null;
        var defaults = _hasLoadedFile ? LegacyActions : FreshActions;
        if (!values.TryGetPropertyValue("actionHotkeys", out var stored))
            return defaults.ToArray();
        if (stored is not JsonArray array || array.Count is < 6 or > 10)
        {
            issue = "旧版功能快捷键应有 6 至 10 项。";
            return defaults.ToArray();
        }

        var names = new string[ActionOrder.Length];
        Array.Fill(names, string.Empty);
        try
        {
            for (var i = 0; i < Math.Min(array.Count, 9); i++)
                names[i] = HotkeyBindings.Parse(array[i]).Name;
            if (array.Count == 6)
            {
                var used = names.Take(6).Select(name => HotkeyBindings.Parse(name).VirtualKey).ToHashSet();
                used.Add(HotkeyBindings.Parse(ReadMapKey(values)).VirtualKey);
                var free = Enumerable.Range(11, 14).First(number => !used.Contains(0x70 + number - 1));
                names[6] = "F" + free.ToString(CultureInfo.InvariantCulture);
            }
        }
        catch (ArgumentException error)
        {
            issue = error.Message;
            names = defaults.ToArray();
        }

        if (array.Count == 10)
        {
            try { names[9] = HotkeyBindings.Parse(array[9]).Name; }
            catch (ArgumentException) { names[9] = string.Empty; } // The raw tail is checked at write time.
        }
        return names;
    }

    private static string[] ParseActionEdits(JsonNode? value)
    {
        if (value is not JsonArray array || array.Count != ActionOrder.Length)
            throw Invalid("actionHotkeys", "must contain ten text entries in the current action order");
        return array.Select(node => HotkeyBindings.Parse(node).Name).ToArray();
    }

    private static void ValidateHotkeys(string mapKey, string[] actions, JsonNode? stored, bool protectStoredTail = true)
    {
        // A malformed picker chord projects as unbound, but must not be
        // silently erased by changing another shortcut.
        if (protectStoredTail && actions[9].Length == 0 && HasInvalidActionTail(stored))
            throw Invalid("actionHotkeys", "第 10 项“手动选图”快捷键无效，请先修复配置或重新绑定");
        HotkeyBindings.ValidateUnique(mapKey, actions);
    }

    private static bool HasInvalidActionTail(JsonNode? stored)
    {
        if (stored is not JsonArray { Count: 10 } array)
            return false;
        try
        {
            HotkeyBindings.Parse(array[9]);
            return false;
        }
        catch (ArgumentException)
        {
            return true;
        }
    }

    private static JsonArray MergeActionEdits(JsonNode? stored, string[] previous, string[] edits, bool repairAll)
    {
        if (stored is not JsonArray { Count: 9 or 10 } original)
            return new JsonArray(edits.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray());
        var merged = (JsonArray)original.DeepClone();
        for (var i = 0; i < edits.Length; i++)
        {
            // The typed view maps a malformed picker tail to empty. When the
            // caller explicitly supplies that empty tail, persist the repair
            // even though its projected value is unchanged.
            if (!repairAll && edits[i] == previous[i] && !(i == 9 && HasInvalidActionTail(stored)))
                continue;
            if (i < merged.Count)
                merged[i] = edits[i];
            else if (edits[i].Length != 0)
                merged.Add(edits[i]);
        }
        return merged;
    }

    private JsonObject ReadLayout()
    {
        lock (_sync)
        {
            if (_values["gameControlLayout"] is JsonObject layout && IsValidLayout(layout))
                return (JsonObject)layout.DeepClone();
            return CreateLayoutV2(compact: true);
        }
    }

    private JsonObject ReadStringMap(string key, IReadOnlyCollection<string> allowedKeys)
    {
        lock (_sync)
            return _values[key] is JsonObject value && IsStringMap(value, allowedKeys)
                ? (JsonObject)value.DeepClone()
                : new JsonObject();
    }

    private JsonObject? ReadFloorRegion()
    {
        lock (_sync)
            return _values["floorControlRegion"] is JsonObject value && IsValidFloorRegion(value)
                ? (JsonObject)value.DeepClone()
                : null;
    }

    private WindowPosition? ReadPosition(string key)
    {
        lock (_sync)
        {
            if (_values[key] is not JsonArray array || array.Count != 2
                || !TryGetInt32(array[0], out var x) || !TryGetInt32(array[1], out var y))
                return null;
            return new WindowPosition(x, y);
        }
    }

    private static void ValidateKnownValue(string key, JsonNode? value)
    {
        if (Defaults.TryGetValue(key, out var defaultValue) && defaultValue is JsonValue defaultScalar
            && TryGetBoolean(defaultScalar, out _))
        {
            if (value is not JsonValue boolValue || !TryGetBoolean(boolValue, out _))
                throw Invalid(key, "must be a JSON boolean");
            return;
        }

        switch (key)
        {
            case "sheetOpacity":
            case "referenceOpacity":
                RequireRange(key, value, 0, 1);
                return;
            case "interfaceOpacity":
                RequireRange(key, value, 0.65, 0.95);
                return;
            case "sheetScale":
                RequireRange(key, value, 0.5, 1.5);
                return;
            case "gameMode":
                if (!TryGetString(value, out var mode) || mode is not ("nightmare" or "difficult" or "difficulty2"))
                    throw Invalid(key, "must be nightmare, difficult, or the retained S1 difficulty2 identity");
                return;
            case "gameSeason":
                if (!TryGetString(value, out var season) || !IsSeasonId(season))
                    throw Invalid(key, "must match a season currently declared by the source catalog (s0 or s1)");
                return;
            case "routeProfileId":
            case "zhan10CoffinVariantId":
                if (!TryGetString(value, out var profile) || !IsProfileId(profile))
                    throw Invalid(key, "must be a nonempty ID of at most 128 characters without control characters");
                return;
            case "modeRouteProfiles":
                if (!IsStringMap(value, ["nightmare", "difficult"]))
                    throw Invalid(key, "must be an object keyed by nightmare and/or difficult");
                return;
            case "contextRouteProfiles":
                if (!IsStringMap(value, ["s0:nightmare", "s0:difficult", "s1:nightmare", "s1:difficult", "s1:difficulty2"]))
                    throw Invalid(key, "must be a map of known season:mode identities to profile IDs");
                return;
            case "mapSelectionMode":
                if (!TryGetString(value, out var selection) || selection is not ("auto" or "manual"))
                    throw Invalid(key, "must be auto or manual");
                return;
            case "gameControlLayout":
                if (value is not JsonObject layout || !IsValidLayout(layout))
                    throw Invalid(key, "does not match a supported control-layout schema");
                return;
            case "sheetPosition":
            case "compactPosition":
                if (value is not null && !IsPosition(value))
                    throw Invalid(key, "must be null or a pair of signed 32-bit integer coordinates");
                return;
            case "sheetPositionSpace":
                if (!TryGetString(value, out var space) || space is not ("unknown" or "qt-logical-v1" or "legacy-physical-v1"))
                    throw Invalid(key, "must be unknown, qt-logical-v1, or legacy-physical-v1");
                return;
            case "floorControlMode":
                if (!TryGetString(value, out var floorMode) || floorMode is not ("auto" or "region" or "fixed"))
                    throw Invalid(key, "must be auto, region, or fixed");
                return;
            case "floorControlRegion":
                if (value is not null && !IsValidFloorRegion(value))
                    throw Invalid(key, "must contain a normalized rect and its original clientSize");
                return;
        }
    }

    private static bool IsStringMap(JsonNode? node, IReadOnlyCollection<string> allowedKeys)
    {
        if (node is not JsonObject map || map.Select(pair => pair.Key)
                .Any(key => !allowedKeys.Contains(key, StringComparer.Ordinal)))
            return false;
        return map.All(pair => TryGetString(pair.Value, out var value) && IsProfileId(value));
    }

    private static bool IsPosition(JsonNode node)
        => node is JsonArray array && array.Count == 2
           && TryGetInt32(array[0], out _) && TryGetInt32(array[1], out _);

    private static bool IsValidFloorRegion(JsonNode node)
    {
        if (node is not JsonObject region || region.Count != 2
            || region["rect"] is not JsonArray rect || rect.Count != 4
            || region["clientSize"] is not JsonArray size || size.Count != 2)
            return false;
        var values = new double[4];
        for (var index = 0; index < values.Length; index++)
        {
            if (rect[index] is not JsonValue scalar || !TryGetNumber(scalar, out values[index]) || !double.IsFinite(values[index]))
                return false;
        }
        return TryGetInt32(size[0], out var width) && TryGetInt32(size[1], out var height)
               && width is >= 100 and <= 32768 && height is >= 100 and <= 32768
               && values[0] >= 0 && values[1] >= 0 && values[2] >= 0.01 && values[3] >= 0.005
               && values[0] + values[2] <= 1.0001 && values[1] + values[3] <= 1.0001;
    }

    private static bool IsValidLayout(JsonObject layout)
    {
        if (layout.Count != 4 || !TryGetInt32(layout["schemaVersion"], out var version)
            || (version != 1 && version != 2))
            return false;
        var modules = version == 1 ? LayoutV1Modules : LayoutV2Modules;
        if (layout["order"] is not JsonArray order || order.Count != modules.Length
            || order.Any(item => !TryGetString(item, out _))
            || order.Select(item => item!.GetValue<string>()).Distinct(StringComparer.Ordinal).Count() != modules.Length
            || modules.Any(module => !order.Any(item => item!.GetValue<string>() == module)))
            return false;
        if (layout["enabled"] is not JsonArray enabled || enabled.Any(item => !TryGetString(item, out _)))
            return false;
        var enabledNames = enabled.Select(item => item!.GetValue<string>()).ToArray();
        if (enabledNames.Distinct(StringComparer.Ordinal).Count() != enabledNames.Length
            || enabledNames.Any(name => !modules.Contains(name, StringComparer.Ordinal)))
            return false;
        if (layout["display"] is not JsonObject display || display.Count != LayoutDisplayKeys.Length)
            return false;
        return LayoutDisplayKeys.All(key => display[key] is JsonValue flag && TryGetBoolean(flag, out _));
    }

    private static JsonObject CreateLayoutV2(bool compact)
    {
        var order = new JsonArray(LayoutV2Modules.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray());
        var enabledNames = compact ? new[] { "display", "mode", "round" } : new[] { "display", "mode", "manual_map", "round" };
        return new JsonObject
        {
            ["schemaVersion"] = 2,
            ["order"] = order,
            ["enabled"] = new JsonArray(enabledNames.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray()),
            ["display"] = new JsonObject
            {
                ["alignedOpacity"] = !compact,
                ["referenceOpacity"] = !compact,
                ["referencePosition"] = false
            }
        };
    }

    private static void RequireRange(string key, JsonNode? value, double minimum, double maximum)
    {
        if (value is not JsonValue scalar || !TryGetNumber(scalar, out var number)
            || !double.IsFinite(number) || number < minimum || number > maximum)
            throw Invalid(key, $"must be a finite number from {minimum.ToString(CultureInfo.InvariantCulture)} to {maximum.ToString(CultureInfo.InvariantCulture)}");
    }

    private static Dictionary<string, JsonNode?> CreateDefaults()
    {
        var layout = CreateLayoutV2(compact: true);
        return new Dictionary<string, JsonNode?>(StringComparer.Ordinal)
        {
            ["automaticFailureEvidence"] = JsonValue.Create(true),
            ["referenceSheetVisible"] = JsonValue.Create(true),
            ["sheetVisibilityEnabled"] = JsonValue.Create(true),
            ["orbVisible"] = JsonValue.Create(true),
            ["qtControlsVisible"] = JsonValue.Create(true),
            ["reduceMotion"] = JsonValue.Create(false),
            ["uiSoundEnabled"] = JsonValue.Create(false),
            ["difficultGridEnabled"] = JsonValue.Create(false),
            ["onboardingCompleted"] = JsonValue.Create(false),
            ["routePointNumbers"] = JsonValue.Create(false),
            ["sheetOpacity"] = JsonValue.Create(0.55),
            ["referenceOpacity"] = JsonValue.Create(0.55),
            ["interfaceOpacity"] = JsonValue.Create(0.82),
            ["sheetScale"] = JsonValue.Create(1.0),
            ["gameMode"] = JsonValue.Create("nightmare"),
            ["gameSeason"] = JsonValue.Create("s0"),
            ["mapSelectionMode"] = JsonValue.Create("auto"),
            ["routeProfileId"] = JsonValue.Create("wang-qingxin"),
            ["modeRouteProfiles"] = new JsonObject(),
            ["contextRouteProfiles"] = new JsonObject(),
            ["zhan10CoffinVariantId"] = JsonValue.Create("variant-01"),
            ["hotkey"] = JsonValue.Create("G"),
            ["actionHotkeys"] = new JsonArray(FreshActions.Select(name => (JsonNode?)JsonValue.Create(name)).ToArray()),
            ["gameControlLayout"] = layout,
            ["sheetPosition"] = null,
            ["sheetPositionSpace"] = JsonValue.Create("unknown"),
            ["compactPosition"] = null,
            ["floorControlMode"] = JsonValue.Create("auto"),
            ["floorControlRegion"] = null
        };
    }

    private static bool TryGetString(JsonNode? node, out string value)
    {
        try
        {
            if (node is JsonValue scalar)
            {
                value = scalar.GetValue<string>();
                return true;
            }
        }
        catch (InvalidOperationException)
        {
        }
        value = string.Empty;
        return false;
    }

    private static bool TryGetBoolean(JsonNode node, out bool value)
    {
        try
        {
            if (node is JsonValue scalar)
            {
                value = scalar.GetValue<bool>();
                return true;
            }
        }
        catch (InvalidOperationException)
        {
        }
        value = false;
        return false;
    }

    private static bool TryGetNumber(JsonValue node, out double value)
    {
        try
        {
            value = node.GetValue<double>();
            return true;
        }
        catch (InvalidOperationException)
        {
        }
        catch (FormatException)
        {
        }
        value = 0;
        return false;
    }

    private static bool TryGetInt32(JsonNode? node, out int value)
    {
        try
        {
            if (node is JsonValue scalar)
            {
                value = scalar.GetValue<int>();
                return true;
            }
        }
        catch (InvalidOperationException)
        {
        }
        catch (FormatException)
        {
        }
        value = 0;
        return false;
    }

    private static ArgumentException Invalid(string key, string explanation)
        => new($"Setting '{key}' {explanation}.", key);

    private static bool IsDataError(Exception error)
        => error is IOException or UnauthorizedAccessException or JsonException or DecoderFallbackException
            or ArgumentException or InvalidOperationException or NotSupportedException or FormatException;

    private static bool PathEquals(string left, string right)
        => string.Equals(Path.TrimEndingDirectorySeparator(Path.GetFullPath(left)),
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(right)),
            OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal);
}

public readonly record struct WindowPosition(int X, int Y);
