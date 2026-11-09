# WPF input host regression

Run on Windows from this directory with `dotnet run` after the coordinator's serial bare build.
No hook is installed and no physical input is injected. The only replacement is
`IWpfGlobalInputSource`'s native installation / OS event production. Tests link the real
`GlobalInputState`, `WindowsGlobalInputDelivery`, `GlobalInputDispatcher` and WPF host;
the actual WPF dispatcher runs on an STA thread.

Coverage includes source-ordered Started/configuration (including delayed native results),
held seeds/repeat/release, scope-read failure, reused-HWND binding generations, text/edit
transitions, clean modifier taps, picker precedence, existing management/F9 ownership,
pending-operation cancellation versus running-callback drain, real 1024-slot backpressure
and terminal reset, and explicit pointer context invalidation.

This is an offline UI-dispatch regression. It does not prove native-hook installation,
game behavior, input permissions, capture, recognition, or player acceptance.

Host integration: construct on the UI dispatcher with a read-only context factory and
business callback. Construction/configuration do not start listening. Await `StartAsync`,
`ConfigureAsync`, `StopAsync` and `DisposeAsync`; do not block the UI waiting for
Start/Configure. Start/Configure return only after their **source-ordered Started** has
reset policy. Never reset from their returned snapshots. The source serial subscriber
blocks on one cancellable DispatcherOperation; no extra input queue exists in the host.
Stop invalidates permission and cancels pending UI work immediately, then waits for the
source and any running callback. Close must await successful Stop/Dispose before shutting
down the dispatcher. Stop timeout keeps restart blocked until source cleanup finishes.

Context contains existing verified bound HWND facts and `BoundWindow.Generation`, explicitly
granted game/management listening intents, text/edit guards, current visible picker keys
and optionally a cached pointer target. Call `RefreshScope()` immediately after these
change; no input edge is required to revoke modifier taps or a pointer gesture. It does
not change bindings or start a round. Only `delivery.Dispatch` and `delivery.Pointer`
authorize business work; `delivery.Notification` retains original diagnostic metadata.
The UI business owner retains original map-phase/round/session semantics. The assistant-
foreground F9 application-local owner remains the UI's responsibility; global policy
intentionally suppresses that duplicate owner.
