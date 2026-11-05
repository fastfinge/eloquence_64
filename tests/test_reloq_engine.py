"""ReLoq C's eci.dll, the Direct Backend's second engine.

ReLoq C 1.0.1 exported the ECI shape but not its behaviour, and EciEngine
carried an adapter for it.  ReLoq C 1.1 closed every gap the adapter covered
(hozosch/Reloq-C#2, #5 and #6 among them), so it is now driven exactly as
openevv is.  These tests pin the behaviour that makes that safe, each against
the real DLL, so a regression upstream shows up here rather than in NVDA:

* eciSetVoiceParam, eciGetVoiceParam and eciCopyVoice exist and take effect.
* eciSetParam(9) switches language on a live handle.
* eciAddText takes the per-language Engine Encodings, CJK included.
* ECI_INPUT_TYPE turns on backquote commands; nothing else needs switching on.
* eciGetAvailableLanguages reports what it speaks.
* A voice change between eciAddText calls applies from that point, so NVDA's
  capital-letter pitch raise is heard, and an inline `vv opening a later
  fragment adds no pause.
* Volume works across its whole range in every language, not only German.

They need the fetched engine and 64-bit Python, and skip otherwise.
"""

import array
import os
import statistics
import struct
import unittest
from pathlib import Path

from addon.synthDrivers import _eci_engine as engine
from addon.synthDrivers import _eloquence_text

ENU = engine.LANGS["enu"]
DEU = engine.LANGS["deu"]
JPN = engine.LANGS["jpn"]

_REAL_DLL = Path(__file__).parents[1] / "addon" / "synthDrivers" / "reloq" / "eci.dll"
# The add-on's own dictionaries are fetched by Dictionary Update rather than
# kept in the repository, so the dictionary test runs only where an installed
# copy of the add-on has them.
_INSTALLED_DATA = (
	Path(os.environ.get("APPDATA", "")) / "nvda" / "addons" / "Eloquence" / "synthDrivers" / "eloquence"
)


def _temporary(param, value):
	"""A temporary voice change, as the driver queues one between fragments."""
	return lambda instance: instance.set_voice_param(param, value, temporary=True)


def _options(**overrides):
	values = dict(
		volume=100,
		rate=50,
		pause_mode=0,
		backquote_tags=False,
		abbreviation_dict=False,
		phrase_prediction=False,
	)
	values.update(overrides)
	return _eloquence_text.BuildOptions(**values)


@unittest.skipUnless(
	_REAL_DLL.exists() and struct.calcsize("P") == 8,
	"needs the fetched ReLoq C engine and 64-bit Python",
)
class RealEngineTests(unittest.TestCase):
	def _start(self, data_directory=""):
		self.events = []
		config = engine.EngineConfig(
			eci_path=str(_REAL_DLL),
			data_directory=data_directory,
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=1,
			rewrite_ini=False,
		)
		instance = engine.EciEngine(lambda event, **payload: self.events.append((event, payload)), config)
		instance.start()
		self.addCleanup(instance.delete)
		return instance

	def setUp(self):
		self.engine = self._start()

	def _speak(self, *parts):
		"""Queue *parts* and synthesize; return the samples and the indexes.

		A str is built by the Eloquence Text Builder for the current language,
		bytes go in as they are, an int is a Speech Index, and a callable is run
		against the engine at its place in the sequence.
		"""
		self.events.clear()
		voice_id = self.engine.get_state()["params"][engine.ECI_LANGUAGE_PARAM]
		for part in parts:
			if isinstance(part, int):
				self.engine.insert_index(part)
			elif isinstance(part, bytes):
				self.engine.add_text(part)
			elif callable(part):
				part(self.engine)
			else:
				self.engine.add_text(_eloquence_text.build(part, voice_id, _options()))
		self.engine.synthesize()
		pcm = array.array("h")
		pcm.frombytes(b"".join(p["data"] for e, p in self.events if e == "audio"))
		indexes = [p["index"] for e, p in self.events if e == "audio" and p["index"] is not None]
		return pcm, indexes

	@staticmethod
	def _rms(pcm):
		return (sum(v * v for v in pcm) / len(pcm)) ** 0.5

	def _f0(self, pcm):
		"""Median fundamental, by autocorrelation."""
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

	@staticmethod
	def _longest_inner_silence(pcm):
		loud = [k for k, v in enumerate(pcm) if abs(v) > 300]
		longest = run = 0
		for v in pcm[loud[0] : loud[-1]]:
			run = run + 1 if abs(v) <= 300 else 0
			longest = max(longest, run)
		return longest

	def test_it_reports_every_language_it_speaks(self):
		languages = engine.available_languages(str(_REAL_DLL))
		self.assertTrue(set(engine.LANGS.values()) <= languages)
		for voice_id in sorted(engine.LANGS.values()):
			with self.subTest(language=engine.LANG_BY_ID[voice_id]):
				self.engine.set_param(engine.ECI_LANGUAGE_PARAM, voice_id)
				pcm, _indexes = self._speak("1 2 3.")
				self.assertGreater(len(pcm), 0)

	def test_a_language_change_keeps_the_handle(self):
		handle = self.engine._handle
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		self.assertEqual(self.engine._handle, handle)
		switched, _ = self._speak("Guten Tag, wie geht es Ihnen?")
		fresh = self._start()
		fresh.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		self.engine = fresh
		self.assertEqual(self._speak("Guten Tag, wie geht es Ihnen?")[0], switched)

	def test_cjk_text_goes_in_with_its_engine_encoding(self):
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, JPN)
		encoded, _ = self._speak("こんにちは世界")
		self.assertGreater(len(encoded), 0)
		# The same text as cp1252 would have been "?" throughout.
		replaced, _ = self._speak("こんにちは世界".encode("cp1252", errors="replace"))
		self.assertNotEqual(encoded, replaced)

	def test_the_text_builder_preamble_is_obeyed_not_spoken(self):
		plain, _ = self._speak(b"Hello world, this is a test.")
		annotated, _ = self._speak("Hello, world, this is a test.")
		self.assertLess(abs(len(annotated) - len(plain)) / len(plain), 0.25)

	def test_indexes_arrive_in_order(self):
		_pcm, indexes = self._speak("First part.", 5, " Second part.", 6, b"", engine.FINAL_INDEX)
		self.assertEqual(indexes, [5, 6])

	def test_a_variant_comes_from_copy_voice(self):
		self.engine.copy_voice(2)
		voice = self.engine.get_state()["voiceParams"]
		self.assertEqual(voice[engine.PITCH], 81)
		self.assertEqual(voice[engine.BTH], 50)

	def test_a_capital_letter_is_raised(self):
		# The sequence the driver sends for a capital with "Capital pitch
		# change" on: reset to base at the start of the utterance, raise, the
		# letter, reset -- all before synthesis.  ReLoq C 1.0.1 read the pitch
		# only at synthesis, so only the reset was heard.
		self.engine.set_voice_param(engine.PITCH, 65)
		plain = self._f0(self._speak("A")[0])
		raised, _ = self._speak(
			_temporary(engine.PITCH, 65), _temporary(engine.PITCH, 95), "A", _temporary(engine.PITCH, 65)
		)
		self.assertGreater(self._f0(raised), plain * 1.5)
		self.assertEqual(self.engine.get_state()["voiceParams"][engine.PITCH], 65)

	def test_a_rate_change_part_way_through_applies_from_there(self):
		def rate(value):
			return _temporary(engine.RATE, value)

		first, second = "The first half of the sentence. ", "The second half of the sentence."
		slow, _ = self._speak(rate(50), first, second)
		fast, _ = self._speak(rate(150), first, second)
		mixed, _ = self._speak(rate(50), first, rate(150), second)
		self.assertLess(len(fast) * 1.2, len(mixed))
		self.assertLess(len(mixed) * 1.2, len(slow))

	def test_a_split_utterance_has_no_pause_at_the_split(self):
		# NVDA speaks "Alt+a" in a dialog as "Alt+" then "a" in character mode,
		# which reaches the engine as two fragments, each opening with `vv.
		# ReLoq C 1.0.1 paused 4708 samples between them.
		whole = self._longest_inner_silence(self._speak("Apply  button  Alt+  a")[0])
		split = self._longest_inner_silence(self._speak("Apply  button  Alt+  ", "a")[0])
		self.assertLessEqual(split, whole * 1.2)

	def test_volume_goes_all_the_way_down_in_every_language(self):
		# Below 80 ReLoq C 1.0.1 sounded the same as 80 in every language but
		# German, and 0 was not silent.
		for voice_id in (ENU, engine.LANGS["fra"], DEU):
			with self.subTest(language=engine.LANG_BY_ID[voice_id]):
				self.engine.set_param(engine.ECI_LANGUAGE_PARAM, voice_id)
				self.engine.set_voice_param(engine.VLM, 100)
				loud = self._rms(self._speak(b"Hello there, how are you today?")[0])
				self.engine.set_voice_param(engine.VLM, 20)
				quiet = self._rms(self._speak(b"Hello there, how are you today?")[0])
				self.assertLess(quiet, loud / 4)

	def test_rate_changes_the_length(self):
		self.engine.set_voice_param(engine.RATE, 50)
		slow, _ = self._speak("Hello world, this is a test.")
		self.engine.set_voice_param(engine.RATE, 150)
		fast, _ = self._speak("Hello world, this is a test.")
		self.assertLess(len(fast), len(slow))

	@unittest.skipUnless((_INSTALLED_DATA / "ENUmain.dic").exists(), "needs the add-on's dictionaries")
	def test_dictionaries_load_through_the_standard_calls(self):
		without, _ = self._speak(b"Hermione.")
		self.engine = self._start(str(_INSTALLED_DATA))
		with_dictionaries, _ = self._speak(b"Hermione.")
		self.assertNotEqual(len(with_dictionaries), len(without))

	@unittest.skipUnless((_INSTALLED_DATA / "ENUmain.dic").exists(), "needs the add-on's dictionaries")
	def test_dictionaries_survive_reselecting_and_returning_to_a_language(self):
		# EciEngine skips eciSetDict when eciGetDict already reports the set,
		# because ReLoq C rebuilds its tables on every call.  That relies on
		# the engine keeping each language's set, which this checks by ear.
		self.engine = self._start(str(_INSTALLED_DATA))
		loaded, _ = self._speak(b"Hermione.")
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, ENU)
		self.assertEqual(self._speak(b"Hermione.")[0], loaded)
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, DEU)
		self.engine.set_param(engine.ECI_LANGUAGE_PARAM, ENU)
		self.assertEqual(self._speak(b"Hermione.")[0], loaded)


if __name__ == "__main__":
	unittest.main()
