# 路线广场与账号

「路线广场」页列出玩家分享的噩梦路线，下载后作为只读图库出现在「自定义图库」里，可直接「在游戏中使用」，或复制一份再改。账号和广场都用安卓端的同一套服务（`https://api.xingchenmap.cn`），安卓端的账号可以直接登录。

业务来源是尚未合并的 [PR #23](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/23)（分支快照 `0d3c394`）：复用 `account_service.py`、`route_plaza.py` 和既有本机 HTTP 测试，将图库来源元数据适配到当前 `main` 的保存/恢复保护。没有合并整条旧分支，也没有将 Tk 页面装进 Qt 进程。

Qt 入口：侧栏「路线广场」→「账号登录」，下载后点击「去使用」或「自定义图库」。Qt 使用既有 `AppButton`、`ClientPalette`、持久 `StackLayout`、导航器和文本输入快捷键保护。Tk 主入口保持原样，Tk 自定义地图页能识别并删除下载的只读图库，复制/导出仍得到可编辑的个人图库。

## 账号

| 操作 | 接口 |
| --- | --- |
| 登录（用户名或邮箱） | `POST /v1/auth/login` |
| 注册（用户名、邮箱、密码；返回一次性恢复码） | `POST /v1/auth/register` |
| 用恢复码重设密码（所有设备退出登录） | `POST /v1/auth/password-recovery` |
| 刷新登录（访问令牌 15 分钟，刷新令牌 30 天，每次轮换） | `POST /v1/auth/refresh` |
| 退出登录 | `POST /v1/auth/logout` |

- 规则与安卓端一致：用户名 3–32 位英文小写、数字或下划线，密码 6–128 位。
- 访问令牌过期前 30 秒或收到 401 时自动刷新一次；刷新失败、账号被停用或封禁时清除本机登录。
- 密码从不保存。Windows 上只把刷新令牌和用户名用 DPAPI（当前 Windows 用户）加密存到设置目录的 `account-session.bin`；其他系统不落盘，关掉软件就要重新登录。
- 设置目录的 `account-installation-id.txt` 是随机安装编号，随登录请求发送，服务器用它做注册配额和设备封禁，与安卓端相同。
- 令牌只发给 `api.xingchenmap.cn`，请求不跟随重定向。`XINGTU_ACCOUNT_API_URL` 只能指向本机测试服务器。

## 广场

- 列表：`GET /v1/community/map-galleries?mode=nightmare&content_kind=nightmare-routes&limit=50[&query=…]`，不用登录，只显示已通过审核的路线。
- 下载：`POST /v1/community/map-galleries/{版本ID}/packages/nightmare/download`，要登录。返回的阿里云 OSS 签名链接（10 分钟有效）不带令牌直接下载，必须是 HTTPS 的 `*.aliyuncs.com`；每次重定向在发出下一跳请求前重新校验同一白名单，完成后仍校验最终地址。
- 校验：字节数与 SHA-256 必须和服务器记录一致；ZIP 里只能有 `map-gallery.json`，并且 `schemaVersion` 3、`contentKind` `nightmare-routes`、`workId`、`packageId`、`revision` 都要对得上。
- 同一作品的新版本替换旧的那份图库（ID 不变，游戏内的选择不受影响）。图库可以删除，不能改名或编辑；复制、导出得到的是自己的普通图库。

暂不支持投稿、举报和「我的投稿」。

## 坐标换算

安卓的路线点存的是导航网格的格子编号。网格画在旧版模板底图上，和本软件自定义地图资源包用的是同一套图：72 层的尺寸、网格全部一致，每格 2 像素，`cols = ceil(宽 / 2)`。

```
图片 x = min(宽 - 1, (cell % cols) * 2 + 1)
图片 y = min(高 - 1, (cell // cols) * 2 + 1)
原生坐标 = matrixImageToNative · (x, y, 1)
```

- 换算后按本软件的道路网落点：房间点取所在房间（落在走廊上就成为必经点），必经点取最近的道路格；保留颜色（缺省用 `lineColor`）和「新起点」。
- 连线不沿用安卓的 `segments`，在本软件的道路网上重新规划，跟手动加点一样。
- 模板 17–19 等本软件没有的图会跳过；不在道路上的点会丢弃。下载完成后提示跳过了多少。
- PR #23 原记录曾对 72 层的 1026 个房间中心、388 个楼梯端点做过检查；本次没有取得或重跑这些真实资源，不能将该历史记录算作本次验收。当前测试只使用临时合成图包。

## 异步与状态接缝

- `RoutePlazaService` 持有唯一账号实例；账号调用和公开列表在 Qt 线程池执行，通过 queued signal 回到 UI 线程。业务服务和账号文件不进入设置快照、诊断或日志；密码提交后立即从输入框清除，恢复码单独保存在当前弹窗内，关闭即清除。
- 首次进入才读取广场，无启动联网。路线以版本 ID 增量协调；相同刷新没有模型变更。页面常驻，切页保留搜索、列表滚动和焦点。隐藏时服务继续收取任务，列表模型/页面快照到可见时再同步。
- 未登录下载会打开账号弹窗，成功后只续接同一次弹窗、同一版本对象的下载意图。取消弹窗或离开页面后，已接受的账号调用可能完成并更新公共登录状态，但不会重开弹窗或触发旧下载。注册成功须保存一次性恢复码后再续接下载。
- 下载、校验、换算、保存进入 `CustomGalleryService` 已有单写者队列；图库编辑/导入/删除互斥，不创建第二个可变 `GalleryStore`。保存前检查图库磁盘摘要；成功才发布 committed snapshot、`galleriesChanged` 和已下载徽标，继续走已有显示缓存失效链路。
- 资源刷新在下载期间标记撤销并保留一次待刷新；完成网络调用后不会安装旧资源结果。取消下载也在阶段间生效，无法中断正在阻塞的 HTTP 调用；账号请求超时 20 秒、对象下载超时 30 秒，沿用原服务，没有字节级进度。
- 退出将新服务加入原 `close_writers`：撤销待导入下载和弹窗意图，等待已接受的网络任务/原子保存排空后退出；直接 shutdown 同样先撤销。已完成的原子提交不会被伪报为取消。换层/结束本局不改变广场浏览或账号身份；使用图库仍由原运行时校验当前模式和显示授权。
- 同一作品更新保留图库 ID；保存失败恢复旧内存状态与 revision。只读下载图库仍写入磁盘核验回执，复制/普通导出不传播广场来源。

线程和弹窗生命周期参考 Qt 官方 [QThreadPool](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html)、[Thread Signals 示例](https://doc.qt.io/qtforpython-6.10/examples/example_widgets_thread_signals.html)、[Popup.closePolicy](https://doc.qt.io/qt-6/qml-qtquick-controls-popup.html#closePolicy-prop)；采用项目既有 queued signal、阶段取消和关闭排空方式，无新依赖或流程工具。

## 验证范围

`tests/test_route_plaza.py` 为 PR #23 的本机假服务器测试：登录/注册/找回密码、令牌刷新与失效、列表、包校验、篡改拒绝、模板坐标换算。新增 `test_client_route_plaza.py` 覆盖真实 Qt 服务/磁盘图库、搜索刷新、只读/复制/删除、版本替换、错误、取消重试、资源撤销、关闭弹窗后迟到登录、隐藏页面任务与 UI 线程结果收取。

完整 `QtClient` 在独立进程离屏启动，实际侧栏点击 → 广场 → 账号弹窗 → 登录 → 下载中请求退出 → 排空并正常退出，QML 警告为零；验证了账号输入的快捷键保护和启动不请求广场。窗口/捕获/引擎由既有 fixture 替代，HTTP 只连接本机假服务器。另有页面级 Qt 鼠标/键盘事件覆盖搜索、登录和下载后导航。没有操作游戏。

截图来自该完整客户端冒烟，都是合成地图/本机假服务器，账号表单为空；不含真实账号、令牌、游戏图或作者路线素材。截图使用 Linux 字体回退，不能代表 Windows 字体/DPI 验收：

- [普通窗口](images/qt-route-plaza.png)
- [账号登录](images/qt-account-login.png)
- [最小窗口](images/qt-route-plaza-small.png)

未验证：线上账号/广场、Windows DPAPI、真实游戏显示、实机焦点/DPI/全局输入、安装包与安装。业务仍仅支持噩梦路线，困难模式、投稿、举报、「我的投稿」不在现有服务能力内；没有用占位数据补这些能力。没有构建、安装、合并或发布 Setup。

本次环境为 Linux / Python 3.13.5 / PySide6-Essentials 6.11.1，依赖来自
`experiments/qt_quick/requirements-client.txt`。原环境没有 Tk；为运行原有无界面 Tk 测试，
把 Debian `python3.13-tk 3.13.5-2+deb13u5`（核对包 SHA256）解压在 `/tmp/xiaochao-tk-runtime`，
仅通过测试 `PYTHONPATH` 引入，没有创建 Tk 窗口或更改项目依赖。

实际针对性命令（167 项：166 通过，1 项因未提供 `QT_RESOURCE_PRIVATE_ZIP` 跳过）：

```bash
PYTHONPATH=/tmp/xiaochao-tk-runtime/usr/lib/python3.13:/tmp/xiaochao-tk-runtime/usr/lib/python3.13/lib-dynload:app:tests:tools:experiments/qt_quick \
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
/tmp/xiaochao-qt-venv/bin/python -m unittest -q \
  test_client_route_plaza test_route_plaza test_custom_maps test_custom_map_editor \
  test_client_custom_gallery test_client_composition test_client_custom_display \
  test_client_gallery_export_dialog test_client_downloads test_client_shutdown \
  test_client_coordinator test_client_resources test_client_import_upgrade_parity \
  test_client_recapture_composition
```

生成截图时在同一命令额外设置 `XINGTU_QT_PLAZA_EVIDENCE_DIR=docs/images`。
`client.py --self-check` 返回 `ready: true, qml: true, tkLoaded: false, nativeInputStarted: false`。

额外执行 `test_custom_route_integration` 的 10 项中有 4 项失败（旧 `Subject` fixture 缺
`_display_notice_context`），`test_client_theme` 的 1 项失败（旧测试找不到搜索控件）。
两者分别在未修改的默认分支 `c06a3f2` 干净 worktree 用相同命令重现，未改无关测试或扩大业务范围。
这些失败不计入上面的通过组，也没有宣称全库测试通过。


### PR #87 生命周期补测

- 刷新令牌使用独立单飞锁；HTTP 和等待刷新不持有 Qt snapshot 读取的状态锁。登录、注册、恢复、注销和退出按请求代际撤销旧结果，旧刷新不能覆盖新会话。
- 注册成功的恢复码仅保存在本次进程的账号内存中，不写 session vault、设置快照或日志。取消/Esc/切页只关闭展示，不视作“我已保存”；迟到成功不重开弹窗、不续接旧下载。广场出现“领取恢复码”入口，重开后可保存；点击“我已保存”、注销、账号变化或退出清除。退出后不能再领取。
- `test_client_account_lifecycle` 使用真实 QML 弹窗、鼠标/Esc、Qt 工作池与门控本机 transport；覆盖关闭后实际按钮重开、再次关闭、明确确认、换账号、shutdown 和慢刷新期间取消/切页/退出。原 head 的 5 条回归均失败，其中 UI 等待约 2.01 秒；修复后同路径通过。条件控件改变后先等待 Qt Quick layout polish，再按屏幕坐标点击。
- `test_account_concurrency` 使用门控 mock transport 检验会话与请求交错；`test_route_plaza_redirect` 使用真实 urllib 跳转处理及内存 transport，证明被禁止的下一跳不会发出。未连外站；对象下载没有账号 token，不能据此声称 token 泄漏。
- 新增 `qt-route-plaza-windows.yml` 只读运行 candidate、原 head `3188d301`、main `c06a3f2` 三组相关套件。main 尚无广场模块，只比较原有相关套件；新增回归只在 candidate 运行。没有构建、安装或发布 Setup，1.0.6 冻结不变。

补测的 Linux 环境为 Python 3.12 / PySide6 6.11.1，`PYTHONPATH=app:tests:tools:experiments/qt_quick`，`QT_QPA_PLATFORM=offscreen`，`QT_QUICK_BACKEND=software`。沿用上面的相关 14 个模块，另外运行 `test_account_concurrency test_client_account_lifecycle test_route_plaza_redirect`。原 head 167 项（166 通过、1 跳过），main 对应 143 项（142 通过、1 跳过）。跳过项需要私有资源 ZIP；本次没有读取该私有数据。Windows CI、Windows DPAPI 和实机游戏/焦点/DPI/安装验收仍应分别记录，不能以 Linux 离屏结果替代。
