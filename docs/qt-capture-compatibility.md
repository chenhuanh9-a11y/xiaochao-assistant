# Qt 画面采集的系统能力边界

安装器 `tools/qt-client.iss` 的 `MinVersion=10.0.17763` 不等于所有采集路径均可用。
普通客户区 DC 采集沿用原合同；本次没有提高安装门槛，也没有按系统版本禁用应用或采集。

完整客户区 DC 返回空白帧，或客户区 DC 抛出原生错误时，才会请求现有的安全屏幕回退。
该回退必须先在 Qt 所属线程排除本进程所有 Qt 窗口，再采集、恢复原排除值。
`WDA_EXCLUDEFROMCAPTURE` 需要 Windows 10 2004 / build 19041 或更高版本。
旧系统将该值按 `WDA_MONITOR` 处理，其空白窗口行为不能代替排除窗口，因此不能作为本程序的安全回退。
平台合同参考 [Microsoft SetWindowDisplayAffinity 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)。

| 条件 | 当前行为 |
| --- | --- |
| build 17763–19040，客户区 DC 成功 | 正常返回本次完整帧，不加载排除 API。 |
| build 17763–19040，需要屏幕回退 | 条件不支持：停止本次采集，返回 `capture-safe-fallback-unsupported`，不采屏、不隐藏/激活窗口、不禁用整个应用。 |
| build ≥19041，需要屏幕回退 | 仍须通过原排除事务、前台、尺寸、代次、取消与恢复校验；版本满足不代表每次原生调用都会成功。 |

错误类 `CaptureFallbackUnsupported` 保留稳定 `code`；原 DC 异常仍在异常链中。
provider 在 `capture_diagnostics` / `capture_trace` 提供以下无像素元数据：

- `captureCapability`：`safeFallback=unsupported`、系统 build、最低 build、排除模式、原因、`clientDc=failed`、`fallbackRequired=true`。
- `fallbackTrigger`：空白完整帧或原生 DC 错误的稳定原因、异常类型以及数值 `errno` / `winerror`；不复制任意原生错误文本。
- 本次客户区尺寸、屏幕边界与 DPI；既有 request/generation 校验不变。

provider 的 `snapshot.captureCapability` 表示本次绑定最近采集的状态；重新绑定/启用后 DC 状态回到 `not-probed`。
失败不会禁用 provider。后续普通 DC 成功会更新为 `clientDc=captured, fallbackRequired=false`，
保留已知的系统回退能力限制。此状态不能用作跳过任何捕获校验的授权。

运行时应保留 `captureCapability` / `fallbackTrigger` 到 receipt 和准备诊断；不可恢复的能力错误继承
`RuntimeError`，不进入只重试 `OSError` 的准备循环。中文最近问题明确说明此次客户区采集失败、
安全回退的版本条件以及普通客户区采集仍可重试，不把能力不足改写成准备超时。

验证入口为 `test_client_capture_compat.py` 及原 capture/exclusion/full 测试。
测试使用真实 provider、region pipeline、Qt owner 队列、runtime/preparation 和 ProblemsService/export；
系统版本查询、原生窗口/前台、像素及识别测量为明确注入的 fixture。
17763/19040/19041 是依赖注入边界测试，未在这些真实 OS 上安装运行；未采集游戏、注入输入、修改系统版本或打包。
