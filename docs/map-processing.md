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

它不是困难sharedfast06统一provider生成器，本分支未合那套应用变更。预制必须绑定同一metadata、shape与工具版本，不混不同OpenCV或结构版本。

## runtime契约与缺口

`app/star_map_import.py` 的map-package规则和 `app/star_map_download.py` 的HTTPS静态索引规则是生产schema，不等于技能的中间schema。应用没有默认下载URL，本次未公开真实服务器索引或包体。

通用新包体→中间manifest、完整语义提取、生产增量安装/回退仍需逐批适配。help和合成fixture不证明真实地图精度或GUI效果。原文件名、SHA和参数化范围见 [工具来源记录](map-tool-provenance.json)。
