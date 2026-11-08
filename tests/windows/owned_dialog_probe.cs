// Synthetic capability proof only. Never accepts a target PID, HWND or Setup path.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Forms;

public static class OwnedDialogProbe
{
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    sealed class Identity
    {
        public int Pid;
        public long Created;
        public string Image, ScriptHash, SourceHash, Marker;
        public IntPtr Handle;
        public Identity Copy() { return (Identity)MemberwiseClone(); }
    }
    sealed class Fixture
    {
        public Form Window;
        public Identity Identity;
        public string Marker = "owned-probe-" + Guid.NewGuid().ToString("N");
        public long Deadline;
        public int Confirmed, Cancelled, Attempts;
        public string State = "open";
    }
    static string script, source;
    static Form host, decoy;
    static Thread ui;
    static readonly ManualResetEvent ready = new ManualResetEvent(false);
    static readonly Dictionary<IntPtr, Fixture> owned = new Dictionary<IntPtr, Fixture>();
    static readonly HashSet<IntPtr> consumed = new HashSet<IntPtr>();
    static Exception uiFailure;
    static long Now { get { return Stopwatch.GetTimestamp(); } }
    static string Hash(string path)
    {
        using (var sha = SHA256.Create())
        using (var input = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(input));
    }
    static Identity Current(Fixture f)
    {
        using (var p = Process.GetCurrentProcess())
            return new Identity { Pid = p.Id, Created = p.StartTime.ToUniversalTime().Ticks,
                Image = p.MainModule.FileName, ScriptHash = Hash(script), SourceHash = Hash(source),
                Handle = f.Identity == null ? f.Window.Handle : f.Identity.Handle, Marker = f.Marker };
    }
    static void Assert(bool result, string reason)
    {
        if (!result) throw new InvalidOperationException(reason);
    }
    static void OnUI(Action action) { host.Invoke(action); }
    static Fixture Make(int lifetimeMs)
    {
        var f = new Fixture();
        OnUI(delegate {
            f.Window = new Form { Text = f.Marker, Name = f.Marker, Width = 360, Height = 140 };
            var yes = new Button { Name = f.Marker + "-confirm", Text = "Confirm synthetic",
                AccessibleName = f.Marker + "-confirm", Left = 10, Top = 20, Width = 140 };
            var no = new Button { Name = f.Marker + "-cancel", Text = "Cancel synthetic",
                AccessibleName = f.Marker + "-cancel", Left = 170, Top = 20, Width = 140 };
            yes.Click += delegate { Apply(f, "confirm"); };
            no.Click += delegate { Apply(f, "cancel"); };
            f.Window.Controls.Add(yes); f.Window.Controls.Add(no);
            f.Deadline = Now + (long)(Stopwatch.Frequency * (lifetimeMs / 1000.0));
            f.Window.Show();
            f.Identity = Current(f);
        });
        owned.Add(f.Identity.Handle, f);
        return f;
    }
    // UI-thread final consumer rejects both queued duplicates and expired callbacks.
    static void Apply(Fixture f, string action)
    {
        f.Attempts++;
        if (f.State != "open") return;
        if (Now >= f.Deadline) { f.State = "expired"; return; }
        f.State = action;
        if (action == "confirm") f.Confirmed++; else f.Cancelled++;
    }
    static void Validate(Identity requested)
    {
        Fixture f;
        Assert(owned.TryGetValue(requested.Handle, out f), "unowned-handle");
        var actual = Current(f);
        Assert(requested.Pid == actual.Pid && requested.Created == actual.Created &&
            requested.Image == actual.Image && requested.ScriptHash == actual.ScriptHash &&
            requested.SourceHash == actual.SourceHash && requested.Marker == actual.Marker,
            "identity-mismatch");
        Assert(!consumed.Contains(requested.Handle), "already-consumed");
        bool open = false;
        OnUI(delegate { open = f.State == "open" && !f.Window.IsDisposed; });
        Assert(open, "closed");
        uint pid;
        Assert(GetWindowThreadProcessId(requested.Handle, out pid) != 0 && pid == actual.Pid,
            "native-owner-mismatch");
        Assert(Now < f.Deadline, "expired");
    }
    static InvokePattern Resolve(Identity id, string action)
    {
        Assert(action == "confirm" || action == "cancel", "unknown-action");
        Validate(id); // Before any UIA access. No RootElement, enumeration or fallback.
        var root = AutomationElement.FromHandle(id.Handle);
        Assert(root.Current.ProcessId == id.Pid && root.Current.NativeWindowHandle == id.Handle.ToInt32()
            && root.Current.Name == id.Marker, "window-mismatch");
        var matches = root.FindAll(TreeScope.Children, new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
            new PropertyCondition(AutomationElement.NameProperty, id.Marker + "-" + action)));
        Assert(matches.Count == 1, "nonunique-control");
        var button = matches[0];
        Assert(button.Current.ProcessId == id.Pid && button.Current.IsEnabled, "control-mismatch");
        object pattern;
        Assert(button.TryGetCurrentPattern(InvokePattern.Pattern, out pattern), "no-invoke-pattern");
        return (InvokePattern)pattern;
    }
    static InvokePattern Invoke(Identity id, string action)
    {
        var pattern = Resolve(id, action);
        Validate(id); // Revalidate after UIA lookup, immediately before consuming authority.
        consumed.Add(id.Handle); // Never retry uncertain UIA invocation.
        pattern.Invoke();
        return pattern;
    }
    static void Rejected(Identity id, string expected)
    {
        try { Invoke(id, "confirm"); }
        catch (InvalidOperationException e) { Assert(e.Message == expected, "unexpected-rejection"); return; }
        throw new InvalidOperationException("operation-not-rejected");
    }
    static void Counts(Fixture f, int yes, int no, int attempts)
    {
        // UIA Invoke may post asynchronously. Poll only owned state with a strict bound.
        long end = Now + 2 * Stopwatch.Frequency;
        bool match = false;
        do {
            OnUI(delegate { match = f.Confirmed == yes && f.Cancelled == no && f.Attempts == attempts; });
            if (match) return;
            Thread.Sleep(20);
        } while (Now < end);
        throw new InvalidOperationException("unexpected-action-count");
    }
    static void Tests()
    {
        var confirm = Make(20000);
        Action<Action<Identity>> bad = mutate => {
            var id = confirm.Identity.Copy(); mutate(id); Rejected(id, "identity-mismatch");
        };
        bad(id => id.Pid++); bad(id => id.Created++); bad(id => id.Image += ".wrong");
        bad(id => id.ScriptHash = "wrong"); bad(id => id.SourceHash = "wrong");
        bad(id => id.Marker += "-wrong");
        var unknown = confirm.Identity.Copy();
        OnUI(delegate { unknown.Handle = decoy.Handle; });
        Rejected(unknown, "unowned-handle");
        unknown.Handle = IntPtr.Zero; Rejected(unknown, "unowned-handle");
        Counts(confirm, 0, 0, 0);
        Console.WriteLine("PASS identity mismatches and unknown handles rejected before UIA");
        var cached = Invoke(confirm.Identity, "confirm");
        Counts(confirm, 1, 0, 1);
        Rejected(confirm.Identity, "already-consumed");
        // Explicit owned synthetic callback replay tests the final consumer too.
        cached.Invoke(); Counts(confirm, 1, 0, 2);
        Console.WriteLine("PASS explicit confirm exactly once; duplicate rejected/ignored");
        var cancel = Make(10000);
        Invoke(cancel.Identity, "cancel"); Counts(cancel, 0, 1, 1);
        Rejected(cancel.Identity, "already-consumed");
        Console.WriteLine("PASS explicit cancel; no confirmation");
        var late = Make(3000);
        var stale = Resolve(late.Identity, "confirm");
        while (Now < late.Deadline) Thread.Sleep(20);
        Rejected(late.Identity, "expired");
        stale.Invoke(); Counts(late, 0, 0, 1);
        OnUI(delegate { Assert(late.State == "expired", "late-callback-not-observed"); });
        Console.WriteLine("PASS expired action rejected; late callback ignored; no auto-confirm");
        var closed = Make(10000);
        Resolve(closed.Identity, "confirm");
        OnUI(delegate { closed.State = "closed"; closed.Window.Close(); });
        Rejected(closed.Identity, "closed"); Counts(closed, 0, 0, 0);
        Console.WriteLine("PASS closed fixture rejects late action before UIA");
    }
    static int Execute()
    {
        try {
            ui = new Thread(delegate() {
                try {
                    host = new Form { Text = "Synthetic probe host", ShowInTaskbar = false };
                    decoy = new Form { Text = "Synthetic unregistered decoy", ShowInTaskbar = false };
                    host.Shown += delegate { decoy.Show(); ready.Set(); };
                    Application.Run(host);
                } catch (Exception e) { uiFailure = e; ready.Set(); }
            });
            ui.SetApartmentState(ApartmentState.STA); ui.IsBackground = true; ui.Start();
            Assert(ready.WaitOne(5000) && uiFailure == null, "fixture-start-failed");
            Tests();
            Console.WriteLine("PASS synthetic-only UIAutomation probe; Setup/game/UAC NOT tested");
            return 0;
        } catch (Exception e) {
            // Do not dump exception text which could include external UI/provider contents.
            Console.Error.WriteLine("FAIL synthetic probe: " + e.GetType().Name);
            return 1;
        } finally {
            if (host != null && host.IsHandleCreated && uiFailure == null) {
                OnUI(delegate {
                    foreach (var f in owned.Values) f.Window.Dispose();
                    decoy.Dispose(); host.Close();
                });
                Assert(ui.Join(3000), "fixture-cleanup-timeout");
            }
        }
    }
    public static int Run(string scriptPath, string sourcePath)
    {
        script = Path.GetFullPath(scriptPath); source = Path.GetFullPath(sourcePath);
        int code = 1;
        // UIA on a dedicated MTA thread, never the WinForms UI thread.
        var controller = new Thread(delegate() {
            try { code = Execute(); } catch { code = 1; }
        });
        controller.SetApartmentState(ApartmentState.MTA); controller.IsBackground = true;
        controller.Start();
        if (!controller.Join(45000)) {
            Console.Error.WriteLine("FAIL synthetic probe: hard timeout; no confirmation fallback");
            // Terminates only this explicitly launched PowerShell test process and its own UI.
            Environment.Exit(124);
        }
        return code;
    }
}
