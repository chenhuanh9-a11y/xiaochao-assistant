# S1 离线图库接入

本候选在独立分支以 `cb5bba07d39172897c87c43ae893c080c51d6b2c` 的已审季节边界为基础，合并 main `097897d01bb724e1e9740cde632cd00a611d5907`。构造时使用 `git merge --no-commit --no-ff`，合并没有文本冲突，随后进行联合语义验证；最终源码提交与验证状态以交付回执为准。没有推送、安装或发布。

新季资源由现有 `load_browse_context` 与 Qt 地图资源页消费。按普通（内部 `difficulty2`）、困难、噩梦分别显示带门渲染图和结构图。每张图保留源高度，游戏显示层为 -4、-3、-2、-1 层；首次预览采用源高度4对应的 -1 层。浏览不创建运行 pack，不调用识别引擎，不授予作者选择、导入或 live 能力。S0 入口完全保持旧资源根和 pack，S1 不读 S0 pack、seed、作者或相似组。

## 显式资源合同

资源根中索引位置为 `references/seasons/s1/offline-map-index.json`。所有 asset 路径相对同一资源根，必须在 `assets/seasons/s1/` 内，路径解析后也不能越过此命名空间。索引由生成器提供，应用只读，校验版本、完整清单、唯一身份、难度/模式、源高度/游戏层、文件及 SHA256。

```json
{
  "schema": "xingtu-season-offline-maps-v1",
  "season": "s1",
  "sourceVersion": "2.104.3099014",
  "maps": [{
    "mapId": "105001",
    "sourceLayout": "105001",
    "sourceGroup": 105,
    "sourceDifficulty": 2,
    "mode": "difficulty2",
    "name": "105001",
    "floors": [{
      "sourceHeight": 4,
      "gameFloor": -1,
      "label": "-1层",
      "rendered": {"path": "assets/seasons/s1/difficulty2/105001/B1/rendered.png", "sha256": "64位小写十六进制"},
      "structure": {"path": "assets/seasons/s1/difficulty2/105001/B1/structure.png", "sha256": "64位小写十六进制"},
      "structureNpz": {"path": "assets/seasons/s1/difficulty2/105001/B1/structure.npz", "sha256": "64位小写十六进制"}
    }]
  }]
}
```

上面只是形状示例，真实合同必须声明完整44图，每图源高度1–4各一次；105001–105015、106001–106015（不含106006）、107001–107015。组105/106/107必须分别是 `(2,difficulty2)/(3,difficult)/(4,nightmare)`。渲染图和结构图必需；NPZ可选且仅核文件哈希，不因为存在NPZ就宣称识别几何可用。

内部筛选键为 `source-height-N`，单独保存 `sourceHeight`、`gameFloor` 与标签，不复用S0 `B1/1/2` 标识。真实素材、门语义与结构质量由资源生成验收证明；文件hash验证不能替代这些证明。新图缺少图片或hash错误时整个新季索引失败关闭，不悄悄减少竞争或显示范围。

## 源证据

当前包体 `ComuKeys.GoldRushLevelDifficulty` 为 COMMON=2、HARD=3、MARE=4；`UIGoldRushMap.update_p_hard` 使用 `difficulty+1` 的 `img_hard_3/4/5`，源图片确认普通/困难/噩梦。

当前 `UIBattleGoldRushBigMap` 的按钮数值为源层减startLayer，非负值再加1。`MazeBase.build_postprocess` 的startLayer由入口房间高度减enter_room_floor_offset决定。44图入口源高度1、offset=-4，因此startLayer=5；源高度1/2/3/4对应游戏层-4/-3/-2/-1。按钮顺序是源高度4至1。既不把1当1F，也不把4层强套旧三层。

完整源审计位于本次工作目录的 `thy-reader/source-semantics-review/`，不将原始游戏模块提交到仓库。`floor-audit.json` SHA256为 `668df0ced2f10a511e82e3dcc7b0f29a39aab5f50a76bb46fa0937318fa507c8`；`ComuKeys-difficulty-enum.txt`为 `81f5001397c070ce9e7202d94c04caf5a103ce0d4970083d14f4ebd83a4216be`。

## 读取与验收入口

现有客户端的只读self-check增加显式离线根参数：

```powershell
python -X utf8 -B experiments/qt_quick/client.py --self-check --offline-resource-root "绝对路径/resource-root"
```

该入口通过实际图库loader验证三个模式，再用Pillow真正解码全部唯一图片；输出每模式地图/层数、解码数与live缺口。成功不启动窗口、采集、键盘监听或游戏，不证明实际GUI/游戏识别通过。Qt后台载入、源层筛选、预览读图与迟到结果边界另外由 `test_season_offline_resources` 的临时图片和offscreen QML验证。

live继续未登记：虽已确认游戏层身份，仍缺新季楼层控件检测/点击和正侧入口/坐标的运行合同。没有新增运行模式，也没有为普通复用困难候选。楼层控件、门索引、共享typed geometry及真实输入验收齐全后才能另行启用。

本批NPZ的实际producer是OpenCV **5.0.0**，schema为 `xingtu-source-structure-v1`，保存alpha覆盖、边界候选和门端口；它不是生产typed识别pack。除上述UI/入口接缝外，仍缺typed几何/识别资源适配及对应runtime版本准备。原S0使用的4.12识别特征和缓存不得借给这些5.0离线产物。self-check中的liveGaps枚举为已登记的消费者阻断摘要，不表示其它识别资源合同已经通过。

本轮另在忽略目录 `work/s1-offline-gallery/_internal` 放置完整派生资源供常规source路径消费，并写独立 `.qt-lab-owner`；共705文件、135681328字节，只含S1派生产物和索引。`runtime_paths` / Backend与真实QML消费者验证使用该独立sandbox，不修改S0正式资源根，也不启动游戏或全局键盘监听。全量资源解码自检与实际读取证据在 `work/s1-map-integration/actual-*.log` / `.json`。

## main下载复用兼容性

保留 #96 下载前复用已安装图包的行为。候选原有严格收据合同要求完整 `requiresFiles`，因此把 #96 测试fixture补为普通完整收据。进一步验证发现复用检查原来只核作者payload，基础依赖丢失/同大小损坏可能仍报告installed；现改为同一资源读锁下同时核payload和依赖字节，再复核收据摘要。依赖仍只是兼容约束，不能成为所有权。缺失/损坏继续正常下载/检查导入，不直接报告ready。

定向回归日志与清单保存在忽略目录 `work/s1-map-integration/`。真实新资源读取结果与发布状态应分列；本候选没有发布或覆盖正式安装。

另一个未改动的下载边缘是：UI在实际安装验证之前仍检查手动重试预算，因此预算耗尽后再本地装好的图包，须重新检查在线列表才能走零网络复用。网络transport自身仍有次数上限；本次没有扩大到该行为。
