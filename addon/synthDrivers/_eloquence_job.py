"""Windows Job Object backstop that kills the Eloquence Host Process with NVDA.

The Synth Driver side asks the Eloquence Host Process to exit over the Host
Channel on ``terminate()``, and that cooperative path is the one that matters:
a onefile PyInstaller build only removes its ``_MEI`` temp directory when it
exits on its own.

The handshake cannot run when NVDA never gets the chance to send it.  A crash,
a ``taskkill /f``, or an Eloquence Engine call that never returns all leave the
Eloquence Host Process orphaned, holding an Eloquence Engine handle and often
the output device, until someone notices it in Task Manager.

A Job Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` closes that gap
without the Eloquence Host Process cooperating.  The Synth Driver side holds the
only handle to the job, so when the NVDA process goes away for any reason the
kernel closes that handle and terminates every process still in the job.

This is strictly a backstop.  It is never used to shut the Eloquence Host Process
down normally, because a job kill is ``TerminateProcess`` and leaves the ``_MEI``
directory behind - exactly what the cooperative path exists to avoid.  Every
failure here is logged and ignored: a missing backstop must never stop Eloquence
speaking.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Optional

LOGGER = logging.getLogger(__name__)

# winnt.h
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
# JOBOBJECTINFOCLASS.JobObjectExtendedLimitInformation
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# ULONG_PTR and SIZE_T are pointer sized; c_size_t is correct for both bitnesses.
_ULONG_PTR = ctypes.c_size_t


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
	_fields_ = [
		("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
		("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
		("LimitFlags", wintypes.DWORD),
		("MinimumWorkingSetSize", _ULONG_PTR),
		("MaximumWorkingSetSize", _ULONG_PTR),
		("ActiveProcessLimit", wintypes.DWORD),
		("Affinity", _ULONG_PTR),
		("PriorityClass", wintypes.DWORD),
		("SchedulingClass", wintypes.DWORD),
	]


class _IO_COUNTERS(ctypes.Structure):
	_fields_ = [
		("ReadOperationCount", ctypes.c_ulonglong),
		("WriteOperationCount", ctypes.c_ulonglong),
		("OtherOperationCount", ctypes.c_ulonglong),
		("ReadTransferCount", ctypes.c_ulonglong),
		("WriteTransferCount", ctypes.c_ulonglong),
		("OtherTransferCount", ctypes.c_ulonglong),
	]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
	_fields_ = [
		("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
		("IoInfo", _IO_COUNTERS),
		("ProcessMemoryLimit", _ULONG_PTR),
		("JobMemoryLimit", _ULONG_PTR),
		("PeakProcessMemoryUsed", _ULONG_PTR),
		("PeakJobMemoryUsed", _ULONG_PTR),
	]


def _kernel32() -> ctypes.WinDLL:
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
	kernel32.CreateJobObjectW.restype = wintypes.HANDLE
	kernel32.SetInformationJobObject.argtypes = [
		wintypes.HANDLE,
		ctypes.c_int,
		wintypes.LPVOID,
		wintypes.DWORD,
	]
	kernel32.SetInformationJobObject.restype = wintypes.BOOL
	kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
	kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	kernel32.CloseHandle.restype = wintypes.BOOL
	return kernel32


class HostJob:
	"""A kill-on-close Job Object holding the spawned Eloquence Host Processes."""

	def __init__(self, handle: int, kernel32: ctypes.WinDLL):
		self._handle: Optional[int] = handle
		self._kernel32 = kernel32
		self._lock = threading.Lock()

	@classmethod
	def create(cls) -> Optional["HostJob"]:
		"""Return a kill-on-close HostJob, or None if Windows refused to make one."""
		try:
			kernel32 = _kernel32()
			handle = kernel32.CreateJobObjectW(None, None)
			if not handle:
				raise ctypes.WinError(ctypes.get_last_error())
			info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
			info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
			ok = kernel32.SetInformationJobObject(
				handle,
				_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
				ctypes.byref(info),
				ctypes.sizeof(info),
			)
			if not ok:
				error = ctypes.WinError(ctypes.get_last_error())
				kernel32.CloseHandle(handle)
				raise error
		except Exception:
			LOGGER.warning(
				"Could not create the Job Object; an orphaned Eloquence Host Process"
				" will outlive NVDA if it is killed uncleanly",
				exc_info=True,
			)
			return None
		return cls(handle, kernel32)

	def assign(self, process_handle: int) -> bool:
		"""Put an already spawned process into the job.  True if it is now covered."""
		with self._lock:
			if self._handle is None:
				return False
			try:
				if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
					raise ctypes.WinError(ctypes.get_last_error())
			except Exception:
				# Nested jobs need Windows 8 or newer.  Older systems, or a job
				# NVDA itself sits in that forbids nesting, land here.
				LOGGER.warning(
					"Could not assign the Eloquence Host Process to the Job Object;"
					" it will not be killed automatically if NVDA dies uncleanly",
					exc_info=True,
				)
				return False
		return True

	def close(self) -> None:
		"""Close the job handle, terminating anything still inside it.

		Nothing in the add-on calls this: the handle is meant to stay open for the
		life of the NVDA process so the kernel does the killing.  It exists so the
		behaviour can be exercised in tests.
		"""
		with self._lock:
			handle, self._handle = self._handle, None
		if handle is not None:
			self._kernel32.CloseHandle(handle)
