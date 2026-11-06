# 2026-11-06 开发基线与现存分支台账

返回[开发 SOP](project-development-sop.md)和[功能模块维护表](maintenance-modules.md)。这是一次只读核对的历史截面，不是自动更新的在线状态。开始新任务仍需 fetch 并核对最新 SHA。

## 1. 本次文档来源

核对时间：2026-11-06 14:30 UTC（北京时间 22:30）。仓库为 `chenhuanh9-a11y/xiaochao-assistant`。

| 来源 | 精确身份与状态 | 本次可以据此说明什么 |
| --- | --- | --- |
| 远端 main | `029bf1c2fc36aec0ce4eea6537283e3f994f6c6b`；version.json 为 1.0.7 / 1.0.0.11 | 真实远端源码，不等于最新本机或已发包 |
| 远端 codex/release-1.1.0-20261106 | `d2d30a7af2f9c570254c9e830135b778cb1faeaa`；1.1.0 / 1.0.0.22 | 本文档分支基线；main 是其祖先，额外 74 个可达提交，差异 219 文件 |
| 远端 codex/s1-formal-release-1.0.17 | `b80f4be3baf57242976960572282ed43512bcccf` | 另一真实远端候选线，不因名称判定已发布 |
| 本机已交付独立 1.1.1 测试候选 | `b9d60cf1df77892e758bce9c21b3a7568e6d7d99`；1.1.1 / 1.0.0.23 | 从本机交付回执核对；不在本次 fetch 到的远端对象中，未推送/未发布 |
| 后续图库入口候选 | 本机记录只读确认短 SHA `76f794fe`，仍 1.1.1 / 1.0.0.23；完整 SHA 尚未取得 | 构建已经开始，后续独立安装/升级暂停，待资源页卡顿与导入进度合并后统一回归；不是最终发布事实 |
| 云端参考目录 reference-independent-isolated | 无 `.git` 的 1.1.1 源码快照与定向补丁；其部分文件 hash 与 b9d60cf1 Git blob 核对一致 | 补充最新模块和未同步差异，不能给整棵目录授予 b9d60cf1 历史或最终包身份 |

本文档分支 `docs/project-development-sop-20261106` 从远端 d2d30a7a 新建，**只改文档**。对 main 的 219 个文件差异是继承的产品基线差异，不是 SOP 新增实现；需要迁入 main 时审查文档提交本身，不顺带合并所有候选代码。本次没有改产品版本、资源锁或正式分支，没有创建模块空分支，没有安装、打包、上传或发公告。

当前本机功能线领先远端，无法仅从 main 得出“整个最新产品已在云端”。下一轮先同步获准的源码或按精确哈希交接。新文档也属于 tracked payload，当前 S1 构建锁可能不再匹配文档分支；本分支未做生产构建就绪声明。

## 2. 已读取的历史证据与边界

- 当前 AGENTS.md、development.md、Windows 公告/模板、实际 build_qt_client.py/spec/iss、requirements、15 个 CI 工作流、S1 原生/生产资源/作者协议、资源发行与发布候选文档均已用于整理。
- docs/release-1.0.7.md 明确引用的历史已发布基线是 1.0.6（2026-11-02、versionCode 1715）。这只是该历史发布记录的基线，**本次未读取线上更新服务来确认今天最新已发布版本**。
- 本次没有重新获取并完整验证“最近一次正式最终公告全文”；因此没有编写新公告、推定当前 versionCode、确认 1.1.0 已线上发布或把本机 1.1.1 测试包冒充正式基线。真正发版时必须补齐这一步。
- 本机 `DELIVERY-STATIC-111.md` 的交付摘要已通过只读任务结果核对：b9d60cf1 的 Setup 为 **301,653,723 字节**，SHA256 `9cc43d2e50177bc6f495cff086a770118517eea70c0eaf9f68e661aaf87348d1`。记录报告 515 项联合回归、18 个原生 Qt 场景、20 步安装/升级和 4283 个保留文件哈希一致；现用安装未改，未操作游戏、推送或发布。
- 上述是**本机既有交付记录**，不是本次文档任务重新执行的验收。本机记录同时保留“未锁候选误排风险仍未解决；新静态图链路真实游戏及冻结包链路仍待验收”，不能省略这些限制。
- 后续 76f794fe 的 180 项 Windows 回归有先失败后重跑记录：首轮临时目录前提错误，改用独立系统临时目录后通过，源码安全检查未降低；原始失败保留。图库原生验证报告实际点击、真实设置保存/重载和上下文/忙碌保护通过；不应把它累加成 b9d60cf1 的旧结果。

## 3. 本次执行的文档验证

- 独立 clone，fetch 当前 origin，读取 118 个真实分支 head；从核实的 release 基线建立 SOP 分支。
- 已核对构建、预览和开发骨架三个 `--help` 入口参数。未执行 Windows 构建、安装、升级或发布。
- Linux Python 3.12.14 隔离环境中，`test_floor_contract`、`test_preview_windows_notice`、`test_qt_installer_directory` 共 20 tests 通过，无 failure/error/skip。只证明选定合同与命令可运行，不是全库或 1.1.1 最终回归。
- 文档相对链接/明确路径检查、文档 diff --check 和提交/远端身份在本次交付时另行核验；交付回执记录最终 SHA，不在文档内自引造成 hash 循环。

## 4. 现存远端分支：完整清单

下表由 `git for-each-ref` 读取当前远端跟踪 refs，排除 `origin/HEAD` 符号引用；共 **118** 条。这里尚不含本次随后推送的新 SOP 分支。完整 SHA 便于重放和比较。

“main 祖先”“1.1.0 基线祖先”仅表示 Git 提交可达性，不证明测试通过、功能仍有效、当前活跃、已经发布或可以删除。未被完整包含的分支可能含未合入变更，也可能是 squash/cherry-pick 后历史，不能单凭祖先关系宣告待合并。模块名称导航在维护表；本表不从名称猜负责人。

| 真实远端分支（省略 origin/） | 冻结 head SHA | 与核对基线的关系 |
| --- | --- | --- |
| `author/lianghapi-nightmare` | `31f0706587d76fe7ac3da691c9d563ceec1bdf4c` | main 祖先 |
| `chore/quark-link-de934000142f` | `d947cc22128d234f35890d3402a7cd3af4179ac2` | main 祖先 |
| `claude/inapp-tutorial-4eace3` | `25280fcd5c06c8eac70533639a0767b8a63a2f04` | main 祖先 |
| `cloud/diagnostics-e3-e2-e1-f2-20261102` | `8122d18d4a40ca03432af16c7311d7b93a68aa7b` | 未被两条基线完整包含 |
| `cloud/diagnostics-main-20261102` | `76546adebacdb90a334128837a5f749fa92a680b` | main 祖先 |
| `cloud/gallery-recovery-compat` | `797ac0b7b411cd33063b74e1d6f6d365a178297d` | main 祖先 |
| `codex/aicu-author-label-20261028` | `544b34d13c89908113a49cc6b1feee43754031fa` | main 祖先 |
| `codex/alignment-queued-recovery-20261028` | `a89dd94720a8690767a0c59cb3c65f8ab3383c5f` | main 祖先 |
| `codex/author-base-display-fallback` | `adf0d3f796c391d046a84a20000f38f86e62309d` | main 祖先 |
| `codex/author-import-without-restart-20261028` | `8bdb1af30e7f3de278d1eb329a1df2f9836d8e81` | main 祖先 |
| `codex/author-legacy-adoption` | `fd4444646272af8267ba23d9735d0d52452c6970` | main 祖先 |
| `codex/capture-present-integration` | `464836295ba95ee5c88118d4f54654a226819b59` | main 祖先 |
| `codex/diagnostic-evidence-20261029` | `9d3d1a8029525d94eb36598f890a4f07be81bf99` | main 祖先 |
| `codex/diagnostic-session-association-20261029` | `8ce2a0e846783f4d6febf51d3e872434b0c474b0` | main 祖先 |
| `codex/display-recovery-6484` | `ede716c438f9f26368727a4fcca355d19b7c264b` | main 祖先 |
| `codex/editor-static-door-display` | `953f8f9715bc6d8b1d22dac27f5e60f7436368d4` | main 祖先 |
| `codex/f1-current-frame-recovery` | `14bc84b5d873f3efcf726e083b1826cd1b59400a` | main 祖先 |
| `codex/fixed-map-position-editor` | `c7a27f619c6b1651605d9e89c3dd2d85780a7c8f` | main 祖先 |
| `codex/fixed-map-position-trial-launcher` | `2efa2f88b7ac970db772ec2ddc05e13d7c05305b` | 未被两条基线完整包含 |
| `codex/friend-ui-proportions-20261028` | `57fe8caca03de73b9a7464cf0c8e1b62c6de8934` | main 祖先 |
| `codex/front-gate-group-display` | `a153ff968179ce245e7532f393a87886058474cb` | main 祖先 |
| `codex/idvb0059-steady-main-20261029` | `067b7615df1a3b229e9da958fc2702f0e4494664` | main 祖先 |
| `codex/import-103-compatibility` | `9a58a5981e4abcb9f200eaeb22d83505da23e04e` | main 祖先 |
| `codex/import-and-queued-delivery-20261028` | `48a4b18d6c47f2aeed4a7343473d66b73bd323fc` | main 祖先 |
| `codex/installed-download-state-20261028` | `183d9ce1f93d7b9401a855beae7556deaadcab03` | main 祖先 |
| `codex/lightweight-engineering-guardrails-20261029` | `42f9fc0944f48da943820c74b8b635963a129e4b` | main 祖先 |
| `codex/map-maintenance-20261029` | `17209965c8b03eb0c2236c0099871daddcb8f03f` | main 祖先 |
| `codex/multi-author-route-switch-20261028` | `fb83d09ad5eb07ef21201fe730dfa5aa7246d514` | main 祖先 |
| `codex/native-support-window-20261028` | `2757ae24be42212229fee055980a22c2f6e78154` | main 祖先 |
| `codex/pr76-display-recovery-20261103` | `1b2f59f1bccc9b6615289a55bcc8aa3e0f52de7a` | 未被两条基线完整包含 |
| `codex/public-map-extraction-tools` | `cb1d072b76f5eb55f8a50f6bd7aab697264a97fb` | main 祖先 |
| `codex/release-1.0.2-20261028` | `73de4641034994ed90aaf9cd8c5b14c86df3e988` | 未被两条基线完整包含 |
| `codex/release-1.1.0-20261106` | `d2d30a7af2f9c570254c9e830135b778cb1faeaa` | 1.1.0 基线祖先 |
| `codex/release-104-import-compat` | `6b3e35db2a728591fd9fd62782db310ce09ee1d7` | main 祖先 |
| `codex/reopen-lifecycle-20261028` | `819a60763716a8f563f3695c32e0b2dc7c148f38` | main 祖先 |
| `codex/reopen-runtime-20261028` | `86588c9de3da60d2992abea63896386755b143d5` | main 祖先 |
| `codex/runtime-codec-exclusions-20261028` | `da3fbd5dddfa7854a094b8bc130066ac1c42f396` | main 祖先 |
| `codex/s1-formal-release-1.0.17` | `b80f4be3baf57242976960572282ed43512bcccf` | 1.1.0 基线祖先 |
| `codex/side-preprocess-20261029` | `fe1480953601c8fbf1c710d17efd121c428d1693` | 未被两条基线完整包含 |
| `codex/similar-map-groups` | `a2c358fb6ce2b5f2628551d7a569ed65e98486d4` | main 祖先 |
| `codex/similarity-two-stage` | `2270e6f6d36051ac69882cc163aac492c6d028ae` | main 祖先 |
| `codex/tilemap-custom-editor` | `9fcdcd3b9d0da45f884d3228ffebabb2f19c18a3` | 未被两条基线完整包含 |
| `codex/ui-page-refresh-performance` | `60b7a1c2f45d47c305c0c4795e3752d9510b0cb8` | main 祖先 |
| `codex/xiaochao-side-entry` | `18c6c1b9d79504ea79f16eaa813eeace23c3ac28` | main 祖先 |
| `dev/current-integration-20261027` | `e50e2c1c2b2a5c84bed78daa5e75bb7e724852f1` | main 祖先 |
| `docs/author-resource-recovery-contract` | `e449adf5fdb3b39d25e5b2899f8fde9ff74618e1` | main 祖先 |
| `experiment/qt-quick-resource-lab` | `8f8b14bda334ccf7bba26d1eadd87f723e83a34a` | main 祖先 |
| `feat/aichicu-difficult-author-display` | `be988cd7a4b7ffb8648f1e56b0b6742b86275d1a` | main 祖先 |
| `feat/default-failure-evidence` | `96dc4287fa50e01a87b3101d03d772e463f5d4ca` | main 祖先 |
| `feat/floor-control-region` | `2f3d6edf62b8e427c7acb197d07fc29fc62ae8b3` | main 祖先 |
| `feat/qt-next-modules` | `0a79bd109436d4ef251b76744344764c33116cbc` | 未被两条基线完整包含 |
| `feat/qt-route-plaza-account-login` | `4b038212bb5381ce899cd0b0fc537628ca228627` | main 祖先 |
| `feat/qt-route-plaza-submit` | `e255ff5bafe7f3685cddeadbb25c71a671278b10` | main 祖先 |
| `feat/qt-route-room-numbers` | `629333b88ac576fe1beb5df9fb99f545249fd4a3` | main 祖先 |
| `feat/zhan10-nightmare-author-display` | `7fe18149e531917912718d5deb5a945b9cad1bda` | 未被两条基线完整包含 |
| `feature/control-panel-layout` | `d78153f28f05d10cc04b2404784a0df8ed862276` | main 祖先 |
| `feature/custom-maps-chest-tap` | `477294a240f07a44af4cbdcd9dd35c46f577f841` | main 祖先 |
| `feature/legacy-custom-maps` | `dd54988e4db21484f04756b7b7520bedbc34a72f` | 未被两条基线完整包含 |
| `feature/manual-map-mode` | `dc3312358657045e8a61acd82cae5ca25d0e0c8e` | main 祖先 |
| `feature/map-resources-page` | `ea1367d15c2c408079bf252449a636a766b82441` | main 祖先 |
| `feature/qt-manual-map-mode` | `82c60d710538e0a82f68d57ca363ac3fef64ecaa` | main 祖先 |
| `feature/readable-custom-maps` | `4021df159cefa4607e3895773281289a71754b7b` | main 祖先 |
| `feature/route-outline` | `2a7a0cfd5e923c7e95564ca0406a9809fc33e2b9` | main 祖先 |
| `feature/route-plaza` | `0d3c39481926b8267b2964cbd5363a97e6456569` | 未被两条基线完整包含 |
| `feature/windows-notice-publisher` | `7e7d2c244b8cbbdeae7c47b344a0d1dcdc666672` | main 祖先 |
| `feature/xingtu-ui-refresh` | `c398f14aaf4b6dddcdc51612b04d05ffd55addda` | main 祖先 |
| `fix/admin-spec-test-double` | `8a3d7aa7cc68f0c368cf5bbfecd1fc82959b483e` | main 祖先 |
| `fix/author-resource-recovery` | `06b231094f7ae834bb739b818fb75d7cad7efd53` | main 祖先 |
| `fix/base-display-consumers` | `c95db98e6eac1e8f0a3f81caf3c6afea78b07836` | main 祖先 |
| `fix/bounded-runtime-storage` | `7125b82d786a057f30c4efa8cf6407af3eb9a319` | main 祖先 |
| `fix/chest-conflict-recovery` | `9f440afea1fd75bfee23b3254315a3d08644dae3` | main 祖先 |
| `fix/custom-road-links` | `7c7f41eea7a9c0cf641d8547081f3a94f9091db3` | main 祖先 |
| `fix/custom-route-full-cells` | `0211e756ebbfba806e2c2ecad3466d73fc4fa651` | 未被两条基线完整包含 |
| `fix/default-admin-executable` | `c09ff0ae89e3348d72a0ee651807db601c1920f1` | main 祖先 |
| `fix/foreground-return-multiframe` | `3f4611694d0cb97c8804d8338dcd2dd56d461b13` | main 祖先 |
| `fix/gallery-download-traffic` | `0b654e7df06a0d3e499b875dd135eb1a7808f1b8` | main 祖先 |
| `fix/interaction-64710` | `75d048c39ab90012085fe1a177121c66074c2d7f` | main 祖先 |
| `fix/live-author-upgrade` | `ef072704d19dc2bb3949af7c0c06e68ac2bad749` | main 祖先 |
| `fix/managed-packages-tk-anchor-20261029` | `216437d61fb2793cb1c22c186c450375a2406cc7` | main 祖先 |
| `fix/map-package-safe-recovery` | `baae1b00076547d61f196213d7f17f7b218e33d5` | 未被两条基线完整包含 |
| `fix/map-window-shape-feedback` | `0372d8507d6bb275cbc75bd46a07ba0c2b08e68d` | 未被两条基线完整包含 |
| `fix/native-staging-resource-grant` | `f2366813652227ec7f0b8a29e0a573d90eeca82b` | main 祖先 |
| `fix/old-pc-regressions` | `8c763f689f203788df285b46577b835d4ee4e286` | main 祖先 |
| `fix/owned-map-package-upgrades-20261027` | `717b44e94992787d20b6d5c915a998b6edcdec52` | main 祖先 |
| `fix/packaged-map-resources-audit` | `95d8336e5ed60c2fb01d1909e5fdad56fd32494a` | main 祖先 |
| `fix/packaged-ui-audit` | `f2e11797d91f5d35afc4310f656dff697b449697` | main 祖先 |
| `fix/pr76-invalidation-origin` | `8a416f798d82f0b5b6237accf43934830ee762fe` | main 祖先 |
| `fix/qt-bundled-map-defaults` | `693452144a9d667252646e4cacff884817def449` | main 祖先 |
| `fix/qt-chest-explicit-intent` | `da60af4a90560a8edbe5a94ee17559dba0d49622` | 未被两条基线完整包含 |
| `fix/qt-controls-hidden-preference` | `bde54153ba8b7e17ac9797080a7ca1c8ebca6eae` | 未被两条基线完整包含 |
| `fix/qt-field-feedback-8f8b` | `980d64949dcec3bcfb747dbf8bacb6dfc00fbded` | 未被两条基线完整包含 |
| `fix/qt-floor-ready-policy` | `30b48bf3135752216e4e689159a4d02dee22f2c0` | main 祖先 |
| `fix/qt-hotkey-layout-compat` | `495e3c9bcf15f4b791cd6c8b0a26d3309a2e388b` | main 祖先 |
| `fix/qt-manual-picker-takeover-20261102` | `82c60d710538e0a82f68d57ca363ac3fef64ecaa` | main 祖先 |
| `fix/qt-map-intent-reset` | `5a522dc776a2da53e24989c69aa1d747e066307e` | main 祖先 |
| `fix/qt-modifier-cloud-ci-20261102` | `6a8647f74ef542d76c643d14cd24824752b1b41e` | 未被两条基线完整包含 |
| `fix/qt-modifier-main-ci-20261102` | `935b1c08e8b013bef34a38faac5ad10eb69c7575` | main 祖先 |
| `fix/qt-notice-build-record` | `c673066bae30afbd804b5d1643453d23ab7f4d0b` | 未被两条基线完整包含 |
| `fix/qt-recapture-focus-flow` | `382d705ab8cf570755f6c346a6b9e35eed1947e7` | 未被两条基线完整包含 |
| `fix/qt-setup-version-fields` | `75005d219b708df47f075f9ff5b1de4bb1cbd563` | main 祖先 |
| `fix/qt-test-install-directory` | `e9a8e26d5caf2251518940e982280dce15b65abc` | 未被两条基线完整包含 |
| `fix/resource-unready-feedback` | `75b634039b84c9767393c658c54c738179490e23` | 未被两条基线完整包含 |
| `fix/retained-display-alias` | `88c21e4660980b850f6337e8847b7a9898c521de` | main 祖先 |
| `fix/rounded-refresh-p0` | `d21bd9c8a0953eb26e24ce2847666e917f8217c7` | main 祖先 |
| `fix/runtime-freeze-090b560` | `a8d668a8448b5bc68f95657cf9187275a76ba865` | main 祖先 |
| `fix/s0-vertical-only-20261105` | `5dd3bcea27fd80cf8074c4f65179e7e0be60d9be` | main 祖先 |
| `fix/settings-resource-recovery` | `de4296bebb910355e7ffaf156355a7c7184e76d2` | main 祖先 |
| `fix/transient-fit-reporting` | `d45877f1c2ad3cd5b034c8e7a3cc435a3bc4b6a8` | 未被两条基线完整包含 |
| `fix/ui-cold-transitions` | `be7153afaff3f6e734c2804852d19a4735cd657f` | main 祖先 |
| `fix/update-check-retry-state` | `7ff21cdb12e1298de9ed22ad28b5042c6950e2db` | main 祖先 |
| `integration/production-pr73-74-76-90-91-20261103` | `f1987e8986916ea587d42c461b345d1349ff105f` | main 祖先 |
| `integration/qt-next-20261102` | `b7cb7bf74a304214dcd7fa1f2096c3bb4108c98d` | main 祖先 |
| `integration/qt-pr84-88-20261103` | `df40f854ccf57fb4ab70051414b2f9b4693eb6a2` | main 祖先 |
| `main` | `029bf1c2fc36aec0ce4eea6537283e3f994f6c6b` | main 祖先 |
| `perf/ui-smoothness` | `143265d7ea6ebe6ed8179b508d21cf60d7a7c344` | main 祖先 |
| `release/1.0.5` | `6d3e07cfb2856c2aa90b3aa00b220ddbb751aeec` | main 祖先 |
| `release/qt-1.0.6-metadata` | `76dfa533b1249754148e388ab5c9a3b78ea96767` | main 祖先 |
| `test/qt-shortcut-fixture-readiness-20261102` | `4aa1e103276da062dfdc378f6c4189dc63174526` | 未被两条基线完整包含 |
