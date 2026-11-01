# 基础资源部署合同

`tools/build_qt_client.py` 的正式安装投影把完整基础清单写入
`_internal/managed-base-resources/manifest.json`，把每个基础源文件映射到该目录的
`payload/<安装根相对路径>`。安装所有权清单记录实际目标路径。Setup 不直接覆盖活动基础资源。
编辑器默认资源仍有独立的初次播种策略；用户自定义地图、路线、设置和 work 数据不属于基础清单。

已登记可选作者的精确 `references/author-packs/<author>/catalog.json` 与
`display-bindings.json` 属于作者显示包，不是共享基础依赖。正式投影将这 14 个可能路径分类为
`optional-author`，在 `omittedOptionalAuthorFiles` 中明确记录，既不写入基础候选清单，也不直接安装。
无作者包的新安装由现有可选 provider 跳过缺失作者；作者 ZIP 导入负责提供自己的元数据。
运行时仍完整验证 A066 旧候选/状态的清单格式与 bundleId，再从有效基础集合和删除保护集合中
移除这些精确路径；不读取、收养、覆盖或补回其活动内容。未知本地作者内容保留，作者已删除不复活。
旧事务 journal 保持原始清单校验和恢复/退役语义。其他基础路径仍执行全部所有权检查，
作者回执若显式 `requiresFiles` 依赖这些可选路径，仍对实际活动字节严格校验；不豁免任意 references 文件。

主入口在创建 provider、缓存或 worker 前取得 `resource_runtime_lease(root)`，调用
`repair_managed_base_resources(root)`，再检查 pending 状态；资源消费者全部退出后才释放租约。
基础修复、作者导入和恢复共用 `work/map-packages/import.lock`。Setup/卸载通过同一文件的
Windows `CreateFileW` share=0 句柄持锁到回滚/恢复结束，兼容运行时的 CRT 打开和 byte-0 锁。

修复先验证候选完整清单、候选字节、当前全部基础文件、旧受管状态以及已登记作者回执。

- 缺失基础文件可以补齐；内容已等于候选的旧安装可以收养为受管状态。
- 不同内容仅在匹配前次成功提交的 `managed-base-state.json` 哈希时允许迁移。
  无受管状态的不同旧字节没有可信所有权证据，报告 `base-ownership-unknown`，不得凭版本字符串覆盖。
- 作者回执的 `files` 与 `requiresFiles` 必须与完整的候选结果兼容；冲突则整体阻止修复。
  旧回执没有 `requiresFiles` 时允许同字节/缺失补齐，但禁止替换已有基础字节；不宣称已验证旧作者全部依赖。
- 候选或资源不完整、路径是链接/目录/文件障碍、未知所有权、跨卷或缺少硬链接能力均拒绝发布。
- 新版 profile 声明受管部署却缺失候选 manifest 时，明确报告部署缺口，不按旧版忽略。

每次修复先在 `work/map-packages/base-repairs/<UUID>` 写入 `preparing` journal 和
`pending-base-repair.json`，再准备新对象与其文件身份。全部目标经 SHA256 核对后才提交受管状态。
发布中异常恢复整个原集合；进程中断后下一次启动先恢复合法 journal。恢复发现未知改动时不覆盖。
已完成、内容不变的重复调用不改写活动状态或已清完的历史摘要。

`committed` / `rolled-back` 是不可倒退的事务终点。其后设置 `cleanupStatus=retiring`，
逐项核对 retained/backup 的相对路径、inode、大小和 SHA256，拒绝链接与未登记对象，再删除已证文件和空目录。
成功提交、成功回滚和成功恢复都必须完成副本清理；失败抛 `base-cleanup-incomplete`、保留 pending，
不得报告完全成功。下一次只继续清理终点事务，不再恢复任何历史活动文件。部分已删除的子集允许继续，
包括全部 payload 已删但摘要尚未写入、摘要已写但 pending 尚未移除的情况。

payload 清完后，大 journal 原子压缩为最多 1 KiB 的摘要，仅保留最新 16 条，总体最多 16 KiB；
清理旧摘要也逐项验证，仅删除已证摘要与空目录。`repair_managed_base_resources` 同时处理 A066
遗留的已完成但无 pending 历史；活动文件后来已更新或删除，不影响仅退役历史副本，绝不复活旧数据。
`retire_managed_base_resources` 提供同锁的显式退役入口；只读规划/诊断不调用它。

若准备文件已创建但 inode 见证尚未持久化时进程退出，现存对象不能仅凭相同内容猜测所有权：
保留该对象与 pending，明确报未知对象，需进一步人工核实。该极窄窗口不宣称自动恢复成功。
其他未知、内容/身份被改或不可写的副本同样保留并阻止资源消费者继续。
这些保证针对合作进程间的互斥和经验证的文件对象；不宣称整个文件系统在断电时原子提交。

`ManagedBaseResourceError` 提供稳定 `code`、`reason`、`issues` 和有界 `details`。
诊断项包含相对路径、期望/实际大小与 SHA256、缺失/非文件状态及拒绝原因，不含文件正文。
`plan_managed_base_resources` 是只读规划接口，调用者须在租约内取得一致视图。
`managed_base_inventory` 返回当前和候选有效基础清单的 casefold 路径并集，供作者删除流程保护基础文件；
存在但无效的清单会阻止删除，不能当作空清单。

仅重封装工具拒绝旧客户端配合新的候选安装投影。允许复用时必须核对原完整构建记录中的
候选 manifest SHA256/bundleId、release-report SHA256、实际 EXE SHA256、两个 profile
与编译源码提交；源码比较从实际 compiled commit 开始，不能只比较上次安装器包装 commit。
该校验沿用受信任本机构建记录的边界，不把 SHA256 当作发行者签名。

定向测试执行真实投影生成器与真实修复函数，文件内容为隔离合成 fixture。
Windows 测试验证真实 share=0/CRT 锁互斥及文件回滚原语；中断由异常注入模拟。
未执行 Inno 编译、Setup 安装/卸载、发布成品、真实进程强杀或断电测试。
