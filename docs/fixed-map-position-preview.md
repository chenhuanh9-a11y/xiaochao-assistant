# 定点地图位置离线预览

`tools/preview_fixed_map_position.py` 是位置编辑组件入口，不启动完整助手或游戏监测。传入一张有权使用的真实参考图、独立的生产资源根和独立可写状态目录：

```powershell
python -B tools/preview_fixed_map_position.py --resource-root RESOURCE_ROOT --reference-image REFERENCE.png --state-root PRIVATE_STATE
```

控制窗口在前台时显示图片。点击“编辑位置”后可连续拖动；松手不结束编辑，点击“完成”调用原控制器保存 `sheetPosition`。关闭后用同一状态目录启动，可检查恢复的位置。切换至其他程序会隐藏预览并取消未完成的编辑，重新编辑即可继续；未完成的拖动不写入设置。

工具使用产品原有的 `move_sheet`、`ReferencePositionEdit`、`ReferencePointerMailbox`、原生 `LayeredOverlay`、`finish_reference_sheet_move` 和原子设置写入。焦点检查保留原实现。组件输入只包含传入的实际图片，未调用完整的地图识别、作者配准和游戏显示链；正常游戏小抄显示保持关闭，没有伪造地图身份或贴合授权。

不随代码分发图片、地图数据或用户配置。默认不创建连点器、快捷键监听器或截屏线程。自动检查替换原生窗口显示、前台和控件 I/O；不代替用户实际拖动、原生可见窗口与重开体验的验收。
