# 地图处理工具

从仓库根目录运行，先安装 `requirements.txt`。下文 `python` 表示虚拟环境解释器。只写明确的新staging，不将输出放回活动资源。图像、NPZ、原包体、含用户路径的清单与压缩包均不纳入源码Git。

## 维护清单与差异

仅依赖Python标准库，使用中间schema2，契约见 [manifest-contract.md](../skills/update-helper-maps/references/manifest-contract.md)。

```powershell
python skills/update-helper-maps/scripts/map_update.py check --manifest NEW.json --scope inputs
python skills/update-helper-maps/scripts/map_update.py plan --old OLD.json --new NEW.json --out PLAN.json
python skills/update-helper-maps/scripts/map_update.py stage --old OLD.json --manifest NEW.json --out STAGING
python skills/update-helper-maps/scripts/map_update.py check --manifest NEW.json --scope release
python skills/update-helper-maps/scripts/test_map_update.py
```

inputs检查声明与哈希；plan完成分类返回0，仍须阅读blocked。stage只收集已有四类产物，不执行提取/识别/发布；当前stage和release返回3，输入错误返回2。初次导入 `base_version:null` 时省略old，增量必须提供正确旧基线。

## 候选渲染与typed结构配方

渲染包装需要Node.js及外部有权使用的renderer目录，含 `tools/source_renderer.js` 和素材。本仓库不提供它们。该历史封装显式使用旧纹理，不能称新游戏纹理重新提取。

```powershell
node tools/maps/render-candidate-structures.cjs --help
node tools/maps/render-candidate-structures.cjs --run RUN --old-vendor RENDERER_ROOT --output RENDER_STAGE --levels 104001
python tools/maps/native24/plan_24_native_layers.py --workspace NATIVE_STAGE --suju-root SUJU_INPUT --game-root GAME_INPUT --baseline-references BASELINE_REFERENCES
python tools/maps/native24/build_native_24.py --workspace NATIVE_STAGE --baseline-bundle BASELINE_BUNDLE --target-opencv 4.12.0
```

plan读取外部 `integration-plan.json`、`coverage-snapshot.json`、渲染PNG及metadata，输出 `target-24-native-plan.json` 与coverage CSV。build读取plan和旧bundle，输出 `NATIVE_STAGE/batch-native-24-72/runtime-root/` 内的typed NPZ、轮廓、索引及显示资源；已有输出拒绝覆盖。保留24图/72层和legacy lookup key等历史约束，不是任意版本通用API。alpha footprint不等于语义墙/可走区域，未导出通道保持缺失。

### 二楼侧门结构索引与完整包接入

`LITERAL_EXPORTS.json` 必须包含与本批结构版本匹配的 `levelLayouts.exports.data` 和 `roomDefinitions.exports.data`，来自有权使用的游戏包体提取。完成原生结构与版本声明后，使用下面的统一入口生成以下两件；标准输出给出应随完整包收集的相对路径。门锚点按逐图二楼边界换算，绝不复用一楼尺度，也不填造 passport。

- `references/nightmare-entries/side-door-anchors.json`
- `references/nightmare-entries/side-entry-search.npz`

为已有完整资源补齐这两件时，先将完整基线复制到独立的新 `STAGED_RESOURCE_ROOT`，再运行下列命令；已有目标文件拒绝覆盖：

```powershell
python tools/maps/native24/build_side_entry_search.py --resources STAGED_RESOURCE_ROOT --exports LITERAL_EXPORTS.json
```

生成需要同一完整基线中的地图表、shared index/NPZ、`side-entries.json`；源版本或轮廓不匹配会报错。索引写到上述正式位置后会经运行时 `load_index` 回读。此流程不重建作者图、不重新配准、不改变其他资源。实际游戏数据与 NPZ 不进入源码 Git。

完整产品打包必须从这个新资源根收集两件文件到 `_internal/references/nightmare-entries/`，保留原完整基线；不能只携带源码，不能把两件增量当作整套资源根。它们属于程序识别资源，轻量包移除作者图片时仍须保留。打包后用实际产物的 `MapLocalEntryIdentity` 确认 `side_door_error`、`side_search_error` 均为空，`side_doors`、`side_search` 各覆盖 24 张 F2；没有覆盖就不能声称该包已启用侧门索引。旧资源缺失时运行时仍安全回到普通检索，这种兼容行为不代表打包完成。`tools/build.py` 仍只构建无私有资源的开发骨架，不代替完整产品打包。

索引只给门附近结构搜索排序，并用保守距离下界排除不可能种子。门外潜在竞争者仍展开，精修和原验证使用完整参考；没有二楼相似组，也未为困难模式新增侧门数据。

已有单图旧画布配方：

```powershell
python tools/maps/generate_author_game_derived.py --help
python tools/maps/generate_author_game_derived.py --root STAGED_RESOURCE_ROOT --run GAME_RUN --crosswalk CROSSWALK.json --output outputs/game-derived-structure --index references/nightmare-author-structure-index.json
```

它从既有渲染和crosswalk生成104001三个楼层、两个作者profile的六份结构产物，会更新指定外部索引。仅在独立staging使用，不是全图自动提取入口。

## 作者配准与canonical显示

路径由 `tools/maps/authors/map_paths.py` 从配置读取：

```powershell
$env:XIAOCHAO_WANG_INPUT = 'AUTHORIZED_WANG_INPUT'
$env:XIAOCHAO_NIGHTINGALE_INPUT = 'AUTHORIZED_NIGHTINGALE_INPUT'
$env:XIAOCHAO_TILEMAP_ROOT = 'TILEMAP_PROJECT_INPUT'
$env:XIAOCHAO_AUTHOR_WORK = 'AUTHOR_STAGE'
$env:XIAOCHAO_CANONICAL_WORK = 'CANONICAL_STAGE'
# 可选：XIAOCHAO_CJK_FONT，指向本机有权使用的字体。
python tools/maps/authors/author_audit.py
python tools/maps/authors/align_authors.py wang-qingxin
```

inventory输出来源清单、哈希和联系表，配准输出SIFT/affine候选与留出信息，不能直接晋级生产。align仍依赖历史 `target-24-native-plan.json`、两个结构版本目录和人工review中间产物，不是任意图片自动建库入口。

已有R3数据、base依赖和矩阵/证据齐全后：

```powershell
python tools/maps/authors/canonical_display_r4.py plan
python tools/maps/authors/canonical_display_r4.py build
python tools/maps/authors/verify_canonical_r4.py validate
python tools/maps/authors/verify_canonical_r4.py package
```

输入为 `AUTHOR_STAGE/packages-r3/<author>/manifest.json`、base-dependencies和引用文件；输出为 `CANONICAL_STAGE/selection-plan.json`、`packages/<author>/`、验证记录及ZIP。保留历史81层=72当前+9旧结构、24layout/27版本组等断言，新批次应适配而非删除断言。

每作者/结构版本/楼层只选一份单层或合图，保留原RGB采样，用准确base alpha裁切，`M_asset_to_base = M_source_to_base @ T_crop_to_source`只应用一次。复用已有矩阵不替代R3精度复核；作者显示可用与runtime接入分列，生成ZIP不代表允许公开素材。

## 噩梦快结构预制

```powershell
python tools/maps/prepare_fast_structures.py RESOURCE_ROOT --output-root FAST_STAGE
```

输入为已存在的 `references/nightmare-shared-structure-index.json` 与typed NPZ。生成器来自原454认可逻辑对应 `950930a6...`，保持prepare数值逻辑，增加独立输出、路径检查、冲突拒绝和真实loader回读。输出 `FAST_STAGE/references/fast-structure/`，包含uint16距离、chains/rays/pitch/shape和sourceMetadata；不运行识别/求解/GUI，不回写输入。

以上是保留的旧噩梦共享index入口。当前应用已包含sharedfast06；与preview09-19冻结清单一致的新入口为：

```powershell
python tools/maps/prepare_shared_fast_structures.py --help
python tools/maps/prepare_shared_fast_structures.py RESOURCE_ROOT --output-root SHARED_FAST_STAGE --mode nightmare --mode difficult
python tools/maps/prepare_shared_fast_structures.py RESOURCE_ROOT --output-root SELECTED_STAGE --mode difficult --select MAP_ID/1
```

新生成器通过当前应用的真实共享reference provider枚举，支持重复mode和MAP/FLOOR选择；输出NPZ和 `generation-result.json`，用同版loader回读。它只构造资源上下文，不创建Tk、截图、rank或求解；需要完整合法资源和外部输出根。示例中的MAP_ID必须替换为资源索引实际ID，不能猜数字。132层结构和现有132预制的独立交换包见 [结构资源取用](resource-release.md)，未放进Git历史，也没有在本次发布重跑识别或预制算法。

两个生成器的预制均须绑定同一metadata、shape与工具版本，不混不同OpenCV或结构版本。

## runtime契约与缺口

`app/star_map_import.py` 的map-package规则和 `app/star_map_download.py` 的HTTPS静态索引规则是生产schema，不等于技能的中间schema。应用没有默认下载URL，本次未公开真实服务器索引或包体。

通用新包体→中间manifest、完整语义提取、生产增量安装/回退仍需逐批适配。help和合成fixture不证明真实地图精度或GUI效果。原文件名、SHA和参数化范围见 [工具来源记录](map-tool-provenance.json)。
