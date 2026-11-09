using System.Windows;
using StarMap.Core.Frames;
using StarMap.Core.Protocol;
using StarMap.Core.Recognition;
using StarMap.Core.Reliability;
using StarMap.Core.Session;

namespace StarMap.App;

/// <summary>
/// Phase-1 window shell. It does not install a keyboard hook and does not
/// set Handled on any key. System keys must keep reaching Windows.
/// See StarMap.Core.Input.HotkeyPolicy.
/// Status text is limited to this process: session, handshake, frame id, and
/// the slice hash. It does not read or display other windows' titles.
/// </summary>
public partial class MainWindow : Window
{
    private readonly SessionSequencer _session = new();
    private readonly ResultGate _gate;

    public MainWindow()
    {
        InitializeComponent();
        _gate = new ResultGate(_session);
        var snapshot = _session.Read();
        SessionText.Text = "会话 " + snapshot.SessionId + "  代际 " + snapshot.Generation;
        var reply = ProtocolHandshake.Exchange(new HandshakeHello(
            ProtocolHandshake.CurrentVersion,
            ProtocolHandshake.ComponentId,
            ProtocolHandshake.GridSliceCapability));
        HandshakeText.Text = reply.Accepted
            ? "协议 " + reply.ProtocolVersion + "  " + reply.Capability
            : "协议拒绝";
        ResultText.Text = "尚未加载录制帧。";
    }

    private void RunSlice(object sender, RoutedEventArgs args)
    {
        RecordedFrame frame;
        try
        {
            frame = RecordedFrame.Load(FrameBox.Text);
        }
        catch (InvalidOperationException)
        {
            ResultText.Text = "无法读取录制帧。";
            return;
        }

        SliceExecution execution;
        try
        {
            execution = SliceRunner.Run(frame, _session, _gate);
        }
        catch (InvalidOperationException)
        {
            ResultText.Text = "纵向切片没有完成。";
            return;
        }

        var snapshot = _session.Read();
        SessionText.Text = "会话 " + snapshot.SessionId
            + "  代际 " + snapshot.Generation
            + "  序号 " + snapshot.LastIssuedSequence;
        var result = execution.Result;
        ResultText.Text = string.Join(Environment.NewLine, new[]
        {
            "帧 " + frame.Id,
            "结果 " + result.Reason,
            "接受 " + execution.Disposition,
            "点数 " + result.GatePointCount + " / " + result.Rows.Length,
            "结构 " + SliceCanonical.StructureSha256(result),
        });
    }
}
