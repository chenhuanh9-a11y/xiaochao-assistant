# 星图混合迁移开发入口

全量范围、职责、当前进度和未验项统一见 [迁移清单](MIGRATION.md)。当前工程接通了图库、保存帧本局及可选原生捕获/被动贴合层，不能替代正式客户端；现有 `tools/qt_client_entry.py` 继续保留。

需要 Windows 10/11 x64、.NET 10 SDK、Python 3.13 与仓库 `requirements.txt` 的非 Qt 依赖。默认系统 `python` 必须指向该环境；也可显式指定绝对路径。无需 PySide6。

在仓库根目录：

```powershell
dotnet build hybrid/StarMap.Desktop/StarMap.Desktop.csproj -c Release
dotnet build hybrid/StarMap.Grid/StarMap.Grid.csproj -c Release
dotnet run --project hybrid/StarMap.Desktop -c Release -- --source-root "$PWD" --python 'C:\path\to\python.exe'
```

窗口内选择包含 `references`、`assets` 等内容的有权使用的资源目录；也可选择含 `_internal` 的隔离安装目录，或加 `--resource-root '绝对路径'`。资源不随仓库分发。读取沿用原安装资源锁，会创建/使用 `work/map-packages/import.lock`；不导入、升级、修改设置或操作游戏。资料不完整会显示具体错误，可重新选择目录或刷新。

图库可按赛季/模式、来源、楼层、名称筛选实际 PNG。主页使用显式选择的保存 PNG，接入原生产会话的准备、开始、识别、候选选择、确认、楼层和贴合显隐；候选/确认/换层接线仍需各自的实际交互验收。图库和本局在同一 Python 进程共享资源所有者，浏览模式不改变本局模式。隐藏页面暂存最新结果，恢复时补写，导航不重挂页面或重新识别。

选择的 PNG 复制到独立 data root 的 `frames`；默认数据根为源码 `work/hybrid-desktop/<本次运行ID>`，也可显式指定 `--data-root '绝对路径'`，不得放进资源根或帧根之内。准备与本局期间锁定本局模式；取消识别只撤销当前图，结束本局才清除本局。操作失败后同步实际状态，无法同步时可显式重新连接资源目录（会结束旧服务里的本局）。关闭会撤销迟到显示并排空自有 worker。

可勾选“连接游戏窗口”，或加 `--live-capture` 使用 C# 原生捕获。选择窗口并明确绑定后开始本局；实时识别、选图、确认和换层在三秒后请求当前客户区，请先切回游戏。没有全局输入监听，局外地图键也不会启动采集。原生回调通过自有命名管道接到原 Python provider，保留窗口/前台/代次/空白与最终接受拒绝。屏幕 DC fallback 只在自身 WPF 窗口排除成功后执行；失败报告能力缺口。

原生仿射贴合层已接入，但完整实时取像到正确可见浮层尚未验收。自有窗口 GDI 和模拟前台的 IPC 检查不能代替该项。全局热键、定点/提示、托盘/后台/单实例、完整页面与资源写操作仍待迁移。投影矩阵明确拒绝显示；保存帧暂存长期清理与跨启动数据根也待迁移。这里的 `asInvoker` 清单用于无输入监听的开发入口，不能代替未来正式主入口的管理员权限要求。后续完整 Setup 单独构建和验收。

Python 协议回归和 .NET 消费链检查：

```powershell
python -m unittest discover -s tests -p test_hybrid_browse_worker.py -v
dotnet build hybrid/StarMap.IntegrationTests/StarMap.IntegrationTests.csproj -c Release
# FIXTURE_RESOURCE_ROOT 为含噩梦/困难清单与图片的隔离测试目录
dotnet hybrid/StarMap.IntegrationTests/bin/Release/net10.0-windows/StarMap.IntegrationTests.dll "$PWD" 'C:\path\to\python.exe' 'FIXTURE_RESOURCE_ROOT'
# 加有采集来源的当前帧索引，运行主页保存帧/WPF消费检查；第五参数 --runtime-only 可单独运行该组
dotnet hybrid/StarMap.IntegrationTests/bin/Release/net10.0-windows/StarMap.IntegrationTests.dll "$PWD" 'C:\path\to\python.exe' 'REAL_RESOURCE_COPY' 'CURRENT_FRAME_INDEX'
dotnet build hybrid/StarMap.TransportTests/StarMap.TransportTests.csproj -c Release
dotnet hybrid/StarMap.TransportTests/bin/Release/net10.0/StarMap.TransportTests.dll "$PWD" 'C:\path\to\python.exe' 'FIXTURE_RESOURCE_ROOT' 'ISOLATED_DATA_ROOT' 'FRAME_ROOT'
dotnet build hybrid/StarMap.NativeTests/StarMap.NativeTests.csproj -c Release
dotnet hybrid/StarMap.NativeTests/bin/Release/net10.0-windows/StarMap.NativeTests.dll "$PWD" 'C:\path\to\python.exe' 'REAL_RESOURCE_COPY'
# --native-only 仅检查自有Win32窗口；--simulate-foreground 只供明确模拟前台的受控IPC组合检查
```

IntegrationTests 运行真实 Python 子进程、现有 loader、管道传输与 WPF UI 线程图片消费者；输入是测试图片时不能称为真实地图、桌面可见像素或游戏验收。

WPF 与下述开发命令使用同一 Qt-free Python 服务。它复用当前 Python `LiveRuntime`，默认把网格候选交给延迟启动的持久 C# 服务；其余求解和接受/拒绝保持 Python。先按仓库 `requirements.txt` 准备完全匹配的隔离 Python，不能用系统中任意 OpenCV 版本代替（S1 会校验实际版本）。

```powershell
dotnet build hybrid/StarMap.Grid/StarMap.Grid.csproj -c Release
python hybrid/python/runtime_worker.py --resource-root 'RESOURCE_ROOT' --data-root 'ISOLATED_DATA_ROOT' --frame-root 'SAVED_FRAME_ROOT'
python -m unittest tests.test_hybrid_grid_service tests.test_hybrid_grid_backend tests.test_hybrid_native_capture tests.test_hybrid_runtime_session tests.test_hybrid_runtime_worker tests.test_hybrid_unified_service -v
```

三个根目录参数必须为绝对路径，data root 在资源和帧根之外；不指定的 grid 可执行文件使用当前工程 Release 输出。`--grid-backend python` 走同一外层协议的原 Python grid，用于对照。服务通过 UTF-8 JSONL 接受 `hello/catalog/preview/prepare/set_frame/dispatch/snapshot`；请求有 `session/id`，取消严格匹配当前计算。录制证据不会获得 live 权限。数值错误或进程故障会拒绝该次计算，不自动替换 Python 后端。
