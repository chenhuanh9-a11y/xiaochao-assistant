# 来源与权利说明

`extract-map-configs.cjs` 与 `lib/` 复用此前已整理的独立配置提取工具（2026-10-25），没有改为执行游戏 Python。保留原始文件的来源注释：

- `resource-codec.cjs`：由 MiniMap_Creator 的 `tools/extract_doors.js` 与 `check-layouts.cjs` 分离纯解码部分。
- `marshal.js`、`recover_source_literals.js`、`netease_opmap.py`：沿用原工具的 marshal 解析、静态字面量恢复、自定义操作码数据。`netease_opmap.py` 只当文本读取，不启动 Python。
- NXS3 算法在此前整理包中注明参考 `NeoXtractor-IDXWPK/core/npk/decompression.py` 的 `unpack_nxs3`；保留该来源说明。
- `export-catalog.cjs` 的房间变换及反向 `room_paths` 配对规则来自既有 `update-three.cjs` 的数据验证流程，重构为参数化导出器；不复制该历史流程中的任何数据或素材。

所复用的本地独立工具包未附单独 LICENSE 文件，因此本次不另行声称 MIT/Apache 等授权，也不删除或覆盖已有来源注释。仓库已有第三方许可证原样保留。第三方工具及游戏内容的权利归各权利人；脚本的公开不构成对原游戏资源的再分发授权。

提交只含脚本、文档和临时生成合成 fixture 的测试；不含原包、解包配置、模块 `.bin`、作者图、贴图、账户、日志或凭据。代码中的 RSA **公钥**是原封装解码算法常量，不是用户私钥或发布凭据。
