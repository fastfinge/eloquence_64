import builtins
import importlib
import json
import os
import sys
import tempfile
import types
import unittest


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


class AddonUpdaterInstallTests(unittest.TestCase):
	def test_check_for_updates_requires_packaged_addon_asset(self):
		module = _load_updater()
		payload = {
			"tag_name": "v2",
			"assets": [{"name": "source.zip", "browser_download_url": "https://example.test/source.zip"}],
		}
		module.urllib.request.urlopen = lambda req: _FakeUrlResponse(payload)

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
		module.urllib.request.urlopen = lambda req: _FakeUrlResponse(payload)

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


class InstallTasksTests(unittest.TestCase):
	def test_on_install_preserves_existing_dic_files_only(self):
		with tempfile.TemporaryDirectory() as root:
			installed = os.path.join(root, "Eloquence")
			pending = os.path.join(root, "Eloquence.pendingInstall")
			installed_data = os.path.join(installed, "synthDrivers", "eloquence")
			pending_data = os.path.join(pending, "synthDrivers", "eloquence")
			os.makedirs(installed_data)
			os.makedirs(pending_data)

			with open(os.path.join(installed_data, "enumain.dic"), "w", encoding="cp1252") as dictionary:
				dictionary.write("user dictionary")
			with open(os.path.join(installed_data, "ECI.INI"), "w", encoding="utf-8") as ini:
				ini.write("installed ini")
			with open(os.path.join(installed_data, "ENU.SYN"), "w", encoding="utf-8") as voice:
				voice.write("installed voice data")
			with open(os.path.join(pending_data, "enumain.dic"), "w", encoding="cp1252") as dictionary:
				dictionary.write("packaged dictionary")

			addon = types.SimpleNamespace(path=pending, installPath=installed)
			addon_handler = types.SimpleNamespace(getCodeAddon=lambda: addon)
			fake_log = _FakeLog()
			log_handler = types.SimpleNamespace(log=fake_log)

			with _ModuleStubs(addonHandler=addon_handler, logHandler=log_handler):
				sys.modules.pop("addon.installTasks", None)
				install_tasks = importlib.import_module("addon.installTasks")
				install_tasks.onInstall()

			with open(os.path.join(pending_data, "enumain.dic"), encoding="cp1252") as dictionary:
				self.assertEqual(dictionary.read(), "user dictionary")
			self.assertFalse(os.path.exists(os.path.join(pending_data, "ECI.INI")))
			self.assertFalse(os.path.exists(os.path.join(pending_data, "ENU.SYN")))

	def test_on_install_ignores_first_install_without_existing_addon(self):
		with tempfile.TemporaryDirectory() as root:
			pending = os.path.join(root, "Eloquence.pendingInstall")
			os.makedirs(os.path.join(pending, "synthDrivers", "eloquence"))

			addon = types.SimpleNamespace(path=pending, installPath=os.path.join(root, "Eloquence"))
			addon_handler = types.SimpleNamespace(getCodeAddon=lambda: addon)
			log_handler = types.SimpleNamespace(log=_FakeLog())

			with _ModuleStubs(addonHandler=addon_handler, logHandler=log_handler):
				sys.modules.pop("addon.installTasks", None)
				install_tasks = importlib.import_module("addon.installTasks")
				install_tasks.onInstall()

			self.assertEqual(os.listdir(os.path.join(pending, "synthDrivers", "eloquence")), [])


if __name__ == "__main__":
	unittest.main()
