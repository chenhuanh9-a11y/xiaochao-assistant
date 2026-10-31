# Qt 帮助操作图

四张 PNG 直接来自仓库中的实际 QML 离屏渲染，再由 QPainter 拼版、加入编号和说明。
没有生成式界面、游戏截图或作者图；状态、问题记录、地图名称与键位均为明确标注的演示数据。

在仓库根目录执行：

```powershell
& ../qt-integration/.venv-qt-isolated/Scripts/python.exe tools/render_qt_help_images.py --include-next-flows
```

产物：`home.png`、`settings.png`、`resources.png`、`help.png`。
原始截图及来源 SHA256 在 `outputs/qt-help-render/`；该证据目录不打包。
工具强制使用 `QT_QPA_PLATFORM=offscreen` 和软件渲染，不启动真实客户端，不注册输入、不查找游戏、不读写用户配置，不访问网络。
Windows 离屏平台不会枚举系统字体，工具显式读取已安装的 `C:/Windows/Fonts/msyh.ttc` 与 `msyhbd.ttc`；可用重复的 `--font` 参数指定已安装文件。字体仅供本机渲染，不复制或打包；加载失败则停止。
它验证 QML 实际渲染，不代表真实游戏、文件选择器、导入或卸载流程验收。

`client_info.py` 应从自身目录的 `help-images/` 加载；`tools/qt-client.spec` 显式收集本目录四张 PNG 到包内 `help-images/`。
不要放回不跟踪的 `assets/help/`，不要把本地作者图审计副本纳入资源。

集成改变界面后需重新运行工具并查看四张 PNG。导入、卸载和广场的最终界面须等对应实现完成后刷新；不能仅修改图中文字冒充新流程已经实现。
本版成品使用 `--include-next-flows`，以实际 `ClientManualSelection.qml` / `ClientPlaza.qml` 和合成已安装图包状态刷新四卡。省略该参数只生成基础流程。工具还会实际展开四张帮助卡，核对 `Image.Ready`，保存 `consumer-*.png`。
