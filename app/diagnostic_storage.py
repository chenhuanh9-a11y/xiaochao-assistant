"""Byte-bounded diagnostic storage with explicit verified ownership.

One root has a 16 MiB / 32-file / 7-day owned-log budget, in addition to each
stream's 1 MiB + one backup. Unknown, changed, linked and external objects are
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
import tempfile
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


@contextmanager
def _transaction(root):
    with _guard:
        _safe(root);root.mkdir(parents=True,exist_ok=True)
        lock=root/LOCK;_safe(lock)
        if lock.exists() and (not lock.is_file() or lock.stat().st_size):
            raise DiagnosticStorageError('diagnostic-lock-invalid')
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        if os.fstat(fd).st_nlink != 1:
            os.close(fd);raise DiagnosticStorageError('diagnostic-lock-linked')
        locked=False
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
            yield
        finally:
            if locked:
                if os.name=='nt':
                    import msvcrt
                    os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)


def _read(root):
    p=root/REGISTRY;_safe(p)
    if not p.exists():return {}
    if not p.is_file() or p.stat().st_size>128*1024:raise DiagnosticStorageError('diagnostic-registry-invalid')
    try:
        record=json.loads(p.read_text('utf-8'))
        files=record['files']
        if record['schema']!='diagnostic-ownership-v1' or not isinstance(files,dict) or len(files)>ROOT_FILES+2:raise ValueError()
        for name,r in files.items():
            if not LOG_NAME.fullmatch(name) or len(name)>180 or not isinstance(r,dict):raise ValueError()
            if type(r['bytes']) is not int or not 0<=r['bytes']<=FILE_BYTES:raise ValueError()
            if not re.fullmatch(r'[a-f0-9]{64}',r['sha256']) or type(r['writtenAt']) not in (int,float):raise ValueError()
        return files
    except (ValueError,KeyError,TypeError):raise DiagnosticStorageError('diagnostic-registry-invalid') from None


def _save(root,files):
    path=root/REGISTRY;_safe(path)
    # Preserve crash leftovers/unknown files but do not add unbounded debris.
    import itertools
    leftovers=list(itertools.islice(root.glob('.diagnostic-ownership-*.tmp'),5))
    if len(leftovers)>4 or sum(p.lstat().st_size for p in leftovers)>512*1024:
        raise DiagnosticStorageError('diagnostic-journal-reserve-unavailable')
    fd,name=tempfile.mkstemp(prefix='.diagnostic-ownership-',suffix='.tmp',dir=root)
    try:
        with os.fdopen(fd,'wb') as h:
            h.write(json.dumps(dict(schema='diagnostic-ownership-v1',files=files),separators=(',',':')).encode())
            h.flush();os.fsync(h.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def _verified(root,name,entry):
    p=root/name;_safe(p)
    if not p.is_file() or p.stat().st_size!=entry['bytes']:return False
    return hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256']


def _entry(path,now):
    data=path.read_bytes()
    return dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),writtenAt=now)


def _append(path,data):
    # Single complete UTF-8 JSON line, no character-based size prediction.
    with path.open('ab') as h:
        count=h.write(data)
        if count!=len(data):raise DiagnosticStorageError('diagnostic-short-write')
        h.flush()


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
    # Verify before every mutation. Existing unknown files are never adopted.
    for p in (path,previous):
        _safe(p)
        if p.exists() and (p.name not in files or not _verified(root,p.name,files[p.name])):
            raise DiagnosticStorageError('diagnostic-ownership-unavailable')
    # Discard absent ledger entries only; never remove an unknown path.
    files={n:r for n,r in files.items() if (root/n).exists() or (root/n).is_symlink()}
    rotate=path.exists() and path.stat().st_size+len(data)>file_limit
    projected=dict(files)
    if rotate:
        projected.pop(previous.name,None)
        projected[previous.name]=projected.pop(path.name)
    projected[path.name]=dict(bytes=(0 if rotate or not path.exists() else path.stat().st_size)+len(data),sha256='',writtenAt=now)
    # Protect both active stream files; retire only verified historical files.
    victims=[]
    for name,r in sorted(projected.items(),key=lambda pair:pair[1]['writtenAt']):
        over=sum(x['bytes'] for x in projected.values())>root_limit or len(projected)>max_files
        expired=now-r['writtenAt']>max_age
        if name==path.name or (rotate and name==previous.name) or not (over or expired):continue
        if name not in files or not _verified(root,name,files[name]):continue
        victims.append(name);projected.pop(name)
    if sum(x['bytes'] for x in projected.values())>root_limit or len(projected)>max_files:
        raise DiagnosticStorageError('diagnostic-root-budget-unavailable')
    for name in victims:
        (root/name).unlink();files.pop(name)
    if rotate:
        os.replace(path,previous)
        files.pop(path.name,None);files[previous.name]=_entry(previous,now)
    _append(path,data)
    files[path.name]=_entry(path,now)
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
