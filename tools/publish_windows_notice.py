"""Publish the Windows update notice (version, announcement, installer checksum).

Usage (from the repository root):

  python tools/publish_windows_notice.py --installer PATH --notes NOTES.txt
      Preview only: checks everything and shows what would be sent.

  python tools/publish_windows_notice.py --installer PATH --notes NOTES.txt --publish
      Publish interactively: confirm by typing the version, then the admin
      token at a hidden prompt (or from UPDATE_ADMIN_TOKEN).

  python tools/publish_windows_notice.py --installer PATH --notes NOTES.txt --publish --yes
      Publish without prompts, e.g. by an assistant after the installer is
      on the cloud drives. The token must be in UPDATE_ADMIN_TOKEN.

The token is never printed or saved by this script.

Only metadata is sent; the installer itself stays on the cloud drives. The
version comes from version.json, so the app and the notice always agree.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from update_service import is_newer, version_key  # noqa: E402

SERVER = "https://api.xingchenmap.cn"
QUARK_LINE = "夸克网盘链接：https://pan.quark.cn/s/4cec4ca814f7"
UC_LINE = "UC网盘链接：https://drive.uc.cn/s/df8af96910bb4?public=1"
MAX_NOTES = 600
MAX_INSTALLER = 1024 ** 3
TIMEOUT = 30


class PublishError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PublishError("服务器返回了重定向，已停止。")


def server_url() -> str:
    """Production unless a local test server is set explicitly."""
    override = os.environ.get("XINGTU_PUBLISH_SERVER", "").rstrip("/")
    if override:
        if not override.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise PublishError("XINGTU_PUBLISH_SERVER 只能指向本机测试服务器。")
        return override
    return SERVER


def _request(url: str, *, data: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                     headers={"Accept": "application/json",
                                              "User-Agent": "XingtuDesktop/publish-notice", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read(512 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read(64 * 1024).decode("utf-8"))
        except ValueError:
            body = {}
        return error.code, body


def read_manifest(base: str) -> dict:
    try:
        status, body = _request(f"{base}/updates/windows.json")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise PublishError("读取线上版本失败，已停止；没有自动重试。") from error
    if status != 200 or not isinstance(body, dict):
        raise PublishError(f"读取线上版本失败（HTTP {status}）。")
    return body


def app_version() -> str:
    data = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    version = str(data.get("displayVersion") or "")
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        raise PublishError(f"version.json 的 displayVersion 无效：{version!r}")
    return version


def server_version(version: str) -> str:
    """The server requires X.Y.Z; 1.0 is published as 1.0.0."""
    parts = version.split(".")
    return ".".join(parts + ["0"] * (3 - len(parts)))


def check_notes(text: str, version: str) -> list[str]:
    """Problems with the announcement; empty when it follows the house format."""
    text = text.replace("\r\n", "\n").strip()
    lines = [line.strip() for line in text.split("\n")]
    problems = []
    if lines[:1] != ["更新公告："]:
        problems.append("第一行必须是「更新公告：」")
    if lines[1:2] != [f"PC端 v{version}"]:
        problems.append(f"第二行必须是「PC端 v{version}」")
    if lines[2:3] != ["更新内容："]:
        problems.append("第三行必须是「更新内容：」")
    if not any(re.match(r"\d+\.\s*\S", line) for line in lines[3:]):
        problems.append("至少要有一条编号的更新内容，例如「1. ……」")
    if QUARK_LINE not in lines:
        problems.append("缺少正确的夸克网盘链接行")
    if UC_LINE not in lines:
        problems.append("缺少正确的 UC 网盘链接行")
    if len(text) > MAX_NOTES:
        problems.append(f"总字数 {len(text)}，超过 {MAX_NOTES}")
    if "星辰" in text or "作者" in text:
        problems.append("公告里不写作者署名")
    return problems


def installer_info(path: Path, display: str, version: str) -> dict:
    if not path.is_file():
        raise PublishError(f"找不到安装包：{path}")
    accepted = {f"星图-{name}-Setup.exe" for name in (display, version)} | {f"IdentityV-Map-pc-{version}.exe"}
    if path.name not in accepted:
        raise PublishError(f"安装包文件名应为 {' 或 '.join(sorted(accepted))}，实际是 {path.name}。")
    size = path.stat().st_size
    if not 0 < size <= MAX_INSTALLER:
        raise PublishError("安装包大小异常。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return dict(artifactFileName=f"IdentityV-Map-pc-{version}.exe", size=size, sha256=digest.hexdigest().upper())


def build_payload(args, base: str) -> tuple[dict, dict]:
    display = app_version()
    version = server_version(display)
    notes = Path(args.notes).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    problems = check_notes(notes, display)
    if problems:
        raise PublishError("公告格式不对：\n  - " + "\n  - ".join(problems))
    installer = installer_info(Path(args.installer), display, version)
    current = read_manifest(base)
    current_code = int(current.get("versionCode") or 0)
    current_version = str(current.get("version") or "")
    payload = dict(platform="windows", version=version, versionCode=current_code, releaseNotes=notes, **installer)
    if args.version_code in (None, current_code) and already_published(current, payload):
        return payload, current  # Same release is already live; nothing new to check.
    payload["versionCode"] = args.version_code if args.version_code is not None else current_code + 1
    if payload["versionCode"] <= current_code:
        raise PublishError(f"内部版本号必须大于线上的 {current_code}。")
    # The 1.7.x Electron line was replaced by this app's 1.0.x line.
    legacy = version_key(current_version)[:2] == (1, 7) and version_key(version) < (1, 7)
    if current_version and not legacy and not is_newer(version, current_version):
        raise PublishError(f"版本 {version} 不比线上的 {current_version} 新，用户不会收到提醒。")
    return payload, current


def already_published(manifest: dict, payload: dict) -> bool:
    return all(manifest.get(key) == payload[key] for key in
               ("version", "versionCode", "releaseNotes", "artifactFileName", "size", "sha256"))


def preview(payload: dict, current: dict) -> str:
    return "\n".join((
        f"线上当前：v{current.get('version')}（内部版本号 {current.get('versionCode')}）",
        f"本次发布：v{payload['version']}（内部版本号 {payload['versionCode']}）",
        f"安装包：{payload['artifactFileName']} · {payload['size']:,} 字节",
        f"SHA-256：{payload['sha256']}",
        f"公告（{len(payload['releaseNotes'])} 字）：",
        payload["releaseNotes"],
    ))


def publish(payload: dict, base: str, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        status, result = _request(f"{base}/update-api/notices", data=body, headers={
            "Content-Type": "application/json; charset=utf-8", "X-Admin-Token": token, "Origin": base})
    except (urllib.error.URLError, TimeoutError, OSError):
        status, result = None, {}
    if status == 401:
        raise PublishError("管理员口令不正确，已停止。不要反复尝试，请联系口令管理人。")
    if status not in (200, 201, None):
        raise PublishError(f"发布失败（HTTP {status}）：{result.get('message') or '服务器未说明原因'}")
    try:
        readback = read_manifest(base)
    except PublishError as error:
        raise PublishError("发布请求后无法确认线上状态，结果不明；请先预览核对，不要直接重试发布。") from error
    if not already_published(readback, payload):
        if status is None:
            raise PublishError("请求结果不明，线上还不是本次版本。请先刷新后台确认，不要直接重试。")
        raise PublishError("服务器返回成功，但线上信息和本次不一致，请立即人工核对。")
    return readback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="发布星图 Windows 更新信息")
    parser.add_argument("--installer", required=True, help="正式安装包（星图-X.Y.Z-Setup.exe 或 IdentityV-Map-pc-X.Y.Z.exe）")
    parser.add_argument("--notes", required=True, help="公告文本文件（UTF-8）")
    parser.add_argument("--version-code", type=int, help="内部版本号；默认线上当前值 + 1")
    parser.add_argument("--publish", action="store_true", help="真正发布")
    parser.add_argument("--yes", action="store_true",
                        help="与 --publish 一起使用：不再询问确认，口令从 UPDATE_ADMIN_TOKEN 读取")
    args = parser.parse_args(argv)
    try:
        base = server_url()
        payload, current = build_payload(args, base)
        print(preview(payload, current))
        print()
        if already_published(current, payload):
            print("线上已经是这一版的同一份信息，无需重复发布。")
            return 0
        if not args.publish:
            print("以上为预览，尚未发布。确认安装包已上传夸克和 UC 后，加 --publish 发布。")
            return 0
        token = os.environ.get("UPDATE_ADMIN_TOKEN", "").strip()
        if args.yes:
            if not token:
                raise PublishError("没有设置 UPDATE_ADMIN_TOKEN 环境变量，无法自动发布。请让口令持有人按说明设置一次。")
        else:
            if not sys.stdin.isatty():
                raise PublishError("没有交互终端，无法确认。自动发布请加 --yes，并设置 UPDATE_ADMIN_TOKEN。")
            typed = input(f"发布后不能修改或删除。确认发布请输入版本号 {payload['version']}：").strip()
            if typed != payload["version"]:
                print("输入不一致，已取消，没有发布。")
                return 1
            token = token or getpass.getpass("管理员口令（输入时不显示）：").strip()
        if not (len(token) >= 32 or (base != SERVER and token)):
            raise PublishError("管理员口令为空或长度不对，已取消，没有发布。")
        readback = publish(payload, base, token)
        print(f"已发布并核对一致：v{readback['version']}（内部版本号 {readback['versionCode']}）。")
        print("在星图「帮助 → 版本与更新」点「检查更新」可以看到提醒。")
        return 0
    except PublishError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
