# 无硬链接环境的图包事务接缝

本文只说明图包新增/升级、作者快照与历史的协议增量。基础资源修复、安装覆盖清理和删除事务另有各自 journal；不把通过 POSIX fixture 当成原生 Windows 验收。

## 能力选择与版本边界

- `preflight_resource_publication` 只有明确的“不支持硬链接”错误才可选择 `move-v1`。权限、磁盘、未知错误继续失败。选择前实测目标同卷、祖先无链接、原子 move 不覆盖已存在目标以及 move 后对象身份保持。
- 新图包/快照 journal 写入显式 `publicationMode`。缺少字段的旧 journal 始终按原 `hardlink-v1` 解释，仍要求原有同 inode 见证。不能将旧 journal 的硬链接要求退化为内容一致。
- payload 的 identity、bytes、SHA256 在发布之前进入持久 journal。`move-v1` 以 `_move_owned(..., linkless=True)` 移动同一个对象，既不用 copy 充当旧对象备份，也不重新采纳发布时路径上出现的同字节新对象。
- Windows 使用核验内容/身份的同一 `READ|DELETE`、share-READ 句柄执行 `FileRenameInfo`、`ReplaceIfExists=0`。POSIX 使用 `renameat2(RENAME_NOREPLACE)` 并核验移动后的对象，仍不宣称能排除敌对进程在最后路径检查窗口做替换或原地写入。参考 Microsoft `SetFileInformationByHandle` 与 Linux `renameat2(2)`。

## 元数据见证

无需保持同 inode 的第二个文件才能保存小型元数据副本，但必须把“保留副本”和“发布对象”区分开：

- `pending-pointer.json` / `commit-pointer.json` / `addition-journal.json` 是保留副本。
- `<保留文件名>.publishing` 是独立新建的发布源，先记录再移动到实际 marker。
- `<保留文件名>.publication.json` 保存固定 schema、保留副本 identity、发布源 identity、bytes 与 SHA256。发布前持久化；恢复同时验证副本、marker 内容和 marker 的原对象身份。相同字节的外来 marker 不符合合同。
- 快照 journal 的 `activationHistory` 单独记录保留的 `new-activation.json` 身份；`changes[].new` 是实际发布的 activation 原对象。历史回退/删除读取保留副本时必须使用 `activationHistory`，不能要求它与 active 同 inode，也不能仅比较字节。
- 快照独立 `committed.json` 仍绑定 journal plan digest。`before_commit` 历史校验回调结束后再次核验旧对象备份、实际 activation 以及保留的历史副本。

控制 journal、proof 与 outcome 是事务声明，并非 payload 所有权本身。其格式、完整内容和引用必须匹配；与既有 journal 一样，声明的同内容重新编码不等于业务目标对象被替换。真正发布的 pending/commit marker、activation、payload 及原对象备份始终使用预持久 identity。恢复确认摘要另绑定声明的当前观察值，确认之后的变化须重新检查。

准备中断只允许有界恢复：未有 proof 的 `.publishing` 必须逐字节等于调用方从本事务生成的完整 canonical 控制声明，再持久记录 identity；已经落盘的完整 proof 或 proof.tmp 必须复用其原 source/retained identity。该规则绝不能用于 payload 或部分/改动数据。首次 proof 用同对象 no-replace move 发布，外来目标保留；重新发布先验证并移除原声明，再独占发布新声明。新增准备失败且已存在 journal 时不递归清除 stage，避免删除冲突对象。

## 失败、恢复与清理

- 新增的收据在 marker 发布前写入并记录 `newReceiptIdentity`。如原收据存在，journal 的 `oldReceipt` 保存其完整内容证据与 identity；先将原对象隔离，再独占发布新收据。最后新收据发布是提交点。
- 新增/升级在回滚时把已发布的新对象 move 回私有 stage/new 位置，然后 move 原备份回原位置。终态写入之前不先 unlink 新对象，避免自己提前释放其身份见证。
- 新增 `move-v1` 成功或回滚后清掉所有 payload 临时对象，保留 journal、outcome 与 publication proof 三个小型终态记录。它们由已有的收据约束删除事务一起退休，不在已清 marker 后进行无法追踪的部分元数据删除。
- 升级的紧凑 retired journal 嵌入 `publicationWitness`。退休清理即使已经删除部分 pointer/proof 元数据，仍可根据事先持久化的身份与内容继续验证剩余对象。只删除合同内的对象；未知文件或不匹配对象保留并报错。
- recovery plan 摘要包含新增 proof、publishing 路径以及原收据备份。用户确认之后发生的变化不能被忽略。
- 取消发生在提交前走同一回滚；提交后的清理失败只能继续清理，不能倒退已提交资源。跨进程资源 lease、原有 idle/closed 要求保持不变。

## 相关验证

`tests/test_linkless_package_publication.py` 使用真实临时文件与实际 no-replace move，只把 `os.link` 替换为明确不支持错误。覆盖：新增/升级/重复导入、快照安装/停用、取消、真实 `os._exit` 发布中断、升级每次实际 move 后退出、回滚中二次进程退出、原 inode 恢复、同字节不同 inode 的 stage/payload/pending/commit 替换保留、终态元数据部分退休，以及快照历史删除证据。

历史 API 接缝需与 `author_snapshot_history.py` 的对应兼容改动一起验：使用能力选择而非强制硬链接，按 `activationHistory` 验证保留副本，把 publication proofs 纳入历史证据。恢复不产生基础资源或删除授权。

原生 Windows 文件系统/句柄及最终 Setup 验收仍需独立执行，不能从这些合成资源和 Linux 进程故障测试推定通过。

## 新增准备记录的发现与恢复

新增 journal 先于 pending marker 持久化，不能仅凭全局 marker 缺失就允许下一次导入。运行时门以现有 `additions-recovery-required` 有界扫描 `staging-*` 中的小型控制记录，不访问或哈希图片；合法终态记录不会继续阻断。恢复列表以原有 `additions-pending`、`pending=True` 展示完整准备事务，不新增 UI 状态协议。

显式恢复重新验证 journal、全部私有原对象、收据、source/proof/proof.tmp 与整个私有目录清单。目标新增路径必须不存在、旧收据必须仍匹配，才能续发 marker 并走原有回滚终态与清理。journal/source/proof.tmp/完整 proof 四个落盘中断点都受支持。未知文件、改动/替换的 payload、部分控制记录均保持原状并失败关闭；不放宽删除的终态证明。

如较旧运行时曾漏过准备记录，且后来已经重装同包，恢复会明确报告现存目标，保留当前安装与旧准备记录，并提示导出诊断核查。此状态不自动回撤当前安装、不猜认两个事务的归属，也不建议直接删除 staging 目录。常规的新中断会在重装之前被门阻止，可按恢复记录完成后重装和删除。

### 旧版无 publicationMode 的兼容边界

旧 `7b98482`（亦见 `c94b5c8`、`f9b7c14`）十字段新增生产者尚未写入 outcome，并在私有副本清理之前清除 pending。真实进程在该点退出后，可以合法留有无 pending、无 outcome 的十字段 journal 和原硬链接；不能将它重判为新 move 协议未终态。既有删除读取合同另支持八字段历史兼容形状；该形状由原 `test_completed_addition_stage_is_discovered_without_receipt` 覆盖，未将其声称为已找到的真实旧生产者格式。

发现门仅对无 `publicationMode` 且精确匹配这两种已支持形状的记录保留原无指针语义。仍检查 schema、根身份、stage 位置、包/路径白名单、条目唯一性、原 identity 与旧收据 digest；十字段形状另验证完整 stagedIdentities/newReceipt，已有 outcome 必须匹配。不会补造终态、回放旧业务或扩大删除权限。payload 的原对象/哈希与未知邻居仍交既有删除合同处理，未知文件保留。坏记录、额外字段、半套新字段以及任何显式新 publicationMode 的未终态记录不能借此放行。
