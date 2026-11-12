"""The Manage dictionaries dialog, built with the real wxPython and NVDA's own guiHelper.

Nothing here is a stand-in for a control: the dialog is constructed as NVDA would construct it and
its controls are driven through their events. Only the questions that would block (file pickers,
message boxes) are answered by the test.

Needs wxPython and NVDA's source tree, and skips without them. Say where the source is with
NVDA_SOURCE, for example NVDA_SOURCE=C:\\git\\nvda\\source. guiHelper is the one NVDA module loaded
from it; it depends on nothing but wx.
"""

import builtins
import importlib
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from addon.synthDrivers import _dictionary_library as library
from addon.synthDrivers import _dictionary_manager as manager_module
from addon.synthDrivers import _dictionary_sets as sets
from addon.synthDrivers._dictionary_update import DownloadedFile, DownloadedSet, FileStatus, SourceResult

try:
	import wx
except ImportError:
	wx = None

_GUI_HELPER = os.path.join(os.environ.get("NVDA_SOURCE", ""), "gui", "guiHelper.py")
CAT, DOG = "`[.1kAt]", "`[.1dOg]"
COMMUNITY = "github.eigencrow.ibmtts-dictionaries"
ALT = "github.mohamed00.alt-ibmtts-dictionaries"


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def _write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as f:
		f.write(data)


@unittest.skipUnless(wx is not None and hasattr(wx, "App"), "needs wxPython")
@unittest.skipUnless(os.path.isfile(_GUI_HELPER), "needs NVDA's source: set NVDA_SOURCE to its source folder")
class DialogTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.app = wx.App(False)
		spec = importlib.util.spec_from_file_location("gui.guiHelper", _GUI_HELPER)
		gui_helper = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(gui_helper)
		cls.messages = []
		gui = types.ModuleType("gui")
		gui.__path__ = []
		gui.guiHelper = gui_helper
		gui.messageBox = lambda message, title, style=0, parent=None: cls.messages.append((message, title))
		cls._had_translation = hasattr(builtins, "_")
		if not cls._had_translation:
			builtins._ = lambda text: text
		cls._modules = mock.patch.dict(
			sys.modules,
			{
				"wx": wx,
				"gui": gui,
				"gui.guiHelper": gui_helper,
				"addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
			},
		)
		cls._modules.start()
		sys.modules.pop("addon.synthDrivers._dictionary_dialog", None)
		cls.dialog_module = importlib.import_module("addon.synthDrivers._dictionary_dialog")

	@classmethod
	def tearDownClass(cls):
		sys.modules.pop("addon.synthDrivers._dictionary_dialog", None)
		cls._modules.stop()
		if not cls._had_translation:
			del builtins._
		cls.app.Destroy()

	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = temporary.name
		self.paths = library.DictionaryPaths(os.path.join(self.root, "config"))
		self.messages.clear()
		self.applied = []
		self.downloaded(
			COMMUNITY, "Community", enuroot_dic=_lines(f"cat\t{CAT}"), deuroot_dic=_lines("hund\thund")
		)
		self.downloaded(ALT, "Alt", enuroot_dic=_lines(f"cat\t{DOG}"))

	def downloaded(self, set_id, name, **files):
		directory = os.path.join(self.paths.sources, set_id)
		os.makedirs(directory, exist_ok=True)
		sets.write_set_ini(directory, set_id, name, "https://github.com/example/example")
		for filename, data in files.items():
			_write(os.path.join(directory, filename.replace("_", ".")), data)

	def open(self, language="enu"):
		manager = manager_module.DictionaryManager(self.paths, [])
		dialog = self.dialog_module.ManageDictionariesDialog(
			None, manager, language, on_applied=lambda: self.applied.append(True)
		)
		self.addCleanup(lambda: dialog and dialog.Destroy())
		return dialog

	# What a user does, sent through the controls' own events.

	def press(self, button):
		self.assertTrue(button.IsEnabled(), f"{button.GetLabel()} is disabled")
		button.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON, button.GetId()))

	def toggle(self, dialog, index):
		dialog.stackList.Check(index, not dialog.stackList.IsChecked(index))
		event = wx.CommandEvent(wx.wxEVT_CHECKLISTBOX, dialog.stackList.GetId())
		event.SetInt(index)
		dialog.stackList.GetEventHandler().ProcessEvent(event)

	def select(self, dialog, index):
		dialog.stackList.SetSelection(index)
		event = wx.CommandEvent(wx.wxEVT_LISTBOX, dialog.stackList.GetId())
		event.SetInt(index)
		dialog.stackList.GetEventHandler().ProcessEvent(event)

	def choose_language(self, dialog, code):
		dialog.languageChoice.SetSelection(list(library.LANGUAGES).index(code))
		dialog.languageChoice.GetEventHandler().ProcessEvent(
			wx.CommandEvent(wx.wxEVT_CHOICE, dialog.languageChoice.GetId())
		)

	def rows(self, dialog):
		return [
			(dialog.stackList.GetString(index), dialog.stackList.IsChecked(index))
			for index in range(dialog.stackList.GetCount())
		]

	def saved_rows(self, language):
		return [(row.id, row.on) for row in library.load_settings(self.paths).peek(language).rows]


class LayoutTests(DialogTestCase):
	def test_it_opens_on_the_language_asked_for_with_that_languages_stack(self):
		dialog = self.open("deu")
		self.assertEqual(dialog.languageChoice.GetStringSelection(), "German")
		self.assertEqual(self.rows(dialog), [("Community, downloaded", False)])

	def test_every_language_is_offered(self):
		dialog = self.open()
		self.assertEqual(dialog.languageChoice.GetCount(), 13)

	def test_the_tab_order_follows_the_design(self):
		# Controls are tabbed through in the order they were created.
		dialog = self.open("eng")
		children = []

		def walk(window):
			for child in window.GetChildren():
				children.append(child)
				walk(child)

		walk(dialog)
		expected = [
			dialog.languageChoice,
			dialog.stackList,
			dialog.moveUpButton,
			dialog.moveDownButton,
			dialog.addFileButton,
			dialog.removeButton,
			dialog.openFolderButton,
			dialog.dialectCheckBox,
			dialog.problemField,
			dialog.sourcesList,
			dialog.addSourceButton,
			dialog.removeSourceButton,
			dialog.updateButton,
			dialog.FindWindow(wx.ID_OK),
			dialog.FindWindow(wx.ID_CANCEL),
		]
		positions = [children.index(control) for control in expected]
		self.assertEqual(positions, sorted(positions))

	def test_every_access_key_is_used_once(self):
		dialog = self.open("eng")
		keys = {}
		for control in dialog.GetChildren():
			label = control.GetLabel() if control.IsShown() else ""
			if "&" in label.replace("&&", ""):
				key = label[label.replace("&&", "__").index("&") + 1].lower()
				keys.setdefault(key, []).append(label)
		clashes = {key: labels for key, labels in keys.items() if len(labels) > 1}
		self.assertEqual(clashes, {})

	def test_the_dialect_checkbox_is_only_there_for_a_dialect(self):
		dialog = self.open("enu")
		self.assertFalse(dialog.dialectCheckBox.IsShown())
		for code, label in (
			("eng", "Also use &American English dictionaries"),
			("esm", "Also use C&astilian Spanish dictionaries"),
			("frc", "Also use &French dictionaries"),
			("chs", "Also use &American English dictionaries for English words"),
		):
			with self.subTest(code=code):
				self.choose_language(dialog, code)
				self.assertTrue(dialog.dialectCheckBox.IsShown())
				self.assertEqual(dialog.dialectCheckBox.GetLabel(), label)
				self.assertTrue(dialog.dialectCheckBox.GetValue())
		self.choose_language(dialog, "deu")
		self.assertFalse(dialog.dialectCheckBox.IsShown())

	def test_the_problem_field_only_shows_when_a_language_could_not_be_built(self):
		_write(os.path.join(self.paths.personal, "enuroot.dic"), _lines("fine\tfine", "no tab here"))
		with self.assertLogs(library.log, "ERROR"):
			library.refresh(self.paths, [])
		dialog = self.open("enu")
		self.assertTrue(dialog.problemField.IsShown())
		self.assertEqual(
			dialog.problemField.GetValue(),
			"The dictionaries from before are still in use. Personal entries from a dictionary editor, "
			"line 2: the line has no tab between the word and its pronunciation.",
		)
		self.assertIn("Problem", dialog._problemLabel.GetLabel())
		self.choose_language(dialog, "deu")
		self.assertFalse(dialog.problemField.IsShown())
		self.assertFalse(dialog._problemLabel.IsShown())


class StackTests(DialogTestCase):
	def test_checking_rows_and_pressing_ok_saves_and_applies(self):
		dialog = self.open()
		self.assertEqual(self.rows(dialog), [("Community, downloaded", False), ("Alt, downloaded", False)])
		self.toggle(dialog, 1)
		self.press(dialog.FindWindow(wx.ID_OK))
		self.assertEqual(self.saved_rows("enu"), [(COMMUNITY, False), (ALT, True)])
		self.assertEqual(self.applied, [True])

	def test_cancel_saves_nothing_and_applies_nothing(self):
		dialog = self.open()
		self.toggle(dialog, 0)
		self.press(dialog.FindWindow(wx.ID_CANCEL))
		self.assertFalse(os.path.exists(self.paths.settings_file))
		self.assertEqual(self.applied, [])

	def test_moving_a_row_keeps_it_selected_and_checked(self):
		dialog = self.open()
		self.toggle(dialog, 0)
		self.select(dialog, 0)
		self.assertFalse(dialog.moveUpButton.IsEnabled())
		self.press(dialog.moveDownButton)
		self.assertEqual(self.rows(dialog), [("Alt, downloaded", False), ("Community, downloaded", True)])
		self.assertEqual(dialog.stackList.GetSelection(), 1)
		self.assertFalse(dialog.moveDownButton.IsEnabled())
		self.press(dialog.FindWindow(wx.ID_OK))
		self.assertEqual(self.saved_rows("enu"), [(ALT, False), (COMMUNITY, True)])

	def test_each_language_keeps_its_own_checks(self):
		dialog = self.open()
		self.toggle(dialog, 0)
		self.choose_language(dialog, "deu")
		self.assertEqual(self.rows(dialog), [("Community, downloaded", False)])
		self.choose_language(dialog, "enu")
		self.assertEqual(self.rows(dialog)[0], ("Community, downloaded", True))

	def test_the_personal_row_is_last_and_cannot_be_moved_or_removed(self):
		_write(os.path.join(self.paths.personal, "enumain.dic"), _lines("WHO\twho"))
		dialog = self.open()
		self.assertEqual(self.rows(dialog)[-1], ("Personal entries from a dictionary editor", True))
		self.select(dialog, 2)
		for button in (dialog.moveUpButton, dialog.moveDownButton, dialog.removeButton):
			self.assertFalse(button.IsEnabled())
		self.select(dialog, 1)
		self.assertFalse(dialog.moveDownButton.IsEnabled())
		self.toggle(dialog, 2)
		self.press(dialog.FindWindow(wx.ID_OK))
		self.assertFalse(library.load_settings(self.paths).peek("enu").personal)

	def test_the_dialect_checkbox_is_saved(self):
		dialog = self.open("eng")
		dialog.dialectCheckBox.SetValue(False)
		dialog.dialectCheckBox.GetEventHandler().ProcessEvent(
			wx.CommandEvent(wx.wxEVT_CHECKBOX, dialog.dialectCheckBox.GetId())
		)
		self.press(dialog.FindWindow(wx.ID_OK))
		self.assertFalse(library.load_settings(self.paths).peek("eng").dialect)

	def test_rows_say_how_many_entries_may_not_take_effect(self):
		self.downloaded(ALT, "Alt", enuabbr_dic=_lines("IDK\tI don't know"))
		settings = library.Settings()
		library.reconcile_sets(settings, sets.discover_sets([], self.paths.sources))
		for row in settings.language("enu").rows:
			row.on = True
		library.save_settings(self.paths, settings)
		library.refresh(self.paths, [])
		self.assertEqual(self.rows(self.open())[1], ("Alt, downloaded; 1 entries may not take effect", True))


class AddFileTests(DialogTestCase):
	def file(self, name, *lines):
		path = os.path.join(self.root, name)
		_write(path, _lines(*lines))
		return path

	def add(self, dialog, path, slot=None, confirm=True):
		self.reports = []

		def confirm_report(title, message, lines, ask):
			self.reports.append((message, lines, ask))
			return confirm

		with (
			mock.patch.object(dialog, "_ask_file", return_value=path),
			mock.patch.object(dialog, "_ask_slot", return_value=slot) as ask_slot,
			mock.patch.object(dialog, "_confirm_report", side_effect=confirm_report),
		):
			self.press(dialog.addFileButton)
		return ask_slot

	def test_a_clean_file_is_added_last_checked_and_selected_without_questions(self):
		dialog = self.open()
		ask_slot = self.add(dialog, self.file("MyRoot.dic", f"dog\t{DOG}"))
		ask_slot.assert_not_called()
		self.assertEqual(self.reports, [])
		self.assertEqual(self.rows(dialog)[-1], ("MyRoot.dic, word roots, your file", True))
		self.assertEqual(dialog.stackList.GetSelection(), 2)
		self.assertTrue(dialog.removeButton.IsEnabled())

	def test_the_kind_is_asked_only_when_the_name_does_not_say(self):
		dialog = self.open()
		ask_slot = self.add(dialog, self.file("words.dic", "WHO\twho"), slot="main")
		ask_slot.assert_called_once_with("words.dic")
		self.assertEqual(self.rows(dialog)[-1], ("words.dic, exact words, your file", True))

	def test_backing_out_of_the_kind_question_adds_nothing(self):
		dialog = self.open()
		self.add(dialog, self.file("words.dic", "WHO\twho"), slot=None)
		self.assertEqual(len(self.rows(dialog)), 2)

	def test_entries_that_may_not_take_effect_are_shown_for_review_with_yes_and_no(self):
		dialog = self.open()
		path = self.file("my-abbr.dic", "Dr.\tdoctor", "IDK\tI don't know", "AT&T\tA T and T")
		self.add(dialog, path, confirm=True)
		((message, lines, ask),) = self.reports
		self.assertTrue(ask)
		self.assertIn("2 entries in this file may not take effect", message)
		self.assertEqual(
			lines,
			[
				"my-abbr.dic, line 2, IDK: an abbreviation expands to plain words",
				"my-abbr.dic, line 3, AT&T: English abbreviations with a hyphen, slash or ampersand are never matched",
			],
		)
		self.assertEqual(self.rows(dialog)[-1], ("my-abbr.dic, abbreviations, your file", True))

	def test_answering_no_adds_nothing(self):
		dialog = self.open()
		self.add(dialog, self.file("my-abbr.dic", "IDK\tI don't know"), confirm=False)
		self.assertEqual(len(self.rows(dialog)), 2)
		self.assertFalse(os.path.exists(self.paths.own))

	def test_a_rejected_file_says_why_and_adds_nothing(self):
		dialog = self.open()
		self.add(dialog, self.file("root.dic", f"cat\t{CAT}", "no tab here"))
		self.assertEqual(
			self.messages,
			[
				(
					"Line 2: the line has no tab between the word and its pronunciation.",
					"Add a dictionary file",
				)
			],
		)
		self.assertEqual(len(self.rows(dialog)), 2)

	def test_removing_an_added_file_and_cancelling_leave_no_copy_behind(self):
		dialog = self.open()
		self.add(dialog, self.file("MyRoot.dic", f"dog\t{DOG}"))
		self.add(dialog, self.file("OtherRoot.dic", f"dog\t{DOG}"))
		self.select(dialog, 2)
		self.press(dialog.removeButton)
		self.assertEqual(
			[text for text, _ in self.rows(dialog)][2:], ["OtherRoot.dic, word roots, your file"]
		)
		self.press(dialog.FindWindow(wx.ID_CANCEL))
		self.assertEqual(os.listdir(self.paths.own), [])


class SourceTests(DialogTestCase):
	def test_the_known_sources_are_listed(self):
		dialog = self.open()
		self.assertEqual(
			dialog.sourcesList.GetStrings(),
			["eigencrow/IBMTTSDictionaries", "Mohamed00/AltIBMTTSDictionaries"],
		)

	def test_adding_a_source_lists_it_and_says_to_update(self):
		dialog = self.open()
		with mock.patch.object(dialog, "_ask_source", return_value="https://github.com/example/Repo"):
			self.press(dialog.addSourceButton)
		self.assertEqual(dialog.sourcesList.GetStrings()[-1], "example/Repo")
		self.assertEqual(dialog.sourcesList.GetSelection(), 2)
		self.assertIn("Update Dictionaries", self.messages[-1][0])

	def test_something_that_is_not_a_repository_is_explained(self):
		dialog = self.open()
		with mock.patch.object(dialog, "_ask_source", return_value="not a repository"):
			self.press(dialog.addSourceButton)
		self.assertEqual(dialog.sourcesList.GetCount(), 2)
		self.assertIn("owner/name", self.messages[-1][0])

	def test_removing_a_source_removes_its_rows_from_the_stack(self):
		dialog = self.open()
		dialog.sourcesList.SetSelection(1)
		self.press(dialog.removeSourceButton)
		self.assertEqual(dialog.sourcesList.GetStrings(), ["eigencrow/IBMTTSDictionaries"])
		self.assertEqual(self.rows(dialog), [("Community, downloaded", False)])
		self.press(dialog.FindWindow(wx.ID_OK))
		self.assertEqual(os.listdir(self.paths.sources), [COMMUNITY])

	def test_after_an_update_new_sets_appear_unchecked_and_the_result_says_so(self):
		dialog = self.open("ita")
		self.assertEqual(self.rows(dialog), [])
		self.downloaded(ALT, "Alt", itaroot_dic=_lines("gatto\tgatto"))
		results = (
			SourceResult(
				"Mohamed00/AltIBMTTSDictionaries",
				(DownloadedSet(ALT, "Alt", (DownloadedFile("itaroot.dic", FileStatus.CREATED),)),),
			),
			SourceResult("example/Missing", error="HTTP Error 404: Not Found"),
		)
		dialog.onUpdated(results, False)
		self.assertEqual(self.rows(dialog), [("Alt, downloaded", False)])
		message, title = self.messages[-1]
		self.assertEqual(title, "Dictionary Update")
		self.assertIn("Alt: 1 files new or changed, 0 removed.", message)
		self.assertIn("example/Missing: could not be updated. HTTP Error 404: Not Found", message)
		self.assertIn("Downloading does not switch anything on.", message)

	def test_an_update_with_entries_to_review_puts_them_in_the_report_field(self):
		dialog = self.open()
		parsed = library.check_file(_lines("IDK\tI don't know"), "enu", "abbr")
		results = (
			SourceResult(
				"example/Repo",
				(
					DownloadedSet(
						"github.example.repo",
						"Repo",
						(
							DownloadedFile("enuabbr.dic", FileStatus.UPDATED, reports=parsed.reports),
							DownloadedFile("enuroot.dic", FileStatus.FAILED, error="noTab", error_line=7),
						),
					),
				),
			),
		)
		shown = []
		with mock.patch.object(
			dialog, "_confirm_report", side_effect=lambda *args, **kw: shown.append((args, kw))
		):
			dialog.onUpdated(results, False)
		((_title, _message, lines), keywords) = shown[0]
		self.assertFalse(keywords["ask"])
		self.assertEqual(
			lines,
			[
				"Repo, enuabbr.dic, line 1, IDK: an abbreviation expands to plain words",
				"Repo, enuroot.dic: not updated, line 7, the line has no tab between the word and its pronunciation",
			],
		)


class ReportDialogTests(DialogTestCase):
	def test_the_list_is_in_a_read_only_field_that_can_be_copied(self):
		dialog = self.dialog_module.ReportDialog(None, "Title", "Message", ["one", "two"], ask=True)
		self.addCleanup(dialog.Destroy)
		field = dialog.reportField
		self.assertEqual(field.GetValue(), "one\ntwo")
		self.assertFalse(field.IsEditable())
		self.assertTrue(field.IsMultiLine())
		self.assertIsNotNone(dialog.FindWindow(wx.ID_YES))
		self.assertIsNotNone(dialog.FindWindow(wx.ID_NO))

	def test_without_a_question_it_only_has_close(self):
		dialog = self.dialog_module.ReportDialog(None, "Title", "Message", ["one"], ask=False)
		self.addCleanup(dialog.Destroy)
		self.assertIsNone(dialog.FindWindow(wx.ID_YES))
		self.assertIsNotNone(dialog.FindWindow(wx.ID_CLOSE))


if __name__ == "__main__":
	unittest.main()
