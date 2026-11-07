# 共享源码

本目录保留识别、贴合、地图与资源索引、窗口捕获、诊断、地图包事务及工具包无关的状态协议。

当前客户端入口为 [`tools/qt_client_entry.py`](../tools/qt_client_entry.py)，Qt 页面及客户端服务在 [`experiments/qt_quick/`](../experiments/qt_quick/)。运行与资源前提见 [仓库 README](../README.md)。旧 Tk 页面及入口已移除，不再要求 Tkinter。

共享模块存在不代表对应资源已公开或能力已完成实机验收。
