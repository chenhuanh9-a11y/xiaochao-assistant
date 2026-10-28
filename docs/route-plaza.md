# 路线广场与账号

「路线广场」页列出玩家分享的噩梦路线，下载后作为只读图库出现在「自定义地图」里，可直接「在游戏中使用」，或复制一份再改。账号和广场都用安卓端的同一套服务（`https://api.xingchenmap.cn`），安卓端的账号可以直接登录。

## 账号

| 操作 | 接口 |
| --- | --- |
| 登录（用户名或邮箱） | `POST /v1/auth/login` |
| 注册（用户名、邮箱、密码；返回一次性恢复码） | `POST /v1/auth/register` |
| 用恢复码重设密码（所有设备退出登录） | `POST /v1/auth/password-recovery` |
| 刷新登录（访问令牌 15 分钟，刷新令牌 30 天，每次轮换） | `POST /v1/auth/refresh` |
| 退出登录 | `POST /v1/auth/logout` |

- 规则与安卓端一致：用户名 3–32 位英文小写、数字或下划线，密码 6–128 位。
- 访问令牌过期前 30 秒或收到 401 时自动刷新一次；刷新失败、账号被停用或封禁时清除本机登录。
- 密码从不保存。Windows 上只把刷新令牌和用户名用 DPAPI（当前 Windows 用户）加密存到设置目录的 `account-session.bin`；其他系统不落盘，关掉软件就要重新登录。
- 设置目录的 `account-installation-id.txt` 是随机安装编号，随登录请求发送，服务器用它做注册配额和设备封禁，与安卓端相同。
- 令牌只发给 `api.xingchenmap.cn`，请求不跟随重定向。`XINGTU_ACCOUNT_API_URL` 只能指向本机测试服务器。

## 广场

- 列表：`GET /v1/community/map-galleries?mode=nightmare&content_kind=nightmare-routes&limit=50[&query=…]`，不用登录，只显示已通过审核的路线。
- 下载：`POST /v1/community/map-galleries/{版本ID}/packages/nightmare/download`，要登录。返回的阿里云 OSS 签名链接（10 分钟有效）不带令牌直接下载，必须是 HTTPS 的 `*.aliyuncs.com`。
- 校验：字节数与 SHA-256 必须和服务器记录一致；ZIP 里只能有 `map-gallery.json`，并且 `schemaVersion` 3、`contentKind` `nightmare-routes`、`workId`、`packageId`、`revision` 都要对得上。
- 同一作品的新版本替换旧的那份图库（ID 不变，游戏内的选择不受影响）。图库可以删除，不能改名或编辑；复制、导出得到的是自己的普通图库。

暂不支持投稿、举报和「我的投稿」。

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
- 用安卓端 72 层的全部 1026 个房间中心和 388 个楼梯端点做过检查：没有丢点；958 个落进房间，58 个落在走廊（两边的房间划分不完全一样）。

## 验证范围

`tests/test_route_plaza.py` 用本机假服务器覆盖了登录、注册、找回密码、令牌刷新与失效、列表、下载校验、篡改拒绝和坐标换算。没有连线上服务器登录或下载过（线上广场目前还没有路线），也没有在 Windows 上测过 DPAPI 和界面。
