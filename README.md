# 星图 1.0 · 小抄助手

星图是面向Windows的第五人格地图与路线辅助工具，使用Python、Tkinter、OpenCV和Pillow。它读取游戏窗口画面，识别地图与楼层，按当前帧几何验证显示路线，并提供资源浏览、离线地图包导入和可配置的下载入口。证据不足时应隐藏路线，保留人工确认与完整小抄等兜底。

**这里发布源码和维护工具，不分发完整游戏资源或安装包。** 地图、作者路线、游戏解包数据、结构NPZ、截图与品牌Logo均需另行提供有权使用的同版本资源。仅克隆仓库不能完成地图识别或直接复现产品包。

## 当前代码

| 分支/快照 | 内容与状态 |
| --- | --- |
| `main` 首次公开基线 | candidate15，73个应用Python文件，原候选指纹 `5E25C1A1`。 |
| `dev/current-integration-20261027` | 完整candidate17/P0冻结源码，78个应用Python文件，原候选指纹 `DC9986E2`；本分支用于草稿评审，未自动合并。 |

本分支包含已整合的可选作者显示、地图下载与导入、困难原生资源适配、原454快结构模块，以及困难启动候选列表缺少 `filter()` 和缩略图取图协议的窄修复。有启动问题的D231不是本次同步终点。后续困难共享快路径sharedfast06未混入此完整快照。

显示版本为 `1.0`，Windows数字版本为 `1.0.0.4`。冻结主程序SHA-256为 `934F4CCEFC3EDFD5B75347DFF37A40958223D30019F31CD699557A555264DE65`。逐文件哈希及公开整理差异见 [快照记录](docs/source-snapshot.json)，首次公开来源保留在 [candidate15记录](docs/source-snapshot-candidate15.json)。公共Git历史独立于原开发仓库，没有导入旧历史、内部协作记录或活动工作树。

## 快速开始

需要Windows 10/11 x64和带Tkinter的Python 3.13。在仓库根目录运行PowerShell：

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --version
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --build-id
```

依赖锁定到冻结候选构建环境。`opencv-python-headless` 负责图像计算，窗口由Tkinter提供，请勿同时安装多个OpenCV分发包。

准备完整合法资源并按 [资源配置](docs/resources.md) 设置目录后启动：

```powershell
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py
```

正常启动需要生产索引、结构、坐标与显示资源匹配。空JSON示例只说明接口，不能用于识别。下载服务没有默认服务器，不承诺地图下载或素材授权。

## 地图处理与技能

真实命令、输入输出和缺口见 [地图处理流程](docs/map-processing.md)。

| 入口 | 用途 |
| --- | --- |
| `skills/update-helper-maps/` | 项目维护技能：中间manifest契约、差异计划、内容寻址staging及fixture测试。 |
| `tools/maps/native24/` | 历史24图/72层结构计划、typed NPZ、轮廓和候选元数据生成配方。 |
| `tools/maps/authors/` | 作者清单、候选配准、canonical选源/裁切、校验与打包。 |
| `tools/maps/prepare_fast_structures.py` | 已有噩梦共享结构的独立预制与冻结loader回读。 |
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
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
& .\.venv\Scripts\python.exe tools\build.py --dry-run
& .\.venv\Scripts\python.exe tools\build.py
```

输出 `dist/xiaochao-assistant/` 开发骨架，包含代码、版本、Windows manifest与第三方声明，不收集本地地图、设置、日志或缓存。沿用manifest要求管理员权限并声明Per-Monitor DPI，启动前应理解该权限。骨架仍需独立资源，不是原候选完整重建或二进制一致性证明。

仓库不提供Setup或Release，源码运行不需要安装器。已有本地产品的安装、覆盖和卸载应按其实际安装包说明操作；本仓库没有自动修改已安装程序的命令。

## 目录与协作

```text
app/                         应用冻结源码与资源loader
tools/build.py               可移植开发骨架构建
tools/maps/                  地图维护、历史配方与预制入口
skills/update-helper-maps/   本项目维护技能、规范和测试
docs/                        资源、流程及来源记录
examples/                    不含真实资源的配置示例
assets/、references/         外部资源本地位置，实际资源默认忽略
```

协作从最新远端创建独立分支，通过PR审阅。推送前先fetch并读取他人提交，禁止force push覆盖协作者。及时同步完成且一致的冻结源码，附清晰提交和真实验证范围；进行中工作放开发分支/草稿PR，确认后合并。推送后核对远端SHA。仓库本身没有常驻自动同步进程。

`work/`、`outputs/`、`resources-private/`、构建产物、截图、设置与日志均默认忽略。地图脚本生成的外部清单可能带来源路径，分享前须检查；忽略规则不能代替内容审查。

## 验证与限制

candidate17已在原构建流程中用相同设置与入口完成旧PE异常→新PE通过：30候选/30真实缩略图读取、无源码覆盖，末端GUI对象使用IO探针。**这只证明启动候选子链，不等于完整GUI、真实游戏识别、性能或玩家验收。** 本次源码同步仅检查增量内容、语法/help与无地图fixture，不重复全图库识别。

Windows捕获与悬浮层API限制平台支持。身份、楼层或当前帧几何不可信时不能猜测显示。维护流程中“作者资源可用”和“runtime已接入”分列；历史矩阵、alpha轮廓和哈希不能单独证明图像语义或生产精度。

## 许可

项目自有代码与维护技能**暂未指定整体许可证**。公开可见不等于授予MIT等许可，使用、修改或再分发授权需向权利人确认。没有擅自为整个项目新增开源授权。

嵌入Lucide/Feather与Microsoft Fluent图标的来源和许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。外部依赖遵循各自许可；地图、路线、游戏素材、renderer与品牌原图需要另行取得授权。
