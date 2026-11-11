# 星图 WPF + C# + Python 全量迁移

本路线在 `codex/wpf-python-hybrid-20261110` 独立推进，正式 Qt/Python 起点为 `cc5100c6e3cea425ac4dbed82df92623754f81fa`（星图 1.1.2）。另一条 `codex/full-csharp-wpf-20261109` 纯 C# 路线继续并行，不能直接以它的测试或文档作为本路线验收。现有 Qt 入口和正式安装保持原样。本文是本任务唯一进度清单；代码完成、自动检查、桌面检查、实机与安装验收分别记录。

## 责任边界

WPF/C# 负责窗口、持久页面、用户交互、Windows 输入/捕获适配、像素呈现和 Python 服务生命周期，并承接已验证混合实验中的持久 C# `_grid` 网格候选搜索。Python 保留其余 rank/识别、后续 refine、最终 pose、当前帧验证和接受/拒绝、结构渲染、楼层策略、作者显示桥、资源校验/事务及用户数据格式；两种模式继续走同一核心。C# grid 不是完整识别链；C# 失败不能把内部 entry 竞争误称为自动回退 Python，也不能让显示反过来决定身份或位姿。

Python 服务采用自有子进程和本地标准输入/输出 JSONL；不监听网络。当前图库与保存帧本局共用一个进程和资源所有者，连接内仅一个计算；业务请求有 session/递增 ID，取消匹配当前计算并排空终态，随后才执行下一请求。图库失败不改变本局，浏览模式独立。日志走 stderr，C# 同时排空两条输出。页面持有当前请求代次，切模式/选图/关闭立即撤销旧结果的显示资格；隐藏页面保留有界最新结果、可见时补写。图库图片只按服务当前清单中的 key 读取，本局帧仅接受显式选择并复制到自有帧根的 PNG。

资源目录由用户或显式启动参数选择，不从旧安装猜测；`_internal` 与安装根分开。沿用现有 `resource_read_lock` 和 `assert_resources_ready`，会创建/使用安装根的 `work/map-packages/import.lock`，不会写设置、导入或修复资源。预览不是身份、安装完整性或本局能力证明。

## 全量功能及验收清单

| 范围 | 当前 Qt 入口与实际状态写者 | 混合路线当前状态 | 必须验收的使用流程 |
| --- | --- | --- | --- |
| 启动、错误边界、单实例、后台和退出 | `tools/qt_client_entry.py` → `client.main/QtClient`；`client_single_instance`、`client_startup_cleanup` | 图库测试入口及自有 worker EOF/重连/退出已实现并有窗口主机回归；完整产品生命周期未迁 | 重启恢复窗口，启动失败可诊断；X 的后台/取消/退出；所有自有任务、监听和子进程排空 |
| 主页面与本局、候选及人工选择 | `ClientHome/Controls/MapPicker.qml`；`SessionService` → `LiveRuntime` | WPF 保存帧主页已接统一服务，开始/实际捕获求解/本局状态/贴合预览/取消/结束/重连有生产回放消费者检查；候选/确认/换层已接命令但实际交互未验，完整原 UI 与实时采集未迁 | 显式开始→捕获→身份/楼层→首贴→后续开图；手选、取消、迟到、结束及重开 |
| C# grid 热点与 Python 后续消费 | `fast_structure_alignment._grid` → `propose/refine`；历史 protocol 2 的 `GridBridge` | 已携入持久服务，作用域注入当前生产链；当前原帧确实执行 grid，合成已知目标 Host 接受对照通过；完整 Runtime 自动 grid 接受/原生显示仍缺 | 同输入候选、实际 Python refine/最终接受拒绝轨迹；延迟启动、取消/超时/崩溃和重启；原图与合成分开 |
| Windows 窗口、捕获、全局输入 | `client_game_focus/capture/input/native_keys/map_input`；coordinator | C# Win32 事实/GDI、独立 native pipe 和原 Python provider 已接；自有窗口整帧/ROI取像通过，真实前台阳性未验；全局键盘边沿与原业务命令已接，完整鼠标/单修饰键/焦点恢复仍未迁 | 严格绑定及游戏重启，完整客户区、前台失效、按键穿透；局外按键不开始对局 |
| 参考/贴合/提示窗口、作者显示桥 | `client_windows`、`LiveImages`、`ClientAligned/Reference/Toast.qml`；runtime/core | WPF 被动仿射贴合层及自身窗口 affinity 排除/恢复已实现，实际透明层/DPI/排除事务未验；参考/提示窗口未迁 | 作者切换保持身份/结构位姿；定点和贴合分别显隐；DPI、捕获排除及旧帧撤回 |
| 地图与楼层图库 | `ClientGallery.qml` → `Backend` → `load_browse_context` | WPF 选择/筛选/实际图片已接统一 worker；隐藏时延后列表/解码，可见时复用当前结果；合成/静态资源与窗口主机检查通过，桌面输入/捕获受限，原 UI 全量等价未验 | 两模式、两赛季、变体、缺图、坏图、刷新、快速换层/模式、真实桌面像素 |
| 图包导入、升级、删除与恢复 | `ResourceService/PackageManagerService` → `star_map_import/author_package_management/resource_transaction` | 未迁 | 真文件选择，普通新增、替换确认、取消、失败/重复、列表与 provider/cache 失效及恢复 |
| 在线图包 | `OnlinePackagesService` → `star_map_download` | 未迁 | 刷新、下载、校验、取消/重试、实际安装和失败恢复 |
| 自定义路线与图库 | `CustomGalleryService/CustomGalleryDisplay` | 未迁 | 创建/编辑/导入导出、保存/取消、启用、跨层显示与图片缓存失效 |
| 设置、快捷键、模式与作者偏好 | `SettingsService` 及 coordinator | 未迁 | 原格式隔离副本、草稿/字段保存/失败恢复；真实消费者立即同步而非整页刷新 |
| 账号、路线广场与投稿 | `RoutePlazaService/ClientAccount/ClientRoutePlaza.qml` → `route_plaza/account_service` | 未迁 | 登录/退出、搜索、下载、投稿/撤回；外部写入须按授权和测试账号验收 |
| 帮助、引导、教程、公告、更新与诊断 | `ClientInfo/Onboarding/Tutorial/ProblemsService` 等 | 未迁 | 延迟公告及已读落盘、后台恢复、退出后拒旧回调，帮助与诊断导出 |
| Setup、数据隔离、升级回退 | `tools/build_qt_client.py`、当前安装与资源事务 | 未迁 | 同远端 commit 干净构建，自包含 Setup；记录资源/版本/字节数/SHA256；隔离安装、中断与回退 |

## 推进顺序

1. Qt-free Python 服务与 C# 进程桥，打通图库真实资源/预览及正常退出，建立可运行工程。
2. 只读核对历史 protocol 2，接当前正式 `_grid` 后端及 `LiveRuntime/EngineFactories` 本局命令/保存帧回放；先证明 C# 候选进入原 Python refine/最终验证及最后消费者，再接 C# Windows 捕获/输入/浮层。
3. 迁移设置、作者/自定义路线和资源事务的唯一写者；完成导入/下载/恢复及相应 WPF 操作。
4. 迁移账号广场、帮助引导/公告更新/诊断，完善后台/单实例/退出。
5. 对照完整用户旅程和当前真实画面，按相同最终提交构建隔离 Setup。缺乏真实设备/游戏或账号权限的项目保持未验，不能由回放补作通过。

## 验证记录

### 2026-11-10：资源/进程切片

本轮使用 Python 3.13.3 与本机已有 .NET SDK 10.0.403，未安装 SDK/依赖，也未启动或操作游戏。构建为 Release、框架依赖，尚未制作或交付 Setup。以下是开发树检查，提交后再核对源码/远端，不能当安装包证据。

| 实际命令/输入 | 结果 | 边界 |
| --- | --- | --- |
| `dotnet build hybrid/StarMap.IntegrationTests/StarMap.IntegrationTests.csproj -c Release --nologo -p:UseSharedCompilation=false` | 3 工程构建成功，0 警告/0 错误 | 使用已有 SDK 的绝对路径；无打包/安装 |
| `python -m unittest tests.test_hybrid_browse_worker -v` | 9/9 通过 | 真实子进程、生产 loader；Qt 导入阻断。S0 两模式与复用现有 S1 fixture 的两模式、PNG、旧快照/改图/同 stat 不同 hash、坏协议、EOF、父安装恢复标记 |
| `dotnet hybrid/StarMap.IntegrationTests/bin/Release/net10.0-windows/StarMap.IntegrationTests.dll <source> <python> <fixture-root>` | 25/25 通过 | 合成资源，真实管道/WPF UI 线程/MainWindow；延迟代理仅延迟真实应答，exit 代理只制造非零退出 |
| 同一程序集、输入换为隔离的真实资源副本 | 26/26 通过 | 原实验 855 文件/223823255 字节静态资源副本，未改原件；默认作者素材缺失保持缺失，明确选择已存在结构来源后检验PNG消费。不涉及帧或楼层控件观察 |
| 实际 MainWindow 的 `RenderTargetBitmap` | 普通 1080×760、最小 760×560 窗口的布局/图像消费者检查通过，已人工看图 | 这是 WPF 视觉树离屏证据。初次截取 content 裁掉 margin/透明背景，失败图保留；改为渲染完整 Window 后复查。不是桌面截图或鼠标键盘验收 |
| Computer Use 原生桌面检查 | 窗口已出现，实际控件树可读取；截图黑帧，输入返回 `GetCursorPos` 拒绝访问 | 未据此判断真实可见像素/交互通过；测试窗口已正常关闭。没有恢复游戏采样或向游戏发键 |

本轮先复现并修复异步管道写入与关闭竞态，保留失败输出；独立审查指出的重连期间退出漏等旧 worker、`_internal` 错用资源锁根及旧 worker 非零退出阻断首次重连均已修复，并通过实际 MainWindow/资源服务回归。当前只读图库请求保持串行，不构成后续本局取消协议或 C# grid 接入证据。当前正常/测试退出后核对自有 WPF/Python 测试进程，未见残留。S1 当前真实帧、全产品 UI、完整输入/捕获/浮层、性能及内存长期运行、安装/升级/回退仍未验。

历史算法来源已核对 [同轮三路线报告（164d63b）](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/164d63bc8c1ee3c3c25c07f884b2a93a17389506/docs/python-csharp-same-round-20261109.md) 和 [最终 Release 报告（306b3d3）](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/306b3d394506bb8680c17808fd550cb484672024/docs/python-csharp-release-validation-20261109.md)，并只读查看原实验 `hybrid-runtime/grid_client.py`、`worker.py`、`csharp/GridService.csproj`。旧实验算法归档是 `e78d4ee`；本路线必须重新绑定 `cc5100c` 及后续正式源码，历史结果不是本路线通过证据。旧实验只替换模块 `_grid`，C# 回传候选后 Python 继续求解；没有实现 C# 崩溃自动回退 Python。新楼层/当前产品验收只用 2026-11-05 及之后有采集来源的原始截图，不恢复已停止采样，不向游戏发送按键。

### 2026-11-10：C# grid 与保存帧生产会话

`StarMap.Grid` 携入原 protocol 2 数值服务，移除 benchmark 写文件入口，不依赖旧实验路径或纯 C# 分支。`using_grid_backend` 只在当前 Python 计算作用域替换 `_grid`，其他线程和默认 Qt 保持原实现。候选仍经过原 Python `propose/refine`、几何/残差验证和 Core 状态消费者。服务失败会使整个请求失败，候选循环即使捕获异常也不能把它变成成功；没有自动切换 Python 后端。下一次显式请求可以重启 C# 子进程。

`RecordedRuntimeSession` 复用真实 `EngineFactories/LiveRuntime`。帧只从显式目录读取，PNG 尺寸在解码前检查，坏帧撤销旧帧/显示。原采集坐标元数据按尺寸核对，缺失时标为未知；固定 `recorded`，不授予 live 权限。资源租约/恢复检查放在实际 factory 获取边界，省略或失败 prepare 后也不能绕过。显式 memory root 留在独立 data root，未传时保留原 Qt 路径/seed 行为。`runtime_worker.py` 是可取消、单计算、无无界请求队列的独立开发入口；还没有 WPF 本局消费者。

独立数值复核发现并修复原实验内核的偶数中位数差异：96 个唯一像素点的可达 coarse 输入中，原 C# 的 `(a+b)*.5f` 与 NumPy 2.2.6 `_lerp` 顺序不同，能改变最高分和 5 像素搜索峰。C# 现按 `b-(b-a)*.5f` 计算；原 Python 不改。回归保留原坐标反例与有效分数/排序反例，由原 Python 计算预期，未放宽断言；独立 reviewer 的额外 300 组逐候选对照严格相等。不能把旧实验已测过当成所有输入逐值一致。

本轮先在系统 Python 上发现依赖漂移：OpenCV 5.0.0 / NumPy 2.2.5 / Pillow 11.2.1。S1 生产校验拒绝 OpenCV 版本，没有修改或绕过校验。正式本轮证据改用已存在的只读隔离 Python 3.13.3 / OpenCV 4.12.0 / NumPy 2.2.6 / Pillow 12.3.0；其依赖目录不含归档算法，实际模块从当前 checkout 导入。旧实验源码和依赖均未修改。

| 输入/检查 | 实际结果与范围 |
| --- | --- |
| Release `dotnet build hybrid/StarMap.Grid/StarMap.Grid.csproj -c Release --nologo -p:UseSharedCompilation=false` | .NET 10，0 警告/0 错误，无额外 NuGet；没有 Setup |
| 最终针对性 unittest 联合检查 | 精确依赖下 93/93 通过：新 service/backend/session/worker、原 season/provider/pose/cache/residual 相关回归及图库 worker。包含最终 Median 反例、资源 guard 与数据根保护；不是全库或实机测试 |
| grid service/backend 定向回归 | 真实 C# 子进程与当前 Python `_grid` 候选严格一致；局部/全局、两条评分路径、float32 边界、无解、错会话取消、真实首评分点取消、运行中杀死自有子进程、显式恢复及拒绝隐式回退 |
| runtime/session/provider 回归 | 真实生产编排与显式合成替身分开；启动失败不挂输出线程、坏帧撤回、prepare 前置绕过反例、外部进程持锁、32M 像素头前置拒绝、Qt 导入阻断，及被阻塞输出管道的实际 EOF 收尾 |
| 当前原帧 4 例，同一 runtime_worker 的 python/csharp 两路线 | 均为 2026-11-09 原始采集记录，S0/S1 噩梦/困难各一例。所比较语义与解码 RGBA 4/4 一致：S0 困难产生 render、grid 0；S0 噩梦身份未唯一且不显示、grid 0；S1 困难/噩梦待确认且不显示、grid 分别 3/8。所有 liveAuthorization=false。**不是四例识别/显示成功，也未证明真实 grid 最终显示接受。** |
| 三个独立合成已知目标 Host 场景，尺度 .4/.5/.6 | 使用真实结构资源生成像素，生成矩阵不传给 grid/refine/验证。每例自然调用 grid 1 次、原 Python refine 8 次、原 Host fast 接受；两路线非时钟语义严格相等。目标地图/楼层是明确给定的单图 fit 输入，不是自动身份或 Runtime render/原生显示验收 |
| 外层 runtime_worker 的实际原帧恢复检查 | 两后端均在实际 grid 路径中拒绝 foreign cancel，精确取消拒绝终态和图片，1ms 期限拒绝后能重新执行实际 grid，EOF 正常结束。C# PID 在终止前保留原生进程 handle，EOF 后核对已退出。另有真实 16MiB 管道背压的输出边界检查；它不是完整 Runtime 大图输出验收 |
| 全后代资源快照 | 同一原帧恢复后的 OS 进程树含 Python/conhost（2 个）与 Python/C#/各自 conhost（4 个）。最终源码 WS 求和分别 147435520 / 232112128 字节，PrivateBytes 求和 800841728 / 856813568 字节。只是各一次时点快照，WS 可重复计算共享页，不是峰值、泄漏结论、公平性能比较或内存收益 |

原帧证据位于本地忽略目录 `work/hybrid-current-final-20261110-01`。初次对照因未归一每进程新生成的 attempt/render token 判为不等，原件保留；仅排除实例 ID 与时钟字段后比较，地图身份、楼层、候选、姿态和接受/拒绝仍严格比较。系统错误环境下的 S1 拒绝也保留。组合命令在合成检查入口因导入顺序失败后，修正并单独重跑 `check_synthetic_fit`，三个正例及原始输出已完整落盘；未覆盖或替换前面的失败输出。

Median 修正后的最终同树复验完整落在 `work/hybrid-median-final-20261110-01`：同一组合命令从四原帧到 controls、三个 synthetic Host 均正常结束，进程树快照也绑定这轮；检查后未见本轮自有 Python/Grid 残留。独立最终审查没有未关闭的实质问题，审查者另在匹配依赖下执行了 23 项消费者回归与 300 组候选对照；并不代表其独立重跑了全部 93 项或真实游戏。

复现入口为 `hybrid/python/replay_check.py --resource-root <explicit-root> --frame-root <explicit-frame-root> --sample-index <capture-index> --case <id> ... --check-controls --check-synthetic-fit --output <new-output>`。它不接受地图/位姿答案用于自动识别，只以采集元数据提供模式和坐标；输出保留实际调用成功/拒绝原因。输出泵沿用原实验机制，按 [Microsoft CancelSynchronousIo](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelsynchronousio) 只取消本输出线程的同步 I/O，不终止线程或其他程序。

待继续：WPF 原主页/本局与该服务的真实交互、原生捕获/输入/显示、完整作者/设置/资源操作与所有页面。接 UI 时图库与本局须在同一资源所有者下组合，不能让两个独立 Python 服务互相争用安装独占租约。RPC 的 `ok` 只表示请求处理，业务成功仍读 receipt/capability/候选与当前 render；取消和页面代次由 C# UI 消费者再次核对。真实帧的自动 grid→最终显示接受正例、长期全进程资源及同条件端到端延迟、安装/升级/回退仍未证明。本轮不宣称全量迁移、完整 UI 或实机通过。

参考：进程重定向和并发排空遵循 [Microsoft ProcessStartInfo 文档](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo) 与 [标准输出重定向说明](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.redirectstandardoutput)；WPF 呈现/UI 所有权参照 [WPF 概述](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview/)。外观沿用现有 Qt 图库的蓝色、字体及左右列表/预览关系，不在本次迁移中重新设计产品。

### 2026-11-10：统一服务与 WPF 保存帧主页

本阶段把 `hello/catalog/preview` 合入可取消 runtime worker，图库与本局只启动一个 Python 服务并共享原资源租约。图库失败、取消、忙重试与切浏览模式不会撤销或改变本局。C# `HybridWorker` 严格匹配 session/id，忽略进度与无关终态，取消后排空当前终态才释放下一个请求；两条输出持续排空，仍仅管理本次自有进程。旧图库 worker 留作窄回归入口。

WPF 主页通过同一服务消费原 `LiveRuntime`，没有另写识别或本局算法。开始后保留生产 `roundPreparing` 阶段，首次捕获后才可能 `roundActive`；两者都锁住本局上下文。显式选 PNG 复制到自有帧目录，当前帧、请求代次、资源及本局元数据再次核对后才呈现 Python 材料图和仿射变换。主页与图库是固定实例；隐藏主页仍收取业务状态，但延后列表/图片投影，隐藏图库延后列表/解码，恢复不重复 RPC。材料缓存绑定实际资源、地图、楼层、有效来源及 PNG，保留未提交的楼层选择。

独立复核发现的两个状态错误已修复并保留定向回归：按钮原接生产 `cancel` 会结束本局，现由传输取消/排空当前工作后执行原 `close_map`，只撤销当前图并保留本局身份；RPC 错误原清空 UI 快照、错误放开上下文，现重新获取实际 snapshot。同步也失败时保留已知本局、关闭业务操作，明确提供重新连接资源目录的路径；显式重连会正常排空旧服务并新建会话。结束与关闭立即撤销旧消费者，迟到的真实成功回复不得恢复图片或本局。

验证使用同一现有 .NET SDK 10.0.403 和精确 Python 3.13.3/OpenCV 4.12.0/NumPy 2.2.6/Pillow 12.3.0。未操作游戏、重新采样、安装、发布或修改正式 Qt 入口。

| 检查/实际输入 | 结果及边界 |
| --- | --- |
| `python` 的 `test_hybrid_browse_worker/test_hybrid_runtime_session/test_hybrid_runtime_worker/test_hybrid_unified_service` | 匹配依赖下 27/27。真实子进程/资源锁/协议与明确合成替身分开；统一服务在阻断 Qt 导入下能读 PNG，持有 runtime 租约后仍能浏览且不改本局 |
| `StarMap.TransportTests` 与 Release 构建 | 40/40，构建 0 警告/0 错误。真实服务/资源 14 项；其余使用明确的传输故障 worker，覆盖阻塞发送的超时/调用取消、半条 JSON 连接拒复用、已排空超时/业务拒绝后复用，及坏 JSON/非对象/缺 event/非法 ID 的错误和收尾。日志 `work/hybrid-transport-final-20261110-04.txt` |
| `StarMap.IntegrationTests` 同次联合执行（4 个位置参数） | 发送/输出泵修复后 Release 构建 0 警告/0 错误，原图库/窗口 26 项及新本局消费者 27 项全部通过。日志 `work/hybrid-unified-wpf-final-20261110-02.txt`；结束后未见本次自有 WPF/Python/grid/代理残留。随后只改非法 ID 检查，该差异由上行 40 项传输回归覆盖 |
| `StarMap.IntegrationTests ... <current-frame-index> --runtime-only` | 当前保存帧消费者 27/27。使用 2026-11-09 采集的 `batch02-event-000097` 与隔离真实资源副本，不传地图/位姿答案；自动身份为 S0 困难 `difficult-map-23`、本例 grid 0，不能补作 C# grid 最终显示接受正例 |
| 程序化 WPF 事件/消费者 | 开始、实际捕获求解、隐藏收取、材料 PNG 解码像素相等、完整六项仿射参数、全客户区裁剪、显隐复用位图、结束/取消后的迟到成功拒绝、错误后重试和实际 EOF 后显式重连均检查；错误注入只将一次 RPC operation 改成未知值，让真实服务返回错误，不替换求解/快照 |
| 普通/最小窗口离屏图 | 1080×760、760×560 及小窗滚动后的实际 `RenderTargetBitmap` 已生成并人工看图。最小窗口原候选/预览被挤到数十像素，已用页面最小内容高度与滚动保留可用区域；这不是桌面截图、鼠标/键盘或真实文件选择器验收 |

当前末次联合检查的离屏证据在忽略目录 `work/hybrid-runtime-renders/1daeb1db73364867b5054627e54220b1`（图库同次位于 `work/hybrid-gallery-renders/f304bd45cef5488eb53b39844eaf4970`），前面的失败/改前图片保留。本例仅比较材料 PNG 与变换消费，不把它称为有独立真值的合成最终屏幕像素验收，也未测从真实输入到可见像素的端到端延迟。

旧图库关闭检查曾间歇触发真实 5 秒超时：请求已送到 Python，但 C# 在写入转读取的间隙被关闭取消，代理写约 159 KB 应答时背压堵塞。两个 Bridge 现先在同一 5 秒总期限内等待活跃写入，再取消排队写并关闭 stdin，保证已发请求进入读取排空；真正超时仍明确失败并仅终止自有进程树。没有延长期限或放宽正常退出断言。测试代理的可选 trace/marker 仅定位时序，不改生产 PNG/loader；失败与修后正常关闭分别记录，强制清理不算正常退出。

最终独立故障注入另复现了发送期限未覆盖 stdin 写入，以及坏协议后满 channel 令已退出进程的输出泵仍阻塞。现请求的发送与等待共用同一期限；写入中断后不复用可能只发送半条 JSON 的连接，并明确终止自有进程，控制取消写另有 1 秒上限。协议故障完成 channel、解除等待写并继续丢弃排空 stdout；泵不再等待进程退出，退出与异常收尾由有界关闭路径管理。正常进程退出预算仍为 5 秒，失败后的任务/管道清理有独立短期限且始终报失败，不能算正常退出。

两个独立原始 probe 修后均已重跑：阻塞发送在 2 秒检查前已失败结束、自有进程已退出；坏 JSON 后再发十条事件，Close 也已完成而不再挂住。新增 200ms 超时与 200ms 调用取消分别检查，并记录最先触发的原因。最初测试把调用取消计时放在进程就绪之前，不能保证“超时先到”，失败证据保留；改为完成握手后才比较两只计时器。ready 仍独立有界（30 秒，与默认原值一致），请求预算从 ready 后开始，未放宽计算超时断言或退出预算。传输机制参考 [Microsoft Channels](https://learn.microsoft.com/en-us/dotnet/core/extensions/channels)。

后续 fresh 复核另以实际桥复现了非法 `id`（字符串）绕过协议错误处理、连接仍可复用的问题。现先检查匹配 session 的 ID 是整数，再做当前 ID 比较；字符串/object/null 均按协议失败撤销连接，合法的旧 ID 与 foreign session 仍忽略。三类非法 ID 的拒绝、拒复用和自有进程退出均保留回归。等待终态期间若调用方也取消，仍优先报告调用取消并排空；发送阶段单独记录最先中断写入的原因。

尚未完成：真实文件框选帧、两模式候选/确认/换层的实际 WPF 使用流程；完整原主页与其他页面；实时捕获/输入与原生定点/贴合/提示窗口；后台/托盘/单实例与 X 的产品退出选择；保存帧暂存长期清理和跨启动数据根；投影矩阵显示、计算中即时显隐。资源写者、作者/设置、导入下载、账号/帮助等继续按全量清单迁移。当前没有 Setup，不能作为正式客户端或全量迁移完成。

### 2026-11-10：原生捕获与被动贴合层

新增 `StarMap.Windows` 将 `GetClientRect/ClientToScreen/GetDpiForWindow`、进程创建时间与可执行路径复核，以及 client/screen DC 的 DIB/BitBlt/GdiFlush 放在 C#；各捕获共用原语，不自动降级到屏幕。生产目标仍同时要求原游戏标题政策和 `dwrg.exe`，未知身份或几何失败即拒绝。Python `ClientCaptureProvider` 保留唯一 generation/启停/前台/空白判定/fallback/捕获前后上下文检查，默认 Qt 构造只调整为延迟导入 `NativeWindowAPI`。共用 `RuntimeSession` 持资源租约和工厂，recorded 与 native 组合分别构造；native 不接受 `set_frame`，不能给保存 PNG 改 live 标签。

每个自有 native worker 使用独立、仅当前 Windows 用户的命名管道，实测客户端 PID 必须等于本 worker。业务 JSONL 保留原 session/id/取消终态；二进制回调绑定当前请求及 callId，元数据回调不进入业务串行锁，避免持 Python runtime 锁的 `is_current` 等待取消线程回包。取像只在 dispatch 作用域，图库不获得取像权。BGR 体有 32M 像素上限与两秒 I/O 截止；半帧或错误通道拒复用。取消挂起 overlapped I/O 后须观察该操作完成才释放缓冲、结构及事件；原先提前释放的 P1 已复现并修正。若一秒取消收尾仍无法证明完成，专用 Python worker 保持栈缓冲至非零退出 70，由父进程按失败处理，而不释放仍被内核引用的内存。

WPF 可选择“连接游戏窗口”或 `--live-capture`，显式选择/绑定后准备本局；实时重新识别、选图、确认和换层统一提供三秒切回游戏的调度，不放宽前台条件。此等待不是算法耗时或性能达标证据。贴合层不激活、不接受输入，按已接受捕获的物理 origin/size/DPI 放置；普通页面隐藏时仍可消费本局材料。上下文在解码前、解码后或显示期间失效，立即撤材料和旧 token，并走原 `close_map` 撤后端显示，不能返回同窗口/页面后复活。原生层只做仿射显示，参考/提示窗口、全局输入、投影和正式生命周期仍继续迁移。

| 本轮检查 | 实际证据与边界 |
| --- | --- |
| Python native capture 与已有 hybrid 四模块 | 13+27 项通过。原生13项含真实 kernel pipe 的合成 peer、半帧、取消后排空、Qt 导入阻断、错误身份，以及真实 pending write 的关闭唤醒；内核不确认完成的分支仅用隔离子进程模拟，验证退出70 |
| 原 Qt 受影响核心 | 匹配 Qt 6.11.1 环境，`test_client_capture_full/test_client_capture_compat/test_client_capture_exclusion/test_client_live_runtime` 93 项通过；使用原合成 native/求解替身，并非真实 GUI/游戏 |
| `StarMap.NativeTests <source> <python> <resources>` | 自建且持续泵消息的 Win32 窗口：整帧/ROI 红绿蓝像素符合独立绘图值、预取消拒帧、移动后拒旧几何；12次捕获 GDI 对象前后均11。严格默认身份政策拒该测试窗，受控集成单独注入仅本测试 PID 的选择政策 |
| 真实 native worker 使用链 | C#→原 Python 会话的 hello/prepare/bind/start/end/EOF 通过，前置元数据流程 PixelCalls=0。Windows 拒绝将测试窗置于前台，实际 capture_now 被 `CaptureForegroundUnavailable` 拒绝且未取像；日志 `work/hybrid-native-owned-window-20261110-01.txt`。这不是完整实时取像阳性 |
| 额外 `--simulate-foreground` | 仅替换“前台 HWND”事实，仍用真实自有 GDI 窗口像素与原生产 Python：一次完整客户区经 C# BGR 管道进入生产链，返回 `map-not-observed`，没有显示接受。日志 `work/hybrid-native-simulated-foreground-20261110-01.txt`；此项仅为 IPC/原消费者组合检查，不能代替上行被拒的真实前台路径 |
| WPF native 消费者反例 | 12项通过：明确替换 RPC/OS 当前性，使用实际 VM/PNG 解码；复现并修复解码前后失效旧 token 复活，验证全部四种取帧 UI 动作在切回等待时可取消。没有启动窗口、捕获或游戏 |
| 本轮最终 WPF 联合检查 | 图库26项、当前保存帧本局28项及上行native消费者12项，共66项通过，含最终 `render_payload.captureDpi` 来源回归。日志 `work/hybrid-native-wpf-final-20261110-01.txt`；普通/最小/滚动离屏图已复看，分别位于 `work/hybrid-runtime-renders/7f79675badd14313ac9c1cbd6f39b8e5` 与 `work/hybrid-gallery-renders/3611c3e8269040e197f3c7874e8bef1e`，不是桌面或游戏验收 |
| 本轮最终传输与构建 | `StarMap.TransportTests` 40项通过，日志 `work/hybrid-native-transport-final-20261110-01.txt`；Bridge/Windows/Desktop Release构建0警告0错误。覆盖当前native接入后的recorded传输恢复，不扩大为原生取像阳性 |

初次自有 native 窗口测试在跨线程后缺少持续消息泵，卡在自身窗口检查/取像入口；补正确消息线程后才获得上述实际 GDI 证据。首轮完整宿主也曾因探针未设置嵌入式 Python 的路径、及读错 `capability.available` 字段在取像前失败；这些失败不算 native 产品故障或取像通过。实际 worker 入口已直接验证。独立复核还发现 DPI 字段加到了待切图接口而非最终 `render_payload`，现已移到最终已接受捕获来源，并用真实保存帧最终消费者保留 DPI 回归。原失败未通过放宽前台/退出/断言变绿。

参考 [Microsoft 客户区坐标](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclientrect)、[display affinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity) 与 [CancelIoEx 完成语义](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex)。实际全客户区到授权正确浮层像素、1.5/多屏 DPI、排除期间取消/退出与实机仍未验；源码接入、模拟、直接 GDI 和产品验收保持分开，没有 Setup。

### 2026-11-10：全局键盘事实与本局输入

`PassThroughKeyboardSource` 在专属消息线程安装 `WH_KEYBOARD_LL`，始终调用 `CallNextHookEx`。回调只记录物理左右键/按下释放/原时钟/前台/代次，不调用 Python、WPF 或业务；初始按住键只播种一次，后续不用异步键状态轮询。唯一有界信箱溢出即停止并报告失败；关闭在队列创建、安装前后都有取消收尾，等待本线程注销 hook。主 UI 消费者每次只排一个 UI 回调，不能把有界信箱搬成无界 dispatcher 队列。

快捷键默认、旧九槽/十槽迁移、值校验、显示偏好解码提取到 Qt-free `client_settings_values`，原 Qt `SettingsService` 继续持唯一写者并委派同一纯解码函数。hybrid 的只读 `input_preferences` 使用独立 data root 的原格式 `ui-settings.json`；读取失败不改文件，不撤销本局。C# 只消费 Python 已解析的 VK/修饰键；鼠标、单独修饰键 tap 和手动选图明确保留未迁能力，不能退化成普通按下。

C# 输入意图序号持续递增；局外/显式准备提交期间的 G/ESC 仍改变其物理相位，但不准备、取像或抢占开始/结束。原生会话准备后，map/close、候选、确认、换层与本局管理继续调用原 Python 命令。关图立即撤销当前消费者、取消旧开图、排空后提交 close_map，保留本局；只有 Python 再次核对为当前的 mapInputObservation 才可调整同一序号相位。输入监听不会因开始/结束本局重装；换绑才重置意图，序号不归零。业务处理与 hook 回调完全分离。

本批检查：匹配 embedded Python 的 preferences/core/unified/session/worker 共27项通过（`work/hybrid-input-python-20261110-03.txt`），含 Qt 阻断的真实 worker、新鲜/旧版/自定义/冲突设置及局外原生产 map 不取资源锁、不调用 grid；设置文件原样保留。原匹配 Qt venv 的设置服务27项通过，新增纯core5项通过，原写者使用临时文件实际读写。第一次 embedded 检查的失败来自测试子进程依赖 PYTHONPATH 以及父 runner 没加仓库路径；现 probe 通过 argv 显式插入目录，不以换环境掩盖。

`StarMap.KeyboardTests` 的确定性物理键/启动held/repeat/左右modifier及有界溢出检查、实际 hook 安装→注销→线程退出冒烟通过，未发送按键；日志 `work/hybrid-keyboard-source-final-20261110-01.txt`。新增15项实际 policy/VM 反例使用明确替换的按键/RPC，覆盖局外、准备中、陈旧/其他前台/Win组合、关图取消旧开图及迟到成功；不算真实输入。末次联合图库26+当前保存帧28+原生显示消费者12+输入消费者15+实际原生主页启动/关闭3项，共84项通过（`work/hybrid-input-wpf-final-20261110-02.txt`），Release构建0警告0错误。后三项使用默认 MainWindow 和真实 Python/native pipe/hook，未绑定游戏，关闭后实际监听线程停止、自有 Python PID41808退出；不是输入或取像阳性。

独立原生审查在真实 hook 探针复现首次 Close/Start 竞态（13/10000）和线程末尾信号早于真实退出（18/2000）；原冒烟不足以证明实际线程退出。现未启动关闭与启动共用锁原子互斥，原两秒预算内还须实际 Join 所属线程。原键盘测试与同一只读独立探针修后分别通过、0/10000及0/2000，未改预算/断言。独立消费者审查还复现 dispatch 后捕获失效、最终 render 撤销却携带旧 mapInputObservation 的问题；生产终态现按 render→snapshot→观测当前性再次核对，保留原完整 LiveRuntime 正反例，仅额外替换失效的 capture.is_current。末次 Python 28项通过，日志 `work/hybrid-input-python-final-20261110-04.txt`；传输40项通过，日志 `work/hybrid-input-transport-final-20261110-01.txt`。修后再跑受影响图库/消费者/原生主页关闭的56项通过，`work/hybrid-input-close-final-20261110-03.txt`，实际worker PID42216退出、键盘线程已Join；未重复未受影响的保存帧28项。

仍缺真实键盘事件→原窗口前台捕获→正确可见浮层端到端证据；鼠标楼层/拖动/单修饰键、完整手选、焦点返回恢复、管理员主入口及后台单实例生命周期继续迁移。当前不操作游戏或发送测试键，不以 hook 安装证明真实键盘使用通过。实现参照 [Microsoft 低级键盘回调](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc) 与 [PostThreadMessage 队列就绪要求](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postthreadmessagew)。

### PR #129 的 Qt 退出 CI 未关闭项

远端 `9cfeb259` 的 [运行 42780060440](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42780060440) 有12个矩阵成功、1个 tutorials 失败：[job 129707663193](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42780060440/job/129707663193) 的 `test_accepted_resource_writer_drains_before_quit` 在约1.806秒以 code7/timeout 退出。同一次 core job 在同一 head 明确运行该项并通过；main-baseline 的8b147149也明确通过该项，但其四个相关文件不同，不能代替原 Qt 基线对照。

隔离归档9cfeb259与正式迁移前cc5100c6，不触碰当前 native 改动。两者的退出测试、fixture、coordinator与Client.qml的Git内容相同。使用本机已有隔离 Python3.13.3 x64、与CI全部六项应用依赖精确同版（含PySide6/shiboken6 6.11.1）、offscreen/software/1.5缩放，按原 `python -B -X utf8 -m unittest -v test_client_shutdown` 交替串行各三次：两个提交均3/3模块通过，共30项，资源写入6/6。CI宿主Server2025/26100，本机Win11/22631；没有改截止/断言，没有触发远端重跑。对照与原始日志在本地 `work/ci-shutdown-comparison-20261110/RESULTS.md` 和 `results/`。

原失败的 `writerFinished/activeWorkers` 是在 `app.exec` 超时返回、再次 `release.set()` 与 `waitForDone(1000)` 后才采样，且未记录wait返回值。它们不能证明1.8秒时写入已完成；心跳178表明主事件循环曾持续处理事件。证据不足以认定迁移引入、缩放、Qt具体竞态或宿主环境是根因。原CI保持未关闭；再次出现时应补240ms release实际触发、worker返回、退出前池/写者状态和wait结果，不以一次重跑成功关闭问题。

### 4032c339 的采集依赖导入 CI 适配

该提交5个workflow中3个成功、2个失败：[diagnostics 42791347627/job129745291883](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42791347627/job/129745291883) 与 [joint 42791347384/features job129745287551](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42791347384/job/129745287551) 都明确失败于 `test_capture_authorization_source_matches_approved_compatibility_feature`。它要求原capture源码字节完全不变，故不接受本路线将 NativeWindowAPI 移到默认构造分支的延迟导入。该head的tutorials与core成功，但不据此关闭上行9cf历史退出超时。

已用匹配Qt依赖先复现该失败，再只适配既有源码检查：仅允许精确的依赖延迟导入/原实例化变换，其余源码仍与原PR76基线逐字节比较；历史fixture、commit/哈希、排除模块及行为断言不变，没有新建基线或门禁。匹配Qt的diagnostic_noninterference、capture compat/exclusion/full及hybrid native capture共56项通过；内存中改变依赖选择、前台、取消、代次的四种反例仍均被检查拒绝。修前日志 `work/capture-ci-4032-before-20261110-175304.txt` 保留。远端须在下一提交核对，不能称此处本地通过已是CI全绿。
