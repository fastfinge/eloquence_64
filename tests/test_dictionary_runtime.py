"""The glue between NVDA and the dictionary library, with NVDA itself stubbed."""

import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from addon.synthDrivers import _dictionary_library as library
from addon.synthDrivers import _dictionary_runtime as runtime

COMMUNITY = "github.eigencrow.ibmtts-dictionaries"
SET_INI = f"""[set]
id = {COMMUNITY}
name = IBMTTSDictionaries
source_url = https://github.com/eigencrow/IBMTTSDictionaries
source_version = v26.11
source_revision = 9c6c401a7aa12f28a41829dddb007fdc46b97730
attribution = The curators
license = CC0-1.0
license_url = https://example.invalid/license
"""
CAT, DOG = "`[.1kAt]", "`[.1dOg]"


def _write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as f:
		f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


class RuntimeTestCase(unittest.TestCase):
	"""Gives the runtime a configuration folder and one installed add-on that provides a set."""

	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.config = os.path.join(temporary.name, "config")
		self.provider = os.path.join(temporary.name, "addons", "eloquenceDictionaryManager")
		dictionaries = os.path.join(self.provider, "dictionaries")
		_write(
			os.path.join(dictionaries, "contract.ini"),
			"[contract]\nformat = eci-dictionary-sets\nversion = 1\n",
		)
		_write(os.path.join(dictionaries, "sets", COMMUNITY, "set.ini"), SET_INI)
		_write(os.path.join(dictionaries, "sets", COMMUNITY, "enuroot.dic"), _lines(f"cat\t{CAT}"))
		self.app_args = types.SimpleNamespace(configPath=self.config, secure=False)
		self.addons = [
			types.SimpleNamespace(path=self.provider, isPendingInstall=False, isPendingRemove=False)
		]
		self.current_synth = types.SimpleNamespace(name="eloquence")
		self.set_synth_calls = []
		# What NVDA was asked to do, in order: cancelling speech and loading a synth.
		self.nvda_calls = []
		modules = {
			"globalVars": types.SimpleNamespace(appArgs=self.app_args),
			"addonHandler": types.SimpleNamespace(getAvailableAddons=lambda: iter(self.addons)),
			"wx": types.SimpleNamespace(CallAfter=lambda function, *args: function(*args)),
			"synthDriverHandler": types.SimpleNamespace(
				getSynth=lambda: self.current_synth, setSynth=self._set_synth
			),
			"speech": types.SimpleNamespace(cancelSpeech=lambda: self.nvda_calls.append("cancel speech")),
		}
		patcher = mock.patch.dict(sys.modules, modules)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.addCleanup(runtime.stop_watching)
		self.paths = library.DictionaryPaths(os.path.abspath(self.config))

	def _set_synth(self, name):
		self.set_synth_calls.append(name)
		self.nvda_calls.append(f"set synth {name}")

	def active(self, name):
		with open(os.path.join(self.paths.active, name), "rb") as f:
			return f.read()


class RefreshTests(RuntimeTestCase):
	def test_the_engine_is_pointed_at_the_generated_files(self):
		self.assertEqual(runtime.active_directory(), self.paths.active)

	def test_the_first_run_builds_from_an_installed_add_ons_set(self):
		self.assertTrue(runtime.refresh())
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_nothing_is_rebuilt_when_nothing_changed(self):
		runtime.refresh()
		with mock.patch.object(library, "build", side_effect=AssertionError("rebuilt")):
			self.assertFalse(runtime.refresh())

	def test_a_personal_entry_saved_by_an_editor_is_picked_up(self):
		runtime.refresh()
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines(f"cat\t{DOG}"))
		self.assertTrue(runtime.refresh())
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{DOG}"))

	def test_a_rebuild_that_produces_the_same_files_reports_no_change(self):
		runtime.refresh()
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines(f"cat\t{CAT}"))
		with mock.patch.object(library, "build", wraps=library.build) as build:
			self.assertFalse(runtime.refresh())
		build.assert_called_once()

	def test_force_rebuilds_even_when_nothing_changed(self):
		runtime.refresh()
		with mock.patch.object(library, "build", wraps=library.build) as build:
			self.assertFalse(runtime.refresh(force=True))
		build.assert_called_once()

	def test_an_add_on_waiting_to_be_installed_or_removed_provides_nothing(self):
		for flag in ("isPendingInstall", "isPendingRemove"):
			with self.subTest(flag=flag):
				setattr(self.addons[0], flag, True)
				self.assertEqual(runtime.provider_directories(), [])
				setattr(self.addons[0], flag, False)

	def test_secure_mode_writes_nothing(self):
		self.app_args.secure = True
		self.assertFalse(runtime.refresh(force=True))
		self.assertFalse(os.path.exists(self.config))

	def test_prepare_never_raises(self):
		with (
			mock.patch.object(library, "refresh", side_effect=OSError("disk full")),
			self.assertLogs(runtime.log, "ERROR"),
		):
			runtime.prepare()

	def test_an_unknown_configuration_folder_means_no_dictionaries(self):
		del self.app_args.configPath
		with self.assertLogs(runtime.log, "ERROR"):
			self.assertEqual(runtime.active_directory(), "")


class ReloadTests(RuntimeTestCase):
	def test_the_synth_is_reloaded_when_it_is_the_one_running(self):
		runtime.reload_synth()
		self.assertEqual(self.set_synth_calls, ["eloquence"])

	def test_speech_is_cancelled_before_the_synth_is_replaced(self):
		# Otherwise NVDA's speech manager keeps waiting for indexes from the driver that is gone:
		# Say All stops, and later speech is queued unheard until a key press cancels it (#177).
		runtime.reload_synth()
		self.assertEqual(self.nvda_calls, ["cancel speech", "set synth eloquence"])

	def test_another_synths_speech_is_not_cancelled(self):
		self.current_synth = types.SimpleNamespace(name="espeak")
		runtime.reload_synth()
		self.assertEqual(self.nvda_calls, [])

	def test_another_synth_is_left_alone(self):
		self.current_synth = types.SimpleNamespace(name="espeak")
		runtime.reload_synth()
		self.current_synth = None
		runtime.reload_synth()
		self.assertEqual(self.set_synth_calls, [])


class WatcherTests(unittest.TestCase):
	def _watch(self, refresh, on_change):
		watcher = runtime.DictionaryWatcher(refresh, on_change, interval=0.01)
		self.addCleanup(watcher.stop)
		watcher.start()
		return watcher

	def test_a_change_is_passed_on_and_no_change_is_not(self):
		answers = iter([False, True, False])
		checked, changed = threading.Semaphore(0), []

		def refresh():
			checked.release()
			return next(answers, False)

		self._watch(refresh, lambda: changed.append(True))
		for _ in range(4):
			self.assertTrue(checked.acquire(timeout=5))
		self.assertEqual(changed, [True])

	def test_a_failing_check_is_logged_once_and_the_watcher_carries_on(self):
		calls = threading.Semaphore(0)

		def refresh():
			calls.release()
			raise OSError("the folder is locked")

		with self.assertLogs(runtime.log, "ERROR") as logs:
			self._watch(refresh, lambda: None)
			for _ in range(4):
				self.assertTrue(calls.acquire(timeout=5))
		self.assertEqual(len(logs.records), 1)

	def test_stopping_ends_the_thread(self):
		watcher = self._watch(lambda: False, lambda: None)
		watcher.stop()
		self.assertFalse(watcher._thread.is_alive())

	def test_a_watcher_can_be_stopped_from_inside_its_own_callback(self):
		done = threading.Event()
		holder = {}

		def on_change():
			holder["watcher"].stop()
			done.set()

		holder["watcher"] = self._watch(lambda: True, on_change)
		self.assertTrue(done.wait(timeout=5))
		holder["watcher"]._thread.join(timeout=5)
		self.assertFalse(holder["watcher"]._thread.is_alive())


class WatchingTests(RuntimeTestCase):
	def test_an_edit_reloads_the_synth_while_it_runs(self):
		runtime.refresh()
		reloaded = threading.Event()
		with (
			mock.patch.object(runtime, "POLL_SECONDS", 0.01),
			mock.patch.object(runtime, "reload_synth", reloaded.set),
		):
			watcher = runtime.DictionaryWatcher(runtime.refresh, runtime.reload_synth, interval=0.01)
			self.addCleanup(watcher.stop)
			watcher.start()
			_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines(f"cat\t{DOG}"))
			self.assertTrue(reloaded.wait(timeout=5))
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{DOG}"))

	def test_no_watcher_runs_in_secure_mode(self):
		self.app_args.secure = True
		runtime.start_watching()
		self.assertIsNone(runtime._watcher)

	def test_starting_twice_leaves_one_watcher(self):
		runtime.start_watching()
		first = runtime._watcher
		runtime.start_watching()
		self.assertFalse(first._thread.is_alive())
		self.assertTrue(runtime._watcher._thread.is_alive())
		runtime.stop_watching()
		self.assertIsNone(runtime._watcher)


if __name__ == "__main__":
	unittest.main()
