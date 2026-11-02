"""Byte-bounded diagnostic storage with explicit verified ownership.

One root has a 16 MiB / 32-file owned-log budget, in addition to each
stream's two fixed 1 MiB slots. No automatic deletion or recycling. Unknown, changed, linked and external objects are
never removed/adopted. Interrupted writes fail closed rather than claiming
ownership. Callers account unconfirmed writes as loss; this is not business IO.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from diagnostic_privacy import safe_record

FILE_BYTES=1024*1024
ROOT_BYTES=16*1024*1024
ROOT_FILES=32
MAX_AGE=7*86400
REGISTRY='.diagnostic-log-ownership.json'
LOCK='.diagnostic-log.lock'
_guard=threading.RLock()
LOG_NAME=re.compile(r'(?:events(?:-[A-Za-z0-9_.-]+)?\.jsonl|startup-error-[A-Za-z0-9_.-]+\.log|thread-stacks-[0-9]+(?:-previous)?\.jsonl|lifecycle-detail(?:-previous)?\.jsonl)\Z')

class DiagnosticStorageError(OSError):pass


def _safe(path):
    for p in (path,*path.parents):
        try:s=p.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:
            raise DiagnosticStorageError('diagnostic-storage-linked')
        if p==path and stat.S_ISREG(s.st_mode) and s.st_nlink!=1:
            raise DiagnosticStorageError('diagnostic-storage-hardlinked')


_local=threading.local()
_root_guards={}


def _windows_open(path, flags, directory=False):
    import ctypes, msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    access=0x80000000 | (0x40000000 if flags & os.O_RDWR else 0)
    # Deny rename/delete and concurrent writers while a handle is validated/used.
    handle=create(str(path),access,1,None,1 if flags & os.O_EXCL else 3,
                  0x00200000 | (0x02000000 if directory else 0),None)
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    fd=msvcrt.open_osfhandle(handle,(flags & os.O_RDWR) | os.O_BINARY)
    st=os.fstat(fd)
    if getattr(st,'st_file_attributes',0)&0x400:
        os.close(fd);raise DiagnosticStorageError('diagnostic-storage-linked')
    return fd


@contextmanager
def _pinned_root(root):
    handles=[]
    try:
        if os.name=='nt':
            current=Path(root.anchor)
            handles.append(_windows_open(current,os.O_RDONLY,True))
            for part in root.parts[1:]:
                current=current/part
                try:fd=_windows_open(current,os.O_RDONLY,True)
                except FileNotFoundError:
                    current.mkdir();fd=_windows_open(current,os.O_RDONLY,True)
                handles.append(fd)
        else:
            handles.append(os.open(root.anchor,os.O_RDONLY|os.O_DIRECTORY))
            for part in root.parts[1:]:
                try:fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=handles[-1])
                except FileNotFoundError:
                    os.mkdir(part,mode=0o700,dir_fd=handles[-1])
                    fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=handles[-1])
                handles.append(fd)
        _local.root=root;_local.root_fd=handles[-1]
        yield
    finally:
        for fd in reversed(handles):os.close(fd)


def _open_fd(path,flags):
    if os.name=='nt':fd=_windows_open(path,flags)
    else:fd=os.open(path.name,flags|os.O_NOFOLLOW,0o600,dir_fd=_local.root_fd)
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:
        os.close(fd);raise DiagnosticStorageError('diagnostic-storage-linked')
    return fd


def _read_fd(fd):
    os.lseek(fd,0,os.SEEK_SET)
    chunks=[]
    while True:
        data=os.read(fd,65536)
        if not data:break
        chunks.append(data)
        if sum(map(len,chunks))>FILE_BYTES:raise DiagnosticStorageError('diagnostic-file-budget-invalid')
    return b''.join(chunks)


def _identity(fd):
    st=os.fstat(fd)
    return [st.st_dev,st.st_ino]


@contextmanager
def _transaction(root):
    with _guard:guard=_root_guards.setdefault(str(root),threading.RLock())
    with guard, _pinned_root(root):
        try:fd=_open_fd(root/LOCK,os.O_RDWR|os.O_CREAT|os.O_EXCL)
        except FileExistsError:fd=_open_fd(root/LOCK,os.O_RDWR)
        locked=False
        try:
            if os.fstat(fd).st_size:raise DiagnosticStorageError('diagnostic-lock-invalid')
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True;yield
        finally:
            if locked and os.name!='nt':
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)


def _read(root):
    try:fd=_open_fd(root/REGISTRY,os.O_RDONLY)
    except FileNotFoundError:_local.journal=None;return {}
    try:
        data=_read_fd(fd);_local.journal=(_identity(fd),data)
    finally:os.close(fd)
    if len(data)>128*1024:raise DiagnosticStorageError('diagnostic-registry-invalid')
    try:
        record=json.loads(data);files=record['files']
        if record['schema']!='diagnostic-ownership-v1' or not isinstance(files,dict) or len(files)>ROOT_FILES+2:raise ValueError()
        for name,r in files.items():
            if not LOG_NAME.fullmatch(name) or len(name)>180 or not isinstance(r,dict):raise ValueError()
            if type(r['bytes']) is not int or not 0<=r['bytes']<=FILE_BYTES:raise ValueError()
            if not re.fullmatch(r'[a-f0-9]{64}',r['sha256']) or type(r['writtenAt']) not in (int,float):raise ValueError()
        return files
    except (ValueError,KeyError,TypeError):raise DiagnosticStorageError('diagnostic-registry-invalid') from None


def _save(root,files):
    data=json.dumps(dict(schema='diagnostic-ownership-v1',files=files),separators=(',',':')).encode()
    if len(data)>128*1024:raise DiagnosticStorageError('diagnostic-registry-invalid')
    expected=_local.journal
    fd=_open_fd(root/REGISTRY,os.O_RDWR if expected else os.O_RDWR|os.O_CREAT|os.O_EXCL)
    try:
        if expected and (_identity(fd)!=expected[0] or _read_fd(fd)!=expected[1]):
            raise DiagnosticStorageError('diagnostic-journal-changed')
        os.lseek(fd,0,os.SEEK_SET)
        if os.write(fd,data)!=len(data):raise DiagnosticStorageError('diagnostic-short-write')
        os.ftruncate(fd,len(data));os.fsync(fd)
        _local.journal=(_identity(fd),data)
    finally:os.close(fd)


def _verified(root,name,entry):
    try:fd=_open_fd(root/name,os.O_RDONLY)
    except FileNotFoundError:return False
    try:
        data=_read_fd(fd)
        return (len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
                and _identity(fd)==entry.get('identity'))
    finally:os.close(fd)


def _append(path,data):
    expected=_local.expected
    fd=_open_fd(path,os.O_RDWR if expected else os.O_RDWR|os.O_CREAT|os.O_EXCL)
    try:
        before=_read_fd(fd)
        if expected and (_identity(fd)!=expected.get('identity') or len(before)!=expected['bytes'] or hashlib.sha256(before).hexdigest()!=expected['sha256']):
            raise DiagnosticStorageError('diagnostic-ownership-unavailable')
        os.lseek(fd,0,os.SEEK_END)
        if os.write(fd,data)!=len(data):raise DiagnosticStorageError('diagnostic-short-write')
        return dict(bytes=len(before)+len(data),sha256=hashlib.sha256(before+data).hexdigest(),
                    writtenAt=_local.now,identity=_identity(fd))
    finally:os.close(fd)


def encode_record(row,limit):
    clean=safe_record(row)
    data=(json.dumps(clean,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
    if len(data)<=limit:return data
    # Keep only safe identity/correlation, never cut bytes in a JSON/UTF-8 token.
    small={k:v for k,v in clean.items() if k in ('event','time','timestampUTC','sessionId','pid','logStream','seq','installId','roundId','cycleId')}
    small.update(diagnosticRecordTruncated=True,originalSanitizedBytes=len(data))
    data=(json.dumps(small,separators=(',',':'))+'\n').encode()
    if len(data)>limit:raise DiagnosticStorageError('diagnostic-record-budget-too-small')
    return data


class BatchStorageError(DiagnosticStorageError):
    def __init__(self, failure, completed):
        super().__init__(str(failure))
        self.completed = completed


def _append_owned(root,path,previous,files,data,file_limit,root_limit,max_files,max_age,now):
    # No pathname unlink/replace: retirement cannot safely prove a later
    # pathname still denotes the verified file. Keep both fixed slots, then
    # reject writes at capacity. No auto-adoption of pre-handle-era ledgers.
    files=dict(files)
    target=path
    if path.name in files and files[path.name]['bytes']+len(data)>file_limit:target=previous
    expected=files.get(target.name)
    if expected and expected['bytes']+len(data)>file_limit:
        raise DiagnosticStorageError('diagnostic-stream-budget-exhausted')
    total=sum(r['bytes'] for r in files.values())+len(data)
    if total>root_limit or len(files)+(target.name not in files)>max_files:
        raise DiagnosticStorageError('diagnostic-root-budget-unavailable')
    _local.expected=expected;_local.now=now
    files[target.name]=_append(target,data)
    return files


def append_records(path,rows,*,file_limit=FILE_BYTES,root_limit=ROOT_BYTES,max_files=ROOT_FILES,max_age=MAX_AGE,now=None):
    """At most 16 records / one ownership fsync; exact confirmed prefix on error."""
    if not isinstance(rows,(tuple,list)) or len(rows)>16:
        raise DiagnosticStorageError('diagnostic-batch-limit')
    if not rows:return 0
    path=Path(path).absolute();root=path.parent
    if not 256<=file_limit<=FILE_BYTES:raise DiagnosticStorageError('diagnostic-file-budget-invalid')
    if not LOG_NAME.fullmatch(path.name) or len(path.name)>180:raise DiagnosticStorageError('diagnostic-file-name-invalid')
    payloads=[encode_record(row,file_limit) for row in rows]
    now=time.time() if now is None else now
    previous=path.with_name(path.stem+'-previous'+path.suffix)
    with _transaction(root):
        files=_read(root);original=dict(files);completed=0
        try:
            for data in payloads:
                files=_append_owned(root,path,previous,files,data,file_limit,root_limit,max_files,max_age,now)
                completed+=1
        except Exception as failure:
            # A clean failure can publish the verified successful prefix. A
            # partial/changed object is never adopted or silently truncated.
            valid=(not path.exists() or path.name in files) and all(_verified(root,n,e) for n,e in files.items())
            if files!=original and valid:
                try:_save(root,files)
                except Exception as journal_error:raise BatchStorageError(journal_error,completed) from journal_error
            raise BatchStorageError(failure,completed) from failure
        try:_save(root,files)
        except Exception as failure:raise BatchStorageError(failure,completed) from failure
    return sum(map(len,payloads))


def append_record(path,row,**options):
    return append_records(path,[row],**options)
