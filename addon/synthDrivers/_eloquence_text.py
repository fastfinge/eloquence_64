"""Pure Eloquence Text Builder and Engine Encoding selection."""

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType

from . import _text_preprocessing


pause_re = re.compile(r"([a-zA-Z0-9]|\s)([,.:;?!)])(\2*?)(\s|[\\/]|$|$)")
time_re = re.compile(r"(\d):(\d+):(\d+)")
punctuation = b",.?:;)(?!"
# Closing brackets openevv names when anything at all separates them from the
# text on their left.  See _insert_pause_commands() and attach_spaced_brackets().
_CLOSING_BRACKETS = ")]}"

_ENGINE_ENCODINGS = MappingProxyType(
	{
		393216: "gb18030",
		524288: "cp932",
		655360: "cp949",
	}
)

_BREAK_FACTORS = MappingProxyType(
	{
		10: 1,
		43: 2,
		60: 3,
		75: 4,
		85: 5,
	}
)


# The backquote codes a pronunciation dictionary value may carry, as Eloquence
# Dictionary Manager's validator defines them: a phonetic string, an emphasis
# code, or a spelling-mode code.  _keep_dictionary_codes() checks where each may sit.
_DICTIONARY_CODE = re.compile(r"`(?:(?P<spr>\[)|(?P<emphasis>00|[0-4])(?=\s)|ts(?P<spelling>[0-3])(?!\d))")
# Spelling mode outlives the utterance that turned it on, on the proprietary
# engine and openevv alike, so every utterance opens by turning it off.
SPELLING_RESET = "`ts0 "


class Backquotes(Enum):
	"""What the Eloquence Text Builder does with a backquote in spoken text."""

	SPACES = "spaces"
	"""Every backquote becomes a space: backquote voice tags are off."""
	COMMANDS = "commands"
	"""Every backquote reaches the engine: backquote voice tags are on."""
	DICTIONARY_CODES = "dictionaryCodes"
	"""Only the codes a dictionary value may carry reach the engine."""


@dataclass(frozen=True)
class BuildOptions:
	"""Settings snapshotted for one Eloquence Text Builder invocation."""

	volume: int
	rate: int
	pause_mode: int
	backquotes: Backquotes
	abbreviation_dict: bool
	phrase_prediction: bool
	# Set only for the in-process openevv backend, which speaks the *name* of a
	# bracket that anything separates from its text.  Guards two rewrites: the
	# whitespace one in attach_spaced_brackets(), and the Pause Policy's command
	# placement in _insert_pause_commands().
	openevv_bracket_fixes: bool = False


def _insert_pause_commands(text: str, command: str, protect_closing_brackets: bool) -> str:
	"""Place a Pause Policy command before each punctuation mark.

	``protect_closing_brackets`` skips a closing bracket that is up against the
	text on its left, because inserting the command there would separate the two
	and openevv then speaks the bracket's name -- the very thing
	attach_spaced_brackets() has just finished preventing.  Measured at 11025 Hz
	against the proprietary engine, which is unaffected either way: "paren)" is
	1.00x, "paren `p0)" is 1.51x and "paren )" is also 1.51x, all three being
	23144 samples on the proprietary engine.

	Only closing brackets need this.  An opening bracket is unaffected by a
	preceding command (" `p0(letter" measures 1.00x), and nothing in the pipeline
	puts one there today in any case -- but the check is written against a set of
	brackets rather than ")" alone so that adding one to pause_re cannot
	reintroduce this quietly.

	A bracket that whitespace still separates from its text keeps its command: it
	is standing alone, as in character navigation, and being announced is then
	correct rather than a defect.
	"""

	def replace(match):
		prefix, mark, repeats, suffix = match.groups()
		if protect_closing_brackets and mark in _CLOSING_BRACKETS and not prefix.isspace():
			return match.group(0)
		return f"{prefix} {command}{mark}{repeats}{suffix}"

	return pause_re.sub(replace, text)


def _keep_dictionary_codes(text: str) -> str:
	"""Turn every backquote a dictionary value may not carry into a space.

	Eloquence Dictionary Manager's validation rule, applied backquote by
	backquote instead of rejecting the text: a phonetic string (`[...]) or an
	emphasis code (`0 to `4, or `00, followed by whitespace) must start the text
	or follow whitespace.  `ts1 to `ts3 may sit anywhere.  `ts0 must start the
	text or follow whitespace, because the engines ignore one glued to the word
	before it.  Text that leaves spelling on gets a `ts0 at its end.
	"""

	kept = []
	spelling_on = False
	start = 0
	position = text.find("`")
	while position >= 0:
		match = _DICTIONARY_CODE.match(text, position)
		after_space = position == 0 or text[position - 1].isspace()
		turns_spelling_on = match is not None and match["spelling"] not in (None, "0")
		if match is not None and (after_space or turns_spelling_on):
			if match["spelling"] is not None:
				spelling_on = turns_spelling_on
		else:
			kept.append(text[start:position])
			kept.append(" ")
			start = position + 1
		position = text.find("`", position + 1)
	kept.append(text[start:])
	if spelling_on:
		kept.append(" `ts0")
	return "".join(kept)


def _engine_encode(text: str, voice_id) -> bytes:
	encoding = _ENGINE_ENCODINGS.get(voice_id)
	if encoding is not None:
		return text.encode(encoding, errors="replace")
	text_bytes = _text_preprocessing._wchar_to_mbcs(text)
	if text_bytes is None:
		text_bytes = text.encode("mbcs", errors="replace")
	return text_bytes


def build(text: str, voice_id: int, options: BuildOptions, opens_utterance: bool = True) -> bytes:
	"""Build engine-ready Eloquence Text bytes for a Voice ID.

	Only the fragment that opens an utterance sets the volume inline.  openevv
	follows IBM's 4.3 and ends the stretch of text at any voice change, `vv to
	the volume it already has included, so a `vv opening every fragment put a
	pause and a fresh intonation wherever NVDA split a sentence -- at every line
	break in Say All.  ETI's 6.1 does not.  The volume is the engine's already,
	through eciSetVoiceParam, so later fragments lose nothing by going without.
	See tests/test_openevv_phrasing.py.

	The same fragment also turns spelling mode off.  A dictionary value that
	turns it on and never off would otherwise have every later utterance
	spelled, until the engine restarts.
	"""

	try:
		voice_id = int(voice_id)
	except (TypeError, ValueError):
		pass
	text = _text_preprocessing.preprocess(text, voice_id)
	# Before any backquote command is added, so the rewrite only ever sees user
	# text.  Skipped whenever backquotes reach the engine: with voice tags on the
	# author is addressing the engine directly and spacing may be deliberate, and
	# a dictionary value is previewed as the engine would expand the entry.
	if options.openevv_bracket_fixes and options.backquotes is Backquotes.SPACES:
		text = _text_preprocessing.attach_spaced_brackets(text)
	if options.backquotes is Backquotes.SPACES:
		text = text.replace("`", " ")
	elif options.backquotes is Backquotes.DICTIONARY_CODES:
		text = _keep_dictionary_codes(text)
	# The Pause Policy reads the character before each mark, and this prefix is
	# what gives a fragment opening on a mark a space there.  So it stays on
	# until the policy has run, whichever fragment this is, and only then goes.
	volume = f"`vv{options.volume} "
	text = volume + text
	if options.pause_mode == 0:
		text = _insert_pause_commands(text, "`p0", options.openevv_bracket_fixes)
	elif options.pause_mode == 2:
		text = _insert_pause_commands(text, "`p1", options.openevv_bracket_fixes)
	if opens_utterance:
		text = SPELLING_RESET + text
	else:
		text = text[len(volume) :]
	text = time_re.sub(r"\1:\2 \3", text)
	text = f"`da{int(options.abbreviation_dict)} {text}"
	text = f"`pp{int(options.phrase_prediction)} {text}"
	return _engine_encode(text, voice_id)


def break_fragment(time_ms: int, options: BuildOptions) -> bytes:
	"""Build an engine-ready break fragment for the snapshotted speech rate."""

	keys = sorted(_BREAK_FACTORS.keys())
	if options.rate <= keys[0]:
		factor = _BREAK_FACTORS[keys[0]]
	elif options.rate >= keys[-1]:
		factor = _BREAK_FACTORS[keys[-1]]
	else:
		# Interpolation lands exactly on the table value when the rate is a key.
		left_index = [index for index, rate in enumerate(keys) if rate < options.rate][-1]
		right_index = left_index + 1
		left_rate = keys[left_index]
		right_rate = keys[right_index]
		factor = 1.0 * _BREAK_FACTORS[left_rate] + (
			_BREAK_FACTORS[right_rate] - _BREAK_FACTORS[left_rate]
		) * (options.rate - left_rate) / (right_rate - left_rate)
	pause_factor = int(factor * time_ms)
	return f"`p{pause_factor}.".encode("ascii")


def trailing_pause(last_built: bytes, options: BuildOptions) -> "bytes | None":
	"""Build the ending fragment required by the active Pause Policy."""

	trimmed = last_built.rstrip()
	if trimmed and trimmed[-1] in punctuation:
		return None
	pause_value = 0 if options.pause_mode == 0 else 1
	return f"`p{pause_value} ".encode("ascii")
