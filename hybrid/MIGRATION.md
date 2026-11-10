# 星图 WPF + C# + Python 全量迁移

本路线在 `codex/wpf-python-hybrid-20261110` 独立推进，正式 Qt/Python 起点为 `cc5100c6e3cea425ac4dbed82df92623754f81fa`（星图 1.1.2）。另一条 `codex/full-csharp-wpf-20261109` 纯 C# 路线继续并行，不能直接以它的测试或文档作为本路线验收。现有 Qt 入口和正式安装保持原样。本文是本任务唯一进度清单；代码完成、自动检查、桌面检查、实机与安装验收分别记录。

## 责任边界

WPF/C# 负责窗口、持久页面、用户交互、Windows 输入/捕获适配、像素呈现和 Python 服务生命周期，并承接已验证混合实验中的持久 C# `_grid` 网格候选搜索。Python 保留其余 rank/识别、后续 refine、最终 pose、当前帧验证和接受/拒绝、结构渲染、楼层策略、作者显示桥、资源校验/事务及用户数据格式；两种模式继续走同一核心。C# grid 不是完整识别链；C# 失败不能把内部 entry 竞争误称为自动回退 Python，也不能让显示反过来决定身份或位姿。

Python 服务采用自有子进程和本地标准输入/输出 JSONL；不监听网络。业务请求有递增 ID，每个连接仅一个执行中的请求。当前图库是串行只读切片；未来本局操作需独立的可取消调度和当前资源/轮次校验，不能把图库队列直接当本局调度。日志走 stderr，C# 同时排空两条输出。页面持有当前请求代次，切模式/选图/关闭立即撤销旧结果的显示资格。图片只按服务当前清单中的 key 读取，不接受任意图片路径。

资源目录由用户或显式启动参数选择，不从旧安装猜测；`_internal` 与安装根分开。沿用现有 `resource_read_lock` 和 `assert_resources_ready`，会创建/使用安装根的 `work/map-packages/import.lock`，不会写设置、导入或修复资源。预览不是身份、安装完整性或本局能力证明。

## 全量功能及验收清单

| 范围 | 当前 Qt 入口与实际状态写者 | 混合路线当前状态 | 必须验收的使用流程 |
| --- | --- | --- | --- |
| 启动、错误边界、单实例、后台和退出 | `tools/qt_client_entry.py` → `client.main/QtClient`；`client_single_instance`、`client_startup_cleanup` | 图库测试入口及自有 worker EOF/重连/退出已实现并有窗口主机回归；完整产品生命周期未迁 | 重启恢复窗口，启动失败可诊断；X 的后台/取消/退出；所有自有任务、监听和子进程排空 |
| 主页面与本局、候选及人工选择 | `ClientHome/Controls/MapPicker.qml`；`SessionService` → `LiveRuntime` | 未迁 | 显式开始→捕获→身份/楼层→首贴→后续开图；手选、取消、迟到、结束及重开 |
| C# grid 热点与 Python 后续消费 | `fast_structure_alignment._grid` → `propose/refine`；历史 protocol 2 的 `GridBridge` | 已只读核对签名与调用兼容；当前正式链尚未接入 | 同输入候选、实际 Python refine/最终接受拒绝轨迹；延迟启动、取消/超时/崩溃和重启；原图与合成分开 |
| Windows 窗口、捕获、全局输入 | `client_game_focus/capture/input/native_keys/map_input`；coordinator | 未迁 | 严格绑定及游戏重启，完整客户区、前台失效、按键穿透；局外按键不开始对局 |
| 参考/贴合/提示窗口、作者显示桥 | `client_windows`、`LiveImages`、`ClientAligned/Reference/Toast.qml`；runtime/core | 未迁 | 作者切换保持身份/结构位姿；定点和贴合分别显隐；DPI、捕获排除及旧帧撤回 |
| 地图与楼层图库 | `ClientGallery.qml` → `Backend` → `load_browse_context` | WPF 选择/筛选/实际图片与 Qt-free worker 已实现；合成/静态资源与窗口主机检查通过，桌面输入/捕获受限，原 UI 全量等价未验 | 两模式、两赛季、变体、缺图、坏图、刷新、快速换层/模式、真实桌面像素 |
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

参考：进程重定向和并发排空遵循 [Microsoft ProcessStartInfo 文档](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo) 与 [标准输出重定向说明](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.redirectstandardoutput)；WPF 呈现/UI 所有权参照 [WPF 概述](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview/)。外观沿用现有 Qt 图库的蓝色、字体及左右列表/预览关系，不在本次迁移中重新设计产品。
