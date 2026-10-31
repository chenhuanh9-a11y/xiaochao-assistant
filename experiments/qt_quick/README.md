# 星图 Qt Quick 实验

独立 PySide6 6.11.1 / QML 原型，基于 `3438365b4cef003f76d08a001f772c888156bd03`。
现有 Tk 入口、识别算法、用户配置和安装目录都不改。尚未生成安装器，不是正式客户端。

## 运行

在仓库根目录，使用 Python 3.13（64 位）：

```powershell
python -m venv .venv-qt-isolated
.\.venv-qt-isolated\Scripts\python.exe -m pip install -r experiments/qt_quick/requirements.txt
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/seed_sandbox.py '包含 _internal 的本地资源来源目录'
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/main.py
```

`seed_sandbox.py` 将 `assets` / `references` 与兼容性元数据复制到
`work/qt-lab-installation`。目标必须是本克隆 `work/` 内的新目录，已有目标会拒绝覆盖。
源目录只读。启动时必须存在实验所有权标记；程序不会寻找或修改已安装客户端的目录。
源码仓库没有附带作者图片，运行者需要已有且有权使用的本地资源。

本次工作区的实验环境和副本已备好，直接运行上面的最后一条命令即可。

本机用户也可直接双击 `experiments/qt_quick/Launch-QtLab.cmd`。
`Launch-QtLab.cmd check` 只检查依赖/资源，不创建窗口；`Launch-QtLab.cmd replay`
打开已准备的隔离识别结果回放。启动脚本固定使用本克隆的 `.venv-qt-isolated`，
不会启动系统 Python、安装依赖或请求管理员权限。

## 已实现

- 复用 `app/map_resource_context.py` / `MapResourceModel`；后台读取两种模式的真实清单，缺失资源明确显示。
- 路线、楼层、名称搜索；当前隔离副本的爱吃醋路线有 72 张图层。异步缩略图按显示尺寸解码。
- GridView 按需创建并复用卡片；稳定 ID 增量协调，不变刷新无模型变更，保留选择和滚动。
- 当前上下文和任务代次校验；取消旧读取，晚到结果不能覆盖新模式。
- 文件选择确认即自动校验、导入、刷新；复用已有 SHA256 / 依赖校验和事务写入。普通导入无第二次确认；确需覆盖时显示所属窗口确认框。结果在状态栏显示。
- 透明、置顶、拖动、展开/收起悬浮预览；面板和图片透明度独立。鼠标穿透由主窗口控制；重显保留其状态。
- 悬浮窗口使用 Qt `WindowDoesNotAcceptFocus` 和现有 Win32 样式契约 `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`。透明、穿透和不激活分别处理。

## 业务边界

默认悬浮窗明确标记“浏览预览 / 未连接识别”，显示用户选择的精确图层。
不截屏、不访问游戏、不创建键盘钩子、不运行另一套识别或全局快捷键监听。
Qt 的应用内键盘事件也没有被当作全局快捷键验证。

`replay_adapter.py` 消费现有 `RoundPose.snapshot()` 数据格式，并调用同一
`RoundPose.pose_for_floor()`，不复制贴合算法。现已另接保存结果到 Qt 渲染：
`prepare_replay.py` 读取已有生产调用方输出，核对成功提交、原始输入 SHA、
作者/地图/楼层的精确资源绑定和历史渲染尺寸，复制到隔离 `work/qt-replay`。
`ReplayCanvas.qml` 对保存的客户端帧按原始尺寸应用记录中的显示矩阵，支持透明度拖动、
窗口缩放、切页保留和悬浮查看。回放载入在后台校验图片哈希，晚到结果不能覆盖新回放。

本机备好的 `work/qt-replay/known-production/replay.json` 来自已有生产算法的成功输出，
**输入是明确记录的合成帧，不是新的实机游戏验收**。没有重跑识别器、编造位姿或手调矩阵，
也没有用历史成功给当前游戏授权。界面标明“生产算法结果 · 合成输入回放”。
实机日志的成功摘要缺少完整成功位姿/逐帧数据，未据此伪造实机回放。
完整迁移仍需接现有实时结果授权/生命周期，保持两模式共享算法。

```powershell
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/prepare_replay.py '已有生产调用方 result.json' --input-kind synthetic
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/main.py --replay work/qt-replay/known-production/replay.json
```

回放输入与作者图片全部是本地副本，不随实验源码分发。`prepare_replay.py` 的目标必须是新目录；
只有来源确为实录输入时才使用 `--input-kind recorded`，未知来源保持 `unverified`。

## 有界验证

```powershell
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/main.py --probe-output work/qt-evidence/local
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/check_backend.py --archive '已有图包.zip'
```

第一条约 9 秒，只向本进程窗口派发 QtTest 事件，不移动系统鼠标或操作游戏。
第二条校验真实资源和可选真实 ZIP，只写实验副本；位姿输入为明确标注的合成数据。
覆盖确认分支使用现有导入器，当前真实包样本验证的是相同内容的导入；
未把这次结果扩大为所有版本升级、断电恢复、原生文件对话框或实机快捷键验证。

最新交互证据：`work/qt-evidence/replay-added-browse/`，16 项断言通过、QML 警告为零。
真实 ZIP / 后台检查：`work/qt-evidence/backend-checks.json`，11 项通过。
回放渲染证据：`work/qt-evidence/replay-render/`，10 项通过、QML 警告为零；
确认了 27,512 像素随路线显隐变化，记录矩阵到场景映射误差约 0.000053 客户端像素。
这验证保存结果呈现，不等于与原 CPU 渲染逐像素一致。
截图包含已有作者图，仅保存在被忽略的本地目录，不随源码提交或公开。

本机 Windows 11 / D3D11 / Qt 6.11.1，最新约 9.17 秒浏览短流程：

| 指标 | p50 | p95 | 最大 |
| --- | ---: | ---: | ---: |
| 界面 8ms 计时器额外延迟 | 0ms | 1.061ms | 92.155ms |
| 进程 RSS，32 次采样 | 181.660MiB | 206.125MiB | 206.902MiB |

计时器延迟是事件循环响应的代理指标，不是点击到显示的端到端延迟。
帧信号间隔受运行环境、遮挡与调度影响；没有显示器呈现时间，不能换算成“真实 FPS”。
本次主窗帧信号只有 2 个有效间隔，不作为帧表现证据；悬浮样本包含停驻和折叠间隔，
不作稳态 FPS 证据。最大额外延迟仍有约 92ms，尚不能宣称完全无卡顿。
回放检查约 4.4 秒，RSS 最高采样约 219MiB；其 501ms 计时器峰值包含截图和数组比较，
不拿它评估正常交互性能。前一版约 51ms 峰值与本次不是严格受控前后比较。
没有同机同操作的 Tk 基线、游戏窗口验证、长时内存验证或打包后测量。

## 部署判断

值得继续分阶段迁移：现有纯 Python 资源模型和位姿状态接口可以复用，Qt 承担显示和交互。
保存结果渲染已接通；接下来验证实录帧序列和实时显示授权、全局快捷键隔离，再考虑替换正式入口。

依赖并不轻：本机 `PySide6-Essentials` 安装文件约 204.9MiB、shiboken 约 3.0MiB；
这包含未裁剪的 Essentials 模块，既不是增量 RSS，也不是 Setup 的大小。
完整产品仍需 Python / NumPy / OpenCV 等，后续要按实际 QML 导入裁剪和构建 Setup 测量，
不以 Qt 空壳体积推断最终包大小。许可证和部署边界见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 可选本地时序诊断

默认关闭。排查开关地图、楼层延迟时，可先正常退出星图，在已获管理员权限的 PowerShell
中仅为这次启动设置环境变量（将路径换成实际安装路径）：

```powershell
$env:XINGTU_LIFECYCLE_TRACE = '1'
& 'D:\Program Files\XiaochaoAssistant\星图.exe'
Remove-Item Env:\XINGTU_LIFECYCLE_TRACE
```

重复启动只唤回已有实例，不会给它启用诊断；无需修改配置或系统环境变量。
下次正常启动即关闭。沿产品单实例入口运行；开发副本须使用独立 sandbox。

文件位于既有运行日志所在的 `work/nightmare-live/` 目录：`lifecycle-detail.jsonl`
和一份 `lifecycle-detail-previous.jsonl`，各最多约 1 MiB。仅记录映射后的地图操作来源、
单调时钟、地图存在判定、楼层检测耗时、队列/结果接受拒绝和图片显示状态。
困难模式的地图与楼层由同一观察器检测，标为 `combinedSensor`，不虚构单独楼层耗时。
运行事件的 `producedAt`、界面接收的 `deliveryMs`、同 token 的展示/图片 ready 时间
可分辨计算、排队和发布延迟；图片 ready 不等于屏幕实际呈现时间。

不记录截图、录屏、原始按键、输入文字、鼠标轨迹、窗口标题或异常原文，不上传。
队列最多 64 条（含在写批次），每 250ms 最多批量写 16 条；拥塞会丢弃并计数。
退出立即停止接收并丢弃未写队列，不等待可选诊断 IO；最后一批可能不完整。
此诊断及受控测试不能代替游戏实机验收。
