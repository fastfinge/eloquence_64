"""Host Channel transport: a local named pipe restricted to this user.

The Host Channel used to be a loopback TCP socket on an ephemeral port, guarded
by a random key that the Synth Driver side passed to the Eloquence Host Process
on its command line.  A process's command line is readable by anyone -- a single
``Get-CimInstance Win32_Process`` query hands over both the port and the key --
so any local process could connect to the port, present the key, and be accepted
as the Eloquence Host Process.  Everything the Synth Driver side then received
went through ``pickle.loads``, which makes that an arbitrary code execution path
into NVDA.

A named pipe removes the shared secret rather than hiding it better:

* The pipe is created with a DACL granting access only to the account NVDA runs
  as, plus LOCAL SYSTEM.  No other user, and no lower-integrity process, can
  open it at all.
* ``FILE_FLAG_FIRST_PIPE_INSTANCE`` means creation fails if the name already
  exists, so nothing can squat the name and impersonate the listener.  The name
  itself carries 128 bits of randomness, so it cannot be guessed either.
* Whoever connects is checked against the Job Object that the Synth Driver side
  puts every Eloquence Host Process into (see ``_eloquence_job``).  That is a
  kernel-answered question about a process we launched, not a secret anyone can
  read and replay.

Nothing is written to the command line that helps an attacker: the pipe name is
useless without the ability to open the pipe and pass the job check.
"""

from __future__ import annotations

import ctypes
import logging
import os
import pickle
import struct
import threading
import time
import _winapi
from ctypes import wintypes
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

_HEADER_STRUCT = struct.Struct("!I")

_PIPE_PREFIX = r"\\.\pipe\eloquence-host-"
_PIPE_NAME_BYTES = 16
# Audio Chunks are a little over 2 KB (see OUTPUT_BUFFER_SAMPLES in
# host_eloquence32.py), so this holds many without blocking the Eloquence Host
# Process mid-synthesis.
_PIPE_BUFFER_BYTES = 65536
# PIPE_TYPE_BYTE, PIPE_READMODE_BYTE and PIPE_WAIT are all zero.  Byte mode keeps
# the length-prefixed framing below in charge of message boundaries.
_PIPE_MODE_BYTE_BLOCKING = 0
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1

# Errors that mean the far end is gone rather than something we can retry.
_DISCONNECTED = frozenset(
	{
		_winapi.ERROR_BROKEN_PIPE,  # 109, peer closed or exited
		232,  # ERROR_NO_DATA, the pipe is being closed
		233,  # ERROR_PIPE_NOT_CONNECTED
		6,  # ERROR_INVALID_HANDLE, we closed underneath an in-flight operation
	}
)


class HostChannelError(RuntimeError):
	"""The Host Channel could not be established."""


def _advapi32() -> ctypes.WinDLL:
	advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
	advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
	advapi32.OpenProcessToken.restype = wintypes.BOOL
	advapi32.GetTokenInformation.argtypes = [
		wintypes.HANDLE,
		ctypes.c_int,
		wintypes.LPVOID,
		wintypes.DWORD,
		ctypes.POINTER(wintypes.DWORD),
	]
	advapi32.GetTokenInformation.restype = wintypes.BOOL
	advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
	advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
	advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
		wintypes.LPCWSTR,
		wintypes.DWORD,
		ctypes.POINTER(wintypes.LPVOID),
		ctypes.POINTER(wintypes.DWORD),
	]
	advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
	return advapi32


def _kernel32() -> ctypes.WinDLL:
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	kernel32.GetCurrentProcess.restype = wintypes.HANDLE
	kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
	kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
	kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
	kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
	kernel32.LocalFree.argtypes = [wintypes.LPVOID]
	kernel32.LocalFree.restype = wintypes.LPVOID
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	kernel32.CloseHandle.restype = wintypes.BOOL
	return kernel32


class _SECURITY_ATTRIBUTES(ctypes.Structure):
	_fields_ = [
		("nLength", wintypes.DWORD),
		("lpSecurityDescriptor", wintypes.LPVOID),
		("bInheritHandle", wintypes.BOOL),
	]


def _current_user_sid(advapi32: ctypes.WinDLL, kernel32: ctypes.WinDLL) -> str:
	"""Return the SID string of the account this process runs as."""
	token = wintypes.HANDLE()
	if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
		raise ctypes.WinError(ctypes.get_last_error())
	try:
		size = wintypes.DWORD()
		# First call fails with ERROR_INSUFFICIENT_BUFFER purely to size the buffer.
		advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
		buffer = ctypes.create_string_buffer(size.value)
		if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
			raise ctypes.WinError(ctypes.get_last_error())
		# TOKEN_USER starts with a SID_AND_ATTRIBUTES whose first member is the PSID.
		sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
		text = wintypes.LPWSTR()
		if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
			raise ctypes.WinError(ctypes.get_last_error())
		try:
			return text.value
		finally:
			kernel32.LocalFree(text)
	finally:
		kernel32.CloseHandle(token)


def _owner_only_security_attributes(advapi32, kernel32):
	"""Build SECURITY_ATTRIBUTES granting this user and SYSTEM, and nobody else.

	Returned alongside the security descriptor so the caller can keep a reference
	for as long as the SECURITY_ATTRIBUTES is in use and free it afterwards.
	"""
	sid = _current_user_sid(advapi32, kernel32)
	# D:P is a protected DACL, so nothing is inherited in; the two ACEs grant
	# GENERIC_ALL to us and to LOCAL SYSTEM.  On a secure screen NVDA already
	# runs as SYSTEM, which simply makes the two entries identical.
	sddl = "D:P(A;;GA;;;{})(A;;GA;;;SY)".format(sid)
	descriptor = wintypes.LPVOID()
	if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
		sddl,
		_SDDL_REVISION_1,
		ctypes.byref(descriptor),
		None,
	):
		raise ctypes.WinError(ctypes.get_last_error())
	attributes = _SECURITY_ATTRIBUTES(
		ctypes.sizeof(_SECURITY_ATTRIBUTES),
		descriptor,
		False,
	)
	return attributes, descriptor


def _as_disconnect(exc: OSError) -> BaseException:
	"""Translate a dead-peer Windows error into EOFError, leaving others alone."""
	if getattr(exc, "winerror", None) in _DISCONNECTED:
		return EOFError(str(exc))
	return exc


class PipeConnection:
	"""Length-prefixed pickle framing over an overlapped named pipe handle.

	The Synth Driver side reads on its receiver thread while sending Host
	Commands from another, which is why every operation gets its own OVERLAPPED:
	a read in flight does not block a concurrent write.  ``_send_lock`` keeps
	concurrent senders from interleaving their frames.
	"""

	def __init__(self, handle: int):
		self._handle: Optional[int] = handle
		self._send_lock = threading.Lock()

	def send(self, payload: Any) -> None:
		data = pickle.dumps(payload, protocol=4)
		frame = _HEADER_STRUCT.pack(len(data)) + data
		with self._send_lock:
			handle = self._handle
			if handle is None:
				raise EOFError("Host Channel is closed")
			try:
				overlapped, _err = _winapi.WriteFile(handle, frame, True)
				written, _err = overlapped.GetOverlappedResult(True)
			except OSError as exc:
				raise _as_disconnect(exc) from exc
			if written != len(frame):
				raise EOFError("Host Channel accepted only part of a frame")

	def recv(self) -> Any:
		(length,) = _HEADER_STRUCT.unpack(self._read_exact(_HEADER_STRUCT.size))
		return pickle.loads(self._read_exact(length))

	def close(self) -> None:
		handle, self._handle = self._handle, None
		if handle is not None:
			try:
				_winapi.CloseHandle(handle)
			except OSError:
				pass

	def _read_exact(self, length: int) -> bytes:
		chunks = []
		remaining = length
		while remaining:
			handle = self._handle
			if handle is None:
				raise EOFError("Host Channel is closed")
			try:
				overlapped, _err = _winapi.ReadFile(handle, remaining, True)
				overlapped.GetOverlappedResult(True)
			except OSError as exc:
				raise _as_disconnect(exc) from exc
			chunk = overlapped.getbuffer()
			if not chunk:
				raise EOFError("Host Channel returned no data")
			chunks.append(chunk)
			remaining -= len(chunk)
		return b"".join(chunks)


class PipeListener:
	"""The Synth Driver side of the Host Channel, before anything connects."""

	def __init__(self, handle: int, name: str, kernel32: ctypes.WinDLL, descriptor):
		self._handle: Optional[int] = handle
		self._kernel32 = kernel32
		# Kept alive only so the security descriptor outlives the pipe handle.
		self._descriptor = descriptor
		self.name = name

	def accept(self, job, timeout: float) -> PipeConnection:
		"""Wait for the Eloquence Host Process to connect and hand over the pipe.

		Anything that connects but is not in *job* is disconnected and we keep
		waiting, so a local process cannot take the Host Channel by connecting
		first, nor deny it to the real Eloquence Host Process by holding the
		single instance.
		"""
		deadline = time.monotonic() + timeout
		while True:
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				raise HostChannelError(
					f"Eloquence Host Process did not connect within {timeout}s. "
					"It may have crashed on startup (e.g. read-only secure screen)."
				)
			self._wait_for_client(remaining)
			pid = self._client_pid()
			if job is None:
				# Job creation already logged a warning of its own.  Accepting is
				# no weaker than the loopback socket this replaced, and refusing
				# would stop Eloquence speaking outright.
				LOGGER.warning("No Job Object to check the Host Channel peer against; accepting pid %s", pid)
				return self._hand_over()
			if job.contains(pid):
				return self._hand_over()
			LOGGER.error(
				"Rejecting Host Channel connection from pid %s: not a process we launched",
				pid,
			)
			self._disconnect()

	def close(self) -> None:
		handle, self._handle = self._handle, None
		if handle is not None:
			try:
				_winapi.CloseHandle(handle)
			except OSError:
				pass
		self._descriptor = None

	# ------------------------------------------------------------------
	def _wait_for_client(self, timeout: float) -> None:
		handle = self._handle
		if handle is None:
			raise HostChannelError("Host Channel listener is closed")
		overlapped = _winapi.ConnectNamedPipe(handle, overlapped=True)
		result = _winapi.WaitForMultipleObjects([overlapped.event], False, int(timeout * 1000))
		if result != _winapi.WAIT_OBJECT_0:
			overlapped.cancel()
			raise HostChannelError(
				f"Eloquence Host Process did not connect within {timeout:.1f}s. "
				"It may have crashed on startup (e.g. read-only secure screen)."
			)
		overlapped.GetOverlappedResult(True)

	def _client_pid(self) -> int:
		pid = wintypes.ULONG()
		if not self._kernel32.GetNamedPipeClientProcessId(self._handle, ctypes.byref(pid)):
			raise ctypes.WinError(ctypes.get_last_error())
		return int(pid.value)

	def _disconnect(self) -> None:
		if self._handle is not None:
			self._kernel32.DisconnectNamedPipe(self._handle)

	def _hand_over(self) -> PipeConnection:
		"""Give the pipe handle to a PipeConnection, which owns it from now on."""
		handle, self._handle = self._handle, None
		self._descriptor = None
		return PipeConnection(handle)


def create_listener() -> PipeListener:
	"""Create the Host Channel end that the Eloquence Host Process connects to."""
	advapi32 = _advapi32()
	kernel32 = _kernel32()
	attributes, descriptor = _owner_only_security_attributes(advapi32, kernel32)
	name = _PIPE_PREFIX + os.urandom(_PIPE_NAME_BYTES).hex()
	handle = _winapi.CreateNamedPipe(
		name,
		_winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE | _winapi.FILE_FLAG_OVERLAPPED,
		_PIPE_MODE_BYTE_BLOCKING,
		1,
		_PIPE_BUFFER_BYTES,
		_PIPE_BUFFER_BYTES,
		0,
		ctypes.addressof(attributes),
	)
	return PipeListener(handle, name, kernel32, descriptor)
