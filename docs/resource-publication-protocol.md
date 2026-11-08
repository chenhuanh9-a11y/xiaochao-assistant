# Resource publication without hard links

The existing `hardlink-v1` protocol remains strict. Its retained hard links are
same-object witnesses; copying bytes is not an equivalent witness and is never
substituted for `os.link`.

`resource_transaction.preflight_resource_publication(root, relatives)` first
checks lexical link/reparse ancestors and the actual destination device, then
probes hard links in `work/map-packages`. Only a recognized unsupported operation
(including Windows error 1/50 and EINVAL/ENOSYS/ENOTSUP) permits a second probe.
Permission, disk, quota and unidentified errors still fail closed.

The second probe creates two complete private files, verifies distinct nonzero
file identities, checks that a move cannot replace the existing destination,
then checks a successful move preserves the source identity, size and SHA-256.
It returns `move-v1` only after those real operations succeed. The Windows move
uses the existing READ|DELETE, share-READ handle and FileRenameInfo with
ReplaceIfExists=false. Linux uses renameat2(RENAME_NOREPLACE); unsupported systems
do not fall back to a check-then-replacing rename.

References: Microsoft's [FILE_RENAME_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info)
and [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle),
and Linux [rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html).

## Managed base state machine

New move-mode journals contain an explicit `publication: move-v1`. The durable
entries already record the exact old and staged identities, sizes and hashes.
The entire plan, candidate and installed-package constraints are validated before
any active resource mutation. Staging copies candidate bytes only into private,
exclusive files and verifies them; it does not copy over active resources.

1. Create and flush a complete private pending marker, record its exact
   `pendingStamp` (identity, bytes, SHA-256) in the preparing journal, then move
   that object to the public marker path without replacement.
2. Stage each complete new file, capture its identity and hash, persist that
   identity, then persist the prepared state.
3. Move each verified old object into quarantine without replacing a target.
4. Move each verified staged object into the active path without replacement.
   The exact staged identity is transferred, rather than recreated by copy.
5. Verify the complete resource inventory before publishing the state file last.
   Persist committed before retiring backups and pending state.

An exception before committed rolls back. Recovery after process exit loads the
same journal and checks the entire active/backup plane before changing it. A
consumed staging pin may be absent only in move mode; existing pins must still
match. An active new object can be removed only by the recorded identity/hash.
Old objects move back with their original identity. If another process replaces
a target with identical bytes or modifies its contents, recovery preserves it,
retains quarantine and remains blocked. A second interruption during rollback
can resume from the old object already restored at its original path.

A durable committed journal is irreversible even if the write reports a later
flush error. Recovery then only cleans up. Unknown journal modes, corrupt
metadata, unknown private files, links, cross-device paths and missing old
evidence are not silently adopted. Existing hardlink journals keep their old
retained-pin requirements.

The compact terminal journal retains `publication` and `pendingStamp`, so a
same-byte replacement marker is still rejected during cleanup. Rearming a missing
marker first durably records its new private object's stamp. An interrupted
rearm only reuses that recorded private object; an unrecorded or replaced source
is preserved and blocked. Bounded retirement removes any recorded private rearm
object before deleting the last journal that proves its ownership. Old hardlink
journals retain their previous marker format and behavior.

## Boundaries and validation

The installation/runtime lease continues to serialize every writer and excludes
other processes. Base repair has no cancellation API; exceptions and process
loss use the same recovery path. Import transactions must retain their own
cancellation and final-receipt commit semantics when adopting this primitive.
Every other caller must explicitly adapt its witness/journal protocol before
using move mode; changing its preflight call alone is unsafe.

Files and metadata are flushed, with directory fsync on POSIX. Windows has no
portable directory flush in the existing adapter. No multi-file power-loss
atomicity or faulty-disk recovery is claimed. A torn/missing journal after power
loss remains a visible recovery block with available data retained, not guessed
ownership or reported success. POSIX retains the existing documented final
pathname/in-place race against hostile external writers; Windows uses the
verified source handle. Capability checks do not prove every filesystem behaves
correctly under hardware failure.

`tests/test_linkless_base_resources.py` uses synthetic bytes and real files,
real no-replace moves, production hashes/journals and actual child `os._exit`.
Only hard-link unavailability and named fault boundaries are injected. Cases
cover first startup, upgrade, cleanup, repeated startup, process loss and repeated
recovery loss, same-byte replacement, modified content, target races, corrupt
journals, uncertain committed writes, leases, link/device rejection and failed
capability probes. Linux execution does not count as Windows native acceptance.

Startup errors include only fixed relative `work/map-packages`, a fixed failure
substage and bounded capability enums. Full paths, account names, volume labels
and raw exception text are not emitted.

## 覆盖升级与安装器边界

`resource_upgrade_reset` 新模式持久化 pending 对象 stamp，再移动发布该对象。
旧资源按已确认清单移入隔离目录，保留原文件和目录身份；个人路线、设置等
既有保护范围不变。新资源记录身份后移动发布。取消或中断时，新对象回到私有
暂存目录，旧对象回原位；未知/修改/替换对象保持原样并报告恢复受阻。
已提交资源只完成 pending 清理，不反向恢复旧版本。

`installer_upgrade_journal` 的 anchor 选择证明格式。每个 generation 和
publication proof 发布前保留独立的 identity/长度/hash 记录，随后移动原对象。
版本链、root/目录身份、owner/session/target 绑定、native 完成证据以及
最新 generation 删除检测均保留。pending 的完整内容与发布前 stamp 保存在
单独 envelope，清除前再次核对当前对象。没有确定 native 完成证据时仍不前进，
进入 native 阶段后不因丢失应答重新执行 native 或单独回滚资源。

## 验证边界

`test_linkless_upgrade_reset` 通过禁止所有硬链接调用验证提交/取消、每次资源
移动后的中断、同字节不同对象替换、pending 替换、最新 generation 丢失以及
真实子进程 os._exit 后恢复。旧协议、历史目录、快照隔离和 coordinator 测试
作为接缝回归。Linux 临时目录与进程测试不代表 Windows Setup、exFAT 或用户
实际磁盘验收；最终组合提交仍须补同一安装包的原生验证。


## 单文件入口

旧作者领养只登记已经完整核实的认可库存收据；move-v1 下以独占原子移动
发布完整收据，作者图片本身不移动。下载 ZIP 复用 Windows 已持有的
READ/WRITE/DELETE 句柄与 FileRenameInfo 无覆盖发布，不为重命名关闭并重开
文件。发布后取消时先在同一句柄恢复私有文件名，再按原对象清理；若私有名
被外来文件占用，保留两个对象并报告恢复受阻。该单对象缓存不借助多文件
事务 journal；进程中断留下的完整校验 ZIP 可按原规则复用。

诊断 ZIP 在原目标目录完成后无覆盖移动发布，不要求目标文件系统支持硬链接。
`test_linkless_download_adoption` 区分 Windows 真实句柄用例和 POSIX 适配器
模拟用例；后者只验证业务流程、哈希、无覆盖与取消状态，不证明 Windows 锁。
## Confirmed package removal

Move-mode removal records the full original inventory and a `pendingStamp`
(identity, bytes, SHA-256) before publishing the complete pending marker by move.
The original selected objects move into `objects/N` while the journal is still
preparing. This is the reversible deletion, not a copied backup followed by an
unlink. Prepared verifies that every active name remains absent and every
quarantined object still matches. Committed is the irreversible boundary.

Cancellation before that boundary restores the original objects without
consulting further cancellation requests; cancellation after committed cannot
turn success into a rollback. Process-loss recovery uses the shared dispatcher,
validates the whole restoration before moving anything and resumes after a second
interruption. A same-byte replacement at the active name or pending-marker name
is rejected by identity. After committed or rolled-back, cleanup does not inspect,
restore or prune newly created user objects at former active resource paths.
Unknown or modified quarantine objects remain blocked and preserved. Legacy
hardlink removal journals keep their existing recovery rules.

`tests/test_linkless_package_removal.py` covers real no-link deletion/restoration,
both cancellation sides of commit, actual process exit and repeated recovery
exit, original identities, marker replacement, quarantine modification, target
appearance, uncertain commit write and interrupted terminal cleanup. Installation
of these synthetic source fixtures is separate from the removal test scope.
