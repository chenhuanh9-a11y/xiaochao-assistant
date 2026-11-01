# 基础资源部署合同

`tools/build_qt_client.py` 的正式安装投影把完整基础清单写入
`_internal/managed-base-resources/manifest.json`，把每个基础源文件映射到该目录的
`payload/<安装根相对路径>`。安装所有权清单记录实际目标路径。Setup 不直接覆盖活动基础资源。
编辑器默认资源仍有独立的初次播种策略；用户自定义地图、路线、设置和 work 数据不属于基础清单。

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

每次修复在 `work/map-packages/base-repairs/<UUID>` 保留新对象、旧对象和持久 journal，
在 `pending-base-repair.json` 登记待完成事务。全部目标经 SHA256 核对后才提交受管状态。
异常恢复整个原集合；进程中断后下一次启动先恢复合法 journal。恢复发现未知改动时不覆盖，
保留备份和 pending 标记并阻止资源消费者启动。已完成、内容不变的重复调用不改写状态。
这些保证针对合作进程间的互斥和经验证的文件对象；不宣称整个文件系统在断电时原子提交。

`ManagedBaseResourceError` 提供稳定 `code`、`reason`、`issues` 和有界 `details`。
诊断项包含相对路径、期望/实际大小与 SHA256、缺失/非文件状态及拒绝原因，不含文件正文。
`plan_managed_base_resources` 是只读规划接口，调用者须在租约内取得一致视图。
`managed_base_inventory` 返回当前和候选清单的 casefold 路径并集，供作者删除流程保护基础文件；
存在但无效的清单会阻止删除，不能当作空清单。

仅重封装工具拒绝旧客户端配合新的候选安装投影。允许复用时必须核对原完整构建记录中的
候选 manifest SHA256/bundleId、release-report SHA256、实际 EXE SHA256、两个 profile
与编译源码提交；源码比较从实际 compiled commit 开始，不能只比较上次安装器包装 commit。
该校验沿用受信任本机构建记录的边界，不把 SHA256 当作发行者签名。

定向测试执行真实投影生成器与真实修复函数，文件内容为隔离合成 fixture。
Windows 测试验证真实 share=0/CRT 锁互斥及文件回滚原语；中断由异常注入模拟。
未执行 Inno 编译、Setup 安装/卸载、发布成品、真实进程强杀或断电测试。
