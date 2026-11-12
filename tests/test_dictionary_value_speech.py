"""Pronunciation dictionary values on the real engines.

Spelling mode must not outlive the utterance that turned it on.  A dictionary
value can turn spelling on with `ts1 to `ts3 and never turn it off, or turn it
off with a `ts0 glued to the word before it, which the engines ignore.  Every
later utterance was then spelled out, until NVDA restarted.  So every fragment
now opens with `ts0, as an utterance opens with `vv for the volume.  Measured 2026-11-12
at 11025 Hz, a plain sentence spoken after one whose dictionary value is
`ts1 abc:

	                        proprietary   openevv main@7ffd7549   ReLoq C
	without the `ts0        8270 ms       8270 ms                 3932 ms
	with it                 3935 ms       3928 ms                 3932 ms
	fresh engine            3935 ms       3928 ms                 3932 ms

ReLoq C does not carry spelling over in the first place.  The opening `ts0
changes nothing else: ordinary speech in every language each engine has was
byte-identical with and without it, on all three engines, and so was a
sentence split with one in front of its second fragment.

previewEciPronunciation() speaks a value as Eloquence Dictionary Manager's Play
new hears it, and that is the entry's own audio, byte for byte, on all three
engines.  On ReLoq C that needs the space that opens the trailing pause (see
_eloquence_text.trailing_pause()).

Needs the fetched engines: 64-bit Python for openevv and ReLoq C, 32-bit for
the proprietary engine, which runs from a scratch copy so the repository's
ECI.INI is never rewritten.

	py -3.13 -m unittest tests.test_dictionary_value_speech
	py -3.13-32 -m unittest tests.test_dictionary_value_speech
"""

import dataclasses
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from addon.synthDrivers import _eci_engine as engine
from addon.synthDrivers import _eloquence_text

_SYNTH = Path(__file__).parents[1] / "addon" / "synthDrivers"
_IS_64_BIT = struct.calcsize("P") == 8
ENU = engine.LANGS["enu"]
KEY = "zqkey"
KEY_SENTENCE = f"Alpha bravo {KEY} charlie delta."
NEXT_SENTENCE = "Charlie delta echo foxtrot, golf hotel india."
ORDINARY = (
	"Hello, world!",
	"NVDA 2026.1 at 3:45:12, costs $4.99 (approx.)",
	"a",
	", and then",
)
PREVIEWED = ("`[.1Ar.0min]", "re `[.1Ar.0min]", "`4 can", "`ts1abc `ts0")
OPTIONS = _eloquence_text.BuildOptions(
	volume=92,
	rate=50,
	pause_mode=0,
	backquotes=_eloquence_text.Backquotes.SPACES,
	abbreviation_dict=False,
	phrase_prediction=False,
)
PREVIEW_OPTIONS = dataclasses.replace(OPTIONS, backquotes=_eloquence_text.Backquotes.DICTIONARY_CODES)


class _DictionaryValueSpeech:
	"""Run against one engine; subclasses say which."""

	dll = None
	wideband = False
	rewrite_ini = False

	def _engine(self, value=None):
		data_directory = ""
		if value is not None:
			data_directory = tempfile.mkdtemp()
			self.addCleanup(shutil.rmtree, data_directory, True)
			(Path(data_directory) / "enumain.dic").write_bytes(f"{KEY}\t{value}\r\n".encode("cp1252"))
		self.events = []
		config = engine.EngineConfig(
			eci_path=str(self.dll),
			data_directory=data_directory,
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
			rewrite_ini=self.rewrite_ini,
			sample_rate=11025,
			wideband=self.wideband,
		)
		instance = engine.EciEngine(lambda event, **payload: self.events.append((event, payload)), config)
		instance.start()
		self.addCleanup(instance.delete)
		return instance

	def _say(self, instance, text, options=OPTIONS):
		"""Speak one utterance as SynthDriver.speak() queues it."""
		self.events.clear()
		built = _eloquence_text.build(text, ENU, options)
		instance.add_text(built)
		trailing = _eloquence_text.trailing_pause(built, options)
		if trailing is not None:
			instance.add_text(trailing)
		instance.synthesize()
		return b"".join(payload["data"] for event, payload in self.events if event == "audio")

	def _next_sentence_after(self, value):
		instance = self._engine(value)
		self._say(instance, KEY_SENTENCE)
		return len(self._say(instance, NEXT_SENTENCE))

	def test_ordinary_speech_is_unchanged_by_the_spelling_reset(self):
		with mock.patch.object(_eloquence_text, "SPELLING_RESET", ""):
			instance = self._engine()
			without = [self._say(instance, text) for text in ORDINARY]
		instance = self._engine()
		with_reset = [self._say(instance, text) for text in ORDINARY]
		for text, before, after in zip(ORDINARY, without, with_reset):
			with self.subTest(text=text):
				self.assertTrue(before)
				self.assertEqual(after, before)

	def test_spelling_left_on_by_a_dictionary_value_stops_at_the_utterance(self):
		fresh = len(self._say(self._engine(), NEXT_SENTENCE))
		for value in ("`ts1 abc", "`ts1abc`ts0", "`ts2 abc`ts0"):
			with self.subTest(value=value):
				self.assertEqual(self._next_sentence_after(value), fresh)

	def test_without_the_reset_the_next_utterance_is_spelled(self):
		# Why the reset is there.  If an engine stops carrying spelling over,
		# this fails, and the reset is merely harmless there.
		fresh = len(self._say(self._engine(), NEXT_SENTENCE))
		with mock.patch.object(_eloquence_text, "SPELLING_RESET", ""):
			spelled = self._next_sentence_after("`ts1 abc")
		self.assertGreater(spelled, fresh * 3 // 2)

	def test_a_preview_is_the_dictionary_entry_s_own_audio(self):
		for value in PREVIEWED:
			with self.subTest(value=value):
				entry = self._say(self._engine(value), KEY)
				preview = self._say(self._engine(), value, PREVIEW_OPTIONS)
				self.assertEqual(preview, entry)
				# With voice tags off the codes would have been read out instead.
				self.assertNotEqual(self._say(self._engine(), value), entry)


@unittest.skipUnless(
	(_SYNTH / "openevv" / "eci.dll").exists() and _IS_64_BIT,
	"needs the fetched openevv engine and 64-bit Python",
)
class OpenevvTests(_DictionaryValueSpeech, unittest.TestCase):
	dll = _SYNTH / "openevv" / "eci.dll"
	wideband = True


@unittest.skipUnless(
	(_SYNTH / "reloq" / "eci.dll").exists() and _IS_64_BIT,
	"needs the fetched ReLoq C engine and 64-bit Python",
)
class ReloqTests(_DictionaryValueSpeech, unittest.TestCase):
	dll = _SYNTH / "reloq" / "eci.dll"

	def test_without_the_reset_the_next_utterance_is_spelled(self):
		self.skipTest("ReLoq C never carried spelling over")


@unittest.skipUnless(
	(_SYNTH / "eloquence" / "ECI.DLL").exists() and not _IS_64_BIT,
	"needs the fetched proprietary engine and 32-bit Python",
)
class ProprietaryTests(_DictionaryValueSpeech, unittest.TestCase):
	rewrite_ini = True

	@classmethod
	def setUpClass(cls):
		# Loading the engine rewrites the ECI.INI next to it with this machine's
		# paths, so it runs from a copy.
		cls._scratch = Path(tempfile.mkdtemp())
		shutil.copytree(_SYNTH / "eloquence", cls._scratch / "eloquence")
		cls.dll = cls._scratch / "eloquence" / "ECI.DLL"

	@classmethod
	def tearDownClass(cls):
		shutil.rmtree(cls._scratch, ignore_errors=True)


if __name__ == "__main__":
	unittest.main()
