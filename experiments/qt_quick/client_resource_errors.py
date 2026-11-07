"""Bounded resource failure protocol shared by workers and problem records.

Codes are authoritative. Older producers may supply a stable token at the exact
start of their reason; arbitrary text (including filenames) never classifies it.
"""
from __future__ import annotations

import re
from resource_compatibility import safe_dependency_path


def error_text(value):
    text = str(value).encode("utf-8", errors="replace")[:4096].decode("utf-8", errors="ignore")
    # Keep the original failure, removing local installation/user locations.
    text = re.sub(r'''(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n"']*''', "<local-path>", text)
    text = re.sub(r'''(?<![:\w/])//[^\r\n"']*''', "<local-path>", text)
    text = re.sub(r'''(?<![\w./])(?:/(?!/)|~/)[^\r\n"']*''', "<local-path>", text)
    return text.encode("utf-8")[:4096].decode("utf-8", errors="ignore")


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", value) else ""


def legacy_code(reason):
    # Colon/semicolon + space are the established ImportRejected delimiters.
    return safe_code(re.split(r": |; ", reason, maxsplit=1)[0])


def safe_error_details(value):
    """Accept only the dependency proof, never arbitrary exception metadata."""
    if not isinstance(value, dict):
        return {}
    result = {}
    path = value.get("path")
    if safe_dependency_path(path):
        result["path"] = path
    for name in ("expectedBytes", "actualBytes"):
        item = value.get(name)
        if type(item) is int and 0 <= item <= (1 << 63) - 1:
            result[name] = item
        elif name == "actualBytes" and name in value and item is None:
            result[name] = None
    for name in ("expectedSha256", "actualSha256"):
        item = value.get(name)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item):
            result[name] = item
        elif name == "actualSha256" and name in value and item is None:
            result[name] = None
    if type(value.get("missing")) is bool:
        result["missing"] = value["missing"]
    return result


def error_fields(error):
    if isinstance(error, dict):
        raw = error_text(error.get("reason", ""))
        explicit = error.get("code")
        receipt_code = error.get("errorCode")
        # Legacy helper receipts used an exception class as errorCode.
        if not explicit and receipt_code not in (None, "", "ImportRejected", "ValueError", "OSError"):
            explicit = receipt_code
        details = error.get("details", error.get("errorDetails", {}))
        error_type = error.get("errorType", "")
    else:
        raw = error_text(error)
        explicit = getattr(error, "code", None) or getattr(error, "reason", None)
        details = getattr(error, "details", {})
        error_type = type(error).__name__ if isinstance(error, BaseException) else ""
    code = (safe_code(explicit) or "resource-operation-failed") if explicit else legacy_code(raw)
    if not code:
        code = {"BadZipFile": "package-unreadable", "PermissionError": "permission-denied",
                "TimeoutError": "request-deadline-exceeded", "ConnectionError": "network-or-file-error",
                "OSError": "resource-file-error"}.get(error_type, "resource-operation-failed")
    return dict(reason=raw, code=code, details=safe_error_details(details),
                errorType=error_type if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", error_type) else "")


_CATEGORIES = {
    "network": {"request-deadline-exceeded", "network-or-file-error", "unexpected-http-response"},
    "url": {"https-required", "invalid-url", "invalid-url-port", "url-credentials-fragment-or-host-invalid",
            "redirect-location-missing", "redirect-limit", "index-not-configured"},
    "binding": {"online-archive-binding-mismatch"},
    "integrity": {"file-integrity-mismatch", "file-size-or-case-mismatch", "online-archive-size-mismatch",
                  "content-length-mismatch", "index-content-length-mismatch", "package-size-or-sha-mismatch"},
    "limit": {"archive-size-limit", "archive-expanded-size-limit", "expanded-file-size-limit",
              "index-size-limit", "package-size-limit"},
    "dependency": {"missing-or-incompatible-dependency", "base-dependencies-not-declared",
                   "unsupported-client-compatibility", "client-version-missing", "unsupported-client-version"},
    "conflict": {"existing-data-conflict", "shared-package-ownership-conflict", "selected-archive-changed",
                 "target-changed-during-import", "resource-plan-changed-reinspect-required",
                 "recovery-plan-changed-reinspect-required", "removal-plan-changed-reinspect-required",
                 "installed-package-changed", "temporary-file-object-changed"},
    "unsafe": {"invalid-path", "unsafe-path", "noncanonical-path", "unsafe-windows-path", "linked-target",
               "target-outside-root", "invalid-dependency-path", "path-not-in-data-allowlist"},
    "busy": {"resource-busy", "resource-runtime-already-active", "resource-runtime-active",
             "application-running-or-not-writable", "another-import-is-running", "close-application-first",
             "live-import-requires-idle", "resource-consumers-not-stopped", "live-import-requires-restart"},
    "helper": {"import-helper-launch-not-confirmed", "recovery-helper-launch-not-confirmed",
               "import-helper-result-unavailable", "recovery-helper-result-unavailable"},
    "file": {"permission-denied", "resource-file-error", "installation-root-missing"},
    "package": {"invalid-archive-count", "unsupported-archive-entry", "duplicate-archive-path",
                "manifest-missing-or-too-large", "unsupported-package-version", "archive-inventory-mismatch",
                "package-unreadable", "duplicate-json-key", "invalid-json", "manifest-not-object",
                "invalid-file-inventory", "invalid-file-record", "invalid-or-duplicate-file-record",
                "invalid-installed-package-receipt", "duplicate-index-key", "invalid-index-json",
                "unsupported-index-schema", "invalid-index-package", "unknown-package-id"},
}


def error_category(code):
    return next((category for category, codes in _CATEGORIES.items() if code in codes), "")
