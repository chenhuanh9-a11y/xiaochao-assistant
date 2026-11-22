namespace StarMap.Core.Input;

/// <summary>
/// Hotkey policy for the C# client. Do not swallow system keys.
///
/// The shipping Qt client observes keys on a dedicated thread with
/// <c>WH_KEYBOARD_LL</c> and always returns <c>CallNextHookEx</c>
/// (<c>experiments/qt_quick/client_native_keys.py</c>,
/// <c>experiments/qt_quick/client_input.py</c>). Business rejection must not
/// consume the original key. A map key outside a round does not start a round,
/// does not capture, and does not recognize. While waiting for the game,
/// management shortcuts stay available to this program only as observations;
/// the key still reaches the system and the game. Phase 1 does not install a
/// hook and does not register a WPF <c>InputBinding</c>. Callers must not set
/// <c>Handled</c> on system chords (Win, Alt+Tab, Alt+F4, Ctrl+Esc) or on any
/// observed global key.
/// </summary>
public static class HotkeyPolicy
{
    public readonly record struct Observation(int VirtualKey, bool PassedToSystem, bool Consumed);

    public static Observation Observe(int virtualKey)
        => new(virtualKey, PassedToSystem: true, Consumed: false);

    public static bool TryConsume(int virtualKey) => false;
}
