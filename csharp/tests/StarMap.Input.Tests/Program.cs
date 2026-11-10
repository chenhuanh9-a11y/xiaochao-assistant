using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using StarMap.App.Platform;
using StarMap.Core.Input;
using StarMap.Core.Settings;

if (args.Contains("--native-lifecycle", StringComparer.Ordinal))
    return await NativeLifecycle.RunAsync();

var checks = 0;
var failures = new List<string>();
void Check(bool condition, string name)
{
    checks++;
    if (condition) Console.WriteLine("ok " + name);
    else { failures.Add(name); Console.Error.WriteLine("FAIL " + name); }
}
GlobalInputBindings Bind(JsonObject map)
{
    var actions = SettingsDocument.ActionOrder.Select(action => map[action]?.GetValue<string>() ?? "").ToArray();
    return GlobalInputBindings.FromSettings(map["map"]?.GetValue<string>() ?? "", actions);
}
double Number(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<string>(out var text)
    ? text switch { "nan" => double.NaN, "inf" => double.PositiveInfinity, "-inf" => double.NegativeInfinity, _ => double.Parse(text) }
    : value!.GetValue<double>();
int[] Ints(JsonNode? value) => value?.AsArray().Select(item => item!.GetValue<int>()).ToArray() ?? [];
var oracle = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "python-oracle.json")))!.AsObject();
foreach (var item in oracle["cases"]!.AsArray().Select(node => node!.AsObject()))
{
    var name = item["name"]!.GetValue<string>();
    var bindings = Bind(item["bindings"]!.AsObject());
    var state = new GlobalInputState { MouseObservationAvailable = item["mouseAvailable"]?.GetValue<bool>() ?? true };
    var levels = Ints(item["initial"]).ToHashSet();
    var snapshot = state.Reset(bindings, 1, levels);
    Check(snapshot.Held.SequenceEqual(Ints(item["initialHeld"])), name + "/initial-held");
    var dispatcher = new GlobalInputDispatcher(bindings);
    dispatcher.Reset(bindings, snapshot);
    dispatcher.SetPickerKeys(item["picker"]?.AsArray().Select(node => node!.GetValue<string>()) ?? []);
    var scope = new GlobalInputScope(42, true, 42, false, true, true);
    var index = 0;
    foreach (var step in item["steps"]!.AsArray().Select(node => node!.AsObject()))
    {
        var prefix = name + "/" + index++;
        if (step["kind"]!.GetValue<string>() == "mouseActivity")
        { state.ObserveMouseActivity((ushort)step["flags"]!.GetValue<int>()); continue; }
        if (step["scope"] is JsonObject change)
        {
            var updated = scope with {
                CurrentForegroundWindow = change["currentForeground"]?.GetValue<long>() ?? scope.CurrentForegroundWindow,
                GameWindowExists = change["gameValid"]?.GetValue<bool>() ?? scope.GameWindowExists,
                ForegroundIsAssistant = change["assistant"]?.GetValue<bool>() ?? scope.ForegroundIsAssistant,
                GameEnabled = change["gameEnabled"]?.GetValue<bool>() ?? scope.GameEnabled,
                ManagementEnabled = change["managementEnabled"]?.GetValue<bool>() ?? scope.ManagementEnabled,
                TextEntryActive = change["text"]?.GetValue<bool>() ?? scope.TextEntryActive,
                EditingBindings = change["editing"]?.GetValue<bool>() ?? scope.EditingBindings,
                GameEventsAfterSeconds = change["after"]?.GetValue<double>() ?? scope.GameEventsAfterSeconds };
            if (scope.TextEntryActive != updated.TextEntryActive || scope.EditingBindings != updated.EditingBindings)
            { state.CancelModifierTaps(); dispatcher.CancelModifierTaps(); }
            scope = updated;
        }
        var vk = step["vk"]!.GetValue<int>();
        var scan = step["scan"]!.GetValue<int>();
        var flags = step["flags"]!.GetValue<uint>();
        var down = step["down"]!.GetValue<bool>();
        var created = Number(step["created"]);
        // Native source creates finite timestamps; corrupt queued DTOs are separately rejected by dispatch.
        var edge = state.Transition(vk, scan, flags, down, step["foreground"]!.GetValue<long>(),
            double.IsFinite(created) ? created : 99.0, levels.Contains);
        var physical = GlobalInputState.PhysicalKey(vk, scan, flags);
        if (down) levels.Add(physical); else levels.Remove(physical);
        var expected = step["expectedEvent"] as JsonObject;
        Check((edge is null) == (expected is null), prefix + "/subscribed-event");
        if (edge is null || expected is null) continue;
        Check(edge.VirtualKey == expected["vk"]!.GetValue<int>()
            && edge.PhysicalVirtualKey == expected["physical"]!.GetValue<int>()
            && edge.Held.SequenceEqual(Ints(expected["held"]))
            && edge.ModifierTap == expected["tap"]!.GetValue<bool>(), prefix + "/python-state");
        Check(edge.PassedToSystem && !edge.Consumed, prefix + "/pass-through-contract");
        var actual = dispatcher.Dispatch(edge with { CreatedAtSeconds = created }, scope, 100.0);
        var wanted = step["actions"]!.AsArray().Select(node => node!.GetValue<string>());
        Check(actual.Actions.SequenceEqual(wanted) && actual.PickerKey == step["pickerKey"]?.GetValue<string>(), prefix + "/python-dispatch");
    }
}
foreach (var item in oracle["mouse"]!.AsArray().Select(node => node!.AsObject()))
{
    var flags = (ushort)item["buttonFlags"]!.GetValue<int>();
    var transitions = GlobalInputMouse.ButtonTransitions(flags);
    var expected = item["transitions"]!.AsArray().Select(node => (node![0]!.GetValue<int>(), node[1]!.GetValue<bool>()));
    Check(transitions.SequenceEqual(expected), "raw-mouse-buttons-" + flags);
    Check(GlobalInputMouse.PointerPhases(flags, (ushort)item["flags"]!.GetValue<int>(), item["x"]!.GetValue<int>(), item["y"]!.GetValue<int>())
        .SequenceEqual(item["phases"]!.AsArray().Select(node => node!.GetValue<string>())), "raw-mouse-phases-" + flags);
}

var unbound = new string[10]; Array.Fill(unbound, "");
var mapped = GlobalInputBindings.FromSettings("Control+g", unbound);
Check(mapped.Items[0] is { Name: "Ctrl+G", VirtualKey: 71, Modifiers: GlobalInputModifiers.Ctrl }, "uses-settings-grammar");
foreach (var (map, action, description) in new[] {
    ("F06", "F6", "same-vk-alias-conflict"), ("CTRL", "Ctrl+G", "bare-modifier-chord-conflict"),
    ("Ctrl+G", "Control+g", "same-chord-alias-conflict") })
{
    var actions = (string[])unbound.Clone(); actions[0] = action;
    var rejected = false;
    try { GlobalInputBindings.FromSettings(map, actions); } catch (ArgumentException) { rejected = true; }
    Check(rejected, description);
}
var badCount = false;
try { GlobalInputBindings.FromSettings("G", []); } catch (ArgumentException) { badCount = true; }
Check(badCount, "action-count-validated");
var brokenTail = new JsonArray(unbound.Select(value => (JsonNode?)JsonValue.Create(value)).ToArray());
brokenTail[9] = null;
var brokenDocument = new SettingsDocument(new JsonObject { ["hotkey"] = "G", ["actionHotkeys"] = brokenTail });
bool invalidTailRejected = false;
try { GlobalInputBindings.FromSettings(brokenDocument); } catch (ArgumentException) { invalidTailRejected = true; }
Check(invalidTailRejected && brokenDocument.Values["actionHotkeys"]![9] is null,
    "invalid-raw-picker-tail-cannot-silently-enable-projected-defaults");
brokenDocument.Set("actionHotkeys", new JsonArray(unbound.Select(value => (JsonNode?)JsonValue.Create(value)).ToArray()));
Check(GlobalInputBindings.FromSettings(brokenDocument).Items.Single(item => item.Action == "manual-map-picker").VirtualKey == 0,
    "explicit-empty-picker-repair-enables-validated-document");
var samePrimary = (string[])unbound.Clone(); samePrimary[0] = "Ctrl+G";
Check(GlobalInputBindings.FromSettings("G", samePrimary).Items.Count == 12, "distinct-modifier-chords-share-primary");

var fresh = GlobalInputBindings.FromSettings("G", unbound);
var sourceState = new GlobalInputState();
var oldSnapshot = sourceState.Reset(fresh, 1);
var route = new GlobalInputDispatcher(fresh); route.Reset(fresh, oldSnapshot);
var oldEdge = sourceState.Transition(71, 0, 0, true, 42, 99.0)!;
route.Reset(fresh, sourceState.Reset(fresh, 2));
Check(route.Dispatch(oldEdge, new(42,true,42,false,true,true),100.0).Actions.Count == 0, "old-generation-rejected");
var immutableHeld = oldEdge.Held.ToArray();
sourceState.Transition(71,0,0,false,42,99.1);
Check(oldEdge.Held.SequenceEqual(immutableHeld), "queued-held-snapshot-immutable");

// Production-linked native code: construction/configuration/stop/disposal only.
// The default branch never calls StartAsync: no hook, raw registration or window is created.
using (var native = new WindowsGlobalInput(fresh))
{
    Check(!native.IsRunning, "constructor-does-not-listen");
    var configured = await native.ConfigureAsync(mapped);
    Check(configured.Held.Count == 0 && !native.IsRunning, "configure-does-not-listen");
    native.SetPointerTarget(new(42, "owned-fixture-context", 90.0));
    Check(!native.IsRunning, "pointer-target-does-not-listen");
    Check((await native.StopAsync()).Stopped && !native.IsRunning, "stop-without-start-is-idempotent");
    Check((await native.StopAsync()).Stopped, "repeated-stop-without-start");
}
Check(Marshal.SizeOf<WindowsGlobalInputNative.RawMouse>() == 24
    && Marshal.OffsetOf<WindowsGlobalInputNative.RawMouse>("ButtonFlags").ToInt32() == 4
    && Marshal.OffsetOf<WindowsGlobalInputNative.RawMouse>("LastX").ToInt32() == 12, "raw-mouse-win32-abi");
Check(Marshal.SizeOf<WindowsGlobalInputNative.RawDevice>() == (IntPtr.Size == 8 ? 16 : 12)
    && Marshal.SizeOf<WindowsGlobalInputNative.RawHeader>() == (IntPtr.Size == 8 ? 24 : 16), "raw-device-header-win32-abi");
await OverflowDeliveryRegression.RunAsync(Check);
var drag=new MapDragMotion(500,500,0,0,1000,1000,System.Text.Json.JsonSerializer.SerializeToElement(new[]{.2,.2,.8,.8}));
Check(drag.Active && drag.Consume("move",510,490) is {DeltaX:10,DeltaY:-10,TotalX:10,TotalY:-10},"calibrated drag begins with physical deltas");
Check(drag.Consume("move",900,900) is {DeltaX:0,DeltaY:0} && drag.Consume("move",500,500) is {DeltaX:0,DeltaY:0},"calibrated leave/reentry never catches up");
Check(drag.Consume("up",503,504) is {DeltaX:3,DeltaY:4,TotalX:13,TotalY:-6,Finished:true} && !drag.Active && drag.Consume("up",503,504) is null,"calibrated release includes final delta once");
var freeDrag=new MapDragMotion(500,500,0,0,1000,1000);
Check(freeDrag.Active && freeDrag.Consume("up",1100,-100) is {DeltaX:600,DeltaY:-600},"uncalibrated held motion preserves original outside-client behavior");
Check(!new MapDragMotion(80,500,0,0,1000,1000).Active && !new MapDragMotion(500,130,0,0,1000,1000).Active
    && !new MapDragMotion(double.NaN,500,0,0,1000,1000).Active,"HUD boundary and nonfinite press grant no drag");
Console.WriteLine($"{checks} checks, {failures.Count} failures. Pure original-source sequence and production-linked no-start checks only; no native installation, OS events, GUI or game acceptance.");
return failures.Count == 0 ? 0 : 1;
