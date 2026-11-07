# 启动、进度与内存：下一版实现和验收

基线：PR #99 的 `1f3983bd0ac999671f31adc2f8df58cfe262a4d0`。这不是已发布包的实测。

## 本次实现

- 原生开屏线程显示真实阶段文本和循环滑块，未知工作量不编造百分比。运行时钩子至 Python 入口、单实例、资源检查、服务创建、窗口创建、首帧提交分别记录；钩子之前的 bootloader、Windows UAC 等待不在内部计时范围。
- 原生开屏在主窗口首次 `frameSwapped` 后关闭，不再把构造完成当作窗口可用。异常、重启/重复实例和退出保留原有清理链。首帧提交不是用户已看到正确像素的证明。
- 首页资源准备、本地导入/扫描/删除/恢复及测试资料准备使用不定进度动画；在线下载仅在有效有限字节总量已知时显示真实比例，到达下载总量后转入独立校验/导入阶段。不因下载到 100% 声称安装完成。
- 首次只构造首页及资源页。图库、设置、回放、帮助、识别、自定义、在线下载、路线广场按首次访问异步加载，首次访问后保留实例、草稿和滚动。资源选择器及确认所属页持续存在，后台服务独立于页面继续运行。
- 不延迟八个游戏相关顶层窗口，避免把开屏改善转移成首次按键延迟。不改识别、贴合算法和图包校验边界。
- QML 警告内存限定最近 128 条；自有进程每 10 秒采样，限定最近 120 条，保存会话最大值，退出停止计时器。不落盘、不截图、不扫描其他进程。诊断导出通过原有隐私白名单附上快照。
- 内存指标为 Working Set、Windows Private Bytes、线程及句柄；不把 Linux VMS 当 Private Bytes，不推断 GPU 或子进程用量。采样失败明确 unknown/unavailable，不写假零。

## 已执行证据

Linux 云端，Python 3.11、PySide6 6.11.1，offscreen/software。原生窗口、采集和引擎使用明确 fixture；不代表 Windows、图包、游戏或 Setup 已通过。

基线聚焦回归 29 项通过。修改后联合运行以下 12 个模块，101 项通过（49.330 秒）：

```
PYTHONPATH=app:tests:experiments/qt_quick QT_QPA_PLATFORM=offscreen \
QT_QUICK_BACKEND=software python -m unittest -q \
 test_client_season_preparation test_client_route_plaza test_client_info \
 test_client_shutdown test_client_composition test_client_package_manager \
 test_client_lazy_pages test_client_performance test_client_startup_splash \
 test_qt_startup_splash test_qt_startup_state test_client_package_progress
```

该次懒加载测试覆盖首次进入设置及返回实例保留，不包含随后新增的全八页访问测试。
云环境存在 Fontconfig cache 警告和资源页销毁时空 context 警告；没有将它们算作实机通过。

全网络阻断后再次交替 8 对全新进程 fixture 构造测量（只含 QtClient 构造，不含 Python imports/UAC/bootloader）：

| 指标 | 基线中位数 | 候选中位数 | 解释 |
| --- | ---: | ---: | --- |
| QtClient 构造 | 941.11 ms | 798.47 ms | 本云环境约下降 15.2%，不是 Windows 冷启动结论 |
| 初始化稳定后本进程 RSS | 212936704 B | 196282368 B | 约减少 15.9 MiB，不是游戏期间稳态/峰值 |

窗口 QObject 子树数量从 4230 降为 1860（不是 GPU 内存或全部 Qt 内部对象）。重复次数不足以得出可靠 p95；不作上线承诺。数据见 `evidence/startup-performance-composition-20261107.json`，采集先于游戏 watcher 接线。

全八页补测发现路线广场首次访问会读取公开列表，因此测试必须以空列表 fixture 替代，并在测试进程阻断 socket connect/DNS。原未隔离补测未取到可用结果，不计入通过数；不以线上访问替代离线断言。修正后的 2 项离线懒加载测试通过（1.920 秒），包含逐一加载全部八页且无 QML 警告/网络尝试、首次实例保留。产品真实联网行为没有因此修改。

## Windows 验收 SOP（待执行）

1. 最终远端 commit 的干净 checkout 构建；记录 Setup 字节数/SHA256、版本、构建指纹、资源锁和实际内置范围。A/B 安装采用相同配置、图包、机器、显示倍率和安全软件环境，不使用用户原目录做试验。
2. 记录至少 20 次温启动和 10 次重启机器后的冷启动，分别列 n/p50/p95/max、失败次数。外部计时从双击到原生开屏、主窗第一正确可见像素、当前资源可用；UAC 人工等待单列。内部阶段可定位耗时，不能替代像素端点。
3. 对缺包、有效包、坏包、S0/S1 双模式、慢盘、离线、重复点击、启动中退出分别验证阶段、提示和不重入。无总量用循环动画；真实下载最后一字节、校验失败、取消及回滚不得显示完成或卡住。
4. 普通/大窗口、100%/150%/200% 缩放逐一首次进入八页，检查键鼠、草稿、焦点、滚动、文件选择器、取消/失败后的原页面，以及关闭后迟到结果不复活页面。
5. 同一配置记录启动空闲、资源页、每个图/层切换、开关图 100 次、对局 30/60 分钟和退出。用 Windows Working Set/Private Bytes、句柄/线程、GPU及独立 watcher 分别采样。报告稳态、峰值、结束后回落及变化斜率；不因 RSS 不立刻回落判泄漏，也不以释放系统工作集掩盖泄漏。
6. 正确性优先：图像身份、首贴/重开延迟、错层/未显示次数都要与基线核对，耗时提升不能抵消错误或首次使用回退。只有在可重复测量中收益成立且未回退才接受优化。

## 参考

- Qt Loader：<https://doc.qt.io/qt-6/qml-qtquick-loader.html>
- Qt Quick performance：<https://doc.qt.io/qt-6/qtquick-performance.html>
- Qt frameSwapped：<https://doc.qt.io/qt-6/qquickwindow.html#frameSwapped>
- psutil process memory：<https://psutil.readthedocs.io/en/latest/#psutil.Process.memory_info>
