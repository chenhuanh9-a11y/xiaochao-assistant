# PC 合作人路线测试材料（待用户审核后转交）

整理时间：2026-11-19 21:50 CST（Asia/Shanghai）

交付对象仓库：https://github.com/chenhuanh9-a11y/xiaochao-assistant  
对口工作：此前 PC 端 [PR #163](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/163)（路线广场导入时校验安卓导航网格版本），用于继续封面生成和广场显示测试。  
本目录只在本机准备，**尚未上传、未发 Issue/PR 评论、未改任何仓库、未调用生产下载或预览接口**。请用户审核后再决定如何放到上述 GitHub 仓库。

本材料与安卓识别卡住、楼层显示等其它任务无关，请不要混进那些修复。

---

## 结论先看

- **没有拿到完整 59 套公开作品的路线坐标包。** 本机没有这 59 套的离线归档，也没有官方“测试库全量导出”文件。不能把公开列表或只有名称/ID 的元数据当成路线包。
- **本机确认有真实路线坐标、且可分享的样本只有 2 套完整广场包，外加 1 份单测格式样例（3 张图各一条短路线，不是作品包）。**
- 文档里记载过的「困难·展十复刻 / 困难·爱吃醋复刻」R2 归档（含坐标）在仓库的 `outputs/` 下，但该目录被 gitignore，本机工作树里**没有这两份文件**。
- 本机还有若干官方**图片图库**（展十、凉哈皮、团子等 `.xingtu-gallery`），已核对 **不含 routePlan / 路线坐标**，因此没有放进本测试包，避免被误当成路线包。
- 公开列表不含坐标、线上下载会计数，与现有服务端源码一致。已上架的纯路线包另有**预览授权接口，源码写明不写下载记录**；本次为避免触发任何生产对象读取，**没有调用该接口去补齐其余作品**。

---

## 1. 实际有哪些 / 缺哪些

### 1.1 已放入本目录、含真实路线坐标的样本

| 文件 | 作品 | 场景 / 难度 | 地图与坐标 | 包 SHA-256 | 来源 |
| --- | --- | --- | ---: | --- | --- |
| `samples/default-lianghapi-hard.xingtu-gallery` | 困难·凉哈皮复刻（作者：凉哈皮／展十／星辰） | 厄运之女 `lady-of-misfortune` / 困难 `classic-hard` | 29 张图，2431 个点（1 楼 1397、2 楼 1034） | `8a22cb9c2fdf7be8b9895d76f1184aa22c1b8177e723bae9266f0768d97a49c8` | 安卓仓库内置默认困难路线包，随源码发布 |
| `samples/season-nightmare-15-r1.xingtu-gallery` | 磷光症（作者：星辰） | 磷光症 `phosphorescence` / 噩梦 `season-s1-nightmare` | 15 张图，60 个点（b1 30、b2 15、b3 15） | `d14d51cc21befa019ab543ffba881bdb66eb07d709b1de6bccb3dbfdf727fcb0` | 仓库测试夹具；provenance 标明为已公开作品，经预览接口于 2026-11-09 采集 |
| `samples/scoped-route-packages.json` | 不是作品包 | 困难、S1 困难、S1 噩梦各 1 条格式样例 | 2 / 6 / 6 个点 | `5523c15ad2fc088a132e84fceab5af29accbb2b32ca573ae0b84d5e589c4b2db` | `mobile/app/src/test/resources/scoped-route-packages.json`，供对照字段，不能计为广场作品 |

两套完整包都是 ZIP，内仅 `map-gallery.json`（格式 4，`contentKind=nightmare-routes`，`imageCount=0`）。坐标都在每张图的 `routePlan.points` 里，不是只有标题列表。

补充身份：

- 凉哈皮复刻：`workId=47b44f94-8201-5eb5-8c2b-e6521cdacca3`，`packageId=da29778a-7c78-5ad8-b576-8089901d1895`，修订 2。路线类型为 `drawn-route`，点是归一化 `x,y`（0–1），带 `artworkSignature`，**包内没有 `navigationSignature` / `navigationVersion`**。目录里同一模板有对应导航签名，需按地图对照，不能把包当成已带导航签名。
- 磷光症：`workId=717f5817-0869-4cce-a703-d5acf581984a`，`packageId=0c460898-e382-3063-9be2-e78da165b6fb`，公开版本 `ce5647d2-316f-4027-8632-2e08ba6a00dd`，修订 1。点是格子 `cell`，带 `navigationVersion=1` 和 64 位 `navigationSignature`，以及 `floorAssociationSignature`。采集说明见 `samples/season-nightmare-15-r1.provenance.json`。

### 1.2 明确缺失（不要当成已经交付）

- **59 套公开作品的路线坐标包**：本机没有。未列出它们的作者个人数据，也未打包。
- **困难·展十复刻、困难·爱吃醋复刻 R2**：文档 `docs/reports/classic-hard-room-goal-replicas-20261111.md` 记录了本地路径和哈希，但文件在被忽略的 `outputs/route-plaza/classic-hard-author-replicas-20261111/room-goals-r2/`，本机不存在。
  - 展十 R2 记录 SHA-256：`1ea36af97b1d0e6c7c6a7948538e3b602b940c4aaf103d3ac3f9b5dc456e305d`，公开版本 `2010fac4-b5b3-5b0a-902c-805be46ebe0d`
  - 爱吃醋 R2 记录 SHA-256：`2996f0cd3e052b7d40c8e4fd79d1e50d6acd7a129e60144537125a4bc469d9f9`，公开版本 `4f6d797d-bd92-5105-ab5f-2e0354a1712e`
- **S1 困难（14 张 106xxx）的完整作品包**：本机没有，只有 `scoped-route-packages.json` 里 106001 的 6 个测试点。
- **S0 噩梦完整作品包**：本机这次没有另放官方噩梦路线包。PR #163 的导航清单覆盖的是 S0 噩梦网格身份，不是路线作品本身。

### 1.3 只读导出约定（源码依据，本次未调用）

公开列表 `GET /v1/community/map-galleries` 只返回作品元数据（标题、修订、下载次数等），**不含路线坐标**。

会增加计数的是下载授权：`POST /v1/community/map-galleries/{version_id}/packages/{mode}/download`，服务端会执行 `download_count=download_count+1`（`account-sync-server/app/map_galleries.py`）。请不要用这个入口批量拉测试包。

已上架、纯路线数据（`content_kind=nightmare-routes` 且 `image_count=0`）可用预览授权，源码注释和部署记录写明**不写下载记录**：

```
GET /v1/community/map-galleries/{version_id}/packages/{mode}/preview
```

其中 `{mode}` 为 `hard` 或 `nightmare`。部署说明见安卓仓库 `account-sync-server/DEPLOYMENT_HANDOFF.md`（2026-10-30 预览接口）。磷光症样本的 provenance 里有一条历史预览 URL，仅作来源说明，**本次没有访问**。若要补齐其余公开作品，需用户另行授权后再用预览接口，且仍应只导出已公开、可分享的路线数据，不要拉草稿、待审或权限不明的私有路线。

当前 PC `app/route_plaza.py` 的 `list_routes()` 请求 `mode=nightmare&limit=50`。若线上实际有跨场景/跨难度合计 59 套，仅用这个列表函数也看不到全部；本次没有请求线上列表，不把 59 这个数字在本机核实为准确计数。

---

## 2. 给 xiaochao-assistant 的接手位置和导入说明

建议审核通过后，把本 ZIP 放到 PC 仓库的测试夹具目录，而不是改正式资源包或 Setup：

- 建议路径：`tests/fixtures/android-route-packages/`（若沿用 examples，也可用 `examples/route-plaza/`）
- 对照文档：已有 `docs/route-plaza.md`
- 导入实现：`app/route_plaza.py`（`read_manifest` / `convert_manifest` / `convert_plan`）
- 导航身份清单：`app/route_plaza_navigation.py`（由 `tools/maps/sync_route_plaza_navigation.py` 生成）
- 对口 PR：https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/163  
  核查用提交：`1b66b51dd2b19ef2a6b745fc928056496db042c0`（2026-11-16 10:06 CST）

离线读取包（不走下载、不增加计数）示例：

```python
import json, zipfile
from pathlib import Path

path = Path("tests/fixtures/android-route-packages/season-nightmare-15-r1.xingtu-gallery")
with zipfile.ZipFile(path) as bundle:
    assert bundle.namelist() == ["map-gallery.json"]
    manifest = json.loads(bundle.read("map-gallery.json"))

# 再交给 convert_manifest(library, manifest)
# 不要使用 download() / POST .../download
```

### 重要：以 PR #163 现有代码直接导入，这两套完整包现在都过不了

这不是包里没有坐标，而是当前 PC 广场导入仍按 **S0 噩梦、格式 3、格子坐标、步长 2** 编写。要对 S0 困难和 S1 做封面/广场测试，需要先扩展导入契约。现有限制（均来自 `1b66b51` 的 `app/route_plaza.py`）：

| 现有假定 | 代码位置 | 与本样本的差异 |
| --- | --- | --- |
| 包格式 `schemaVersion=3`，`mode=nightmare` | `PACKAGE_SCHEMA`、`read_manifest` | 两套样本都是格式 **4**；凉哈皮复刻的 `mode=hard` |
| 模板 ID 只接受 `nightmare-layout-01`–`29` | `TEMPLATE` | 凉哈皮为 `classic-hard-map-01` 等；磷光症为 `season-s1-107xxx` |
| 楼层只映射 `minus-1`/`-1`/`1`/`2` | `FLOORS` | S1 使用 `b1`/`b2`/`b3`（游戏楼层 -1/-2/-3） |
| 点必须是整数 `cell` | `convert_plan` | 凉哈皮复刻是归一化 `x,y` 的 `drawn-route`，没有 `cell` |
| 必须有已登记的 `navigationSignature` | `_check_navigation` | 凉哈皮复刻包内只有 `artworkSignature`；磷光症有签名，但清单里还没有 S1 模板 |
| 网格步长必须为 2，且尺寸与清单一致 | `GRID_STEP`、`route_plaza_navigation.py` | S0 噩梦导航是 `step=2`；**S0 困难与 S1 导航是 `step=4`** |
| 导航清单只有 26 套 S0 噩梦 | `route_plaza_navigation.py`，来源安卓 `aa8712c` | 尚未登记 `classic-hard`、`season-s1`。S0 噩梦 catalog 的 SHA-256 `abef12b9…daf3` 与当前安卓目录仍然一致 |

因此：

- 把这两套包直接丢给当前 `convert_manifest`，预期是整图记入 `missingMaps` 或版本不符，**不会得到可显示坐标**。这正好说明还缺 S0 困难 / S1 的编辑资源和导入扩展，而不是样本造假。
- `editor-identity/template-signature-index.json`（及 `.csv`）给出了三套目录的模板 ID、地图号、当前/兼容导航签名、底图签名、以及每层 `width/height/step/cols/rows`。**只含公开身份和网格尺寸，不含道路边、房间多边形或图片。**
- 若要扩展 `route_plaza_navigation.py`，应沿用已有生成器 `tools/maps/sync_route_plaza_navigation.py` 的方式，从下面给出的安卓目录只同步身份和尺寸，不要把安卓道路图、房间或原图拷进 PC 仓库。生成器目前是为 S0 噩梦目录写的，同步 S0 困难 / S1 时需要同时改步长和楼层名。

封面生成如果只需要读包内坐标和地图身份，可直接解析 `map-gallery.json`，不必先通过 `convert_plan`。落点到 PC 底图前，仍必须用对应场景的签名和 `step` 换算，S1 不能沿用 S0 噩梦的 2px 公式。

---

## 3. S0 困难 / S1 编辑资源的准确路径和基线

安卓仓库：https://github.com/Sakitams/identityv-map  

建议引用分支：`codex/android-1.11.8-windows-validation-20261107`  
建议引用提交：`000fe9b064f44a29a3ad57c61d9ec19553c7be42`（2026-11-18 22:59:23 +0800，说明为 sync 1.11.19 gallery and voice updates）

本材料里的两套路线包和三份 `catalog.json`，在下列提交上**字节相同**：`4c3b635a5e95c77f500e7d13224df487cd22e6c4`（2026-11-10 首次同步进该开发线）、`272d4809821b18d8b62957a5126f6b87af87f0d6`、以及上面的 `000fe9b`。编辑器 JS 在后续提交有改动，但目录身份文件未变。

下面链接均指向 `000fe9b`。只列最小必要文件和目录，不是整库拷贝。

### 3.1 S0 困难（厄运之女·困难，classic-hard）

共用自定义路线编辑器，由困难入口切换上下文：

- 入口：[`mobile/app/src/main/assets/classic-hard-route-editor.js`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/classic-hard-route-editor.js)
- 场景/目录绑定：[`mobile/app/src/main/assets/custom-route-context.js`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/custom-route-context.js)（`classicHard`：`catalogPath=classic-route-editor/catalog.json`，楼层 `1`/`2`）
- 实际编辑实现：[`mobile/app/src/main/assets/nightmare-route-editor.js`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/nightmare-route-editor.js)（导出 `CustomRouteEditor.create`）
- 目录（29 套现行模板，另有 1 个已退役 map-29）：[`mobile/app/src/main/assets/classic-route-editor/catalog.json`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/classic-route-editor/catalog.json)  
  SHA-256：`79f9f11fa600dac735b37c4354cb29c4be52e98c0016b6e30cc64008704d3acc`；`navigationVersion=1`
- 每层导航网格（58 个 JSON，约 3.1 MB，含道路/房间，**未打进本 ZIP**）：[`mobile/app/src/main/assets/classic-route-editor/navigation/`](https://github.com/Sakitams/identityv-map/tree/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/classic-route-editor/navigation)  
  命名：`map-01-1.json` … `map-28-2.json`（无 map-29）；**step=4**
- 原生方案：[`mobile/app/src/main/java/com/xingchen/identityvmapassistant/ClassicHardRoutePlan.kt`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/java/com/xingchen/identityvmapassistant/ClassicHardRoutePlan.kt)
- 底图迁移：`mobile/app/src/main/assets/classic-hard-route-migration.js`，`.../ClassicHardArtwork.kt`，`.../ClassicHardDefaultGallery.kt`
- 目录生成脚本：`mobile/scripts/build-classic-route-catalog.js`，`mobile/scripts/build-classic-route-navigation.py`

### 3.2 S1（磷光症，困难 14 张 + 噩梦 15 张，共 29 套 / 87 层）

S1 困难和 S1 噩梦共用同一份目录，靠 `difficulty` 和地图号区分：`106xxx` 为困难，`107xxx` 为噩梦；楼层为 `b1`/`b2`/`b3`（游戏楼层 -1/-2/-3）。

- 场景/目录绑定：同上 [`custom-route-context.js`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/custom-route-context.js)（`season-s1-hard` / `season-s1-nightmare`，`catalogPath=season-route-editor/catalog.json`）
- 地图来源列表：[`mobile/app/src/main/assets/season-route-sources.js`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/season-route-sources.js)
- 编辑实现：同样是 `nightmare-route-editor.js` 的 `CustomRouteEditor`
- 目录：[`mobile/app/src/main/assets/season-route-editor/catalog.json`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/season-route-editor/catalog.json)  
  SHA-256：`32ea92bd88e2baba4bb835cb45442d4baac702135ffd63e97fd651197777dece`；`schema=identityv-season-route-catalog-v1`，`sourceVersion=2.104.3099014`，`navigationVersion=1`
- 导航网格（87 个 JSON，约 3.1 MB，**未打进本 ZIP**）：[`mobile/app/src/main/assets/season-route-editor/navigation/`](https://github.com/Sakitams/identityv-map/tree/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/season-route-editor/navigation)  
  命名：`106001-b1.json` 等；**step=4**
- S1 底图：[`mobile/app/src/main/assets/season-maps/s1/`](https://github.com/Sakitams/identityv-map/tree/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/season-maps/s1)
- 原生校验：S1 方案走 `NightmareRoutePlan.seasonContract` 与 [`SeasonRouteStore.kt`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/java/com/xingchen/identityvmapassistant/SeasonRouteStore.kt)、[`SeasonRouteLibrary.kt`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/java/com/xingchen/identityvmapassistant/SeasonRouteLibrary.kt)；测试为 `SeasonRoutePlanTest.kt`（没有单独的 `SeasonRoutePlan.kt`）
- 目录生成脚本：`mobile/scripts/build-season-route-editor-catalog.js`，`mobile/scripts/build-season-route-navigation.py`

困难地图号：106001–106005、106007–106015（无 106006）。  
噩梦地图号：107001–107015。

### 3.3 相关：S0 噩梦（PR #163 已用，不是本次缺的那两份编辑器，但签名对照需要）

- 目录：[`mobile/app/src/main/assets/nightmare-route-editor/catalog.json`](https://github.com/Sakitams/identityv-map/blob/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/nightmare-route-editor/catalog.json)  
  SHA-256：`abef12b9e86a52611bb829e78015b63acdd6e1302eb46506d176b30c84b4daf3`（与 PC `route_plaza_navigation.py` 记录一致）
- 导航：[`.../nightmare-route-editor/navigation/`](https://github.com/Sakitams/identityv-map/tree/000fe9b064f44a29a3ad57c61d9ec19553c7be42/mobile/app/src/main/assets/nightmare-route-editor/navigation)（78 层，**step=2**）
- 原生：`NightmareRoutePlan.kt`
- PC 同步来源提交仍是 `aa8712c3f2779e2d9f7a49801632a49ff410d53e`；目录哈希与当前 `000fe9b` 相同。

---

## 4. 版本 / 地图 / 导航签名怎么对应

详细表在：

- `editor-identity/template-signature-index.json`
- `editor-identity/template-signature-index.csv`（一行一层，共 223 层：S0 困难 58 + S1 87 + S0 噩梦 78）
- `editor-identity/sample-package-inventory.json`（两套样本包的字段摘要）

对应规则：

1. 先看包里的 `scenario` + `mode` / `routeCatalogId`，再看每张图 `routePlan.templateId` 和 `sourceMapId`。
2. 用 `templateId` 到对应 catalog 找当前 `navigationSignature`（以及 S0 噩梦的 `compatibleNavigationSignatures`、S0 困难的 `artworkSignature`、S1 的 `floorAssociationSignature`）。
3. `navigationVersion` 目前三套目录都是整数 **1**。
4. 换算格子时必须用该层导航的 `step/cols/rows/width/height`。S0 困难与 S1 为 **step=4**，S0 噩梦为 **step=2**。混用会落错点。
5. 凉哈皮复刻包内点是相对底图的归一化 `x,y`，并带当前 `artworkSignature`（例如 map-01 为 `9c9e5645b07d5b6a8c96a2f606c4f611bb92efca7376760db931bad440c727dc`，与 catalog 现行底图签名一致）。不要按 S0 噩梦的 cell 公式去读它。
6. 单测样例 `scoped-route-packages.json` 里，S0 困难 map-01 用的是历史 `artworkSignature` `5fca6dca…`（出现在 catalog 的 `artworkHistory`），不是现行 `9c9e5645…`。

---

## 5. 本目录文件

- `README.md`：本说明
- `samples/`：两套可分享路线包、磷光症 provenance、单测格式样例
- `editor-identity/`：签名与网格尺寸对照（无道路/房间/图片）
- `route-coordinate-test-samples-20261119.zip`：上面这些文件的最小 ZIP，供转交
- `SHA256SUMS`：ZIP 内外文件的校验和
- `PROGRESS.md`：整理过程记录

原始仓库文件均未改动，本目录只有复制和新建的说明/对照表。

已扫描样本包和对照表，未发现密钥、账号、私钥或敏感日志。未打包作者私有路线、全库数据或官方图片图库。权限无法确认的私有路线：本机没有找到可列出的私有路线库，故无待审清单。

---

## 6. 还需要用户决定的事项

1. 是否允许合作人用预览接口补拉其余已公开作品（不会走会加计数的 download；但仍会访问生产对象存储）。本材料未做这一步。
2. 被 gitignore 的展十 / 爱吃醋 R2 归档若在别的机器上，是否补进测试包（哈希见第 1.2 节）。
3. 审核通过后，由父代理按用户指定，把本 ZIP 放到 `chenhuanh9-a11y/xiaochao-assistant` 的测试目录或 Issue/PR 附件；本整理步骤不会自行发布。
