# 爱吃醋作者名称兼容

此前误标为“夜莺”的噩梦作者图统一显示为“爱吃醋”。仅修正显示名称，不创建新作者身份。

- 稳定资源包 ID 仍为 `nightmare-nightingale`，运行时 profile 仍为 `nightingale`。
- 安装路径仍为 `_internal/assets/authors/nightingale` 与 `_internal/references/author-packs/nightingale`。
- 保留既有设置和导入收据的身份，不迁移或复制图片目录。
- 资源包的 `label`、catalog 的 `displayName`/`authorName`/变体显示名改为“爱吃醋”；不改变配准矩阵、结构版本、楼层和几何状态。
- 现有 `6b341879` 客户端仍可安装同一 ID 的新资源包，但作者控件的硬编码名称可能显示旧名。本提交修正该控件的公共名称映射。

验证：主模块只更改一处显示字符串；稳定 ID、识别、贴合、导入逻辑与基线不变。四个离线资源包另以真实 importer 和生产资源消费者在独立目录验证，未进行 GUI 点击或游戏验收。
