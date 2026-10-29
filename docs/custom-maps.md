# 自定义地图

自定义地图页使用作者自己的地图底图（旧版 PC/安卓自定义地图的 24 张噩梦图），路线按旧版方式规划，进游戏后随地图结构自动贴合。

## 三类数据

| 用途 | 来源 | 坐标 |
| --- | --- | --- |
| 显示底图 | 旧版路线编辑器的 `blank/` 底图（负一楼用 `maps/` 图） | 底图像素；每层带一个到游戏原生像素的 3×3 矩阵 |
| 同层寻路 | 噩梦 24 图交接包的道路网 `topology-*.json`：格中心点、所属房间、房间内格边、配对门 | 游戏原生像素（与公开结构资源逐层哈希一致） |
| 跨层楼梯 | 旧版编辑器中已核实的楼梯，换算到原生像素后接到最近的道路点 | 游戏原生像素 |

游戏数据目前没有已核实的跨层连接，所以先用旧版楼梯；以后游戏楼梯核实后可替换。

### 道路补丁

游戏数据里有 13 层的同一种房间（凹字形房间紧贴一条窄道，同一个房间号、共 20 个点）缺了两部分之间的 `room-grid` 连线，凹字形那 15 个点走不到。用户 2026-10-28 在游戏里确认这些房间都能正常进出，按游戏数据 2.104.3059230 记在 [custom-road-patches.json](../tools/maps/custom-road-patches.json)，共 26 条。

- 构建时逐条按点号、坐标、房间号核对后追加，任何一项对不上或两点不相邻就构建失败；游戏数据本身已有的连线报告为“已由游戏数据覆盖”，不重复添加。
- 交接包里的原始拓扑不改动，只改资源包里输出的那份；manifest 的 `sources.roadPatches` 记录补丁文件 SHA-256、追加数和覆盖数。
- 以后游戏数据更新、补丁全部被覆盖时可删除这份文件。安卓端同一清单：`mobile/scripts/assets/nightmare-game-road-exceptions.json`。
- `tests/test_custom_map_reachability.py` 检查已安装资源包里每层的每个房间都能从一楼大门经楼梯走到。

旧版模板与地图的对应关系固定写在 [build_custom_map_package.py](../tools/maps/build_custom_map_package.py)，是按轮廓重合度在全部候选里选出的；模板 17、18、19 没有对应地图，不收录。

## 对齐

构建时每层先按重心和面积对齐，再用 ECC（仿射）以底图不透明区域对齐结构轮廓。72 层重合度 0.83–0.93，两轴缩放基本一致、剪切接近 0；重合度低于 0.8 时构建失败。194 个旧楼梯端点换算后到最近道路点不超过 42 像素（一格 60 像素）。

## 资源包

当前版本：[custom-maps-20261028-3](https://github.com/chenhuanh9-a11y/xiaochao-assistant/releases/tag/custom-maps-20261028-3)，`xingtu-custom-maps-20261028-3.zip`，8,392,815 字节，SHA-256 `9b10fd9d6b37bf987548fe4fa5d015b22110943d91408d2fa64e5bfad47ce416`。打包软件前下载并安装：

```
python tools/maps/install_custom_maps.py --archive xingtu-custom-maps-20261028-3.zip --sha256 9b10fd9d6b37bf987548fe4fa5d015b22110943d91408d2fa64e5bfad47ce416
```

重新构建：

```
python tools/maps/build_custom_map_package.py --legacy 旧版/nightmare-route-editor --game-data 噩梦24图交接包 --structures 结构资源 --out 新目录 --version 新版本号 --zip 发布目录 [--review 对比图目录]
python tools/maps/install_custom_maps.py --archive xingtu-custom-maps-VERSION.zip --sha256 压缩包SHA256
```

资源包含 `manifest.json`（对应关系、矩阵、入口、楼梯与逐文件 SHA-256）、`images/`、`topology/` 和入口图标，安装到 `assets/custom-maps`（不进 Git，可用 `XINGTU_CUSTOM_MAPS_ROOT` 指定）。软件读取每个文件前都会核对 SHA-256。

为节省空间，底图存为 WebP（颜色质量 90，透明通道无损；`--quality 0` 改为全无损），道路网只保留软件用到的点、房间和连线；门、房间轮廓等来源信息留在游戏数据交接包里。旧版 PNG 资源包仍可读取。

## 路线与图库

- 点房间内部加目标（落在房间中心），点走廊加必经点（插入到最近的一段）；同一层按道路网连线，跨层自动走楼梯。“新起点”开始一条不与前一段相连的路线。
- 路线只保存为数据：楼层、原生坐标、房间/必经点、颜色。不保存图片。
- 默认图库在 [custom_default_gallery.json](../app/custom_default_gallery.json)，只读；在编辑器里画好后“导出”，替换这个文件即可更新内置路线。玩家图库保存在设置目录的 `custom-galleries.json`，可新建、复制、改名、删除、导入、导出。
- 设置里的“作者路线”会列出每个图库（“自定义 · 图库名”），自定义地图页的“在游戏中使用”也会切换到它。选中后，资源包里有的地图在游戏内显示该图库的底图和路线；没画路线的图只显示底图。

## 画默认路线

不用启动游戏，macOS 和 Windows 都能用：

```
python tools/draw_custom_routes.py
```

- 需要 Python 3.13（带 Tk）、Pillow、NumPy、OpenCV（版本见 `requirements.txt`），资源包已经安装到 `assets/custom-maps`。
- 图库保存在 `work/custom-map-editor/`（不进 Git）。
- 复制默认图库或新建一个图库，画好 24 张后点「写入默认图库」。工具会把当前图库写入 `app/custom_default_gallery.json`，名称和 ID 固定为「默认路线」/`default`。提交这个文件后，新打的安装包就会内置这套路线。

## 限制

- 两段路线经过同一段走廊时会重叠，没有像旧版那样并排分道显示。
- 道路网的门是静态配对，实时开关状态未知；房间内部碰撞未完全建模。
- 尚未在 Windows 实机和游戏内验证对齐效果。
# 构建交接与兼容修复

固定资源 Release：`custom-maps-20261028-3`，资源 ZIP `xingtu-custom-maps-20261028-3.zip`，8,392,815 字节，SHA256 `9b10fd9d6b37bf987548fe4fa5d015b22110943d91408d2fa64e5bfad47ce416`。Release 是编辑器资源包，不是软件安装包。

```powershell
python tools/maps/install_custom_maps.py --archive <下载的ZIP> --sha256 9b10fd9d6b37bf987548fe4fa5d015b22110943d91408d2fa64e5bfad47ce416 --root <干净构建输出根目录>
```

资源安装在应用根目录 `assets/custom-maps`，包含 manifest、图片和道路拓扑；`app/custom_default_gallery.json` 由构建脚本随程序收集。不要把验证目录、玩家图库或设置打进安装包。安装工具会替换目标资源目录，仅用于已确认的构建 stage；不应拿它覆盖未知用户自制资源。

builder 验证道路拓扑声明的 shape 和 mask 哈希，并保存实际 mask 哈希；runtime 在缓存复用/显示前对当前结构执行同样检查。哈希算法是 `SHA256(ascontiguousarray(mask > 0, uint8).tobytes())`。公开包已含此字段，缺失字段或同尺寸不同结构均拒绝显示。

旧设置目录中的 `custom-routes.json` 会按楼层迁移为独立可编辑图库，以保留全部原生坐标和颜色，不捏造跨楼层连接。原文件不移动、不改写，可恢复；迁移摘要写入新 `custom-galleries.json` 保证幂等，不自动切换玩家的游戏内选项。迁移失败保留原文件，原因可由 `GalleryStore.migration_error` 读取。自定义图库偏好不再因作者 catalog 缺失被启动重置，同名图库通过 ID 区分。

存在楼梯链接时采用零启发式保证跨层路径的最短路条件。此次离线检查覆盖模型、迁移、无窗口编辑器和主程序集成、builder；公开包145文件校验，并对 nightmare-03 的 B1/1F 做实际结构哈希及同尺寸篡改拒绝检查。未执行 Windows GUI、游戏或实际输入，不据此宣称游戏内观感验收。


## 增量维护的补丁检查

道路补丁的每个地图和楼层都必须属于本次构建清单；未知地图、遗漏楼层在写入输出前报错。构建子集时应显式提供只针对该子集的补丁文件，不得静默过滤默认补丁。已安装资源的可达性检查仅在默认可选资源目录完全不存在时跳过；显式指定目录缺失、坏 manifest 或空包均失败，不能写成已验证。

补丁仍记录最初确认版本 2.104.3059230。应用到新游戏版本前须核对新来源、完整地图/楼层身份及结构哈希，并逐条验证节点编号、坐标、房间和相邻格；同版本号或相同画布尺寸不足以证明适用。新数据已包含该边时只报告 covered，不重复添加。出现绑定变化应停止相关构建并重新核实，不能靠改版本字段放行。这里的 26 条边仅为编辑器层内道路，不修改识别底图、跨层楼梯或运行时贴合。


## 只扩展新增原生地图

`tools/maps/extend_custom_map_package.py` 接受已验证的现有 editor 包、显式新楼层 plan、`export-catalog.cjs` 的 geometry 输出和同源 runtime 结构根。先验证现有逐文件哈希、source layout/worldFloor/version/structureVersionKey、renderer 坐标恒等映射与 alpha 精确等于 shared_mask，才复制旧文件、对新增正常渲染图编码 WebP 并生成同层道路。旧图不重做 ECC，不重编码。plan 含 runtimeMapId/sourceLayout/floor/sourceWorldFloor/nativeSourceMetadata/nativeSourcePNG；它是本地来源清单，不提交原始数据。

`tools/maps/complete_incremental_editor_connections.py` 对旧/新规范化 literal 和显式 source-connectors 清单再验证：旧 10609 定义相同、原例外逆房间变换得到一致 local 边，才应用到新同房型实例；不根据孤立组件猜连边。楼梯必须为源 room_type=4，所有 port 槽位/旋转/坐标逐项匹配原定义；连接表示同一源楼梯房间的跨层关联，内部导航折线和游戏通过性仍未实测。正门锚点是源 room_type=2 的房间 port，不冒充出生图标中心。仅楼层占用或不明类型不会生成跨层边。

两个命令都要求新输出目录，原始资料、旧包、用户路线保留。先执行增量，再补关联，最后用真实 `CustomMapLibrary` 检查全部图像/拓扑加载、旧文件字节复用及全图可达性；无法证明的项必须显式报告，不跳过坏 manifest 或任意补边。

```powershell
python tools/maps/extend_custom_map_package.py --baseline BASE --plan NEW_FLOORS.json --geometry GEOMETRY --resources RUNTIME --out NEW_STAGE --version PACKAGE_VERSION
python tools/maps/complete_incremental_editor_connections.py --source NEW_STAGE --out FINAL_STAGE --resources RUNTIME --plan NEW_FLOORS.json --geometry GEOMETRY --old OLD_NORMALIZED.json --new NEW_NORMALIZED.json --patches tools/maps/custom-road-patches.json --connectors SOURCE_CONNECTORS.json
python tools/maps/test_extend_custom_map_package.py
python tools/maps/test_incremental_editor_connections.py
```
