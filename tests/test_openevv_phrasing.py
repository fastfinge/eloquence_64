"""A sentence split across fragments must speak as one sentence on openevv.

NVDA's Say All puts a Speech Index at every line break, so a sentence that
wraps reaches the engine as two fragments.  Until this was fixed every fragment
opened with `` `vv<volume> ``, and openevv, following IBM's 4.3, ends the
stretch of text at any change of voice, speed, pitch, inflection or volume --
even to the volume it already has (openevv docs/quirks.md, "Text that stops
without punctuation does not pause like a sentence").  ETI's 6.1 does not.
Measured 2026-11-05 against openevv main@ec5a1103 at 11025 Hz:

	                                     openevv    proprietary 6.1
	one fragment                         6253 ms    6230 ms
	split, second opens `vv80            6503 ms    6230 ms
	split, second opens `pp0 `da0        6253 ms    6230 ms

The 250 ms is a ~140 ms pause at the split plus the intonation starting again,
heard as a pause in the middle of a paragraph.  `vs, `vb, `vf, `v1 and
eciSetVoiceParam between the fragments all do the same; `pp and `da do not.

So the Eloquence Text Builder sets the volume only in the fragment that opens
an utterance -- it is the engine's already, through eciSetVoiceParam.

Needs the fetched openevv engine and 64-bit Python:

	py -3.13 -m unittest tests.test_openevv_phrasing
"""

import array
import struct
import unittest
from pathlib import Path

from addon.synthDrivers import _eci_engine as engine
from addon.synthDrivers import _eloquence_text

_REAL_DLL = Path(__file__).parents[1] / "addon" / "synthDrivers" / "openevv" / "eci.dll"
ENU = engine.LANGS["enu"]
FIRST = "The committee reviewed the proposal carefully and"
SECOND = "decided that the new budget should be approved next week."


@unittest.skipUnless(
	_REAL_DLL.exists() and struct.calcsize("P") == 8,
	"needs the fetched openevv engine and 64-bit Python",
)
class SplitSentenceTests(unittest.TestCase):
	def setUp(self):
		self.events = []
		config = engine.EngineConfig(
			eci_path=str(_REAL_DLL),
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=1,
			rewrite_ini=False,
			wideband=True,
		)
		self.engine = engine.EciEngine(lambda event, **payload: self.events.append((event, payload)), config)
		self.engine.start()
		self.addCleanup(self.engine.delete)
		self.options = _eloquence_text.BuildOptions(
			volume=80,
			rate=50,
			pause_mode=0,
			backquote_tags=False,
			abbreviation_dict=False,
			phrase_prediction=False,
			openevv_bracket_fixes=True,
		)
		# Say something first, so every measured utterance is a second one:
		# openevv carries state from one utterance into the next, as 6.1 does.
		self._samples([(FIRST + " " + SECOND, True)])

	def _samples(self, fragments):
		self.events.clear()
		for position, (text, opens) in enumerate(fragments):
			if position:
				self.engine.insert_index(position)
			self.engine.add_text(_eloquence_text.build(text, ENU, self.options, opens_utterance=opens))
		self.engine.synthesize()
		pcm = array.array("h")
		pcm.frombytes(b"".join(payload["data"] for event, payload in self.events if event == "audio"))
		return len(pcm)

	def test_a_sentence_split_as_the_driver_builds_it_speaks_as_one(self):
		whole = self._samples([(FIRST + " " + SECOND, True)])
		split = self._samples([(FIRST, True), (SECOND, False)])
		self.assertEqual(split, whole)

	def test_a_volume_command_mid_sentence_is_still_what_breaks_it(self):
		# The reason for the above.  If openevv stops ending the stretch at a
		# voice change, this fails, and the volume could go back into every
		# fragment -- though there is no need for it to.
		whole = self._samples([(FIRST + " " + SECOND, True)])
		split = self._samples([(FIRST, True), (SECOND, True)])
		self.assertGreater(split, whole + self.engine.sample_rate // 10)


if __name__ == "__main__":
	unittest.main()
