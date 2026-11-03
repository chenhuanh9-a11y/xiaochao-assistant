# 路线广场与账号

「路线广场」页列出玩家分享的噩梦路线，下载后作为只读图库出现在「自定义图库」里，可直接「在游戏中使用」，或复制一份再改。账号和广场都用安卓端的同一套服务（`https://api.xingchenmap.cn`），安卓端的账号可以直接登录。

业务来源是尚未合并的 [PR #23](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/23)（分支快照 `0d3c394`）：复用 `account_service.py`、`route_plaza.py` 和既有本机 HTTP 测试，将图库来源元数据适配到当前 `main` 的保存/恢复保护。没有合并整条旧分支，也没有将 Tk 页面装进 Qt 进程。

Qt 入口：侧栏「路线广场」→「账号登录」，下载后点击「去使用」或「自定义图库」。Qt 使用既有 `AppButton`、`ClientPalette`、持久 `StackLayout`、导航器和文本输入快捷键保护。Tk 主入口保持原样，Tk 自定义地图页能识别并删除下载的只读图库，复制/导出仍得到可编辑的个人图库。

## 账号

| 操作 | 接口 |
| --- | --- |
| 登录（用户名或邮箱） | `POST /v1/auth/login` |
| 注册（用户名、邮箱、密码；返回账号恢复码） | `POST /v1/auth/register` |
| 用账号恢复码重设密码 | `POST /v1/auth/password-recovery` |
| 刷新登录（访问令牌 15 分钟，刷新令牌 30 天，每次轮换） | `POST /v1/auth/refresh` |
| 退出登录 | `POST /v1/auth/logout` |

- 规则与安卓端一致：用户名 3–32 位英文小写、数字或下划线，密码 6–128 位。
- 访问令牌过期前 30 秒或收到 401 时自动刷新一次；刷新失败、账号被停用或封禁时清除本机登录。
- 密码从不保存。Windows 上只把刷新令牌和用户名用 DPAPI（当前 Windows 用户）加密存到设置目录的 `account-session.bin`；其他系统不落盘，关掉软件就要重新登录。
- 设置目录的 `account-installation-id.txt` 是随机安装编号，随登录请求发送，服务器用它做注册配额和设备封禁，与安卓端相同。
- 令牌只发给 `api.xingchenmap.cn`，请求不跟随重定向。`XINGTU_ACCOUNT_API_URL` 只能指向本机测试服务器。
- 注册后请保存恢复码；确认保存后，本机不再显示。密码找回请求继续发送 `preserve_recovery_code=True`；服务端是否支持恢复码重复使用、如何撤销其他设备会话尚未确认，客户端不据此作出承诺。本机 HTTP fixture 只验证请求字段和本地状态，不能证明服务端行为。

## 广场

- 列表：`GET /v1/community/map-galleries?mode=nightmare&content_kind=nightmare-routes&limit=50[&query=…]`，不用登录，只显示已通过审核的路线。
- 下载：`POST /v1/community/map-galleries/{版本ID}/packages/nightmare/download`，要登录。返回的阿里云 OSS 签名链接（10 分钟有效）不带令牌直接下载，必须是 HTTPS 的 `*.aliyuncs.com`；每次重定向在发出下一跳请求前重新校验同一白名单，完成后仍校验最终地址。
- 校验：字节数与 SHA-256 必须和服务器记录一致；ZIP 里只能有 `map-gallery.json`，并且 `schemaVersion` 3、`contentKind` `nightmare-routes`、`workId`、`packageId`、`revision` 都要对得上。
- 同一作品的新版本替换旧的那份图库（ID 不变，游戏内的选择不受影响）。图库可以删除，不能改名或编辑；复制、导出得到的是自己的普通图库。

Qt 同时提供「投稿」和「我的投稿」，支持选择个人图库、说明、上传进度、停止/重试、修订、提交已有包审核和撤回。仅支持噩梦路线，困难模式明确提示不支持。服务端没有图库 DELETE 接口，因此没有删除投稿按钮；举报尚未接入 Qt。

## 投稿协议与来源

PC [PR #23](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/23) 的 `0d3c39481926b8267b2964cbd5363a97e6456569` 只有账号、浏览和下载，没有投稿代码。投稿以安卓仓库只读快照 `7e1aba96312390c534b6cc400dfc2570a9601571` 中的已提交协议文件为依据：

- `mobile/app/src/main/assets/community-page.js` 是语音社区；图库/路线投稿实际在 `map-gallery-page.js`，原生桥为 `CommunityBridge.kt`，HTTP 方法和上传头规则在 `AccountSyncClient.kt`。
- `GalleryPackageStore.kt`、`GalleryPackageArchive.kt`、`NightmareRoutePlan.kt`、`nightmare-route-model.js` 定义导出身份、schema 3、导航签名、房间/途经点、楼梯和方向游程。规划使用导航实际单位与楼梯距离；不声称两端选出的连线像素完全相同。
- `account-sync-server/app/map_galleries.py` 及 `tests/test_nightmare_route_galleries.py`、`test_map_gallery_contract.py`、`test_route_data_packages.py` 明确服务器状态转换与限制。这里只读源码，未运行/部署安卓服务端，也未使用线上账号。

| 操作（都复用 AccountService Bearer 鉴权、刷新与安装 ID） | 接口/行为 |
| --- | --- |
| 我的投稿 | `GET /v1/community/my/map-galleries`，返回最近 100 个版本，Qt 只取个人 `nightmare-routes` |
| 创建草稿 | `POST /v1/community/map-galleries`，`title`、`description`、`content_kind=nightmare-routes` |
| 修订 | `PUT /v1/community/map-galleries/{版本ID}`，相同字段；草稿/拒绝原地修改，已上架/撤回/下架创建新的版本 ID，保持 work ID 并复制既有包绑定；审核中不能修改 |
| 上传授权 | `POST /v1/community/map-galleries/{返回的版本ID}/upload-sessions` |
| 上传对象 | 授权返回的 HTTPS OSS URL，固定长度 `PUT`，不附账号 token/cookie，不跟随跳转；只接受 content-type/content-md5 与 x-amz-/x-oss-/x-cos- 签名头 |
| 上传完成 | `POST /v1/community/map-galleries/{版本ID}/upload-sessions/{会话ID}/complete`，`{"etag":"…"}` |
| 提交审核 | `POST /v1/community/map-galleries/{版本ID}/submit`，`{"confirmed":true}`；仅草稿/拒绝且有已完成包 |
| 撤回/下架自己的作品 | `POST /v1/community/map-galleries/{版本ID}/withdraw`，`{"confirmed":true}`；仅审核中/已上架 |

上传授权字段为 `schema_version=3`、`work_id`、`package_id`、`package_revision`、`mode=nightmare`、`map_count`、`image_count=0`、`manifest_bytes`、`object_bytes`、`sha256`、`idempotency_key`、`content_type=application/x-xingtu-gallery`。未绑定包时与安卓一样，用 `xingtu-map-gallery:{workId}:nightmare:{localGalleryId}` 的 Java name UUID（MD5、v3）稳定生成 package ID；已有包沿用 ID 并递增包 revision，不能把作品 revision 当作包 revision。服务端若因其他设备/未完成上传返回 409 的结构化 `package_id/package_revision`，按该明确身份重建包并只重试授权一次，不能猜测下一个 revision。

ZIP 仅含 UTF-8 `map-gallery.json`，使用固定时间戳和 STORE，保证重试字节一致且不会触发安卓解压比率上限。manifest 包含 `schemaVersion=3`、`contentKind=nightmare-routes`、`mode`、`workId/packageId/revision`、标题、作者、`minClientVersion=1.11.2`（更新后的导航版本）、`imageCount=0`、`routeEntryFloor=1` 和 `maps`；每张图包含稳定的 `mapEntryId`、`displayName`、`defaultFloor`、空 `floors` 和 `routePlan`，不上传地图图片或个人图库存储文件。

限制按实际交集执行：标题 2–48 字（安卓桥；服务端 1–80、包 1–60），说明最多 500 字；1–27 张图，单图最多 128 个点，单方案 160 KiB，合计最多 200000 个方向步，每段最多 8 个楼层 part、每 part 最多 24000 个游程数值；manifest 6 MiB，包 64 MiB。服务器还限制账号对象容量 256 MiB、最多 3 个待审核、每天提交 5 次；本机不猜配额剩余量，以服务端反馈为准。上传会话回包及完成回包都核对身份/摘要/大小，完成后才提交审核。

六种状态直接展示为草稿、审核中、已上架、已拒绝、已撤回、已下架；拒绝/下架原因来自 `rejection_reason`。400 未确认、401 登录失效、403 无权/封禁、404 不存在、409 状态或包身份冲突、413 大小/容量、422 字段/包格式、429 频率/配额、503 存储不可用均保留服务器原因。没有发现投稿专属稳定 `error_code` 表；账号封禁仍用既有错误码处理。

安卓曾修复的两个问题分别处理：玩家 `official_source_id: null` 是无官方来源，不能转成字符串 `"null"`；409 不能无条件覆盖所有操作的真实原因。Qt 投稿在保留详细原因的同时提示刷新，下载保持独立错误处理。安卓 `ad669ecdd9c21a103510ce07c6fbb99c90d3e616` 还修正了请求未启动/已排入错误回调的区别，Qt 用唯一 worker 请求代际及 queued result，不借用 WebView 的布尔返回值。

## 投稿坐标与资源契约

上传是下载的反向坐标变换，但连线必须在安卓实际导航图上重新规划，不能直接把 PC 道路的像素线段伪装成安卓 `segments`：

1. 个人图库的原生点经每层 `template_to_native` 的逆矩阵回到旧模板像素，再取每格 2px 的 cell；只容许最多 2px 的网格量化偏差。
2. 房间点按安卓 label 找真实 room ID 和中心，途经点保留道路格，保留颜色/新起点。找不到对应道路或房间就失败，不丢点、不捏造房间、不上传半套地图。
3. 按资源中的实际方向边规划，同层方向游程遵守 1/2/4/8/16/32/64/128；跨层仅用 `verified=true, kind=stairs` 的已核实楼梯。写入资源的真实 `navigationSignature`。

**现有资源缺口：** 旧 PC 编辑资源包没有上述安卓导航和签名，不能凭原生道路图反推。每张支持投稿的地图需要可选 `routeSubmission: {path, sha256}`，引用含 `templateId/navigationSignature/catalogSha256/floors/stairs` 的导航 JSON；文件受资源哈希/路径约束，尺寸必须与模板坐标契约一致。缺少、损坏、重复绑定或坐标不符会明确失败，发生在创建远端草稿之前。现有浏览、编辑、下载不依赖这个可选能力。

沿用 `tools/maps/build_custom_map_package.py`，增加显式 `--submission-navigation <已授权的安卓 nightmare-route-editor 根目录>`，可从同一次已授权资源构建输入生成该契约；不自动扫描安卓目录、不拷贝图片/作者路线/原始游戏数据。原资源验证和 `prepare_qt_editor_base.py` 的文件清单包含该导航文件及哈希。本次没有生成或发布生产地图包，也没有把安卓导航素材提交到 PC 仓库；**实际交付前仍需提供与生产编辑底图对应的已授权导航资源包**。离线测试的导航全为临时合成数据。

## 投稿异步与取消

投稿表单和两张列表常驻；仅增量协调发生变化的记录，进度不重置图库选择/输入/滚动。未登录进入投稿会先引导登录，表单填写后遇到登录失效可续接同一次意图；关闭账号/投稿窗口或切页会撤销该意图，不因迟到登录重新上传。注册的恢复码确认语义沿用原服务。

从图库单写者发布的 committed snapshot 取得个人图库和只读 library，在广场唯一线程池转换、打包、上传；本机图库不发生投稿写入，上传不占图库 busy。开始时在 worker 校验磁盘摘要，阶段间核对图库内容、资源对象/代际、账号身份代际和取消标记。页面隐藏仍收取结果，恢复后才同步列表；标题输入继续受全局快捷键保护。`AccountService.identity_context` 在刷新 token 时保持稳定，换账号/注销/关闭才撤销身份。进度通过 queued signal 收取，UI 不等网络或导航计算。

失败/停止保留一个有界尝试（固定包、幂等键和草稿 ID），表单锁定相同输入供重试；重试先读服务端状态，已提交则直接采用真实结果，已完成上传无需重传。关闭表单放弃本地续接后，仍可从「我的投稿」修订草稿或提交其已绑定包。创建/修订响应丢失导致草稿 ID 未知时，不自动重发非幂等的创建请求，提示先刷新确认。已经接受的 submit/withdraw 以回包为准，不能因请求中途取消而伪报未提交。没有伪造删除或远端上传取消 API，停止可能留下草稿/尚待服务端清理的上传对象。

取消在转换搜索、发送分块及 HTTP 阶段边界生效；正在阻塞的 socket/API 调用须等返回或超时（对象 PUT 30 秒，账号 API 20 秒）。沿用 `close_writers` 和 QThreadPool 排空，退出先拒绝新请求、取消未发阶段，再等已接受任务完成。参考 [Qt QThreadPool 生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html) 和 Python 官方 [http.client](https://docs.python.org/3.13/library/http.client.html)，使用标准库固定长度发送/关闭连接，无新依赖或流程工具。

待确认的线上协议：源码快照与部署版本是否一致，尤其结构化 409、上传会话字段、存储签名头及配额；未知草稿创建结果缺少服务端幂等查询/创建键；未完成上传对象的过期清理策略；未来是否开放删除。`minClientVersion=1.11.2` 与当前导航在真实安卓版本上的互通尚未验证。既有 `preserve_recovery_code` 服务端语义仍未确认，本次没有改它。

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

- `RoutePlazaService` 持有唯一账号实例；账号调用和公开列表在 Qt 线程池执行，通过 queued signal 回到 UI 线程。业务服务和账号文件不进入设置快照、诊断或日志；密码提交后立即从输入框清除。未确认保存的恢复码仅留在当前进程的账号内存，关闭弹窗清除展示，可再次领取；明确确认、换账号或退出后清除。
- 首次进入才读取广场，无启动联网。路线以版本 ID 增量协调；相同刷新没有模型变更。页面常驻，切页保留搜索、列表滚动和焦点。隐藏时服务继续收取任务，列表模型/页面快照到可见时再同步。
- 未登录下载会打开账号弹窗，成功后只续接同一次弹窗、同一版本对象的下载意图。取消弹窗或离开页面后，已接受的账号调用可能完成并更新公共登录状态，但不会重开弹窗或触发旧下载。注册成功须确认保存账号恢复码后再续接下载。
- 授权、下载、校验和坐标换算在广场自己的线程池执行，使用请求开始时的只读 library；慢网络不占图库的 busy 状态，期间仍可选图、编辑和切换启用图库。准备完成后，只有保存进入 `CustomGalleryService` 原有单写者队列，不创建第二个可变 `GalleryStore`。已有短写入未完成时，最多保留一个待保存结果，通过 `pendingWrites` 纳入退出排空；取消和资源刷新在实际写入前再次核对。下载不清空当前个人图库的撤销/重做记录。保存前仍检查图库磁盘摘要；成功才发布 committed snapshot、`galleriesChanged` 和已下载徽标，继续走已有显示缓存失效链路。
- 每次资源刷新立即推进资源代际；图库正忙时仍保留 `_refresh_pending`，随后补刷新。准备结果在 UI 收取与写入前均核对 library 对象和资源代际，完成网络调用后不会安装旧资源结果。取消下载也在阶段间生效，无法中断正在阻塞的 HTTP 调用；账号请求超时 20 秒、对象下载超时 30 秒，沿用原服务，没有字节级进度。
- 退出将新服务加入原 `close_writers`：撤销待导入下载和弹窗意图，等待已接受的网络任务/原子保存排空后退出；直接 shutdown 同样先撤销。已完成的原子提交不会被伪报为取消。换层/结束本局不改变广场浏览或账号身份；使用图库仍由原运行时校验当前模式和显示授权。
- 同一作品更新保留图库 ID；保存失败恢复旧内存状态与 revision。只读下载图库仍写入磁盘核验回执，复制/普通导出不传播广场来源。
- 会话状态锁只保护内存读写；刷新通过独立单飞锁联网，并用请求代际和旧会话身份核对结果。DPAPI 和会话写盘使用另一把锁，按会话 revision 丢弃迟到保存，状态读取不等网络或磁盘；注销/新登录最终磁盘结果不被旧刷新覆盖。401 重试后同样处理封禁码；封禁保留服务端提示，普通登录失效可在当前广场重新登录后续接下载，取消或离开页面不重开弹窗。找回其他账号不清除当前登录；仅与本地登录标识相同才立即退出，用户名/邮箱别名无法本地确认时由后续鉴权反馈失效。
- “有更新”要求版本 ID 不同且 revision 更高；资源重复绑定 templateId 明确报资源缺口。旧客户端可能丢弃 `source` 并把下载图库当作可编辑图库，旧版本再次保存后来源信息也可能丢失。

线程和弹窗生命周期参考 Qt 官方 [QThreadPool](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html)、[Thread Signals 示例](https://doc.qt.io/qtforpython-6.10/examples/example_widgets_thread_signals.html)、[Popup.closePolicy](https://doc.qt.io/qt-6/qml-qtquick-controls-popup.html#closePolicy-prop)；采用项目既有 queued signal、阶段取消和关闭排空方式，无新依赖或流程工具。

## 验证范围

`tests/test_route_plaza.py` 为 PR #23 的本机假服务器测试：登录/注册/找回密码、令牌刷新与失效、列表、包校验、篡改拒绝、模板坐标换算。新增 `test_client_route_plaza.py` 覆盖真实 Qt 服务/磁盘图库、搜索刷新、只读/复制/删除、版本替换、错误、取消重试、资源撤销、关闭弹窗后迟到登录、隐藏页面任务与 UI 线程结果收取。

完整 `QtClient` 在独立进程离屏启动，实际侧栏点击 → 广场 → 账号弹窗 → 登录 → 下载中请求退出 → 排空并正常退出，QML 警告为零；验证了账号输入的快捷键保护和启动不请求广场。窗口/捕获/引擎由既有 fixture 替代，HTTP 只连接本机假服务器。另有页面级 Qt 鼠标/键盘事件覆盖搜索、登录和下载后导航。没有操作游戏。

截图来自该完整客户端冒烟，都是合成地图/本机假服务器，账号表单为空；不含真实账号、令牌、游戏图或作者路线素材。截图使用 Linux 字体回退，不能代表 Windows 字体/DPI 验收：

- [普通窗口](images/qt-route-plaza.png)
- [账号登录](images/qt-account-login.png)
- [最小窗口](images/qt-route-plaza-small.png)

未验证：线上账号/广场、Windows DPAPI、真实游戏显示、实机焦点/DPI/全局输入、安装包与安装。业务仍仅支持噩梦路线；困难模式、举报未接入。投稿资源缺口及线上互通待验见上文，没有用占位数据补生产能力。没有构建、安装、合并或发布 Setup。

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

### Qt 投稿补测（2026-11-04）

基于 PC `origin/main 9d7fa80`，Linux / Python 3.13.5 / PySide6 6.11.1，使用既有 `/tmp/xiaochao-qt-venv`。实际联合命令：

```bash
PYTHONPATH=/tmp/xiaochao-tk-runtime/usr/lib/python3.13:/tmp/xiaochao-tk-runtime/usr/lib/python3.13/lib-dynload:app:tests:tools:tools/maps:experiments/qt_quick \
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
/tmp/xiaochao-qt-venv/bin/python -m unittest -q \
  test_route_submission test_client_route_submission \
  test_client_route_plaza test_route_plaza test_custom_maps test_custom_map_editor \
  test_client_custom_gallery test_client_composition test_client_custom_display \
  test_client_gallery_export_dialog test_client_downloads test_client_shutdown \
  test_client_coordinator test_client_resources test_client_import_upgrade_parity \
  test_client_recapture_composition test_account_concurrency \
  test_client_account_lifecycle test_route_plaza_redirect \
  test_build_custom_map_package test_extend_custom_map_package test_prepare_qt_editor_base
```

结果 **267 项，266 通过、1 跳过**（未提供 `QT_RESOURCE_PRIVATE_ZIP`）。中间一次重叠运行中原有 `test_tutorial_progress_drains_through_actual_window_close_choice` 触发其 8 秒子进程超时；没有放宽超时或更改该用例，最终联合重跑通过，不把中间失败隐去。

- 新增 8 项本机假 HTTP/实际 PUT 测试，检查鉴权隔离、包字节/哈希/manifest、坐标/房间/跨层/新起点、修订返回新 ID、幂等重试、服务端修订号纠正、取消、不跟随跳转、会话篡改、资源缺口、未知创建结果不重复创建。
- 新增 11 项 Qt 测试，包含真实 QML 鼠标/键盘投稿及「我的投稿」/撤回，登录续接与关闭后迟到结果、重复点击、进度、取消重试、资源刷新/图库变更、隐藏页面、已有包直接送审、稳定模型和退出排空。结果信号核对 UI 线程。资源生产/清单测试检查导航契约保留、坐标不符和篡改拒绝。
- 完整 QtClient 在独立进程离屏运行：侧栏 → 登录 → 选择已保存的个人图库 → 打开投稿表单 → 输入保护 → 实际上传中请求退出 → 排空后正常关闭；无启动广场请求、QML 警告为零、退出没有迟到送审、本机图库字节未变化。截图仅在 ignored `work/qt-submit-evidence`，使用合成资源/本机假服务器；未提交图片素材。capture/native/游戏引擎由既有 fixture 替代。
- `client.py --self-check` 返回 `ready=true, qml=true, tkLoaded=false, nativeInputStarted=false`；`git diff --check` 通过。测试有意注入的启动失败提示属于已有负例，不是实际客户端启动验收失败。

未验证线上登录/上传/审核、OSS 实服务、真实安卓导入、生产导航资源包、Windows DPI/焦点/DPAPI/游戏与安装。没有改安卓仓库，没有使用真实用户素材，没有构建、安装、发布或合并。
