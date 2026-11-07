# 结构预处理缓存：字节预算与长期合成检查

基线为 PR #101 `642c472f5c1ee45735dff4ce83f20d8dc8764de0`。本轮仅修改两个静态预处理缓存的插入/计量，不改识别、贴合公式、结果提交、资源身份或游戏/系统设置。未引入第三方项目代码、组件或依赖。

## 实现范围

- `fast_structure_alignment._prepared` 保留原4项上限；`idvb0059_steady._cache` 保留原6项上限。两者各新增512 MiB retained ndarray预算。
- 计入缓存强持有的 reference 四种 mask，以及 prepared 自身数组。按底层 backing owner 去重，视图即使只见少量像素仍计其保留的大底层数组；同一 mask 的别名和同缓存跨条目共享不会重复计费。
- 预算只在冷插入时、原锁内计算，正常热命中完全不增加遍历或像素操作。冷加载/计算仍在锁外，不扩大串行计算区。
- 按 LRU 淘汰旧项，但最新单项即使独自超预算仍保留，避免大图每次开图都重建。保证的是 `retainedArrayBytes <= max(512 MiB, 最新单项字节)`，另保持4/6项上限；不是硬内存拒绝策略。
- 冷插入显式 `move_to_end`，覆盖并发同 key 冷加载后第二次完成插入的次序。移除缓存引用不清空/修改已交给求解者的数组，不使在途调用失效。
- 保留原 reference identity/stat键和强引用，防止 id 重用误配地图/楼层。资源替换仍须新 reference；本轮不引入按文件变化自动失效的新语义。
- 两模块提供 `prepared_memory_snapshot()`，输出计数和预算，不输出路径、地图信息或像素。它不是后台采样器，也没有新增 UI/遥测上传。

预算在新产品完成后执行，不限制解压/计算期间的瞬时分配或阻止超大单项分配。预算不包含 Python metadata/墙线字典、分配器开销、临时求解工作、其他缓存持有者或 GPU。对未知外部 buffer owner仅计可见 ndarray，不能声称测全任意外国内存；生产 loader 的标准 NumPy-owned arrays 和已知 buffer/view路径是本轮覆盖范围。静态 reference 不允许原地替换底层数组继续冒充同资源，这是原缓存合同而非新增许可。

## 持有者及仍未处理的热点

只将各缓存字节相加会重复计算共享数组。A/B脚本另对两个缓存做整体底层去重，但依然不是全进程占用。

| 持有者 | 已有/本轮边界 | 重要限制 |
| --- | --- | --- |
| SharedReferenceProvider | 原6项，未改 | native legacy分支绕过此LRU；重新构造provider才切资源代际 |
| AuthorStructureResourceStore | 原4项，未改 | 与typed reference共享数组；不加载未用clean edge/distance；没有字节预算 |
| fast prepared | 4项 + 本轮512MiB软预算 | distance约2HW + reference mask + chain/ray；保留最新超额项 |
| steady prepared | 6项 + 本轮512MiB软预算 | edge/k3/k5约3HW、float32 distance4HW、int32坐标8E，加reference；保留最新超额项 |
| DifficultNativeContext._legacy_cache | 未改 | legacy全集可能强持有产品数组，不随provider LRU释放 |
| EngineFactories.invalidate | 未改 | 清引擎集合，不清这两个全局prepared缓存；旧代际最多留到容量/预算淘汰 |
| nightmare_candidate._fast_reference_masks | 未改 | 小图直接留原mask，大图另留resized；另一个未受本轮限制的持有者 |
| CommonCoordinateFilter | 原完成+pending共3池，未改 | pending按阶段保存，避免取消后重复建设；单池仍可很大，不能套“超额删除当前job”破坏恢复能力 |

CommonCoordinateFilter完成池主要数组约 `(4N+3)*共同画布面积`，还有chain/ray。仓内索引的噩梦B3全15候选池仅这些数组约345.2MiB；pending另含原mask、canvas、edge、合并/prepare中间量。后续应对冷完成池/旧pending单独设计淘汰，同时保留当前构建，不能宣称两个prepared预算已经解决所有结构内存。

## 为什么采用512 MiB，而不是初版256 MiB

仓内 `s1-private-assets/s1_entrance_coordinate_index.json` 含176个登记层，其中60个属于已退役difficulty2，当前噩梦/困难是116层。只读取尺寸/坐标，没有解码真实图包。

标准uint8四mask、uint8 edge/k3/k5、float32 distance，以及最多每像素一个int32 xy点，steady保守数组上界为19×HW。单地图4层最大231,807,600 B（221.1 MiB），但只保留该工作集还不够：现有缓存允许跨地图6项，识别候选扫描也需要它。

初版256 MiB在六个2048²独立资源反复使用时，将原本热的30次访问变为30次冷准备，单次中位数约18.27ms，而基线为0次冷准备。虽然最近3层仍热，不能把该方案称为不伤害贴合性能。**256 MiB默认已被否决，不是最终产品。** 原型中的低峰值及清空后RSS反升均保留在[预算决策JSON](evidence/structure-cache-budget-decision-20261107.json)，不作为最终收益。

最终512 MiB：当前每模式最大的6个登记层的保守数组上界约496.8 MiB，混合当前两模式的6个最大不同登记层也只有530,100,000 B（505.54 MiB）。同一标准资源代际内可保留既有6项。六个2048²反例重跑恢复0次冷准备；新增测试同时锁住此工作集和索引上界。

该推导不包括metadata，不覆盖S0、未来图包、同层多个旧资源代际，或异常大的外部底层owner。fast的chain/ray也不受steady公式约束。较大工作集仍可能触发淘汰；最新单项超额会保留，避免当前图不断重建。不能承诺任何未来资源的六项热命中永远不变。

尺寸原始计算：[index-size JSON](evidence/structure-cache-index-size-20261107.json)。

## 最终512 MiB A/B结果

同一脚本、基线/候选各全新进程，512²、2048²、3072²合成结构各3对，顺序交替。每次先构造12个独立资源对象，再反复使用最后3个，3000轮，合计54,000次热访问。全部Python socket/DNS入口禁止联网，没有真实游戏输入、QML或可见像素端点。静态参考/预处理内容指纹不一致0，热miss为0。

| 合成尺寸 | 基线/候选 两缓存去重数组字节 | steady条数 | 基线/候选采样峰值RSS中位数 |
| --- | ---: | --- | ---: |
| 512² | 20,490,816 / 20,490,816 B | 6 / 6 | 82,870,272 / 82,890,752 B |
| 2048² | 328,884,288 / 328,884,288 B | 6 / 6 | 472,018,944 / 472,358,912 B |
| 3072² | 740,262,976 / 518,674,688 B | 6 / 4 | 960,401,408 / 734,027,776 B |

fast始终保留4项。3072²单层面积超出当前S1每个登记层，专用于异常大工作集压力，不是典型用户图包。该场景少留211.3 MiB底层数组；正常512²/2048²未减少原热缓存，也没有实质内存收益，不混淆两者。

热路径p50三次中位数512²为0.023995→0.023574ms，2048²为0.023475→0.023905ms，3072²为0.023735→0.023805ms。3072²冷准备p50为57.625→58.622ms。共享云主机且另有低频合成长跑，因此不据这些微小差异保证无性能倒退或提升。代码与测试保证热命中不做新增计量；冷插入增加有界数组列表扫描。六大图反例避免了实质的重复准备回归。

最终三组清空后RSS三次中位数依次为66,682,880→58,834,944、265,527,296→81,313,792、95,162,368→57,540,608 B。它们不是所有运行必然下降的证明，分配器保留与系统归还时机不由LRU直接决定；原型清空后反升反例仍在预算决策记录中。存活数组、RSS、峰值和清空后数值必须分开。采样可能漏瞬时峰值。

完整原始数据和每次p50/p95/max见[A/B JSON](evidence/structure-cache-ab-20261107.json)。p95用nearest-rank；脚本延迟记录用固定大小deque，长跑只保留最新窗口，不能冒称全程分位数。

## 回归与长期观察

新14项反例覆盖：view/负stride/stride-trick/Python buffer底层去重，跨条目共享，字节与条数共同上限，最新超大项保持热命中，旧消费者存活，资源对象替换，非法文件不驱逐好项，引用释放，正常4/6项工作集、六个大图跨地图热缓存、登记尺寸上界，并发冷插入/快照，诊断只含计数。

扩展联合184项：174通过、10项Windows平台跳过，失败0、错误0、联网尝试0。包含既有贴合、姿态先验、两模式adapter、留样、provider、QImage所有权和完整Runtime→QML材质授权回归。离屏QML正确像素回归通过，但不是本轮结构缓存的真游戏端到端验收；退出时既有Fontconfig/QML null-context告警仍可见，不声称控制台无警告。

同一集合的170个既有用例在未改基线重跑：160通过、10跳过，无失败/错误；候选另增14项。

[回归JSON](evidence/structure-cache-regression-20261107.json)保存模块、计数及跳过原因。

最终512 MiB产品代码另跑600.096秒：512²合成结构、10ms间隔，每250次替换一个资源对象，58,356次热访问、245次冷准备。热miss、数组指纹不一致、联网尝试均0；最后fast/steady仍为4/6项，去重数组20,490,816 B，结束清空后均0项，已交付的最后结果在清空后仍通过指纹核对。

RSS采样峰值87,146,496 B，清空后61,227,008 B。113个采样中，最后半段RSS在85,073,920–87,146,496 B之间，仍有2,072,576 B（约1.98 MiB）变化，未完成原因归因。缓存数组有界不等于全进程零增长，不能声称不存在泄漏。测试测量队列本身有固定上限，长跑延迟只统计最新4096次热访问（p50 0.116ms、p95 0.245ms），不当作全程/游戏分位数。早期测试并行和10ms节奏会改变调度，不能与无间隔A/B微小延迟直接比较。

[10分钟原始JSON](evidence/structure-cache-soak-20261107.json)包含实际Python 3.12.14、NumPy 2.2.6、OpenCV 4.12.0版本、逐样本字节、产品与测量脚本的Git blob SHA；运行前后逐一核验未变化。仅Linux云端合成资源缓存，未包含真实Windows游戏/截图/求解端到端，也不替代30/60分钟验收。

## 复现与后续验收

```
PYTHONPATH=app:tests:experiments/qt_quick QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
python -m unittest -q test_structure_cache_memory test_pose_prior_contract \
 test_s1_fit_diagnostics test_idvb0059_runtime test_idvb0059_retained_display \
 test_client_idvb0059_adapter test_marker_occluded_structure test_marker_implicit_domain \
 test_client_providers test_client_image_memory test_client_aligned_transform
python tools/measure_structure_cache_memory.py --source-root <checkout> --size 2048 --cycles 3000
python tools/measure_structure_cache_memory.py --size 512 --seconds 600 --cycles 1 --churn-every 250 --interval-ms 10
```

全量回归未运行。没有改Windows工作流或替代现有发布门槛。最终集成后应重跑新增模块及相关两模式回归，并在最终Windows包、真实同资源上做30/60分钟及100次开图：记录Working Set、Private Bytes、GPU、句柄/线程、缓存字节/冷重建、正确可见像素、未显示和错层真值。500/300ms目标仍待真实端到端证据。源码同步不代表合并、安装或发布。
