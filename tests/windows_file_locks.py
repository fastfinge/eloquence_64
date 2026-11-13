"""Hold a file open the way another program does, to cause real Windows sharing violations.

Python's own open() always shares reading and writing, so it cannot stand in for a virus scanner,
a backup tool or a sync client. CreateFileW with a chosen share mode can.
"""

import contextlib
import ctypes
import sys
import threading

FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE = 1, 2, 4

if sys.platform == "win32":
	from ctypes import wintypes

	_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	_kernel32.CreateFileW.restype = wintypes.HANDLE
	_kernel32.CreateFileW.argtypes = [
		wintypes.LPCWSTR,
		wintypes.DWORD,
		wintypes.DWORD,
		wintypes.LPVOID,
		wintypes.DWORD,
		wintypes.DWORD,
		wintypes.HANDLE,
	]
	_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	_GENERIC_READ = 0x80000000
	_OPEN_EXISTING = 3
	_FILE_ATTRIBUTE_NORMAL = 0x80
	_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


@contextlib.contextmanager
def held_open(path, share=0, for_seconds=None):
	"""Hold path open for reading, letting others do only what share allows (0: nothing at all).

	With for_seconds, the file is let go after that long while the block carries on.
	"""
	handle = _kernel32.CreateFileW(
		path, _GENERIC_READ, share, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None
	)
	if handle == _INVALID_HANDLE_VALUE:
		raise ctypes.WinError(ctypes.get_last_error())
	lock, closed = threading.Lock(), []

	def release():
		with lock:
			if not closed:
				closed.append(True)
				_kernel32.CloseHandle(handle)

	timer = None
	if for_seconds is not None:
		timer = threading.Timer(for_seconds, release)
		timer.start()
	try:
		yield
	finally:
		if timer is not None:
			timer.cancel()
		release()
