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


## 2026-10-29 本轮增量证据

当前配置版本 2.104.3085069 相对 2.104.3059230 新增 layout 104026、104027；已有 layout 无修改或删除。噩梦 26 图/78 层，困难 30 图/60 层（其中 map29 保持 legacy 2 层）。目标数来自本轮清单，不作为后续硬编码。

新增6层正常渲染、typed 二值 footprint、fast 预制已由同 provider 生成，旧132份 fast 字节保持；实际复合运行根 138/138 loader 通过。新增104026 F1合成画面经原生产 rank 可进入候选并通过原门，只有受控算法回放，不是游戏端到端或新增地图全体验验收。旧真实 F1 小结构帧仍保留安全拒绝，不声称全部掉显解决。

新地图图像使用已记录的旧 renderer 纹理资产；当前 texture bytes 未独立重新解码，GPU/动态门/雾等仍未验证。困难map29的权威原生绑定缺口保留。PR29离线候选契约与 runtime 索引不同，未接入的消费者不能因为文件已生成就宣称启用。


本轮新增的参数化入口位于 `tools/maps/`，均只处理显式本机输入，原始 extraction 不提交仓库：

1. `normalize_increment_catalog.cjs --run CURRENT_EXTRACTION --old-catalog VERIFIED_BASELINE_CATALOG --output NEW_CATALOG`：转换当前 literal，保留来源指纹。
2. `render_native_increment.cjs --run CURRENT_EXTRACTION --vendor VERIFIED_RENDERER_AND_ASSETS --output NEW_RENDER --levels CHANGED_LAYOUT_IDS`：仅渲染目标层；未知 layout 报错，不接受空成功。
3. `render_native_entry_rooms.cjs --plan PLAN_JSON --vendor VERIFIED_RENDERER_AND_ASSETS --output NEW_ENTRY_DIR`：提取计划中的入口房间。
4. `build_native_increment.py --base BASE_RESOURCE_ROOT --source CLEAN_REPO_ROOT --plan PLAN_JSON --output NEW_STAGE --entry-inventory NEW_ENTRY_DIR/render-inventory.json --literals NORMALIZED_LITERALS`：写前验证 map/floor/world、PNG/metadata 哈希、画布及输入输出不重叠，再追加原完整索引。
5. `build_native_source_connectors.py --literals NORMALIZED_LITERALS --raw-literal CURRENT_EXTRACTION/literal-exports.json --plan PLAN_JSON --output NEW_CONNECTOR_SIDECAR`：提取明确房型的源端口，不伪造图标或导航折线。

计划须给每层 sourceLayout、runtimeMapId、floor、sourceWorldFloor、nativeCanvasWH、源PNG/metadata路径和SHA256。使用新的输出目录，禁止置于输入目录内。`tests/test_native_increment_contract.py` 覆盖错层、错layout、重复、哈希/路径与输出重叠拒绝。既有 `prepare_shared_fast_structures.py` 只生成新引用；旧文件校验后复用。相似条件仍走原 front-only scanner/builder，完整输入清单包括新增地图。
