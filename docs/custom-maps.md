# 自定义地图

自定义地图页使用作者自己的地图底图（旧版 PC/安卓自定义地图的 24 张噩梦图），路线按旧版方式规划，进游戏后随地图结构自动贴合。

## 三类数据

| 用途 | 来源 | 坐标 |
| --- | --- | --- |
| 显示底图 | 旧版路线编辑器的 `blank/` 底图（负一楼用 `maps/` 图） | 底图像素；每层带一个到游戏原生像素的 3×3 矩阵 |
| 同层寻路 | 噩梦 24 图交接包的道路网 `topology-*.json`：格中心点、所属房间、房间内格边、配对门 | 游戏原生像素（与公开结构资源逐层哈希一致） |
| 跨层楼梯 | 旧版编辑器中已核实的楼梯，换算到原生像素后接到最近的道路点 | 游戏原生像素 |

游戏数据目前没有已核实的跨层连接，所以先用旧版楼梯；以后游戏楼梯核实后可替换。

旧版模板与地图的对应关系固定写在 [build_custom_map_package.py](../tools/maps/build_custom_map_package.py)，是按轮廓重合度在全部候选里选出的；模板 17、18、19 没有对应地图，不收录。

## 对齐

构建时每层先按重心和面积对齐，再用 ECC（仿射）以底图不透明区域对齐结构轮廓。72 层重合度 0.83–0.93，两轴缩放基本一致、剪切接近 0；重合度低于 0.8 时构建失败。194 个旧楼梯端点换算后到最近道路点不超过 42 像素（一格 60 像素）。

## 资源包

当前版本：[custom-maps-20261028-1](https://github.com/chenhuanh9-a11y/xiaochao-assistant/releases/tag/custom-maps-20261028-1)，`xingtu-custom-maps-20261028-1.zip`，56,790,370 字节，SHA-256 `d5dd4be7de77dd6ba248527d83cf05a51faf54e30127bd88abb31c7d3e47c466`。打包软件前下载并安装：

```
python tools/maps/install_custom_maps.py --archive xingtu-custom-maps-20261028-1.zip --sha256 d5dd4be7de77dd6ba248527d83cf05a51faf54e30127bd88abb31c7d3e47c466
```

重新构建：

```
python tools/maps/build_custom_map_package.py --legacy 旧版/nightmare-route-editor --game-data 噩梦24图交接包 --structures 结构资源 --out 新目录 --version 20261028-1 --zip 发布目录 [--review 对比图目录]
python tools/maps/install_custom_maps.py --archive xingtu-custom-maps-VERSION.zip --sha256 压缩包SHA256
```

资源包含 `manifest.json`（对应关系、矩阵、入口、楼梯与逐文件 SHA-256）、`images/`、`topology/` 和入口图标，安装到 `assets/custom-maps`（不进 Git，可用 `XINGTU_CUSTOM_MAPS_ROOT` 指定）。软件读取每个文件前都会核对 SHA-256。

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
