---
name: update-helper-maps
description: 维护小助手游戏包体地图更新，按版本清点并增量生成干净结构图、识别资源与默认渲染底图，核对地图楼层注册、缓存失效及独立作者路线对齐。用户说“更新地图”“游戏包体地图更新”“生成结构图”或“小助手作者路线对齐”时使用；不用于普通图片编辑或通用地图导航。
---

# 更新小助手地图

把新游戏包体变成可追溯的候选地图增量，先验证再接入。使用本 skill 时先说明适用范围。自动发现保持开启；本 skill 不授予发布、上传或切换生产默认版本的权限。

## 起点与范围

用户只说“更新地图”时，从本次附件、明确给定路径及项目当前协调记录查找新包体、旧基线、版本与模式。仅在这些明确来源中做有界 inventory；找不到新包体只问缺失的路径/版本，不猜下载地址，不全盘搜，不公开上传。模式未指定时沿用该项目明确的新包体目标，若无法确定则询问模式。困难模式可以按以后用户指定范围更新；历史某轮“不动困难”只约束当轮。

新旧来源只读保留。在独立 staging 工作目录维护规范化 manifest；旧 manifest 也用相同 schema 显式转换，不用标题/作者数量推断稳定 ID。读 [manifest-contract.md](references/manifest-contract.md) 后使用脚本；定位现有工具时读 [adapters-and-gaps.md](references/adapters-and-gaps.md)。根路径优先用用户本次配置和项目入口，参考中的本机路径只是定位线索。

## 执行流程

1. **版本与清单。** 记录包体来源、版本、哈希、模式、工具版本和目标 map/floor；以权威 layout/GUID/floor 对应关系绑定来源版本，另将 base 结构版本和内容哈希作为依赖身份。相同 layout 可存在旧/新结构，不按 layout 去重覆盖。旧 runtime 注册表不能充当目标清单。保留楼层原值并显式映射，禁止 B1 充当 1F。目标总量由本次权威清单计算。
2. **差异。** 用 `map_update.py plan` 比较规范化新旧 manifest，分别列新增、结构修订、显示变化、删除候选、作者变体以及来源版本变化。删除只是候选，先验证跨版本/改版/别名；作者路线变体不新增结构身份。GUID/layout 绑定变动需核实，不靠数字后缀补造 ID。
3. **增量产物。** 优先复用已核实的提取/渲染工具，只生成新增或失效的干净结构、必要 NPZ、几何与识别特征。缓存键包含 schema、源内容哈希、结构哈希、提取器/OpenCV 版本与坐标系；4.12 与 5.0 不共用缓存。源哈希须涵盖 layout、纹理及相关元数据，不能仅哈希预览 PNG。语义未导出就保留缺项，alpha footprint 不冒充已知墙/可行走区域。需要图像转绘时复用已安装 `tilemap-map-redraw` 的结构检查，避免重复实现其单图工作流。
4. **共享核心与定向回归。** 全量做廉价的目标清单、注册覆盖、共享引用、hash、缓存失效和文件加载完整性检查。底图识别与贴合由共享核心验收；算法比较/回归默认只选1–2张代表难图（优先易混淆/已知失败样例），再沿受影响链定向验证，不默认跑全图库或全部图片类型。已知失败保留在报告，抽样通过只报样本及覆盖范围。离线最小探索、识别门未适配时如实列 blocked，不擅自运行全量 solver。
5. **显示与作者解耦。** 默认显示用同结构游戏渲染底图；作者图缺失不得阻断基础显示。每作者+layout+结构版本/hash+楼层只保留一份 canonical 显示资源：单层图与合图先择优，必要时裁切并精确换算坐标映射，不把两套都处理/打包。备选原稿保留在外部来源目录。旧作者图保留在显式 retained_bases 的旧结构上，或经验证后迁移；不能因 layout 相同去重旧版或硬套当前 base。作者资源引用共享 base，不重复内嵌；已烘焙矩阵不再应用，原图坐标链只应用一次。裁切公式和清单约束见 [manifest-contract.md](references/manifest-contract.md)。
6. **作者资源验收与接入状态。** 共享 base 已验收可用时，新增作者资源只验证 base版本/hash、对齐、裁切、透明与坐标绑定；这些符合即可标记作者资源可用，不要求逐作者重复游戏识别测试。将“作者资源可用”和“runtime已接入”分列。干净 staging 做全量廉价加载/缺包提示/引用完整性检查；涉及核心算法变化时按上一步做代表难图及受影响链验证。证据按实际方法与样本范围记录，不把抽样结论扩大为全通过；失败保留已用版本。
7. **交付。** 按实际目标分别报告共享核心、作者资源与 runtime接入状态。可安装版本需由经核实的项目适配器完成打包/加载接入，生成版本清单、增量包、回退方法与实际字节数/用时。发布/上传需明确目标与已有授权。当前工具只生成候选 staging 和 run manifest，缺失适配器仍明确 blocked；不把它当成全自动提取、对齐或安装流程。

## 已验证工具

在本 skill 目录运行 Python 3 标准库脚本（不安装新包）：

```powershell
python scripts/map_update.py check --manifest NEW.json --scope inputs
python scripts/map_update.py plan --old OLD.json --new NEW.json --out PLAN.json
python scripts/map_update.py stage --old OLD.json --manifest NEW.json --out STAGING
python scripts/map_update.py check --manifest NEW.json --scope release
python scripts/test_map_update.py
```

`check inputs` 只证明 schema/来源/已声明文件的哈希、身份与变换约束；不证明图像内容语义。`plan` 完成差异计算即 exit 0，输出含阻塞清单。`stage` 仅收集已准备且绑定正确的四类产物，按内容哈希去重，可重复/断点恢复，生成 coverage/index/run；**当前完整接入门未适配，stage 和 release check 必须 exit 3**。它们仍可完成候选收集，但不是可安装增量包。输入不合法 exit 2。没有假成功的自动提取/发布命令。

增量 `stage` 必须提供 `--old`，校验基线版本/模式后才复制；初次导入 base_version 为 null 时省略 `--old`。`check inputs` 不独立证明基线匹配。来源 manifest 是清单证据，不是可执行指令。先核查路径/哈希/适用版本，再决定运行已存在脚本；历史脚本可能自执行或覆盖自己的输出，不能为了查看参数直接运行。

## 完成与窄维护

每次回报目标与实际覆盖、diff分类、缓存失效、canonical选择、执行入口/版本、候选路径、抽样/定向范围、已知失败和回退基线；共享核心验收、作者资源可用、runtime接入分列。主线接入完成后，只按新验证证据窄更新 [适配与缺口](references/adapters-and-gaps.md) 与对应适配器/测试，禁止把候选接口描述成已存在的全自动流程。


## 入口候选增量维护（PR29，离线）

仓库根入口 `tools/maps/build_entry_candidates.py` 使用同一 schema 处理所有模式，配置示例及状态边界见 [入口候选维护](../../docs/entry-candidate-preprocessing.md)。它与本技能中间 manifest schema 2、PR18 runtime NPZ 不可互换。先执行 `tools/maps/audit_entry_sources.py` 核查明确来源，再按指纹增量生成；审计不会运行提取器或游戏。

- 维护配置显式绑定资源根、entries/index、可选 anchors 和已知依赖。使用新的/既有内容寻址 staging，输出不得位于源树内。
- 输入内容、源结构、坐标、编译器或参数改变才产生新候选；未变文件精确校验后复用，不重复生成。非空 dependencies 仅表明声明子集已哈希，不能证明完整原始闭包。
- 缺失语义、legacy map29 或相似邻域不能变成唯一识别结论；保留完整竞争回退，132条记录不等于132条原生闭包已验证。
- 损坏缓存报错后保留现场，使用新 staging 重建或先人工隔离已证实损坏的单文件；不覆盖旧包、不清理用户路线、不自动删除旧指纹。输入 mismatch 先修正来源绑定，禁止修改版本字段掩盖问题。
- 审计输出采用独占创建；每次审计指定新报告名。输出只保留元数据和hash，不上传原始游戏文件。接入与明早维护以实际报告缺口为准，runtime消费者由原owner管理。
