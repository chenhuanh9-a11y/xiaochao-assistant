# 阶段一打包与玩家资料

huan 已决定安装包自包含，并把 .NET 运行时打进包里。草案里的“初期不裁剪”仍是阶段一基线：不开启 trimming。本阶段不制作安装器，也不发布。

计划中的 Windows 发布命令（现在不要在 Linux 上把它当成已测量的安装包）：

```powershell
dotnet publish csharp/src/StarMap.App/StarMap.App.csproj -c Release -r win-x64 --self-contained true -p:PublishTrimmed=false -p:PublishSingleFile=false
```

软件交付以后只用 Setup。上面的 publish 目录只是运行时候选，不能拿它的字节数代替完整安装包，也不能拿它和现版 Setup 比出“大约三分之一”。那句话仍然未验证。

玩家资料以后按复制、校验、再切换。永不自动删除旧原件。失败时保留旧引用。卸载默认保留资料；清理必须单独确认。两套程序并排时不共用一个会被对方覆盖的组件目录，也不要两套同时写同一份资料。阶段一没有迁移代码，只有这条约束。

体积和冷启动的记录步骤在 `tools/phase1/measure_publish.py`。没有 `XINGTU_PUBLISH_DIR` 或 `XINGTU_PYTHON_SETUP` 时，字节数和冷启动都打印 `unknown`。冷启动要记到窗口里握手文字可见；没有这个终点就保持未知，不要把进程创建时间写成开屏成绩。首贴约 500ms、锁图后重开约 300ms 仍是仓库目标，不是本骨架的实测。
