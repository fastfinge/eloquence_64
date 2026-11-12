import json
import os
import tempfile
import unittest

from addon.synthDrivers import _dictionary_library as library
from addon.synthDrivers import _dictionary_sets as sets
from addon.synthDrivers import _dictionary_stack as stack
from addon.synthDrivers import _eci_engine
from addon.synthDrivers._dictionary_library import Row

CAT, DOG, COW = "`[.1kAt]", "`[.1dOg]", "`[.1kW]"


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


class LibraryTestCase(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.paths = library.DictionaryPaths(temporary.name)
		self.settings = library.Settings()
		self.sets = {}

	def _set(self, set_id, files, installed=False):
		"""Make a downloaded set holding {file name: bytes} and register it."""
		directory = os.path.join(self.paths.sources, set_id)
		os.makedirs(directory, exist_ok=True)
		sets.write_set_ini(directory, set_id, set_id, "https://github.com/example/example")
		for name, data in files.items():
			with open(os.path.join(directory, name), "wb") as f:
				f.write(data)
		found = sets.read_set(directory, installed=False)
		self.sets[set_id] = sets.DictionarySet(**{**found.__dict__, "installed": installed})
		return self.sets[set_id]

	def _personal(self, name, data):
		os.makedirs(self.paths.personal, exist_ok=True)
		with open(os.path.join(self.paths.personal, name), "wb") as f:
			f.write(data)

	def _switch_on(self, language, *set_ids):
		for set_id in set_ids:
			self.settings.language(language).rows.append(Row("set", set_id, on=True))

	def _build(self, languages=("enu",)):
		return library.build(self.paths, self.settings, self.sets, languages)

	def _active(self, name):
		with open(os.path.join(self.paths.active, name), "rb") as f:
			return f.read()

	def _active_names(self):
		return sorted(name for name in os.listdir(self.paths.active) if name.endswith(".dic"))


class LanguageTableTests(unittest.TestCase):
	def test_every_engine_language_has_a_stack(self):
		self.assertEqual(set(library.LANGUAGES), set(_eci_engine.LANGS))

	def test_the_dialects_are_the_ones_the_engine_used_to_fall_back_for(self):
		self.assertEqual(library.DIALECT_BASES, {"eng": "enu", "esm": "esp", "frc": "fra", "chs": "enu"})


class SettingsTests(LibraryTestCase):
	def test_no_file_means_nothing_is_switched_on(self):
		settings = library.load_settings(self.paths)
		self.assertEqual(settings.languages, {})
		self.assertTrue(settings.language("enu").personal)
		self.assertTrue(settings.language("eng").dialect)

	def test_a_new_installation_lists_the_two_known_sources(self):
		self.assertEqual(
			library.load_settings(self.paths).sources,
			["eigencrow/IBMTTSDictionaries", "Mohamed00/AltIBMTTSDictionaries"],
		)

	def test_removing_every_source_sticks(self):
		self.settings.sources.clear()
		library.save_settings(self.paths, self.settings)
		self.assertEqual(library.load_settings(self.paths).sources, [])

	def test_removing_a_sets_rows_leaves_the_other_rows(self):
		self.settings.language("enu").rows += [Row("set", "github.a.b", on=True), Row("set", "github.c.d")]
		self.settings.language("deu").rows += [
			Row("set", "github.a.b"),
			Row("own", "github.a.b", slot="root"),
		]
		library.remove_set_rows(self.settings, ["github.a.b"])
		self.assertEqual(self.settings.language("enu").rows, [Row("set", "github.c.d")])
		self.assertEqual(self.settings.language("deu").rows, [Row("own", "github.a.b", slot="root")])

	def test_settings_survive_a_save_and_load(self):
		language = self.settings.language("eng")
		language.rows += [
			Row("set", "github.a.b", on=True),
			Row("own", "words.dic", slot="root", label="My words.dic", imported=True),
		]
		language.dialect = False
		self.settings.seen_sets.add("github.a.b")
		self.settings.sources.append("example/Repo")
		library.save_settings(self.paths, self.settings)
		self.assertEqual(library.load_settings(self.paths), self.settings)

	def test_the_file_holds_no_absolute_paths(self):
		# A configuration can be copied to another machine or a portable copy (issue #31).
		self._set("github.a.b", {"enuroot.dic": _lines(f"cat\t{CAT}")})
		library.reconcile_sets(self.settings, self.sets)
		library.add_own_file(self.paths, self.settings, "enu", "root", _lines(f"dog\t{DOG}"), "words.dic")
		library.save_settings(self.paths, self.settings)
		with open(self.paths.settings_file, encoding="utf-8") as f:
			text = f.read()
		self.assertNotIn(self.paths.config_directory.replace("\\", "\\\\"), text)
		self.assertNotIn(os.sep + "own", text)

	def test_an_unreadable_file_falls_back_to_the_defaults(self):
		os.makedirs(self.paths.data)
		with open(self.paths.settings_file, "w", encoding="utf-8") as f:
			f.write("{ not json")
		with self.assertLogs(library.log, "ERROR"):
			self.assertEqual(library.load_settings(self.paths), library.Settings())


class ReconcileTests(LibraryTestCase):
	def test_a_downloaded_set_gets_a_row_that_is_off(self):
		self._set("github.a.b", {"enuroot.dic": b"", "deuroot.dic": b""})
		self.assertTrue(library.reconcile_sets(self.settings, self.sets))
		self.assertEqual(self.settings.language("enu").rows, [Row("set", "github.a.b", on=False)])
		self.assertEqual(self.settings.language("deu").rows, [Row("set", "github.a.b", on=False)])
		self.assertNotIn("fra", self.settings.languages)

	def test_an_installed_set_is_switched_on_the_first_time_it_is_seen(self):
		self._set("github.a.b", {"enuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self.assertEqual(self.settings.language("enu").rows, [Row("set", "github.a.b", on=True)])

	def test_switching_an_installed_set_off_sticks(self):
		self._set("github.a.b", {"enuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self.settings.language("enu").rows[0].on = False
		self.assertFalse(library.reconcile_sets(self.settings, self.sets))
		self.assertFalse(self.settings.language("enu").rows[0].on)

	def test_a_set_seen_before_is_not_switched_on_after_its_row_was_lost(self):
		self._set("github.a.b", {"enuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self.settings.language("enu").rows.clear()
		library.reconcile_sets(self.settings, self.sets)
		self.assertEqual(self.settings.language("enu").rows, [Row("set", "github.a.b", on=False)])

	def test_a_second_installed_set_stays_off_where_one_is_already_on(self):
		self._set("github.a.first", {"enuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self._set("github.a.second", {"enuroot.dic": b"", "deuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self.assertEqual([row.on for row in self.settings.language("enu").rows], [True, False])
		self.assertEqual([row.on for row in self.settings.language("deu").rows], [True])

	def test_a_row_for_a_set_that_went_missing_is_kept(self):
		self._set("github.a.b", {"enuroot.dic": b""}, installed=True)
		library.reconcile_sets(self.settings, self.sets)
		self.assertFalse(library.reconcile_sets(self.settings, {}))
		self.assertEqual(self.settings.language("enu").rows, [Row("set", "github.a.b", on=True)])


class OwnFileTests(LibraryTestCase):
	def test_the_slot_comes_from_the_file_name_when_it_says(self):
		cases = {
			"ENURoot.dic": "root",
			"enumain.dic": "main",
			"abbr.dic": "abbr",
			"my-words-main.DIC": "main",
			"words.dic": None,
			"mainly.dic": None,
			"root.txt": None,
		}
		for name, expected in cases.items():
			with self.subTest(name=name):
				self.assertEqual(library.guess_slot(name), expected)

	def test_an_added_file_is_copied_and_goes_last_in_the_stack(self):
		self._switch_on("enu", "github.a.b")
		data = _lines(f"dog\t{DOG}")
		row = library.add_own_file(
			self.paths, self.settings, "enu", "root", data, r"C:\somewhere\My words.dic"
		)
		self.assertEqual(self.settings.language("enu").rows[-1], row)
		self.assertEqual((row.kind, row.slot, row.on, row.imported), ("own", "root", True, False))
		with open(os.path.join(self.paths.own, row.id), "rb") as f:
			self.assertEqual(f.read(), data)

	def test_two_files_of_the_same_name_do_not_collide(self):
		first = library.add_own_file(self.paths, self.settings, "enu", "root", b"a\tb\r\n", "words.dic")
		second = library.add_own_file(self.paths, self.settings, "deu", "root", b"c\td\r\n", "WORDS.dic")
		self.assertNotEqual(first.id.lower(), second.id.lower())
		self.assertEqual(len(os.listdir(self.paths.own)), 2)

	def test_removing_a_file_removes_its_row_and_its_copy(self):
		row = library.add_own_file(self.paths, self.settings, "enu", "root", b"a\tb\r\n", "words.dic")
		library.remove_own_file(self.paths, self.settings, "enu", row)
		self.assertEqual(self.settings.language("enu").rows, [])
		self.assertEqual(os.listdir(self.paths.own), [])

	def test_checking_a_file_reports_without_adding_anything(self):
		report = library.check_file(_lines("IDK\tI don't know"), "enu", "abbr")
		self.assertEqual(len(report.reports), 1)
		with self.assertRaises(stack.DictionaryFileError):
			library.check_file(_lines("no tab here"), "enu", "main")
		self.assertFalse(os.path.exists(self.paths.own))

	def test_an_asian_file_is_only_checked_for_size(self):
		self.assertEqual(library.check_file("単語\tたんご\r\n".encode("cp932"), "jpn", "main").reports, ())


class BuildTests(LibraryTestCase):
	def test_nothing_switched_on_builds_no_files(self):
		self._set("github.a.b", {"enuroot.dic": _lines(f"cat\t{CAT}")})
		library.reconcile_sets(self.settings, self.sets)
		outcomes = self._build()
		self.assertEqual(self._active_names(), [])
		self.assertEqual(outcomes["enu"], library.LanguageOutcome())

	def test_rows_stack_in_order_and_the_later_one_wins(self):
		self._set("github.a.first", {"enuroot.dic": _lines(f"cat\t{CAT}", f"dog\t{DOG}")})
		self._set("github.a.second", {"enuroot.dic": _lines(f"cat\t{COW}")})
		self._switch_on("enu", "github.a.first", "github.a.second")
		self._build()
		self.assertEqual(self._active("enuroot.dic"), _lines(f"dog\t{DOG}", f"cat\t{COW}"))
		self.settings.language("enu").rows.reverse()
		self._build()
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}", f"dog\t{DOG}"))

	def test_a_row_that_is_off_contributes_nothing(self):
		self._set("github.a.first", {"enuroot.dic": _lines(f"cat\t{CAT}")})
		self._set("github.a.second", {"enuroot.dic": _lines(f"cat\t{COW}")})
		self._switch_on("enu", "github.a.first", "github.a.second")
		self.settings.language("enu").rows[1].on = False
		self._build()
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_personal_entries_win_over_every_row(self):
		self._set("github.a.b", {"enuroot.dic": _lines(f"cat\t{CAT}"), "enumain.dic": _lines("GUI\tgooey")})
		self._switch_on("enu", "github.a.b")
		library.add_own_file(self.paths, self.settings, "enu", "root", _lines(f"cat\t{DOG}"), "words.dic")
		self._personal("enuroot.dic", _lines(f"cat\t{COW}", "gui\tgee you eye".replace(" ", "")))
		self._build()
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{COW}", "gui\tgeeyoueye"))
		# The personal root entry also removes the set's main entry, which would otherwise beat it.
		self.assertEqual(self._active_names(), ["enuroot.dic"])

	def test_personal_entries_can_be_switched_off(self):
		self._personal("enuroot.dic", _lines(f"cat\t{COW}"))
		self.settings.language("enu").personal = False
		self._build()
		self.assertEqual(self._active_names(), [])

	def test_a_missing_set_is_left_out_and_nothing_takes_its_place(self):
		self._set("github.a.present", {"enuroot.dic": _lines(f"cat\t{CAT}")})
		self._switch_on("enu", "github.a.gone", "github.a.present")
		with self.assertLogs(library.log, "WARNING"):
			outcomes = self._build()
		self.assertEqual(outcomes["enu"].error, "")
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_entries_that_may_not_take_effect_are_counted_per_row(self):
		self._set(
			"github.a.b", {"enuabbr.dic": _lines("IDK\tI don't know", "AT&T\tA T and T", "Dr.\tdoctor")}
		)
		self._switch_on("enu", "github.a.b")
		outcomes = self._build()
		self.assertEqual(outcomes["enu"].reported, {"set:github.a.b": 2})
		self.assertEqual(len(self._active("enuabbr.dic").splitlines()), 3)


class DialectTests(LibraryTestCase):
	def setUp(self):
		super().setUp()
		self._set(
			"github.a.b",
			{"enuroot.dic": _lines(f"cat\t{CAT}", f"dog\t{DOG}"), "engroot.dic": _lines(f"cat\t{COW}")},
		)
		self._switch_on("enu", "github.a.b")
		self._switch_on("eng", "github.a.b")

	def test_a_dialect_stacks_its_own_dictionaries_on_the_main_languages(self):
		self._build(("enu", "eng"))
		self.assertEqual(self._active("engroot.dic"), _lines(f"dog\t{DOG}", f"cat\t{COW}"))
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}", f"dog\t{DOG}"))

	def test_the_dialect_checkbox_switches_the_main_language_off(self):
		self.settings.language("eng").dialect = False
		self._build(("enu", "eng"))
		self.assertEqual(self._active("engroot.dic"), _lines(f"cat\t{COW}"))

	def test_a_dialect_with_nothing_of_its_own_gets_the_main_languages_files(self):
		self.settings.language("eng").rows.clear()
		self._build(("enu", "eng"))
		self.assertEqual(self._active("engroot.dic"), self._active("enuroot.dic"))

	def test_a_main_language_row_that_is_off_does_not_reach_the_dialect(self):
		self.settings.language("enu").rows[0].on = False
		self._build(("enu", "eng"))
		self.assertEqual(self._active("engroot.dic"), _lines(f"cat\t{COW}"))

	def test_both_personal_layers_come_last_and_the_dialects_own_wins(self):
		self._personal("enuroot.dic", _lines("cat\tenupersonal", "dog\tenupersonal"))
		self._personal("engroot.dic", _lines("dog\tengpersonal"))
		self._build(("enu", "eng"))
		self.assertEqual(self._active("engroot.dic"), _lines("cat\tenupersonal", "dog\tengpersonal"))

	def test_the_other_dialects_follow_their_main_language(self):
		self._set("github.c.d", {"esproot.dic": _lines("gato\tgato"), "fraroot.dic": _lines("chat\tchat")})
		self._switch_on("esp", "github.c.d")
		self._switch_on("fra", "github.c.d")
		self._build(("esp", "esm", "fra", "frc", "deu"))
		self.assertEqual(self._active("esmroot.dic"), _lines("gato\tgato"))
		self.assertEqual(self._active("frcroot.dic"), _lines("chat\tchat"))
		self.assertNotIn("deuroot.dic", self._active_names())


class AsianLanguageTests(LibraryTestCase):
	def test_files_are_copied_byte_for_byte_and_the_last_row_wins(self):
		first, second = "単語\tたんご\r\n".encode("cp932"), "言葉\tことば\r\n\r\n".encode("cp932")
		self._set("github.a.first", {"jpnmain.dic": first, "jpnroot.dic": first})
		self._set("github.a.second", {"jpnmain.dic": second})
		self._switch_on("jpn", "github.a.first", "github.a.second")
		self._build(("jpn",))
		self.assertEqual(self._active("jpnmain.dic"), second)
		self.assertEqual(self._active("jpnroot.dic"), first)

	def test_chinese_uses_american_english_files_for_the_slots_it_lacks(self):
		chinese = "词\tci\r\n".encode("gb18030")
		self._set(
			"github.a.b",
			{"chsmain.dic": chinese, "enumain.dic": _lines("WHO\twho"), "enuroot.dic": _lines(f"cat\t{CAT}")},
		)
		self._switch_on("chs", "github.a.b")
		self._switch_on("enu", "github.a.b")
		self._build(("chs", "enu"))
		self.assertEqual(self._active("chsmain.dic"), chinese)
		self.assertEqual(self._active("chsroot.dic"), _lines(f"cat\t{CAT}"))
		self.settings.language("chs").dialect = False
		self._build(("chs", "enu"))
		self.assertNotIn("chsroot.dic", self._active_names())


class FailureTests(LibraryTestCase):
	def setUp(self):
		super().setUp()
		self._set("github.a.b", {"enuroot.dic": _lines(f"cat\t{CAT}"), "deuroot.dic": _lines("hund\thund")})
		self._switch_on("enu", "github.a.b")
		self._switch_on("deu", "github.a.b")
		self._build(("enu", "deu"))

	def test_a_rejected_file_keeps_that_languages_last_good_files(self):
		self._personal("enuroot.dic", _lines(f"dog\t{DOG}", "", "this line has no tab"))
		self._personal("deuroot.dic", _lines("katze\tkatze"))
		with self.assertLogs(library.log, "ERROR"):
			outcomes = self._build(("enu", "deu"))
		self.assertEqual(
			(
				outcomes["enu"].error,
				outcomes["enu"].error_source,
				outcomes["enu"].error_slot,
				outcomes["enu"].error_line,
			),
			("noTab", "personal", "root", 3),
		)
		self.assertEqual(outcomes["enu"].slots, ("root",))
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}"))
		# The other language is still rebuilt.
		self.assertEqual(outcomes["deu"].error, "")
		self.assertEqual(self._active("deuroot.dic"), _lines("hund\thund", "katze\tkatze"))

	def test_a_stack_too_large_for_the_engine_keeps_the_last_good_files(self):
		value = "a" * 500
		for name in ("first", "second"):
			lines = [
				f"{name}{library.SLOTS[0]}{number:07d}".translate(_LETTERS) + f"\t{value}"
				for number in range(8300)
			]
			library.add_own_file(self.paths, self.settings, "enu", "root", _lines(*lines), f"{name}.dic")
		with self.assertLogs(library.log, "ERROR"):
			outcomes = self._build(("enu",))
		self.assertEqual((outcomes["enu"].error, outcomes["enu"].error_slot), ("stackTooLarge", "root"))
		self.assertEqual(self._active("enuroot.dic"), _lines(f"cat\t{CAT}"))

	def test_the_failure_is_recorded_for_the_dialog(self):
		self._personal("enuroot.dic", _lines("no tab"))
		with self.assertLogs(library.log, "ERROR"):
			self._build(("enu", "deu"))
		record = library.read_build_record(self.paths)
		self.assertEqual(record["languages"]["enu"]["error"], "noTab")
		self.assertEqual(record["languages"]["deu"]["error"], "")

	def test_no_half_built_folder_is_left_behind(self):
		self._build(("enu", "deu"))
		self.assertEqual(sorted(os.listdir(self.paths.data)), ["active", "sources"])


_LETTERS = str.maketrans("0123456789", "abcdefghij")


class StalenessTests(LibraryTestCase):
	def setUp(self):
		super().setUp()
		self._set("github.a.b", {"enuroot.dic": _lines(f"cat\t{CAT}")})
		self._switch_on("enu", "github.a.b")

	def _stale(self):
		return library.is_stale(self.paths, self.settings, self.sets)

	def test_it_is_stale_before_the_first_build_and_fresh_after(self):
		self.assertTrue(self._stale())
		self._build()
		self.assertFalse(self._stale())

	def test_a_changed_selection_makes_it_stale(self):
		self._build()
		self.settings.language("enu").rows[0].on = False
		self.assertTrue(self._stale())

	def test_an_edit_saved_by_a_dictionary_editor_makes_it_stale(self):
		self._build()
		self._personal("enuroot.dic", _lines(f"dog\t{DOG}"))
		self.assertTrue(self._stale())
		self._build()
		self.assertFalse(self._stale())
		self._personal("enuroot.dic", _lines(f"dog\t{DOG}", f"cow\t{COW}"))
		self.assertTrue(self._stale())

	def test_a_changed_set_file_makes_it_stale(self):
		self._build()
		with open(self.sets["github.a.b"].files["enu", "root"], "ab") as f:
			f.write(_lines(f"dog\t{DOG}"))
		self.assertTrue(self._stale())

	def test_a_language_that_failed_is_not_rebuilt_over_and_over(self):
		self._personal("enuroot.dic", _lines("no tab"))
		with self.assertLogs(library.log, "ERROR"):
			self._build()
		self.assertFalse(self._stale())

	def test_the_build_record_is_plain_json(self):
		self._build()
		with open(os.path.join(self.paths.active, "build.json"), encoding="utf-8") as f:
			self.assertEqual(json.load(f)["generator"], library.GENERATOR_VERSION)


if __name__ == "__main__":
	unittest.main()
