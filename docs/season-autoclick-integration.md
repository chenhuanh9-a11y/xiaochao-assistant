# 赛季候选与连点窗口空档修复整合

本阶段在独立分支 `integrate/season-autoclick`、独立工作树进行。基线为已冻结赛季候选 `3002f820983842249347da8a59c7311fe2823329`；不修改该冻结工作树，也不纳入另一个 `feat/season-provider-isolation` 分支的新 provider 准备代码。

将本地独立修复 `2166a69a1552d7e53da1906b0ba40fef6b1a1224` 原样 cherry-pick 为 `8d892059c95ee71e4e582d598f0dbb915a224cb8`，无冲突。源码 tree 为 `5443fae73cd9651c404a1198d92dabe569aa1650`。两个文件与来源提交逐文件 diff 为空；#94 的 coordinator/runtime/diagnostics 三个文件与冻结候选逐文件 diff 为空，未覆盖或丢失其修复。

## 行为与用户数据边界

游戏窗口目录暂时缺失、但协调器仍保留绑定时，连点暂停实际输入而保留已开启意图；目录恢复且身份相符后，仍通过前台窗口/进程/快捷键等原门禁，并要求触发键释放后重新按住。明确换绑定或进程身份变化仍撤销本次意图，不自动继续。

仅修改 `client_chest_auto_tap.py` 与 `test_client_chest_intent.py`；没有变更设置文件格式、迁移、保存路径、地图文件、安装目录或资源锁。测试使用独立临时 JSON/合成窗口和输入替身，未操作真实用户配置、游戏、快捷键或发送实际按键。未知设置字段、重启恢复、暂停不重新保存、文件错误及按键释放边界由对应连点测试检查。

## 验证方式

Python 3.12、PySide6 6.11.1，`QT_QPA_PLATFORM=offscreen`、`QT_QUICK_BACKEND=software`、`PYTHONPATH=app:tests:tools:experiments/qt_quick`。每个模块在独立进程运行 `python -B -m unittest -v <module>`，保留全部失败和跳过输出。

覆盖赛季选择/目录/图库/资源/会话、设置可访问入口、图库操作/布局/事务刷新、旧包恢复/依赖/收据、赛季准备/上下文失败、#94 诊断、协调器、图库计数、控件动作/稳定性、连点意图/底层服务/Qt 适配、控件可见性和窗口反馈。原始日志在忽略目录 `work/integration-evidence/regressions-autoclick-integration/`。最终共 **25 模块、280 项全部通过，无跳过**。两个变更 Python 文件 AST 和 `git diff --check` 通过。

未上传、推送、合并、打包、安装或发布。未做 Windows 原生窗口/快捷键、真实游戏或 Setup 验收；本轮测试不能作为这些阶段已通过的证据。S1 仍无实际资源，继续失败关闭，不混入 S0 地图。
