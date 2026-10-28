# 可选视频与AVIF依赖排除

程序使用GDI/Pillow截图、OpenCV图片解码/几何运算及PNG/JPEG/WebP显示，不调用VideoCapture、VideoWriter或FFmpeg后端。AVIF也不是当前素材或导入格式。

`tools/build.py`使用显式spec构建开发骨架。Analysis排除`PIL.AvifImagePlugin`和`PIL._avif`，然后在COLLECT前用`tools/runtime_exclusions.py`精确过滤`opencv_videoio_ffmpeg*.dll`和`_avif.*.pyd`。不能只给FFmpeg使用`--exclude-module`，因为它是cv2 hook收集的二进制插件。

保留cv2.pyd、PIL核心、PNG/JPEG/WebP插件和共享C运行库。私有完整资源的正式构建配方也必须采用同一过滤函数，不能假称本开发骨架包含地图。独立helper无需OpenCV/Pillow，不应为此引入这些依赖。

软件统一交付Setup安装包，不再生成ZIP免安装软件包；地图导入ZIP仍保留。依赖文件原始磁盘节约与实际Setup节约分列，未构建安装器时包体收益标待测。旧交付物和用户安装目录不删除或改写。

## 轻量默认资源配置

`tools/package_profiles.py`记录`structure-editor-light`的资源清单规则：输入路径相对`_internal`，排除滋酥居、展十和可选作者图片目录，保留catalog、配准元数据、纯结构、相似组及132份预制。正式收集器在复制授权资源前调用`partition_resources(inventory)`，保持各行bytes/SHA不变，不扫描本机寻找地图。编辑器图库在EXE旁`assets/custom-maps`，helper及其独立运行库也由单独的锁定输入原样收集。

本次已交付1.0.1轻量Setup基于远端`6b341879`产品源码；本分支只公开构建规则，未改产品算法或重新构建该交付物。与当次实际清单对照：467条程序资源去掉162条作者图片后保留305条；编辑器另有24图72层、146个文件。选取规则等价于当次外部配方，新增可选作者目录规则用于后续相同轻量配置。资源锁、图片及本机绝对路径均不入库。

已知限制：当前产品的候选缩略图仍读取作者图片，无作者图时会显示占位，原断言失败证据保留；此规则不是缩略图回退修复。困难编辑器素材未包含。无默认HTTPS下载索引，保留离线导入能力。安装/GUI/游戏验收未运行；不能因为构建成功写成全部功能验收通过。
