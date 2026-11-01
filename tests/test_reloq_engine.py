"""ReLoq C's eci.dll, the Direct Backend's second engine.

It exports the ECI shape but differs from it in ways EciEngine has to absorb,
all measured against ReLoq C 1.0.1:

* No eciSetVoiceParam, eciGetVoiceParam or eciCopyVoice.  The voice is set
  through its own parameters (RELOQ_VOICE_PARAMS), and variants are emulated.
* eciSetParam(9) is refused for any language but the handle's own, so each
  language needs a handle of its own.
* eciAddText decodes everything as cp1252; eciAddTextUtf8 takes every language.
* eciSynthesize fails until RELOQ_GENERIC_VOICE is on, and backquote commands
  are spoken until RELOQ_PROCESS_TAGS is on.
* eciGetAvailableLanguages reports nothing, by design, while eciNewEx refuses
  only languages it really lacks.

The fake below models those rules; the last class drives the real DLL when the
build has fetched it and Python is 64-bit.
"""

import array
import ctypes
import os
import statistics
import struct
import types
import unittest
from pathlib import Path
from unittest import mock

from addon.synthDrivers import _eci_engine as engine

ENU = engine.LANGS["enu"]
DEU = engine.LANGS["deu"]
JPN = engine.LANGS["jpn"]


class _ReloqDll:
	"""Per-handle parameters, and ReLoq C's refusals."""

	def __init__(self, languages=(ENU, DEU, JPN)):
		self.languages = set(languages)
		self.handles = {}
		self.deleted = []
		self.utf8_text = []
		self.dictionary_paths = []
		self.inserted_pitches = []
		self.next_handle = 1000

	def eciNewEx(self, language):
		if language not in self.languages:
			return None
		self.next_handle += 1
		self.handles[self.next_handle] = {engine.ECI_LANGUAGE_PARAM: language, engine.ECI_SAMPLE_RATE: 1}
		return self.next_handle

	def eciDelete(self, handle):
		self.deleted.append(handle)

	def eciSetParam(self, handle, param, value):
		params = self.handles[handle]
		if param == engine.ECI_LANGUAGE_PARAM and value != params[param]:
			return -1
		if param in (engine.ECI_INPUT_TYPE, 41, 42):
			return -1
		previous = params.get(param, 0)
		params[param] = value
		return previous

	def eciGetParam(self, handle, param):
		return self.handles[handle].get(param, 0)

	def eciAddTextUtf8(self, handle, text, length, character_mode):
		self.utf8_text.append((handle, text, length, character_mode))
		return 1

	def eciInsertPitch(self, handle, value):
		self.inserted_pitches.append((handle, value))
		return 1

	def eciSetDictionaryPath(self, handle, path):
		self.dictionary_paths.append((handle, path))
		return 1

	def eciAddText(self, handle, text):
		raise AssertionError("ReLoq C must be given UTF-8 through eciAddTextUtf8")

	def eciSetVoiceParam(self, *args):
		raise AssertionError("ReLoq C does not export eciSetVoiceParam")

	def eciGetVoiceParam(self, *args):
		raise AssertionError("ReLoq C does not export eciGetVoiceParam")

	def eciCopyVoice(self, *args):
		raise AssertionError("ReLoq C does not export eciCopyVoice")

	def __getattr__(self, name):
		if name.startswith("eci") and name.endswith("Dict"):
			raise AssertionError(f"ReLoq C does not export {name}")

		def anything(*args):
			return 1

		return anything


def _start(dll, variant=0, data_directory=""):
	config = engine.EngineConfig(
		eci_path="eci.dll",
		data_directory=data_directory,
		language_code="enu",
		enable_abbrev_dict=True,
		enable_phrase_prediction=True,
		voice_variant=variant,
		rewrite_ini=False,
		reloq=True,
	)
	instance = engine.EciEngine(lambda event, **payload: None, config)
	with mock.patch.object(ctypes.windll, "LoadLibrary", return_value=dll), mock.patch.object(
		engine.EciEngine, "_declare_signatures"
	):
		instance.start()
	return instance


class StartTests(unittest.TestCase):
	def test_generic_voice_and_tag_processing_are_turned_on(self):
		dll = _ReloqDll()
		instance = _start(dll)
		params = dll.handles[instance._handle]
		self.assertEqual(params[engine.RELOQ_GENERIC_VOICE], 1)
		self.assertEqual(params[engine.RELOQ_PROCESS_TAGS], 1)

	def test_dictionaries_load_through_its_own_entry_point(self):
		dll = _ReloqDll()
		directory = os.path.dirname(__file__)
		instance = _start(dll, data_directory=directory)
		self.assertEqual(dll.dictionary_paths, [(instance._handle, directory.encode("utf-8"))])

	def test_a_variant_is_emulated_from_the_preset_table(self):
		dll = _ReloqDll()
		instance = _start(dll, variant=2)
		params = dll.handles[instance._handle]
		gender, head, pitch, inflection, roughness, breathiness, volume = engine.RELOQ_VOICE_PRESETS[2]
		self.assertEqual(params[engine.RELOQ_GENDER], gender)
		self.assertEqual(params[engine.RELOQ_VOICE_PARAMS[engine.HSZ]], head)
		self.assertEqual(params[engine.RELOQ_VOICE_PARAMS[engine.BTH]], breathiness)
		self.assertEqual(instance.get_state()["voiceParams"][engine.PITCH], pitch)
		self.assertEqual(instance.get_state()["voiceParams"][engine.FLUCTUATION], inflection)


class VoiceTests(unittest.TestCase):
	def test_voice_params_go_through_its_parameters(self):
		dll = _ReloqDll()
		instance = _start(dll)
		instance.set_voice_param(engine.RATE, 120)
		self.assertEqual(dll.handles[instance._handle][engine.RELOQ_VOICE_PARAMS[engine.RATE]], 120)
		self.assertEqual(instance.get_state()["voiceParams"][engine.RATE], 120)

	def test_a_temporary_pitch_is_queued_in_the_text(self):
		# NVDA's capital-letter raise and its reset are both queued before the
		# utterance is synthesized, and ReLoq C reads its pitch parameter only
		# then, so setting the parameter would leave only the reset audible.
		dll = _ReloqDll()
		instance = _start(dll)
		instance.set_voice_param(engine.PITCH, 40)
		instance.set_voice_param(engine.PITCH, 90, temporary=True)
		instance.set_voice_param(engine.PITCH, 40, temporary=True)
		self.assertEqual(dll.inserted_pitches, [(instance._handle, 90), (instance._handle, 40)])
		self.assertEqual(dll.handles[instance._handle][engine.RELOQ_VOICE_PARAMS[engine.PITCH]], 40)
		self.assertEqual(instance.get_state()["voiceParams"][engine.PITCH], 40)

	def test_other_temporary_changes_still_set_the_parameter(self):
		dll = _ReloqDll()
		instance = _start(dll)
		instance.set_voice_param(engine.RATE, 80)
		instance.set_voice_param(engine.RATE, 120, temporary=True)
		self.assertEqual(dll.handles[instance._handle][engine.RELOQ_VOICE_PARAMS[engine.RATE]], 120)
		self.assertEqual(instance.get_state()["voiceParams"][engine.RATE], 80)
		self.assertEqual(dll.inserted_pitches, [])

	def test_text_goes_in_as_utf8(self):
		dll = _ReloqDll()
		instance = _start(dll)
		text = "`vv100 こんにちは".encode("utf-8")
		instance.add_text(text)
		self.assertEqual(dll.utf8_text, [(instance._handle, text, len(text), 0)])


class LanguageTests(unittest.TestCase):
	def test_a_language_change_moves_to_a_handle_of_its_own(self):
		dll = _ReloqDll()
		instance = _start(dll)
		english = instance._handle
		instance.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		self.assertNotEqual(instance._handle, english)
		self.assertEqual(dll.handles[instance._handle][engine.ECI_LANGUAGE_PARAM], DEU)
		self.assertEqual(instance.get_state()["params"][engine.ECI_LANGUAGE_PARAM], DEU)

	def test_returning_to_a_language_reuses_its_handle(self):
		dll = _ReloqDll()
		instance = _start(dll)
		english = instance._handle
		instance.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		instance.set_param(engine.ECI_LANGUAGE_PARAM, ENU)
		self.assertEqual(instance._handle, english)
		self.assertEqual(len(dll.handles), 2)

	def test_the_new_handle_carries_the_voice_rate_and_setup(self):
		dll = _ReloqDll()
		instance = _start(dll, variant=4)
		instance.set_voice_param(engine.RATE, 140)
		instance.set_sample_rate(22050)
		instance.set_param(engine.ECI_LANGUAGE_PARAM, JPN)
		params = dll.handles[instance._handle]
		self.assertEqual(params[engine.RELOQ_VOICE_PARAMS[engine.RATE]], 140)
		self.assertEqual(params[engine.RELOQ_GENDER], engine.RELOQ_VOICE_PRESETS[4][0])
		self.assertEqual(params[engine.ECI_SAMPLE_RATE], 2)
		self.assertEqual(params[engine.RELOQ_GENERIC_VOICE], 1)
		self.assertEqual(params[engine.RELOQ_PROCESS_TAGS], 1)

	def test_a_rate_change_reaches_every_handle(self):
		dll = _ReloqDll()
		instance = _start(dll)
		instance.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		instance.set_sample_rate(44100)
		for params in dll.handles.values():
			self.assertEqual(params[engine.ECI_SAMPLE_RATE], 5)

	def test_delete_releases_every_handle(self):
		dll = _ReloqDll()
		instance = _start(dll)
		instance.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		instance.delete()
		self.assertEqual(sorted(dll.deleted), sorted(dll.handles))


class ConstructibleLanguagesTests(unittest.TestCase):
	def test_only_languages_it_can_create_are_reported(self):
		dll = _ReloqDll(languages=(ENU, JPN))
		# Plain functions, which take the argtypes the probe declares, as ctypes'
		# own function pointers do and bound methods do not.
		library = types.SimpleNamespace(
			eciNewEx=lambda language: dll.eciNewEx(language),
			eciDelete=lambda handle: dll.eciDelete(handle),
		)
		with mock.patch.object(ctypes.windll, "LoadLibrary", return_value=library):
			self.assertEqual(engine.constructible_languages("eci.dll"), frozenset({ENU, JPN}))
		self.assertEqual(len(dll.deleted), 2)

	def test_a_library_that_will_not_load_reports_nothing(self):
		with mock.patch.object(ctypes.windll, "LoadLibrary", side_effect=OSError):
			self.assertEqual(engine.constructible_languages("eci.dll"), frozenset())


_REAL_DLL = Path(__file__).parents[1] / "addon" / "synthDrivers" / "reloq" / "eci.dll"


@unittest.skipUnless(
	_REAL_DLL.exists() and struct.calcsize("P") == 8,
	"needs the fetched ReLoq C engine and 64-bit Python",
)
class RealEngineTests(unittest.TestCase):
	def setUp(self):
		self.events = []
		config = engine.EngineConfig(
			eci_path=str(_REAL_DLL),
			data_directory=str(Path(__file__).parents[1] / "addon" / "synthDrivers" / "eloquence"),
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=1,
			rewrite_ini=False,
			reloq=True,
		)
		self.engine = engine.EciEngine(lambda event, **payload: self.events.append((event, payload)), config)
		self.engine.start()
		self.addCleanup(self.engine.delete)

	def _speak(self, *parts):
		self.events.clear()
		for part in parts:
			if isinstance(part, int):
				self.engine.insert_index(part)
			else:
				self.engine.add_text(part.encode("utf-8"))
		self.engine.synthesize()
		audio = sum(len(p["data"]) for e, p in self.events if e == "audio") // 2
		indexes = [p["index"] for e, p in self.events if e == "audio" and p["index"] is not None]
		return audio, indexes

	def test_every_language_it_reports_speaks(self):
		languages = engine.constructible_languages(str(_REAL_DLL))
		self.assertTrue(languages)
		for voice_id in sorted(languages):
			with self.subTest(language=engine.LANG_BY_ID[voice_id]):
				self.engine.set_param(engine.ECI_LANGUAGE_PARAM, voice_id)
				audio, _indexes = self._speak("`pp0 `da1 `vv100 1 2 3.")
				self.assertGreater(audio, 0)

	def test_the_text_builder_preamble_is_obeyed_not_spoken(self):
		plain, _ = self._speak("Hello world, this is a test.")
		annotated, _ = self._speak("`pp0 `da1 `vv80 Hello `p0, world `p0, this is a test.")
		self.assertLess(abs(annotated - plain) / plain, 0.25)

	def test_indexes_arrive_in_order(self):
		_audio, indexes = self._speak("First part.", 5, " Second part.", 6, "", engine.FINAL_INDEX)
		self.assertEqual(indexes, [5, 6])

	def test_cjk_text_is_spoken(self):
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, JPN)
		audio, _ = self._speak("こんにちは世界")
		self.assertGreater(audio, 0)

	def _f0(self):
		"""Median fundamental of the last utterance, by autocorrelation."""
		pcm = array.array("h")
		pcm.frombytes(b"".join(p["data"] for e, p in self.events if e == "audio"))
		rate = self.engine.sample_rate
		lags = range(rate // 400, rate // 60)
		estimates = []
		for start in range(0, len(pcm) - 512, 256):
			frame = pcm[start : start + 512]
			if max(abs(v) for v in frame) < 1500:
				continue
			mean = sum(frame) / len(frame)
			frame = [v - mean for v in frame]
			energy = sum(v * v for v in frame)
			best = max(lags, key=lambda lag: sum(a * b for a, b in zip(frame, frame[lag:])))
			if sum(a * b for a, b in zip(frame, frame[best:])) > 0.4 * energy:
				estimates.append(rate / best)
		return statistics.median(estimates)

	def test_a_capital_letter_is_raised(self):
		# The sequence the driver sends for a capital with "Capital pitch
		# change" on: reset to base at the start of the utterance, raise, the
		# letter, reset -- all before synthesis.  Setting the parameter for each
		# step left only the reset audible (114.8 Hz either way on 1.0.1).
		self.engine.set_voice_param(engine.PITCH, 65)
		self._speak("`vv100 A")
		plain = self._f0()
		self.events.clear()
		self.engine.set_voice_param(engine.PITCH, 65, temporary=True)
		self.engine.set_voice_param(engine.PITCH, 95, temporary=True)
		self.engine.add_text("`vv100 A".encode("utf-8"))
		self.engine.set_voice_param(engine.PITCH, 65, temporary=True)
		self.engine.synthesize()
		self.assertGreater(self._f0(), plain * 1.5)
		self.assertEqual(self.engine.get_state()["voiceParams"][engine.PITCH], 65)
		# And the raise ends with the utterance.
		self._speak("`vv100 A")
		self.assertAlmostEqual(self._f0(), plain, delta=plain * 0.05)

	def _longest_inner_silence(self):
		pcm = array.array("h")
		pcm.frombytes(b"".join(p["data"] for e, p in self.events if e == "audio"))
		loud = [k for k, v in enumerate(pcm) if abs(v) > 300]
		longest = run = 0
		for v in pcm[loud[0] : loud[-1]]:
			run = run + 1 if abs(v) <= 300 else 0
			longest = max(longest, run)
		return longest

	def test_a_split_utterance_has_no_pause_at_the_split(self):
		# NVDA speaks "Alt+a" in a dialog as "Alt+" then "a" in character mode,
		# which reaches the engine as two fragments.  With `vv opening the
		# second, ReLoq C paused 4708 samples between them.
		from addon.synthDrivers import _eloquence_text

		options = _eloquence_text.BuildOptions(
			volume=100,
			rate=50,
			pause_mode=0,
			backquote_tags=False,
			abbreviation_dict=False,
			phrase_prediction=False,
			utf8=True,
			inline_volume=False,
		)
		enu = engine.LANGS["enu"]
		self._speak(_eloquence_text.build("Apply  button  Alt+  a", enu, options).decode("utf-8"))
		whole = self._longest_inner_silence()
		self._speak(
			_eloquence_text.build("Apply  button  Alt+  ", enu, options).decode("utf-8"),
			_eloquence_text.build("a", enu, options).decode("utf-8"),
		)
		self.assertLessEqual(self._longest_inner_silence(), whole * 1.2)

	def test_rate_changes_the_length(self):
		self.engine.set_voice_param(engine.RATE, 50)
		slow, _ = self._speak("Hello world, this is a test.")
		self.engine.set_voice_param(engine.RATE, 150)
		fast, _ = self._speak("Hello world, this is a test.")
		self.assertLess(fast, slow)


if __name__ == "__main__":
	unittest.main()
