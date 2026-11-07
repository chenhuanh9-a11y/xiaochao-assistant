# 赞赏界面

当前入口是 Qt 客户端帮助页的赞赏面板，使用 `experiments/qt_quick/ClientHelp.qml` 与 `ClientInfoService`。仅展示 `assets/support` 内既有图片；不自动付款，不启动浏览器或游戏监听。

旧 Tk `support_window.py` 和独立预览工具已退役。图片及许可保留。Qt 界面/信息服务验证见 `test_client_info.py`、`test_client_composition.py`；离线检查不等于扫码付款或真实 Windows 验收。
