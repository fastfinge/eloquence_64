"""The working copy behind the Manage dictionaries dialog."""

import os
import tempfile
import unittest
from unittest import mock

from addon.synthDrivers import _dictionary_library as library
from addon.synthDrivers import _dictionary_manager as manager_module
from addon.synthDrivers import _dictionary_sets as sets
from addon.synthDrivers import _dictionary_stack as stack
from addon.synthDrivers import _dictionary_update as dictionary_update
from addon.synthDrivers._dictionary_library import Row

CAT, DOG = "`[.1kAt]", "`[.1dOg]"
COMMUNITY = "github.eigencrow.ibmtts-dictionaries"
ALT = "github.mohamed00.alt-ibmtts-dictionaries"
ALT_FOLDER = ALT + "..ibmtts.206.2e7"


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def _write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as f:
		f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


class ManagerTestCase(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = temporary.name
		self.paths = library.DictionaryPaths(os.path.join(self.root, "config"))
		self.provider = os.path.join(self.root, "addons", "eloquenceDictionaryManager")

	def downloaded(self, set_id, name="Repo", **files):
		directory = os.path.join(self.paths.sources, set_id)
		os.makedirs(directory, exist_ok=True)
		sets.write_set_ini(directory, set_id, name, "https://github.com/example/example")
		for filename, data in files.items():
			_write(os.path.join(directory, filename.replace("_", ".")), data)

	def installed(self, set_id=COMMUNITY, **files):
		dictionaries = os.path.join(self.provider, "dictionaries")
		_write(
			os.path.join(dictionaries, "contract.ini"),
			"[contract]\nformat = eci-dictionary-sets\nversion = 1\n",
		)
		_write(
			os.path.join(dictionaries, "sets", set_id, "set.ini"),
			f"[set]\nid = {set_id}\nname = IBMTTSDictionaries\nsource_url = https://example.invalid\n"
			"source_version = v26.11\nsource_revision = abc\nattribution = x\nlicense = CC0-1.0\n"
			"license_url = https://example.invalid/l\n",
		)
		for filename, data in files.items():
			_write(os.path.join(dictionaries, "sets", set_id, filename.replace("_", ".")), data)

	def manager(self):
		return manager_module.DictionaryManager(self.paths, [self.provider])

	def saved(self):
		return library.load_settings(self.paths)

	def own_file(self, manager, language="enu", name="words-root.dic", lines=(f"cat\t{CAT}",)):
		path = os.path.join(self.root, name)
		_write(path, _lines(*lines))
		data, _report = manager.read_file(path, language, "root")
		return manager.add_file(language, "root", data, path)


class RowTests(ManagerTestCase):
	def test_a_new_installation_lists_nothing(self):
		self.assertEqual(self.manager().rows("enu"), [])

	def test_sets_are_listed_with_where_they_come_from(self):
		self.installed(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.downloaded(ALT, "AltIBMTTSDictionaries", enuroot_dic=_lines(f"cat\t{DOG}"))
		rows = self.manager().rows("enu")
		self.assertEqual(
			[(row.kind, row.name, row.version, row.installed, row.on) for row in rows],
			[
				("set", "IBMTTSDictionaries", "v26.11", True, True),
				("set", "AltIBMTTSDictionaries", "", False, False),
			],
		)
		self.assertEqual(self.manager().rows("deu"), [])

	def test_opening_the_dialog_saves_nothing(self):
		self.installed(enuroot_dic=_lines(f"cat\t{CAT}"))
		self.manager().rows("enu")
		self.assertFalse(os.path.exists(self.paths.settings_file))

	def test_a_set_that_went_missing_is_listed_as_unavailable(self):
		settings = library.Settings()
		settings.language("enu").rows.append(Row("set", "github.gone.away", on=True))
		library.save_settings(self.paths, settings)
		(row,) = self.manager().rows("enu")
		self.assertEqual((row.name, row.available, row.on), ("github.gone.away", False, True))

	def test_the_personal_row_is_last_and_only_there_when_there_are_entries(self):
		self.downloaded(ALT, enuroot_dic=_lines(f"cat\t{CAT}"))
		_write(os.path.join(self.paths.personal, "enumain.dic"), _lines("WHO\twho"))
		manager = self.manager()
		self.assertEqual([row.kind for row in manager.rows("enu")], ["set", "personal"])
		self.assertTrue(manager.rows("enu")[-1].on)
		self.assertEqual(manager.rows("deu"), [])

	def test_rows_say_how_many_entries_the_last_build_reported(self):
		self.downloaded(ALT, enuabbr_dic=_lines("IDK\tI don't know", "Dr.\tdoctor"))
		manager = self.manager()
		manager.set_on("enu", 0, True)
		manager.apply()
		library.refresh(self.paths, [self.provider])
		self.assertEqual(self.manager().rows("enu")[0].reported, 1)

	def test_a_language_that_could_not_be_built_says_why(self):
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines("no tab here"))
		with self.assertLogs(library.log, "ERROR"):
			library.refresh(self.paths, [self.provider])
		error = self.manager().build_error("enu")
		self.assertEqual(
			(error["error"], error["error_source"], error["error_line"]), ("noTab", "personal", 1)
		)
		self.assertIsNone(self.manager().build_error("deu"))


class StackEditingTests(ManagerTestCase):
	def setUp(self):
		super().setUp()
		self.downloaded(COMMUNITY, "Community", enuroot_dic=_lines(f"cat\t{CAT}"))
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines("cow\tcow"))
		self.manager_ = self.manager()

	def names(self):
		return [row.name or row.kind for row in self.manager_.rows("enu")]

	def test_switching_rows_on_and_off_changes_only_the_working_copy_until_ok(self):
		self.manager_.set_on("enu", 0, True)
		self.assertTrue(self.manager_.rows("enu")[0].on)
		self.assertFalse(os.path.exists(self.paths.settings_file))
		self.manager_.apply()
		self.assertEqual([row.on for row in self.saved().peek("enu").rows], [True, False])

	def test_the_personal_row_can_be_switched_off(self):
		self.manager_.set_on("enu", 2, False)
		self.manager_.apply()
		self.assertFalse(self.saved().peek("enu").personal)

	def test_rows_move_up_and_down_and_stop_at_the_ends(self):
		# New sets are listed in the order of their IDs.
		self.assertEqual(self.names(), ["Community", "Alt", "personal"])
		self.assertEqual(self.manager_.move("enu", 0, +1), 1)
		self.assertEqual(self.names(), ["Alt", "Community", "personal"])
		self.assertEqual(self.manager_.move("enu", 1, +1), 1)
		self.assertEqual(self.manager_.move("enu", 0, -1), 0)
		self.assertEqual(self.names(), ["Alt", "Community", "personal"])

	def test_the_personal_row_never_moves_and_nothing_moves_below_it(self):
		self.assertFalse(self.manager_.can_move("enu", 2, -1))
		self.assertFalse(self.manager_.can_move("enu", 1, +1))
		self.assertTrue(self.manager_.can_move("enu", 1, -1))

	def test_cancel_restores_the_saved_selections(self):
		self.manager_.set_on("enu", 0, True)
		self.manager_.move("enu", 0, +1)
		self.manager_.cancel()
		self.assertEqual(self.names(), ["Community", "Alt", "personal"])
		self.assertFalse(self.manager_.rows("enu")[0].on)

	def test_the_order_decides_which_entry_is_heard(self):
		self.manager_.set_on("enu", 0, True)
		self.manager_.set_on("enu", 1, True)
		self.manager_.apply()
		library.refresh(self.paths, [self.provider])
		# Alt is the later row, so its entry for cat is the one heard.
		with open(os.path.join(self.paths.active, "enuroot.dic"), "rb") as f:
			self.assertEqual(f.read(), _lines(f"cat\t{DOG}", "cow\tcow"))
		self.manager_.move("enu", 1, -1)
		self.manager_.apply()
		library.refresh(self.paths, [self.provider])
		with open(os.path.join(self.paths.active, "enuroot.dic"), "rb") as f:
			self.assertEqual(f.read(), _lines(f"cat\t{CAT}", "cow\tcow"))


class OwnFileTests(ManagerTestCase):
	def test_an_added_file_goes_last_switched_on(self):
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		manager = self.manager()
		index = self.own_file(manager)
		row = manager.rows("enu")[index]
		self.assertEqual(
			(index, row.kind, row.name, row.slot, row.on, row.imported),
			(1, "own", "words-root.dic", "root", True, False),
		)

	def test_reading_a_file_reports_its_entries_without_adding_it(self):
		path = os.path.join(self.root, "abbr.dic")
		_write(path, _lines("IDK\tI don't know"))
		manager = self.manager()
		_data, report = manager.read_file(path, "enu", "abbr")
		self.assertEqual(len(report.reports), 1)
		self.assertEqual(manager.rows("enu"), [])
		self.assertFalse(os.path.exists(self.paths.own))

	def test_a_rejected_file_raises_and_adds_nothing(self):
		path = os.path.join(self.root, "root.dic")
		_write(path, _lines("no tab here"))
		with self.assertRaises(stack.DictionaryFileError):
			self.manager().read_file(path, "enu", "root")

	def test_cancel_takes_back_a_file_added_in_this_session(self):
		manager = self.manager()
		self.own_file(manager)
		self.assertEqual(len(os.listdir(self.paths.own)), 1)
		manager.cancel()
		self.assertEqual(os.listdir(self.paths.own), [])
		self.assertEqual(manager.rows("enu"), [])

	def test_removing_a_saved_file_only_deletes_it_at_ok(self):
		manager = self.manager()
		self.own_file(manager)
		manager.apply()
		manager = self.manager()
		manager.remove("enu", 0)
		self.assertEqual(manager.rows("enu"), [])
		self.assertEqual(len(os.listdir(self.paths.own)), 1)
		manager.cancel()
		self.assertEqual(len(self.manager().rows("enu")), 1)
		manager = self.manager()
		manager.remove("enu", 0)
		manager.apply()
		self.assertEqual(os.listdir(self.paths.own), [])
		self.assertEqual(self.saved().peek("enu").rows, [])

	def test_a_file_added_and_removed_before_ok_leaves_nothing(self):
		manager = self.manager()
		manager.remove("enu", self.own_file(manager))
		self.assertEqual(os.listdir(self.paths.own), [])
		manager.apply()
		self.assertEqual(self.saved().peek("enu").rows, [])

	def test_only_the_users_own_files_can_be_removed_from_a_stack(self):
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines("cow\tcow"))
		manager = self.manager()
		self.own_file(manager)
		self.assertEqual([manager.can_remove("enu", index) for index in range(3)], [False, True, False])
		manager.remove("enu", 0)
		self.assertEqual(len(manager.rows("enu")), 3)

	def test_a_file_missing_from_the_own_folder_is_listed_as_unavailable(self):
		manager = self.manager()
		self.own_file(manager)
		manager.apply()
		os.remove(os.path.join(self.paths.own, os.listdir(self.paths.own)[0]))
		self.assertFalse(self.manager().rows("enu")[0].available)


class DialectTests(ManagerTestCase):
	def test_only_dialects_have_a_main_language(self):
		manager = self.manager()
		self.assertEqual(
			{code: manager.dialect_base(code) for code in ("enu", "eng", "esm", "frc", "chs", "deu")},
			{"enu": None, "eng": "enu", "esm": "esp", "frc": "fra", "chs": "enu", "deu": None},
		)

	def test_the_dialect_setting_is_on_until_switched_off(self):
		manager = self.manager()
		self.assertTrue(manager.uses_dialect_base("eng"))
		manager.set_uses_dialect_base("eng", False)
		manager.apply()
		self.assertFalse(self.saved().peek("eng").dialect)


class SourceTests(ManagerTestCase):
	def test_a_new_installation_lists_the_two_known_sources(self):
		self.assertEqual(
			self.manager().sources, ["eigencrow/IBMTTSDictionaries", "Mohamed00/AltIBMTTSDictionaries"]
		)

	def test_a_source_can_be_added_as_a_name_or_a_url(self):
		manager = self.manager()
		self.assertEqual(manager.add_source("https://github.com/example/Repo.git"), "example/Repo")
		self.assertEqual(manager.sources[-1], "example/Repo")
		manager.apply()
		self.assertEqual(self.saved().sources[-1], "example/Repo")

	def test_what_cannot_be_a_source_is_refused_with_a_reason(self):
		manager = self.manager()
		for text, reason in (
			("not a repository", "notARepository"),
			("EIGENCROW/ibmttsdictionaries", "alreadyListed"),
			("eigencrow/ibmtts-dictionaries", "cannotBeUsed"),
		):
			with self.subTest(text=text), self.assertRaises(ValueError) as raised:
				manager.add_source(text)
			self.assertEqual(str(raised.exception), reason)
		self.assertEqual(len(manager.sources), 2)

	def test_removing_a_source_takes_its_sets_out_of_every_stack_and_deletes_them_at_ok(self):
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"), esproot_dic=_lines("gato\tgato"))
		self.downloaded(ALT_FOLDER, "Alt/IBMTTS 6.7", enuroot_dic=_lines(f"cat\t{CAT}"))
		self.downloaded(COMMUNITY, "Community", enuroot_dic=_lines(f"cat\t{CAT}"))
		manager = self.manager()
		manager.remove_source(1)
		self.assertEqual([row.name for row in manager.rows("enu")], ["Community"])
		self.assertEqual(manager.rows("esp"), [])
		self.assertEqual(len(os.listdir(self.paths.sources)), 3)
		manager.apply()
		self.assertEqual(os.listdir(self.paths.sources), [COMMUNITY])
		self.assertEqual(self.saved().sources, ["eigencrow/IBMTTSDictionaries"])

	def test_cancelling_a_removal_keeps_the_source_and_its_sets(self):
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		manager = self.manager()
		manager.remove_source(1)
		manager.cancel()
		self.assertEqual(len(manager.sources), 2)
		self.assertEqual([row.name for row in self.manager().rows("enu")], ["Alt"])
		self.assertEqual(os.listdir(self.paths.sources), [ALT])

	def test_a_removed_source_does_not_come_back_when_the_sets_are_looked_for_again(self):
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		manager = self.manager()
		manager.remove_source(1)
		manager.refresh_sets()
		self.assertEqual(manager.rows("enu"), [])

	def test_removing_a_source_leaves_an_installed_add_ons_copy_alone(self):
		self.installed(enuroot_dic=_lines(f"cat\t{CAT}"))
		manager = self.manager()
		manager.remove_source(0)
		self.assertEqual([row.name for row in manager.rows("enu")], ["IBMTTSDictionaries"])

	def test_updating_downloads_every_listed_source_but_not_what_an_add_on_provides(self):
		self.installed(enuroot_dic=_lines(f"cat\t{CAT}"))
		manager = self.manager()
		with mock.patch.object(dictionary_update, "update_sources", return_value=()) as update:
			manager.update_sources()
		repositories, directory, installed = update.call_args.args[:3]
		self.assertEqual(repositories, manager.sources)
		self.assertEqual(directory, self.paths.sources)
		self.assertEqual(installed, {COMMUNITY})

	def test_sets_downloaded_by_an_update_get_rows_that_are_off(self):
		manager = self.manager()
		self.assertEqual(manager.rows("enu"), [])
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		manager.refresh_sets()
		self.assertEqual([(row.name, row.on) for row in manager.rows("enu")], [("Alt", False)])

	def test_cancel_takes_back_the_download_of_a_source_added_in_this_session(self):
		manager = self.manager()
		manager.add_source("example/Repo")
		self.downloaded("github.example.repo", enuroot_dic=_lines(f"cat\t{CAT}"))
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))
		manager.refresh_sets()
		manager.cancel()
		self.assertEqual(os.listdir(self.paths.sources), [ALT])
		self.assertEqual(manager.sources, ["eigencrow/IBMTTSDictionaries", "Mohamed00/AltIBMTTSDictionaries"])


if __name__ == "__main__":
	unittest.main()
