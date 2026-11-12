"""The dictionary library: which dictionaries each language uses, and the files built from them.

Everything lives in NVDA's configuration folder, so it survives add-on updates and reaches secure
screens through NVDA's own copy of the configuration:

  eciDictionaries/              shared with other add-ons (EloquenceDictionaryManager's contract)
    personal/                   the user's own entries, written by a dictionary editor; read only here
    legacy/eloquence/           the files older versions of this add-on kept, written once
  eloquenceDictionaries/        this add-on's own
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
from dataclasses import asdict, dataclass, field, replace

from . import _dictionary_entries as entries
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

_BUILD_RECORD = "build.json"
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

	def language(self, code):
		return self.languages.setdefault(code, LanguageSettings())

	def to_json(self):
		return {
			"version": SETTINGS_VERSION,
			"seen_sets": sorted(self.seen_sets),
			"languages": {code: asdict(language) for code, language in sorted(self.languages.items())},
		}

	@classmethod
	def from_json(cls, data):
		settings = cls(seen_sets=set(data.get("seen_sets", ())))
		for code, language in data.get("languages", {}).items():
			settings.languages[code] = LanguageSettings(
				rows=[Row(**row) for row in language.get("rows", ())],
				personal=bool(language.get("personal", True)),
				dialect=bool(language.get("dialect", True)),
			)
		return settings


def load_settings(paths):
	"""Read dictionaries.json. A missing or unreadable file gives the defaults: nothing switched on."""
	try:
		with open(paths.settings_file, encoding="utf-8") as f:
			return Settings.from_json(json.load(f))
	except FileNotFoundError:
		return Settings()
	except (OSError, ValueError, TypeError, AttributeError) as error:
		log.error("Could not read %s, starting from no selections: %r", paths.settings_file, error)
		return Settings()


def save_settings(paths, settings):
	_write_atomically(
		paths.settings_file, json.dumps(settings.to_json(), indent="\t", ensure_ascii=False).encode("utf-8")
	)


def reconcile_sets(settings, sets):
	"""Give every available set a row in each language it has files for. Returns True if anything changed.

	A set an installed add-on provides is switched on the first time it is seen, for each language
	that has no set switched on yet; after that its ID is remembered, so switching it off sticks. A
	set this add-on downloaded is never switched on for the user. Rows for sets that have gone
	missing are kept, so they resume by themselves if the set returns.
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
			language.rows.append(Row("set", set_id, on=switch_on))
			changed = True
		if first_time:
			settings.seen_sets.add(set_id)
			changed = True
	return changed


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
		try:
			files, reported = _build_language(language, paths, settings, sets, western)
			outcomes[language] = LanguageOutcome(slots=tuple(files), reported=reported)
		except _LanguageFailed as failure:
			log.error("Keeping the previous dictionaries for %s: %s", language, failure.outcome)
			files = _previous_files(paths, language)
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
	_swap_in(new_directory, paths.active)
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
		if base and settings.language(language).dialect:
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
	if base and language not in UNPARSED_LANGUAGES and settings.language(language).dialect:
		codes.insert(0, base)
	layers = []
	for code in codes:
		for row in settings.language(code).rows:
			if row.on:
				files = _row_files(row, code, paths, sets)
				if files:
					layers.append((f"{row.kind}:{row.id}", code, files))
	for code in codes:
		if settings.language(code).personal:
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


def _swap_in(new_directory, active_directory):
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
		os.replace(temporary, path)
	except BaseException:
		try:
			os.remove(temporary)
		except OSError:
			pass
		raise
