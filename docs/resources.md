# 源码与资源分离

地图、作者路线、游戏解包数据、结构NPZ、截图和Logo不直接纳入源码Git。当前仅干净结构与配套预制通过独立素材Release提供，取用方法见 [结构资源说明](resource-release.md)；作者路线图、四套地图包、纹理与Logo不在当前发布范围。不要将本机产品包的 `_internal` 或整个素材目录直接提交到Git。

## 目录约定

`resource-root` 指向生产资源根目录，**不是**包含 `_internal` 的上一层目录：

```text
resource-root/
  references/                  # 同一版本的生产索引与坐标规则
    nightmare-map-pack.json
    nightmare-author-route-catalog.json
    nightmare-canonical-coordinate-pack.json
    nightmare-shared-structure-index.json
    nightmare-structure-version-bindings.json
    ...                        # 其他由所选资源模式要求的元数据
  assets/                      # 有权使用的地图、作者路线、结构等
  outputs/                     # 旧资源模式可能引用的预处理产物，不是运行日志
```

上面的文件名用于说明加载接口，不是充分资源清单。实际地图、楼层、坐标变换、结构索引及其哈希必须来自同一套有效资源；不能随意混合旧版索引和新版图片。

## 配置入口

| 环境变量 | 用途 |
| --- | --- |
| `IDENTITYV_NIGHTMARE_RESOURCE_ROOT` | 运行时噩梦资源根目录；相关资源校验 CLI 也支持此入口。 |
| `IDENTITYV_SHARED_STRUCTURE_INDEX` | 共享结构索引的文件路径。 |
| `IDENTITYV_SHARED_STRUCTURE_ROOT` | 共享结构产品的根目录。 |
| `IDENTITYV_NIGHTMARE_MAPS` | 旧地图包模式的地图图片根目录。 |
| `IDENTITYV_AUTHOR_ROUTE_ASSET_ROOT` | 作者路线目录；不代替完整资源索引。 |
| `IDENTITYV_AUTHOR_STRUCTURE_INDEX` / `IDENTITYV_AUTHOR_STRUCTURE_ROOT` | 旧作者结构索引与产物根目录。 |
| `IDENTITYV_DIFFICULT_MAPS` | 已有困难模式的图片路径覆盖，不会补齐其他缺失资源。 |
| `IDENTITYV_LEGACY_ASSETS_ROOT` | 本次整理新增，供三个旧原型模块取代个人绝对路径；默认 `assets/legacy`。 |
| `IDENTITYV_NIGHTMARE_LIVE_ROOT` | 诊断输出目录，可选择私有本地目录。 |

可参考 `examples/environment.ps1` 设置路径，再启动主程序。环境变量不是资源授权，也不保证任意资源包都兼容。

## 示例与校验

`examples/resource-root/references/` 的三个 JSON 完全为本次编写的空索引，只展示数据容器形状，没有真实地图、路线、截图、坐标或用户数据。**请勿复制后宣称识别可用。** `NightmarePack.validate()` 对空地图集会返回失败；正常界面还需要更多有效生产索引。

获得有权使用的完整同版本资源后，可以检查：

```powershell
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --validate-pack
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --validate-author-routes
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --validate-author-structures
& .\.venv\Scripts\python.exe app\identityv_daily_assistant.py --validate-coordinate-pack
```

这些命令检查资源结构，不代表真实游戏或视觉验收。此源码发布只做源码语法、CLI、构建脚本和公开内容检查，没有重新运行全套地图识别或 GUI 验收。

## 品牌与打包

`star_brand.py` 会尝试加载 `assets/brand/xingtu-round.png` 与 `xingtu-square.png`；缺失时返回空图像。品牌原图版权未确认，暂不随源码公开。帮助配图同样未上传。

`tools/build.py` 是新的源码骨架开发构建入口，独立于原产品完整候选构建流程。它不打包地图和 Logo，不自动复制任何资源根目录。完整产品重建还需要合法资源、资源版本绑定和对应验收流程；克隆公开仓库不能复现原候选的安装包或识别效果。

为减小体积，`tools/build.py` 不打包软件用不到的两部分，共约 36 MB：

- Pillow 的 AVIF 编解码（`PIL._avif`、`PIL.AvifImagePlugin`，约 7.9 MB）。Pillow 本身把它当可选模块，缺失时 PNG、WebP、JPEG 照常读写。
- OpenCV 的 FFmpeg 视频插件（`opencv_videoio_ffmpeg*.dll`，约 28.3 MB）。它只在 `VideoCapture` / `VideoWriter` 读写视频时加载，软件不用；构建完成后从 `dist` 删除。

正式安装包如果用别的打包流程，也可以照做：加上这两个 `--exclude-module`，打包后删除这个 DLL。改动后需在 Windows 上实际启动并完成一次识别确认；尚未在 Windows 构建中验证。
