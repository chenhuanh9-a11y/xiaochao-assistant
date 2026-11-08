# 标准作者图包（schema 2）

标准包用于为**已经安装的结构地图**添加作者路线展示。程序先升级一次以支持本格式；之后新增作者、路线或部分楼层只发布 ZIP，无需为作者名单更新 EXE。包不包含识别算法、脚本或结构底图。新增结构地图和不兼容格式仍需要对应的结构资源或程序支持。

schema 1 的现有图包继续使用原来的校验及导入逻辑，不要求重新制作。

## 准备目录

```text
prepared/
├── map-package.json
└── _internal/
    ├── assets/authors/author-nightmare-example/
    │   └── map-one-1.png
    └── references/author-packs/author-nightmare-example/
        ├── catalog.json
        └── display-bindings.json
```

`packageId` 同时是包的独立命名空间和路线 `profileId`，格式为 `author-nightmare-<slug>` 或 `author-difficult-<slug>`，最长 64 个字符；slug 使用小写英文字母、数字及中划线。作者和路线名称由元数据声明，不从文件名猜测。一个作者可以为不同模式或路线家族使用不同的包 ID；更新同一个包时保持 ID，修改 `packageVersion`。

`map-package.json` 的制包输入：

```json
{
  "schemaVersion": 2,
  "revision": 1,
  "packageType": "author-display",
  "packageId": "author-nightmare-example",
  "standardAuthor": {
    "profileId": "author-nightmare-example",
    "mode": "nightmare",
    "displayName": "示例作者 · 速刷路线",
    "authorName": "示例作者",
    "routeFamilyName": "速刷路线",
    "packageVersion": "1.0"
  }
}
```

`revision` 是容器修订号，当前为 1；`packageVersion` 是独立于星图程序版本的包版本说明。制包命令自动生成 `files` 和 `requiresFiles` 中的路径、字节数、SHA-256，作者无需手填摘要。导入兼容性由已支持的资源格式与实际基础资源依赖决定，不使用程序显示版本白名单。摘要只提供完整性检查，不认证作者身份。

## 作者目录

`catalog.json` 沿用现有 `AuthorRouteCatalog` schema 1，仅包含当前包的一份 profile：

```json
{
  "schemaVersion": 1,
  "profiles": [{
    "id": "author-nightmare-example",
    "displayName": "示例作者 · 速刷路线",
    "authorName": "示例作者",
    "routeFamilyName": "速刷路线",
    "runtimeEnabled": true,
    "settingsSelectable": true,
    "alignedOverlayEligible": true,
    "maps": {
      "EXISTING-MAP-ID": [{
        "id": "canonical",
        "displayName": "速刷路线",
        "resourceBase": "asset-root",
        "identityStatus": "display-only",
        "displayPolicy": "requires-current-shared-structure-fit",
        "layers": {"1": "assets/authors/author-nightmare-example/map-one-1.png"}
      }]
    }
  }]
}
```

地图 ID、楼层原值必须来自目标基础资源。允许只提供部分地图、部分楼层；缺层继续走现有的结构显示回退，不能借用另一层的矩阵。每张地图只提供一个整理好的 canonical variant，各楼层各自绑定。备选原稿放在包外。

PNG 必须是可完整解码的 RGBA 图片，最多 64,000,000 像素。只能写入当前包自己的图片目录及上面两个 JSON。不能覆盖其他作者、结构资源、程序或用户配置；未声明图片、未使用图片和重复路径都会拒绝。

## 每层坐标绑定

`display-bindings.json` 沿用 `canonical-author-display-bindings-v1`。下面的大写占位符必须从目标安装的坐标包、结构版本登记取得，不能照抄为生产数据：

```json
{
  "schema": "canonical-author-display-bindings-v1",
  "profileId": "author-nightmare-example",
  "canonicalPackId": "BASE-CANONICAL-PACK-ID",
  "coordinateReferenceProfileId": "shared-structure",
  "entries": [{
    "profileId": "author-nightmare-example",
    "mapId": "EXISTING-MAP-ID",
    "floor": "1",
    "assetPath": "assets/authors/author-nightmare-example/map-one-1.png",
    "sourceVersion": "BASE-SOURCE-VERSION",
    "nativeResourceKey": {
      "layoutId": "BASE-LAYOUT-ID",
      "structureRawRecordSha256": "BASE-STRUCTURE-SHA256",
      "floor": "1"
    },
    "structureVersionKey": "BASE-LAYOUT-ID@BASE-STRUCTURE-SHA256/1",
    "matrixAssetToCanonical": [[1,0,0],[0,1,0],[0,0,1]],
    "matrixAssetToBase": [[1,0,0],[0,1,0],[0,0,1]],
    "registration": {"status": "confirmed"},
    "identityAuthority": false,
    "structureValidationAuthority": false,
    "currentFrameGeometryFitRequired": true,
    "crossFloorProjectionAllowed": false,
    "coordinateBridge": {
      "baseToNativeIsIdentity": true,
      "authorGeometryErrorUnchanged": true
    }
  }]
}
```

矩阵映射实际包内图片像素到该楼层 canonical 像素，必须有限、可逆且两份声明相等；示例单位矩阵只适用于图片本来就处于该坐标系的情况。裁切后应同步换算矩阵；已经烘焙变换的图片不得再应用原稿变换。`confirmed` 表示制包者已经核对准备的对齐，不是程序自动证明视觉精度。导入验证声明及结构绑定，游戏显示仍必须通过当前帧结构贴合。

基础依赖沿用现有资源登记：噩梦使用 `nightmare-canonical-coordinate-pack.json`、`nightmare-structure-version-bindings.json` 等；困难使用 `difficult-native-coordinate-pack.json`、`difficult-native-version-bindings.json` 等。旧结构或未知楼层不能冒充当前结构；第一版不在作者包内携带旧结构副本。

## 制包与导入

在源码目录运行，`INSTALL_ROOT` 是含 `_internal` 的目标星图安装根目录：

```powershell
python app/star_map_import.py pack PREPARED_DIRECTORY INSTALL_ROOT --output AUTHOR_PACKAGE.zip
python app/star_map_import.py inspect AUTHOR_PACKAGE.zip INSTALL_ROOT
```

制包时读取目标基础资源，生成依赖清单，然后调用与玩家端相同的完整校验器；不会安装资源，也不会覆盖已有的输出 ZIP。最后打印实际字节数和 SHA-256。成品 ZIP 根目录是 `map-package.json`，负载位于 `data/_internal/...`，沿用现有导入容器布局。

玩家使用星图现有的本地 ZIP 导入入口。安装回执保留作者声明，运行时及资源浏览页据此发现新作者，作者偏好可在重启后恢复。更新、取消、故障恢复及删除复用已有资源事务；用户文件和基础资源的保护保持生效。无需修改作者白名单或在线目录；在线分发只是在此基础上提供下载入口。

机器回归使用临时小型基础资源和真实 RGBA 图片，覆盖两模式的制包、导入、列表、偏好、每层矩阵、像素读取、更新中断恢复及删除；不代表真实作者素材的贴合精度、打包 EXE 或游戏体验已经验收。
