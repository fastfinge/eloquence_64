"""Dictionary Stacks: turn an ordered list of dictionary layers into the files the engine loads.

The Eloquence Engine takes one file per slot (main, root, abbreviation) for a language. A user can
switch on several sources for a language at once, so this module reads each source's files, merges
them in order with later layers winning, and writes one file per slot. It also names downloaded
repositories the way every add-on sharing the dictionary folder does.

Everything here was measured on the proprietary engine, openevv and ReLoq C (fastfinge/eloquence_64#173),
and follows EloquenceDictionaryManager's directory contract, version 1. No NVDA dependencies.
"""

import re
from dataclasses import dataclass

from . import _dictionary_entries as entries

# ReLoq C refuses a main or root file one byte over this and then loads nothing from it; the other
# engines take more. It applies to a file read in and to a file generated from a whole stack.
MAX_FILE_BYTES = 8 * 1024 * 1024

# Rule breaks that mean an entry cannot be written as one line of a dictionary file.
_UNWRITABLE = frozenset({"emptyKey", "emptyValue", "keyTab", "valueTab"})
# Rule breaks that reject the whole file, because it is then not a dictionary for this language.
_FILE_LEVEL = frozenset({"unencodableCharacter", "keyTooLong", "valueTooLong"})

# The two datasets EloquenceDictionaryManager registered before the naming rule existed.
_REGISTERED_SET_IDS = {
	("eigencrow", "ibmttsdictionaries"): "github.eigencrow.ibmtts-dictionaries",
	("mohamed00", "altibmttsdictionaries"): "github.mohamed00.alt-ibmtts-dictionaries",
}


class DictionaryFileError(Exception):
	"""A file is not a usable Pronunciation Dictionary, so nothing from it is loaded.

	reason is one of tooLarge, notText, noTab, or an entry rule identifier; line is the 1-based line
	it was found on, or None when it concerns the whole file.
	"""

	def __init__(self, reason, line=None):
		super().__init__(reason if line is None else f"{reason} on line {line}")
		self.reason = reason
		self.line = line


class StackTooLargeError(Exception):
	"""The merged file for one slot would be larger than an engine accepts."""

	def __init__(self, slot, size):
		super().__init__(
			f"The merged {slot} dictionary would be {size} bytes; the limit is {MAX_FILE_BYTES}."
		)
		self.slot = slot
		self.size = size


@dataclass(frozen=True)
class EntryReport:
	"""Something worth telling the user about one line of a dictionary file."""

	line: int
	key: str
	# Entry rule identifiers, as in _dictionary_entries. Empty when there is only a warning.
	problems: tuple[str, ...] = ()
	warning: str | None = None
	# False when the entry could not be written at all and was left out.
	kept: bool = True


@dataclass(frozen=True)
class ParsedDictionary:
	entries: tuple[tuple[str, str], ...] = ()
	reports: tuple[EntryReport, ...] = ()


@dataclass(frozen=True)
class Layer:
	"""One row of a Dictionary Stack: the entries one source has for one language."""

	main: tuple[tuple[str, str], ...] = ()
	root: tuple[tuple[str, str], ...] = ()
	abbr: tuple[tuple[str, str], ...] = ()


def parse_dictionary(data, language, slot):
	"""Read one western dictionary file. Raises DictionaryFileError if it has to be rejected whole.

	Entries that break an entry rule are kept, because the engines use many of them in practice: of
	the entries in the community's files that break a rule, all but one were measured to work. They
	are reported so the user can see them. Only an entry that cannot be written as a line is left out.
	"""
	if len(data) > MAX_FILE_BYTES:
		raise DictionaryFileError("tooLarge")
	if b"\x00" in data:
		raise DictionaryFileError("notText")
	kept, reports = [], []
	for number, line in enumerate(re.split(r"\r\n|\r|\n", _decode(data)), 1):
		# The proprietary engine stops reading at a blank line and the others skip it, so dropping
		# them is the only reading all three agree on.
		if not line.strip():
			continue
		if "\t" not in line:
			raise DictionaryFileError("noTab", number)
		key, value = (part.strip() for part in line.split("\t", 1))
		problems = entries.entry_problems(language, slot, key, value)
		for problem in problems:
			if problem in _FILE_LEVEL:
				raise DictionaryFileError(problem, number)
		writable = _UNWRITABLE.isdisjoint(problems)
		warning = entries.entry_warning(language, slot, key, value)
		if problems or warning:
			reports.append(EntryReport(number, key, problems, warning, kept=writable))
		if writable:
			kept.append((key, value))
	return ParsedDictionary(tuple(kept), tuple(reports))


def _decode(data):
	"""Decode a dictionary file of unknown encoding.

	Sources publish a mix of UTF-8 and ANSI files. Accented text in cp1252 is almost never valid
	UTF-8, so trying UTF-8 first separates the two. A byte cp1252 leaves undefined becomes U+FFFD,
	which then fails the encoding rule.
	"""
	try:
		return data.decode("utf-8-sig")
	except UnicodeDecodeError:
		return data.decode(entries.WESTERN_ENCODING, errors="replace")


def merge_layers(layers):
	"""Merge layers, earliest first, into {slot: ((key, value), ...)} with later layers winning.

	Within a slot the later layer's entry for a key replaces the earlier one. Root keys compare
	without case, as the engine compares them; main and abbreviation keys compare exactly.

	Every engine speaks a word that has both a main and a root entry with the main one, whatever
	order the files were built in. So when a layer has a word in root, that word is dropped from
	main in the layers before it, comparing without case. Otherwise a personal root entry would lose
	to a source's main entry. Nothing is dropped within one layer.
	"""
	main, root, abbr = {}, {}, {}
	for layer in layers:
		overridden = {key.lower() for key, _ in layer.root}
		if overridden:
			main = {key: value for key, value in main.items() if key.lower() not in overridden}
		for key, value in layer.main:
			_put_last(main, key, (key, value))
		for key, value in layer.root:
			_put_last(root, key.lower(), (key, value))
		for key, value in layer.abbr:
			_put_last(abbr, key, (key, value))
	return {
		"main": tuple(main.values()),
		"root": tuple(root.values()),
		"abbr": tuple(abbr.values()),
	}


def _put_last(merged, merge_key, entry):
	"""Replace merge_key's entry and move it to the end, where a second copy in a file would sit."""
	merged.pop(merge_key, None)
	merged[merge_key] = entry


def generate_files(layers, encoding=entries.WESTERN_ENCODING):
	"""Merge layers and return {slot: bytes} for each slot that has entries.

	Raises StackTooLargeError when a slot's file would be over MAX_FILE_BYTES.
	"""
	files = {}
	for slot, merged in merge_layers(layers).items():
		if not merged:
			continue
		data = "".join(f"{key}\t{value}\r\n" for key, value in merged).encode(encoding)
		if len(data) > MAX_FILE_BYTES:
			raise StackTooLargeError(slot, len(data))
		files[slot] = data
	return files


def set_id_for_repository(owner, repository, folder=""):
	"""Return the Managed Dictionary Set ID for a GitHub repository, or a folder inside it.

	Every add-on that downloads a repository names it this way (EloquenceDictionaryManager ADR 0003),
	so a download and an installed copy of the same dataset share one ID. Returns None for a
	repository whose name would produce one of the registered IDs without being that dataset.
	"""
	registered = _REGISTERED_SET_IDS.get((owner.lower(), repository.lower()))
	set_id = registered or f"github.{_encode_name(owner)}.{_encode_name(repository)}"
	if registered is None and set_id in _REGISTERED_SET_IDS.values():
		return None
	folder = "/".join(part for part in re.split(r"[/\\]", folder) if part)
	return f"{set_id}..{_encode_name(folder)}" if folder else set_id


def _encode_name(name):
	"""Percent-encoding with "." in place of "%": only a-z, 0-9 and "-" are kept as they are."""
	encoded = []
	for character in name:
		if character.isascii() and (character.isalnum() or character == "-"):
			encoded.append(character.lower())
		else:
			encoded.extend(f".{byte:02x}" for byte in character.encode("utf-8"))
	return "".join(encoded)
