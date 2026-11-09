# 输入迁移回归

`create_python_oracle.py` 执行原 `client_native_keys.py` 的 `_transition_event` / `_modifier_mouse_activity`，以及原 `client_input.py` 的 `_key_event` 方法体与 `client_native_mouse.py` 的纯数据包函数，写出 `python-oracle.json`。替换边界是 QObject/Signal/mailbox 构造、OS 按键/窗口事实、单调时钟；不安装 hook，不注册 Raw Input，不创建 Qt GUI，也不运行游戏。时间、按键、窗口与编辑状态序列是独立给定的输入。

`Program.cs` 用同一输入比较 C# 的源事件 held/物理 VK/修饰键轻点、最终动作/选图键。另核对设置 grammar/冲突、代际失效和真实源文件链接的“构造/配置/Stop 不启动监听”、Raw Input ABI。根集成已裸构建并实际运行 406 项检查，0 失败；该计数包含生产 Channel 的溢出释放回归，不代表系统按键或游戏验收。

`OverflowDeliveryRegression` 用生产 `WindowsGlobalInputDelivery` 的真实 1024 容量 Channel、串行 dispatcher 和实际 `GlobalInputDispatcher`，阻塞已收到 down 的订阅者，填满 move 队列并让 release/cancel/Failed/Stopped 的同队列写入确实失败。它随后模拟原生 finally 的 Complete，核对 producer 不等订阅者、恢复后按 cancel → Failed → Stopped 收到唯一终态，以及 Stopped 的空 held snapshot 能清除丢失 release 遗留的状态。替换的只有原生安装/OS 事件生产与 cleanup 完成；没有 hook、实际输入或游戏。正常已经 up 的手势另查不会重复 cancel。

独立的 `--native-lifecycle` 入口会实际安装并正常注销原生键盘/Raw Input 三轮，再检查启动中停止。根集成已通过带正确 manifest 的 Windows apphost 实跑通过；默认回归不进入此分支。fixture 不注入键鼠、不激活窗口、不附加业务 dispatcher、不记录按键身份，不把安装释放结果称为系统按键穿透验收。

Windows 原生源只在显式 `StartAsync()` 安装 WH_KEYBOARD_LL；每条回调始终继续 `CallNextHookEx`。鼠标沿 Python 当前的 Raw Input，独立消息线程的不可见 `HWND_MESSAGE` 窗口只注册 `INPUTSINK`，不使用鼠标 hook、`NOLEGACY` 或 `CAPTUREMOUSE`；发现进程内已有鼠标 Raw Input 注册则拒绝抢占，键盘仍可用，单独修饰键轻点暂停。原生消息/释放失败通过带代际的通知及 Stop 回执保留；不强杀线程。

`ObservationReceived` 只在独立串行分发任务调用，显式订阅者负责将消息转给自己的线程。Started 的 snapshot 需传入 `GlobalInputDispatcher.Reset`；每个 Key 都应依源顺序传入 Dispatch，包含被拒绝的 releases。dispatch 时重新读取前台 scope，控制文本编辑、管理/游戏 intent；绑定或上下文切换须丢弃旧代际。文本编辑、快捷键录入与 intent 改变时，同时调用 source 与 dispatcher 的 `CancelModifierTaps`，即使这期间没有新的按键也要取消旧轻点。监听不默认准备本局、不执行采集/识别/游戏动作。构造与主 App 默认启动均不启用监听。

普通通知仍使用非阻塞 `TryWrite`，容量满即停止源。每个 run 另保留唯一的固定终态槽，既不扩展事件队列，也不在 hook 等待空位；唯一 dispatcher 排空已接收的数据后，按其真正交付过的最后手势补 cancel，再交付错误（如有）和 Stopped。Stopped 总带空 held snapshot，订阅者必须清除对应逻辑 held/tap 状态（可用 `GlobalInputDispatcher.Reset`），并撤销运行/界面意图；这不是模拟物理放键，不产生快捷键动作。永久不返回的订阅者仍会让 Stop 有界超时，线程不被强杀；恢复后的订阅者可读到终态，不依赖调用方当时是否正在等待 Stop。

Pointer 仅在调用方显式给出 `SetPointerTarget` 后产生；`ContextId` 指向调用方缓存的不可变 runtime 上下文，按压时捕获该 ID，后续事件保留它。同窗缓存刷新只影响下一次按压，不能破坏当前拖动；会话/楼层/资源失效时调用方必须先设 null，再提供新目标。目标改变、旧帧按压、过期数据包、失焦与停止会取消已有手势。坐标来自 `MSG.pt` 的物理屏幕像素；设备相对量仅判活动，不当作显示位移。未来 runtime 仍须验证上下文并消费，当前没有 UI/浮窗/地图动作订阅。

尚未证明：用户实际系统事件穿透、权限/DPI、消息循环失败、长期句柄释放及本局 UI 的业务消费。已通过的安装/注销和启动中停止只覆盖自有测试入口；纯算法和 ABI 检查不得称为全局键实机通过。

原生合同参考：[LowLevelKeyboardProc](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)、[Raw Input](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input)、[RegisterRawInputDevices](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerrawinputdevices)。
