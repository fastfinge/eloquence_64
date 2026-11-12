"""Entry rules for western Pronunciation Dictionaries.

What makes one key and value valid for the Eloquence Engine, in each of the three slots a western
language has: main (exact words), root (word roots) and abbr (abbreviations). The rules, and the
identifiers they are reported under, are the ones EloquenceDictionaryManager publishes as shared
examples, so its editor and this add-on agree on what a valid entry is. tests/data holds a copy of
those examples and tests/test_dictionary_entries.py checks every one.

An invalid entry never stops a file from loading: the engine ignores it at lookup and falls back to
its own pronunciation. So these rules exist to tell the user which entries do nothing, not to
protect the engine. Chinese, Japanese and Korean dictionaries have a different format and are not
covered here. No NVDA dependencies.
"""

import re
import string
import unicodedata

SLOTS = ("main", "root", "abbr")
# Every western Eloquence language reads its Pronunciation Dictionaries in the Windows ANSI code page.
WESTERN_ENCODING = "cp1252"
# Agreed limits, in bytes once encoded. The longest entry in the community's files is a 45-byte key
# and a 91-byte value.
MAX_KEY_BYTES = 128
MAX_VALUE_BYTES = 512

NEVER_MATCHES = "abbreviationKeyNeverMatches"

# Measured on the proprietary engine, openevv and ReLoq C: US English loads an abbreviation key with
# a hyphen, slash or ampersand and then never matches it. British English is assumed to do the same.
_ENGLISH = frozenset({"enu", "eng"})
_NEVER_MATCHING_CHARACTERS = frozenset("-/&")
# A period joins two words of an abbreviation, and a hyphen may follow it, as in "Dipl.-Ing.".
_ABBREVIATION_JOIN = re.compile(r"\.-?")
_ENGLISH_ABBREVIATION_JOIN = re.compile(r"\.-?|[-/&]")
_SPR_OPEN = "`["


def entry_problems(language, slot, key, value, encoding=WESTERN_ENCODING):
	"""Return the identifier of every rule this entry breaks; an empty tuple means it is valid."""
	problems = _mechanical_problems("key", key) + _mechanical_problems("value", value)
	if not problems:
		problems += _SLOT_RULES[slot](language, key, value)
	for text in (key, value):
		if not _encodes(text, encoding):
			problems.append("unencodableCharacter")
	# A field that cannot be encoded has no length in bytes, so it fails the encoding rule alone.
	if _encodes(key, encoding) and len(key.encode(encoding)) > MAX_KEY_BYTES:
		problems.append("keyTooLong")
	if _encodes(value, encoding) and len(value.encode(encoding)) > MAX_VALUE_BYTES:
		problems.append("valueTooLong")
	if slot != "abbr" and not _mechanical_problems("value", value):
		problems += _spr_problems(value)
	return tuple(dict.fromkeys(problems))


def entry_warning(language, slot, key, value, encoding=WESTERN_ENCODING):
	"""Return NEVER_MATCHES for a valid entry the engine loads but will never use, else None."""
	if slot != "abbr" or language not in _ENGLISH or _NEVER_MATCHING_CHARACTERS.isdisjoint(key):
		return None
	if entry_problems(language, slot, key, value, encoding):
		return None
	return NEVER_MATCHES


def _mechanical_problems(field, text):
	"""Characters no line of a dictionary file can hold, whatever the slot."""
	if not text:
		return [f"empty{field.capitalize()}"]
	problems = []
	if "\t" in text:
		problems.append(f"{field}Tab")
	if "\r" in text or "\n" in text:
		problems.append(f"{field}Newline")
	if "\0" in text:
		problems.append(f"{field}Nul")
	return problems


def _encodes(text, encoding):
	try:
		text.encode(encoding)
	except UnicodeEncodeError:
		return False
	return True


def _main_problems(language, key, value):
	problems = []
	if any(character.isspace() for character in key):
		problems.append("mainKeyWhitespace")
	if _is_punctuation(key[-1]):
		problems.append("mainKeyFinalPunctuation")
	return problems


def _root_problems(language, key, value):
	problems = []
	if not _is_word(key):
		problems.append("rootKeyInvalid")
	if not (value.isalpha() or _is_one_spr(value)):
		problems.append("rootValueInvalid")
	return problems


def _abbreviation_problems(language, key, value):
	problems = []
	join = _ENGLISH_ABBREVIATION_JOIN if language in _ENGLISH else _ABBREVIATION_JOIN
	# A trailing period is part of the key: "Dr." matches only "Dr." while "approx" matches both.
	body = key.removesuffix(".")
	if not all(_is_word(word) for word in join.split(body)):
		problems.append("abbreviationKeyInvalid")
	if not all(word.isalpha() for word in re.split("[ -]", value)):
		problems.append("abbreviationValueInvalid")
	return problems


_SLOT_RULES = {"main": _main_problems, "root": _root_problems, "abbr": _abbreviation_problems}


def _is_punctuation(character):
	return character in string.punctuation or unicodedata.category(character).startswith("P")


def _is_word(text):
	"""Letters, optionally with apostrophes inside: not first, not last, and never two together."""
	return all(part.isalpha() for part in text.split("'"))


def _is_one_spr(value):
	"""Whether value is one Symbolic Phonetic Representation and nothing else."""
	return (
		value.startswith(_SPR_OPEN)
		and value.find("]", len(_SPR_OPEN)) == len(value) - 1
		and _SPR_OPEN not in value[len(_SPR_OPEN) :]
	)


def _spr_problems(value):
	"""Check the structure of each `[...] in value.

	The phoneme symbols themselves are not checked: each language has its own table, and the engine
	spells out an SPR it cannot read instead of failing.
	"""
	problems = []
	position = 0
	while (start := value.find(_SPR_OPEN, position)) >= 0:
		body_start = start + len(_SPR_OPEN)
		end = value.find("]", body_start)
		next_open = value.find(_SPR_OPEN, body_start)
		if end < 0 or 0 <= next_open < end:
			problems.append("sprUnclosed")
			break
		body = value[body_start:end]
		if not body:
			problems.append("sprEmpty")
		# Apostrophes quote a two-character symbol, so they come in pairs around exactly two characters.
		quoted = body.split("'")
		if len(quoted) % 2 == 0 or any(len(symbol) != 2 for symbol in quoted[1::2]):
			problems.append("sprQuoteInvalid")
		# A period starts a syllable. With more than one syllable and no primary stress, the engine
		# rejects the whole SPR and spells it out.
		if "." in body and "1" not in body:
			problems.append("sprPrimaryStressRequired")
		position = end + 1
	return problems
