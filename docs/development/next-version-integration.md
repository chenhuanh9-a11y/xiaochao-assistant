# 下一版云端集成检查点

本分支是开发集成检查点，不是发布候选、已打包或实机验收结论。基线固定为
`1f3983bd0ac999671f31adc2f8df58cfe262a4d0`，不改动 `main` 或用户安装目录。

## 已纳入的固定源码

按以下顺序 cherry-pick，三次均无文本冲突：

1. 性能、真实进度和游戏启动绑定：`9c4dab293ccc61b5e230c66fd30f8d192530ed54`。
2. 可选备份与卸载资料向导：`bf062657d991d978d3ec4a2fb49f2ea7533d6316`。
3. 升级/卸载 watcher 生命周期接线：`abf3486c4d48a4df010e3d77eebdd41994981162`。

代码检查点为 `a154ea6d385dbb4b8c1dd47e4837dc27be1f3537`。性能固定提交与原开发
`dc2a203` 的树完全一致；卸载接线固定提交与原开发 `d977083` 的树完全一致。
两条完成分支没有改动同一个文件，但 watcher 生命周期是跨文件依赖，不能拆开打包。

## 本检查点执行结果

Linux 云端、Python 3.11、PySide6 6.11.1，Qt offscreen/software；使用 fixture 和临时目录，
没有执行 Windows 安装/卸载、注册登录项、真实游戏或操作用户数据。
以下联合命令在上述代码检查点完成 **179 项，全部通过，52.292 秒**：

```sh
PYTHONPATH=app:tests:tools:experiments/qt_quick QT_QPA_PLATFORM=offscreen \
QT_QUICK_BACKEND=software python -m unittest -v \
  test_game_startup test_game_startup_control test_game_startup_service \
  test_qt_startup_state test_qt_startup_splash test_uninstall_data_wizard \
  test_qt_installer_directory test_qt_installer_reuse_guard test_qt_installer_projection \
  test_client_startup_splash test_client_lazy_pages test_client_performance \
  test_client_package_progress test_client_shutdown test_client_composition \
  test_client_package_manager test_client_info test_client_season_preparation \
  test_client_route_plaza
```

实际解释器为云端已有 `.venv-qt-joint/bin/python`。测试输出有 Fontconfig 缓存目录警告，
不影响本次断言；它不是 Windows GUI 像素验收。`git diff --check` 通过。

## 尚未纳入与交叉检查

- 资源兼容/瘦身：等待最终固定提交与复核。与当前分支共同修改 `client.py`、
  `ClientResources.qml`、`build_qt_client.py`、`qt-game-watcher.spec`；需同时保留
  启动阶段回调、真实进度、资源自检和 watcher 构建合同。
- 贴合证据与回放：等待负责人最终修复和固定提交。预期交叉为 `client.py`、
  `diagnostic_schema.py`，以及资源分支的 `client_live_runtime.py`。
- Tk 退役：仍是独立待验分支。测试删除必须只限已退役入口，保留或迁移共享服务和
  Qt 最终消费者断言；与其他分支的 CI/文档/共享测试改动逐项核对。

在本检查点，`qt-game-watcher.spec` 和安装器事件已接线，但生产构建尚未调用该 spec，
installer-only 重打包也尚未验证 watcher 独立库存。两项由资源构建分支完成后才能称
完整源码集成；不能以当前 179 项通过宣称 watcher 已进入 Setup。

后续顺序：先审固定资源提交并合并构建接缝，再审最终贴合提交，最后纳入 Tk 退役并重跑
受影响的联合检查。每次冲突后检查实际调用链而非仅接受文本合并。最终远端同一 commit
仍需 Windows 编译、Setup 独立安装/升级/卸载、普通账号登录/UAC、真实 GUI 和游戏验收。
