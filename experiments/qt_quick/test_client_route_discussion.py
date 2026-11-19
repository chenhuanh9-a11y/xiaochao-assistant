"""Discussion API contract and late-response UI guards; localhost account only."""
import threading
import unittest
from unittest.mock import patch

from test_client_route_plaza import PlazaTests, until, item, Router, APP, HERE
import route_plaza


class DiscussionTests(unittest.TestCase):
    setUp = PlazaTests.setUp
    cleanup = PlazaTests.cleanup
    load = PlazaTests.load
    sign_in = PlazaTests.sign_in

    def summary(self, **changes):
        route = self.service._routes["ver-2"]
        result = dict(work_id=route.work_id, like_count=4, comment_count=1, liked_by_me=False,
                      my_comment_id=None, next_offset=None,
                      comments=[dict(id="comment-1", author_username="bob", body="这条路线很好用",
                                     liked_by_me=False, like_count=2)])
        result.update(changes)
        return result

    def test_public_detail_and_authenticated_actions_use_exact_endpoints(self):
        self.load()
        with patch.object(route_plaza, "call", return_value=self.summary()) as request:
            self.service.openDetails("ver-2")
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args[:2], ("GET", "/v1/community/map-galleries/ver-2/comments"))
        self.assertEqual(self.service.snapshot["detail"]["likeCount"], 4)
        self.service.closeDetails()
        self.sign_in()
        with patch.object(self.service.account, "authorized", return_value=self.summary(liked_by_me=True)) as request:
            self.service.openDetails("ver-2")
            until(lambda: not self.service.snapshot["busy"])
            self.service.likeRoute(True)
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args, ("PUT", "/v1/community/map-galleries/ver-2/like", dict(liked=True)))
            self.service.saveComment("我的路线建议")
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args, ("PUT", "/v1/community/map-galleries/ver-2/comment", dict(body="我的路线建议")))
            self.service.likeComment("comment-1", True)
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args[1], "/v1/community/map-galleries/ver-2/comments/comment-1/like")
            self.service.deleteComment()
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args[:2], ("DELETE", "/v1/community/map-galleries/ver-2/comment"))
            self.service.reportContent("comment-1", "垃圾内容", "合成测试")
            until(lambda: not self.service.snapshot["busy"])
            self.assertEqual(request.call_args.args[1], "/v1/community/map-galleries/ver-2/comments/comment-1/reports")
            self.assertEqual(self.service.snapshot["message"], "举报已提交")

    def test_closed_detail_does_not_reappear_after_slow_response(self):
        self.load()
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture release timed out")
            return self.summary()
        with patch.object(route_plaza, "call", side_effect=delayed):
            self.service.openDetails("ver-2")
            self.assertTrue(entered.wait(2))
            self.service.closeDetails()
            release.set()
            until(lambda: not self.service.snapshot["busy"])
        self.assertFalse(self.service.snapshot["detailOpen"])
        self.assertEqual(self.service.snapshot["detail"], {})

    def test_account_change_discards_late_personal_markers(self):
        self.load()
        self.sign_in()
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture release timed out")
            return self.summary(liked_by_me=True, my_comment_id="comment-1")
        with patch.object(self.service.account, "authorized", side_effect=delayed):
            self.service.openDetails("ver-2")
            self.assertTrue(entered.wait(2))
            self.service.account.logout()
            release.set()
            until(lambda: not self.service.snapshot["busy"])
        self.assertFalse(self.service.snapshot["detailOpen"])
        self.assertEqual(self.service.snapshot["detail"], {})

    def test_foreign_work_and_invalid_comment_do_not_replace_current_detail(self):
        self.load()
        with patch.object(route_plaza, "call", return_value=self.summary(work_id="other-work")):
            self.service.openDetails("ver-2")
            until(lambda: not self.service.snapshot["busy"])
        self.assertTrue(self.service.snapshot["error"])
        self.assertEqual(self.service.snapshot["detail"]["comments"], [])
        with patch.object(route_plaza, "call") as request:
            self.service.saveComment("x")
            self.assertTrue(self.service.snapshot["error"])
            request.assert_not_called()

    def test_narrow_qml_discussion_controls_and_plain_text(self):
        from PySide6.QtCore import QUrl, QPointF, Qt, QCoreApplication, QEvent
        from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
        self.load()
        self.sign_in()
        engine = QQmlApplicationEngine()
        router = Router()
        engine.rootContext().setContextProperty("routePlazaService", self.service)
        engine.rootContext().setContextProperty("clientCoordinator", router)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(r) for r in rows))
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible:true; width:650; height:470; palette:ClientPalette {}
ClientRoutePlaza { anchors.fill:parent; anchors.margins:16 }
ClientAccount {}
}''', QUrl.fromLocalFile(str(HERE / "discussion-test.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        response = self.summary()
        response["comments"][0]["body"] = "<b>plain text</b>"
        try:
            with patch.object(self.service.account, "authorized", return_value=response) as request:
                self.service.openDetails("ver-2")
                until(lambda: not self.service.snapshot["busy"] and item(window, "routeCommentText_comment-1") is not None)
                label = item(window, "routeCommentText_comment-1")
                self.assertEqual(label.property("text"), "<b>plain text</b>")
                self.assertTrue(QQmlExpression(engine.rootContext(), label, "textFormat === 0").evaluate()[0])
                field = item(window, "routeCommentInput")
                field.setProperty("text", "界面评论测试")
                APP.processEvents()
                button = item(window, "routeCommentSubmit")
                point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                self.assertTrue(0 <= point.x() < window.width() and 0 <= point.y() < window.height())
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
                until(lambda: not self.service.snapshot["busy"])
                self.assertEqual(request.call_args.args, ("PUT", "/v1/community/map-galleries/ver-2/comment", dict(body="界面评论测试")))
                self.assertFalse(window.grabWindow().isNull())
                self.assertEqual(warnings, [])
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
