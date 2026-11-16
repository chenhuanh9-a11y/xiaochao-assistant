# 地图资源导入与加载检查清单

任务：删除非必要和重复检查，规则不变，提交草稿 PR。基线为 `502ec2a9c690a6d0d3e7b25f5cba8b78f51585b5`。本清单先于产品改动落盘。

范围仅为地图资源。相邻地图识别、楼层判断、路线显示只核对资源交接，不改变语义准入、相似组、阈值或显示授权。遵守任务转述的已决定卡片：56 的 S0 模板相似度拦截、63 的兼容基础版本和逐层结构/坐标核对、55 的缺导航版本旧路线拒绝。已读本机进度、模块、发卡规则及仓库开发规范；进度中的未合入信息以本轮实际远端基线为准。本轮资料未附服务器的完整决定档案，不把任务转述以外的历史决定补作授权。

“保留”表示该步骤挡住错包、坏包或依赖不匹配；取消、回退、覆盖确认和旧请求失效属于既有行为，保持不变。次数以一次服务操作为单位，N 是库存版本数，F 是相应包文件数；动态准确计数和时间另见实测记录。

| 路径及位置（基线函数） | 触发、扫描范围与内容 | 处理及理由 |
| --- | --- | --- |
| 资源页 `ClientImportedResources.qml` → `listPackages` | 首次进入且库存未加载时 1 次；普通刷新不重挂页面 | 保留行为 |
| `imported_resource_inventory.list_saved_versions/_scan_packages/_read_version` | 每次列表扫描 N 个版本的有界清单及入口存在性；普通列表不读所有载荷 | 保留：错包、坏包 |
| 同上 `verify=True` | 显式检查文件时，每版本一次完整目录、大小、摘要检查 | 保留：坏包；不能把按钮变成只读标题 |
| 库存 `_assert_observed_manifests_current`、catalog token | 列表返回前复核所观察清单与选择代次 | 保留：坏包、依赖不匹配；不能把旧观察报告为当前 |
| `ImportedResourceService._read_inventory` | 列表后再取当前 catalog，核对 generation，投影可用模式 | 保留：依赖不匹配 |
| `client_imported_admission.available_base_contexts/_context_base` | 每个活动模式读基础和控件全部文件，公共组件在本次扫描内去重；读域退出复查实际字节 | 保留：坏包、错包；没有跨操作健康证明可复用 |
| 手动 `_import` URL、删除事务状态 | 每次导入 1 次；必须本地文件、不得跨未完成删除 | 保留：错包及失败恢复行为 |
| 在线 `client_downloads` 下载摘要/大小/目录绑定 | 下载流完成时 1 次；服务接收时再绑定当前请求 | 保留：错包、坏包；不改变下载和迟到请求行为 |
| 在线 `_import` → `inspect_archive` → `save_archive` | 同一服务操作先完整检查归档、解压并丢弃全部载荷，随后保存又读同一归档并解压 | 删除独立预检：把包名、大小绑定并入保存时的清单检查，原摘要校验保留；没有必要解压两次 |
| `_open_archive` | 每次开包完整 SHA、规则文件/大小、ZIP 尾部及中央目录上限 | 保留：坏包；外部传来的下载证明不能代替当前实际文件 |
| `_same_archive` | 保持同一打开文件期间核对文件及路径身份、大小和时间 | 保留：坏包；防替换、写入 |
| `_archive_inventory/_parse_manifest` | 1 次 ZIP 全条目元数据和 manifest；类型、版本、角色、上下文、能力声明、重复字段 | 保留：错包、坏包 |
| `_archive_inventory/_reject_file_prefix_conflicts` | F 个路径及前缀，禁止逃逸、链接、重复和文件/目录冲突，限制解压尺寸 | 保留：坏包 |
| `_extract_archive/_read_member` | F 个载荷一次解压、CRC、大小、SHA、排他写入及落盘字节核对 | 保留：坏包 |
| `_validate_staged(stage)` | 刚逐文件写入并核对后，再完整扫描暂存包 | 删除：解压已核对各文件，发布前 `_verify_owned_stage` 仍独立验证完整树，语义读取也校验实际输入 |
| `_import.validate_components` | 每组件 1 次：后缀、单一基础/路线角色，基础/控件成对，作者需活动基础 | 保留：错包、依赖不匹配 |
| `admit_import` 作者前置校验 | 当前基础全内容，作者身份和兼容基础声明 | 保留：坏包、依赖不匹配；不是最终逐层授权 |
| `save_archive` 发布前第二次 `_open_archive` | 再完整哈希原归档及解析 ZIP 目录 | 删除第二次开包和 ZIP 解析，保留首次描述符至发布前并对该句柄重新完整哈希；必要理由为坏包：Windows 可恢复全部时间元数据，同长度改写不能仅凭 stamp 排除 |
| `_verify_owned_stage` | 发布前一次完整拥有清单、文件内容/身份、目录身份核对 | 保留：坏包；能发现校验回调期间暂存文件被改或新增 |
| 已存在版本 `_existing_version` | 同版本重试及发布竞争，完整验证已有目录 | 保留：坏包；不得把已损坏库存当成成功重试 |
| 发布后 `_validate_staged(root)` | 完整检查真实目标，绑定发布目录身份 | 保留：坏包；不能仅凭移动成功报资源就绪 |
| `_remove_owned_stage` | 仅清理已知仍属于本操作的暂存对象，未知/被改对象保留 | 保留现有数据保护，不借优化修改删除行为 |
| 保存成功 `_read_inventory(include_availability=False)` | 保存后扫描 N 个清单，随后计划又扫描 N 个清单 | 暂保留：该结果写 UI 库存，失败/取消也须显示已保存版本；不把扫描挪到计时后 |
| 自动基础 / 显式启用 `plan_context_activations` | 每次计划 1 次全库存及相关作者兼容选项；可能等待覆盖确认 | 保留：依赖不匹配；计划不能跨用户等待复用健康结论 |
| `_plan_result/_report_import` | 有交互计划或早退出时补完整库存与可用模式 | 保留行为：长时间计划、取消或租约拒绝也要准确回执 |
| `selection.inspect_selection` | 每次前置/提交校验，对选中闭包每包一次完整库存，检查角色、模式、能力和依赖 | 保留：错包、坏包、依赖不匹配 |
| `_SelectionValidator._validate_context` | 首次资格构造真实引擎、基础/控件、作者逐层结构/坐标和 fast 声明；相同输入纯语义结果有既有有界复用 | 保留：坏包、依赖不匹配；S0 模板拦截和跨兼容版本规则不变 |
| `_SelectionValidator._prove_inputs` | 已 `inspect_selection` 完整库存，资格复用或多模式并行后再查完整库存，随后读域又读选中组件全部文件 | 保留：坏包。远端既有回归真实证明，最后完整库存之后、提交见证之前作者载荷仍可被改写；跨包/作者身份核对时间段必须有退出读域的最终实际载荷证明，不属于可删重复 |
| `PackageView._read_manifest_bytes/_validate_disk_tree/_read_payload`、库存 `_read_version` | 稳定读取函数已对返回的同一 bytes 计算摘要，调用者立即再次哈希 | 删除第二次同字节哈希，直接比较稳定读取返回的摘要；文件每次仍实际读取和验证，不缓存跨操作健康结论 |
| `ComponentResourceReader.json` | `.json`/`.JSON` 的同一 bytes 经普通读取预检解析一次，返回对象前又解析一次 | 合为一次解析并返回该局部对象；保留命名空间、大小上限、复杂度/非有限数/重复键拦截及成功观察者顺序；不缓存跨调用结果 |
| `activate_catalog_locked` | 发布前和 durable commit 前调用验证器，核对 catalog 身份及事务回退 | 保留：坏包、依赖不匹配；两次之间有真实写入，不能删成一次 |
| 启用完成 `_read_inventory`、`resourcesChanged` | 完整列表/可用模式刷新及消费者失效；importCompleted 等这些结束 | 保留：坏包及当前资源交接，不延迟到计时终点之后 |
| 新进程启动/`prepare_context` | 从当前 catalog 重新读基础、控件、作者，构造实际 provider/引擎；不复用上一进程健康结果 | 保留：错包、坏包、依赖不匹配 |
| 同进程重复 `prepare_context`/读取域 | 既有私有构造及纯结果复用；每次操作仍验证实际消耗的资源和当前代次 | 保留：坏包；不改识别/显示消费者的读取合同 |

## 实施与交付记录

实施删除：在线独立整包预检、保存时重复打开归档/解析 ZIP、解压后立即重复全库存、稳定读取后对同一 bytes 的重复哈希、同一 JSON 的重复解析。发布前的第二次实际归档哈希保留：独立 Windows 反例证明，仅依靠文件身份/长度/时间不能保证原摘要仍有效；已加入真实元数据恢复回归。最终选择的退出读域载荷复验恢复为基线实现：远端 `test_cached_second_check_rejects_payload_changed_after_final_inventory` 在首稿真实复现了库存校验后损坏仍被提交，不能删除此必要坏包拦截或放宽该测试。必要验证不后移到回执之后；取消、失败回退、库存写入负责人及覆盖确认不变。不增加跨操作缓存、跳过错误开关或个人目录回退。

与在途 `agent/render-map-1-dual-pack` 的已知重叠：`app/imported_resource_archive.py`、`experiments/qt_quick/client_imported_resources.py` 及相关导入测试；本任务不改资源格式和生成产物。整合时须保留对方新增包内容/角色检查，将下载绑定接到最终保存入口，再联合跑结构包/独立路线包的导入、自动启用、兼容版本、损坏与取消回退，并对最终集成源码重新实测。

测试配置为 Windows Python 3.13，`PYTHONPATH=experiments/qt_quick;app;tests;tools;tools/maps`、`QT_QPA_PLATFORM=offscreen`，临时目录全部置于任务的隔离证据目录。单独的 `py -3.13` 无法在本轮受限执行环境找到解释器，使用同一个实际 `python`。最初只配置任务给出的两条路径会缺 fixture/exporter，因此补齐现有测试依赖路径；不把这些收集失败计作测试通过。合并运行的长批次曾中断且没有完成结果，最终按可观测进度的分组命令记录，不从中断批次凑数。

旧模块相关检查 `python -m unittest tests.test_author_package_management tests.test_map_package_upgrade test_client_import_upgrade_parity test_client_package_manager` 共 67 项：66 通过，1 失败。失败为 `ManagedAuthorTests.test_confirmation_receipt_swap_and_junction_target_fail_closed`；在独立未修改基线运行同一用例，复现完全相同的 `ImportRejected not raised`。这是旧作者包管理对 Windows reparse seam 的历史失败，本轮不放宽断言、不改无关删除模块，也不把该组称为通过。

额外探查 `tests.test_imported_native_admission_scope`、`tests.test_imported_native_base` 时，两套历史 fixture 均在 `setUpClass` 的现行原布局导出中因 `KeyError: displayName` 失败，实际执行 0 项；未修改基线复现相同的两个错误。保留该证据缺口，不改资源准入来适配旧 fixture；当前原布局的生产接缝另以 `tests.test_original_qt_resource_flow` 和四模式真实包覆盖。JSON 回归最初误把 NaN 预期码写作 `json-invalid`，已按独立语义预期修正为准确的 `json-nonfinite`，没有放宽为泛异常。

已完成的定向分组：归档 32 项（29 通过、3 环境跳过）；包/事务/库存/读取 67 项（61 通过、6 环境跳过）；Qt 资源服务/导入/激活/启动/语义复用/兼容路线 51 项全部通过；JSON 最后增量的读取器 11 项（8 通过、3 环境跳过）。分组有重叠，不能相加成独立总数。旧 8.3 别名/符号链接的环境跳过不代表相关 Windows 能力验收。

实测及最终定向结果在完成后追加。这里不声称 GUI、游戏、其他电脑或安装验收已通过。

2026-11-16 续查：首稿远端 26 项检查中 25 项成功，`joint (packaging-imported-display)` 因既有作者选择坏包回归失败。本机最小实际入口先复现 `ImportRejected not raised`；恢复 `_prove_inputs` 为基线实现后，原失败用例、作者身份核对期间改写及新增跨包改写均拒绝，`python -u -m unittest -v test_client_imported_author_selection test_client_imported_semantic_reuse` 19 项全部通过（100.623 秒）。此前“未见剩余产品问题”不能覆盖这次远端发现，已按实际反例收回该删减；既有失败测试和回退断言不改。

2026-11-16 续查还核实上轮后台进程已不存在，状态记录停在首轮候选导入的 2/8 包，没有终态结果。原始日志、已成功的基线导入/加载以及中断候选均保留；中断不计为通过或完整耗时。接续测量复用已完成的基线样本，其余在当前轮次观察到结束，并记录样本跨日及未隔绝其他负载的限制。
