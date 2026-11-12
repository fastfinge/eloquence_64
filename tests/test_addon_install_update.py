import builtins
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


class _FakeLog:
	def __init__(self):
		self.messages = []

	def info(self, *args, **kwargs):
		self.messages.append(("info", args, kwargs))

	def error(self, *args, **kwargs):
		self.messages.append(("error", args, kwargs))


class _ModuleStubs:
	def __init__(self, **modules):
		self.modules = modules
		self.original_modules = {}
		self.original_translation = None
		self.had_translation = False

	def __enter__(self):
		self.had_translation = hasattr(builtins, "_")
		if self.had_translation:
			self.original_translation = builtins._
		builtins._ = lambda text: text
		for name, module in self.modules.items():
			self.original_modules[name] = sys.modules.get(name)
			sys.modules[name] = module
		return self

	def __exit__(self, exc_type, exc, traceback):
		for name, module in self.original_modules.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module
		if self.had_translation:
			builtins._ = self.original_translation
		else:
			del builtins._


class _FakeUrlResponse:
	def __init__(self, payload):
		self.payload = json.dumps(payload).encode("utf-8")

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, traceback):
		return False

	def read(self):
		return self.payload


def _load_updater():
	addon_handler = types.SimpleNamespace(initTranslation=lambda: None)
	with _ModuleStubs(addonHandler=addon_handler):
		sys.modules.pop("addon.synthDrivers._eloquence_updater", None)
		module = importlib.import_module("addon.synthDrivers._eloquence_updater")
		module._ = lambda text: text
		return module


def _patch_urlopen(test, module, urlopen):
	"""The updater shares urllib.request with every other test, so the fake must not outlive this one."""
	patcher = mock.patch.object(module.urllib.request, "urlopen", urlopen)
	patcher.start()
	test.addCleanup(patcher.stop)


class _FakeDownloadResponse:
	"""Yields the given blocks from read(). A block that is an exception is raised instead."""

	def __init__(self, blocks):
		self.blocks = list(blocks)

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, traceback):
		return False

	def info(self):
		return {"Content-Length": "8"}

	def read(self, size):
		block = self.blocks.pop(0) if self.blocks else b""
		if isinstance(block, Exception):
			raise block
		return block


class AddonUpdaterDownloadTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_updater()
		root = tempfile.TemporaryDirectory()
		self.addCleanup(root.cleanup)
		self.manager = self.module.EloquenceUpdateManager(os.path.join(root.name, "synthDrivers"))
		os.makedirs(self.manager.addon_dir)
		self.timeouts = []

	def serve(self, blocks):
		def urlopen(req, timeout):
			self.timeouts.append(timeout)
			return _FakeDownloadResponse(blocks)

		_patch_urlopen(self, self.module, urlopen)

	def test_download_reports_progress_and_bounds_network_waits(self):
		self.serve([b"1234", b"5678"])
		progress = []

		addon_path = self.manager.download_update(
			"https://example.test/Eloquence.nvda-addon",
			lambda percent, message: progress.append(percent),
			lambda: False,
		)

		with open(addon_path, "rb") as package:
			self.assertEqual(package.read(), b"12345678")
		self.assertEqual(progress, [50, 99])
		self.assertEqual(self.timeouts, [self.module.NETWORK_TIMEOUT_SECONDS])

	def test_cancelled_download_raises_and_removes_the_partial_package(self):
		self.serve([b"1234", b"5678"])
		progress = []

		with self.assertRaises(self.module.UpdateCancelled):
			self.manager.download_update(
				"https://example.test/Eloquence.nvda-addon",
				lambda percent, message: progress.append(percent),
				lambda: bool(progress),
			)

		self.assertEqual(progress, [50])
		self.assertFalse(os.path.exists(self.manager.temp_dir))

	def test_failed_download_raises_and_removes_the_partial_package(self):
		self.serve([b"1234", TimeoutError("timed out")])

		with self.assertRaises(TimeoutError):
			self.manager.download_update(
				"https://example.test/Eloquence.nvda-addon", lambda percent, message: None, lambda: False
			)

		self.assertFalse(os.path.exists(self.manager.temp_dir))


class AddonUpdaterInstallTests(unittest.TestCase):
	def test_check_for_updates_requires_packaged_addon_asset(self):
		module = _load_updater()
		payload = {
			"tag_name": "v2",
			"assets": [{"name": "source.zip", "browser_download_url": "https://example.test/source.zip"}],
		}
		_patch_urlopen(self, module, lambda req, timeout: _FakeUrlResponse(payload))

		with tempfile.TemporaryDirectory() as root:
			with open(os.path.join(root, "manifest.ini"), "w", encoding="utf-8") as manifest:
				manifest.write("version = v1\n")
			manager = module.EloquenceUpdateManager(os.path.join(root, "synthDrivers"))

			with self.assertRaisesRegex(RuntimeError, "NVDA add-on package"):
				manager.check_for_updates()

	def test_check_for_updates_uses_nvda_addon_release_asset(self):
		module = _load_updater()
		payload = {
			"tag_name": "v2",
			"body": "Changes",
			"assets": [
				{"name": "source.zip", "browser_download_url": "https://example.test/source.zip"},
				{
					"name": "Eloquence-v2.nvda-addon",
					"browser_download_url": "https://example.test/Eloquence.nvda-addon",
				},
			],
		}
		_patch_urlopen(self, module, lambda req, timeout: _FakeUrlResponse(payload))

		with tempfile.TemporaryDirectory() as root:
			with open(os.path.join(root, "manifest.ini"), "w", encoding="utf-8") as manifest:
				manifest.write("version = v1\n")
			manager = module.EloquenceUpdateManager(os.path.join(root, "synthDrivers"))

			self.assertEqual(
				manager.check_for_updates(),
				(True, "2", "https://example.test/Eloquence.nvda-addon", "Changes"),
			)

	def test_install_update_calls_nvda_addon_store_install_api(self):
		module = _load_updater()
		calls = []
		addon_store = types.ModuleType("addonStore")
		install_module = types.ModuleType("addonStore.install")
		install_module.installAddon = lambda addon_path: calls.append(addon_path)

		with _ModuleStubs(addonStore=addon_store, **{"addonStore.install": install_module}):
			manager = module.EloquenceUpdateManager(os.getcwd())

			self.assertTrue(manager.install_update("update.nvda-addon", parent=object()))

		self.assertEqual(calls, ["update.nvda-addon"])


# The installer's dictionary backup is covered in tests/test_dictionary_migration.py.


if __name__ == "__main__":
	unittest.main()
