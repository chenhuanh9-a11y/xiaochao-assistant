# 第三方内容

本文件描述仓库中已识别的嵌入第三方图标，不构成项目自有代码的整体许可。

| 内容 | 位置 | 来源与许可 |
| --- | --- | --- |
| Lucide 图标数据 | `app/desktop_lucide.py` | Lucide，源修订 `1802f33f6c270395fcd981067f22719140e2ffbc`；ISC，部分源自 Feather 的图标为 MIT。完整声明见 `THIRD_PARTY_LUCIDE_LICENSE.txt`。 |
| Microsoft Fluent System Icons 数据 | `app/desktop_icons.py` | Microsoft，源修订 `82ecf7cbdb43684403c7125efa160be04a33dca5`；MIT。声明已嵌入模块，并提取至 `THIRD_PARTY_FLUENT_LICENSE.txt`。 |

源码保留图标来源 URL、修订号及内嵌许可。原开发目录中的 SVG 工作副本未随仓库上传；本仓库保留的是模块内现有的图标数据与许可。

外部 Python 依赖由 `requirements.txt` 安装，其权利及许可由相应上游分发包规定。创建二进制分发前还需保留实际打包依赖的许可。地图、作者路线图、游戏数据与品牌图片未随本仓库分发。
