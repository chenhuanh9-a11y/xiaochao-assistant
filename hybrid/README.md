# 星图混合迁移开发入口

全量范围、职责、当前进度和未验项统一见 [迁移清单](MIGRATION.md)。当前工程是独立图库切片，不能替代正式客户端；现有 `tools/qt_client_entry.py` 继续保留。

需要 Windows 10/11 x64、.NET 10 SDK、Python 3.13 与仓库 `requirements.txt` 的非 Qt 依赖。默认系统 `python` 必须指向该环境；也可显式指定绝对路径。无需 PySide6。

在仓库根目录：

```powershell
dotnet build hybrid/StarMap.Desktop/StarMap.Desktop.csproj -c Release
dotnet run --project hybrid/StarMap.Desktop -c Release -- --source-root "$PWD" --python 'C:\path\to\python.exe'
```

窗口内选择包含 `references`、`assets` 等内容的有权使用的资源目录；也可选择含 `_internal` 的隔离安装目录，或加 `--resource-root '绝对路径'`。资源不随仓库分发。读取沿用原安装资源锁，会创建/使用 `work/map-packages/import.lock`；不导入、升级、修改设置或操作游戏。资料不完整会显示具体错误，可重新选择目录或刷新。

当前可操作：按赛季/模式、来源、楼层、名称筛选并查看实际 PNG 预览，刷新资源，关闭后等待自有 Python 服务结束。切换/关闭会撤销迟到图片；选择未改变不重读图片。尚未连接本局、热键或浮层；后续完整 Setup 将单独构建和验收。这里的 `asInvoker` 清单仅用于无输入监听的图库测试入口，不能代替未来正式主入口的管理员权限要求。

Python 协议回归和 .NET 消费链检查：

```powershell
python -m unittest discover -s tests -p test_hybrid_browse_worker.py -v
dotnet build hybrid/StarMap.IntegrationTests/StarMap.IntegrationTests.csproj -c Release
# FIXTURE_RESOURCE_ROOT 为含噩梦/困难清单与图片的隔离测试目录
dotnet hybrid/StarMap.IntegrationTests/bin/Release/net10.0-windows/StarMap.IntegrationTests.dll "$PWD" 'C:\path\to\python.exe' 'FIXTURE_RESOURCE_ROOT'
```

IntegrationTests 运行真实 Python 子进程、现有 loader、管道传输与 WPF UI 线程图片消费者；输入是测试图片时不能称为真实地图、桌面可见像素或游戏验收。
