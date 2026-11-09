# Window WGC 单帧客户区后端

使用系统 Windows SDK 的 WinRT/CsWinRT projection；没有 Windows App SDK、SharpDX、Vortice 或额外 NuGet。App 与引用 App 的测试需采用 `net10.0-windows10.0.19041.0`，本轮锁 `WindowsSdkPackageVersion=10.0.19041.57`。该 SDK pack 的 Windows SDK 组件按 Microsoft Windows SDK 许可分发，CsWinRT runtime 为 MIT；由既有 .NET SDK targeting/发布流程提供，不把编译 TFM 当成运行时支持证明。

`WindowsGraphicsCaptureBackend` 构造不探测或采集。只有显式 `CaptureAsync(BoundWindow, timeout, token)` 才建立一次 HWND CaptureItem、D3D11 device、free-threaded frame pool 与 session。OS/API/IsSupported 探测不通过、互操作不支持、device lost、窗口关闭、几何改变、超时或取消均返回明确失败；不会打开 picker、请求额外权限、改变安全设置、激活窗口或建立后台常驻 session。失败由 Root 决定是否显式采用现有 GDI 候选，没有 screen fallback；没有 owner exclusion 事务时更不捕获桌面。

每次从当前绑定窗口重新读取客户区完整尺寸、ClientToScreen、DPI、GetWindowRect 和 DWM extended frame bounds，并在像素复制与统计后复核全部身份/几何。只在 ContentSize 与实测 DWM 或 WindowRect 尺寸完全一致、原点唯一、完整客户区位于该 surface 内时裁剪；没有固定边框/标题栏补偿，不缩放。SoftwareBitmap deep-copy 保留原 BGRA8/alpha，按整行字节裁剪；输出包含 ContentSize、surface 大小、裁剪矩形/依据、前后几何、各采样 QPC 及 compositor 相对时间的 TimeSpan ticks。后者固定为 100ns，不能直接当作 QPC 原始计数。该后端保留 SDR BGRA8 合同，不声称保留 HDR。

Root 继续拥有当前 BoundWindow/generation 与最终提交验证。后端重复核对 HWND、PID、线程、进程启动与窗口类；返回 raw BGRA 和 metadata，不自行识别、更新会话或显示图像。持久同进程/同线程/同窗口类的 HWND 重用仍需 Root 的绑定生命周期处理。

FrameArrived 使用专有 gate 只移交一帧；取消/结束先关闭接收、回收未领取帧，再解绑事件并关闭 session、pool、D3D device。已领取帧在其所有权范围内关闭，完成复制后再检查取消与窗口状态，迟到回调不能移交新帧。GraphicsCaptureItem 没有 IClosable；事件解绑后其 CsWinRT RCW 由 runtime 管理，手动 ABI/factory/HSTRING/原生 D3D 引用均有对应释放。超时涵盖等待帧与异步软件复制，原生同步创建/关闭调用不能被强制中断；不强杀 worker。

根任务串行执行：

- 默认 `dotnet run --project csharp/tests/StarMap.Wgc.Tests`：纯实测几何公式、原始 BGRA 裁剪与 no-start guard；没有显示窗口或 WGC session。
- 显式 `dotnet run --project csharp/tests/StarMap.Wgc.Tests -- --owned-fixture --output <新目录>`：实际窗口 WGC、原生 BGRA、三个建销轮次、已取得真实 frame 后取消再恢复、取得 frame 后关闭自有窗口。只显示 ShowActivated=false 的自有四颜色窗口，不操作游戏或用户窗口，不注入输入。

fixture 的 frame-owned observer 仅在内部自有窗口入口可用，用于精确安排取消/关自有窗口；实际 CaptureItem/device/frame pool/session/surface/software-copy/复核流程不替换。unsupported 返回 exit 2，不当作通过或伪造颜色帧。保存的 PNG 和 metadata 用于核对实际客户区边缘、ContentSize 与 crop 来源，禁止用旧游戏帧代替本次证据。11/09 根集成已裸构建，13 项默认纯检查通过；`wgc-owned-fixture-03-software` 的实际 WGC 12 项全通过，`03-default` 仍是 exit 1（先白帧颜色失败，后黑帧被拒绝）。独立回读四组完整 client 字节与对应 surface 裁剪完全一致；这些证据不代表默认硬件呈现或游戏采集通过。

白帧调查版保持原 ShowActivated=false、Topmost=false、WGC readback/crop 不变。每轮断言前先保存 `owned-surface-NN.bgra/.png`（实际 SoftwareBitmap readback、裁剪前）与 `owned-client-NN.bgra/.png`（实际返回字节）。第一轮保存后，再对同一自有 HWND 做独立 client-DC 读回，最后做 WPF visual 离屏渲染并记录实际布局/颜色；顺序和不同采样时刻写入 diagnostics，离屏渲染不能代替 HWND 呈现证据。颜色失败仍记录 false 并保留 exit 1，只继续收集三轮，不能用后续成功抵销首轮失败。没有增加重试或改变生产首帧接受路径。

独立反事实命令为 `StarMap.Wgc.Tests.exe --owned-fixture --software-render --output <新目录>`。只有该显式 flag 才在 standalone fixture 进程创建 WPF Application 前设置 `RenderOptions.ProcessRenderMode=SoftwareOnly`；默认命令保持原行为，不修改产品 App、用户窗口、WGC device/readback/crop、颜色检查或 fallback。回执记录请求模式、进程/target preference、RenderCapability.Tier（这是能力，不冒称当前实际 GPU 管线）、SourceInitialized/Loaded/ContentRendered 与 capture QPC。若 software 模式使同一 WGC 路径获得正确颜色，证据范围是原 fixture 的默认 WPF 原生呈现问题；不能把该结果当作默认渲染、游戏硬件帧或完整产品采集验收。依据：[微软 WPF render-thread 排查](https://learn.microsoft.com/en-us/troubleshoot/developer/dotnet/framework/general/wpf-render-thread-failures)、[ProcessRenderMode](https://learn.microsoft.com/en-us/dotnet/api/system.windows.media.renderoptions.processrendermode)。

参考：[WinRT desktop TFM](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/desktop-to-uwp-enhance)、[CreateForWindow](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow)、[CreateFreeThreaded](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.direct3d11captureframepool.createfreethreaded?view=winrt-26100)、[SoftwareBitmap deep copy](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.imaging.softwarebitmap.createcopyfromsurfaceasync?view=winrt-26100)、[CopyToBuffer](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.imaging.softwarebitmap.copytobuffer?view=winrt-26100)、[D3D11CreateDevice](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-d3d11createdevice)、[CsWinRT marshal implementation](https://github.com/microsoft/CsWinRT/blob/master/src/WinRT.Runtime/Marshalers.cs)。
