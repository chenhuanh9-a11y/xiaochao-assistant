# P5 旧格式停用与最终验收清单

状态以最终整合后的同一远端提交、版本和协议为准。本清单记录应提交的证据；当前所有最终整合、Windows 安装和实机项均未验收。

## 旧格式退役与资源保全

- [x] 新资源包必须由真实 `manifest.json`、组件类型和完整性/依赖校验接纳；扩展名只用于检查包类型，不代替内容校验。依据：`app/imported_resource_archive.py`、`app/imported_resource_packages.py`、`app/resource_package_types.py`；相关回归：`test_resource_package_types`、`test_imported_resource_archive`、`test_imported_resource_packages`。
- [x] 旧 `map-package.json` 包及未知旧 schema 在实际归档入口明确拒绝；旧 ZIP 传输名不授予新包类型。拒绝前不发布内容。依据：`app/imported_resource_archive.py`；回归：`test_imported_resource_archive`。
- [x] 资源页对旧格式显示“旧图包格式已停用，请获取新版资源包；已有文件已保留。”选中的源文件字节保留。依据：`experiments/qt_quick/client_imported_resources.py`；回归：`test_client_imported_resources`。
- [x] 保留旧资源收据及旧安装资源；旧 pending 仍触发历史事务保护/恢复，不自动认领进新版 catalog。依据及回归：`test_legacy_receipt_startup_retention`、`test_resource_reset_legacy_protocol`、`experiments/qt_quick/client_resource_startup.py`。
- [ ] 以四种旧 S0/S1 固定包及旧作者格式真实样本逐一走生产导入/下载接纳入口，核拒绝原因、原 ZIP/文件/收据/catalog 字节前后不变；样本来源、哈希与结果待最终整合记录。
- [ ] 错后缀、未知后缀、合法新版专属后缀、旧/无类型下载 URL 的 ZIP 传输名、错误 schema、错误依赖和损坏内容均通过真实服务反例；新版合法 ZIP 内部传输不因 `.zip` 扩展名被误拒。
- [ ] 失败恢复完成后实际读取并消费已启用新版资源；旧原件和旧 pending 仍保留且不被自动导入/认领。

## 最终整合后必须记录

- [ ] 最终远端 commit、程序版本、资源协议及完整性记录彼此绑定；说明检查对应的准确 commit。
- [ ] Setup 字节数与 SHA256，并证明其由同一最终远端 commit 构建；本任务不构建正式发布包。
- [ ] 干净无资源启动：资源缺失时仍能正常进入程序，旧安装树未作为 fallback。
- [ ] 四种组合（S0 困难、S0 噩梦、S1 困难、S1 噩梦）的真实资源导入、模式可用性与程序消费；标明真实资源来源和完整性。
- [ ] 同一程序消费单独更新的第二版资源，记录版本切换及实际消费证据。
- [ ] 升级、回退、取消和中断恢复均保留个人/旧资源数据，恢复后实际消费正确版本；仅目录仍存在不算通过。
- [ ] Windows 冻结包、真实 GUI、游戏、正式安装与发布分别记录证据和状态。未执行项标为“未验收”，不能由合成夹具、离屏 GUI、旧 CI 或旧架构 Setup 成功替代。

## 本子任务记录

- 范围：地图资源、排查与帮助；相邻模块为安装与更新、设置与主界面。未改识别逻辑、相似组或显示授权规则。
- 整合关系：`app/star_map_import.py`、`app/star_map_download.py`、`experiments/qt_quick/client_imported_resources.py`、`client_resource_startup.py` 与 #133/P2、#138 的 `codex/pr133-p3-s1-unify-20261111`、#133/P4 `agent/pr133-p4-1-progressive-display` 及第 1 个 P5 子任务有交叠；最终整合时逐项复核同一入口、拒绝语义和事务保护，不覆盖其他副本的工作。
- 代码/临时归档测试只能证明对应软件接缝；真实四模式资源、Windows 冻结包、安装、真实 GUI、游戏与正式发布仍待最终整合和 huan 本机验收。
- 本轮环境检查：核心资源测试命令执行 68 项后因系统 Python 缺 NumPy 导入错误（其他启动项已运行）；类型测试未能加载。客户端下载、升级、资源 UI 和启动 Qt 模块因缺 PySide6 未能启动。该环境没有 pip；上述均记为未验证，不算通过。
