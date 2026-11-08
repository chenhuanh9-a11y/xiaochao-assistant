# 准备并生成标准作者图包

星图 1.1.2 测试分支继续使用[通用作者显示包 V2](generic-author-display-package-v2.md)。这里新增制包输入和命令，不增加第二套运行时 ZIP 格式。已支持该协议的客户端可以导入未在代码中登记的新作者；识别算法或未接入的结构地图仍需要对应的程序/基础资源支持。

先准备图片和逐层坐标映射，玩家端只负责导入，不自动猜图、切层或重新配准。准备目录包含 `author-package.json` 和它引用的图片，例如 `images/map-01-B1.png`。

`author-package.json`：

```json
{
  "schema": "xingtu-author-package-source-v1",
  "profileId": "author-example-s0",
  "season": "s0",
  "authorName": "示例作者",
  "displayName": "示例作者 · 速刷路线",
  "layers": [{
    "mode": "nightmare",
    "mapId": "EXISTING-MAP-ID",
    "floor": "B1",
    "image": "images/map-01-B1.png",
    "matrixDisplayToReference": [[1,0,0],[0,1,0],[0,0,1]]
  }]
}
```

地图 ID、原生楼层和矩阵必须经过制包者核对。上例单位矩阵仅适用于图片本身就在目标参考坐标中的情况。S1 使用 `season: "s1"` 和实际 `mode`、地图 ID、负楼层 ID（如 `-1`），不能套用 S0 或另一层的映射。允许只提供部分地图/楼层，缺层由现有显示逻辑处理。裁切、缩放或旋转图片后，映射必须同步变换；已烘焙的矩阵不能再重复应用。

制包程序从目标安装的基础解析器取得结构版本、坐标画布与精确依赖，并计算图片/基础资源摘要，不需要手工填写哈希或复制程序的作者名单。基础资源不匹配、未知楼层、重复层、无效变换或不兼容图片都会拒绝，不改变目标安装。

在源码根目录运行，`INSTALL_ROOT` 是包含 `_internal` 的目标星图安装根目录：

```powershell
python app/star_map_import.py pack PREPARED_DIRECTORY INSTALL_ROOT --output AUTHOR_PACKAGE.zip
python app/star_map_import.py inspect AUTHOR_PACKAGE.zip INSTALL_ROOT --season s0 --versioned-author
```

`pack` 的赛季来自准备清单；`inspect` 应明确选择同一赛季。`--versioned-author` 使用当前 Qt 客户端同样的资源快照校验；省略时保留历史 CLI 的兼容行为。制包会调用实际导入校验器，成功后独占创建输出 ZIP，并打印字节数与 SHA-256；已有输出不会被覆盖。只读解析和验证全部完成后才发布 ZIP。

玩家使用现有“本地 ZIP 导入”。协议、资源发现、作者 ID 选择、部分楼层回退及更新/恢复/移除沿用 1.1.2 已有机制；更新历史由资源快照登记，不用另外的回执格式管理。安装后不自动修改当前对局、结构身份或跨层变换。图包哈希只证明完整性，不认证作者或人工对齐质量。

本次源码验证使用真实导入/快照事务和图像消费者，基础地图是小型 typed fixture，并包含 S0、S1 两模式负楼层测试。构建、实际安装及真实作者/游戏验收仍须分别记录。
