# 同赛季、同模式 provider 隔离准备

本阶段基于独立候选 `3002f82`，在独立工作树开发，不修改冻结测试包候选。不登记或启用 S1，不引入地图素材，不改识别门限、评分、结构/坐标校验或共享识别算法。

## 用户意图与实际边界

新赛季的地图只在自身赛季、当前模式中参与识别和结构比对，不与 S0 或另一个模式的地图合并候选集。噩梦与困难仍共用已有识别框架，差异由各自 pack、结构 provider 和参数表达。目录中显示一个赛季不等于拥有该赛季的资源使用权。

现有 `MapIdentityEngine` 从 `self.pack.maps` / `self.pack.by_id` 取地图范围，provider 向其传入显式根下的结构、正门/侧门索引及相似性目录。因此接入点应是“精确 season:mode 的 pack/provider”，而不是把新旧图放进一个 pack 后再靠 UI 过滤。

## 本阶段实现

- `EngineFactories._resource_binding(mode, season)` 返回不可变的上下文和资源根绑定。目前只有 S0 的原根登记，不扫描父目录、不发现其他季、不将未知季默认解释成 S0。`_get` 在读取缓存前验证绑定，`_build` 在重型导入和资源读取前再次验证；伪造已准备状态或错误缓存也不能绕过未登记季门禁。
- `_build` 显式接收 season，所有结构/正门/侧门/相似性资源仍使用该绑定的单一根。当前注册状态和 S0 资源路径完全不变。
- 生产学习目录调用既有 `memory_root(mode, season)`；隔离诊断目录也预留独立赛季层级，S0 原目录保持。旧 portable layout seed 仅 S0 使用。这里没有为 S1 声明任何新种子。
- 困难模式的旧内置作者 fallback 仅 S0 可用。作者路线仍为显示数据，不成为识别证据。
- `ConfirmedFrontGroups` 显式接收 season，既有人工复核名单只属于 S0。S1 即使复用相同 mapId，也没有已登记分组；不根据结构相近自动生成分组。S0 组 ID、成员和 digest 不变。

## 无素材可以验证什么

使用临时路径、合成 mapId 和可控 worker 验证：两模式缓存各自独立；构建调用携带明确季；缺登记拒绝读取和构建；绕过准备流程直接调用底层构建也拒绝；同名新季地图不能套旧确认组；旧 portable seed 不出现在新季 helper 配置中；S0 实际 typed-resource fixture、取消和迟到构建、准备失败/重试、切季冷重启继续通过。合成输入不能证明 S1 地图内容或完整资源合同已可用。

## 真正接入 S1 前仍需完成

1. 取得有来源且获授权的 S1 地图与索引：地图/楼层身份、结构与版本、正门/侧门、坐标、UI 楼层模板等真实模式合同，以及文件清单/版本/hash。只对该季该模式登记精确资源根；不采用 S0 默认路径。作者显示图与相似组可缺省，但不得编造；存在能力缺口时如实阻断相应功能。
2. 对真实资源运行既有 typed structure、坐标、门、版本和哈希校验，确认 pack 与所有索引包含的是同季同模式范围。现有单根校验不能替代资源来源审核；不能复制一份 S0 清单重命名成 S1。
3. 连同 registry、coordinator `_map_resource_problem`、LiveRuntime `_get_engine` / readiness、EngineAdapter `_rank_saved_frame` 的 S0 硬门统一审查，再允许经过验证的绑定。这些门本阶段均保留，不能单独改 `available` 或 UI 标志解锁。
4. 若需要 S1 图库/导入/作者或自定义路线，另接各自精确上下文协议；现有 S0 ZIP 白名单、收据/依赖/覆盖事务不放宽。缺 S1 资源时仍明确“未登记/未就绪”，不读取 S0 作者、样本或历史。
5. 同季同模式内的真正识别正确率、错层、首贴/复贴可见时延及 Windows 原生游戏流程必须使用真实资源/输入验收。本阶段不声称完成。

## 2026-11-04 验证记录

以 Python 3.12、PySide6 6.11.1、Qt offscreen/software，使用 `PYTHONPATH=app:tests:tools:experiments/qt_quick`，每模块独立进程运行 `python -B -m unittest -v <module>`。模块为：`test_season_provider_boundary`、`test_season_resources`、`test_season_catalog`、`test_season_session`、`test_client_providers`、`test_client_group_registry`、`test_client_front_scope`、`test_client_similarity_protocol`、`test_client_season_preparation`、`test_client_context_failure`、`test_client_coordinator`、`test_client_engine`、`test_client_paths`、`test_client_custom_display`、`test_client_resource_feedback`、`test_storage_retention`、`test_memory_retention_links`。

本轮共 **17 模块、211 项：208 通过、2 失败、1 跳过**。相似性协议两项 `test_established_logical_group_never_runs_internal_member_competition` 和 `test_explicit_map_open_rediscovers_family_without_reselecting_its_member` 的 rank 调用次数断言失败（1 != 2），在未改动冻结基线 `3002f82` 同环境逐模块复现相同失败；未为消除断言改识别策略。链接保留测试有一项跳过，具体原因保留在日志。完整输出及汇总位于忽略目录 `work/integration-evidence/regressions-provider-boundary/`，不称全库全绿。

独立只读复审发现的旧测试 worker 签名不兼容已修复；另一发现是“假定不存在的 seed 文件”不足以保证禁读，已改为 `NightmareMemoryStore` 显式支持 `None` 并在读取入口早退，增加真实 store 禁止任何 `read_text` 的测试。默认参数/S0 行为不变。最终复审无新增阻断，覆盖子集与本次回归重叠，不累计计数；Windows 原生、真实 S1 图和实际游戏未验证。
