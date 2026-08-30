import importlib.util
import subprocess
import sys
import types
import unittest
from pathlib import Path

requires_windows = unittest.skipUnless(sys.platform == "win32", "Job Objects are Windows only")


def _load_module(name, filename):
	"""Import a synthDrivers module with the NVDA-only imports stubbed out."""
	config_module = types.ModuleType("config")
	config_module.conf = {}
	nvwave_module = types.ModuleType("nvwave")
	nvwave_module.WavePlayer = object
	build_version_module = types.ModuleType("buildVersion")
	build_version_module.version_year = 2026

	stubs = {
		"config": config_module,
		"nvwave": nvwave_module,
		"buildVersion": build_version_module,
	}
	previous = {stub: sys.modules.get(stub) for stub in stubs}
	sys.modules.update(stubs)
	module_name = f"addon.synthDrivers.{name}"
	try:
		path = Path(__file__).parents[1] / "addon" / "synthDrivers" / filename
		spec = importlib.util.spec_from_file_location(module_name, path)
		module = importlib.util.module_from_spec(spec)
		sys.modules[module_name] = module
		spec.loader.exec_module(module)
		return module
	finally:
		sys.modules.pop(module_name, None)
		for stub, old_module in previous.items():
			if old_module is None:
				sys.modules.pop(stub, None)
			else:
				sys.modules[stub] = old_module


class HostJobTests(unittest.TestCase):
	"""The Job Object has to outlive an Eloquence Host Process that never cooperates."""

	@requires_windows
	def test_closing_the_job_kills_an_assigned_process(self):
		job_module = _load_module("_eloquence_job_test", "_eloquence_job.py")
		job = job_module.HostJob.create()
		self.assertIsNotNone(job, "Windows refused to create a Job Object")
		# Stands in for an Eloquence Host Process wedged inside the Eloquence
		# Engine: it will not
		# exit on its own within the lifetime of this test.
		proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
		try:
			self.assertTrue(job.assign(int(proc._handle)))
			self.assertIsNone(proc.poll(), "helper exited before the job was closed")
			# Closing the last handle is what NVDA dying does for us.  The exit
			# code a job close leaves behind is unspecified, so only the fact that
			# the sleep never ran to completion is worth asserting.
			job.close()
			proc.wait(timeout=10)
			self.assertIsNotNone(proc.poll())
		finally:
			if proc.poll() is None:
				proc.kill()
				proc.wait(timeout=10)

	@requires_windows
	def test_assign_after_close_is_refused(self):
		job_module = _load_module("_eloquence_job_test", "_eloquence_job.py")
		job = job_module.HostJob.create()
		self.assertIsNotNone(job)
		job.close()
		self.assertFalse(job.assign(0))

	@requires_windows
	def test_close_is_idempotent(self):
		job_module = _load_module("_eloquence_job_test", "_eloquence_job.py")
		job = job_module.HostJob.create()
		self.assertIsNotNone(job)
		job.close()
		job.close()

	def test_a_missing_job_does_not_block_startup(self):
		"""The backstop is best effort; losing it must not break speech."""
		client_module = _load_module("_eloquence_job_client_test", "_eloquence.py")
		client = client_module.EloquenceHostClient()
		original_create = client_module._job.HostJob.create
		client_module._job.HostJob.create = staticmethod(lambda: None)
		try:
			# Would raise if _adopt_into_job did not tolerate a job it never got.
			client._adopt_into_job(object())
		finally:
			client_module._job.HostJob.create = original_create
		self.assertIsNone(client._job)


if __name__ == "__main__":
	unittest.main()
