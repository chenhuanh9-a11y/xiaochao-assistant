# 星图 / 小抄助手

Windows 上的第五人格地图与路线辅助工具源码。主要入口为 `app/identityv_daily_assistant.py`，使用 Python、Tkinter、OpenCV 与 Pillow，包含窗口截图、地图候选识别、当前帧配准、路线显示、资源浏览和离线地图包导入等模块。

**本仓库是试验源码快照，不是可直接使用的完整产品包。** 克隆后可安装依赖、检查源码和构建程序骨架；地图识别与路线显示必须另行提供有权使用、且版本一致的资源。仓库没有地图、作者路线、游戏解包数据、结构底图、品牌 Logo 或安装包，也没有可下载资源的承诺。

## 快照范围

- 整理日期：2026-10-27 UTC；来源为冻结的 `source-snapshot-1.0-suju15`，包含 73 个 Python 文件。
- 对应候选显示版本 `1.0`、Windows 数字版本 `1.0.0.4`，原候选构建指纹 `5E25C1A1`。
- 冻结主程序 SHA-256：`D6D5B20A234286B00DF2B84EEC005E18A3F0A8A6A36FDA4D464253966159F0DC`。
- 原候选是以基线提交 `534cc0be0a6f08c9abc351ff86a44c46a2432068` 加逐文件核验的冻结覆盖文件组装；本仓库建立新的干净历史，没有搬运原开发仓库历史。
- 公开整理只将三个旧原型模块中的个人素材绝对路径替换为环境变量/仓库相对目录。主程序源码保持冻结字节。原始源码哈希和具体整理范围见 [快照记录](docs/source-snapshot.json)。公开源码的构建指纹可能与原候选不同。
- 后续在线功能、困难模式 native 识别的进行中改动未合入；已有困难模式相关模块不代表完整困难资源已经随仓库交付。

## 环境与运行

面向 Windows 10/11 x64、Python 3.13（需要带 Tkinter 的 Python 安装）。在仓库根目录运行 PowerShell：

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --version
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --build-id
```

依赖版本来自该冻结候选使用的本机构建环境。`opencv-python-headless` 的用途是图像计算，界面由 Tkinter 提供，不要同时安装多个 OpenCV Python 分发包。

正常启动命令：

```powershell
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py
```

**正常启动前需要配置完整资源。** 仅克隆本仓库时，缺少 `references/nightmare-map-pack.json` 等生产资源，正常启动或资源校验会报告资源缺失；这属于此源码分发的已知限制。样例配置不会制造虚假的地图识别能力。资源目录、支持的配置项与空索引样例见 [资源分离说明](docs/resources.md) 和 [环境变量示例](examples/environment.ps1)。

## 构建

仓库提供新的、可移植的开发构建脚本，生成不带地图/品牌资源的 PyInstaller 程序骨架：

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
& .\.venv\Scripts\python.exe tools\build.py
```

输出为 `dist/xiaochao-assistant/`。本脚本只打包代码、版本清单、Windows DPI/权限 manifest 与第三方许可，不自动收集本地资源、配置、日志或缓存。产物仍需独立配置合法资源，不等同于原候选安装包，也不承诺与其 EXE 二进制一致。此仓库不发布 Release 或安装包。

## 已知边界

- 当前代码是试验状态；源码/CLI 检查不能替代真实游戏识别、界面手感或外部机器验收。
- 地图身份、楼层或当前帧几何证据不足时，应隐藏路线并由人工确认；缺少资源不会自动替换为猜测结果。
- 原候选的资源和规则仅覆盖其已冻结范围；不应将已有模块理解为所有地图、困难模式或在线能力均已完成。
- 捕获和悬浮层使用 Windows API；不支持将此版本直接作为 Linux/macOS 应用运行。打包沿用原 manifest 的管理员权限要求，应在理解权限含义后启动本地构建。
- `work/`、`outputs/` 是运行时状态、诊断和可能的截图位置；默认忽略，不应公开上传。分享故障资料前需自行检查画面与路径信息。

## 许可与素材

项目自有代码**暂未指定整体许可证**。公开可见不代表已授予 MIT 等开源许可；使用、修改或再分发的授权需另行向权利人确认。没有将第三方图标的许可证套用到整个项目。

已保留的嵌入图标及许可见 [第三方说明](THIRD_PARTY_NOTICES.md)。地图、作者路线、游戏素材与品牌原图不随源码分发；需要另行取得相应授权。
