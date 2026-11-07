# Qt 运行内存分析与冗余拷贝优化

基线：集成 PR #101 `d3e741c75d5d34ccc07fbce0965724069d763efc`。
本轮只改变 LiveImages 像素所有权/交付，不改识别、贴合、资源身份、授权 token、缓存容量或用户系统设置。

## 确认的问题与修正

`experiments/qt_quick/client_coordinator.py:LiveImages` 原来在判断相同材质之前复制整幅 RGBA，且每次 `requestImage` 再深拷贝一次。1080p RGBA 一份 8,294,400 字节，两位消费者会再持有两份完整副本。

修正：

1. 输入尺寸先检查，再做类型/连续内存转换，非法超大 strided 输入不会为了拒绝它先分配大数组。
2. 同一已授权材质范围仍逐字节比较；相同才返回原 URL，省去那次临时深拷贝。不是仅凭路径、名字或哈希重用。
3. 新像素仍复制一次到 Qt 自有存储，不把 NumPy 的生命周期交给渲染线程。
4. 请求返回独立 `QImage` 值，底层像素使用 Qt implicit sharing。消费者写入时分离；清空/淘汰 provider 不会使仍被消费者使用的快照悬空。没有直接返回可变的同一个 Python 对象。
5. 保留原有锁、暂存接受/拒绝、引用图保护、最多二/三幅图、clear/revoke、逐帧授权和确认协议。昂贵的新像素复制仍在锁外，发布者仍为原 UI 所有者。

依据：Qt [QImage](https://doc.qt.io/qt-6/qimage.html) 与 [implicit sharing](https://doc.qt.io/qt-6/implicit-sharing.html) 的值语义。没有引入第三方项目代码或新依赖。实际验证库为 PySide6/Qt 6.11.1。

## 缓存与引用审查

以下是源码容量/所有权审计，不是用户 Windows 进程的实测。共享 ndarray 被多个缓存持有，不能将每行相加当成 RSS。

| 所有者 | 已有边界 | 字节/生命周期边界 |
| --- | --- | --- |
| LiveImages | 通常2项，暂存3项；单幅≤20,000,000 RGBA像素 | provider 自有像素理论≤160/240 MB（十进制）；NumPy 输入、Qt 消费者、GPU纹理另计。本次不降低上限，以免破坏双缓冲/暂存 |
| SharedReferenceProvider | 6项 LRU | 无总字节预算；clean/validation/known/unknown 共2–4个 uint8 HW 数组，另有元数据/墙线。工厂资源失效清引擎，但其他求解缓存可仍持有旧 reference |
| AuthorStructureResourceStore | 4项 | 不加载不用的 clean edge/distance；其余 mask 尚无总字节预算 |
| DifficultNativeContext legacy 引用 | 已声明 legacy key 集合 | `_legacy_cache` 持有 typed reference；包装与底层产品共享数组，不是每份都复制 |
| BaseMapDisplay | 6项且96 MiB | 计 clean mask + routeSource；资源代际失效清缓存。S1NativeDisplay 和 S1AuthorDisplays 各有6项/96 MiB像素预算，但不是全进程总预算 |
| RetainedDisplayCache | 8项/96 MiB | 另有256 MiB工作预算，变换临时输出与消费者像素不全计入持有量 |
| fast_structure_alignment prepared | 4项 | 保留 reference 防 id 复用，另含 uint16 distance（2HW）和链/射线，尚无字节上限 |
| idvb0059_steady prepared | 6项 | 引用源 reference，edge/k3/k5约3HW、float distance约4HW、点集约8E；仅项数不是内存硬上限 |
| S1 CommonCoordinateFilter | 完成+待做共3个 pool | 同坐标候选 mask/edge/float field 约6×候选数×画布面积，还有并集/链；可成为较大热点，需真实画布和候选数测量后决定预算 |
| 旧 route_image / 透明作者图 | 分别6项 / 默认2项 | 依图像尺寸，不是全局字节预算；保留热图是为了重开/换层，不在缺真实资源数据时贸然缩小 |
| Runtime 最后截图 | 当前观察/求解所有者及最后一幅地图帧 | 末帧故意供楼层框选编辑器使用，结束本局不等同于零截图内存；600次合成开关图检查未累计整段采集历史 |

结论：已有缓存大多限制项数，部分显示缓存还有字节限制；它们不是统一的进程内存上限。本轮先去掉无益副本。未修改识别缓存淘汰策略、未调用强制工作集回收或反复 gc 来掩盖增长。测试中的 gc 仅用于分清 Python 循环垃圾与仍在持有的合成帧。

## 实际 A/B 结果

云 Linux、Python 3.12.14、Qt 6.11.1，共享主机，无游戏/屏幕截图。相同脚本、全新进程顺序交替，每场景3对，1920×1080合成 RGBA：同材质重复交付各10,000次，持续换像素各1,000次，暂存接受/拒绝各1,000次。合计72,000次，累计循环时间约193.7秒。全部 Python socket/DNS 路径禁用，像素不匹配0、联网尝试0。

下表为三次运行统计的中位数；p95每次使用 nearest-rank。不是把3个 p95再次叫做总体 p95。

| 场景 | 基线/候选 p50 ms | 基线/候选 p95 ms | 基线/候选采样峰值 RSS |
| --- | ---: | ---: | ---: |
| 相同材质重复交付 | 3.704 / 0.215 | 6.494 / 0.332 | 118575104 / 94294016 B |
| 像素持续改变 | 5.791 / 1.119 | 7.547 / 1.643 | 126611456 / 109862912 B |
| 暂存接受/拒绝 | 7.355 / 4.446 | 9.442 / 7.259 | 126853120 / 110379008 B |

脚本/原始数据的 `reopen` 场景名仅指重复交付，没有调用真实关图。真实关图会 `clear_aligned`，随后仍需必需的像素入库复制，不能将该场景时间称为按 G 重开图耗时。

另加“每轮交付后 clear_aligned，再创建下一幅图”的3对A/B，各1,000轮，共6,000轮。基线/候选 p50 3.691/0.329ms，p95 5.538/0.568ms，采样峰值RSS 118771712/102309888 B。数据见 [clear/reopen JSON](evidence/runtime-memory-clear-reopen-20261107.json)。这仍然只计图片provider接口。

provider 自有像素峰值前后相同：8,294,400 / 16,588,800 / 24,883,200 B，说明没有靠提前丢弃图像换取结果。消费者修改、NumPy源修改、清空后快照存活、并发读/换图、3000轮暂存生命周期均另测。

重要反例：同材质重复交付场景清空后的 RSS 中位数基线68,931,584 B，候选77,701,120 B，候选约高8.4 MiB；其他两场景清空后接近。分配器/Qt保留内存不保证立即归还操作系统。因此只结论为本地图片交付的冗余复制、延迟和采样峰值改善，不声称运行内存所有时刻下降或证明没有泄漏。RSS 每100次采样，可能漏掉瞬时峰值；getrusage 高水位与 psutil RSS 口径另列，不混合。

原始数据：[A/B JSON](evidence/runtime-memory-ab-20261107.json)。脚本 `tools/measure_qt_image_memory.py` 可用 `--source-root` 指向基线/候选，默认5000次；`--interval-ms` 可用于带间隔稳态运行。计时端点是 provider publish/request 完成，不含输入、采集、求解、QML绘制或正确可见像素。

## 分钟级稳态观察

候选额外运行3,000轮，每轮间隔40ms，实际123.11秒，像素不匹配0、联网尝试0，provider始终持有一幅8,294,400 B图像，结束后0项。采样峰值RSS 94,236,672 B，清空后77,639,680 B。最后半段RSS变化包含测量列表/采样记录增长，不能据此推断产品泄漏；详见 [paced JSON](evidence/runtime-memory-paced-candidate-20261107.json)。此前一次更长命令的运行会话中断且未留下完整报告，没有计入结果。分钟级合成稳定不等同于30/60分钟真实游戏稳定。

## 测试与待验

新测试 `test_client_image_memory`：真实QImage/COW、源内存回收、超大输入早拒、并发快照、3000轮暂存，以及真实 LiveRuntime 配合假采集/假求解，两模式各300次开关图。后者逐次核对新结果、旧token撤销、身份不重识别，结束后清空姿态；最后一幅供楼层编辑器使用的帧保留。

首轮联合140项中138通过、2项失败；在未修改的 d3e741c 干净基线分别复现：
- `test_client_aligned_transform.AlignedTransformProbe.test_actual_runtime_pointer_to_qml_keeps_material_and_rejects_stale_floor_scope`：frameId仍1:1而测试期望1:2。
- `test_client_live_controls.CoordinatorLiveTests.test_destroyed_and_reused_hwnd_cancels_then_rebinds_unique_new_process`：session.stop 调用0次而期望1次。

没有删除或放宽这些现有断言，不将部分通过说成全绿。真实QML的独立材质移动/像素边界用例通过，但失败的实际Runtime→QML用例仍是集成缺口。

Windows发布前仍需同最终安装包、同资源分别做30/60分钟与100次真实开图，记录 Working Set、Private Bytes、GPU、句柄/线程、子进程、p50/p95/max、未显示与错层真值。缓存预算调整应先量化真实资源的独立数组总字节与命中/重建时间，再保留当前地图和邻层热缓存；不能仅凭本次合成接口改善认定500/300ms端到端目标已达到。

最终聚焦91项通过（8.092秒），命令：

```
PYTHONPATH=app:tests:experiments/qt_quick QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
python -m unittest -q test_client_image_memory test_client_material_identity \
 test_client_coordinator test_client_floor_region test_client_providers \
 test_client_aligned_transform.AlignedTransformProbe.test_real_qml_moves_bounds_with_same_material_url_and_no_reload
```

扩大回归315项为269通过、46失败。随后把308个既有用例在基线/候选分别运行，两者同为262通过、46失败、0错误，失败ID集合完全相同，涉及退役 difficulty2 的旧启动预期；完整对照在 [regression JSON](evidence/runtime-memory-regression-20261107.json)。新增7项独立通过。另有上述原始两个基线失败，不掩盖集成缺口。py_compile 与 diff --check 通过。
