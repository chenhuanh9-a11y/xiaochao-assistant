# 1.1.9 结构包独立支持新地图：事实盘点

> 范围：只记录固定基线 `main@502ec2a9c690`（版本元数据 1.1.9）可由源码和现存 PR 复核的事实，供独立路线图任务评估。这里不是新结构包规范，也不排序版本、不替代最终设计审查。文中的“未做/未知”表示这次没有证据证明已完成，不等于证明不存在。
>
> 源码链接均固定在基线提交 `main@502ec2a9c690`；[版本元数据](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/version.json) 为 1.1.9。PR 状态按 2026-11-15 核实。

## 结论摘要

- **1.1.9 的更新对象已经是可导入的独立资源包**：基础识别资源、楼层控件与作者显示有版本化组件、清单、哈希和安装/激活事务；程序不靠包文件名授予可用性。入口与整体运行边界见 [`docs/development.md`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/docs/development.md#L44-L52) 和 [`app/imported_resource_packages.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/imported_resource_packages.py#L27-L48)。这提供了“同一已支持运行机制下增补地图”的基础，但不证明只交一个包就能支持任何新地图。
- **赛季/模式仍有程序白名单**：包清单只接受 `s0/s1 × nightmare/difficult` 四组；Qt 启用入口同样硬编码四组；原生基础包导出器仅接受 S0/S1 与困难/噩梦。未来赛季、普通等新模式或新能力都还需要程序适配，不能从目录可新增显示名推断出包可启用。[清单](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/season_catalog.py#L1-L12) 只说可扩展显示目录，且明确它不授予 provider 资源访问权。
- **地图与楼层身份受基础数据和消费接口约束**：基础组件给出身份、typed index、版本绑定、坐标与地图楼层数据；加载器再把它们交给原有运行时。地图清单/结构/坐标契约不匹配时，资源不能仅靠改名接入。具体未来地图是否能只用现有格式表达，须对照真实资源与客户端消费者逐图证明；仓库克隆不包含完整可运行的生产游戏资源。
- **作者路线可跨同一基础组件的兼容版本**：已合并 PR #168 允许按当前已启用基础组件解析作者路线，不必保留制包时的旧版本；地图、楼层、结构键、坐标空间、来源快照、画布和配准仍逐层校验。这减少了包版本耦合，但没有放宽实际结构或坐标兼容性。[当前说明](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/docs/development.md#L44-L50)；[#168 已合并](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/168)。
- **至少有两个路线广场互通缺口正在单独处理**：基线 `app/route_plaza.py` 仍按旧 `templateId`/格子坐标与 PC 旧画布转换；基线没有 `app/route_plaza_navigation.py`。#163 仍开放，处理安卓导航网格版本校验；#134 仍开放草稿，处理图库/广场四范围适配。两者与本报告的资源消费调查有重叠，不能算作基线已完成能力。[基线路线广场代码](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/route_plaza.py#L1-L15)、[#163](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/163)、[#134](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/134)。

## 资源链路：入口到最终消费者

| 阶段 | 基线中可复核事实 | 实际限制 / 状态 |
| --- | --- | --- |
| 用户操作入口 | 「地图资源」提供导入、下载、包库存、激活、作者路线选择和恢复入口；Qt 服务通过资源租约串行化激活和删除。入口由 [`client_imported_resources.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/experiments/qt_quick/client_imported_resources.py#L293-L380)（恢复 `L660-L681`）及 [`ClientResources.qml`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/experiments/qt_quick/ClientResources.qml)、[`ClientOnlinePackages.qml`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25b5/experiments/qt_quick/ClientOnlinePackages.qml) 承载。 | **已做（源码）**。没有本轮 Windows GUI 手动验收证据。下载与安装分开；下载完成不代表已安装或启用。 |
| 配置与状态 | [`season_catalog.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/season_catalog.py#L7-L12) 暴露 S0、S1 显示定义；[`season_context.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/season_context.py#L12-L59) 汇总已知上下文；[`imported_resource_packages.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/imported_resource_packages.py#L27-L48) 清单 schema 为 v1，兼容控件银行格式 v2，登记组件类型和四个上下文。 | **部分可配置**。新增地图可作为受支持上下文的资源数据；新增赛季/模式需同步程序中的白名单、provider 与设置/UI。显示目录不是能力注册表。 |
| 下载 / 导入 / 启用 | 下载模块采用静态 HTTPS 索引与 ZIP，索引上限 256 KiB、包体 1 GiB，校验后交给导入器；没有默认线上地址，也不自动安装、重试或重启（[`star_map_download.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/star_map_download.py#L1-L4)、[`限制常量`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/app/star_map_download.py#L25-L28)）。导入和激活分别做清单/文件预检、依赖检查与提交/回滚；待恢复事务先恢复（[`client_imported_resources.py`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/experiments/qt_quick/client_imported_resources.py#L293-L380)、[`恢复入口`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/experiments/qt_quick/client_imported_resources.py#L660-L681)）。 | **已做（源码）**。部署索引、实际包体、下载分发和线上往返未验收。更新包内容不是客户端自动发现的发布流程。 |
| 资源 provider | 原基础包 provider 验证 package/component 引用、兼容声明和资源快照，再把独立包的原始资源接到既有 Qt 引擎；[`docs/development.md`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/docs/development.md#L44-L52) 说明原 typed/legacy 结构和 fast 数据仍由原实现消费，客户端不会因包存在就把资源视为语义可用。 | **同机制地图有条件复用**。须具备现有 provider 可读的身份清单、typed/legacy 结构、坐标、参数、楼层控件和绑定。provider 不能凭 manifest 任意创建新游戏机制。 |
| 最终消费者 | 身份/地图/楼层资源进入识别和结构查询；楼层控制数据供「楼层判断」；基础结构供「路线贴合」；作者显示与自定义路线随后由显示桥和画布变换进入「路线显示」。[`docs/development.md`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/docs/development.md#L19-L22) 给出消费顺序，[资源边界](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5/docs/development.md#L44-L52) 给出限制。 | **实际使用仍需各消费者兼容**。身份规则、楼层规则、贴合规则或显示授权改变不属于包更新，涉及识别逻辑时须另行确认。 |

## 逐项能力与缺口

状态用语：**已做**=基线源码可定位；**在做**=另有开放 PR/任务；**未做**=源码明确不支持或缺少接入；**未知**=没有足够证据。表内不是运行验收结果。

| 能力 | 基线事实与证据 | 状态及边界 |
| --- | --- | --- |
| 赛季与模式发现 | `season_catalog.py:L7-L12` 仅列 S0/S1；`season_context.py:L12-L24,L31-L59` 校验目录；资源清单 `imported_resource_packages.py:L37-L39`、Qt 激活入口 `client_imported_resources.py:L16-L25` 和导出器 `tools/export_original_qt_base.py:L26-L44` 都限定四个组合。 | **同机制加地图：部分已做；新赛季/模式：未做**。新增目录行不足以接通全链。 |
| 地图与楼层身份 | 基础包上下文和 typed identity/coordinate 接口由 manifest 与 runtimeInterfaces 声明；S1 provider 还会逐一校验版本绑定（`app/s1_native_resources.py:L25-L62,L110-L129`）。地图 ID、楼层布局及其资源关系是实际消费者输入。 | **现格式内新增地图：有条件；新增身份语义/楼层结构：未知或需程序适配**。没有真实新图包，本轮无法验证具体地图能否用既有身份表达。 |
| 独立目录、版本及缓存 | 已有版本目录、组件引用、manifest SHA256 和文件摘要；读取视图缓存 manifest 身份，拒绝内容替换（`imported_resource_packages.py:L71-L82,L125-L180`）。作者资产、结构缓存有自己的版本/所有者键；见 `author_route_catalog.py:L115-L143` 与 `structure_cache_memory.py`。 | **机制已做**。缓存键能否覆盖未来格式或新增消费者，需按具体接入复核；没有内存/真实资源压力验收。 |
| 格式与客户端能力版本 | 当前支持包 schema v1、控件银行格式 v2、组件 schemaVersion 1；客户端兼容字段、依赖和组件类型都校验（`imported_resource_packages.py:L27-L48,L186-L263`）。基础包出口有 `xingtu-original-qt-base-v1`，另有中间维护清单与生产包 schema，二者不是同一合同（`docs/map-processing.md:L7-L17,L112-L114`）。 | **当前契约已存在，扩展规范仍待定**。不能把中间 schema 或现有版本号直接宣称为新契约；客户端能力版本不是任意前向兼容承诺。 |
| 结构与坐标绑定 | `docs/development.md:L44-L52` 记录逐层核对实际地图、楼层、结构版本键、坐标空间、参考画布与配准矩阵；换作者不得改变结构位姿。导出器仅接收显式来源命名空间，并做原格式预检（`tools/export_original_qt_base.py:L26-L58`）。 | **绑定检查已做**。新地图需带对应结构、坐标及楼层资产；重定义坐标或结构关系可能需要消费者/识别逻辑改动。 |
| 控件数据 | 控件资源以独立 `controls-profile` 组件关联上下文；S0 模板由程序固定数据导出，S1 使用既有参数/精度。出口和运行时校验银行 ID、生成器版本与数值合同，见 `docs/development.md:L52`。 | **已做既有上下文**。新控件类型、楼层数或检测能力不能假设仅改包。真实缺失模板、目标 Windows 构建均未验收。 |
| 已登记相似组 | 运行时依据已登记组和相似资源检查；包组件类型包含 `reviewed-group-registry`（`imported_resource_packages.py:L33-L39`）。维护规约要求组成员经复核登记，不允许任意扩大。 | **只允许已有规则/登记能力**。新增相似组成员属于「地图识别」规则，不能以结构包独立更新为名自动写入或承诺免程序更新。 |
| 导出与预检 | `tools/export_original_qt_base.py` 固定 S0/S1 与噩梦/困难，筛选资源命名空间、扩展名，接着调用 `validate_original_package`（`L26-L58`）；`docs/map-processing.md:L102-L114` 描述真实 provider 导出和明确的适配缺口。 | **部分已做、非通用流水线**。维护工具明确不自动完成包体提取、语义转换、生产增量安装/回退；输入不完整须停下。 |
| 没有作者路线时的显示 | 作者路线是可选的独立 display 组件；没有该组件时基础结构 provider 仍是独立对象。显示桥按作者选择和已核验资源提供内容，身份/贴合不由作者选择决定（`docs/development.md:L19-L22,L44-L50`）。 | **源码边界已做**。无作者路线时实际 UI、原生结构底图及路线隐藏/提示效果没有本轮 GUI 验收，不能称已验收。 |
| 导航 / 编辑底图 | `app/custom_maps.py:L1-L15` 区分编辑底图、道路图和显示变换；路线广场固定旧模板和每格 2px 的坐标换算（`app/route_plaza.py:L1-L15,L33-L43`）。 | **当前格式受限**。新编辑底图、安卓导航网格或导航签名需要各自兼容；#163/#134 尚未合入基线。 |
| 自定义图库的保存与迁移 | `custom_maps.py` 使用图库 schema、旧图库迁移和有界路径点；路线以有序目标数据保存，不是把路线固化成图片（`L12-L15,L36-L47`，`GalleryStore` 见 `L540-L672`）。升级与资源迁移各自有事务/恢复代码。 | **既有格式已做**。跨新画布/地图 ID 的迁移映射未证实；升级回滚不能自动解决语义迁移。 |
| 路线广场导入 / 导出与安卓互通 | 基线 Qt 导入仍是旧 `templateId`、网格与旧编辑画布模型；当前 PR #134 提供四范围迁移，#163 提供导航版本校验，均未合入。`docs/route-plaza.md` 记录上传还要求与安卓真实导航资源配套。 | **在做 / 生产互通未知**。本机合成数据或离线测试不能证明线上服务和安卓版本往返。 |
| 升级、回退、中断恢复 | 基础包启用/删除用 catalog 事务、快照和 journal；Qt 启动时会识别 pending 状态并提供恢复，见 `client_imported_resources.py:L234-L266,L660-L681` 与 `app/imported_resource_activation.py`、`app/imported_resource_removal.py`。 | **源代码已有机制**。断电/磁盘故障恢复、真实 Windows 文件锁、从旧资源迁移和安装升级联合验收均未由本任务验证。未知/损坏数据会保留或要求显式恢复，不保证任意状态自动修复。 |
| 下载和分发 | 下载器对静态索引与 ZIP 做大小、HTTPS、取消和摘要边界，无默认 URL（`star_map_download.py:L1-L4,L25-L28`）；`docs/development.md:L143-L149` 说明已安装下载包的有界清理。 | **客户端传输已做**。真实发布目录、下载 CDN、包签名/授权策略及多版本分发未在仓库中被本轮证实；无线上往返验收。 |
| 冻结程序资源闭包 | `docs/development.md:L50-L52` 说明原始资源由 provider 从独立包加载；正式程序不应因独立更新而内置整套地图。 | **未知 / 未验收**。本任务没有构建 Windows 冻结程序，也没有逐项核对打包 manifest、缺省安装、资源目录与干净机器启动。只读清单不能证明冻结包无隐式资源依赖。 |
| 诊断 | `排查与帮助` 可导出诊断；安全限制、打包内容与用户画面边界见 `app/diagnostic_export.py`、`app/diagnostic_privacy.py`、`docs/development.md:L149-L160`。 | **邻接能力存在**。新包缺失、版本不匹配、回滚失败是否都有用户可读诊断及脱敏证据，需按新格式补验；未在此改变诊断代码。 |

## 14 个正式模块：范围与后续检查入口

本任务实际改动范围为「地图资源」「设置与主界面」「排查与帮助」；其余模块只做只读依赖核对。相关测试名是后续入口，不表示本轮已运行或通过。模块名称、依赖和检查入口按中台 14 模块正式清单核对；仓库自身开发说明另见 [`docs/development.md`](https://github.com/chenhuanh9-a11y/xiaochao-assistant/blob/502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5/docs/development.md)。

| 正式模块 | 与结构包独立更新的关系 | 后续检查入口（历史建议，不代表通过） |
| --- | --- | --- |
| 地图识别 | **相邻且高风险**：身份资源可由基础包供给，但候选判断、身份确认与已登记相似组属于程序逻辑。报告不改识别或相似组。 | `tests/test_native_similarity_runtime.py`、`tests/test_front_gate_observation.py`、`tests/test_side_entry_search.py`、`experiments/qt_quick/test_client_similarity_protocol.py` |
| 楼层判断 | **相邻**：楼层模板/控件随资源供给；运行规则及楼层状态转换仍由客户端决定。新增楼层形态可能超出当前合同。 | `tests/test_floor_contract.py`、`tests/test_floor_observation_evidence.py`、`tests/test_s0_floor_highlight_geometry.py`、`tests/test_s0_vertical_controls.py`、`experiments/qt_quick/test_client_floor_region.py` |
| 路线贴合 | **相邻且高风险**：消费基础结构、地图/楼层和当前帧几何；本任务不改算法或位姿授权。 | `tests/test_s1_runtime_pose_prior.py`、`tests/test_structure_cache_memory.py`、`experiments/qt_quick/test_client_live_runtime.py`、`test_client_group_queue.py`、`test_client_aligned_transform.py` |
| 游戏画面采集 | 无直接包依赖；为识别/贴合提供上游游戏画面。 | `experiments/qt_quick/test_client_capture_full.py`、`test_client_capture_compat.py`、`test_client_recapture_focus.py`、`tests/test_capture_preparation_privacy.py` |
| 本局与快捷键 | 无直接包依赖；当前对局上下文限制资源浏览不能切换本局模式。 | `experiments/qt_quick/test_client_session.py`、`test_client_input.py`、`test_client_map_input.py`、`tests/test_control_layout.py` |
| 手动选图 | **相邻**：可选地图列表来自已激活上下文，手选结果仍走共同锁图提交。 | `tests/test_manual_map_selection.py`、`experiments/qt_quick/test_client_manual_map.py`、`test_client_manual_map_integration.py` |
| 路线显示 | **相邻**：作者路线/自定义路线由此显示；不得让换包改变地图身份、结构位姿或授权。 | `tests/test_independent_opacity_controls.py`、`tests/test_base_map_display.py`、`tests/test_reference_position_editor.py`、`experiments/qt_quick/test_client_custom_display.py`、`test_client_route_reprojection.py` |
| 地图资源 | **主模块**：下载、导入、库存、激活、作者选择、缓存、升级/回滚及恢复均在本报告范围。 | `tests/test_author_package_management.py`、`test_author_snapshot_history.py`、`test_live_author_import.py`、`test_map_package_upgrade.py`、`test_map_package_removal.py`、`test_download_cache_recovery.py`、`experiments/qt_quick/test_client_downloads.py`、`test_client_import_upgrade_parity.py`、`test_client_package_manager.py` |
| 自定义图库 | **相邻**：路线保存以稳定图库数据为单位；资源底图/道路/坐标变化需要迁移核对。 | `tests/test_custom_maps.py`、`test_custom_map_reachability.py`、`experiments/qt_quick/test_client_custom_gallery.py`、`test_client_custom_display.py` |
| 路线广场 | **相邻且在途重叠**：使用外部账号服务和安卓导航格式；#134/#163 仍开放。 | `tests/test_route_plaza.py`、`test_route_plaza_redirect.py`、`tests/test_route_submission.py`、`experiments/qt_quick/test_client_route_plaza.py`、`test_client_account_lifecycle.py` |
| 开箱连点 | 无直接包依赖；依赖本局状态与设置偏好，不由路线结构包控制。 | `tests/test_chest_auto_tap.py`、`experiments/qt_quick/test_client_chest_auto_tap.py`、`test_client_chest_intent.py` |
| 设置与主界面 | **主模块**：赛季/模式目录、模式锁定和资源可用状态入口；仅改 UI 目录不能增加包/运行时支持。 | `experiments/qt_quick/test_client_settings.py`、`test_client_onboarding.py`、`test_client_announcement.py`、`tests/test_season_session.py`、`tests/test_update_service.py` |
| 排查与帮助 | **主模块的验收相邻项**：需向用户解释包缺失、格式/依赖不兼容及待恢复事务，不上传诊断。 | `tests/test_diagnostic_export.py`、`test_diagnostic_evidence.py`、`test_diagnostic_redaction.py`、`test_automatic_failure_samples.py`、`experiments/qt_quick/test_client_diagnostic_integration.py` |
| 安装与更新 | **相邻**：程序更新、冻结资源闭包、旧包迁移与恢复要和独立地图资源分开检查；本报告不改制包/安装代码。 | `tests/test_installer_upgrade_coordinator.py`、`test_uninstall_data_wizard.py`、`test_game_startup.py`、`tools/verify_installer_compile_only.py`、`.github/workflows/installer-compile-only-windows.yml` |

### 维护用地图资源制作（非第 15 个正式模块）

结构包制作链目前是部分适配的维护工具，不是任意游戏包到可用生产资源的自动流水线。`docs/map-processing.md:L7-L17,L34-L42,L102-L114` 要求外部授权来源、逐批适配并保留 blocked 缺口；`docs/resource-release.md:L26-L51` 说明结构交换包不是完整客户端资源根。后续入口：`skills/update-helper-maps/scripts/map_update.py --help`、`tools/maps/prepare_fast_structures.py --help`、`tools/maps/prepare_shared_fast_structures.py`。这些命令未在本轮运行。

## 已决定事实、在途工作与证据边界

- 用户已决定继续使用 Inno Setup 并补旧 QtIFW 接管；本报告只记录旧文档和当前仓库的不一致，不重新选择安装器，也不把 `docs/development.md` 的历史 QtIFW 描述当成现行安装事实。依据中台决定账本：2026-11-11 卡片 24；P1/P5 的“不内置地图，资源包导入”按已决定事实处理。
- PR #168 已合并，因此“作者路线只能匹配制作时的完全相同基础包版本”不能列为当前缺口；适用范围仍只有同一基础组件兼容版本，并受实际地图/楼层/坐标/画布检查约束。
- PR #142 仍是开放草稿；按照本任务要求保留“必须先读进度说明”的当时流程规则。历史 `progress.md` 声称正式版 1.1.2，但固定代码基线当前 `version.json` 是 1.1.9。本报告不把 P1-P5 或 #142 当成正式版已发布，也不把历史文档当成 1.1.9 事实。
- 本报告与本机任务 `agent/render-map-1-dual-pack`（渲染图）、`agent/load-speed-1-strip-checks`（加载）、`agent/download-speed-1-strip-checks`（下载）只在调查范围重叠；读取可用成果后交由统一整合测试，不复制这些任务的实现。
- 与 #134、#163 的路线广场/导航调查范围重叠；它们各自在自己的 PR 中推进。后续 PR 说明应披露重叠及合并后的统一整合测试需求。
- 本轮只检查了基线源码、现存文档和 PR 元数据；**没有运行仓库脚本、单元测试、Windows 冻结构建、真实资源安装、Windows 游戏、线上下载或安卓往返**。表中测试是候选检查入口，全部不是已通过证据。
- 不能从没有私有生产游戏资源的源码仓库验证地图精度、真实楼层、画面效果、游戏窗口识别、客户端性能或真实发布分发。任何需要改「地图识别」「楼层判断」「路线贴合」判定、相似组成员或路线显示授权的结论，均须从路线图交回确认，不得由本报告授权实现。

## 本轮范围与改动

只新增此事实报告；未改产品代码、测试、资源、制包工具、版本、CI、外部中台配置。模块范围是「地图资源」「设置与主界面」「排查与帮助」；相邻模块如上所列，未点名模块没有被修改。
