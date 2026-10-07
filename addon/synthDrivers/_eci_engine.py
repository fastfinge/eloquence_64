"""Shared ctypes wrapper around an ECI-compatible Eloquence Engine.

This module is imported by two different processes with two different bitnesses:

* The Eloquence Host Process (32-bit, frozen by PyInstaller) uses it to drive the
  proprietary 32-bit ``ECI.DLL``, and forwards engine events over the Host
  Channel.
* The Synth Driver side (64-bit, in NVDA's own process) uses it to drive
  openevv's 64-bit ``eci.dll`` directly, with no host process and no IPC.

Two rules keep that dual use working, and both are asserted by the test suite
rather than left to convention:

* **No NVDA imports.** The Eloquence Host Process must stay self contained.
* **No relative imports.** The Synth Driver side imports this as
  ``from . import _eci_engine``; the frozen host imports it as a top-level
  ``import _eci_engine``. A relative import would only resolve on one side.

The engine reports audio and Speech Indexes through a *sink* supplied by the
caller instead of writing to a transport itself, which is what lets the same
code serve both a Host Channel and an in-process audio queue.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import (
	POINTER,
	c_char_p,
	c_int,
	c_short,
	c_void_p,
	cast,
)
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Dict, Optional

LOGGER = logging.getLogger("eloquence.engine")

Callback = ctypes.WINFUNCTYPE(c_int, c_int, c_int, c_int, c_void_p)

# Every ECI entry point this module calls, with its ctypes signature.
#
# Declaring these is not tidiness.  An ECI handle is a pointer, and without
# argtypes ctypes marshals the Python int it has become as a C int: that
# silently truncates any handle above 2 GiB and, on 64-bit, raises "int too long
# to convert" outright.  A larger openevv build did exactly that, having been
# mapped above the boundary the previous one happened to sit below.  Nothing here
# depends on which engine is loaded -- the proprietary 32-bit DLL is one unlucky
# allocation away from the same fault -- so tests/test_eci_signatures.py checks
# that every call site in this module is covered.
ECI_SIGNATURES = {
	"eciNewEx": ([c_int], c_void_p),
	"eciDelete": ([c_void_p], c_void_p),
	"eciRegisterCallback": ([c_void_p, Callback, c_void_p], None),
	"eciSetOutputBuffer": ([c_void_p, c_int, POINTER(c_short)], c_int),
	"eciAddText": ([c_void_p, c_char_p], c_int),
	"eciInsertIndex": ([c_void_p, c_int], c_int),
	"eciSynthesize": ([c_void_p], c_int),
	"eciSynchronize": ([c_void_p], c_int),
	"eciStop": ([c_void_p], c_int),
	"eciGetParam": ([c_void_p, c_int], c_int),
	"eciSetParam": ([c_void_p, c_int, c_int], c_int),
	"eciGetVoiceParam": ([c_void_p, c_int, c_int], c_int),
	"eciSetVoiceParam": ([c_void_p, c_int, c_int, c_int], c_int),
	"eciCopyVoice": ([c_void_p, c_int, c_int], c_int),
	# A dictionary handle is a pointer in its own right, so it needs the same
	# treatment as the engine handle.
	"eciNewDict": ([c_void_p], c_void_p),
	"eciSetDict": ([c_void_p, c_void_p], c_int),
	"eciLoadDict": ([c_void_p, c_void_p, c_int, c_char_p], c_int),
	"eciDeleteDict": ([c_void_p, c_void_p], c_int),
	# Queried by available_languages() on a freshly loaded library, before any
	# engine exists, so it is declared there rather than here.
}

# Eloquence parameter identifiers.
HSZ = 1
PITCH = 2
FLUCTUATION = 3
RGH = 4
BTH = 5
RATE = 6
VLM = 7

# Synthesis state parameters.
ECI_INPUT_TYPE = 1
ECI_SYNTH_MODE = 8  # 0=Sentence, 1=Manual

# Parameter 9 holds the active Voice ID.
ECI_LANGUAGE_PARAM = 9

# Size of the Eloquence Engine's PCM output buffer, in 16-bit samples.  The
# engine fires its audio callback once this many samples are ready, so this sets
# the granularity of every Audio Chunk -- and with it the floor on how soon NVDA
# can start playing an utterance.
#
# At the 11025 Hz output rate one Audio Chunk covers
# OUTPUT_BUFFER_SAMPLES / 11025 seconds: 1100 samples is just under 100 ms,
# where the previous 3300 was just under 300 ms.  The extra Host Channel traffic
# is negligible -- framing and unframing a chunk costs about 1.6 us against the
# ~100 ms of audio it carries -- so the tradeoff is latency against how much
# slack the Audio Playback Pipeline has to absorb a scheduling hiccup.  Lower
# this further only alongside testing for underruns on slow machines.
OUTPUT_BUFFER_SAMPLES = 1100


def output_buffer_samples(rate: int) -> int:
	"""Samples per Audio Chunk at *rate*, holding the chunk's duration constant.

	OUTPUT_BUFFER_SAMPLES is tuned as a duration rather than a count, so it is
	scaled with the rate: leaving it fixed would make a chunk 23 ms at 48 kHz and
	quadruple the callback and Host Channel traffic for no gain.  Scaling by the
	ratio rather than recomputing from milliseconds keeps 11025 Hz on exactly
	1100, so nothing moves at the default rate.
	"""
	return max(1, round(OUTPUT_BUFFER_SAMPLES * rate / SAMPLE_RATE))


def choose_sample_rate(requested: int, supported) -> int:
	"""The rate an engine should actually run at when *requested* is asked for.

	An engine that cannot do the requested rate runs at its best one below it --
	the proprietary engine answers a request for 44100 with 11025 -- and the
	Audio Playback Pipeline follows whichever engine is speaking.  Falling back
	*downwards* matters: picking a higher rate instead would make every utterance
	from that engine play slow and low.
	"""
	if not supported:
		return SAMPLE_RATE
	if requested in supported:
		return requested
	lower = [rate for rate in supported if rate < requested]
	return max(lower) if lower else min(supported)

# A sentinel index value used by Eloquence to mark the end of a chunk.
FINAL_INDEX = 0xFFFF

# The PCM format every ECI-compatible engine here produces.  The Audio Playback
# Pipeline is configured from these rather than from constants of its own, so a
# future engine that differs cannot leave the two out of step.  SAMPLE_RATE is
# the rate every engine supports and starts at; see SAMPLE_RATE_VALUES.
SAMPLE_RATE = 11025
CHANNELS = 1
BITS_PER_SAMPLE = 16

# ECI parameter 5 selects the output sample rate, as an index rather than a rate.
# The mapping is measured, not taken from a header, and the ordering is odd
# because openevv appended its additions to IBM's original three:
#
#   value  0      1      2      3      4      5      6
#   Hz     8000   11025  22050  16000  32000  44100  48000
#
# What each engine actually accepts differs and is probed at run time by
# _probe_sample_rates(), never hardcoded -- the proprietary ECI.DLL rejects
# everything above 11025 with -1, where openevv takes all seven.  Measured
# against openevv main@7ee8c572: these are genuine rate changes rather than
# slower speech, confirmed by measuring F0 on a sustained vowel against each
# claimed rate (103.0-103.9 Hz across all seven, within 0.9%).
ECI_SAMPLE_RATE = 5
SAMPLE_RATE_VALUES: Dict[int, int] = {
	0: 8000,
	1: 11025,
	2: 22050,
	3: 16000,
	4: 32000,
	5: 44100,
	6: 48000,
}

# openevv's own parameter, with no counterpart in IBM's ECI.  Above 11025 Hz
# openevv still synthesises at 11025 and resamples, so without this every higher
# rate is the 11025 Hz voice upsampled, with nothing above ~5.5 kHz.  On, a
# second synthesiser running at 22050 supplies the band above ~5.4 kHz.  Measured
# against openevv main@c3a253fb, energy above 6 kHz relative to the whole
# utterance goes from about -83 dB to -28 dB at 22050, 44100 and 48000 alike,
# with identical duration; at 8000 and 11025 it changes nothing.  It survives
# changes to the sample rate, the language, and the voice; only eciReset clears it.
#
# Not a user setting: there is no rate at which off sounds better.  Set only on
# the Direct Backend (EngineConfig.wideband), since the proprietary engine has no
# such parameter.
ECI_WIDEBAND = 32

LANGS: Dict[str, int] = {
	"esm": 131073,
	"esp": 131072,
	"ptb": 458752,
	"frc": 196609,
	"fra": 196608,
	"fin": 589824,
	"deu": 262144,
	"ita": 327680,
	"enu": 65536,
	"eng": 65537,
	"chs": 393216,  # Mandarin Chinese (0x00060000)
	"jpn": 524288,  # Japanese (0x00080000)
	"kor": 655360,  # Korean (0x000A0000)
}
LANG_BY_ID: Dict[int, str] = {voice_id: language_code for language_code, voice_id in LANGS.items()}
DICTIONARY_LANGUAGE_FALLBACKS: Dict[str, tuple] = {
	"eng": ("enu",),
	"esm": ("esp",),
	"frc": ("fra",),
	"chs": ("enu",),
}

# Voice parameters re-read from the engine whenever the voice or variant changes.
_VOICE_PARAMS = (RATE, PITCH, VLM, FLUCTUATION, HSZ, RGH, BTH)

# Upper bound on how many language ids eciGetAvailableLanguages may report.  The
# ECI enumeration is a fixed-size out parameter, so the caller has to pick the
# ceiling; IBM's engine offers nine and openevv currently one.
_MAX_LANGUAGES = 64


def get_short_path(path):
	"""Returns the 8.3 short path version of a long path, or the original path if it fails."""
	try:
		buf_size = 260
		while True:
			buf = ctypes.create_unicode_buffer(buf_size)
			needed = ctypes.windll.kernel32.GetShortPathNameW(path, buf, buf_size)
			if needed == 0:
				return path
			if needed < buf_size:
				return buf.value
			buf_size = needed
	except Exception:
		return path


def get_dictionary_candidates(language_code: str) -> tuple:
	"""Return main/root/abbreviation dictionary candidates for an Eloquence language."""
	language_code = (language_code or "enu").lower()
	language_codes = (language_code, *DICTIONARY_LANGUAGE_FALLBACKS.get(language_code, ()))
	include_generic = any(code in {"enu", "eng"} for code in language_codes)
	return (
		(*(f"{code}main.dic" for code in language_codes), *(("main.dic",) if include_generic else ())),
		(*(f"{code}root.dic" for code in language_codes), *(("root.dic",) if include_generic else ())),
		(*(f"{code}abbr.dic" for code in language_codes), *(("abbr.dic",) if include_generic else ())),
	)


@dataclass
class EngineConfig:
	eci_path: str
	data_directory: str
	language_code: str
	enable_abbrev_dict: bool
	enable_phrase_prediction: bool
	voice_variant: int
	# openevv resolves its own data relative to the DLL and ships an eci.ini that
	# needs no rewriting, unlike the proprietary engine whose ECI.INI carries
	# absolute C:\dummy\ placeholders.
	rewrite_ini: bool = True
	# Requested output rate in Hz.  Each engine runs at the nearest rate it
	# supports at or below this; see choose_sample_rate().
	sample_rate: int = SAMPLE_RATE
	# Turn on openevv's eciWideband; see ECI_WIDEBAND.  Off for the proprietary
	# engine, which has no such parameter.
	wideband: bool = False


def available_languages(dll_path: str) -> frozenset:
	"""Ask an ECI library which Voice IDs it can actually create.

	Used to decide per Voice Identity whether the in-process engine can serve it
	or whether the Eloquence Host Process has to.  openevv ships US English only
	today, so this is queried at run time rather than hardcoded: a future openevv
	that adds languages starts serving them with no change here.

	Returns an empty set when the library cannot be loaded or does not implement
	the enumeration, which callers must treat as "use the host for everything".
	"""
	try:
		directory = os.path.dirname(os.path.abspath(dll_path))
		if os.path.isdir(directory):
			try:
				os.add_dll_directory(directory)
			except (OSError, AttributeError):
				pass
		dll = ctypes.windll.LoadLibrary(os.path.abspath(dll_path))
		dll.eciGetAvailableLanguages.argtypes = [c_void_p, c_void_p]
		languages = (c_int * _MAX_LANGUAGES)()
		count = c_int(_MAX_LANGUAGES)
		dll.eciGetAvailableLanguages(ctypes.byref(languages), ctypes.byref(count))
	except Exception:
		LOGGER.exception("Could not enumerate languages from %s", dll_path)
		return frozenset()
	reported = max(0, min(count.value, _MAX_LANGUAGES))
	return frozenset(languages[index] for index in range(reported))


def available_sample_rates(dll_path: str) -> tuple:
	"""Ask an ECI library which output rates it takes, without keeping it loaded.

	The companion to available_languages(), and used for the same reason: the
	sample-rate combo box is built from what the engines really support rather
	than from a list in the source, so an openevv release that adds a rate starts
	offering it with no change here.

	Needs a real engine handle, because the parameter is per engine and the only
	way to test a value is to offer it (see EciEngine._probe_sample_rates).
	Returns an empty tuple when the library cannot be loaded or reports nothing,
	which callers must read as "assume only SAMPLE_RATE".
	"""
	handle = None
	dll = None
	try:
		directory = os.path.dirname(os.path.abspath(dll_path))
		if os.path.isdir(directory):
			try:
				os.add_dll_directory(directory)
			except (OSError, AttributeError):
				pass
		dll = ctypes.windll.LoadLibrary(os.path.abspath(dll_path))
		dll.eciNewEx.argtypes = [c_int]
		dll.eciNewEx.restype = c_void_p
		dll.eciSetParam.argtypes = [c_void_p, c_int, c_int]
		dll.eciSetParam.restype = c_int
		dll.eciGetParam.argtypes = [c_void_p, c_int]
		dll.eciGetParam.restype = c_int
		dll.eciDelete.argtypes = [c_void_p]
		previous = os.getcwd()
		if os.path.isdir(directory):
			os.chdir(directory)
		try:
			handle = dll.eciNewEx(LANGS["enu"])
		finally:
			os.chdir(previous)
		if not handle:
			return ()
		rates = []
		for value, rate in SAMPLE_RATE_VALUES.items():
			if dll.eciSetParam(handle, ECI_SAMPLE_RATE, value) == -1:
				continue
			if dll.eciGetParam(handle, ECI_SAMPLE_RATE) == value:
				rates.append(rate)
		return tuple(sorted(rates))
	except Exception:
		LOGGER.exception("Could not enumerate sample rates from %s", dll_path)
		return ()
	finally:
		if handle and dll is not None:
			try:
				dll.eciDelete(handle)
			except Exception:
				LOGGER.exception("eciDelete failed after enumerating sample rates")


class EciEngine:
	"""Wraps access to an ECI-compatible Eloquence library.

	``sink`` is called as ``sink(event_name, **payload)`` for every engine event.
	The Eloquence Host Process forwards those over the Host Channel; the Synth
	Driver side turns them straight into Audio Chunks.
	"""

	def __init__(self, sink: Callable[..., None], config: EngineConfig):
		self._sink = sink
		self._config = config
		self._dll = None  # type: ignore[assignment]
		self._handle = None  # type: ignore[assignment]
		self._dictionary_handle = None
		self._dictionary_handles: Dict[str, object] = {}
		self._loaded_dictionary_languages: set = set()
		self._callback = Callback(self._on_callback)
		self._audio_buffer = BytesIO()
		self._sample_rate = config.sample_rate
		self._supported_sample_rates: Dict[int, int] = {}
		self._samples = output_buffer_samples(self._sample_rate)
		# eciSetOutputBuffer expects a pointer to 16-bit PCM samples.  Using a
		# c_short array keeps the data in the correct format and avoids the
		# char* semantics of create_string_buffer which truncate at the first
		# NUL byte when passed as c_char_p.
		self._buffer = (c_short * self._samples)()
		self._params: Dict[int, int] = {}
		self._voice_params: Dict[int, int] = {}
		self._speaking = False
		self._saw_final_index = False
		self._pending_indexes: list = []
		self._send_disabled = False

	# ------------------------------------------------------------------
	# Event reporting
	def _send_event(self, event: str, **payload: object) -> None:
		if self._send_disabled:
			return
		try:
			self._sink(event, **payload)
		except Exception:
			if not self._send_disabled:
				self._send_disabled = True
				LOGGER.error("Failed to send event %s; further sends disabled", event)

	# ------------------------------------------------------------------
	# Engine management
	def start(self) -> None:
		self._load_dll()

	def _load_dll(self) -> None:
		LOGGER.info("Loading Eloquence library from %s", self._config.eci_path)
		eloquence_dir = os.path.dirname(self._config.eci_path)
		if self._config.rewrite_ini:
			self._rewrite_ini(eloquence_dir)
		# openevv's eci.dll resolves its own data relative to itself, so the
		# directory has to be searchable before the load rather than after.
		if os.path.isdir(eloquence_dir):
			try:
				os.add_dll_directory(eloquence_dir)
			except (OSError, AttributeError):
				pass
		self._dll = ctypes.windll.LoadLibrary(self._config.eci_path)
		self._declare_signatures()

		language_id = LANGS.get(self._config.language_code, LANGS["enu"])
		handle = self._dll.eciNewEx(language_id)
		if not handle:
			raise RuntimeError(
				f"Failed to create an Eloquence handle for language "
				f"{self._config.language_code!r} (0x{language_id:x}) using "
				f"{self._config.eci_path}"
			)
		self._handle = handle
		self._dll.eciRegisterCallback(handle, self._callback, None)
		self._supported_sample_rates = self._probe_sample_rates()
		# Before the first eciSetOutputBuffer, so the buffer is sized for the rate
		# the engine will actually run at.
		self._sample_rate = self._apply_sample_rate(self._config.sample_rate)
		result = self._dll.eciSetOutputBuffer(handle, self._samples, self._buffer)
		if not result:
			raise RuntimeError("eciSetOutputBuffer failed")
		# Allow annotated input so that backquote commands are interpreted instead of spoken.
		self._dll.eciSetParam(handle, ECI_INPUT_TYPE, 1)
		self._params[ECI_INPUT_TYPE] = 1
		if self._config.wideband:
			# An openevv older than the parameter refuses it with -1, which leaves
			# it speaking exactly as before, so a refusal is only worth a note.
			if self._dll.eciSetParam(handle, ECI_WIDEBAND, 1) == -1:
				LOGGER.info("Eloquence engine does not support eciWideband")
			else:
				self._params[ECI_WIDEBAND] = 1
		self._params[ECI_LANGUAGE_PARAM] = self._dll.eciGetParam(handle, ECI_LANGUAGE_PARAM)
		for param in (RATE, PITCH, VLM, FLUCTUATION):
			self._voice_params[param] = self._dll.eciGetVoiceParam(handle, 0, param)
		self._load_dictionaries()
		if self._config.voice_variant:
			self.copy_voice(self._config.voice_variant)
		if self._config.enable_phrase_prediction:
			self._dll.eciSetParam(handle, 42, 1)
		if self._config.enable_abbrev_dict:
			self._dll.eciSetParam(handle, 41, 1)

	def _probe_sample_rates(self) -> Dict[int, int]:
		"""Ask the engine which rates it takes, as {Hz: ECI parameter value}.

		eciSetParam answers with the parameter's *previous* value, or -1 when it
		rejects the one offered, which is the only way to tell what an engine
		supports -- there is no enumeration call for this as there is for
		languages.  The original value is put back afterwards.

		An engine that does not implement the parameter at all reports nothing
		here, and the caller then leaves the parameter alone and runs at
		SAMPLE_RATE.  Degrading to the one rate every engine has beats guessing.
		"""
		original = self._dll.eciGetParam(self._handle, ECI_SAMPLE_RATE)
		supported: Dict[int, int] = {}
		for value, rate in SAMPLE_RATE_VALUES.items():
			if self._dll.eciSetParam(self._handle, ECI_SAMPLE_RATE, value) == -1:
				continue
			# A silent refusal is still a refusal: trust the read-back, not the
			# return value.
			if self._dll.eciGetParam(self._handle, ECI_SAMPLE_RATE) == value:
				supported[rate] = value
		if original >= 0:
			self._dll.eciSetParam(self._handle, ECI_SAMPLE_RATE, original)
		if not supported:
			LOGGER.info("Eloquence engine does not report any selectable sample rate")
		return supported

	def _apply_sample_rate(self, requested: int) -> int:
		"""Put the engine on the best rate it has for *requested*; return it."""
		if not self._supported_sample_rates:
			return SAMPLE_RATE
		rate = choose_sample_rate(requested, self._supported_sample_rates)
		self._dll.eciSetParam(self._handle, ECI_SAMPLE_RATE, self._supported_sample_rates[rate])
		self._params[ECI_SAMPLE_RATE] = self._supported_sample_rates[rate]
		self._samples = output_buffer_samples(rate)
		self._buffer = (c_short * self._samples)()
		return rate

	def set_sample_rate(self, requested: int) -> int:
		"""Change the output rate between utterances; return the effective one.

		Safe on a live engine -- both engines were measured accepting a change on
		an existing handle -- but only between utterances: the output buffer is
		replaced here, and swapping it under a synthesis in flight would hand the
		engine a buffer it is already writing into.
		"""
		rate = self._apply_sample_rate(requested)
		self._sample_rate = rate
		if not self._dll.eciSetOutputBuffer(self._handle, self._samples, self._buffer):
			raise RuntimeError("eciSetOutputBuffer failed")
		return rate

	@property
	def sample_rate(self) -> int:
		return self._sample_rate

	def supported_sample_rates(self) -> tuple:
		return tuple(sorted(self._supported_sample_rates))

	def _declare_signatures(self) -> None:
		"""Give ctypes an argtype for every ECI entry point this module calls."""
		for name, (argtypes, restype) in ECI_SIGNATURES.items():
			try:
				function = getattr(self._dll, name)
			except AttributeError:
				# openevv does not export quite everything the proprietary engine
				# does, and a missing entry point fails loudly at its call site
				# rather than here, where it would take the whole engine down.
				LOGGER.info("Eloquence library does not export %s", name)
				continue
			function.argtypes = argtypes
			function.restype = restype

	def _rewrite_ini(self, eloquence_dir: str) -> None:
		"""Point the proprietary ECI.INI at the real engine directory.

		The shipped ECI.INI carries ``C:\\dummy\\`` placeholders.  A short path is
		used because the legacy DLL cannot read paths outside the ANSI codepage.
		"""
		ini_path = self._config.eci_path[:-3] + "ini"
		with open(ini_path, "r", encoding="utf-8") as f:
			ini_content = f.read()
		short_eloquence_dir = get_short_path(eloquence_dir)
		updated_content = ini_content.replace("C:\\dummy\\", short_eloquence_dir + "\\")
		with open(ini_path, "w", encoding="utf-8") as f:
			f.write(updated_content)

	def _load_dictionaries(self) -> None:
		language_code = (self._config.language_code or "enu").lower()
		if not self._config.data_directory or not os.path.isdir(self._config.data_directory):
			# No directory of .dic files to load.
			#
			# Both backends do load them now.  openevv v0.3 could not: eciLoadDict
			# returned 6 (failure) for every file the proprietary engine accepts
			# with 0, and calling it anyway left the engine in a state where
			# eciDelete raised an access violation.  Measured again on
			# main@7ee8c572, loading the add-on's own ENUmain/ENURoot/ENUabbr
			# returns 0 and produces audio identical to the proprietary engine's,
			# sample for sample, over repeated loads and engine lifecycles.  The
			# 2 MB root dictionary costs about 88 ms once at engine start, against
			# the proprietary engine's 108 ms.
			return
		dictionary_dir = get_short_path(self._config.data_directory)
		dictionary_candidates = get_dictionary_candidates(language_code)
		self._dictionary_handle = self._dictionary_handles.get(language_code)
		if self._dictionary_handle is None:
			self._dictionary_handle = self._dll.eciNewDict(self._handle)
			self._dictionary_handles[language_code] = self._dictionary_handle

		if language_code not in self._loaded_dictionary_languages:
			for index, candidates in enumerate(dictionary_candidates):
				for candidate in candidates:
					path = os.path.join(dictionary_dir, candidate)
					if os.path.exists(path):
						self._dll.eciLoadDict(
							self._handle, self._dictionary_handle, index, path.encode("mbcs")
						)
						break
			self._loaded_dictionary_languages.add(language_code)
		self._dll.eciSetDict(self._handle, self._dictionary_handle)

	# ------------------------------------------------------------------
	# Public API invoked from the controller or the direct backend
	def add_text(self, text: bytes) -> None:
		self._dll.eciAddText(self._handle, text)

	def insert_index(self, index: int) -> None:
		self._dll.eciInsertIndex(self._handle, index)
		if index != FINAL_INDEX:
			self._pending_indexes.append(index)

	def synthesize(self) -> None:
		self._speaking = True
		self._saw_final_index = False
		try:
			self._dll.eciSynthesize(self._handle)
			if not self._dll.eciSynchronize(self._handle):
				LOGGER.warning("eciSynchronize reported failure")
		finally:
			self._speaking = False
			# Ensure any buffered audio is pushed even if the final index was not
			# delivered (for example if the controller stops early).
			self._flush_audio()
			# If no final index was delivered, still emit a final marker so NVDA
			# receives synthDoneSpeaking (e.g. when there is no text to speak).
			if not self._saw_final_index:
				self._report_latest_pending_index()
				self._send_event("audio", data=b"", index=None, final=True)

	def stop(self) -> None:
		"""Reset after a cancellation.

		What actually makes cancellation audible is upstream -- the Speech
		Generation advances and the player is stopped -- so the engine side of this
		is only about not carrying state into the next utterance.  eciStop itself
		aborts nothing either way: a cancellation always finds the engine idle,
		because eciSynthesize has already drained by the time one arrives.

		openevv could not survive this call at all before Mudb0y/openevv#35 was
		fixed, and the add-on carried a flag to skip it there; see
		tests/test_engine_cancellation.py for what that was and why it is gone.
		"""
		self._dll.eciStop(self._handle)
		self._audio_buffer.seek(0)
		self._audio_buffer.truncate(0)
		self._pending_indexes.clear()
		self._saw_final_index = False
		self._speaking = False
		self._send_event("stopped")

	def delete(self) -> None:
		if self._handle:
			if self._dll:
				for dictionary_handle in self._dictionary_handles.values():
					try:
						self._dll.eciDeleteDict(self._handle, dictionary_handle)
					except Exception:
						LOGGER.exception("Failed to delete Eloquence dictionary")
			self._dictionary_handles.clear()
			self._loaded_dictionary_languages.clear()
			self._dictionary_handle = None
			self._dll.eciDelete(self._handle)
			self._handle = None

	def set_param(self, param_id: int, value: int) -> None:
		self._dll.eciSetParam(self._handle, param_id, value)
		self._params[param_id] = value
		# When changing voice (param 9), update all voice parameters
		if param_id == ECI_LANGUAGE_PARAM:
			self._config.language_code = LANG_BY_ID.get(value, "enu")
			self._load_dictionaries()
			for param in _VOICE_PARAMS:
				self._voice_params[param] = self._dll.eciGetVoiceParam(self._handle, 0, param)

	def set_voice_param(self, param_id: int, value: int, temporary: bool = False) -> None:
		self._dll.eciSetVoiceParam(self._handle, 0, param_id, value)
		if not temporary:
			self._voice_params[param_id] = value

	def copy_voice(self, variant: int) -> None:
		self._dll.eciCopyVoice(self._handle, variant, 0)
		for param in _VOICE_PARAMS:
			self._voice_params[param] = self._dll.eciGetVoiceParam(self._handle, 0, param)

	def get_state(self) -> Dict[str, object]:
		return {
			"params": dict(self._params),
			"voiceParams": dict(self._voice_params),
			# The Synth Driver side needs both: the effective rate to configure the
			# Audio Playback Pipeline, and the supported set to build the combo box
			# from what the engines really have rather than from a hardcoded list.
			"sampleRate": self._sample_rate,
			"supportedSampleRates": self.supported_sample_rates(),
		}

	# ------------------------------------------------------------------
	# Callbacks from Eloquence
	def _on_callback(self, handle, message, length, user_data):
		# Returning 2 is ECI's "stop synthesizing" answer.  It is only ever
		# returned here when synthesis is not in flight, and it must stay that
		# way: measured against openevv v0.3, returning 2 from the audio callback
		# *during* eciSynchronize segfaults the process, where the proprietary
		# engine tolerates it.  So an in-flight utterance is never cancelled
		# through this path.  Cancellation instead advances the Speech Generation
		# and stops the player, which is also exactly what the Eloquence Host
		# Process does -- it serves its Host Channel single threaded, so a stop
		# cannot reach its engine until synthesize() has returned either.
		if not self._speaking:
			return 2
		if message == 0:
			# Audio data callback - send immediately without buffering
			data = ctypes.string_at(cast(self._buffer, c_void_p), length * ctypes.sizeof(c_short))
			# Send this chunk immediately to minimize latency
			self._send_event("audio", data=data, index=None, final=False)
		elif message == 2:
			# Index callback
			is_final = length == FINAL_INDEX
			index_value = length if not is_final else None
			if is_final:
				self._report_latest_pending_index()
			else:
				self._discard_pending_indexes_through(length)
			# Send empty chunk with index marker
			self._send_event("audio", data=b"", index=index_value, final=is_final)
			if is_final:
				self._saw_final_index = True
				self._speaking = False
		return 1

	def _discard_pending_indexes_through(self, index: int) -> None:
		"""Forget indexes at or before an index reported by the engine.

		NVDA treats an observed later Speech Index as evidence that earlier
		indexes with no intervening audio were also reached.  Mirroring that rule
		here prevents an older skipped index from being reported out of order at
		final completion.
		"""
		try:
			position = self._pending_indexes.index(index)
		except ValueError:
			return
		del self._pending_indexes[: position + 1]

	def _report_latest_pending_index(self) -> None:
		"""Report the latest index if the engine silently skipped its callback."""
		if not self._pending_indexes:
			return
		index = self._pending_indexes[-1]
		self._pending_indexes.clear()
		# Host logging intentionally records errors only.  Treat this protocol
		# recovery as an error so a silent engine failure leaves diagnostics.
		LOGGER.error("Eloquence skipped index callback %s; reporting it at completion", index)
		self._send_event("audio", data=b"", index=index, final=False)

	def _flush_audio(self, index: Optional[int] = None, force: bool = False, final: bool = False) -> None:
		if self._audio_buffer.tell() == 0:
			if force or final:
				self._send_event("audio", data=b"", index=index, final=final)
			return
		payload = self._audio_buffer.getvalue()
		self._audio_buffer.seek(0)
		self._audio_buffer.truncate(0)
		self._send_event("audio", data=payload, index=index, final=final)


class EciDispatcher:
	"""Executes Host Commands against an EciEngine.

	This is the Host Command protocol itself, and both backends run this same
	code: the Eloquence Host Process reaches it after unpickling a command off
	the Host Channel, and the Synth Driver side's in-process backend calls it
	directly.  Keeping one implementation is what stops the two backends
	answering the same command differently.

	``should_exit`` is set by the ``delete`` command so a transport that owns a
	process can shut itself down; an in-process caller simply ignores it.
	"""

	def __init__(self, sink: Callable[..., None]):
		self._sink = sink
		self.engine: Optional[EciEngine] = None
		self.should_exit = False
		self._handlers = {
			"initialize": self._handle_initialize,
			"addText": self._handle_add_text,
			"insertIndex": self._handle_insert_index,
			"synthesize": self._handle_synthesize,
			"stop": self._handle_stop,
			"delete": self._handle_delete,
			"setParam": self._handle_set_param,
			"setVoiceParam": self._handle_set_voice_param,
			"copyVoice": self._handle_copy_voice,
			"setSampleRate": self._handle_set_sample_rate,
		}

	def knows(self, command: str) -> bool:
		return command in self._handlers

	def handle(self, command: str, payload: Optional[Dict[str, object]] = None) -> Dict[str, object]:
		handler = self._handlers.get(command)
		if handler is None:
			raise KeyError(command)
		return handler(**(payload or {}))

	# ------------------------------------------------------------------
	def _handle_initialize(self, **payload):
		config = EngineConfig(
			eci_path=payload["eciPath"],
			data_directory=payload["dataDirectory"],
			language_code=payload["language"],
			enable_abbrev_dict=payload.get("enableAbbreviationDict", False),
			enable_phrase_prediction=payload.get("enablePhrasePrediction", False),
			voice_variant=payload.get("voiceVariant", 0),
			rewrite_ini=payload.get("rewriteIni", True),
			sample_rate=payload.get("sampleRate", SAMPLE_RATE),
			wideband=payload.get("wideband", False),
		)
		self.engine = EciEngine(self._sink, config)
		self.engine.start()
		return self.engine.get_state()

	def _handle_set_sample_rate(self, rate: int):
		self.engine.set_sample_rate(int(rate))
		return self.engine.get_state()

	def _handle_add_text(self, text: bytes):
		self.engine.add_text(text)
		return {"status": "ok"}

	def _handle_insert_index(self, value: int):
		self.engine.insert_index(value)
		return {"status": "ok"}

	def _handle_synthesize(self):
		self.engine.synthesize()
		return {"status": "ok"}

	def _handle_stop(self):
		self.engine.stop()
		return {"status": "ok"}

	def _handle_delete(self):
		if self.engine:
			self.engine.delete()
		self.should_exit = True
		return {"status": "ok"}

	def _handle_set_param(self, paramId: int, value: int):
		self.engine.set_param(paramId, value)
		return self.engine.get_state()

	def _handle_set_voice_param(self, paramId: int, value: int, temporary: bool = False):
		self.engine.set_voice_param(paramId, value, temporary=temporary)
		if temporary:
			return {"voiceParams": {paramId: value}}
		return self.engine.get_state()

	def _handle_copy_voice(self, variant: int):
		self.engine.copy_voice(variant)
		return self.engine.get_state()
