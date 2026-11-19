# 星图 · 小抄助手

作者图包统一沿用通用 V2；准备好图片与逐层坐标后可直接制包，见[标准作者图包制作说明](docs/author-package-preparation.md)。新作者无需逐个修改客户端名单。

星图是面向Windows的第五人格地图与路线辅助工具，使用Python、Qt Quick、OpenCV和Pillow。它读取游戏窗口画面，识别地图与楼层，按当前帧几何验证显示路线，并提供资源浏览、离线地图包导入和可配置的下载入口。证据不足时应隐藏路线，保留人工确认与完整小抄等兜底。

**这里发布源码和维护工具，不分发完整游戏资源或安装包。** 干净结构与配套预制通过独立素材Release提供，见 [结构资源取用](docs/resource-release.md)；作者路线图、四套地图包、游戏渲染纹理、截图与品牌Logo不在当前发布范围。仅克隆仓库不能完成完整地图识别或直接复现产品包。

## 当前代码

当前客户端使用 PySide6 / Qt Quick，源码入口为 `tools/qt_client_entry.py`，界面及客户端服务位于 `experiments/qt_quick/`（目录名保留历史沿革）。旧 Tk 页面、主入口、独立预览与 Tk 构建配方已退役。共享识别、贴合、资源与数据协议仍位于 `app/`，本次清理不改算法或资源字节。

自定义地图与路线广场按灾厄之女／磷光症、困难／噩梦管理。编辑器、安卓路线包互通、投稿及讨论的范围和独立资源准备见 [Qt 路线契约](docs/android-route-parity.md)。

版本以 `version.json` 为准。历史候选的功能及哈希记录仍保存在 `docs/source-snapshot*.json`，不代表当前源码的指纹或完整验收。清理范围、保留的共享测试及 Qt 契约对应见 [Tk 清理记录](docs/tk-cleanup.md)。

## 快速开始

需要 Windows 10/11 x64 和 Python 3.13。在仓库根目录运行 PowerShell：

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r experiments/qt_quick/requirements-client.txt
& .\.venv\Scripts\python.exe tools\qt_client_entry.py --help
& .\.venv\Scripts\python.exe tools\qt_client_entry.py --self-check
```

`--self-check` 默认只检查导入/打包接口，不打开窗口、监听或捕获，不等于地图资源完整。依赖锁定到冻结候选构建环境；`opencv-python-headless` 负责图像计算，窗口由 Qt 提供，请勿同时安装多个 OpenCV 分发包。

源码运行使用独立且带所有权标记的沙盒，不读取或覆盖已安装客户端的数据。先按 [资源配置](docs/resources.md) 准备有权使用的完整资源：

```powershell
& .\.venv\Scripts\python.exe experiments\qt_quick\seed_sandbox.py '包含 _internal 的授权本地资源来源目录'
& .\.venv\Scripts\python.exe tools\qt_client_entry.py --self-check --verify-resources
& .\.venv\Scripts\python.exe tools\qt_client_entry.py
```

默认沙盒为 `work/qt-lab-installation`。种子工具拒绝覆盖已有目标；空 JSON 示例不能用于识别。资源自检通过也不代表真实游戏或视觉验收。

## 地图处理与技能

真实命令、输入输出和缺口见 [地图处理流程](docs/map-processing.md)。

| 入口 | 用途 |
| --- | --- |
| `skills/update-helper-maps/` | 项目维护技能：中间manifest契约、差异计划、内容寻址staging及fixture测试。 |
| `tools/maps/native24/` | 历史24图/72层结构计划、typed NPZ、轮廓和候选元数据生成配方。 |
| `tools/maps/authors/` | 作者清单、候选配准、canonical选源/裁切、校验与打包。 |
| `tools/maps/prepare_fast_structures.py` | 已有噩梦共享结构的独立预制与冻结loader回读。 |
| `tools/maps/prepare_shared_fast_structures.py` | 与本版冻结源码一致的两模式provider预制、选择生成与回读。 |
| `tools/maps/assemble_structure_resources.py` | 核验并解包独立结构Release，不导入作者路线或修改用户配置。 |
| `tools/maps/render-candidate-structures.cjs` | 调用外部合法renderer的候选渲染包装；renderer与纹理未分发。 |
| `app/star_map_import.py` / `star_map_download.py` | 生产数据包与静态索引的schema、路径/哈希校验及传输。 |

先查看安全帮助入口：

```powershell
& .\.venv\Scripts\python.exe skills\update-helper-maps\scripts\map_update.py --help
& .\.venv\Scripts\python.exe tools\maps\prepare_fast_structures.py --help
```

这些代码不是从任意新游戏包到生产更新的全自动流水线。历史配方保留批次数量与证据格式断言，新批次必须先适配。维护技能的 `stage` 和 `check --scope release` 在生产适配器未闭合时返回 **3（blocked）**，不能当成可安装更新成功。

## 构建与安装范围

```powershell
& .\.venv\Scripts\python.exe -m pip install -r tools/requirements-qt-build.txt
& .\.venv\Scripts\python.exe tools\build_qt_client.py --check-environment
& .\.venv\Scripts\python.exe tools\build_qt_client.py --help
```

构建入口为 `tools/build_qt_client.py` / `tools/qt-client.spec`。实际构建须提供干净源码 commit、明确授权的资源根及带哈希的清单、Inno Setup 编译器和新的输出目录。先用 `--plan-only` 校验同一组参数，再按开发流程构建 Setup；不从本机猜测资源，不包含未提交源码，不自动安装或发布。

旧 `tools/build.py` 和 `xiaochao-assistant.spec` 已移除，不再构建 Tk 开发骨架。软件只交付 Setup，地图导入 ZIP 不受此限制。

## 目录与协作

```text
app/                         共享算法、资源loader与数据服务
experiments/qt_quick/        当前 Qt 页面与客户端服务
tools/qt_client_entry.py     当前源码及打包启动边界
tools/build_qt_client.py     Qt Setup 构建
tools/maps/                  地图维护、历史配方与预制入口
skills/update-helper-maps/   本项目维护技能、规范和测试
docs/                        资源、流程及来源记录
examples/                    不含真实资源的配置示例
assets/、references/         外部资源本地位置，实际资源默认忽略
```

协作从最新远端创建独立分支，通过PR审阅。推送前先fetch并读取他人提交，禁止force push覆盖协作者。及时同步完成且一致的冻结源码，附清晰提交和真实验证范围；进行中工作放开发分支/草稿PR，确认后合并。推送后核对远端SHA。仓库本身没有常驻自动同步进程。

`work/`、`outputs/`、`resources-private/`、构建产物、截图、设置与日志均默认忽略。地图脚本生成的外部清单可能带来源路径，分享前须检查；忽略规则不能代替内容审查。

## 验证与限制

0B721D65已在原构建流程中完成无源码覆盖的实际PE代表检查：同设置30候选/30真实缩略图、模式按钮到真实controller往返、真实model/PIL/结构解码及单损坏NPZ局部恢复；末端GUI变量、widget、PhotoImage等使用明确IO替身。诊断采样复用已核producer/poll/store代际证据。**这些子链不等于完整GUI、真实游戏识别、性能或玩家验收。** 03/10身份歧义仍未解决。本次公开同步只做必要增量语法/help和内容检查，不重复全图库识别或实际PE验证。

86E85F7F另有10项捕获协议、28项实际资源显示等价/失效检查及6项包内CLI检查。关键路径回放使用合成地图截图、截图IO替身、隐藏Tk和PixelSink，不能作为玩家原始帧或物理按键延迟。用户随后实测反馈明显改善并同意提交PR；该反馈仅针对本次测试范围，不代表所有地图和困难模式全面验收。

Windows捕获与悬浮层API限制平台支持。身份、楼层或当前帧几何不可信时不能猜测显示。维护流程中“作者资源可用”和“runtime已接入”分列；历史矩阵、alpha轮廓和哈希不能单独证明图像语义或生产精度。

## 许可

项目自有代码与维护技能**暂未指定整体许可证**。公开可见不等于授予MIT等许可，使用、修改或再分发授权需向权利人确认。没有擅自为整个项目新增开源授权。

嵌入Lucide/Feather与Microsoft Fluent图标的来源和许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。外部依赖遵循各自许可；地图、路线、游戏素材、renderer与品牌原图需要另行取得授权。
