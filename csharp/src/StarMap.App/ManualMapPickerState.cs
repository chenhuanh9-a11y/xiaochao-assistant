namespace StarMap.App;

internal sealed record ManualMapPickerEntry(
    string MapId,
    string Name,
    string Direction,
    string? ThumbnailPath,
    string ThumbnailStamp);

internal sealed record ManualMapPickerOption(
    int Index,
    string Key,
    string Label,
    string? MapId,
    int Count,
    string? ThumbnailPath,
    string ThumbnailStamp);

/// <summary>Pure two-stage manual map picker state, ported from the legacy picker core.</summary>
internal sealed class ManualMapPickerState
{
    const int PageSize = 4;
    static readonly string[] OptionKeys = ["F1", "F2", "F3", "F4"];
    static readonly string[] NightmareGroupOrder = ["左中右", "左中", "左右", "右中"];
    const string OtherGroup = "其他";

    sealed record Group(string Label, IReadOnlyList<ManualMapPickerEntry> Maps);

    readonly IReadOnlyList<Group> groups;
    int? selectedGroup;
    int page;
    int groupPage;

    internal ManualMapPickerState(IReadOnlyList<ManualMapPickerEntry> maps, string mode)
    {
        ArgumentNullException.ThrowIfNull(maps);
        groups = BuildGroups(maps, mode ?? string.Empty);
        if (groups.Count == 1) selectedGroup = 0;
    }

    internal string Stage => selectedGroup is null ? "groups" : "maps";
    internal string? GroupLabel => selectedGroup is { } index ? groups[index].Label : null;
    internal bool SingleGroup => groups.Count == 1;
    internal int Page => page;
    internal int PageCount => (int)Math.Max(1L, ((long)ItemCount() + PageSize - 1) / PageSize);

    internal IReadOnlyList<ManualMapPickerOption> Options
    {
        get
        {
            int itemCount = ItemCount();
            int start = page * PageSize;
            int count = Math.Min(PageSize, Math.Max(0, itemCount - start));
            var result = new ManualMapPickerOption[count];
            for (int i = 0; i < count; i++)
            {
                int itemIndex = start + i;
                if (selectedGroup is null)
                {
                    var group = groups[itemIndex];
                    result[i] = new(i, OptionKeys[i], group.Label, null, group.Maps.Count, null, string.Empty);
                }
                else
                {
                    var map = groups[selectedGroup.Value].Maps[itemIndex];
                    result[i] = new(i, OptionKeys[i], OptionTitle(map, groups[selectedGroup.Value].Label),
                        map.MapId, 1, map.ThumbnailPath, map.ThumbnailStamp);
                }
            }
            return Array.AsReadOnly(result);
        }
    }

    internal ManualMapPickerEntry? Choose(int index)
    {
        int itemCount = ItemCount();
        int itemIndex = page * PageSize + index;
        if (index < 0 || index >= PageSize || itemIndex >= itemCount) return null;

        if (selectedGroup is null)
        {
            groupPage = page;
            selectedGroup = itemIndex;
            page = 0;
            return null;
        }

        return groups[selectedGroup.Value].Maps[itemIndex];
    }

    internal bool Back()
    {
        if (selectedGroup is null || SingleGroup) return false;
        selectedGroup = null;
        page = groupPage;
        return true;
    }

    internal bool Turn(int delta)
    {
        long target = (long)page + delta;
        int next = (int)Math.Clamp(target, 0L, PageCount - 1L);
        if (next == page) return false;
        page = next;
        return true;
    }

    int ItemCount() => selectedGroup is { } index ? groups[index].Maps.Count : groups.Count;

    static IReadOnlyList<Group> BuildGroups(IReadOnlyList<ManualMapPickerEntry> maps, string mode)
    {
        var ordered = new List<Group>();
        var grouped = new Dictionary<string, List<ManualMapPickerEntry>>(StringComparer.Ordinal);
        foreach (var map in maps)
        {
            string label = MapGroup(map);
            if (!grouped.TryGetValue(label, out var members))
            {
                members = [];
                grouped.Add(label, members);
                ordered.Add(new(label, members));
            }
            members.Add(map);
        }

        var labels = new List<string>();
        if (mode == "nightmare")
            foreach (string preferred in NightmareGroupOrder)
                if (grouped.ContainsKey(preferred)) labels.Add(preferred);

        foreach (var group in ordered)
            if (group.Label != OtherGroup && !labels.Contains(group.Label, StringComparer.Ordinal))
                labels.Add(group.Label);
        if (grouped.ContainsKey(OtherGroup)) labels.Add(OtherGroup);

        return Array.AsReadOnly(labels.Select(label =>
            new Group(label, Array.AsReadOnly(grouped[label].ToArray()))).ToArray());
    }

    static string MapGroup(ManualMapPickerEntry map)
    {
        var (prefix, _) = SplitName(map.Name);
        if (prefix.Length > 0) return prefix;
        string direction = (map.Direction ?? string.Empty).Trim();
        return direction.Length > 0
            && direction != "unknown"
            && direction != "全部"
                ? direction
                : OtherGroup;
    }

    static string OptionTitle(ManualMapPickerEntry map, string group)
    {
        var (prefix, remainder) = SplitName(map.Name);
        return prefix.Length > 0 && prefix == group ? remainder : map.Name ?? string.Empty;
    }

    static (string Prefix, string Remainder) SplitName(string? name)
    {
        string value = name ?? string.Empty;
        string[] parts = value.Split('/').Select(part => part.Trim()).ToArray();
        if (parts.Length >= 2 && parts.All(part => part.Length > 0))
            return (parts[0], string.Join(" / ", parts.Skip(1)));
        return (string.Empty, value.Trim());
    }
}
