"""Tests for the host-side shutdown and logging fixes (#141)."""

import ctypes
import logging
import os
import tempfile
import unittest

# host_eloquence32.py uses ctypes.WINFUNCTYPE at module load time, which only
# exists on Windows.  Provide a stub so the module imports on non-Windows CI.
if not hasattr(ctypes, "WINFUNCTYPE"):
	ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE  # type: ignore[attr-defined]

import host_eloquence32 as host


class FailingConnection:
	"""A connection whose send() always raises, simulating a broken socket."""

	def __init__(self):
		self.send_count = 0

	def send(self, message):
		self.send_count += 1
		raise ConnectionResetError("simulated broken pipe")


class RecordingConnection:
	"""A connection that records all sent messages."""

	def __init__(self):
		self.messages = []

	def send(self, message):
		self.messages.append(message)


class FakeDll:
	def __init__(self):
		self.calls = []

	def eciNewDict(self, handle):
		return 1

	def eciSetDict(self, handle, dictionary_handle):
		pass

	def eciLoadDict(self, handle, dictionary_handle, index, path):
		pass

	def eciSetParam(self, handle, param_id, value):
		pass

	def eciGetVoiceParam(self, handle, voice, param_id):
		return param_id


def make_runtime(conn=None):
	runtime = host.EloquenceRuntime(
		conn=conn or RecordingConnection(),  # type: ignore[arg-type]
		config=host.HostConfig(
			eci_path="",
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
		),
	)
	runtime._dll = FakeDll()  # type: ignore[assignment]
	runtime._handle = "eci"
	return runtime


class SendEventDisableTests(unittest.TestCase):
	"""_send_event must stop attempting sends after the first failure."""

	def test_send_disabled_after_first_failure(self):
		conn = FailingConnection()
		runtime = make_runtime(conn)  # type: ignore[arg-type]
		# First call tries to send and fails
		runtime._send_event("audio", data=b"x", index=None, final=False)
		self.assertTrue(runtime._send_disabled)
		# First failed send actually called conn.send once
		self.assertEqual(conn.send_count, 1)
		# Subsequent calls must not even try
		runtime._send_event("audio", data=b"y", index=None, final=False)
		runtime._send_event("stopped")
		self.assertEqual(conn.send_count, 1)

	def test_send_not_disabled_when_connection_works(self):
		conn = RecordingConnection()
		runtime = make_runtime(conn)  # type: ignore[arg-type]
		runtime._send_event("audio", data=b"x", index=None, final=False)
		self.assertFalse(runtime._send_disabled)
		self.assertEqual(len(conn.messages), 1)


class ServeForeverErrorSendGuardTests(unittest.TestCase):
	"""serve_forever must not crash when the error-response send fails."""

	def test_error_response_send_failure_does_not_crash(self):
		class FailingRecvConnection:
			def __init__(self):
				self._messages = [
					{"type": "command", "id": 1, "command": "bogus", "payload": {}},
				]

			def recv(self):
				if not self._messages:
					raise EOFError
				return self._messages.pop(0)

			def send(self, message):
				# The error-response send must fail
				if message.get("id") == 1 and "error" in message:
					raise ConnectionResetError("simulated")
				pass

		conn = FailingRecvConnection()
		controller = host.HostController(conn)  # type: ignore[arg-type]
		# This must not raise — the error-response send failure is caught
		controller.serve_forever()
		# Reached here = no unhandled exception
		self.assertTrue(True)

	def test_unknown_command_error_response_still_sent(self):
		"""When the connection is healthy, the error response IS delivered."""

		class HealthyConnection:
			def __init__(self):
				self.sent = []

			def recv(self):
				if not self.sent:
					return {"type": "command", "id": 1, "command": "bogus", "payload": {}}
				raise EOFError

			def send(self, message):
				self.sent.append(message)

		conn = HealthyConnection()
		controller = host.HostController(conn)  # type: ignore[arg-type]
		controller.serve_forever()
		error_responses = [m for m in conn.sent if "error" in m]
		self.assertEqual(len(error_responses), 1)
		self.assertEqual(error_responses[0]["error"], "unknownCommand")


class ConfigureLoggingTruncationTests(unittest.TestCase):
	"""configure_logging must truncate (not append to) the log file."""

	def setUp(self):
		# Save and clear existing handlers so tests don't interfere with each
		# other or leak handlers into the global logger.
		self._saved_handlers = logging.getLogger().handlers.copy()
		logging.getLogger().handlers.clear()

	def tearDown(self):
		logging.getLogger().handlers = self._saved_handlers

	def test_log_file_truncated_on_startup(self):
		with tempfile.TemporaryDirectory() as log_dir:
			log_path = os.path.join(log_dir, "eloquence-host.log")
			# Pre-write some stale content
			with open(log_path, "w") as f:
				f.write("stale error log from previous session\n" * 100)
			self.assertGreater(os.path.getsize(log_path), 0)

			# Reconfigure — should truncate
			host.configure_logging(log_dir)
			# The file should now be empty (no errors logged yet at level ERROR)
			self.assertEqual(os.path.getsize(log_path), 0)

	def test_log_file_without_dir(self):
		# Must not crash when log_dir is None
		host.configure_logging(None)


if __name__ == "__main__":
	unittest.main()
