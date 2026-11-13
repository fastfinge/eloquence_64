"""The dictionary library: which dictionaries each language uses, and the files built from them.

Everything lives in NVDA's configuration folder, so it survives add-on updates and reaches secure
screens through NVDA's own copy of the configuration:

  eciDictionaries/              shared with other add-ons (EloquenceDictionaryManager's contract)
    personal/                   the user's own entries, written by a dictionary editor; read only here
    legacy/eloquence/           the files older versions of this add-on kept, written once
  eloquenceDictionaries/        this add-on's own
    legacy/<n>/                 old files backed up later, after going back to an older version
    sources/<set-id>/           Dictionary Sources this add-on downloaded
    own/                        copies of files the user added
    active/                     the generated files; the only ones the engine loads
    dictionaries.json           the selections

Each language has a Dictionary Stack: an ordered list of rows, each a Managed Dictionary Set or one
of the user's own files, switched on or off, with the personal entries always last. build() turns
every language's stack into active/<language><slot>.dic. No NVDA dependencies.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields, replace

from . import _dictionary_entries as entries
from . import _dictionary_sets
from . import _dictionary_stack as stack

log = logging.getLogger(__name__)

# Raise this when a change to the merge rules must rebuild active/ on machines whose inputs did not change.
GENERATOR_VERSION = 1
SETTINGS_VERSION = 1
SLOTS = entries.SLOTS
LANGUAGES = ("enu", "eng", "esp", "esm", "fra", "frc", "deu", "ita", "ptb", "fin", "chs", "jpn", "kor")
# Chinese, Japanese and Korean dictionaries are not key and value lines in a western code page, so
# they are never parsed or merged: the last row switched on for a slot supplies the whole file.
UNPARSED_LANGUAGES = frozenset({"chs", "jpn", "kor"})
# A dialect can also use its main language's dictionaries, with its own stacked on top. For Chinese
# the American English ones are what the engine reads English words with.
DIALECT_BASES = {"eng": "enu", "esm": "esp", "frc": "fra", "chs": "enu"}
# The Dictionary Sources a new installation offers. Nothing is downloaded until the user asks.
DEFAULT_SOURCES = ("eigencrow/IBMTTSDictionaries", "Mohamed00/AltIBMTTSDictionaries")

_BUILD_RECORD = "build.json"
# Written into a later backup of old files once its rows are saved, so it is imported once.
_IMPORTED_MARKER = ".imported"
# How long to keep trying a file another program has open, such as a virus scanner.
_IN_USE_RETRY_SECONDS = 0.5
_IN_USE_RETRY_INTERVAL = 0.02
_SLOT_IN_NAME = re.compile(r"(main|root|abbr)(?=\.dic$)", re.IGNORECASE)


@dataclass(frozen=True)
class DictionaryPaths:
	config_directory: str

	@property
	def personal(self):
		return os.path.join(self.config_directory, "eciDictionaries", "personal")

	@property
	def legacy(self):
		return os.path.join(self.config_directory, "eciDictionaries", "legacy", "eloquence")

	@property
	def data(self):
		return os.path.join(self.config_directory, "eloquenceDictionaries")

	@property
	def later_legacy(self):
		return os.path.join(self.data, "legacy")

	@property
	def sources(self):
		return os.path.join(self.data, "sources")

	@property
	def own(self):
		return os.path.join(self.data, "own")

	@property
	def active(self):
		return os.path.join(self.data, "active")

	@property
	def settings_file(self):
		return os.path.join(self.data, "dictionaries.json")


@dataclass
class Row:
	"""One row of a language's Dictionary Stack.

	kind is "set" (id is a Managed Dictionary Set ID) or "own" (id is a file name inside own/).
	"""

	kind: str
	id: str
	on: bool = False
	# Own files only: the slot the file fills, the name the user knows it by, and whether it came
	# from an older version of the add-on rather than from the user adding it.
	slot: str = ""
	label: str = ""
	imported: bool = False


@dataclass
class LanguageSettings:
	rows: list = field(default_factory=list)
	personal: bool = True
	dialect: bool = True


@dataclass
class Settings:
	languages: dict = field(default_factory=dict)
	# Every set ID ever offered, so a set the user switched off is not switched back on.
	seen_sets: set = field(default_factory=set)
	# Dictionary Sources as "owner/name" GitHub repositories, in the order the user listed them.
	sources: list = field(default_factory=lambda: list(DEFAULT_SOURCES))
	# Whether the files an older version of the add-on kept have been taken in; done once.
	legacy_imported: bool = False

	def language(self, code):
		"""The settings for a language, to change them."""
		return self.languages.setdefault(code, LanguageSettings())

	def peek(self, code):
		"""The settings for a language, to read them: asking about a language must not add it."""
		return self.languages.get(code) or LanguageSettings()

	def to_json(self):
		return {
			"version": SETTINGS_VERSION,
			"seen_sets": sorted(self.seen_sets),
			"sources": list(self.sources),
			"legacy_imported": self.legacy_imported,
			# A language still at its defaults is left out, so the file says only what was chosen.
			"languages": {
				code: asdict(language)
				for code, language in sorted(self.languages.items())
				if language != LanguageSettings()
			},
		}

	@classmethod
	def from_json(cls, data):
		settings = cls(seen_sets=set(data.get("seen_sets", ())))
		settings.legacy_imported = bool(data.get("legacy_imported", False))
		if "sources" in data:
			settings.sources = [str(source) for source in data["sources"]]
		for code, language in data.get("languages", {}).items():
			settings.languages[code] = LanguageSettings(
				# A later version may add fields to a row; this one ignores them.
				rows=[
					Row(**{key: value for key, value in row.items() if key in _ROW_FIELDS})
					for row in language.get("rows", ())
				],
				personal=bool(language.get("personal", True)),
				dialect=bool(language.get("dialect", True)),
			)
		return settings


_ROW_FIELDS = frozenset(row_field.name for row_field in fields(Row))
# Held around every read and write of dictionaries.json. The watcher and the dialog run in the
# same process, and on Windows a read during the other's save fails with a sharing violation.
_settings_lock = threading.RLock()


class SettingsUnreadable(Exception):
	"""dictionaries.json is there but cannot be read. It is left exactly as it is."""

	def __init__(self, path, cause):
		super().__init__(f"Could not read {path}: {cause!r}")
		self.path = path
		self.cause = cause


def load_settings(paths):
	"""Read dictionaries.json. A missing file gives the defaults: nothing switched on.

	A file that is there but cannot be read raises SettingsUnreadable. It never gives the defaults,
	because saving those would replace every selection the user made.
	"""
	with _settings_lock:
		try:
			data = _while_in_use(_read_file, paths.settings_file)
		except FileNotFoundError:
			return Settings()
		except OSError as error:
			raise SettingsUnreadable(paths.settings_file, error) from error
	try:
		return Settings.from_json(json.loads(data.decode("utf-8")))
	except (ValueError, TypeError, AttributeError) as error:
		raise SettingsUnreadable(paths.settings_file, error) from error


def save_settings(paths, settings):
	with _settings_lock:
		_write_atomically(
			paths.settings_file,
			json.dumps(settings.to_json(), indent="\t", ensure_ascii=False).encode("utf-8"),
		)


def _keep_unreadable_copy(paths):
	"""Copy an unreadable dictionaries.json aside for whoever repairs it. Returns the copy's path or ""."""
	copy = os.path.join(paths.data, "dictionaries.unreadable.json")
	try:
		shutil.copyfile(paths.settings_file, copy)
	except OSError:
		return ""
	return copy


def reconcile_sets(settings, sets):
	"""Give every available set a row in each language it has files for. Returns True if anything changed.

	A set an installed add-on provides is switched on the first time it is seen, for each language
	that has no set switched on yet; after that its ID is remembered, so switching it off sticks. A
	set this add-on downloaded is never switched on for the user. Rows for sets that have gone
	missing are kept, so they resume by themselves if the set returns.

	A new set goes below the user's own files, so what they added themselves keeps winning until
	they reorder the stack.
	"""
	changed = False
	for set_id, dictionary_set in sorted(sets.items()):
		first_time = set_id not in settings.seen_sets
		for code in LANGUAGES:
			if code not in dictionary_set.languages:
				continue
			language = settings.language(code)
			if any(row.kind == "set" and row.id == set_id for row in language.rows):
				continue
			another_is_on = any(row.kind == "set" and row.on for row in language.rows)
			switch_on = dictionary_set.installed and first_time and not another_is_on
			first_own = next(
				(index for index, row in enumerate(language.rows) if row.kind == "own"), len(language.rows)
			)
			language.rows.insert(first_own, Row("set", set_id, on=switch_on))
			changed = True
		if first_time:
			settings.seen_sets.add(set_id)
			changed = True
	return changed


def remove_set_rows(settings, set_ids):
	"""Drop the rows for sets the user removed, in every language."""
	set_ids = set(set_ids)
	for language in settings.languages.values():
		language.rows[:] = [row for row in language.rows if not (row.kind == "set" and row.id in set_ids)]


def import_legacy(paths, settings, old_dictionary_directory=None):
	"""Take in the dictionary files older versions of the add-on kept. Returns (changed, backups).

	Older versions loaded .dic files from the add-on's own folder. The installer copies them once
	to legacy/eloquence in the configuration, and also carries them into the new add-on folder, so
	going back to an older version still finds them. Files in old_dictionary_directory that no
	backup holds yet are backed up here: to legacy/eloquence if there is none, which a failed
	installer backup leaves; otherwise to a later backup, eloquenceDictionaries/legacy/<n>, which
	going back to an older version, changing its files and updating again leaves.

	legacy/eloquence is imported once, then each later backup once, on top of what came before.
	Each file becomes one of the user's own files, marked as imported:

	- A file the old engine was loading is switched on, so speech stays as it was. One it never
	  loaded, such as main.dic beside enumain.dic, comes in switched off.
	- A name with no language, main.dic, root.dic or abbr.dic, counts as American English, which
	  is the only language the old engine loaded it for.
	- A file whose name says neither language nor slot is left in the backup and not imported.

	A backup holding a file that cannot be read is imported at a later check, once it can be.
	The backups are never changed: a dictionary editor reads legacy/eloquence to recover hand-made
	entries. backups lists the later backups taken in; refresh() marks each one as imported once
	the settings holding its rows are saved.
	"""
	backed_up = _back_up_old_files(paths, old_dictionary_directory)
	changed = False
	if not settings.legacy_imported:
		if os.path.isdir(paths.legacy):
			done = _import_backup(paths, settings, paths.legacy)
		else:
			# Nothing to import, which is remembered; unless that is because backing up failed.
			done = backed_up
		if done:
			settings.legacy_imported = True
			changed = True
	backups = []
	if settings.legacy_imported:
		backups = [
			backup
			for backup in _later_backups(paths, pending=True)
			if _import_backup(paths, settings, backup)
		]
	return changed or bool(backups), backups


def _import_backup(paths, settings, directory):
	"""Add a backup's files to the stacks. Returns False, having added nothing, if one cannot be read."""
	names = {name.lower(): name for name in sorted(os.listdir(directory)) if name.lower().endswith(".dic")}
	files = []
	for lower in sorted(names, key=_legacy_import_order):
		match = _LEGACY_NAME.fullmatch(lower)
		language = (match.group(1) or "enu") if match else None
		if language not in LANGUAGES:
			files.append((lower, None, None, b""))
			continue
		path = os.path.join(directory, names[lower])
		try:
			data = _read_file(path)
		except OSError as error:
			if _not_reported_yet(path, error):
				log.error(
					"Could not read the old dictionary file %s; importing it once it can be: %r", path, error
				)
			return False
		_reported.pop(path, None)
		files.append((lower, language, match.group(2), data))
	loaded = _files_the_old_engine_loaded(set(names))
	for lower, language, slot, data in files:
		if language is None:
			log.warning(
				"Not importing %s: its name does not say which language and slot it is for", names[lower]
			)
			continue
		if language not in UNPARSED_LANGUAGES:
			data, dropped = _salvage(data, language, slot)
			if dropped:
				log.warning(
					"Importing %s without %s line(s) no engine could have used", names[lower], dropped
				)
		if data:
			add_own_file(
				paths, settings, language, slot, data, names[lower], imported=True, on=lower in loaded
			)
	return True


_LEGACY_NAME = re.compile(r"([a-z]{3})?(main|root|abbr)\.dic")
# {old dictionary folder: whether backing it up worked}. Checked once per start: its files change
# only when an add-on is installed, and a failure belongs in the log once.
_old_directories_checked = {}
# {file: the error last logged for it}, so a problem that lasts is logged once and not at every
# check the watcher makes.
_reported = {}


def _not_reported_yet(subject, error):
	"""Whether error is new for subject, and so worth logging. Remembers it as reported."""
	if _reported.get(subject) == repr(error):
		return False
	_reported[subject] = repr(error)
	return True


def _legacy_import_order(lower_name):
	"""The order that makes the imported rows behave as the old engine did with the same files.

	Each file becomes a row, and a later row's root entry removes the word from earlier rows' main.
	The old engine loaded a language's files together, where main simply beat root. Importing
	root first and main last keeps it that way. Names with no language go first of all, below
	enumain.dic and its like, which were the ones found first.
	"""
	slot = lower_name[-len("main.dic") :][:4]
	return (
		len(lower_name) > len("main.dic"),
		("root", "abbr", "main").index(slot) if slot in SLOTS else 0,
		lower_name,
	)


def _back_up_old_files(paths, old_directory):
	"""Back up the .dic files in old_directory, unless a backup already holds exactly those files.

	Returns False if that failed; it is tried again at the next start.
	"""
	if not old_directory:
		return True
	if old_directory not in _old_directories_checked:
		try:
			_back_up_new_old_files(paths, old_directory)
			_old_directories_checked[old_directory] = True
		except OSError:
			_old_directories_checked[old_directory] = False
			log.exception("Could not back up the old dictionary files from %s", old_directory)
	return _old_directories_checked[old_directory]


def _back_up_new_old_files(paths, old_directory):
	names = _dic_names(old_directory)
	if not names:
		return
	if not os.path.isdir(paths.legacy):
		_copy_files(old_directory, names, paths.legacy)
		return
	backups = [paths.legacy, *_later_backups(paths, pending=False)]
	# This runs at every start, before the engine. Every copy is made with copy2, which keeps the
	# times, so equal names, sizes and times are taken as the same files without reading them.
	stamps = _stamps(old_directory, names)
	if any(_stamps(backup, _dic_names(backup)) == stamps for backup in backups):
		return
	content = _content_digest(old_directory, names)
	if any(_content_digest(backup, _dic_names(backup)) == content for backup in backups):
		return
	number = max((int(os.path.basename(backup)) for backup in backups[1:]), default=0) + 1
	target = os.path.join(paths.later_legacy, str(number))
	_copy_files(old_directory, names, target)
	log.info("Backed up old dictionary files from %s to %s", old_directory, target)


def _dic_names(directory):
	if not os.path.isdir(directory):
		return []
	return [
		name
		for name in sorted(os.listdir(directory))
		if name.lower().endswith(".dic") and os.path.isfile(os.path.join(directory, name))
	]


def _stamps(directory, names):
	return {name.lower(): _stamp(os.path.join(directory, name)) for name in names}


def _content_digest(directory, names):
	"""A digest of these files' names, without case, and contents: equal when two folders hold the same."""
	digest = hashlib.sha256()
	for name in sorted(names, key=str.lower):
		digest.update(name.lower().encode("utf-8") + b"\0")
		digest.update(hashlib.sha256(_read_file(os.path.join(directory, name))).digest())
	return digest.hexdigest()


def _later_backups(paths, pending):
	"""The later backups, oldest first: only those not yet imported when pending is set."""
	try:
		names = os.listdir(paths.later_legacy)
	except FileNotFoundError:
		return []
	backups = [os.path.join(paths.later_legacy, name) for name in sorted(filter(str.isdigit, names), key=int)]
	if pending:
		return [backup for backup in backups if not os.path.exists(os.path.join(backup, _IMPORTED_MARKER))]
	return backups


def _copy_files(source_directory, names, target_directory):
	# Copied beside the final folder and renamed, so a copy cut short never passes for a finished one.
	partial = target_directory + ".partial"
	shutil.rmtree(partial, ignore_errors=True)
	os.makedirs(partial)
	for name in names:
		shutil.copy2(os.path.join(source_directory, name), os.path.join(partial, name))
	os.rename(partial, target_directory)


def _files_the_old_engine_loaded(names):
	"""Which of these lower-case file names the old engine would have loaded for some language.

	For each slot it took the first that existed of: the language's own file, its main language's
	file for a dialect, and for the two Englishes and Chinese the name with no language at all.
	"""
	loaded = set()
	for language in LANGUAGES:
		codes = [language, *([DIALECT_BASES[language]] if language in DIALECT_BASES else [])]
		generic = [""] if {"enu", "eng"} & set(codes) else []
		for slot in SLOTS:
			for code in codes + generic:
				if f"{code}{slot}.dic" in names:
					loaded.add(f"{code}{slot}.dic")
					break
	return loaded


def _salvage(data, language, slot):
	"""Return (the lines of an old file that can be loaded, how many lines were dropped).

	An old file may hold what a new one would be rejected for: the old update accepted lines with no
	tab, and nothing checked encodings or lengths. Rejecting the whole file would take away entries
	the user has been hearing, so the lines no engine could use are dropped and the rest is kept.
	"""
	if b"\x00" in data or len(data) > stack.MAX_FILE_BYTES:
		return b"", 0
	kept, dropped = [], 0
	for line in re.split(r"\r\n|\r|\n", stack.decode_dictionary(data)):
		if not line.strip():
			continue
		key, _, value = (part.strip() for part in line.partition("\t"))
		problems = set(entries.entry_problems(language, slot, key, value))
		if problems & (stack.FILE_LEVEL_PROBLEMS | stack.UNWRITABLE_PROBLEMS):
			dropped += 1
			continue
		kept.append(f"{key}\t{value}\r\n")
	return "".join(kept).encode(entries.WESTERN_ENCODING), dropped


def guess_slot(filename):
	"""Return the slot a dictionary file's name says it is for, or None when the name does not say."""
	match = _SLOT_IN_NAME.search(os.path.basename(filename))
	return match.group(1).lower() if match else None


def check_file(data, language, slot):
	"""Parse a file the user wants to add, for its report. Raises stack.DictionaryFileError if it is rejected."""
	if language in UNPARSED_LANGUAGES:
		if len(data) > stack.MAX_FILE_BYTES:
			raise stack.DictionaryFileError("tooLarge")
		return stack.ParsedDictionary()
	return stack.parse_dictionary(data, language, slot)


def add_own_file(paths, settings, language, slot, data, label, imported=False, on=True):
	"""Copy a dictionary file into own/ and add it to the end of the language's stack.

	The copy is what gets loaded, so deleting or moving the original changes nothing. The caller has
	already run check_file() and shown its report. Returns the new Row.
	"""
	os.makedirs(paths.own, exist_ok=True)
	stem = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.splitext(os.path.basename(label))[0]) or "dictionary"
	taken = {name.lower() for name in os.listdir(paths.own)}
	name, number = f"{stem}.dic", 1
	while name.lower() in taken:
		number += 1
		name = f"{stem}-{number}.dic"
	_write_atomically(os.path.join(paths.own, name), data)
	row = Row("own", name, on=on, slot=slot, label=label, imported=imported)
	settings.language(language).rows.append(row)
	return row


def remove_own_file(paths, settings, language, row):
	settings.language(language).rows.remove(row)
	try:
		os.remove(os.path.join(paths.own, row.id))
	except FileNotFoundError:
		pass


@dataclass(frozen=True)
class LanguageOutcome:
	"""What building one language's files came to."""

	# Slots that now have a file in active/.
	slots: tuple = ()
	# Set when the language could not be built and its previous files were kept: a short reason
	# identifier, the row it concerns ("set:<id>", "own:<file>", "personal" or "" for the whole
	# stack), the slot, and the line if there is one.
	error: str = ""
	error_source: str = ""
	error_slot: str = ""
	error_line: int = 0
	# {source: number of entries reported as possibly not taking effect}.
	reported: dict = field(default_factory=dict)
	# Whether this language's files differ from the ones active/ held before the build.
	changed: bool = False


class _LanguageFailed(Exception):
	def __init__(self, reason, source="", slot="", line=0):
		super().__init__(reason)
		self.outcome = LanguageOutcome(
			error=reason, error_source=source, error_slot=slot, error_line=line or 0
		)


def build(paths, settings, sets, languages=LANGUAGES):
	"""Rebuild active/ from every language's stack. Returns {language: LanguageOutcome}.

	The new files are written to a folder beside active/ and swapped in at the end, so the engine
	never sees a half-built set. A language whose stack cannot be built keeps the files it had, and
	the others are still rebuilt. Raises OSError if the folder itself cannot be written or swapped.
	"""
	new_directory = paths.active + ".new"
	shutil.rmtree(new_directory, ignore_errors=True)
	os.makedirs(new_directory)
	outcomes, western = {}, {}
	# Main languages first: a dialect, and Chinese, can borrow what they built.
	for language in sorted(languages, key=lambda code: code in DIALECT_BASES):
		previous = _previous_files(paths, language)
		try:
			files, reported = _build_language(language, paths, settings, sets, western)
			outcomes[language] = LanguageOutcome(
				slots=tuple(files), reported=reported, changed=files != previous
			)
		except _LanguageFailed as failure:
			log.error("Keeping the previous dictionaries for %s: %s", language, failure.outcome)
			files = previous
			outcomes[language] = replace(failure.outcome, slots=tuple(files))
		for slot, data in files.items():
			with open(os.path.join(new_directory, f"{language}{slot}.dic"), "wb") as f:
				f.write(data)
	record = {
		"generator": GENERATOR_VERSION,
		"fingerprint": fingerprint(paths, settings, sets),
		"languages": {language: asdict(outcome) for language, outcome in outcomes.items()},
	}
	with open(os.path.join(new_directory, _BUILD_RECORD), "w", encoding="utf-8") as f:
		json.dump(record, f, indent="\t")
	swap_in_directory(new_directory, paths.active)
	return outcomes


def _build_language(language, paths, settings, sets, western):
	"""Return ({slot: bytes}, {source: reported count}) for one language."""
	layers = _layer_files(language, paths, settings, sets)
	if language in UNPARSED_LANGUAGES:
		files = {}
		for source, _code, by_slot in layers:
			for slot, path in by_slot.items():
				files[slot] = _read(path, source, slot)
		base = DIALECT_BASES.get(language)
		if base and settings.peek(language).dialect:
			for slot, data in western.get(base, {}).items():
				files.setdefault(slot, data)
		return files, {}
	parsed_layers, reported = [], {}
	for source, code, by_slot in layers:
		parsed = {}
		for slot, path in by_slot.items():
			try:
				# code is the language the file was written for, which a dialect borrowing it keeps.
				result = stack.parse_dictionary(_read(path, source, slot), code, slot)
			except stack.DictionaryFileError as error:
				raise _LanguageFailed(error.reason, source, slot, error.line) from error
			parsed[slot] = result.entries
			if result.reports:
				reported[source] = reported.get(source, 0) + len(result.reports)
		parsed_layers.append(stack.Layer(**parsed))
	try:
		files = stack.generate_files(parsed_layers)
	except stack.StackTooLargeError as error:
		raise _LanguageFailed("stackTooLarge", slot=error.slot) from error
	western[language] = files
	return files, reported


def _layer_files(language, paths, settings, sets):
	"""Return [(source, language, {slot: path})] in stacking order, earliest first.

	A dialect that also uses its main language's dictionaries gets that language's rows first and
	its own on top. The personal entries come after every row, the dialect's own last of all, so a
	personal entry beats every set.
	"""
	base = DIALECT_BASES.get(language)
	codes = [language]
	if base and language not in UNPARSED_LANGUAGES and settings.peek(language).dialect:
		codes.insert(0, base)
	layers = []
	for code in codes:
		for row in settings.peek(code).rows:
			if row.on:
				files = _row_files(row, code, paths, sets)
				if files:
					layers.append((f"{row.kind}:{row.id}", code, files))
	for code in codes:
		if settings.peek(code).personal:
			files = _existing({slot: os.path.join(paths.personal, f"{code}{slot}.dic") for slot in SLOTS})
			if files:
				layers.append(("personal", code, files))
	return layers


def _row_files(row, language, paths, sets):
	if row.kind == "set":
		dictionary_set = sets.get(row.id)
		if dictionary_set is None:
			# Never substitute another set: the row waits, and resumes if the set comes back.
			log.warning("The dictionary set %s selected for %s is not available", row.id, language)
			return {}
		return {slot: path for (code, slot), path in dictionary_set.files.items() if code == language}
	return _existing({row.slot: os.path.join(paths.own, row.id)})


def _existing(files):
	return {slot: path for slot, path in files.items() if os.path.isfile(path)}


def _read(path, source, slot):
	try:
		with open(path, "rb") as f:
			return f.read()
	except OSError as error:
		raise _LanguageFailed("unreadable", source, slot) from error


def _previous_files(paths, language):
	files = {}
	for slot in SLOTS:
		try:
			with open(os.path.join(paths.active, f"{language}{slot}.dic"), "rb") as f:
				files[slot] = f.read()
		except OSError:
			pass
	return files


def swap_in_directory(new_directory, active_directory):
	"""Replace active_directory with new_directory, leaving the old one in place if that fails."""
	old_directory = active_directory + ".old"
	shutil.rmtree(old_directory, ignore_errors=True)
	try:
		if os.path.isdir(active_directory):
			os.rename(active_directory, old_directory)
		os.rename(new_directory, active_directory)
	except OSError:
		# Put the previous set back if it was moved aside, so a failed swap leaves things as they were.
		if not os.path.isdir(active_directory) and os.path.isdir(old_directory):
			os.rename(old_directory, active_directory)
		shutil.rmtree(new_directory, ignore_errors=True)
		raise
	shutil.rmtree(old_directory, ignore_errors=True)


def refresh(paths, provider_directories, force=False, old_dictionary_directory=None):
	"""Bring active/ up to date with the selections and the files behind them.

	Finds the available sets, gives new ones their rows, and rebuilds only when something a build
	reads has changed (or force is set). Returns True when a language's generated files changed,
	which is when the engine needs to load them again. old_dictionary_directory is where an older
	version of the add-on kept its files; see import_legacy().
	"""
	found = _dictionary_sets.discover_sets(provider_directories, paths.sources)
	# Held from reading the selections to saving them, so a save from the dialog cannot fall between.
	with _settings_lock:
		try:
			settings = load_settings(paths)
		except SettingsUnreadable as error:
			# Saving anything now would replace the user's selections, and rebuilding would change
			# what they hear, so everything stays as it is until the file can be read again.
			if _not_reported_yet(paths.settings_file, error.cause):
				copy = _keep_unreadable_copy(paths)
				log.error(
					"Could not read %s, so the dictionaries stay as they are until it can be: %r%s",
					paths.settings_file,
					error.cause,
					f". A copy of it is in {copy}" if copy else "",
				)
			return False
		_reported.pop(paths.settings_file, None)
		own_before = _own_files(settings)
		try:
			# Both must run, so no short-circuit: the old files first, so new sets land below them.
			imported, backups = import_legacy(paths, settings, old_dictionary_directory)
			reconciled = reconcile_sets(settings, found)
			if imported or reconciled:
				save_settings(paths, settings)
		except Exception:
			# Copies with no saved row would be imported again beside themselves next time.
			for name in _own_files(settings) - own_before:
				_remove_quietly(os.path.join(paths.own, name))
			raise
		for backup in backups:
			_write_atomically(os.path.join(backup, _IMPORTED_MARKER), b"")
	if not force and not is_stale(paths, settings, found):
		return False
	outcomes = build(paths, settings, found)
	return any(outcome.changed for outcome in outcomes.values())


def _own_files(settings):
	return {row.id for language in settings.languages.values() for row in language.rows if row.kind == "own"}


def fingerprint(paths, settings, sets):
	"""A digest of everything a build reads: the selections and each input file's size and time.

	Comparing it costs a few dozen stat calls, so it is cheap enough to do at start-up and every
	couple of seconds while speaking, which is how an edit saved by a dictionary editor is noticed.
	"""
	inputs = []
	for set_id, dictionary_set in sorted(sets.items()):
		for (language, slot), path in sorted(dictionary_set.files.items()):
			inputs.append((f"set:{set_id}:{language}{slot}", *_stamp(path)))
	for label, directory in (("own", paths.own), ("personal", paths.personal)):
		try:
			names = sorted(os.listdir(directory))
		except OSError:
			names = []
		inputs.extend((f"{label}:{name.lower()}", *_stamp(os.path.join(directory, name))) for name in names)
	state = {"generator": GENERATOR_VERSION, "settings": settings.to_json(), "inputs": inputs}
	return hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()


def _stamp(path):
	try:
		status = os.stat(path)
	except OSError:
		return (0, 0)
	return (status.st_size, status.st_mtime_ns)


def read_build_record(paths):
	"""Return what the last build recorded, or {} if there has not been one."""
	try:
		with open(os.path.join(paths.active, _BUILD_RECORD), encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return {}


def is_stale(paths, settings, sets):
	"""Whether active/ no longer matches the selections and the files they point at."""
	return read_build_record(paths).get("fingerprint") != fingerprint(paths, settings, sets)


def _write_atomically(path, data):
	directory, filename = os.path.split(path)
	os.makedirs(directory, exist_ok=True)
	descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=filename + ".", suffix=".tmp")
	try:
		with os.fdopen(descriptor, "wb") as f:
			f.write(data)
		# Windows refuses to replace a file another program has open without delete sharing.
		_while_in_use(os.replace, temporary, path)
	except BaseException:
		_remove_quietly(temporary)
		raise


def _remove_quietly(path):
	try:
		os.remove(path)
	except OSError:
		pass


def _read_file(path):
	with open(path, "rb") as f:
		return f.read()


def _while_in_use(action, *args):
	"""Return action(*args), trying again for a moment while Windows says the file is in use."""
	deadline = time.monotonic() + _IN_USE_RETRY_SECONDS
	while True:
		try:
			return action(*args)
		except PermissionError:
			if time.monotonic() >= deadline:
				raise
			time.sleep(_IN_USE_RETRY_INTERVAL)
