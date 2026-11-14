# 星图 C# 阶段一骨架

这是独立于现版 Python/Qt 的阶段一开始。现版仍是发布版。本目录不替换安装包，也不合并到 `main`。

已定：界面用 WPF；安装方式为自包含并捆绑 .NET 运行时；阶段一从这条纵向切片做起。WPF 工程只在 Windows 上构建。Linux 上构建并核对 `net8.0` 类库、命令行和严格对等。

仓库里没有可复用的 C# 网格实验源码（同轮报告只提交了摘要）。本切片按 `app/fast_structure_alignment.py` 的 `sample_edges` 与 `_grid` 逐字移植，不改阈值。`prepare`、`refine`、`propose` 的其余部分、身份、楼层和显示都不在本阶段。

## 热键

不吞系统键。`StarMap.Core.Input.HotkeyPolicy` 对任何虚拟键都返回“继续交给系统、不消费”。阶段一不安装 `WH_KEYBOARD_LL`，WPF 窗口也不注册 `InputBinding`，不把 `Handled` 设为 true。这与 `experiments/qt_quick/client_native_keys.py` 的 `CallNextHookEx` 契约一致：业务拒绝不能吃掉原键；局外地图键不因此开始本局。

## 状态写者

只有 `SessionSequencer` 写入会话号、代际和请求序号。界面、网格计算和 `ResultGate` 只读快照。序号只增不重置；发出下一请求会使上一序号过期；`Invalidate` 提升代际并作废当前请求。迟到、乱序、重复、超时和旧代际结果由 `ResultGate` 丢弃。超时先标记失效，再取消。`RestartBackoff` 只给出有上限的占位退避，不启动进程；关闭后或超过 5 次返回“不要重启”。

## Linux 上构建与核对

需要 .NET 8 SDK 与 NumPy 2.2.6（`requirements.txt`）。导入 `fast_structure_alignment` 时会加载 OpenCV，但本切片不调用它。

```bash
dotnet build csharp/StarMap.Linux.sln -c Release
dotnet run --project csharp/tests/StarMap.Core.Tests/StarMap.Core.Tests.csproj -c Release --no-build
python3 tools/phase1/compare_grid_slice.py
python3 tools/phase1/identify_resources.py
python3 tools/phase1/measure_publish.py
```

`StarMap.sln` 含 WPF。在 Linux 上构建 `StarMap.App` 会停在 `NETSDK1100`。不要为了在 Linux 上编过而打开 `EnableWindowsTargeting`。

对等比较调用真实的 Python `sample_edges` / `_grid`，与 C# 的结构字节 SHA-256 逐字节一致，不设分数容差。三份合成帧再加一次 `--cancel`。合成帧在 `csharp/fixtures/`，由 `python3 tools/phase1/write_synthetic_frames.py` 重写；它们不是游戏画面。

命令行只打印帧标识、哈希和序号，不打印帧路径。

```bash
dotnet csharp/src/StarMap.Cli/bin/Release/net8.0/StarMap.Cli.dll --frame csharp/fixtures/prior-local
```

## Windows 上尚未做的事

在 Windows 上构建完整解决方案，并发布自包含、不裁剪的 WPF 壳：

```powershell
dotnet build csharp/StarMap.sln -c Release
dotnet publish csharp/src/StarMap.App/StarMap.App.csproj -c Release -r win-x64 --self-contained true -p:PublishTrimmed=false -p:PublishSingleFile=false
```

阶段一不制作安装器。体积和冷启动在 Linux 上记为未知；Windows 验收必须单独做，不能把进程刚起来当成开屏到可见像素。详见 `docs/csharp-phase1-packaging.md` 与 `docs/csharp-phase1-verification.md`。

## 本阶段明确不做

不改 Python 识别、阈值或现版热键。不读取其他窗口标题、用户名或其他程序信息。不自动删除玩家资料。不把这条网格切片说成完整客户端或实机通过。
