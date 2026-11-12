"""What the Manage dictionaries dialog does, without the dialog.

DictionaryManager holds a working copy of the selections while the dialog is open. Switching rows
on and off, reordering and removing only change that copy; apply() saves it and cancel() drops it.
Two things have to touch the disk before OK, because their result is what the user is deciding
about: adding a file (it is copied in so it can be checked and listed) and updating the Dictionary
Sources (a download). cancel() takes both back.

Everything the dialog shows comes from here as plain data, so this module needs neither wx nor
NVDA and the dialog itself stays a thin layer of controls.
"""

import os
from dataclasses import dataclass

from . import _dictionary_library as library
from . import _dictionary_sets, _dictionary_update


@dataclass(frozen=True)
class RowView:
	"""One line of a language's stack as the dialog lists it."""

	# "set", "own", or "personal" for the dictionary editor's entries, which are always last.
	kind: str
	id: str
	on: bool
	# A set's name, or the name the user's file had when it was added.
	name: str = ""
	version: str = ""
	# Set rows: provided by an installed add-on rather than downloaded by this one.
	installed: bool = False
	# False for a set that is not there any more, or a file that has gone from own/.
	available: bool = True
	# Own rows: the slot the file fills, and whether it came from an older version of the add-on.
	slot: str = ""
	imported: bool = False
	# How many of its entries the last build reported as possibly not taking effect.
	reported: int = 0


class DictionaryManager:
	def __init__(self, paths, provider_directories):
		self.paths = paths
		self._provider_directories = list(provider_directories)
		self._saved = library.load_settings(paths)
		self.settings = library.load_settings(paths)
		self._own_added = []
		self._own_removed = []
		self._sources_removed = []
		self.sets = {}
		self.refresh_sets()

	# ------------------------------------------------------------------ what is there

	def refresh_sets(self):
		"""Look again for sets, after an update, and give new ones their rows in the working copy."""
		found = _dictionary_sets.discover_sets(self._provider_directories, self.paths.sources)
		hidden = {
			set_id
			for repository in self._sources_removed
			for set_id in _dictionary_update.source_set_ids(repository, found)
			if not found[set_id].installed
		}
		self.sets = {set_id: found[set_id] for set_id in found if set_id not in hidden}
		library.reconcile_sets(self.settings, self.sets)

	def rows(self, language):
		record = library.read_build_record(self.paths).get("languages", {}).get(language, {})
		reported = record.get("reported", {})
		views = []
		for row in self.settings.peek(language).rows:
			count = reported.get(f"{row.kind}:{row.id}", 0)
			if row.kind == "set":
				found = self.sets.get(row.id)
				views.append(
					RowView(
						"set",
						row.id,
						row.on,
						name=found.name if found else row.id,
						version=found.version if found else "",
						installed=bool(found and found.installed),
						available=found is not None,
						reported=count,
					)
				)
			else:
				views.append(
					RowView(
						"own",
						row.id,
						row.on,
						name=row.label or row.id,
						available=os.path.isfile(os.path.join(self.paths.own, row.id)),
						slot=row.slot,
						imported=row.imported,
						reported=count,
					)
				)
		if self.has_personal_entries(language):
			views.append(
				RowView(
					"personal",
					"",
					self.settings.peek(language).personal,
					reported=reported.get("personal", 0),
				)
			)
		return views

	def has_personal_entries(self, language):
		return any(
			os.path.isfile(os.path.join(self.paths.personal, f"{language}{slot}.dic"))
			for slot in library.SLOTS
		)

	def build_error(self, language):
		"""Return the last build's failure for this language as a dict, or None if it built."""
		record = library.read_build_record(self.paths).get("languages", {}).get(language, {})
		return record if record.get("error") else None

	# ------------------------------------------------------------------ the stack

	def set_on(self, language, index, on):
		rows = self.settings.language(language).rows
		if index < len(rows):
			rows[index].on = bool(on)
		else:
			self.settings.language(language).personal = bool(on)

	def can_move(self, language, index, delta):
		rows = self.settings.peek(language).rows
		return 0 <= index < len(rows) and 0 <= index + delta < len(rows)

	def move(self, language, index, delta):
		"""Move a row up (-1) or down (+1). Returns its new index. The personal row never moves."""
		if not self.can_move(language, index, delta):
			return index
		rows = self.settings.language(language).rows
		rows.insert(index + delta, rows.pop(index))
		return index + delta

	def can_remove(self, language, index):
		"""Only the user's own files are removed here; a set goes with its source or its add-on."""
		rows = self.settings.peek(language).rows
		return 0 <= index < len(rows) and rows[index].kind == "own"

	def remove(self, language, index):
		if not self.can_remove(language, index):
			return
		row = self.settings.language(language).rows.pop(index)
		if row in self._own_added:
			# Added and removed again before OK: nothing else knows the copy, so it goes now.
			self._own_added.remove(row)
			self._delete_own(row.id)
		else:
			self._own_removed.append(row.id)

	def read_file(self, path, language, slot):
		"""Read a file the user picked and check it. Returns (data, ParsedDictionary).

		Raises OSError if it cannot be read and stack.DictionaryFileError if it has to be rejected.
		"""
		with open(path, "rb") as f:
			data = f.read()
		return data, library.check_file(data, language, slot)

	def add_file(self, language, slot, data, label):
		"""Copy a checked file in and put it last in the stack, switched on. Returns its index."""
		row = library.add_own_file(self.paths, self.settings, language, slot, data, os.path.basename(label))
		self._own_added.append(row)
		return len(self.settings.language(language).rows) - 1

	def dialect_base(self, language):
		"""The main language whose dictionaries this dialect can also use, or None."""
		return library.DIALECT_BASES.get(language)

	def uses_dialect_base(self, language):
		return self.settings.peek(language).dialect

	def set_uses_dialect_base(self, language, on):
		self.settings.language(language).dialect = bool(on)

	# ------------------------------------------------------------------ sources

	@property
	def sources(self):
		return list(self.settings.sources)

	def add_source(self, text):
		"""Add a GitHub repository to the list. Returns it as "owner/name".

		Raises ValueError with one of: notARepository, alreadyListed, cannotBeUsed.
		"""
		parsed = _dictionary_update.parse_repository(text)
		if parsed is None:
			raise ValueError("notARepository")
		repository = "/".join(parsed)
		if repository.lower() in (source.lower() for source in self.settings.sources):
			raise ValueError("alreadyListed")
		if _dictionary_update.set_id_for_source(repository) is None:
			raise ValueError("cannotBeUsed")
		self.settings.sources.append(repository)
		if repository in self._sources_removed:
			self._sources_removed.remove(repository)
			self.refresh_sets()
		return repository

	def remove_source(self, index):
		"""Take a source off the list and its sets out of every stack. Its files go at apply()."""
		repository = self.settings.sources.pop(index)
		self._sources_removed.append(repository)
		downloaded = [
			set_id
			for set_id in _dictionary_update.source_set_ids(repository, self.sets)
			if not self.sets[set_id].installed
		]
		library.remove_set_rows(self.settings, downloaded)
		self.refresh_sets()

	def update_sources(self, is_cancelled=lambda: False, report_progress=None):
		"""Download every listed source. Blocks, so run it off the UI thread; then call refresh_sets()."""
		installed = {set_id for set_id, found in self.sets.items() if found.installed}
		return _dictionary_update.update_sources(
			self.sources, self.paths.sources, installed, is_cancelled, report_progress
		)

	# ------------------------------------------------------------------ OK and Cancel

	def apply(self):
		"""Save the working copy and delete what was removed. The caller rebuilds afterwards."""
		library.save_settings(self.paths, self.settings)
		for name in self._own_removed:
			self._delete_own(name)
		for repository in self._sources_removed:
			_dictionary_update.remove_source(repository, self.paths.sources)
		self._own_added, self._own_removed, self._sources_removed = [], [], []
		self._saved = library.load_settings(self.paths)

	def cancel(self):
		"""Drop the working copy, and take back the files and downloads made for it."""
		for row in self._own_added:
			self._delete_own(row.id)
		saved = {source.lower() for source in self._saved.sources}
		for repository in self.settings.sources:
			if repository.lower() not in saved:
				_dictionary_update.remove_source(repository, self.paths.sources)
		self._own_added, self._own_removed, self._sources_removed = [], [], []
		self.settings = library.load_settings(self.paths)
		self.refresh_sets()

	def _delete_own(self, name):
		try:
			os.remove(os.path.join(self.paths.own, name))
		except OSError:
			pass
