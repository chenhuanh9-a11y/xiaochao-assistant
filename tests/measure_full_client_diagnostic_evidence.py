"""Reproducible synthetic PNG/ZIP size, time and peak-memory measurement.

Run with the repository's Windows Python and OpenCV. Fresh synthetic images
exist only in owned TemporaryDirectory roots and are cleaned afterward. No
captured/private data is used; one JSON object is printed to stdout.
"""
import ctypes
import io
import json
import os
import platform
from pathlib import Path
import sys
import tempfile
import threading
import time
import zipfile

import cv2
import numpy as np

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
from client_failure_samples import FailureSampleRecorder


RESOLUTIONS = ((1920, 1080), (2560, 1440), (3840, 2160))
SEED = 20261111
SAMPLE_SECONDS = 0.005


def _working_set_reader():
    if os.name == 'nt':
        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.c_ulong),
                ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
                ('PrivateUsage', ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        get_memory = psapi.GetProcessMemoryInfo
        get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), ctypes.c_ulong]
        get_memory.restype = ctypes.c_int
        get_process = kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        process = get_process()

        def read():
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            if not get_memory(process, ctypes.byref(counters), counters.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(counters.WorkingSetSize)

        return read

    try:
        import psutil
        process = psutil.Process()
        return lambda: int(process.memory_info().rss)
    except ImportError:
        if os.path.exists('/proc/self/statm'):
            page_size = os.sysconf('SC_PAGE_SIZE')

            def read_proc():
                with open('/proc/self/statm', 'r', encoding='ascii') as stream:
                    resident_pages = int(stream.read().split()[1])
                return resident_pages * page_size

            return read_proc
    raise RuntimeError('peak working-set measurement needs Windows, psutil, or /proc')


_read_working_set = _working_set_reader()


class _PeakWorkingSet:
    def __init__(self):
        self.baseline = 0
        self.peak = 0
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        self.baseline = _read_working_set()
        self.peak = self.baseline

        def sample():
            while not self.stop.wait(SAMPLE_SECONDS):
                try:
                    self.peak = max(self.peak, _read_working_set())
                except OSError:
                    pass

        self.thread = threading.Thread(target=sample, name='working-set-sampler', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.peak = max(self.peak, _read_working_set())

    @property
    def peak_extra_bytes(self):
        return max(0, self.peak - self.baseline)


def _synthetic_frame(width, height, kind, seed):
    if kind == 'high-entropy':
        frame = np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)
    else:
        frame = np.full((height, width, 3), (35, 45, 58), dtype=np.uint8)
        frame[height // 8:height * 7 // 8, width // 12:width * 11 // 12] = (54, 69, 83)
        step_x, step_y = max(1, width // 16), max(1, height // 12)
        for x in range(step_x, width, step_x):
            frame[:, max(0, x - 2):x + 2, :3] = (67, 82, 96)
        for y in range(step_y, height, step_y):
            frame[max(0, y - 2):y + 2, :, :3] = (67, 82, 96)
        for index in range(8):
            left = width * (index + 1) // 12
            top = height * (index % 4 + 1) // 6
            frame[top:top + max(4, height // 40), left:left + max(4, width // 35), :3] = (
                105 + index * 7, 124 + index * 5, 138 + index * 3)

    frame[0, :, :] = (17, 61, 193)
    frame[-1, :, :] = (203, 31, 47)
    frame[:, 0, :] = (29, 211, 73)
    frame[:, -1, :] = (239, 197, 11)
    frame[0, 0, :] = (5, 10, 15)
    frame[0, -1, :] = (25, 50, 75)
    frame[-1, 0, :] = (100, 125, 150)
    frame[-1, -1, :] = (175, 200, 225)
    return frame


def _measure(kind, width, height, index):
    frame = _synthetic_frame(width, height, kind, SEED + index)
    with _PeakWorkingSet() as memory:
        started = time.perf_counter()
        success, encoded = cv2.imencode('.png', frame)
        encode_seconds = time.perf_counter() - started
        copy_started = time.perf_counter()
        png = encoded.tobytes()
        output_copy_seconds = time.perf_counter() - copy_started
    if not success:
        raise RuntimeError(f'PNG encoding failed for {kind} {width}x{height}')
    png_bytes = len(png)

    archive = io.BytesIO()
    with _PeakWorkingSet() as zip_memory:
        started = time.perf_counter()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr('evidence/001/image-0.png', png)
        zip_seconds = time.perf_counter() - started
    zip_bytes = archive.tell()
    del png, encoded, archive

    if os.name != 'nt':
        raise RuntimeError('production Windows writer measurement requires Windows')
    with tempfile.TemporaryDirectory(prefix='full-client-writer-measure-') as temporary:
        recorder = FailureSampleRecorder()
        recorder.automatic_enabled = True
        recorder.policy.interval_seconds = 0
        with _PeakWorkingSet() as writer_memory:
            writer_started = time.perf_counter()
            writer_result = recorder.record_automatic(root=Path(temporary),
                reason='identity-not-uniquely-verified', frame=frame, current=lambda: True,
                diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                    attemptId=f'{SEED + index:032x}', frameCapturedAtUtc='2026-11-11T00:00:00+00:00'))
            writer_seconds = time.perf_counter() - writer_started
        if not writer_result.get('saved'):
            raise RuntimeError(f'production writer failed: {writer_result}')
        writer_image = next((Path(temporary) / 'failures' / 'automatic-v2').glob('auto-*.png'))
        writer_png_bytes = writer_image.stat().st_size

    result = dict(kind=kind, width=width, height=height, channels=3, frameFormat='BGR',
        inputBytes=int(frame.nbytes), pngBytes=png_bytes,
        pngBytesPerPixel=round(png_bytes / frame.shape[0] / frame.shape[1], 4),
        encodeSeconds=round(encode_seconds, 6), outputCopySeconds=round(output_copy_seconds, 6),
        encodeAndCopyPeakAdditionalWorkingSetBytes=memory.peak_extra_bytes,
        zipBytes=zip_bytes, zipSeconds=round(zip_seconds, 6),
        zipPeakAdditionalWorkingSetBytes=zip_memory.peak_extra_bytes,
        writerSavedPngBytes=writer_png_bytes, writerElapsedSeconds=round(writer_seconds, 6),
        writerPeakAdditionalWorkingSetBytes=writer_memory.peak_extra_bytes)
    del frame
    return result


def main():
    cases = []
    index = 0
    for kind in ('structure-like', 'high-entropy'):
        for width, height in RESOLUTIONS:
            cases.append(_measure(kind, width, height, index))
            index += 1
    print(json.dumps(dict(schema='full-client-png-measurement-v1', seed=SEED,
        platform=platform.platform(), python=platform.python_version(),
        opencv=cv2.__version__, memoryMetric='peak process working-set delta during each encode, ZIP or writer stage; sampled every 5ms',
        scope='synthetic BGR frames; encode/ZIP local phases and real Windows record_automatic measured separately; writer metric includes PNG encode, journal, identity and file IO, excludes InfoService ZIP export',
        zipCompression='ZIP_DEFLATED', cases=cases), separators=(',', ':')))


if __name__ == '__main__':
    main()
