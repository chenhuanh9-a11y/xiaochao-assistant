"""Qt owner of the original separate import helper's request and receipt.

No process identity or closed-application check is replaced here. The original
launcher retains a real parent handle and the original helper checks normal exit.
"""
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import star_map_import_helper as helper
from client_paths import _no_links


class ImportHandoff:
    def __init__(self, root):
        self.root = Path(root)
        self.pending = self.root / "work/map-packages/qt-import-pending.json"
        self.process = None
        self.request_id = None

    def launch(self, archive, inspection):
        request_id = uuid4().hex
        request = dict(requestId=request_id, archiveSha256=inspection["archiveSha256"])
        _no_links(self.pending)
        self.pending.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pending.with_suffix("." + request_id + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(request, stream)
            os.replace(temporary, self.pending)
            process = helper.launch_import_helper(self.root / "star-map-import-helper.exe",
                archive, self.root, request["archiveSha256"], request_id, wait_seconds=120)
        except Exception:
            helper.cancel_helper_request(self.root, request_id)
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
        self.process, self.request_id = process, request_id
        return request_id

    def cancel(self):
        if self.request_id:
            helper.cancel_helper_request(self.root, self.request_id)

    def read_receipt(self):
        _no_links(self.pending)
        if not self.pending.exists():
            return None
        unavailable = dict(success=False, reason="import-helper-result-unavailable")
        try:
            if self.pending.stat().st_size > 65536:
                return unavailable
            expected = json.loads(self.pending.read_text(encoding="utf-8"))
            if (not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(expected.get("requestId", "")))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(expected.get("archiveSha256", "")))):
                return unavailable
            receipt = helper.receipt_path(self.root)
            _no_links(receipt)
            if not receipt.is_file() or receipt.stat().st_size > 1048576:
                return unavailable
            result = json.loads(receipt.read_text(encoding="utf-8"))
            if (not all(result.get(key) == expected[key] for key in ("requestId", "archiveSha256"))
                    or type(result.get("success")) is not bool):
                return unavailable
            if result["success"] and (result.get("parentExit") != "exited-matching-process-handle"
                    or not result.get("completedAtUTC")
                    or type(result.get("added")) is not int or result["added"] < 0):
                return unavailable
            return result
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return unavailable
