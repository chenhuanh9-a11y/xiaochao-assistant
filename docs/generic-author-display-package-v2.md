# 通用作者显示包 V2

新作者不再登记到客户端代码列表。符合协议且匹配已安装基础资源的图包，使用现有导入事务即可安装、选择、更新和移除。没有逐包签名、人工审批或内置作者名单要求。

本协议校验格式、字节完整性、资源身份及坐标模型，不认证作者身份、版权或几何配准质量。图包只提供显示，不授权地图识别、结构变更或跨层投影；当前游戏帧的身份与几何验证照常执行。错误但格式有效的作者矩阵仍可能造成显示偏移，应由制包者实图检查。

## ZIP 和身份

- ZIP 顶层只有 `map-package.json` 和清单所列 `data/<安装相对路径>` 普通文件。沿用现有大小、重复项、大小写、Windows 路径、链接、文件哈希限制。
- `packageId` 与绑定 `profileId` 相同，格式 `author-` 加小写字母/数字/连字符 slug，slug 首尾必须为字母或数字，最长 48 字符。不要沿用其他图集的 ID。建议 `author-aichicu-s1`、`author-zhan10-s1`，同作者不同赛季或独立图集用不同 ID。
- 图片：`_internal/assets/authors/<profileId>/<season>/<mode>/<mapId>/<floor>.png` 或 `.jpeg`。floor 必须是原生提供方的实际楼层 ID，如 S0 的 `B1`、`F1`，S1 的 `-1`，不能将显示编号当作原生楼层。
- 绑定：`_internal/references/author-packs/<profileId>/display-bindings.json`。
- PNG 允许 RGB/RGBA；JPEG 允许 RGB/L；单帧、最大 8192×8192 且总像素不超过 16 Mi。RGBA alpha 原样保留。

## map-package.json

必需字段：

```json
{
  "schema": "xingtu-author-display-package-v2",
  "schemaVersion": 1,
  "revision": 1,
  "season": "s1",
  "packageId": "author-example-s1",
  "clientCompatibility": {"authorDisplayProtocol": 2},
  "files": [{"path": "...", "bytes": 123, "sha256": "64位小写十六进制"}],
  "requiresFiles": [{"path": "...", "bytes": 123, "sha256": "64位小写十六进制"}]
}
```

`files` 必须恰好等于绑定 JSON 加 layers 声明的所有图片，禁止代码、任意 sidecar 或别人的命名空间。`requiresFiles` 是只读依赖，必须恰好等于全部作者层对应基础资源的依赖并集，绝不作为可写文件导入。

制包时调用 `generic_author_base.resolve_layer(install_root, season, mode, mapId, floor)` 获得权威层元数据和 `dependencies` 路径，再从该安装资源读取每个依赖的 bytes/SHA256。不可改动依赖哈希来迎合另一套基础资源。安装目标基础资源与制包输入不匹配时，明确拒绝。

## display-bindings.json

根字段：`schema="xingtu-author-display-bindings-v2"`、`season`、`profileId`、`authorName`、`displayName`、`identityAuthority=false`、`structureValidationAuthority=false`、`crossFloorProjectionAllowed=false`、`currentFrameGeometryFitRequired=true`、`layers`。

`authorName` 和 `displayName` 是非空纯文字名称，最多120字符，不能含控制字符或尖括号 `<`、`>`；名称不提供富文本或内嵌图片加载能力。

`layers` 为 1–1000 个不重复的 `(mode,mapId,floor)`，可为真实已接入楼层的子集。每层字段：

- `season`、`mode`、`mapId`、`floor`。
- `displayPath`、`displaySha256`、`displayBytes`、`displayShapeHW`。
- 原样使用基础解析器返回的 `referenceResource`、`referenceShapeHW`、`coordinateSpace`、`structureVersionKey`、`baseSnapshot`、`sourceVersion`。
- `referenceSha256` 必须匹配对应依赖。解析器返回 `nativeImageResource` 时必须同时声明该字段及对应 `nativeImageSha256`。
- `matrixDisplayToReference`：有限数值的 3×3 正向仿射变换，末行 `[0,0,1]`；允许继承的横纵不同比例及剪切，拒绝反射、奇异、投影和数值失稳矩阵。每个系数绝对值不超过100000，线性部分两个奇异值均为0.01–100（将旧相似模型对应的缩放界限分别应用于两个主轴）。图像到该层参考画布的变换由制包方提供，读取时不修改、归一化或重新配准。

该仿射能力来自既有显示消费者：S0 `OptionalAuthorDisplays` 接受有限可逆仿射，`AuthorCoordinateAdapters` 使用 `canonical_to_client @ adapter`，Qt运行时在当前帧结构验证成功后使用 `source_matrix @ matrixDisplayToReference`。V2最初复用了旧S1单包相似矩阵门槛，错误排除了可正常继承的S0非等比矩阵；现改为独立的有界仿射验证，旧S1原包仍保持原相似约束。矩阵能导入不等于配准正确，更不允许作者显示矩阵取代结构识别或当前帧验证。

没有强制 `registration.status=offline-reviewed`，也不将图包自述审核结果作为可信声明。缺少作者层时回退该实际层的结构资源，不补造路线，不借其他层或作者。

## 兼容、赛季与生命周期

- S0 旧包与两个现有 S1 包保持原协议、ID、收据和审核哈希集合，旧包不会经 V2 绕过原验证。新旧包可并存，不自动改变用户选择。
- V2 包更新必须沿用自身命名空间；覆盖已有文件仍须通过原收据、备份、提交/取消/恢复事务。不能更新其他作者或基础结构。
- 当前 V2 回执的完整依赖路径闭包还会依据其拥有的层路径独立复核，不能通过删短 `requiresFiles` 释放其他图包的约束。Windows 回执文件名仅改大小写不会使其从约束发现中消失，链接回执会阻断写入。
- 启动修复先校验完整候选资源，再用该候选核对 V2 闭包，以便恢复缺失的基础文件；回执原有依赖哈希继续约束所有待写字节。损坏的 V2 回执不会降级为旧格式保留记录，图包导入、更新和移除仍核对当前安装资源。
- 同 ID 更新可替换原路径或增加层，不可直接删减已安装清单/把 PNG 路径改成 JPEG 路径；原事务没有退休遗漏文件的步骤，此类更新须先通过既有移除操作清理旧包再导入，避免遗留失去收据管理的图片。同 ID 不允许跨赛季替换。
- 基础解析器目前接入 S0 困难/噩梦与 S1 困难/噩梦/普通（`difficulty2`）已登记结构。S0 缺少有效共享结构/坐标绑定的旧资源会拒绝，不能用作者图伪造基础资源。
- 未来 S2/S3 不需要新的作者协议或逐作者名单，但软件须先有该赛季的基础资源 provider、坐标模型及实际运行能力。`generic_author_base.register_provider` 是应用代码注册入口，ZIP 不能自行注册提供方。当前未接入赛季拒绝，不能宣称已能运行 S2/S3。
- 运行时注册按绑定 season/mode 隔离，缓存按图像与绑定哈希隔离，资源更新沿用现有重新加载链路。格式接受不代表真实 Windows GUI/游戏验收通过。

## 实现与交付范围

本补丁基于 `fee2bb1`（`4cf57de` 上的爱吃醋外围透明兼容补丁），不是最新 Windows 1.1.0 源码快照。集成到不同基线须按语义合并并重新验证，不可盲目覆盖。补丁不包含作者素材、私有资源、安装包或发布操作。
