# 本地游戏地图配置提取

这套工具可由自己的客户端提取真实布局及房型，再导出房间实例、占用体素和静态门配对。不是 PR8 的单图硬编码示例；地图 ID 从输入配置枚举，也支持 `--maps` 选择。

需要 **Node.js 24+**（使用内置 zstd），无 npm/Python 依赖。只读本地资源，不执行恢复出来的游戏 Python，不启动游戏、不发网络请求。仅支持已核实的 SKPW/1DPW、包装/编码及自定义 marshal/字节码格式；其他格式报错，不补造配置。输入是安装或合法解压后的客户端目录；不直接读取安装 EXE/APK/ZIP。

## 最简用法

从仓库根目录运行，替换你自己的路径；每次输出必须是一个**不存在的新目录**，且不能位于客户端目录内。

```powershell
node tools/maps/extract/extract-map-configs.cjs --game "C:/Games/Client" --out "C:/MapExports/run-01"
node tools/maps/extract/export-catalog.cjs --literals "C:/MapExports/run-01/literal-exports.json" --out "C:/MapExports/catalog-01" --game-version "你的客户端版本"
```

第一步读取 `res/script.idx` 与对应 `script*.wpk`，也读取存在的 `Documents/res` 更新层。每个模块优先选择更新层，否则选择基础层；这是一项明确的离线选择规则，不证明运行时挂载优先级。无需指定一个固定地图或困难/噩梦数量。

第二步也可只选代表地图：

```powershell
node tools/maps/extract/export-catalog.cjs --literals "C:/MapExports/run-01/literal-exports.json" --maps 104001,103001 --out "C:/MapExports/subset-01" --game-version "你的客户端版本"
```

已持有本项目旧 `map_data_complete.json` 的用户可以跳过包提取：

```powershell
node tools/maps/extract/export-catalog.cjs --catalog "C:/MapExports/map_data_complete.json" --maps 104001 --out "C:/MapExports/geometry-01"
```

## 输出及数据契约

第一步输出 `literal-exports.json`、`summary.json`、`modules.json`、`discovery.json` 与匹配模块 `.bin`。Python 字典保留为 `{kind:"dict",entries:[[key,value],...]}`，因此整数/元组键不会静默丢失。`dungeonParams` 遇函数体停止，明确 `partial`；不执行函数。支持 `--compare 上次/literal-exports.json` 做布局记录比较。

第二步输出：

| 文件/字段 | 含义 |
| --- | --- |
| `catalog.json` | `maps[id].rooms`、`roomDefinitions[typeId]`、`levelGroups`、`levelRules`、`mapImageConfigs`、模块哈希。保留不同位置的实例索引；不能以同房型合并收益。 |
| `ID.geometry.json` → `rooms` | `roomIndex,roomConfigId,position,rotation,worldVoxels,occupiedWorldFloors`。`visitableFloors:null` 和 `verticalAccess:"unknown"` 明示尚未确认可走楼层。 |
| `pairedDoors` | 仅把两房间 `room_paths` 变换后恰好反向的体素对配对；保留双方实例及槽位、源楼层、门中心。`liveDoorState:"unknown",traversable:null`。歧义/孤立槽保留 `unpairedDoorPaths`，不强行配对。 |
| `externalDoorReferences` | 布局 `door` 原始索引语义的实例/槽位引用；不是已确认的正门/侧门图标。缺少场景锚点或门规则时不生成图标。 |
| `transfers:[]` | 当前没有经验证的楼梯/跳落关联导出；名称或占高不能成为跨层边。 |
| `manifest.json` | 输入文件 SHA256、版本（未提供则 null）、每图统计、各输出 SHA256、能力边界。不记录本机绝对输入路径。 |

源整数坐标是 `[x,y,z]`，`y` 是源高度，二维地图平面为 `[x,-z]`；格中心为 `[x+.5,-z-.5]`。旋转是 0–3 个四分之一转，体素以格边界旋转（例如旋转 1：`[x,z]→[z,-x-1]`），再平移。**不自动把源高度映射为 B1/1F/2F**；占用上层可能只是挑高，不能据此添加必访上层。真实可走结构和用户确认应作为独立证据。

`pixelRegistration:null`：这些数据是世界格坐标，不能凭 bounds 猜测贴到编辑器像素上。当前编辑器/识别资源要另用 producer 的显式矩阵、楼层绑定、源版本及资源哈希配准；显示用正常源渲染图、规划用几何、识别用二值 mask，三层独立绑定。此工具不会改现有路线文件或 UI。

## 明确尚未交付的环节

- 贴图/场景资源字节提取、正常地图渲染：此目录不含贴图或 renderer。仓库的 `tools/maps/render-candidate-structures.cjs` 仍需外部合法 `source_renderer.js` 和匹配资产，不能仅凭本次输出声称完成。
- 全内墙/障碍碰撞、精确通道中心线和可走区域：`room_voxels` 是占用，不等于格子全可走。当前只导出源几何与门，不生成虚假的无墙寻路网格。
- 可走上层、楼梯传送/跳落、实时随机门状态、正门出口场景锚点：仍需独立源字段适配与验证。场景配置虽静态恢复，尚无通用已验证锚点导出器。
- 未覆盖的新客户端封装/字节码版本，不保证兼容；保留失败输出供本地排查，使用新目录重试。

## 离线合成验证

```powershell
node --test tools/maps/extract/test-extraction.cjs
node tools/maps/extract/extract-map-configs.cjs --help
node tools/maps/extract/export-catalog.cjs --help
```

测试临时构造完全虚构的 IDX/WPK、marshal 模块及两个房间，走完整提取链，核对更新层优先、门配对、挑高不生成可走楼层、坏格式及覆盖保护；不含游戏数据或作者图。本次没有重扫真实客户端，也没有进行 GUI/游戏验证。来源与许可证说明见 [NOTICE.md](NOTICE.md)。
