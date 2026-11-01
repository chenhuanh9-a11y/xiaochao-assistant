# Qt close lifecycle

The main window first rejects a close event while cancellable work stops and
accepted writes drain. `closeReady` then hides the modal progress popup and
allows the subsequent close event. Only then is `app.quit()` requested.
`quit()` is a request and can be vetoed; it is not an unconditional process exit.

Before the fix, `onClosing` rejected every event and the modal progress popup
stayed visible after all work finished. An isolated full-client run closed during
resource preparation emitted `quitRequested` but remained in the event loop until
the diagnostic's 12-second timeout. Every instrumented shutdown phase then
completed immediately. An in-memory restoration of the old handler and popup
also reproduced this with a synthetic accepted settings writer: the write
completed, active workers reached zero, but the event loop timed out at 1.806 s.
Thus the reproduced hang is a close-veto lifecycle bug, not a blocked writer.

Closing now stops foreground/tracking/context timers and input/capture first,
revokes display authorization, cancels pending requests, and polls owned pools
without blocking the UI thread. Accepted resource/settings/custom writes are not
abandoned. Backend pools also drain before final synchronous service teardown.
Late presentation/native callbacks and resource-initialization callbacks ignore
closing/closed instances; they cannot re-enable observation or reopen overlays.

Verification on PySide6 6.11.1, offscreen/software rendering:

- Four actual QWindow-close subprocess cases pass: idle, cancellable worker,
  accepted settings write, accepted resource write. Both accepted writes persist;
  a heartbeat verifies the event loop responds while waiting. Total 1.532 s.
- Twelve coordinator contract checks pass, including late callbacks during close.
- Full client closed during preparation exits normally in 0.669 s including startup.
- Full client idle close exits normally (6.731 s includes resource preparation
  before the test requests close). All instrumented service shutdowns complete.

These checks use only owned isolated instances and synthetic writers. They do not
start native capture/hotkey polling, inspect or focus the user's game, modify an
installed copy, or certify every possible in-game shutdown condition. Private
probe logs live under ignored `work/qt-evidence/`.

Official references used for this implementation:

- [QCoreApplication.quit](https://doc.qt.io/qt-6/qcoreapplication.html#quit):
  the application or windows may reject the quit request.
- [Window.closing](https://doc.qt.io/qt-6/qml-qtquick-window.html#closing-signal):
  accept the event only when it is safe to close.
- [QThreadPool.waitForDone](https://doc.qt.io/qt-6/qthreadpool.html#waitForDone-1):
  waits for all threads; asynchronous drain keeps that wait out of the UI path.
