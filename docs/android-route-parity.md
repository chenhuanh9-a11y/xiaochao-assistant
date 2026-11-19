# Qt 自定义路线与广场

开发基线：GitHub `main` 的 `cc5100c`。安卓协议与规划模型取自已提交的 `4c3b635a5e95c77f500e7d13224df487cd22e6c4`；安卓活动目录仅作只读输入，未包含其进行中的未提交修复。模型原文件 SHA-256 为 `ceee5e5e0a156ac902c001d0fd9f52ebb4af9fa0c32e70940b5f6b3503fa1219`，Qt 副本逐字节相同。

## 范围与保存

| 范围 | 地图／楼层 | 包格式 | 本机资料 |
| --- | --- | --- | --- |
| 灾厄之女噩梦 `legacy-nightmare` | 原 PC 26／78 | schema 3 | 原 `custom-galleries.json` |
| 灾厄之女困难 `classic-hard` | 安卓 29／58 | schema 4 | `route-galleries/classic-hard/galleries.json` |
| 磷光症困难 `season-s1-hard` | 安卓 14／42，B1–B3 | schema 4 | `route-galleries/season-s1-hard/galleries.json` |
| 磷光症噩梦 `season-s1-nightmare` | 安卓 15／45，B1–B3 | schema 4 | `route-galleries/season-s1-nightmare/galleries.json` |

编辑器和广场共用范围选择；浏览范围不修改对局模式、身份、楼层或结构位姿。旧噩梦的原生规划和旧资料保留。其他范围保存原底图坐标，包内保留安卓网格 cell、房间、颜色、独立起点和导航签名；困难单图上限 512 点，磷光症为 128 点。导入不兼容范围时整体拒绝，避免静默丢掉其他楼层。

已保存的跨范围游戏选项使用 `custom:<范围>~<图库ID>`，旧噩梦仍用原 ID。启动时恢复三套已保存图库的不可变快照，不要求先打开编辑器。浏览另一范围不会撤销原范围已生效的显示；提交、下载和保存的有效性检查仍绑定本次资源与磁盘摘要。

## 规划与异步

`app/scoped_route_library.py` 在调用 worker 内创建短生命周期 [QJSEngine](https://doc.qt.io/qtforpython-6/PySide6/QtQml/QJSEngine.html)，执行 `app/android_route_model.js`。JavaScript 引擎和值不跨线程保存；网格元数据缓存最多两张地图／库。UI 沿用图库单写者和 queued result；关闭、取消、刷新、换范围、账号变化及已关闭讨论分别作废相关结果，已接受保存继续排空。

磷光症沿用安卓已声明的二维门集合策略 `bidirectional-source-door-sets-v1`，并保留跨层签名，不把它称为实战核实楼梯。旧困难未提供已验证跨层连接时会拒绝连续跨层路线；可用独立起点分别编辑楼层，不补造楼梯。

## 游戏显示

`client_custom_display.py` 和 provider 仅接显示链。新范围须匹配 season、mode、map、floor、原始图像路径、矩阵及当前 typed reference 的精确 clean-mask SHA-256；任一缺口拒绝显示。S1 B4 不借用 B1–B3 图片。原识别、定位、分数门和提交授权保持原调用；隐藏像素测试不能证明实际游戏上屏或运行时资源已经匹配。

## 资源准备与打包

地图图片、导航和绘制注册保存在独立资源目录，不提交到 Git。只读导入必须指定活动安卓根和新的输出目录：

```sh
python tools/maps/import_android_route_resources.py --android-root /path/to/identityv-map --output /new/resource-root/assets/android-route-editor
```

导出包含 293 个数据文件（加 manifest 共 294），149996274 字节原文件，覆盖 58 张图／145 层。清单逐项绑定 SHA-256、来源 commit 和规划模型摘要。复制到构建资源根 `assets/android-route-editor/` 后，必须将全目录纳入现有 `xingtu-qt-resource-inventory-v1` 的精确清单；构建器不猜本机素材，生产构建仍需更新并核对现有 source/resource release lock。模型 JS 已纳入 Qt spec 的程序文件，冻结模式从 `_MEIPASS` 读取。

源代码运行会优先读本次隔离资源，其次读源码根明确导入的资源；不会搜索安卓目录或其他旧安装。没有这套独立资源时，新范围显示资源未就绪，旧噩梦仍可继续使用。

本机保留的资源 ZIP：`work/route-parity-20261111/xingtu-qt-route-resources-20261111.zip`，121663816 字节，SHA-256 `9401e51dfdccc354e221f302027a9b942c537eb67271f6c14b79bbd4d8cb78d8`。它是私有本地数据工件，不是 Setup，未上传 GitHub Release。

## 本轮验证（2026-11-11，macOS）

- 定向组合回归 90 项通过（包含窄窗口讨论页鼠标／纯文本和下载作品来源保留）；账号变化的讨论专项 5 项通过。S0/S1 边界、范围选择与准备曾联合检查 46 项通过；该范围与最终组合有交集，不相加为独立测试总数。命令与日志位于 `work/route-parity-20261111/`。
- 293 项实际资源摘要、145 层尺寸和显示声明通过；三个范围各一张实际图的双目标保存文档往返一致，两张 S1 代表图的源门集合跨层可规划。逐项结果在 `real-resource-verification.json`；另用安卓原有 `nightmare-route-preview.js` 的 `validate()` 独立通过三种同层及两种跨层样例，结果在 `android-validator-result.json`。路线包同时包含原生 normalize 要求的 scenario/difficulty/sourceMapId；旧困难另带 planned-route/artworkSignature，保留 160 KiB、二十万步、part/run 上限。不是全地图所有路线的可达性、JVM 或游戏验证。
- 三张实际地图在 offscreen Qt 编辑页保存、显示和截图通过，运行期间页面 QML warnings 为空；截图是 Mac 组件预览，不是 Windows 成品验收。继承旧测试的销毁阶段会输出两条已存在的空 context 属性提示，不能记作运行期无提示的整包结果。安卓原样 JS 在 Qt 编译时另有变量声明静态提示，未据此改算法或隐藏业务错误。
- 原图库导出全集受 SMB 无 hardlink／macOS 无既有原子 no-replace 实现影响；修改前 `main` 的原导出模块也复现同样错误。原日志 `baseline-export.log` 只复核这条旧导出路径，不声称所有失败都完成 Windows 验证。原 Windows 导出安全门未放宽。
- 未使用真实账号执行投稿、评论、举报等写操作；新互动采用本机账号 fixture 和模拟协议响应，原账号、下载与投稿已有 localhost HTTP 回归。Windows DPAPI、原生文件选择、成品启动、真实账号／OSS 往返及游戏显示仍待 Windows 验收。

本轮没有更改版本、构建／安装 Setup、部署服务或发布。GitHub 交付保留功能分支／草稿 PR，需补上 Windows 与资源锁验收后再合并发布。

复现本轮最终定向检查（使用已安装 Qt 客户端依赖的 Python）：

```sh
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software PYTHONPATH=app:tests:experiments/qt_quick python -m unittest test_scoped_routes.ScopedRoutesTests test_client_scoped_routes.ScopedEditorTests test_client_route_discussion.DiscussionTests test_client_route_plaza.PlazaTests test_route_submission test_custom_maps test_client_custom_display test_client_providers test_season_resources -q
```

Windows 使用相同测试目标，`PYTHONPATH` 分隔符改为分号，并单独运行原图库导出全集。测试夹具只使用合成底图／导航和本机 HTTP，不需要真实账号或生产素材。Qt JS 的 `qt.qml.usedbeforedeclared` 分类是原样安卓脚本的编译提示；它不改变测试退出状态。
