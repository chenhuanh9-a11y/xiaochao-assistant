# 原生相似地图组资源

`tools/maps/build_native_similarity_groups.py` 从已绑定版本与楼层的共享结构 NPZ 生成相似组资源。它不重新提取游戏包体，也不使用作者攻略图。输入的 alpha footprint 只表示渲染结构占据的像素，不表示语义墙体、可行走区域或当前可见区域。

以同一原生坐标空间的三成员一楼为例：

```powershell
python tools/maps/build_native_similarity_groups.py <完整资源根目录> --output-root <新的空候选目录> --member nightmare-04 --member nightmare-12 --member nightmare-19 --profile zhan10-speed --floor 1 --variant variant-01 --group-id native-04-12-19
```

生成器要求各成员有明确的原生世界边界、等比例像素尺度及单位渲染器转换；不能用作者画布尺寸推算结构坐标。输出记录成员的 mapId、楼层、结构版本、源版本、依赖文件内容和原生到组画布转换，包含所有无序成员对的正向差异掩码。其他楼层必须独立生成，不能继承一楼变换。

候选目录包含选中成员的参考文件和精简 shared index，方便独立回读；**不能把整个目录覆盖到完整资源包**。CLI 输出的 `completeBaseIntegration` 明确区分：

- `newFiles`：要加入匹配完整基线的分组索引和差异 NPZ。
- `referenceDependencies`：必须在完整基线中核对内容与版本的原生参考。
- `preserveBaseFiles`：必须保留的完整 shared index。
- `candidateOnlyFiles`：候选自含依赖，不作为完整包替换内容。

接入前复制完整基线到独立测试目录，核对参考依赖后只加入 `newFiles`，再从实际生产资源根加载。应回读新增文件，并确认完整 shared index 与基线逐字节一致、未参加本组的地图仍然注册。生成候选和文件回读只证明资源构建，不证明生产识别、组状态切换、可见贴合或实机体验已通过。

游戏资源、实际玩家截图和诊断日志留在受控的本地证据目录；源码仓库仅保存生成器和合成 fixture 测试。
