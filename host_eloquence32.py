"""32-bit host process for Eloquence synthesis.

This module is executed as a separate helper process under a 32-bit Python
runtime.  It exposes a simple RPC protocol over a length-prefixed pickle IPC
channel so that 64-bit NVDA builds can continue to make use of the original
synthesizer.

The engine itself lives in ``_eci_engine``, which is shared verbatim with the
Synth Driver side so the two backends cannot drift apart.  PyInstaller freezes a
copy of it into this executable (see the ``--paths`` flag in build_host.cmd), so
nothing here depends on the add-on being importable at run time.  What remains
in this module is only what is genuinely process-shaped: the Host Channel, the
command dispatch and the log file.

The helper deliberately avoids importing NVDA modules to keep the runtime self
contained.  All configuration required to load the DLL, open dictionaries and
select the initial voice is provided by the controller process as part of the
`initialize` command.
"""

from __future__ import annotations

import argparse
import logging
import os
import pickle
import struct
import sys
import threading
import time
import _winapi
from typing import Optional

# The engine wrapper is shared with the Synth Driver side.  In a frozen build
# PyInstaller has already baked it in and this resolves from the archive; in a
# development checkout it is found through the path added below.
sys.path.append(os.path.join(os.path.dirname(__file__), "eloquence"))
sys.path.append(os.path.join(os.path.dirname(__file__), "addon", "synthDrivers"))
from _eci_engine import EciEngine, EngineConfig  # noqa: E402


_HEADER_STRUCT = struct.Struct("!I")

# Errors that mean the far end is gone rather than something we can retry.
_DISCONNECTED = frozenset(
	{
		_winapi.ERROR_BROKEN_PIPE,  # 109, peer closed or exited
		232,  # ERROR_NO_DATA, the pipe is being closed
		233,  # ERROR_PIPE_NOT_CONNECTED
		6,  # ERROR_INVALID_HANDLE, we closed underneath an in-flight operation
	}
)


class IpcConnection:
	"""Length-prefixed message channel over the Host Channel named pipe.

	The Synth Driver side creates the pipe with a DACL that admits only its own
	account, so this end simply opens it.  Reads and writes are synchronous: the
	Eloquence Host Process is single threaded, and blocking in ReadFile until the
	next Host Command arrives is exactly the behaviour it wants.  When NVDA dies
	the pipe breaks and ReadFile fails, which is what ends serve_forever().
	"""

	def __init__(self, handle):
		self._handle = handle
		self._send_lock = threading.Lock()

	def send(self, payload):
		data = pickle.dumps(payload, protocol=4)
		frame = _HEADER_STRUCT.pack(len(data)) + data
		with self._send_lock:
			try:
				written = _winapi.WriteFile(self._handle, frame)[0]
			except OSError as exc:
				raise _as_disconnect(exc) from exc
			if written != len(frame):
				raise EOFError("Host Channel accepted only part of a frame")

	def recv(self):
		(length,) = _HEADER_STRUCT.unpack(self._recv_exact(_HEADER_STRUCT.size))
		return pickle.loads(self._recv_exact(length))

	def close(self):
		handle, self._handle = self._handle, None
		if handle is not None:
			try:
				_winapi.CloseHandle(handle)
			except OSError:
				pass

	def _recv_exact(self, length):
		chunks = []
		remaining = length
		while remaining:
			if self._handle is None:
				raise EOFError("Host Channel is closed")
			try:
				chunk = _winapi.ReadFile(self._handle, remaining)[0]
			except OSError as exc:
				raise _as_disconnect(exc) from exc
			if not chunk:
				raise EOFError("Host Channel returned no data")
			chunks.append(chunk)
			remaining -= len(chunk)
		return b"".join(chunks)


def _as_disconnect(exc):
	"""Translate a dead-peer Windows error into EOFError, leaving others alone."""
	if getattr(exc, "winerror", None) in _DISCONNECTED:
		return EOFError(str(exc))
	return exc


def connect_to_host_channel(name, timeout=10.0):
	"""Open the Host Channel the Synth Driver side is listening on."""
	deadline = time.monotonic() + timeout
	while True:
		try:
			handle = _winapi.CreateFile(
				name,
				_winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
				0,
				_winapi.NULL,
				_winapi.OPEN_EXISTING,
				0,
				_winapi.NULL,
			)
		except OSError as exc:
			# Something else momentarily holds the single pipe instance.
			if exc.winerror != _winapi.ERROR_PIPE_BUSY or time.monotonic() >= deadline:
				raise
			time.sleep(0.05)
			continue
		return IpcConnection(handle)


LOGGER = logging.getLogger("eloquence.host")


def configure_logging(log_dir: Optional[str]) -> None:
	"""Initialise logging for the helper.

	The log file is truncated at startup so each host invocation starts fresh
	and old error output does not accumulate across NVDA restarts.  On a clean
	exit with no errors the file stays at zero bytes.
	"""
	log_file = None
	if log_dir:
		log_file = os.path.join(log_dir, "eloquence-host.log")
		try:
			with open(log_file, "w"):
				pass
		except OSError:
			log_file = None
	logging.basicConfig(
		filename=log_file,
		level=logging.ERROR,
		format="%(asctime)s %(levelname)s %(message)s",
	)


class HostController:
	def __init__(self, conn: IpcConnection):
		self._conn = conn
		self._runtime: Optional[EciEngine] = None
		self._should_exit = False
		self._handlers = {
			"initialize": self._handle_initialize,
			"addText": self._handle_add_text,
			"insertIndex": self._handle_insert_index,
			"synthesize": self._handle_synthesize,
			"stop": self._handle_stop,
			"delete": self._handle_delete,
			"setParam": self._handle_set_param,
			"setVoiceParam": self._handle_set_voice_param,
			"copyVoice": self._handle_copy_voice,
		}

	def _send_event(self, event: str, **payload: object) -> None:
		"""Forward one engine event to the Synth Driver side.

		This is the sink handed to EciEngine.  Failures are raised rather than
		swallowed: the engine disables further sends after the first one, which is
		what keeps a closed Host Channel from logging once per Audio Chunk.
		"""
		self._conn.send({"type": "event", "event": event, "payload": payload})

	def serve_forever(self) -> None:
		LOGGER.info("Host controller waiting for commands")
		while not self._should_exit:
			try:
				message = self._conn.recv()
			except (EOFError, ConnectionError, OSError) as exc:
				LOGGER.info("Connection closed, stopping host controller: %s", exc)
				break
			if not isinstance(message, dict):
				LOGGER.warning("Unexpected message %r", message)
				continue
			msg_type = message.get("type")
			if msg_type != "command":
				LOGGER.warning("Unsupported message %s", msg_type)
				continue
			msg_id = message.get("id")
			command = message.get("command")
			handler = self._handlers.get(command)
			if handler is None:
				LOGGER.error("Unknown command %s", command)
				try:
					self._conn.send({"type": "response", "id": msg_id, "error": "unknownCommand"})
				except Exception:
					LOGGER.error("Failed to send error response for unknown command %s", command)
				continue
			try:
				payload = handler(**message.get("payload", {}))
				self._conn.send({"type": "response", "id": msg_id, "payload": payload})
				# Exit after sending response to delete command
				if command == "delete" and self._should_exit:
					break
			except Exception as exc:
				LOGGER.exception("Command %s failed", command)
				try:
					self._conn.send({"type": "response", "id": msg_id, "error": str(exc)})
				except Exception:
					LOGGER.error("Failed to send error response for command %s", command)

	# ------------------------------------------------------------------
	# Command handlers
	def _handle_initialize(self, **payload):
		config = EngineConfig(
			eci_path=payload["eciPath"],
			data_directory=payload["dataDirectory"],
			language_code=payload["language"],
			enable_abbrev_dict=payload.get("enableAbbreviationDict", False),
			enable_phrase_prediction=payload.get("enablePhrasePrediction", False),
			voice_variant=payload.get("voiceVariant", 0),
		)
		self._runtime = EciEngine(self._send_event, config)
		self._runtime.start()
		return self._runtime.get_state()

	def _handle_add_text(self, text: bytes):
		self._runtime.add_text(text)
		return {"status": "ok"}

	def _handle_insert_index(self, value: int):
		self._runtime.insert_index(value)
		return {"status": "ok"}

	def _handle_synthesize(self):
		self._runtime.synthesize()
		return {"status": "ok"}

	def _handle_stop(self):
		self._runtime.stop()
		return {"status": "ok"}

	def _handle_delete(self):
		if self._runtime:
			self._runtime.delete()
		self._should_exit = True
		return {"status": "ok"}

	def _handle_set_param(self, paramId: int, value: int):
		self._runtime.set_param(paramId, value)
		return self._runtime.get_state()

	def _handle_set_voice_param(self, paramId: int, value: int, temporary: bool = False):
		self._runtime.set_voice_param(paramId, value, temporary=temporary)
		if temporary:
			return {"voiceParams": {paramId: value}}
		return self._runtime.get_state()

	def _handle_copy_voice(self, variant: int):
		self._runtime.copy_voice(variant)
		return self._runtime.get_state()


def main() -> None:
	parser = argparse.ArgumentParser(description="Eloquence 32-bit helper")
	parser.add_argument("--pipe", required=True, help="Host Channel named pipe to connect to")
	parser.add_argument("--log-dir", default=None)
	args = parser.parse_args()

	configure_logging(args.log_dir)
	LOGGER.info("Opening Host Channel at %s", args.pipe)

	conn = connect_to_host_channel(args.pipe)
	controller = HostController(conn)
	controller.serve_forever()


if __name__ == "__main__":
	try:
		main()
	except Exception:
		# This process is built with --noconsole, so an escaping traceback has
		# nowhere to go: PyInstaller's windowed bootloader parks on a message box
		# the user cannot see, and the Eloquence Host Process hangs instead of
		# exiting.  Record the failure in eloquence-host.log, which the add-on
		# already reads for diagnostics, and exit non-zero so the Synth Driver
		# side reports a real exit code rather than a connect timeout.
		LOGGER.exception("Eloquence Host Process terminating on an unhandled error")
		sys.exit(1)
