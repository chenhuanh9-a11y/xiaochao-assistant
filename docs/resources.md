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

当前 Qt 源码客户端只使用显式 `--sandbox` 或默认 `work/qt-lab-installation`，资源在该沙盒的 `_internal/` 下，要求 `.qt-lab-owner` 所有权标记。使用 `experiments/qt_quick/seed_sandbox.py` 从授权来源建立新的隔离副本；目标存在时不覆盖。正式包使用打包时绑定的资源，不接受环境变量切换到外部开发资源。

旧 Tk 入口的 `IDENTITYV_*_RESOURCE_ROOT` 启动方式已退役；不得把历史环境变量配置视为 Qt 资源选择合同。

## 示例与校验

`examples/resource-root/references/` 的三个 JSON 完全为本次编写的空索引，只展示数据容器形状，没有真实地图、路线、截图、坐标或用户数据。**请勿复制后宣称识别可用。** `NightmarePack.validate()` 对空地图集会返回失败；正常界面还需要更多有效生产索引。

获得有权使用的完整同版本资源后，可以检查：

```powershell
& .\.venv\Scripts\python.exe tools\qt_client_entry.py --self-check --verify-resources
```

这些命令检查资源结构，不代表真实游戏或视觉验收。此源码发布只做源码语法、CLI、构建脚本和公开内容检查，没有重新运行全套地图识别或 GUI 验收。

## 品牌与打包

Qt 图标和教程位于 `experiments/qt_quick/icons`、`experiments/qt_quick/tutorial`；赞赏图片仍保留在 `assets/support`。清理旧 Tk 代码不删除这些当前资源或第三方许可。

`tools/build_qt_client.py` 是当前 Setup 构建入口。构建需要干净源码 commit、合法资源、资源哈希清单和对应验收；克隆公开仓库不能单独复现完整产品资源。旧 Tk `tools/build.py` 已退役。
