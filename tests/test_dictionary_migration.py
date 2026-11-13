"""Moving from dictionaries kept in the add-on folder to the dictionary library.

Two halves: the installer backs the old files up into the configuration, and the first synth load
imports that backup as the user's own files so speech stays as it was.
"""

import builtins
import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

from addon.synthDrivers import _dictionary_library as library
from tests.windows_file_locks import FILE_SHARE_READ, held_open

CAT, DOG, COW = "`[.1kAt]", "`[.1dOg]", "`[.1kW]"


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def _write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as f:
		f.write(data)


def _new_start():
	"""What restarting NVDA forgets: which old folders were checked, and what was already logged."""
	library._old_directories_checked.clear()
	library._reported.clear()


class MigrationTestCase(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = temporary.name
		self.paths = library.DictionaryPaths(os.path.join(self.root, "config"))
		self.old_folder = os.path.join(self.root, "addons", "Eloquence", "synthDrivers", "eloquence")
		_new_start()

	def legacy(self, **files):
		for name, data in files.items():
			_write(os.path.join(self.paths.legacy, name.replace("_", ".")), data)

	def refresh(self):
		return library.refresh(self.paths, [], old_dictionary_directory=self.old_folder)

	def rows(self, language):
		return [
			(row.label, row.slot, row.on, row.imported)
			for row in library.load_settings(self.paths).peek(language).rows
		]

	def active(self, name):
		with open(os.path.join(self.paths.active, name), "rb") as f:
			return f.read()

	def active_names(self):
		return sorted(name for name in os.listdir(self.paths.active) if name.endswith(".dic"))


class ImportTests(MigrationTestCase):
	def test_files_the_old_engine_loaded_come_in_switched_on_and_speech_stays(self):
		self.legacy(
			ENUmain_dic=_lines("WHO\twho"),
			enuroot_dic=_lines(f"cat\t{CAT}"),
			DEUroot_dic=_lines("hund\thund"),
		)
		self.assertTrue(self.refresh())
		self.assertEqual(
			self.rows("enu"), [("enuroot.dic", "root", True, True), ("ENUmain.dic", "main", True, True)]
		)
		self.assertEqual(self.rows("deu"), [("DEUroot.dic", "root", True, True)])
		self.assertEqual(self.active("enumain.dic"), _lines("WHO\twho"))
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))
		self.assertEqual(self.active("deuroot.dic"), _lines("hund\thund"))

	def test_a_word_in_both_main_and_root_keeps_both_entries_as_the_old_engine_had_them(self):
		# The old engine loaded the three files together and main simply beat root. Imported as rows
		# with root last, the root entry would have removed the main one the user was hearing.
		self.legacy(
			enumain_dic=_lines("GUI\tgooey"),
			enuroot_dic=_lines("gui\tguy"),
			enuabbr_dic=_lines("Dr.\tdoctor"),
		)
		self.refresh()
		self.assertEqual(
			[slot for _label, slot, _on, _imported in self.rows("enu")], ["root", "abbr", "main"]
		)
		self.assertEqual(self.active("enumain.dic"), _lines("GUI\tgooey"))
		self.assertEqual(self.active("enuroot.dic"), _lines("gui\tguy"))

	def test_a_name_with_no_language_counts_as_american_english(self):
		self.legacy(root_dic=_lines(f"cat\t{CAT}"))
		self.refresh()
		self.assertEqual(self.rows("enu"), [("root.dic", "root", True, True)])
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_a_file_the_old_engine_never_reached_comes_in_switched_off(self):
		# enumain.dic was found first, so main.dic beside it was never loaded for any language.
		self.legacy(main_dic=_lines("WHO\tgeneric"), enumain_dic=_lines("WHO\tspecific"))
		self.refresh()
		self.assertEqual(
			self.rows("enu"), [("main.dic", "main", False, True), ("enumain.dic", "main", True, True)]
		)
		self.assertEqual(self.active("enumain.dic"), _lines("WHO\tspecific"))

	def test_a_dialect_keeps_hearing_its_main_languages_file(self):
		# The old engine fell back from British to American English; the dialect setting does that now.
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.refresh()
		self.assertEqual(self.rows("eng"), [])
		self.assertEqual(self.active("engroot.dic"), _lines(f"cat\t{CAT}"))

	def test_british_and_american_files_now_both_apply_to_british_english(self):
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}", f"dog\t{DOG}"), engroot_dic=_lines(f"cat\t{COW}"))
		self.refresh()
		self.assertEqual(self.active("engroot.dic"), _lines(f"dog\t{DOG}", f"cat\t{COW}"))

	def test_a_file_whose_name_says_too_little_stays_in_the_backup_only(self):
		self.legacy(
			backup_dic=_lines(f"cat\t{CAT}"),
			xyzroot_dic=_lines(f"cat\t{CAT}"),
			enuroot_dic=_lines(f"dog\t{DOG}"),
		)
		with self.assertLogs(library.log, "WARNING"):
			self.refresh()
		self.assertEqual(self.rows("enu"), [("enuroot.dic", "root", True, True)])
		self.assertEqual(sorted(os.listdir(self.paths.legacy)), ["backup.dic", "enuroot.dic", "xyzroot.dic"])

	def test_lines_no_engine_could_use_are_dropped_and_the_rest_kept(self):
		# The old update accepted lines with no tab, and nothing checked encodings.
		old = "cat\t" + CAT + "\r\n\r\nno tab on this line\r\ndog\t" + DOG + "\r\n漢\than\r\n"
		self.legacy(enuroot_dic=old.encode("utf-8"))
		with self.assertLogs(library.log, "WARNING") as logs:
			self.refresh()
		self.assertIn("2 line(s)", logs.output[0])
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}", f"dog\t{DOG}"))

	def test_entries_after_an_old_blank_line_become_active(self):
		# The proprietary engine stopped reading at a blank line; files from before that fix have one.
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}", "", f"dog\t{DOG}"))
		self.refresh()
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}", f"dog\t{DOG}"))

	def test_entries_that_break_a_rule_but_work_are_kept(self):
		self.legacy(enuabbr_dic=_lines("IDK\tI don't know"))
		self.refresh()
		self.assertEqual(self.active("enuabbr.dic"), _lines("IDK\tI don't know"))

	def test_an_asian_file_is_imported_untouched(self):
		data = "単語\tたんご\r\n\r\n".encode("cp932")
		self.legacy(jpnmain_dic=data)
		self.refresh()
		self.assertEqual(self.active("jpnmain.dic"), data)

	def test_the_backup_is_never_changed(self):
		original = b"cat\t`[.1kAt]\n\nno tab here\n"
		self.legacy(enuroot_dic=original)
		with self.assertLogs(library.log, "WARNING"):
			self.refresh()
		with open(os.path.join(self.paths.legacy, "enuroot.dic"), "rb") as f:
			self.assertEqual(f.read(), original)

	def test_the_import_happens_once(self):
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.refresh()
		settings = library.load_settings(self.paths)
		library.remove_own_file(self.paths, settings, "enu", settings.language("enu").rows[0])
		library.save_settings(self.paths, settings)
		self.refresh()
		self.assertEqual(self.rows("enu"), [])
		self.assertEqual(os.listdir(self.paths.own), [])

	def test_nothing_to_import_is_remembered_too(self):
		self.refresh()
		self.assertTrue(library.load_settings(self.paths).legacy_imported)
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.refresh()
		self.assertEqual(self.rows("enu"), [])

	def test_imported_files_can_be_switched_off_for_a_clean_start(self):
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.refresh()
		settings = library.load_settings(self.paths)
		settings.language("enu").rows[0].on = False
		library.save_settings(self.paths, settings)
		self.assertTrue(self.refresh())
		self.assertEqual(self.active_names(), [])

	def test_a_set_from_an_installed_add_on_lands_below_the_imported_files(self):
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		provider = os.path.join(self.root, "addons", "eloquenceDictionaryManager")
		set_id = "github.eigencrow.ibmtts-dictionaries"
		dictionaries = os.path.join(provider, "dictionaries")
		_write(
			os.path.join(dictionaries, "contract.ini"),
			b"[contract]\nformat = eci-dictionary-sets\nversion = 1\n",
		)
		_write(
			os.path.join(dictionaries, "sets", set_id, "set.ini"),
			(
				f"[set]\nid = {set_id}\nname = IBMTTSDictionaries\nsource_url = https://example.invalid\n"
				"source_version = v1\nsource_revision = abc\nattribution = x\nlicense = CC0-1.0\n"
				"license_url = https://example.invalid/l\n"
			).encode(),
		)
		_write(
			os.path.join(dictionaries, "sets", set_id, "enuroot.dic"), _lines(f"cat\t{COW}", f"dog\t{DOG}")
		)
		library.refresh(self.paths, [provider], old_dictionary_directory=self.old_folder)
		rows = library.load_settings(self.paths).peek("enu").rows
		self.assertEqual([(row.kind, row.on) for row in rows], [("set", True), ("own", True)])
		# The user's old file, hand edits included, keeps winning over the set.
		self.assertEqual(self.active("enuroot.dic"), _lines(f"dog\t{DOG}", f"cat\t{CAT}"))


class BackupAtFirstLoadTests(MigrationTestCase):
	def test_files_still_in_the_add_on_folder_are_backed_up_and_imported(self):
		# What is left when the installer could not write the backup, and on a development copy.
		_write(os.path.join(self.old_folder, "enuroot.dic"), _lines(f"cat\t{CAT}"))
		_write(os.path.join(self.old_folder, "ECI.INI"), b"not a dictionary")
		self.refresh()
		self.assertEqual(os.listdir(self.paths.legacy), ["enuroot.dic"])
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_files_the_backup_does_not_hold_go_to_a_later_backup_and_are_imported(self):
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		_write(os.path.join(self.old_folder, "deuroot.dic"), _lines("hund\thund"))
		self.refresh()
		self.assertEqual(os.listdir(self.paths.legacy), ["enuroot.dic"])
		later = os.path.join(self.paths.later_legacy, "1")
		self.assertEqual(sorted(os.listdir(later)), [".imported", "deuroot.dic"])
		self.assertEqual(self.rows("enu"), [("enuroot.dic", "root", True, True)])
		self.assertEqual(self.rows("deu"), [("deuroot.dic", "root", True, True)])
		self.assertEqual(self.active("deuroot.dic"), _lines("hund\thund"))

	def test_files_a_backup_already_holds_are_not_backed_up_again(self):
		# What every start after an update sees: the old files carried into the add-on folder.
		self.legacy(enuroot_dic=_lines(f"cat\t{CAT}"))
		_write(os.path.join(self.old_folder, "ENUROOT.dic"), _lines(f"cat\t{CAT}"))
		self.refresh()
		_new_start()
		self.refresh()
		self.assertFalse(os.path.exists(self.paths.later_legacy))
		self.assertEqual(self.rows("enu"), [("enuroot.dic", "root", True, True)])

	def test_a_backup_that_fails_is_tried_again_next_start_and_logged_once(self):
		_write(os.path.join(self.old_folder, "enuroot.dic"), _lines(f"cat\t{CAT}"))
		with (
			mock.patch.object(library.shutil, "copy2", side_effect=OSError("disk full")),
			self.assertLogs(library.log, "ERROR") as logs,
		):
			self.refresh()
			self.refresh()
		self.assertEqual(len(logs.records), 1)
		self.assertFalse(library.load_settings(self.paths).legacy_imported)
		_new_start()
		self.refresh()
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))


class ImportThatCannotFinishTests(MigrationTestCase):
	@unittest.skipUnless(sys.platform == "win32", "a Windows sharing violation")
	def test_a_file_locked_at_the_first_start_is_imported_at_a_later_one(self):
		self.legacy(enumain_dic=_lines("WHO\twho"), enuroot_dic=_lines(f"cat\t{CAT}"))
		with (
			self.assertLogs(library.log, "ERROR") as logs,
			held_open(os.path.join(self.paths.legacy, "enumain.dic"), share=0),
		):
			self.refresh()
			self.refresh()
		self.assertEqual(len(logs.records), 1)
		self.assertFalse(library.load_settings(self.paths).legacy_imported)
		self.assertEqual(self.rows("enu"), [])
		self.assertFalse(os.path.exists(self.paths.own))
		self.refresh()
		self.assertEqual(
			self.rows("enu"), [("enuroot.dic", "root", True, True), ("enumain.dic", "main", True, True)]
		)
		self.assertEqual(self.active("enumain.dic"), _lines("WHO\twho"))

	@unittest.skipUnless(sys.platform == "win32", "a Windows sharing violation")
	def test_an_import_whose_save_fails_leaves_no_copies_to_be_imported_twice(self):
		library.save_settings(self.paths, library.Settings())
		self.legacy(enumain_dic=_lines("WHO\twho"))
		# Open without delete sharing, so dictionaries.json cannot be replaced.
		with (
			held_open(self.paths.settings_file, share=FILE_SHARE_READ),
			self.assertRaises(PermissionError),
		):
			self.refresh()
		self.assertEqual(os.listdir(self.paths.own), [])
		self.refresh()
		self.assertEqual(os.listdir(self.paths.own), ["enumain.dic"])
		self.assertEqual(self.rows("enu"), [("enumain.dic", "main", True, True)])


def _old_version_on_install(addon):
	"""What versions before the dictionary library did on install: carry the .dic files across."""
	source = os.path.join(addon.installPath, "synthDrivers", "eloquence")
	target = os.path.join(addon.path, "synthDrivers", "eloquence")
	os.makedirs(target, exist_ok=True)
	for name in os.listdir(source):
		if name.lower().endswith(".dic"):
			shutil.copy2(os.path.join(source, name), os.path.join(target, name))


class RollbackTests(MigrationTestCase):
	"""Updating, going back to an older version, and updating again, as NVDA's add-on handler does it.

	The incoming version is unpacked beside the installed one and its onInstall runs; at the next
	restart the installed folder is deleted and the new one takes its place.
	"""

	def setUp(self):
		super().setUp()
		self.installed = os.path.dirname(os.path.dirname(self.old_folder))
		self.config = self.paths.config_directory

	def install(self, on_install):
		pending = self.installed + ".pendingInstall"
		os.makedirs(os.path.join(pending, "synthDrivers", "eloquence"))
		on_install(types.SimpleNamespace(path=pending, installPath=self.installed))
		shutil.rmtree(self.installed)
		os.replace(pending, self.installed)
		_new_start()

	def this_version_on_install(self, addon):
		_run_install_tasks(addon, self.config, _FakeLog())

	def test_files_made_after_going_back_are_imported_at_the_next_update(self):
		_write(os.path.join(self.old_folder, "enumain.dic"), _lines("WHO\twho"))
		self.install(self.this_version_on_install)
		self.refresh()
		self.assertEqual(self.rows("enu"), [("enumain.dic", "main", True, True)])

		self.install(_old_version_on_install)
		# The older version still has its dictionaries.
		with open(os.path.join(self.old_folder, "enumain.dic"), "rb") as f:
			self.assertEqual(f.read(), _lines("WHO\twho"))
		# Where it keeps the files its own Update Dictionaries and editing produce.
		_write(os.path.join(self.old_folder, "enumain.dic"), _lines("WHO\twho", "omg\toh my god"))
		_write(os.path.join(self.old_folder, "enuroot.dic"), _lines(f"cat\t{CAT}"))

		self.install(self.this_version_on_install)
		self.refresh()
		self.assertEqual(os.listdir(self.paths.legacy), ["enumain.dic"])
		with open(os.path.join(self.paths.legacy, "enumain.dic"), "rb") as f:
			self.assertEqual(f.read(), _lines("WHO\twho"))
		self.assertEqual(os.listdir(self.paths.later_legacy), ["1"])
		self.assertEqual(
			self.rows("enu"),
			[
				("enumain.dic", "main", True, True),
				("enuroot.dic", "root", True, True),
				("enumain.dic", "main", True, True),
			],
		)
		self.assertEqual(self.active("enumain.dic"), _lines("WHO\twho", "omg\toh my god"))
		self.assertEqual(self.active("enuroot.dic"), _lines(f"cat\t{CAT}"))

		# Nothing is imported twice: not at the next start, and not at an update with no going back.
		self.refresh()
		self.install(self.this_version_on_install)
		self.refresh()
		self.assertEqual(os.listdir(self.paths.later_legacy), ["1"])
		self.assertEqual(len(self.rows("enu")), 3)


def _run_install_tasks(addon, config, log):
	"""Run this version's installTasks.onInstall with NVDA's modules stood in for."""
	stubs = {
		"addonHandler": types.SimpleNamespace(getCodeAddon=lambda: addon),
		"logHandler": types.SimpleNamespace(log=log),
		"globalVars": types.SimpleNamespace(appArgs=types.SimpleNamespace(configPath=config)),
	}
	had_translation = hasattr(builtins, "_")
	with mock.patch.dict(sys.modules, stubs):
		sys.modules.pop("addon.installTasks", None)
		install_tasks = importlib.import_module("addon.installTasks")
		try:
			install_tasks.onInstall()
		finally:
			sys.modules.pop("addon.installTasks", None)
			if not had_translation and hasattr(builtins, "_"):
				del builtins._
	return install_tasks


class _FakeLog:
	def __init__(self):
		self.messages = []

	def info(self, *args, **kwargs):
		self.messages.append(("info", args))

	def error(self, *args, **kwargs):
		self.messages.append(("error", args))


class InstallTaskTests(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		root = temporary.name
		self.config = os.path.join(root, "config")
		self.installed = os.path.join(root, "addons", "Eloquence")
		self.pending = os.path.join(root, "addons", "Eloquence.pendingInstall")
		self.installed_data = os.path.join(self.installed, "synthDrivers", "eloquence")
		self.pending_data = os.path.join(self.pending, "synthDrivers", "eloquence")
		os.makedirs(self.pending_data)
		self.legacy = os.path.join(self.config, "eciDictionaries", "legacy", "eloquence")
		self.log = _FakeLog()
		self.addon = types.SimpleNamespace(path=self.pending, installPath=self.installed)

	def on_install(self):
		return _run_install_tasks(self.addon, self.config, self.log)

	def errors(self):
		return [message for message in self.log.messages if message[0] == "error"]

	def test_the_old_dictionary_files_are_backed_up_into_the_configuration(self):
		_write(os.path.join(self.installed_data, "ENUmain.dic"), b"user dictionary")
		_write(os.path.join(self.installed_data, "enuroot.dic"), b"roots")
		_write(os.path.join(self.installed_data, "ECI.INI"), b"installed ini")
		_write(os.path.join(self.installed_data, "ENU.SYN"), b"installed voice data")
		self.on_install()
		self.assertEqual(sorted(os.listdir(self.legacy)), ["ENUmain.dic", "enuroot.dic"])
		with open(os.path.join(self.legacy, "ENUmain.dic"), "rb") as f:
			self.assertEqual(f.read(), b"user dictionary")
		self.assertEqual(self.errors(), [])

	def test_the_old_files_are_carried_into_the_new_add_on_folder_too(self):
		# So that going back to an older version finds them where it loads them from.
		_write(os.path.join(self.installed_data, "enumain.dic"), b"user dictionary")
		_write(os.path.join(self.installed_data, "ENU.SYN"), b"installed voice data")
		self.on_install()
		self.assertEqual(os.listdir(self.pending_data), ["enumain.dic"])
		self.assertEqual(os.listdir(self.legacy), ["enumain.dic"])

	def test_the_backup_is_written_once_and_never_changed(self):
		_write(os.path.join(self.legacy, "enumain.dic"), b"first backup")
		_write(os.path.join(self.installed_data, "enumain.dic"), b"something newer")
		_write(os.path.join(self.installed_data, "deumain.dic"), b"another file")
		self.on_install()
		self.assertEqual(os.listdir(self.legacy), ["enumain.dic"])
		with open(os.path.join(self.legacy, "enumain.dic"), "rb") as f:
			self.assertEqual(f.read(), b"first backup")

	def test_a_first_installation_does_nothing(self):
		self.addon.installPath = self.pending
		self.on_install()
		self.assertFalse(os.path.exists(self.config))

	def test_an_old_version_without_dictionaries_leaves_no_empty_backup(self):
		_write(os.path.join(self.installed_data, "ECI.INI"), b"installed ini")
		self.on_install()
		self.assertFalse(os.path.exists(self.legacy))

	def test_a_failed_backup_does_not_fail_the_install_and_carries_the_files_forward(self):
		_write(os.path.join(self.installed_data, "enumain.dic"), b"user dictionary")
		_write(os.path.join(self.installed_data, "ENU.SYN"), b"installed voice data")
		# A file where the configuration's folder should be: the backup cannot be created.
		_write(os.path.join(self.config, "eciDictionaries"), b"in the way")
		self.on_install()
		self.assertEqual(len(self.errors()), 1)
		# Kept beside the engine, where the synth backs them up from on its first start.
		self.assertEqual(os.listdir(self.pending_data), ["enumain.dic"])

	def test_no_half_copied_backup_is_left_behind(self):
		_write(os.path.join(self.installed_data, "enumain.dic"), b"user dictionary")
		with mock.patch("shutil.copy2", side_effect=OSError("disk full")):
			self.on_install()
		self.assertFalse(os.path.exists(self.legacy))
		self.assertTrue(self.errors())


if __name__ == "__main__":
	unittest.main()
