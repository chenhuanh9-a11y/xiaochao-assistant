# 仓库入口与维护缺口

## 2026-11-04 提取数据桥与新赛季边界

新增 `tools/maps/extract/prepare-increment-inputs.cjs` 将当前提取器的四份 JSON
适配为旧 normalizer 的 extraction-manifest 和构建器使用的 normalized-literals。
它校验输入真实路径、模块哈希/大小/来源、typed 字典、核心目录非空、房间引用
和图像键唯一性；不运行模块、不读取真实客户端、不执行注册或发布。
参数及边界见 `tools/maps/extract/README.md`。

新增 manifest 字段和 catalog.inputScope 均是附加来源元数据。旧合法
extraction-manifest 无 scope 时保持可读，输出 inputScope=null，不默认套用
赛季。旧唯一逗号图像键仍可核对匹配旧纹理；冲突键、错误结构、零图输入和
越界软链接不再作为合法输入接收。partial 状态取真实 literal 字段。
生产 map-package schema、中间 map_update schema2 均未修改。

新赛季与旧同赛季增量分别规划，新图只与同赛季、同模式竞争；数据桥仍标记
候选筛选和竞争隔离未执行。旧三层噩梦新增脚本与其完整运行根不能直接推广
为 S1 provider，不能把 S0 候选复制进 S1。真实数据和 provider 回读仍独立验收。

独立集成基线 3002f82 上验证：Node 合成提取/桥接/normalizer 9 项，维护清单
19 项，native increment contract 9 项，public tools 3 项，editor door display
7 项，共 47 项通过；包括旧合法格式合成兼容测试。没有可用的真实历史提取
目录，因此未验证真实历史素材或真实 renderer，不能把此结果写成生产兼容
全通过。未接入 S1、未打包、未推送或发布。

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
