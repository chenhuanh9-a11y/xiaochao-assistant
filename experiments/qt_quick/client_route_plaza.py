"""Qt presentation for PR #23's account/plaza services; no Tk or game owner."""
from __future__ import annotations

import threading
import logging
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from backend import ResourceRows
from client_paths import runtime_paths
from account_service import AccountError, AccountService, BLOCKED_CODES
import route_plaza
import route_submission
from route_scopes import SCOPES


class PlazaRows(ResourceRows):
    fields = ("key", "title", "description", "author", "mapCount", "downloadCount", "updatedDate", "installed", "updateAvailable")


class SubmissionRows(ResourceRows):
    fields = ("key", "title", "description", "revision", "status", "statusLabel", "reason", "updatedDate", "editable", "withdrawable", "canSubmit")


class _Signals(QObject):
    done = Signal(object, object)
    progress = Signal(object)


class _Work(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            result, error = self.function(), None
        except AccountError as failure:
            # Preserve status/code without carrying credential-bearing frames
            # or transport context across the queued signal.
            result, error = None, AccountError(str(failure), failure.status, failure.code)
        except Exception as failure:
            # Exception reprs may contain request bodies/URLs. Never log or expose them.
            logging.getLogger(__name__).warning("Plaza worker failed (%s)", type(failure).__name__)
            result, error = None, "操作失败，请稍后重试"
        finally:
            self.function = None  # Release submitted credentials promptly.
        self.signals.done.emit(result, error)


class RoutePlazaService(QObject):
    changed = Signal()

    def __init__(self, sandbox, galleries, *, account=None, listing=route_plaza.list_routes):
        super().__init__()
        self.account = account or AccountService(runtime_paths(sandbox).settings_path.parent)
        self.galleries = galleries
        self.listing = listing
        self._scope_id = getattr(galleries, "scopeId", "legacy-nightmare")
        self._sort = "latest"
        self._scope_pending = False
        self._detail_version = ""
        self._detail = {}
        self._detail_request = None
        if hasattr(galleries, "scopeChanged"):
            galleries.scopeChanged.connect(self._scope_changed)
        self.model = PlazaRows()
        self.submissions = SubmissionRows()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._routes = {}
        self._query = ""
        self._loaded = self._active = self._busy = self._closed = self._closing = False
        self._generation = self._dialog_generation = 0
        self._job = None
        self._operation = self._download = ""
        self._message = "打开路线广场后读取公开路线"
        self._error = False
        self._account_open = False
        self._account_mode = "登录"
        self._account_message = self._recovery_code = ""
        self._pending_download = None
        self._cancel = threading.Event()
        self._tab = "square"
        self._mine = {}
        self._mine_owner = None
        self._mine_loaded = False
        self._editor_open = False
        self._editor_generation = 0
        self._editor_version = ""
        self._editor_title = self._editor_description = ""
        self._submission = self._submission_context = self._submission_owner = None
        self._pending_action = None
        self._progress = dict(stage="", sent=0, total=0)
        galleries.galleriesChanged.connect(self._refresh_rows)
        galleries.changed.connect(self.changed)

    def _snapshot(self):
        return dict(scopeId=self._scope_id, sort=self._sort, galleryBusy=self.galleries.busy,
                    scopeOptions=[dict(id=x.id, name=x.label) for x in SCOPES.values()], busy=self._busy, operation=self._operation, message=self._message, error=self._error,
                    username=self.account.username or "", persistent=self.account.persistent,
                    loaded=self._loaded, count=len(self._routes), downloading=self._download,
                    query=self._query, accountOpen=self._account_open, accountMode=self._account_mode,
                    accountMessage=self._account_message, recoveryPending=bool(self.account.pending_recovery_code),
                    hasRecoveryCode=bool(self._visible_recovery_code()), tab=self._tab,
                    mineLoaded=self._mine_loaded, editorOpen=self._editor_open,
                    editorGeneration=self._editor_generation, editorVersion=self._editor_version,
                    editorTitle=self._editor_title, editorDescription=self._editor_description,
                    detailOpen=bool(self._detail_version), detail=dict(self._detail),
                    submissionLocked=self._submission is not None, progress=dict(self._progress),
                    submissionChoices=self.galleries.submissionChoices() if self._editor_open else [])

    snapshot = Property("QVariantMap", _snapshot, notify=changed)
    routeModel = Property(QObject, lambda s: s.model, constant=True)
    submissionModel = Property(QObject, lambda s: s.submissions, constant=True)
    @Slot(str)
    def selectScope(self, scope_id):
        if self._busy or self._editor_open or self._account_open or self._closed or self._closing:
            return
        if scope_id in SCOPES and hasattr(self.galleries, "selectScope"):
            self.galleries.selectScope(scope_id)

    @Slot(str)
    def setSort(self, sort):
        if sort in ("latest", "likes", "comments") and sort != self._sort and not self._busy:
            self._sort = sort
            self.search(self._query)

    def _scope_changed(self):
        self._scope_id = self.galleries.scopeId
        self._loaded = self._mine_loaded = False
        self._routes = {}
        self.model.reconcile([])
        self.closeSubmission()
        self.closeDetails()
        self._sync_mine_rows()
        if self._busy:
            self._cancel.set()
            self._generation += 1
            self._scope_pending = True
        elif self._active:
            self.search(self._query)
        self.changed.emit()

    def _resume_scope(self):
        if self._scope_pending and not self._busy and not self._closing and not self._closed:
            self._scope_pending = False
            if self._active:
                self.search(self._query)

    @Slot(str)
    def openDetails(self, version_id):
        if self._closed or self._closing or self._busy or version_id not in self._routes:
            return
        route = self._routes[version_id]
        self._detail_version = version_id
        self._detail = dict(title=route.title, description=route.description, author=route.author,
                            versionId=version_id, comments=[], likeCount=route.like_count,
                            commentCount=route.comment_count, liked=False, myCommentId="", nextOffset=None)
        self._engage("GET", "/comments", public=True)

    @Slot()
    def closeDetails(self):
        self._detail_version = ""
        self._detail = {}
        self.changed.emit()

    def _engage(self, method, suffix, body=None, *, public=False, append=False, report=False):
        if self._closed or self._closing or self._busy or self._detail_version not in self._routes:
            return
        if not public and not self.account.username:
            self.openAccount()
            return
        from account_service import quoted
        route = self._routes[self._detail_version]
        owner = self.account.identity_context
        self._detail_request = (route.version_id, route.work_id, owner, append, report)
        path = "/v1/community/map-galleries/" + quoted(route.version_id) + suffix
        def request():
            if self.account.username:
                return self.account.authorized(method, path, body)
            return route_plaza.call(method, path, body)
        self._run("engagement", request)

    @Slot(bool)
    def likeRoute(self, liked):
        self._engage("PUT", "/like", dict(liked=bool(liked)))

    @Slot(str)
    def saveComment(self, text):
        text = str(text).strip()
        if not 2 <= len(text) <= 800:
            self._note("评论需要 2–800 个字", True)
            return
        self._engage("PUT", "/comment", dict(body=text))

    @Slot()
    def deleteComment(self):
        self._engage("DELETE", "/comment")

    @Slot()
    def moreComments(self):
        offset = self._detail.get("nextOffset")
        if type(offset) is int and 0 <= offset <= 100000:
            self._engage("GET", "/comments?offset=" + str(offset), public=True, append=True)

    @Slot(str, bool)
    def likeComment(self, comment_id, liked):
        if not any(c["id"] == comment_id for c in self._detail.get("comments", [])):
            return
        from account_service import quoted
        self._engage("PUT", "/comments/" + quoted(comment_id) + "/like", dict(liked=bool(liked)))

    @Slot(str, str, str)
    def reportContent(self, comment_id, reason, details):
        reason, details = str(reason).strip(), str(details).strip()
        if not 1 <= len(reason) <= 32 or len(details) > 1000:
            self._note("请填写举报原因（最多 32 字）和补充说明（最多 1000 字）", True)
            return
        from account_service import quoted
        if comment_id and not any(c["id"] == comment_id for c in self._detail.get("comments", [])):
            return
        suffix = ("/comments/" + quoted(comment_id) if comment_id else "") + "/reports"
        self._engage("POST", suffix, dict(reason=reason, details=details or None), report=True)

    def _complete_engagement(self, result, error):
        version, work, owner, append, report = self._detail_request
        if version != self._detail_version:
            return
        if owner != self.account.identity_context:
            self.closeDetails()
            if error:
                self._note(str(error), True)
            return
        if error:
            self._note(str(error), True)
            return
        if report:
            self._note("举报已提交")
            return
        if not isinstance(result, dict) or result.get("work_id") != work or not isinstance(result.get("comments"), list):
            self._note("作品讨论返回的内容无法识别", True)
            return
        rows = []
        for row in result["comments"][:50]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            rows.append(dict(id=row["id"], author=str(row.get("author_username") or "匿名")[:40],
                             body=str(row.get("body") or "")[:800],
                             liked=row.get("liked_by_me") is True, likeCount=route_plaza._count(row.get("like_count")),
                             mine=row["id"] == result.get("my_comment_id")))
        previous = self._detail.get("comments", []) if append else []
        rows = list({r["id"]: r for r in previous + rows}.values())[-200:]
        following = result.get("next_offset")
        self._detail.update(comments=rows, likeCount=route_plaza._count(result.get("like_count")),
            commentCount=route_plaza._count(result.get("comment_count")), liked=result.get("liked_by_me") is True,
            myCommentId=str(result.get("my_comment_id") or ""),
            nextOffset=following if type(following) is int and 0 <= following <= 100000 else None)
        self.changed.emit()

    def _visible_recovery_code(self):
        # Only expose an unacknowledged code belonging to the current account.
        code = self.account.pending_recovery_code
        return code if self._account_open and code == self._recovery_code else ""

    recoveryCode = Property(str, _visible_recovery_code, notify=changed)

    def _note(self, message, error=False):
        self._message, self._error = message, error
        self.changed.emit()

    @Slot(bool)
    def setActive(self, active):
        if self._closed or self._closing or self._active == active:
            return
        self._active = active
        if active:
            self._refresh_rows()
            if not self._loaded and not self._busy:
                self.search(self._query)
        else:
            self.closeAccount()
            self.closeSubmission()
            self.closeDetails()

    def _refresh_rows(self):
        if not self._active or self._closed or self._closing:
            return
        self._sync_mine_rows()
        installed = self.galleries.plazaSources()
        self.model.reconcile([dict(key=r.version_id, title=r.title, description=r.description, author=r.author,
            mapCount=r.map_count, downloadCount=r.download_count, updatedDate=r.updated_date,
            installed=installed.get(r.work_id, {}).get("versionId") == r.version_id,
            updateAvailable=r.work_id in installed and installed[r.work_id]["versionId"] != r.version_id
                and r.revision > installed[r.work_id].get("revision", 0))
            for r in self._routes.values()])

    def _run(self, operation, function):
        if self._closed or self._closing or self._busy:
            return False
        self._busy, self._operation = True, operation
        self._generation += 1
        self._request = (self._generation, self._dialog_generation)
        job = self._job = _Work(function)
        job.signals.done.connect(self._complete, Qt.QueuedConnection)
        job.signals.progress.connect(self._on_progress, Qt.QueuedConnection)
        self.changed.emit()
        self.pool.start(job)
        return True

    @Slot(str)
    def search(self, query):
        if self._busy or self._closing or self._closed:
            return
        self._query = str(query).strip()[:80]
        value = self._query
        scope_id, sort = self._scope_id, self._sort
        def listing():
            if self.listing is route_plaza.list_routes:
                return self.listing(value, scope_id=scope_id, sort=sort)
            return self.listing(value)
        if self._run("list", listing):
            self._note("正在加载路线广场…")

    @Slot(object, object)
    def _complete(self, result, error):
        generation, dialog = self._request
        self._job = None
        if self._operation == "download":
            if self._closed or self._closing or generation != self._generation:
                self._busy, self._operation, self._download = False, "", ""
                self._resume_scope()
                return
            route, context, cancel = self._download_request
            if cancel.is_set() or not self.galleries.plazaContextCurrent(context):
                error = AccountError("已取消下载，未导入路线")
            if error:
                self._download_complete(route, generation, None, error)
            elif not self.galleries.installPlaza(result, context, cancel.is_set,
                    lambda result, error: self._download_complete(route, generation, result, error)):
                self._download_complete(route, generation, None, "自定义图库正忙，请完成当前操作后重试。")
            return
        self._busy = False
        operation, self._operation = self._operation, ""
        if self._closed or self._closing or generation != self._generation:
            self._resume_scope()
            return
        if operation == "engagement":
            self._complete_engagement(result, error)
            self.changed.emit()
            return
        if operation in ("mine", "submit", "withdraw", "review"):
            self._complete_submission(operation, result, error)
            return
        if operation == "list":
            self._loaded = True
            if error:
                self._note("路线广场加载失败：" + str(error), True)
            else:
                self._routes = {r.version_id: r for r in result}
                self._refresh_rows()
                self._note(f"找到 {len(self._routes)} 套路线。" if result else
                           "没有找到匹配的路线。" if self._query else "广场里还没有路线。")
        elif operation == "logout":
            self._clear_private()
            self._note("已退出登录。")
        elif self._account_open and dialog == self._dialog_generation:
            if error:
                self._account_message = str(error)
                self.changed.emit()
            elif operation == "找回密码":
                self._account_mode, self._account_message = "登录", result
                self._pending_download = None
                self._pending_action = None
                self.changed.emit()
            elif operation == "注册" and self.account.pending_recovery_code:
                self._recovery_code = self.account.pending_recovery_code
                self._account_message = "注册成功。请保存恢复码；确认保存后，本机不再显示。"
                self.changed.emit()
            else:
                self.finishSignIn()
        else:
            # An accepted call can finish after dismissal; publish session state,
            # but never restore the dialog or resume its old download intention.
            self.changed.emit()
        if self._mine_owner is not None and self._mine_owner != self.account.identity_context:
            self._clear_private()

    @Slot()
    def openAccount(self):
        if self._closed or self._closing or self._account_open or self._busy:
            return
        self._dialog_generation += 1
        self._account_open = True
        self._account_mode = "登录"
        self._recovery_code = self.account.pending_recovery_code
        self._account_message = ("请保存账号恢复码；确认保存后，本机不再显示。退出软件后，本机不再保留。"
                                 if self._recovery_code else "")
        self.changed.emit()

    @Slot()
    def closeAccount(self):
        self._account_open = False
        self._dialog_generation += 1
        self._pending_download = None
        self._pending_action = None
        self._recovery_code = self._account_message = ""
        self.changed.emit()

    @Slot(str)
    def setAccountMode(self, mode):
        if self._busy or self._visible_recovery_code() or mode not in ("登录", "注册", "找回密码"):
            return
        self._account_mode, self._account_message = mode, ""
        self.changed.emit()

    @Slot(str, str, str, str, str, str)
    def submitAccount(self, identifier, username, email, code, password, confirmation):
        if not self._account_open or self._visible_recovery_code() or self._busy:
            return
        mode = self._account_mode
        if mode != "登录" and password != confirmation:
            self._account_message = "两次输入的密码不一致"
            self.changed.emit()
            return
        if mode == "登录":
            function = lambda: self.account.login(identifier, password)
        elif mode == "注册":
            function = lambda: self.account.register(username, password, email)
        else:
            function = lambda: self.account.recover(identifier, code, password)
        if self._run(mode, function):
            self._account_message = "请稍候…"
            self.changed.emit()

    @Slot()
    def finishSignIn(self):
        if self._busy or not self._account_open or not self.account.username:
            return
        pending = self._pending_download
        action = self._pending_action
        self.account.acknowledge_recovery_code(self._visible_recovery_code())
        self.closeAccount()
        self._note("已登录，可以下载或投稿路线。")
        if self._mine_owner is not None and self._mine_owner != self.account.identity_context:
            self._clear_private()
        if pending is not None and self._active and self._routes.get(pending.version_id) is pending:
            self.downloadRoute(pending.version_id)
        elif action and self._active:
            if action[0] == "editor" and action[1] == self._editor_generation and self._editor_open:
                # Continue at the form; uploading still requires the user's Submit.
                self.changed.emit()
            elif action[0] == "mine" and self._tab == "mine":
                self.loadMine()
            elif action[0] == "submit" and action[1] == self._editor_generation and self._editor_open:
                self.submitGallery(*action[2:])

    @Slot()
    def copyRecoveryCode(self):
        code = self._visible_recovery_code()
        if code:
            QGuiApplication.clipboard().setText(code)
            self._account_message = "已复制恢复码，请妥善保存，保存后请清空剪贴板。"
            self.changed.emit()

    @Slot()
    def logout(self):
        if self._busy:
            return
        self.closeAccount()
        self.closeSubmission()
        self._clear_private()
        self._run("logout", self.account.logout)

    def _clear_private(self):
        self.closeDetails()
        self._mine, self._mine_owner, self._mine_loaded = {}, None, False
        self._submission = self._submission_context = self._submission_owner = None
        if self._active:
            self.submissions.reconcile([])

    def _sync_mine_rows(self):
        scope = SCOPES[self._scope_id]
        rows = [r for r in self._mine.values() if r.get("scenario", "lady-of-misfortune") == scope.scenario
                and (not r.get("packages") or any(p.get("mode") == scope.mode for p in r["packages"]))] if self._mine_owner == self.account.identity_context else []
        self.submissions.reconcile([dict(key=r["id"], title=r["title"], description=r.get("description", ""),
            revision=r["revision"], status=r["status"], statusLabel=route_submission.STATUSES[r["status"]],
            reason=r.get("rejection_reason") or "", updatedDate=str(r.get("updated_at", ""))[:10],
            editable=r["status"] in route_submission.EDITABLE, withdrawable=r["status"] in ("pending", "approved"),
            canSubmit=r["status"] in ("draft", "rejected") and bool(r["packages"])) for r in rows])

    @Slot(str)
    def selectTab(self, tab):
        if self._closed or self._closing or tab not in ("square", "mine") or self._tab == tab:
            return
        self._tab = tab
        self._pending_action = None
        self.changed.emit()
        if tab == "mine" and not self._busy:
            self.loadMine()

    @Slot()
    def loadMine(self):
        if self._busy or self._closed or self._closing:
            return
        if not self.account.username:
            self.openAccount()
            self._pending_action = ("mine",)
            return
        self._private_request_owner = self.account.identity_context
        if self._run("mine", lambda: route_submission.list_mine(self.account)):
            self._note("正在读取我的投稿…")

    @Slot(str)
    def openSubmission(self, version_id=""):
        if self._busy or self._closing or self._closed or self._editor_open:
            return
        row = self._mine.get(version_id) if self._mine_owner == self.account.identity_context else None
        if version_id and (row is None or row["status"] not in route_submission.EDITABLE):
            self._note("当前投稿不能修订，请刷新「我的投稿」。", True)
            return
        self._editor_generation += 1
        self._editor_open = True
        self._editor_version = version_id
        self._editor_title = row["title"] if row else ""
        self._editor_description = row.get("description", "") if row else ""
        self._submission = self._submission_context = self._submission_owner = None
        self._progress = dict(stage="", sent=0, total=0)
        self.changed.emit()
        if not self.account.username:
            self.openAccount()
            self._pending_action = ("editor", self._editor_generation)

    @Slot()
    def closeSubmission(self):
        self._editor_open = False
        self._editor_generation += 1
        self._pending_action = None
        if self._operation == "submit":
            self.cancelSubmission()
        elif not self._busy:
            self._submission = self._submission_context = self._submission_owner = None
        self.changed.emit()

    @Slot(str, str, str)
    def submitGallery(self, gallery_id, title, description):
        if not self._editor_open or self._busy or self._closing or self._closed:
            return
        if not self.account.username:
            self.openAccount()
            self._pending_action = ("submit", self._editor_generation, gallery_id, title, description)
            return
        try:
            if self._submission is None:
                route_submission.metadata(title, description)
                self._submission_context = self.galleries.submissionSnapshot(gallery_id)
                self._submission_owner = self.account.identity_context
                self._editor_title, self._editor_description = title, description
                self._submission = route_submission.Submission(gallery_id, None, title, description,
                                                              version=self._editor_version or None, scope_id=self._scope_id)
            elif (self._submission_owner != self.account.identity_context
                  or not self.galleries.submissionContextCurrent(self._submission_context)):
                raise ValueError("账号、图库或资源已变化，请关闭投稿窗口后重新选择；已有草稿请到「我的投稿」确认")
        except (ValueError, AccountError) as error:
            self._note(str(error), True)
            return
        owner = self._private_request_owner = self.account.identity_context
        context = self._submission_context
        attempt = self._submission
        editor = self._editor_generation
        self._cancel = cancel = threading.Event()
        def cancelled():
            return cancel.is_set() or self.account.identity_context != owner or not self.galleries.submissionContextCurrent(context)
        def work():
            generation = self._request[0]
            def progress(stage, sent=0, total=0):
                self._job.signals.progress.emit((generation, editor, stage, sent, total))
            progress("正在转换坐标并打包")
            route_submission.check_cancelled(cancelled)
            if not self.galleries.submissionDiskCurrent(context):
                raise AccountError("图库文件已被其他操作修改，请刷新图库后重新选择投稿")
            if attempt.maps is None:
                library, _resource_generation, gallery = context
                try:
                    attempt.maps = route_submission.prepare_maps(library, gallery, cancelled=cancelled)
                except route_plaza.RouteError as failure:
                    raise AccountError(str(failure)) from None
                except (ValueError, KeyError, OSError, TypeError):
                    raise AccountError("投稿导航资源格式或校验不正确，请更新自定义地图资源") from None
            return attempt.run(self.account, cancelled=cancelled, progress=progress)
        self._run("submit", work)
        self._note("正在准备投稿…")

    @Slot(object)
    def _on_progress(self, update):
        generation, editor, stage, sent, total = update
        if self._closing or self._closed or generation != self._generation or self._operation != "submit":
            return
        self._progress = dict(stage=stage, sent=sent, total=total)
        self._note(stage + (f" · {sent} / {total} 字节" if total else "…"))

    @Slot()
    def cancelSubmission(self):
        if self._operation == "submit":
            self._cancel.set()
            self._note("正在停止投稿；已发出的请求结束后收取结果…")

    @Slot(str)
    def withdrawSubmission(self, version_id):
        self._submission_action("withdraw", version_id)

    @Slot(str)
    def submitDraft(self, version_id):
        self._submission_action("review", version_id)

    def _submission_action(self, operation, version_id):
        row = self._mine.get(version_id) if self._mine_owner == self.account.identity_context else None
        allowed = ("pending", "approved") if operation == "withdraw" else ("draft", "rejected")
        if self._busy or self._closing or self._closed or row is None or row["status"] not in allowed:
            return
        self._private_request_owner = self.account.identity_context
        if operation == "withdraw":
            function = lambda: route_submission.withdraw(self.account, version_id)
        else:
            function = lambda: route_submission.submit_review(self.account, version_id)
        self._run(operation, function)
        self._note("正在撤回投稿…" if operation == "withdraw" else "正在提交审核…")

    def _complete_submission(self, operation, result, error):
        if self._private_request_owner != self.account.identity_context:
            self._clear_private()
            self._note(str(error) if isinstance(error, AccountError) else "登录已变化，请重新登录后刷新「我的投稿」。", True)
            return
        if error:
            message = str(error)
            if isinstance(error, AccountError) and error.status == 409:
                message = "投稿状态已变化，请刷新「我的投稿」后重试。" + message
            if operation == "submit" and self._submission and (self._submission.draft or self._submission.creation_unknown):
                message += " 已有草稿可在「我的投稿」查看；重试会续接本次草稿。"
            self._note(message, True)
            return
        self._mine_owner = self.account.identity_context
        if operation == "mine":
            self._mine = {r["id"]: r for r in result}
            self._mine_loaded = True
        else:
            self._mine[result["id"]] = result
            if operation == "submit":
                self._submission = self._submission_context = self._submission_owner = None
                self._editor_open = False
                self._editor_generation += 1
        if self._active:
            self._sync_mine_rows()
        self._note("我的投稿已刷新。" if operation == "mine" else "已撤回投稿。" if operation == "withdraw" else "已提交人工审核，可在「我的投稿」查看状态。")

    @Slot(str)
    def downloadRoute(self, version_id):
        if self._closed or self._closing or self._busy:
            return
        route = self._routes.get(version_id)
        if route is None:
            return
        if not self.account.username:
            self.openAccount()
            self._pending_download = route
            return
        context = self.galleries.plazaInstallContext()
        if context is None:
            self._note("自定义图库正忙，请完成当前操作后重试。", True)
            return
        self._cancel = cancel = threading.Event()
        self._download_request = (route, context, cancel)
        self._download = version_id
        library, _generation = context
        self._run("download", lambda: route_plaza.prepare_install(self.account, library, route,
                                                                 cancelled=cancel.is_set))
        self._note(f"正在下载并校验「{route.title}」…")

    def _download_complete(self, route, generation, result, error):
        self._busy, self._operation, self._download = False, "", ""
        if self._closed or self._closing or generation != self._generation:
            return
        self._refresh_rows()
        if error:
            self._note(f"「{route.title}」下载失败：{error}", True)
            if (isinstance(error, AccountError) and error.status in (400, 401, 403, 422)
                    and error.code not in BLOCKED_CODES and not self.account.username and self._active):
                self.openAccount()
                self._account_message = "登录已失效，请重新登录后继续下载。"
                self._pending_download = route
                self.changed.emit()
        else:
            report = result["report"]
            lost = []
            if report["missingMaps"]:
                lost.append(f"{report['missingMaps']} 张图本软件没有")
            if report["droppedPoints"]:
                lost.append(f"{report['droppedPoints']} 个点不在道路上")
            self._note(f"已下载「{route.title}」：{report['maps']} 张图的路线" +
                       (f"（{'，'.join(lost)}，已跳过）" if lost else "") + "。到「自定义地图」里选用。")

    @Slot()
    def cancelDownload(self):
        if self._operation == "download":
            self._cancel.set()
            self._note("正在取消，网络请求结束后停止导入…")

    @Slot()
    def cancel(self):
        """Application close: drain accepted work without allowing another request."""
        self._closing = True
        self.account.shutdown()
        self._cancel.set()
        self._generation += 1
        self.closeAccount()

    def shutdown(self):
        self.cancel()
        self._closed = True
        self.pool.waitForDone()
        self._submission = self._submission_context = self._submission_owner = None
