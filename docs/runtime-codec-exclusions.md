# 可选视频与AVIF依赖排除

程序使用GDI/Pillow截图、OpenCV图片解码/几何运算及PNG/JPEG/WebP显示，不调用VideoCapture、VideoWriter或FFmpeg后端。AVIF也不是当前素材或导入格式。

`tools/build.py`使用显式spec构建开发骨架。Analysis排除`PIL.AvifImagePlugin`和`PIL._avif`，然后在COLLECT前用`tools/runtime_exclusions.py`精确过滤`opencv_videoio_ffmpeg*.dll`和`_avif.*.pyd`。不能只给FFmpeg使用`--exclude-module`，因为它是cv2 hook收集的二进制插件。

保留cv2.pyd、PIL核心、PNG/JPEG/WebP插件和共享C运行库。私有完整资源的正式构建配方也必须采用同一过滤函数，不能假称本开发骨架包含地图。独立helper无需OpenCV/Pillow，不应为此引入这些依赖。

软件统一交付Setup安装包，不再生成ZIP免安装软件包；地图导入ZIP仍保留。依赖文件原始磁盘节约与实际Setup节约分列，未构建安装器时包体收益标待测。旧交付物和用户安装目录不删除或改写。
