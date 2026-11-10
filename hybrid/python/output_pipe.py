"""Bounded output; cancel only this pump's blocked Windows pipe I/O on EOF.

Adapted from the project's protocol-2 hybrid experiment. Using os.write keeps
the canceled thread independent of Python's buffered stdout shutdown lock.
"""
import ctypes
import os
import queue
import threading
import time


class OutputPipe:
    def __init__(self, fd):
        self.fd = fd
        self.queue = queue.Queue(maxsize=16)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._write, name="hybrid-runtime-output")
        self.thread.start()

    def send(self, data):
        if self.closed.is_set():
            raise BrokenPipeError("runtime-output-closed")
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            self.closed.set()
            raise BrokenPipeError("runtime-output-backpressure") from None

    def _write(self):
        try:
            while not self.closed.is_set():
                try:
                    data = self.queue.get(timeout=.05)
                except queue.Empty:
                    continue
                view = memoryview(data)
                while view and not self.closed.is_set():
                    count = os.write(self.fd, view)
                    if count <= 0:
                        raise BrokenPipeError("runtime-output-closed")
                    view = view[count:]
        except OSError:
            self.closed.set()

    def close(self):
        self.closed.set()
        if not self.thread.is_alive():
            return
        if os.name != "nt":
            raise RuntimeError("runtime-output-cancellation-requires-windows")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel.OpenThread.restype = ctypes.c_void_p
        kernel.CancelSynchronousIo.argtypes = [ctypes.c_void_p]
        kernel.CancelSynchronousIo.restype = ctypes.c_bool
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenThread(0x0001, False, self.thread.native_id)
        try:
            deadline = time.monotonic() + 2
            while self.thread.is_alive() and time.monotonic() < deadline:
                if handle:
                    kernel.CancelSynchronousIo(handle)
                self.thread.join(.01)
            if self.thread.is_alive():
                raise RuntimeError("runtime-output-shutdown-incomplete")
        finally:
            if handle:
                kernel.CloseHandle(handle)
