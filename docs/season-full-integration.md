# 赛季、连点、provider 与解包桥独立集成

## 精确范围

独立分支 `integrate/season-full`，工作树 `season-full-integration`。以已复审的赛季/空卡/#94/连点候选 `a7d2010be9bba172e2783dbb94af91d1e6c9c836` 为基线，逐项纳入：

| 来源 | 本次提交 |
| --- | --- |
| provider fail-closed 接缝 `8cafcf4` | `b25ffc7` |
| provider 验证计数文档 `a9f78ff` | `e071e63` |
| 解包桥及维护 skill `71c100c` | `d31ec34` |

源码/测试 HEAD `d31ec34901a1eb15427c36b13d11c86145aa12ff`，tree `d780968e348a2c1b79cad118559ae7ca0698df4e`。逐项 cherry-pick 无文本冲突；逐文件 blob 比较和独立复审确认 provider、解包桥、连点、空卡及 #94 与各自已复审来源一致，没有整文件覆盖其他修改。后续仅本记录文档。

原冻结树 `3002f82`、provider 树 `a9f78ff`、连点树 `a7d2010`、解包树 `71c100c` 均保持原 HEAD 和干净工作区。没有上传、推送、合并、安装或发布。

## 语义边界

S1 仍未登记不可用。provider 在查缓存和实际构建前验证精确 season:mode，旧 seed、确认组、作者 fallback 不借给非 S0。coordinator、LiveRuntime 与保存帧 adapter 的原门禁仍保留，没有改识别门限、评分或共享结构/坐标/正侧门校验。

解包桥仅适配离线文件合同。season/mode 是来源标签，输出仍明确 `mapSelectionApplied:false`、`competitionScopeEnforced:false`、`runtimeIntegration:not_performed`；生产链没有将此标签作为资源权限的消费者。真实多季/多模式源配置仍需权威清单筛选，不把 S0 运行根复制追加为 S1。维护 skill 不把候选 staging 说成可安装增量包。

连点窗口目录暂缺仍暂停实际输入，恢复须通过原身份/前台/触发键释放门禁；明确换进程或绑定仍撤销意图。旧设置/schema、地图素材、安装事务、版本和资源锁未改。所有测试使用临时文件、合成地图/窗口/输入或替身，没有修改用户配置、读取真实游戏素材或发送实际游戏按键。

## 定向验证

将三个既有验证集合按模块名去重，完整重跑 69 个 Python 模块，不是全仓库测试宣称。每模块独立进程，Python 3.12 / PySide6 6.11.1，`QT_QPA_PLATFORM=offscreen`、`QT_QUICK_BACKEND=software`、`PYTHONPATH=app:tests:tools:experiments/qt_quick`，命令 `python -B -m unittest -v <module>`。模块清单、逐项结果和全部日志保存在忽略目录 `work/integration-evidence/regressions-full-season/summary.json` 及同目录 `.log`。

最终 Python 定向集合共 **69 模块、945 项：920 通过、6 失败、19 跳过**。6 项失败均经下面的精确基线对照，没有新增失败或超时；跳过原因完整保留在各模块日志，不算通过。

另行执行 `node --test tools/maps/extract/test-extraction.cjs`：9 项全部通过；`python skills/update-helper-maps/scripts/test_map_update.py`：19 项全部通过。独立复审重叠的 provider 7 项及 Node 9 项不重复累计。10 个变更 Python 文件 AST、两个变更 Node 执行文件语法及 `git diff --check` 通过。

失败对照使用另建干净 `a7d2010` 工作树，在相同环境重跑四个模块，记录在 `work/integration-evidence/baseline-full-season/`。六项失败身份/断言一致：live runtime 两项 static-reference 的 Linux/Windows API 边界；live controls 一项 HWND 复用旧 fixture；资源读取一项 Python 3.12 `Path.resolve()` 内部 stat 与 no-stat 替身断言冲突；相似性协议两项 rank 调用次数期望（1 != 2）。未删断言、改门限或把基线失败算成通过。

独立只读复审未发现新增阻断，确认来源范围与生产门禁。该结论不代表真实 S1 资源、Windows GUI/游戏、renderer 或原生输入验收通过。

## 后续打包前置

只读 `tools/build_qt_client.py --check-environment` 返回 `success:false`：当前非 Windows x64 Python 3.13，缺 Windows Qt 平台插件、PyInstaller 6.22.2、hooks 2026.7、pefile 2024.8.26 与显式 Inno Setup 编译器。未尝试在 Linux 制作 Windows Setup。

应先把精确集成提交送入独立 Windows 工作目录，复跑受影响联合检查和原生 UI/输入边界，再用获授权的 S0 资源及明确 hash 库存完整 freeze 独立 client 测试实例；不覆盖旧安装，不只重编旧 Setup 外壳。按仓库合同，可交付构建必须绑定最终同一远端 commit，因此当前禁止推送的范围下尚不能声称交付前置已齐全。构建后核对源码/资源/版本回执、管理员 manifest、Setup 字节数/SHA256，安装和游戏验收另列。历史 CI baseline 锚点需按实际比较目标复核。

S1 素材缺失不阻挡 S0 兼容与 S1 失败关闭的代码测试，但真正 S1 接入仍需来源明确的结构、索引/楼层、门/坐标/UI 模板、版本/hash、权威目标清单及完整消费者接入。不能仅改目录 available 或桥标签开启运行态。
