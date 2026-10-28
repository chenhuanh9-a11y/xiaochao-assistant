# 仓库入口与维护缺口

公开整理日期：2026-10-27。路径相对仓库根目录；不保存本机工作树、内部协调记录或真实素材位置。

| 阶段 | 已存在的代码 | 范围和缺口 |
| --- | --- | --- |
| 中间清单、差异、候选收集 | 本技能 `scripts/map_update.py` / `test_map_update.py` | 标准库 schema 2；stage/release 因生产适配器缺失返回3。 |
| 原生结构批次计划 | `tools/maps/native24/plan_24_native_layers.py` | 历史24图/72层配方，不自动适配任意新版本。 |
| typed NPZ、轮廓、候选runtime元数据 | `tools/maps/native24/build_native_24.py` | 固定批次断言，OpenCV4.12.0；alpha footprint不等于语义墙/可走区域。 |
| 渲染封装 | `tools/maps/render-candidate-structures.cjs` | 用户自有包装脚本，依赖外部 `tools/source_renderer.js` 与合法素材；外部renderer未分发。 |
| 单图game-derived旧画布产物 | `tools/maps/generate_author_game_derived.py` | 固定104001/六产物配方，会更新所选外部索引，须在独立staging使用。 |
| 作者inventory / 候选配准 | `tools/maps/authors/author_audit.py`、`align_authors.py` | 读取配置目录，候选矩阵不是自动验收。 |
| canonical选源/裁切/打包 | `tools/maps/authors/canonical_display_r4.py`、`verify_canonical_r4.py` | 历史R3→R4，复用已有矩阵，固定27组/81层等断言，不自动产生R3人工证据。 |
| 离线快结构预制 | `tools/maps/prepare_fast_structures.py` | 原454生成器，增加独立输出及真实loader回读；只处理已有噩梦共享index，不是困难sharedfast06生成器。 |
| 当前两模式共享预制 | `tools/maps/prepare_shared_fast_structures.py` | preview09-19冻结生成器，通过同版provider枚举/选择生成与回读；需要合法外部资源，不运行识别/求解。 |
| 生产loader / package schema | `app/shared_structure_reference.py`、`difficult_native_context.py`、`star_map_import.py`、`star_map_download.py` | preview09-19应用快照；中间清单不能直接交给生产loader。 |

完整命令与前置条件见仓库 `docs/map-processing.md`。无完整合法资源时，只运行help、语法检查和合成fixture。不要将历史脚本指向活动源目录，不为查参数执行未经审查的顶层脚本。

中间清单收集、作者显示验收和生产runtime接入是不同状态。源包体→规范化schema、完整语义提取、代表难图核心验收、生产增量安装及回退仍需逐批适配；不存在promote命令。源码中保留的历史批次数字不是以后目标数量。


## 2026-10-29 入口候选增量与来源审计

`tools/maps/build_entry_candidates.py` 与 `tools/maps/audit_entry_sources.py` 已提供独立 CLI，见 [维护用法](../../../docs/entry-candidate-preprocessing.md)。候选 schema 为 entry-candidates/1，不与 runtime NPZ 或本技能 manifest schema 2 混用。9 项定向测试通过；132楼层/246邻域已生成，二次132复用。后续只读闭包审计未重生成候选。

已核查：130原生楼层 metadata 的 layout/worldFloor 与条目一致，130次源catalog SHA256匹配；困难map29两层 legacy NPZ SHA256匹配记录，但 examined source/index/provenance 没有原生layout绑定。map29维持legacy/manual working-draft，不从图像相似或地图序号猜原生ID，不阻断其他候选。

仍未闭合：旧catalog七个相对原始模块在其vendor根/data下未找到，不能把嵌入的literal exports当成原二进制存在证明；贴图字节、renderer模块根及source-to-product重放还需补证。当前包体噩梦图像亦明确来自旧vendor资产，不能仅凭mapImages字段相同宣称当前版本纹理字节已重提取。审计只报告部分来源证据，不设置full closure已验证。
