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
`RoundPose.pose_for_floor()`，不复制贴合算法。当前只完成适配层及合成快照契约检查；
尚未将实机识别结果或完整回放接到 Qt 的地图叠加渲染。浏览缩略图不能证明识别/贴合正确。
完整迁移仍需将现有结果授权/生命周期适配进来，保持两模式共享算法。

## 有界验证

```powershell
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/main.py --probe-output work/qt-evidence/local
.\.venv-qt-isolated\Scripts\python.exe experiments/qt_quick/check_backend.py --archive '已有图包.zip'
```

第一条约 9 秒，只向本进程窗口派发 QtTest 事件，不移动系统鼠标或操作游戏。
第二条校验真实资源和可选真实 ZIP，只写实验副本；位姿输入为明确标注的合成数据。
覆盖确认分支使用现有导入器，当前真实包样本验证的是相同内容的导入；
未把这次结果扩大为所有版本升级、断电恢复、原生文件对话框或实机快捷键验证。

最新交互证据：`work/qt-evidence/review-fixes/`，16 项断言通过、QML 警告为零。
真实 ZIP / 后台检查：`work/qt-evidence/backend-checks.json`，11 项通过。
截图包含已有作者图，仅保存在被忽略的本地目录，不随源码提交或公开。

本机 Windows 11 / D3D11 / Qt 6.11.1，约 9.17 秒短流程：

| 指标 | p50 | p95 | 最大 |
| --- | ---: | ---: | ---: |
| 界面 8ms 计时器额外延迟 | 0.016ms | 2.633ms | 50.741ms |
| 主窗 frameSwapped 信号到达间隔 | 3.151ms | 7.796ms | 54.359ms |
| 进程 RSS，31 次采样 | 184.887MiB | 191.027MiB | 196.438MiB |

计时器延迟是事件循环响应的代理指标，不是点击到显示的端到端延迟。
帧信号间隔受运行环境、遮挡与调度影响；没有显示器呈现时间，不能换算成“真实 FPS”。
悬浮帧样本包含停驻和折叠间隔，不作稳态 FPS 证据。最大延迟仍有约 51ms，尚不能宣称完全无卡顿。
没有同机同操作的 Tk 基线、游戏窗口验证、长时内存验证或打包后测量。

## 部署判断

值得继续分阶段迁移：现有纯 Python 资源模型和位姿状态接口可以复用，Qt 承担显示和交互。
先接真实回放与显示授权、全局快捷键隔离测试，再考虑替换正式入口。

依赖并不轻：本机 `PySide6-Essentials` 安装文件约 204.9MiB、shiboken 约 3.0MiB；
这包含未裁剪的 Essentials 模块，既不是增量 RSS，也不是 Setup 的大小。
完整产品仍需 Python / NumPy / OpenCV 等，后续要按实际 QML 导入裁剪和构建 Setup 测量，
不以 Qt 空壳体积推断最终包大小。许可证和部署边界见 [THIRD_PARTY.md](THIRD_PARTY.md)。
