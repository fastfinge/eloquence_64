"""Tests for the named pipe Host Channel and the identity check that guards it."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

requires_windows = unittest.skipUnless(sys.platform == "win32", "The Host Channel is Windows only")

SYNTH_DRIVERS = Path(__file__).parents[1] / "addon" / "synthDrivers"


def _load(filename):
	"""Load a dependency-free synthDrivers module straight off disk."""
	name = "eloquence_test_" + Path(filename).stem.lstrip("_")
	spec = importlib.util.spec_from_file_location(name, SYNTH_DRIVERS / filename)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _client_source(pipe_name, body, ready_file=None):
	"""Source for a child that opens the Host Channel, signals, then runs *body*.

	The retry on ERROR_PIPE_BUSY mirrors the Eloquence Host Process: the pipe
	allows a single instance, so a client that arrives while someone else holds
	it has to wait rather than fail.
	"""
	preamble = textwrap.dedent(
		"""
		import _winapi, pickle, struct, time
		deadline = time.monotonic() + 15
		while True:
		    try:
		        h = _winapi.CreateFile(NAME, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0,
		                               _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL)
		        break
		    except OSError as exc:
		        if exc.winerror != _winapi.ERROR_PIPE_BUSY or time.monotonic() >= deadline:
		            raise
		        time.sleep(0.02)
		if READY:
		    open(READY, "w").close()
		def send(payload):
		    data = pickle.dumps(payload, protocol=4)
		    _winapi.WriteFile(h, struct.pack("!I", len(data)) + data)
		"""
	)
	return preamble.replace("NAME", repr(pipe_name)).replace("READY", repr(ready_file)) + textwrap.dedent(
		body
	)


class PipeChannelTests(unittest.TestCase):
	@requires_windows
	def setUp(self):
		self.ipc = _load("_eloquence_ipc.py")
		self.jobs = _load("_eloquence_job.py")
		self.children = []
		self.listener = None
		self.job = None
		self._temp = tempfile.TemporaryDirectory()

	def tearDown(self):
		for child in self.children:
			if child.poll() is None:
				child.kill()
			child.wait(timeout=10)
		if self.listener is not None:
			self.listener.close()
		if self.job is not None:
			self.job.close()
		self._temp.cleanup()

	# ------------------------------------------------------------------
	def _spawn(self, source, job=None):
		child = subprocess.Popen([sys.executable, "-c", source])
		self.children.append(child)
		if job is not None:
			# Assigning the process we spawned also covers any child it re-execs,
			# which is how the onefile Eloquence Host Process actually starts.
			job.assign(int(child._handle))
		return child

	def _ready_path(self, label):
		return os.path.join(self._temp.name, label + ".ready")

	def _wait_for_ready(self, path, timeout=15):
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			if os.path.exists(path):
				return
			time.sleep(0.02)
		self.fail("client never reported that it opened the Host Channel")

	# ------------------------------------------------------------------
	@requires_windows
	def test_the_pipe_name_cannot_be_squatted(self):
		"""FILE_FLAG_FIRST_PIPE_INSTANCE is what stops a fake listener taking the name."""
		import _winapi

		self.listener = self.ipc.create_listener()
		self.assertTrue(self.listener.name.startswith("\\\\.\\pipe\\eloquence-host-"))
		with self.assertRaises(OSError):
			_winapi.CreateNamedPipe(
				self.listener.name,
				_winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE,
				0,
				1,
				4096,
				4096,
				0,
				_winapi.NULL,
			)

	@requires_windows
	def test_the_pipe_is_readable_only_by_this_user_and_system(self):
		"""Read the DACL back off the live pipe rather than trusting the SDDL we built."""
		import ctypes
		from ctypes import wintypes

		SE_KERNEL_OBJECT = 6
		DACL_SECURITY_INFORMATION = 0x00000004

		advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		self.listener = self.ipc.create_listener()

		descriptor = wintypes.LPVOID()
		status = advapi32.GetSecurityInfo(
			wintypes.HANDLE(self.listener._handle),
			SE_KERNEL_OBJECT,
			DACL_SECURITY_INFORMATION,
			None,
			None,
			None,
			None,
			ctypes.byref(descriptor),
		)
		self.assertEqual(status, 0, "GetSecurityInfo failed")
		text = wintypes.LPWSTR()
		self.assertTrue(
			advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
				descriptor,
				1,
				DACL_SECURITY_INFORMATION,
				ctypes.byref(text),
				None,
			)
		)
		sddl = text.value
		kernel32.LocalFree(text)
		kernel32.LocalFree(descriptor)

		self.assertTrue(sddl.startswith("D:P"), f"DACL is not protected: {sddl}")
		self.assertIn(";;;SY)", sddl, f"LOCAL SYSTEM was not granted access: {sddl}")
		# Everyone, Authenticated Users, Users and World must not appear: those are
		# the entries that would put the Host Channel back within reach of another
		# account on the machine.
		for trustee in (";;;WD)", ";;;AU)", ";;;BU)", ";;;IU)", ";;;AN)"):
			self.assertNotIn(trustee, sddl, f"DACL grants {trustee} too broadly: {sddl}")

	@requires_windows
	def test_two_listeners_do_not_collide(self):
		first = self.ipc.create_listener()
		second = self.ipc.create_listener()
		try:
			self.assertNotEqual(first.name, second.name)
		finally:
			first.close()
			second.close()

	@requires_windows
	def test_a_peer_in_the_job_is_accepted_and_frames_round_trip(self):
		self.job = self.jobs.HostJob.create()
		self.assertIsNotNone(self.job)
		self.listener = self.ipc.create_listener()
		self._spawn(
			_client_source(self.listener.name, '\nsend({"greeting": "host"})\ntime.sleep(10)\n'),
			job=self.job,
		)
		connection = self.listener.accept(self.job, timeout=15)
		try:
			self.assertEqual(connection.recv(), {"greeting": "host"})
		finally:
			connection.close()

	@requires_windows
	def test_a_peer_outside_the_job_is_rejected_and_the_real_one_still_gets_in(self):
		"""The attack the loopback socket allowed: connect first, be believed."""
		self.job = self.jobs.HostJob.create()
		self.assertIsNotNone(self.job)
		self.listener = self.ipc.create_listener()

		# Not in the job, and it connects first.  Under the old transport it would
		# have won simply by replaying the authkey read off the command line.
		ready = self._ready_path("impostor")
		impostor = self._spawn(
			_client_source(
				self.listener.name,
				'\nsend({"greeting": "impostor"})\ntime.sleep(20)\n',
				ready_file=ready,
			)
		)
		self._wait_for_ready(ready)

		self._spawn(
			_client_source(self.listener.name, '\nsend({"greeting": "host"})\ntime.sleep(10)\n'),
			job=self.job,
		)
		connection = self.listener.accept(self.job, timeout=20)
		try:
			self.assertEqual(connection.recv(), {"greeting": "host"})
		finally:
			connection.close()
		self.assertIsNone(impostor.poll(), "the impostor should be disconnected, not killed")

	@requires_windows
	def test_accept_times_out_when_nothing_connects(self):
		self.job = self.jobs.HostJob.create()
		self.listener = self.ipc.create_listener()
		with self.assertRaises(self.ipc.HostChannelError):
			self.listener.accept(self.job, timeout=0.5)

	@requires_windows
	def test_a_dead_peer_reads_as_eof(self):
		"""What ends the receiver loop now that there is no socket timeout poll."""
		self.job = self.jobs.HostJob.create()
		self.listener = self.ipc.create_listener()
		child = self._spawn(
			_client_source(self.listener.name, '\nsend({"greeting": "host"})\n'),
			job=self.job,
		)
		connection = self.listener.accept(self.job, timeout=15)
		try:
			self.assertEqual(connection.recv(), {"greeting": "host"})
			child.wait(timeout=10)
			with self.assertRaises(EOFError):
				connection.recv()
		finally:
			connection.close()

	@requires_windows
	def test_job_membership_rejects_an_unrelated_process(self):
		self.job = self.jobs.HostJob.create()
		outsider = self._spawn("import time; time.sleep(10)")
		self.assertFalse(self.job.contains(outsider.pid))


if __name__ == "__main__":
	unittest.main()
