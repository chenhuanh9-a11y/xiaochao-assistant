# 仓库入口与维护缺口

公开整理日期：2026-10-27。路径相对仓库根目录；不保存本机工作树、内部协调记录或真实素材位置。

| 阶段 | 已存在的代码 | 范围和缺口 |
| --- | --- | --- |
| 中间清单、差异、候选收集 | 本技能 `scripts/map_update.py` / `test_map_update.py` | 标准库 schema 2；stage/release 因生产适配器缺失返回3。 |
| 原生结构批次计划 | `tools/maps/native24/plan_24_native_layers.py` | 历史24图/72层配方，不自动适配任意新版本。 |
| typed NPZ、轮廓、候选runtime元数据 | `tools/maps/native24/build_native_24.py` | 固定批次断言，OpenCV4.12.0；alpha footprint不等于语义墙/可走区域。 |
| 渲染封装 | `tools/maps/render-candidate-structures.cjs` | 用户自有包装脚本，依赖外部 `tools/source_renderer.js` 与合法素材；外部renderer未分发。 |
| 单图game-derived旧画布产物 | `tools/maps/generate_author_game_derived.py` | 固定104001/六产物配方，会更新所选外部索引，须在独立staging使用。 |
| 作者inventory / 候选配准 | `tools/maps/authors/author_audit.py`、`align_authors.py` | 读取配置目录，候选矩阵不是自动验收。 |
| canonical选源/裁切/打包 | `tools/maps/authors/canonical_display_r4.py`、`verify_canonical_r4.py` | 历史R3→R4，复用已有矩阵，固定27组/81层等断言，不自动产生R3人工证据。 |
| 离线快结构预制 | `tools/maps/prepare_fast_structures.py` | 原454生成器，增加独立输出及真实loader回读；只处理已有噩梦共享index，不是困难sharedfast06生成器。 |
| 生产loader / package schema | `app/shared_structure_reference.py`、`difficult_native_context.py`、`star_map_import.py`、`star_map_download.py` | candidate17应用快照；中间清单不能直接交给生产loader。 |

完整命令与前置条件见仓库 `docs/map-processing.md`。无完整合法资源时，只运行help、语法检查和合成fixture。不要将历史脚本指向活动源目录，不为查参数执行未经审查的顶层脚本。

中间清单收集、作者显示验收和生产runtime接入是不同状态。源包体→规范化schema、完整语义提取、代表难图核心验收、生产增量安装及回退仍需逐批适配；不存在promote命令。源码中保留的历史批次数字不是以后目标数量。
