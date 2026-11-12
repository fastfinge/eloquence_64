"""The Manage dictionaries dialog.

A thin layer of controls over _dictionary_manager.DictionaryManager, which does the work and holds
the working copy. Nothing here decides anything about dictionaries; it shows what the manager says
and passes on what the user does.

Opened from the Eloquence settings panel. It has its own OK and Cancel: OK saves the selections and
rebuilds the dictionary files, Cancel drops every change made while it was open.

Every question the dialog asks goes through a small method (_ask_file, _ask_slot, _confirm_report,
_message), so a test can drive the dialog with the real wxPython without anything blocking.
"""

import os

import addonHandler
import gui
import wx
from gui import guiHelper

from . import _dictionary_library as library
from . import _dictionary_stack as stack
from ._dictionary_update import FileStatus, UpdateStage

addonHandler.initTranslation()

try:
	# NVDA's list announces a row's checked state; the plain wx one does not.
	from gui.nvdaControls import CustomCheckListBox
except Exception:  # Outside NVDA, where the tests build the dialog with wxPython alone.
	CustomCheckListBox = wx.CheckListBox


def language_names():
	return {
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"enu": _("American English"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"eng": _("British English"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"esp": _("Castilian Spanish"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"esm": _("Latin American Spanish"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"fra": _("French"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"frc": _("Canadian French"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"deu": _("German"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"ita": _("Italian"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"ptb": _("Brazilian Portuguese"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"fin": _("Finnish"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"chs": _("Mandarin Chinese"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"jpn": _("Japanese"),
		# Translators: The name of an Eloquence language in the dictionaries dialog.
		"kor": _("Korean"),
	}


def dialect_labels():
	return {
		# Translators: A checkbox in the dictionaries dialog, shown for British English.
		"eng": _("Also use &American English dictionaries"),
		# Translators: A checkbox in the dictionaries dialog, shown for Latin American Spanish.
		"esm": _("Also use C&astilian Spanish dictionaries"),
		# Translators: A checkbox in the dictionaries dialog, shown for Canadian French.
		"frc": _("Also use &French dictionaries"),
		# Translators: A checkbox in the dictionaries dialog, shown for Mandarin Chinese.
		"chs": _("Also use &American English dictionaries for English words"),
	}


def slot_names():
	return {
		# Translators: A kind of dictionary file: entries that match one exact word.
		"main": _("exact words"),
		# Translators: A kind of dictionary file: entries that match a word and the words built on it.
		"root": _("word roots"),
		# Translators: A kind of dictionary file: entries for abbreviations.
		"abbr": _("abbreviations"),
	}


def problem_messages():
	"""Why a file was rejected or an entry may not take effect, by identifier."""
	return {
		# Translators: A reason a dictionary file cannot be used.
		"tooLarge": _("the file is larger than 8 MB, which is more than every engine can load"),
		# Translators: A reason a dictionary file cannot be used.
		"notText": _("it is not a text file"),
		# Translators: A reason a dictionary file cannot be used.
		"noTab": _("the line has no tab between the word and its pronunciation"),
		# Translators: A reason a dictionary file cannot be used.
		"unencodableCharacter": _("it has a character the language's encoding cannot hold"),
		# Translators: A reason a dictionary file cannot be used.
		"keyTooLong": _("the word is longer than 128 characters"),
		# Translators: A reason a dictionary file cannot be used.
		"valueTooLong": _("the pronunciation is longer than 512 characters"),
		# Translators: A reason a dictionary file cannot be used.
		"unreadable": _("the file could not be read"),
		# Translators: A reason a language's dictionaries could not be built.
		"stackTooLarge": _("together the dictionaries switched on are larger than 8 MB"),
		# Translators: A reason a dictionary entry was left out.
		"emptyKey": _("the word is missing"),
		# Translators: A reason a dictionary entry was left out.
		"emptyValue": _("the pronunciation is missing"),
		# Translators: A reason a dictionary entry was left out.
		"keyTab": _("the word contains a tab"),
		# Translators: A reason a dictionary entry was left out.
		"valueTab": _("the pronunciation contains a tab"),
		# Translators: A reason a dictionary entry may not take effect.
		"mainKeyWhitespace": _("an exact word cannot contain a space"),
		# Translators: A reason a dictionary entry may not take effect.
		"mainKeyFinalPunctuation": _("an exact word cannot end with punctuation"),
		# Translators: A reason a dictionary entry may not take effect.
		"rootKeyInvalid": _("a word root can only have letters, and apostrophes inside it"),
		# Translators: A reason a dictionary entry may not take effect.
		"rootValueInvalid": _("a word root is pronounced as one plain word or one phonetic string"),
		# Translators: A reason a dictionary entry may not take effect.
		"abbreviationKeyInvalid": _("an abbreviation can only have letters and periods"),
		# Translators: A reason a dictionary entry may not take effect.
		"abbreviationValueInvalid": _("an abbreviation expands to plain words"),
		# Translators: A reason a dictionary entry may not take effect.
		"sprUnclosed": _("the phonetic string is not closed with ]"),
		# Translators: A reason a dictionary entry may not take effect.
		"sprEmpty": _("the phonetic string is empty"),
		# Translators: A reason a dictionary entry may not take effect.
		"sprQuoteInvalid": _("a quoted phonetic symbol is not two characters between apostrophes"),
		# Translators: A reason a dictionary entry may not take effect.
		"sprPrimaryStressRequired": _("the phonetic string has no primary stress, written 1"),
		# Translators: A reason a dictionary entry may not take effect.
		"abbreviationKeyNeverMatches": _(
			"English abbreviations with a hyphen, slash or ampersand are never matched"
		),
	}


def describe_problem(identifier):
	return problem_messages().get(identifier, identifier)


def describe_row(view):
	"""One line of the stack list."""
	if view.kind == "personal":
		# Translators: A row in the dictionaries list: the entries made with a dictionary editor add-on.
		text = _("Personal entries from a dictionary editor")
	elif view.kind == "set":
		name = f"{view.name} ({view.version})" if view.version else view.name
		if not view.available:
			# Translators: A row in the dictionaries list. {name} is a dictionary set that cannot be found.
			text = _("{name}, not available").format(name=name)
		elif view.installed:
			# Translators: A row in the dictionaries list. {name} is a dictionary set.
			text = _("{name}, from an installed add-on").format(name=name)
		else:
			# Translators: A row in the dictionaries list. {name} is a dictionary set.
			text = _("{name}, downloaded").format(name=name)
	else:
		kind = slot_names().get(view.slot, view.slot)
		if view.imported:
			# Translators: A row in the dictionaries list. {name} is a file, {kind} is what its entries match.
			text = _("{name}, {kind}, imported from the old add-on").format(name=view.name, kind=kind)
		else:
			# Translators: A row in the dictionaries list. {name} is a file, {kind} is what its entries match.
			text = _("{name}, {kind}, your file").format(name=view.name, kind=kind)
		if not view.available:
			# Translators: Added to a row in the dictionaries list when the file has been deleted.
			text = _("{row}, file missing").format(row=text)
	if view.reported:
		# Translators: Added to a row in the dictionaries list. {count} is a number of dictionary entries.
		text = _("{row}; {count} entries may not take effect").format(row=text, count=view.reported)
	return text


def format_reports(filename, reports):
	"""The lines for a read-only report field: one per entry that may not take effect."""
	lines = []
	for report in reports:
		reasons = "; ".join(
			describe_problem(reason) for reason in (*report.problems, *filter(None, [report.warning]))
		)
		if report.kept:
			# Translators: A line of a report about a dictionary file.
			template = _("{file}, line {line}, {word}: {reasons}")
		else:
			# Translators: A line of a report about a dictionary file, for an entry that had to be left out.
			template = _("{file}, line {line}, {word}: left out, {reasons}")
		lines.append(template.format(file=filename, line=report.line, word=report.key, reasons=reasons))
	return lines


def format_rejection(error):
	"""Why a whole file was rejected, as one sentence."""
	reason = describe_problem(error.reason)
	if error.line:
		# Translators: Why a dictionary file cannot be used. {line} is a line number.
		return _("Line {line}: {reason}.").format(line=error.line, reason=reason)
	# Translators: Why a dictionary file cannot be used.
	return _("This file cannot be used: {reason}.").format(reason=reason)


def format_update_results(results):
	"""Return (summary lines, report lines) for what an update of the sources came to."""
	summary, report = [], []
	for result in results:
		if result.error:
			# Translators: A line of the dictionary update result. {source} is a GitHub repository.
			summary.append(
				_("{source}: could not be updated. {error}").format(
					source=result.repository, error=result.error
				)
			)
			continue
		if result.provided_by_add_on:
			summary.append(
				# Translators: A line of the dictionary update result. {source} is a GitHub repository.
				_("{source}: not downloaded, because an installed add-on already provides it.").format(
					source=result.repository
				)
			)
			continue
		for stored in result.sets:
			changed = sum(file.status in (FileStatus.CREATED, FileStatus.UPDATED) for file in stored.files)
			removed = sum(file.status is FileStatus.REMOVED for file in stored.files)
			summary.append(
				# Translators: A line of the dictionary update result. {name} is a dictionary set.
				_("{name}: {changed} files new or changed, {removed} removed.").format(
					name=stored.name, changed=changed, removed=removed
				)
			)
			for file in stored.files:
				if file.status is FileStatus.FAILED:
					problem = describe_problem(file.error)
					if file.error_line:
						# Translators: Part of a dictionary update report. {line} is a line number.
						problem = _("line {line}, {reason}").format(line=file.error_line, reason=problem)
					report.append(
						# Translators: A line of a dictionary update report about a file that was rejected.
						_("{name}, {file}: not updated, {reason}").format(
							name=stored.name, file=file.filename, reason=problem
						)
					)
				report.extend(format_reports(f"{stored.name}, {file.filename}", file.reports))
		if result.cancelled:
			# Translators: A line of the dictionary update result.
			summary.append(_("{source}: cancelled.").format(source=result.repository))
	return summary, report


class ReportDialog(wx.Dialog):
	"""A message with a list too long for a message box, in a read-only field that can be copied.

	With ask=True it has Yes and No and ShowModal() answers wx.ID_YES or wx.ID_NO; otherwise it has
	Close.
	"""

	def __init__(self, parent, title, message, lines, ask=False):
		super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, wx.VERTICAL)
		helper.addItem(wx.StaticText(self, label=message))
		self.reportField = helper.addItem(
			wx.TextCtrl(
				self,
				value="\n".join(lines),
				size=(600, 250),
				style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
			),
			flag=wx.EXPAND,
			proportion=1,
		)
		helper.addDialogDismissButtons(wx.YES | wx.NO if ask else wx.CLOSE)
		main_sizer.Add(
			helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND, proportion=1
		)
		self.SetSizerAndFit(main_sizer)
		if ask:
			self.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_YES), id=wx.ID_YES)
			self.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_NO), id=wx.ID_NO)
			self.SetEscapeId(wx.ID_NO)
		else:
			self.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
			self.SetEscapeId(wx.ID_CLOSE)
		self.reportField.SetFocus()
		self.CentreOnParent()


class ManageDictionariesDialog(wx.Dialog):
	"""Choose which dictionaries each Eloquence language uses, and in which order."""

	def __init__(self, parent, manager, language="enu", on_applied=None):
		# Translators: The title of the dialog for choosing Eloquence pronunciation dictionaries.
		super().__init__(parent, title=_("Eloquence dictionaries"))
		self.manager = manager
		self._on_applied = on_applied
		self._languages = list(library.LANGUAGES)
		self._language_names = language_names()
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, wx.VERTICAL)

		self.languageChoice = helper.addLabeledControl(
			# Translators: The label of a combo box in the dictionaries dialog.
			_("&Language:"),
			wx.Choice,
			choices=[self._language_names[code] for code in self._languages],
		)
		self.languageChoice.SetSelection(
			self._languages.index(language) if language in self._languages else 0
		)
		self.languageChoice.Bind(wx.EVT_CHOICE, self.onLanguage)

		self.stackList = helper.addLabeledControl(
			# Translators: The label of the list of dictionaries in the dictionaries dialog.
			_("&Dictionaries for this language. Where two have the same word, the lower one wins:"),
			CustomCheckListBox,
			choices=[],
		)
		self.stackList.Bind(wx.EVT_CHECKLISTBOX, self.onToggle)
		self.stackList.Bind(wx.EVT_LISTBOX, self.onSelect)

		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the dictionaries dialog.
		self.moveUpButton = buttons.addButton(self, label=_("Move &up"))
		# Translators: A button in the dictionaries dialog.
		self.moveDownButton = buttons.addButton(self, label=_("Move dow&n"))
		# Translators: A button in the dictionaries dialog.
		self.addFileButton = buttons.addButton(self, label=_("Add &file..."))
		# Translators: A button in the dictionaries dialog.
		self.removeButton = buttons.addButton(self, label=_("&Remove"))
		# Translators: A button in the dictionaries dialog. It opens the folder holding the user's own files.
		self.openFolderButton = buttons.addButton(self, label=_("&Open folder"))
		helper.addItem(buttons)
		self.moveUpButton.Bind(wx.EVT_BUTTON, lambda evt: self.onMove(-1))
		self.moveDownButton.Bind(wx.EVT_BUTTON, lambda evt: self.onMove(+1))
		self.addFileButton.Bind(wx.EVT_BUTTON, self.onAddFile)
		self.removeButton.Bind(wx.EVT_BUTTON, self.onRemove)
		self.openFolderButton.Bind(wx.EVT_BUTTON, self.onOpenFolder)

		self.dialectCheckBox = helper.addItem(wx.CheckBox(self, label=""))
		self.dialectCheckBox.Bind(wx.EVT_CHECKBOX, self.onDialect)

		self.problemField = helper.addLabeledControl(
			# Translators: The label of a read-only field in the dictionaries dialog. It is only shown
			# when the dictionaries of the chosen language could not be built.
			_("&Problem with this language:"),
			wx.TextCtrl,
			style=wx.TE_READONLY,
		)
		self._problemLabel = self.problemField.GetPrevSibling()

		sources_box = wx.StaticBoxSizer(
			# Translators: The label of a group of controls in the dictionaries dialog.
			wx.StaticBox(self, label=_("Dictionary sources on GitHub")),
			wx.VERTICAL,
		)
		sources = guiHelper.BoxSizerHelper(self, sizer=sources_box)
		self.sourcesList = sources.addLabeledControl(
			# Translators: The label of a list in the dictionaries dialog.
			_("&Sources:"),
			wx.ListBox,
			choices=[],
		)
		self.sourcesList.Bind(wx.EVT_LISTBOX, self.onSelect)
		# Children of the group box, like the list NVDA's helper puts there, so the group is read as one.
		sources_parent = sources_box.GetStaticBox()
		source_buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the dictionaries dialog.
		self.addSourceButton = source_buttons.addButton(sources_parent, label=_("Add sour&ce..."))
		# Translators: A button in the dictionaries dialog.
		self.removeSourceButton = source_buttons.addButton(sources_parent, label=_("Re&move source"))
		# Translators: A button in the dictionaries dialog. It downloads every listed source again.
		self.updateButton = source_buttons.addButton(sources_parent, label=_("Upda&te Dictionaries"))
		sources.addItem(source_buttons)
		self.addSourceButton.Bind(wx.EVT_BUTTON, self.onAddSource)
		self.removeSourceButton.Bind(wx.EVT_BUTTON, self.onRemoveSource)
		self.updateButton.Bind(wx.EVT_BUTTON, self.onUpdate)
		helper.addItem(sources_box, flag=wx.EXPAND)

		helper.addDialogDismissButtons(wx.OK | wx.CANCEL)
		main_sizer.Add(helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
		self.SetSizer(main_sizer)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
		self.Bind(wx.EVT_CLOSE, self.onCancel)

		self._refresh_sources()
		self._refresh_language()
		self.languageChoice.SetFocus()
		self.CentreOnParent()

	# ------------------------------------------------------------------ showing

	@property
	def language(self):
		return self._languages[self.languageChoice.GetSelection()]

	def _refresh_language(self):
		"""Show the chosen language: its stack, its dialect setting and any build problem."""
		self._refresh_stack()
		language = self.language
		labels = dialect_labels()
		self.dialectCheckBox.Show(language in labels)
		if language in labels:
			self.dialectCheckBox.SetLabel(labels[language])
			self.dialectCheckBox.SetValue(self.manager.uses_dialect_base(language))
		error = self.manager.build_error(language)
		self.problemField.Show(error is not None)
		self._problemLabel.Show(error is not None)
		if error is not None:
			self.problemField.SetValue(self._describe_build_error(language, error))
		self.Layout()
		self.Fit()

	def _refresh_stack(self, select=None):
		self._rows = self.manager.rows(self.language)
		self.stackList.Set([describe_row(view) for view in self._rows])
		for index, view in enumerate(self._rows):
			self.stackList.Check(index, view.on)
		if self._rows:
			self.stackList.SetSelection(min(select or 0, len(self._rows) - 1))
		self._refresh_buttons()

	def _refresh_sources(self, select=0):
		sources = self.manager.sources
		self.sourcesList.Set(sources)
		if sources:
			self.sourcesList.SetSelection(min(select, len(sources) - 1))
		self._refresh_buttons()

	def _refresh_buttons(self):
		index = self.stackList.GetSelection()
		language = self.language
		self.moveUpButton.Enable(index != wx.NOT_FOUND and self.manager.can_move(language, index, -1))
		self.moveDownButton.Enable(index != wx.NOT_FOUND and self.manager.can_move(language, index, +1))
		self.removeButton.Enable(index != wx.NOT_FOUND and self.manager.can_remove(language, index))
		self.removeSourceButton.Enable(self.sourcesList.GetSelection() != wx.NOT_FOUND)
		self.updateButton.Enable(bool(self.manager.sources))

	def _describe_build_error(self, language, error):
		reason = describe_problem(error["error"])
		source = error.get("error_source", "")
		name = next(
			(
				describe_row(view)
				for view in self.manager.rows(language)
				if f"{view.kind}:{view.id}".rstrip(":") == source
			),
			"",
		)
		if error.get("error_line") and name:
			# Translators: Why a language's dictionaries could not be built. {row} is a row of the list.
			return _("The dictionaries from before are still in use. {row}, line {line}: {reason}.").format(
				row=name, line=error["error_line"], reason=reason
			)
		if name:
			# Translators: Why a language's dictionaries could not be built. {row} is a row of the list.
			return _("The dictionaries from before are still in use. {row}: {reason}.").format(
				row=name, reason=reason
			)
		# Translators: Why a language's dictionaries could not be built.
		return _("The dictionaries from before are still in use: {reason}.").format(reason=reason)

	# ------------------------------------------------------------------ questions, kept apart so tests can answer

	def _message(self, message, title, style=wx.OK | wx.ICON_INFORMATION):
		return gui.messageBox(message, title, style, self)

	def _ask_file(self):
		with wx.FileDialog(
			self,
			# Translators: The title of a dialog for choosing a dictionary file to add.
			_("Add a dictionary file"),
			# Translators: The kinds of file offered when adding a dictionary file.
			wildcard=_("Dictionary files (*.dic)|*.dic|All files (*.*)|*.*"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as dialog:
			return dialog.GetPath() if dialog.ShowModal() == wx.ID_OK else None

	def _ask_slot(self, filename):
		names = slot_names()
		with wx.SingleChoiceDialog(
			self,
			# Translators: A question when adding a dictionary file whose name does not say what kind it is.
			_("What do the entries in {file} match?").format(file=filename),
			# Translators: The title of a dialog for choosing a dictionary file to add.
			_("Add a dictionary file"),
			[names[slot] for slot in library.SLOTS],
		) as dialog:
			return library.SLOTS[dialog.GetSelection()] if dialog.ShowModal() == wx.ID_OK else None

	def _ask_source(self):
		with wx.TextEntryDialog(
			self,
			# Translators: A question when adding a dictionary source.
			_("The GitHub repository to download dictionaries from, as owner/name or as its address:"),
			# Translators: The title of a dialog for adding a dictionary source.
			_("Add a dictionary source"),
		) as dialog:
			return dialog.GetValue() if dialog.ShowModal() == wx.ID_OK else None

	def _confirm_report(self, title, message, lines, ask):
		with ReportDialog(self, title, message, lines, ask=ask) as dialog:
			return dialog.ShowModal() == wx.ID_YES

	# ------------------------------------------------------------------ the stack

	def onLanguage(self, evt):
		self._refresh_language()

	def onSelect(self, evt):
		self._refresh_buttons()
		evt.Skip()

	def onToggle(self, evt):
		index = evt.GetInt()
		self.manager.set_on(self.language, index, self.stackList.IsChecked(index))
		# NVDA's list listens for the same event to announce the new state.
		evt.Skip()

	def onMove(self, delta):
		index = self.stackList.GetSelection()
		if index == wx.NOT_FOUND:
			return
		self._refresh_stack(select=self.manager.move(self.language, index, delta))
		self.stackList.SetFocus()

	def onDialect(self, evt):
		self.manager.set_uses_dialect_base(self.language, self.dialectCheckBox.GetValue())

	def onAddFile(self, evt):
		path = self._ask_file()
		if not path:
			return
		language = self.language
		slot = library.guess_slot(path) or self._ask_slot(os.path.basename(path))
		if not slot:
			return
		# Translators: The title of messages about adding a dictionary file.
		title = _("Add a dictionary file")
		try:
			data, checked = self.manager.read_file(path, language, slot)
		except stack.DictionaryFileError as error:
			self._message(format_rejection(error), title, wx.OK | wx.ICON_ERROR)
			return
		except OSError as error:
			# Translators: A message when a dictionary file the user chose cannot be opened.
			self._message(
				_("The file could not be read: {error}").format(error=error), title, wx.OK | wx.ICON_ERROR
			)
			return
		if checked.reports:
			lines = format_reports(os.path.basename(path), checked.reports)
			# Translators: A question when adding a dictionary file. A list of entries follows.
			message = _(
				"{count} entries in this file may not take effect. They are listed below, "
				"so you can fix them in your file first. Use the file anyway?"
			).format(count=len(checked.reports))
			if not self._confirm_report(title, message, lines, ask=True):
				return
		self._refresh_stack(select=self.manager.add_file(language, slot, data, path))
		self.stackList.SetFocus()

	def onRemove(self, evt):
		index = self.stackList.GetSelection()
		if index == wx.NOT_FOUND or not self.manager.can_remove(self.language, index):
			return
		self.manager.remove(self.language, index)
		self._refresh_stack(select=index)
		self.stackList.SetFocus()

	def onOpenFolder(self, evt):
		os.makedirs(self.manager.paths.own, exist_ok=True)
		os.startfile(self.manager.paths.own)

	# ------------------------------------------------------------------ sources

	def onAddSource(self, evt):
		text = self._ask_source()
		if not text or not text.strip():
			return
		# Translators: The title of messages about adding a dictionary source.
		title = _("Add a dictionary source")
		try:
			self.manager.add_source(text)
		except ValueError as error:
			messages = {
				# Translators: A message when what the user typed is not a GitHub repository.
				"notARepository": _(
					"That is not a GitHub repository. Type it as owner/name, for example eigencrow/IBMTTSDictionaries."
				),
				# Translators: A message when the user adds a dictionary source that is already listed.
				"alreadyListed": _("That source is already in the list."),
				# Translators: A message when a GitHub repository cannot be used as a dictionary source.
				"cannotBeUsed": _("That repository cannot be used as a dictionary source."),
			}
			self._message(messages.get(str(error), str(error)), title, wx.OK | wx.ICON_ERROR)
			return
		self._refresh_sources(select=len(self.manager.sources) - 1)
		self._message(
			# Translators: A message after adding a dictionary source.
			_("Added. Choose Update Dictionaries to download it."),
			title,
		)

	def onRemoveSource(self, evt):
		index = self.sourcesList.GetSelection()
		if index == wx.NOT_FOUND:
			return
		self.manager.remove_source(index)
		self._refresh_sources(select=index)
		self._refresh_stack()

	def onUpdate(self, evt):
		from ._background_work import BackgroundWork

		messages = {
			# Translators: Message of a progress dialog when updating dictionaries. {source} is a repository.
			UpdateStage.DOWNLOADING: _("Downloading {source}..."),
			# Translators: Message of a progress dialog when updating dictionaries. {source} is a repository.
			UpdateStage.CHECKING: _("Checking {source}..."),
		}

		def work(is_cancelled, report):
			return self.manager.update_sources(
				is_cancelled, lambda stage, source: report(None, messages[stage].format(source=source))
			)

		BackgroundWork(
			self,
			# Translators: Title of a progress dialog when updating dictionaries
			_("Updating Dictionaries"),
			# Translators: Message of a progress dialog when updating dictionaries
			_("Downloading dictionaries..."),
			work,
			self.onUpdated,
			self.onUpdateError,
		)

	def onUpdated(self, results, cancelled):
		self.manager.refresh_sets()
		self._refresh_stack(select=self.stackList.GetSelection())
		summary, report = format_update_results(results or ())
		# Translators: Part of the message shown after a dictionary update.
		summary.append(
			_(
				"Downloading does not switch anything on. Check the dictionaries you want for each language, then choose OK."
			)
		)
		# Translators: Title of the message shown after a dictionary update.
		title = _("Dictionary Update")
		if report:
			# Translators: Part of the message shown after a dictionary update. A list follows.
			summary.append(_("These entries may not take effect, and these files were rejected:"))
			self._confirm_report(title, "\n".join(summary), report, ask=False)
		else:
			self._message("\n".join(summary), title)

	def onUpdateError(self, error):
		self._message(
			# Translators: Text of a message dialog when updating dictionaries fails.
			_("An error occurred while updating the dictionaries: {error}").format(error=error),
			# Translators: Title of the message shown after a dictionary update.
			_("Dictionary Update"),
			wx.OK | wx.ICON_ERROR,
		)

	# ------------------------------------------------------------------ OK and Cancel

	def onOk(self, evt):
		self.manager.apply()
		if self._on_applied is not None:
			with wx.BusyCursor():
				self._on_applied()
		self._end(wx.ID_OK)

	def onCancel(self, evt):
		self.manager.cancel()
		self._end(wx.ID_CANCEL)

	def _end(self, result):
		if self.IsModal():
			self.EndModal(result)
		else:
			self.Destroy()
