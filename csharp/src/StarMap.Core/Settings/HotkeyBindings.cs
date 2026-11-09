using System.Text;
using System.Text.Json.Nodes;

namespace StarMap.Core.Settings;

// Preference parsing only. VK identities, including OEM aliases, follow
// app/hotkey_keys.py and app/hotkey_bindings.py and require no keyboard hook.
internal static class HotkeyBindings
{
    [Flags]
    internal enum Modifiers { None = 0, Ctrl = 1, Alt = 2, Shift = 4 }

    internal readonly record struct Chord(string Name, int VirtualKey, Modifiers Held = Modifiers.None);

    private static readonly Dictionary<string, int> NamedKeys = new(StringComparer.Ordinal)
    {
        ["MOUSE2"] = 0x02, ["MOUSE3"] = 0x04, ["MOUSE4"] = 0x05, ["MOUSE5"] = 0x06,
        ["BACKSPACE"] = 0x08, ["TAB"] = 0x09, ["ENTER"] = 0x0D,
        ["SHIFT"] = 0x10, ["CTRL"] = 0x11, ["ALT"] = 0x12, ["PAUSE"] = 0x13,
        ["CAPSLOCK"] = 0x14, ["SPACE"] = 0x20, ["PAGEUP"] = 0x21, ["PAGEDOWN"] = 0x22,
        ["END"] = 0x23, ["HOME"] = 0x24, ["LEFT"] = 0x25, ["UP"] = 0x26,
        ["RIGHT"] = 0x27, ["DOWN"] = 0x28, ["PRINTSCREEN"] = 0x2C, ["INSERT"] = 0x2D, ["DELETE"] = 0x2E,
        ["NUM*"] = 0x6A, ["NUM+"] = 0x6B, ["NUM_SEPARATOR"] = 0x6C, ["NUM-"] = 0x6D,
        ["NUM."] = 0x6E, ["NUM/"] = 0x6F, ["NUMLOCK"] = 0x90, ["SCROLLLOCK"] = 0x91,
        ["VOLUME_MUTE"] = 0xAD, ["VOLUME_DOWN"] = 0xAE, ["VOLUME_UP"] = 0xAF,
        ["MEDIA_NEXT"] = 0xB0, ["MEDIA_PREV"] = 0xB1, ["MEDIA_STOP"] = 0xB2, ["MEDIA_PLAY_PAUSE"] = 0xB3,
        [";"] = 0xBA, ["="] = 0xBB, [","] = 0xBC, ["-"] = 0xBD, ["."] = 0xBE, ["/"] = 0xBF,
        ["\u0060"] = 0xC0, ["["] = 0xDB, ["\\"] = 0xDC, ["]"] = 0xDD, ["'"] = 0xDE,
        ["OEM_1"] = 0xBA, ["OEM_PLUS"] = 0xBB, ["OEM_COMMA"] = 0xBC, ["OEM_MINUS"] = 0xBD,
        ["OEM_PERIOD"] = 0xBE, ["OEM_2"] = 0xBF, ["OEM_3"] = 0xC0, ["OEM_4"] = 0xDB,
        ["OEM_5"] = 0xDC, ["OEM_6"] = 0xDD, ["OEM_7"] = 0xDE, ["OEM_102"] = 0xE2
    };

    private static readonly Dictionary<string, string> Aliases = new(StringComparer.Ordinal)
    {
        ["空格"] = "SPACE", ["CAPS"] = "CAPSLOCK", ["RETURN"] = "ENTER", ["CONTROL"] = "CTRL",
        ["PGUP"] = "PAGEUP", ["PGDN"] = "PAGEDOWN", ["DEL"] = "DELETE", ["INS"] = "INSERT",
        ["PRTSC"] = "PRINTSCREEN", ["右键"] = "MOUSE2", ["鼠标右键"] = "MOUSE2", ["RBUTTON"] = "MOUSE2",
        ["RIGHTBUTTON"] = "MOUSE2", ["鼠标中键"] = "MOUSE3", ["鼠标侧键1"] = "MOUSE4",
        ["鼠标侧键2"] = "MOUSE5", ["XBUTTON1"] = "MOUSE4", ["XBUTTON2"] = "MOUSE5"
    };

    internal static Chord Parse(JsonNode? node)
    {
        if (node is not JsonValue scalar || !scalar.TryGetValue<string>(out var text))
            throw new ArgumentException("快捷键需要文本；留空表示解绑。");
        return Parse(text);
    }

    internal static Chord Parse(string text)
    {
        var raw = text.Trim();
        if (raw.Length == 0)
            return new("", 0);
        if (TryReadPrimary(raw, out var primary))
            return primary;

        var parts = raw.Split('+', StringSplitOptions.TrimEntries);
        if (parts.Length < 2 || parts.Any(part => part.Length == 0) || !TryReadPrimary(parts[^1], out primary))
            throw new ArgumentException("无法识别快捷键。请使用有效主键，可搭配 Ctrl、Alt、Shift。");
        if (ModifierFor(primary.VirtualKey) != Modifiers.None)
            throw new ArgumentException("组合键需要一个主键，不能只使用修饰键。");

        var held = Modifiers.None;
        foreach (var part in parts.AsSpan(0, parts.Length - 1))
        {
            var modifier = part.ToUpperInvariant() switch
            {
                "CTRL" or "CONTROL" => Modifiers.Ctrl,
                "ALT" => Modifiers.Alt,
                "SHIFT" => Modifiers.Shift,
                _ => Modifiers.None
            };
            if (modifier == Modifiers.None || (held & modifier) != 0)
                throw new ArgumentException("修饰键仅支持 Ctrl、Alt、Shift，且不能重复。");
            held |= modifier;
        }

        var name = new StringBuilder();
        if (held.HasFlag(Modifiers.Ctrl)) name.Append("Ctrl+");
        if (held.HasFlag(Modifiers.Alt)) name.Append("Alt+");
        if (held.HasFlag(Modifiers.Shift)) name.Append("Shift+");
        return new(name.Append(primary.Name).ToString(), primary.VirtualKey, held);
    }

    private static bool TryReadPrimary(string raw, out Chord primary)
    {
        var upper = raw.ToUpperInvariant();
        var name = Aliases.GetValueOrDefault(upper, upper);
        if (NamedKeys.TryGetValue(name, out var vk))
            primary = new(name, vk);
        else if (name.Length == 1 && char.IsAsciiLetterOrDigit(name[0]))
            primary = new(name, name[0]);
        else if (name.Length == 7 && name.StartsWith("NUMPAD", StringComparison.Ordinal) && char.IsAsciiDigit(name[^1]))
            primary = new(name, 0x60 + name[^1] - '0');
        else if (name.StartsWith('F') && FunctionNumber(name.AsSpan(1)) is int number)
            primary = new(name, 0x70 + number - 1);
        else
        {
            primary = default;
            return false;
        }
        return true;
    }

    private static int? FunctionNumber(ReadOnlySpan<char> suffix)
    {
        // The legacy name retains its spelling; F06 and F6 are the same VK.
        var number = 0;
        foreach (var digit in suffix.EnumerateRunes())
        {
            if (!Rune.IsDigit(digit))
                return null;
            number = number * 10 + (int)Rune.GetNumericValue(digit);
            if (number > 24)
                return null;
        }
        return number >= 1 ? number : null;
    }

    private static Modifiers ModifierFor(int vk) => vk switch
    {
        0x11 => Modifiers.Ctrl,
        0x12 => Modifiers.Alt,
        0x10 => Modifiers.Shift,
        _ => Modifiers.None
    };

    internal static void ValidateUnique(string mapKey, IReadOnlyList<string> actions)
    {
        var chords = new Dictionary<(int Vk, Modifiers Held), string>();
        void Add(Chord chord, string target)
        {
            if (chord.VirtualKey == 0)
                return;
            var identity = (chord.VirtualKey, chord.Held);
            if (!chords.TryAdd(identity, target))
                throw new ArgumentException($"{target} 与 {chords[identity]} 的快捷键重复。");
        }

        Add(Parse(mapKey), "地图键");
        for (var index = 0; index < actions.Count; index++)
            Add(Parse(actions[index]), SettingsDocument.ActionOrder[index]);
        foreach (var ((vk, held), bareTarget) in chords)
        {
            var modifier = ModifierFor(vk);
            if (modifier == Modifiers.None || held != Modifiers.None)
                continue;
            foreach (var (identity, target) in chords)
                if ((identity.Held & modifier) != 0)
                    throw new ArgumentException($"{target} 与 {bareTarget} 的单独修饰键冲突。");
        }
    }
}
