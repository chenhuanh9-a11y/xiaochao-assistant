# 星图 C# / WPF 客户端

完整迁移正在独立分支实施，当前范围、真实入口、模块与验收状态见 [MIGRATION.md](MIGRATION.md)。WPF 测试版已接原资源查看、实验设置、手动窗口绑定、不吞键输入主机、S0 入口/条件/登记相似组判定、S1 结构候选，以及当前帧 Host/canonical/显示素材消费。稳态跟踪、组内成员事务、自动恢复与其他页面仍未完成。正式 Python/Qt 客户端保留。

SDK 固定 .NET 10.0.403，UI 源码采用 Material Design in XAML Toolkit 5.4.0；布局依据原 Qt 的实际页面与操作职责，构建及界面验证状态见迁移记录。组件、依赖与字体许可随输出携带。在 `src/StarMap.App` 实际目录执行 `dotnet build-server shutdown`，再执行裸 `dotnet build`。启动 `StarMap.Migration.exe`，可选 `--resource-root <现有资源目录>`、`--data-root <独立实验数据目录>`；不自动读取或写入原版玩家设置，不自动枚举/采集窗口或登记热键。默认数据目录为 `%LOCALAPPDATA%/StarMap.Migration`。

`StarMap.Runtime` 直接消费 `StarMap.Vision` 的当前帧楼层观察、S0 入口策略/Host Fit 和 S1 结构候选/目标层 Fit，WPF 通过 `SessionService` 发起明确操作。人工确认先锁定地图身份；实际 canonical 残差、资源基准与 Core 授权通过后才准备显示 material。真实原图回放始终标为 Recorded，不授予 LiveAuthorization。新的 Runtime/overlay 主机接线正在验证，不能把组件对照当实机验收。真实回放仅使用 2026-11-05 之后原始客户区。全局输入默认不启动；连接窗口后可显式启用，或点击“开始本局”尝试启用。准备本局本身不要求游戏存在，也不授予捕获权限。已接地图键、Escape、候选、楼层、本局动作和默认 F11 显隐；手动选图浮层和鼠标业务仍待接入。

主页显隐按钮和配置中的显隐快捷键共用即时入口，隐藏时保留当前材料并继续识别。只保存 `sheetVisibilityEnabled`，不会连带保存其他设置草稿；已有保存忙时不排队，较新的显隐选择保持未保存，可稍后显式保存。初次设置载入前不能切换，正常关闭会等待已接受的显隐写入。真实文件、未显示的 WPF 控件及替换 OS 输入源的默认 F11 调度已离线验证，真实全局按键与游戏浮层仍待验收。

用户明确授权后，可在 `tests/StarMap.Capture.Tests` 实际目录裸构建并用 `--watch-map --capture-window <当前枚举选中的句柄> --output <新目录> --label-file <标签JSON>` 启动独立只读采样。标签格式为 `{"season":"s0","mode":"nightmare","generation":1}`；支持 s0/s1 和 nightmare/difficult，切换组合必须递增 generation。采样器只响应目标前台的真实 G；每事件保留成功、关图、取消或失败结果，图片是完整原客户区，自动观察不等于身份/位姿验收。

`MAP_WATCH_READY` 表示原生观察器就绪；在输出目录创建 `stop.request` 后，等待 `MAP_WATCH_STOPPED` 和停止回执确认源与任务实际排空。`--watch-map-boundary` 是无 hooks 的输入边界检查；`--watch-map-storage-boundary --output <新目录>` 用合成 2×2 图和真实文件系统错误检查原图/记录/日志失败留证，均不代表游戏验收。样本、标签文件和采集回执只留本机，不能提交到 Git。

以下保留最初阶段一切片的设计范围；当前目标框架已统一更新为 .NET 10，它不是完整迁移完成声明。

## 最初阶段一

这是独立于现版 Python/Qt 的阶段一开始。现版仍是发布版。本目录不替换安装包，也不合并到 `main`。

已定：界面用 WPF；安装方式为自包含并捆绑 .NET 运行时；阶段一从这条纵向切片做起。WPF 工程只在 Windows 上构建。Linux 上构建并核对 `net10.0` 类库、命令行和严格对等。

仓库里没有可复用的 C# 网格实验源码（同轮报告只提交了摘要）。本切片按 `app/fast_structure_alignment.py` 的 `sample_edges` 与 `_grid` 逐字移植，不改阈值。`prepare`、`refine`、`propose` 的其余部分、身份、楼层和显示都不在本阶段。

## 热键

不吞系统键。`StarMap.Core.Input.HotkeyPolicy` 对任何虚拟键都返回“继续交给系统、不消费”。阶段一不安装 `WH_KEYBOARD_LL`，WPF 窗口也不注册 `InputBinding`，不把 `Handled` 设为 true。这与 `experiments/qt_quick/client_native_keys.py` 的 `CallNextHookEx` 契约一致：业务拒绝不能吃掉原键；局外地图键不因此开始本局。

## 状态写者

只有 `SessionSequencer` 写入会话号、代际和请求序号。界面、网格计算和 `ResultGate` 只读快照。序号只增不重置；发出下一请求会使上一序号过期；`Invalidate` 提升代际并作废当前请求。迟到、乱序、重复、超时和旧代际结果由 `ResultGate` 丢弃。超时先标记失效，再取消。`RestartBackoff` 只给出有上限的占位退避，不启动进程；关闭后或超过 5 次返回“不要重启”。

## Linux 上构建与核对

需要 .NET 10 SDK 与 NumPy 2.2.6（`requirements.txt`）。导入 `fast_structure_alignment` 时会加载 OpenCV，但本切片不调用它。

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
dotnet csharp/src/StarMap.Cli/bin/Release/net10.0/StarMap.Cli.dll --frame csharp/fixtures/prior-local
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
