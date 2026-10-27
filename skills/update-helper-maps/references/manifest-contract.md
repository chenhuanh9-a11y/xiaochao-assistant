# 规范化清单与工具契约

这是维护用中间 schema 2，不是当前应用运行时 schema。对原生 index/包体 schema 的转换还需项目适配器；保留原 manifest 并记录其哈希/路径作为来源。工具不会扫描目录猜 ID，也不解析或运行原包体。schema 1 草稿不自动升级，需显式加入结构版本依赖。

```json
{
  "schema": 2,
  "version": "game-build-new",
  "base_version": "game-build-old",
  "mode": "nightmare",
  "tools": {"extractor": "renderer-commit+recipe", "opencv": "4.12.0"},
  "floors": [{
    "layout_id": "stable-layout", "map_guid": "authoritative-guid", "floor": "B1",
    "source_version": "game-build-new", "base_structure_version": "layout-snapshot-new",
    "coordinate_system": "game-native-pixels:x-right,y-down:60px-per-voxel",
    "source": {"path": "inputs/source-bundle.json", "sha256": "64 hex chars"},
    "structure_hash": "64 hex chars", "display_hash": "64 hex chars",
    "artifacts": {
      "clean": {"path": "prepared/clean.npz", "sha256": "64 hex chars", "structure_hash": "64 hex chars"},
      "geometry": {"path": "prepared/geometry.json", "sha256": "64 hex chars", "structure_hash": "64 hex chars"},
      "recognition": {"path": "prepared/features.npz", "sha256": "64 hex chars", "structure_hash": "64 hex chars"},
      "rendered_display": {"path": "prepared/render.png", "sha256": "64 hex chars", "structure_hash": "64 hex chars", "kind": "game-rendered"}
    },
    "author_variants": [{
      "id": "canonical-display", "author_id": "stable-author-id", "base_version": "layout-snapshot-new",
      "base_key": ["stable-layout", "authoritative-guid", "B1", "layout-snapshot-new", "64 hex structure_hash"],
      "baked": true, "transform_applications": 0,
      "asset": {"path": "prepared/author.png", "sha256": "64 hex chars"}
    }]
  }],
  "retained_bases": []
}
```

路径相对 manifest 所在目录，也可显式绝对路径；读取后核对 SHA256。源 bundle 的哈希应绑定实际依赖闭包，引用文件哈希必须由适配器验证。`structure_hash` 是规范化结构内容的哈希，不是作者像素；`display_hash` 是默认渲染显示版本/内容的哈希。四类 artifacts 可在 plan 阶段全部省略；有提供时必须带绑定正确的 structure_hash，rendered_display 还要求 kind。stage 缺任意角色就报告 blocked，不生成该楼层虚假的完整 entry。

一个 floor 身份为 `(layout_id, map_guid, floor)`，source_version 必须等于本清单 version。每个 layout 只绑定一个 GUID，反向也唯一；同一 floor 不可重复。初次导入用 `base_version: null` 且 plan/stage 不传 old；增量 plan/stage 必须用 `--old OLD.json` 提供旧 manifest，new.base_version 必须等于 old.version，模式一致。check inputs 仅校验单份清单，不验证旧基线。变化 GUID/layout 被列为删除/新增及 binding_changes，仍需核实，工具不自动重绑定。

逻辑身份 `(layout_id,map_guid,floor)` 用于当前目标 diff；实际 base_key 为这三项加 `base_structure_version,structure_hash`。source_version 是来源包体版本，base_structure_version 是有证据的结构快照版本，两者不必相同；不要仅依赖可能未随 overlay 更新的 catalog header。`structure_hash` 应涵盖规范化结构及其坐标语义。

`retained_bases` 可放完整旧 floor row，保留自己的 source_version/base_structure_version/structure_hash/artifacts/author_variants，并附旧 producer 的 `tools`（同 doc.tools 的两字段格式）。当前 floors 与 retained_bases 可含相同逻辑身份，但完整 base_key 不可重复；历史 base 不计入当前目标数。工具不会自动把 old 复制进 new，需根据旧作者依赖明确选择保留。coverage.base_versions 分别报告 current/retained，缺产物在 missing_products / retained_missing_products 分列。plan.invalidated_mappings 列旧/新 base、旧作者ID及旧 base 是否保留；它不是已迁移证明。

历史字段名 author_variants 保留，但本次打包仅放每作者每base每floor择优后的一个 canonical display，不把单层图与合图都放进来。`author_id` 是稳定作者身份；为了兼容已写中间清单它可省略，此时 id 本身必须是稳定作者身份，不能用不同来源文件/路线后缀绕过唯一约束。提供 author_id 时，工具拒绝同一author_id+完整base_key的多个entry，即使它们id或字节hash不同。旧/新结构的完整base_key不同，同作者可各保留一份。备选原稿留在外部来源目录，不进入manifest资产集合；脚本不会自动替你择优、裁切或对齐。

作者条目可省略或为空；当前默认显示始终引用当前 row 的 rendered_display。作者 base_version 必须等于所属 row.base_structure_version，base_key 必须包含精确结构hash；baked=true 时 transform_applications=0，否则为1。作者资源只含 asset引用、作者身份与共享base_key，不内嵌base。旧作者可继续依赖显式保留的旧base；迁移需验证新对齐，不能只改ID/版本字段。

裁切必须同步转换坐标：若从原稿裁去左上角 `(x0,y0)`，不缩放时 `p_source = T_crop_to_source @ p_crop`，其中 T 为平移(x0,y0)的3×3矩阵，故 `M_crop_to_base = M_source_to_base @ T_crop_to_source`。若还缩放/旋转，按实际操作组成完整逆映射后再相乘，核查裁切边界与透明区域。已烘焙base像素再裁切时，只保存裁切到base的偏移/坐标绑定，不再应用原稿到base矩阵；具体字段交由运行时适配器编码。

共享base已通过核心识别/贴合验收后，作者资源的base版本/hash、对齐、裁切、透明及坐标绑定验证符合，即可报告“作者资源可用”。不以逐作者重复游戏识别测试为前置条件；runtime是否接入单列。当前collector只核查清单声明/文件hash，输出 `author_resource_validation=not_evaluated_by_this_collector`、`runtime_integration=blocked_adapter`，不会凭字节校验伪造对齐验收。实际验证结果由已核实的作者适配器/证据给出。

比较和算法回归优先1–2张代表难图及受影响链定向；廉价清单、hash与文件加载完整性可全量。报告实际样本与已知失败，抽样不可称全通过；这些约束不是要求新增自动抽样算法。

plan 分类可重叠：新增、结构变化（结构版本/hash或坐标系）、显示变化、作者变体、来源变化（source SHA/版本）、删除候选、绑定变化。保留项也会因工具/源/结构版本/坐标/schema 变化失效。工具只提供 conservative cache key，不证明旧缓存算法可复用。

stage 输出采用 `resources/<sha256>`（无扩展名）、index.json、run.json、.map-update-run.json，同字节资源只存一份。标记绑定 manifest 指纹；同目录只允许相同输入续跑，不允许拿新版本覆盖旧 staging。若中断，仅重用哈希正确的已复制资源；不自动清理未知文件。index.json 的 source key/base_key 为本中间 schema，不能直接交给生产 loader。

状态/退出码：inputs 检查通过为 INPUTS_VALID_ONLY/0；plan 完成为 PLANNED/0（含 blocked gates）；stage 为 STAGED_BLOCKED/3；release 检查为 BLOCKED/3；格式/哈希/基线错误为 INVALID/2。输出与 exit code 均须阅读。当前无 promote 命令，不存在可写 production 的参数。
