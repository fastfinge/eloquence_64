"""Pure Eloquence Text Builder and Engine Encoding selection."""

from dataclasses import dataclass
import re
from types import MappingProxyType

from . import _text_preprocessing


pause_re = re.compile(r"([a-zA-Z0-9]|\s)([,.:;?!)])(\2*?)(\s|[\\/]|$|$)")
time_re = re.compile(r"(\d):(\d+):(\d+)")
punctuation = b",.?:;)(?!"

_ENGINE_ENCODINGS = MappingProxyType({
	393216: "gb18030",
	524288: "cp932",
	655360: "cp949",
})

_BREAK_FACTORS = MappingProxyType({
	10: 1,
	43: 2,
	60: 3,
	75: 4,
	85: 5,
})


@dataclass(frozen=True)
class BuildOptions:
	"""Settings snapshotted for one Eloquence Text Builder invocation."""

	volume: int
	rate: int
	pause_mode: int
	backquote_tags: bool
	abbreviation_dict: bool
	phrase_prediction: bool


def _engine_encode(text: str, voice_id) -> bytes:
	encoding = _ENGINE_ENCODINGS.get(voice_id)
	if encoding is not None:
		return text.encode(encoding, errors="replace")
	text_bytes = _text_preprocessing._wchar_to_mbcs(text)
	if text_bytes is None:
		text_bytes = text.encode("mbcs", errors="replace")
	return text_bytes


def build(text: str, voice_id: int, options: BuildOptions) -> bytes:
	"""Build engine-ready Eloquence Text bytes for a Voice ID."""

	try:
		voice_id = int(voice_id)
	except (TypeError, ValueError):
		pass
	text = _text_preprocessing.preprocess(text, voice_id)
	if not options.backquote_tags:
		text = text.replace("`", " ")
	text = f"`vv{options.volume} {text}"
	if options.pause_mode == 0:
		text = pause_re.sub(r"\1 `p0\2\3\4", text)
	elif options.pause_mode == 2:
		text = pause_re.sub(r"\1 `p1\2\3\4", text)
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
