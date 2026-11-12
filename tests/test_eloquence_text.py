import unittest
from unittest import mock

from addon.synthDrivers import _eloquence_text


class EloquenceTextBuilderTests(unittest.TestCase):
	def _options(
		self,
		*,
		volume=80,
		rate=50,
		pause_mode=1,
		backquotes=_eloquence_text.Backquotes.SPACES,
		abbreviation_dict=False,
		phrase_prediction=False,
	):
		return _eloquence_text.BuildOptions(
			volume=volume,
			rate=rate,
			pause_mode=pause_mode,
			backquotes=backquotes,
			abbreviation_dict=abbreviation_dict,
			phrase_prediction=phrase_prediction,
		)

	def test_annotation_prefix_order(self):
		built = _eloquence_text.build(
			"Hello",
			65536,
			self._options(volume=77, abbreviation_dict=True, phrase_prediction=True),
		)

		self.assertEqual(built, b"`pp1 `da1 `ts0 `vv77 Hello")

	def test_pause_policy_injection(self):
		expected = {
			0: b"`pp0 `da0 `ts0 `vv80 Hello `p0, world `p0!",
			1: b"`pp0 `da0 `ts0 `vv80 Hello, world!",
			2: b"`pp0 `da0 `ts0 `vv80 Hello `p1, world `p1!",
		}
		for pause_mode, expected_bytes in expected.items():
			with self.subTest(pause_mode=pause_mode):
				self.assertEqual(
					_eloquence_text.build("Hello, world!", 65536, self._options(pause_mode=pause_mode)),
					expected_bytes,
				)

	def test_backquote_stripping_when_tags_disabled(self):
		self.assertEqual(
			_eloquence_text.build("literal ` tag", 65536, self._options(backquotes=_eloquence_text.Backquotes.SPACES)),
			b"`pp0 `da0 `ts0 `vv80 literal   tag",
		)
		self.assertEqual(
			_eloquence_text.build("literal ` tag", 65536, self._options(backquotes=_eloquence_text.Backquotes.COMMANDS)),
			b"`pp0 `da0 `ts0 `vv80 literal ` tag",
		)

	def test_dictionary_codes_mode_keeps_only_what_a_dictionary_value_may_carry(self):
		# Eloquence Dictionary Manager's validation rule, one backquote at a time.
		# Kept codes reach the engine; every other backquote becomes a space.
		cases = {
			# A phonetic string, at the start or after whitespace.
			"`[.1Ar.0min]": "`[.1Ar.0min]",
			"re `[.1Ar.0min]": "re `[.1Ar.0min]",
			"re`[.1Ar.0min]": "re [.1Ar.0min]",
			# An emphasis code needs whitespace on both sides.
			"`1 can": "`1 can",
			"`00 marker": "`00 marker",
			"`1can": " 1can",
			"can `4": "can  4",
			# Spelling on may be glued to text; spelling off may not.
			"`ts1 abc `ts0": "`ts1 abc `ts0",
			"`ts1abc `ts0": "`ts1abc `ts0",
			"x`ts1 abc `ts0": "x`ts1 abc `ts0",
			"`ts10 abc `ts0": " ts10 abc `ts0",
			# A value that leaves spelling on has it turned off at its end.
			"`ts1 abc": "`ts1 abc `ts0",
			"`ts1abc`ts0": "`ts1abc ts0 `ts0",
			"`ts2 abc`ts0": "`ts2 abc ts0 `ts0",
			# Everything else, including inside a phonetic string.
			"`vv10 marker": " vv10 marker",
			"`p3000 marker": " p3000 marker",
			"`l4.0 marker": " l4.0 marker",
			"`[.1A`vv0r]": "`[.1A vv0r]",
			"``[.1Ar]": "  [.1Ar]",
			"plain words": "plain words",
		}
		options = self._options(backquotes=_eloquence_text.Backquotes.DICTIONARY_CODES)
		for value, sent in cases.items():
			with self.subTest(value=value):
				self.assertEqual(
					_eloquence_text.build(value, 65536, options),
					b"`pp0 `da0 `ts0 `vv80 " + sent.encode("cp1252"),
				)

	def test_a_continuation_fragment_carries_no_volume_command(self):
		# openevv ends the phrase at any voice change, even one to the volume it
		# already has; see Driver.speak() and tests/test_openevv_phrasing.py.
		self.assertEqual(
			_eloquence_text.build("Hello", 65536, self._options(), opens_utterance=False),
			b"`pp0 `da0 Hello",
		)

	def test_a_continuation_opening_on_a_mark_keeps_its_pause_command(self):
		# The volume prefix is what puts a space before that mark, and the Pause
		# Policy needs one there; it has to see the fragment as it always did.
		opening = _eloquence_text.build(", and then", 65536, self._options(pause_mode=0))
		continuation = _eloquence_text.build(
			", and then", 65536, self._options(pause_mode=0), opens_utterance=False
		)
		self.assertEqual(opening, b"`pp0 `da0 `ts0 `vv80  `p0, and then")
		self.assertEqual(continuation, b"`pp0 `da0  `p0, and then")

	def test_time_fix(self):
		self.assertEqual(
			_eloquence_text.build("Meet at 3:45:12", 65536, self._options()),
			b"`pp0 `da0 `ts0 `vv80 Meet at 3:45 12",
		)

	def test_break_fragment_table_interpolation_and_clamping(self):
		expected = {
			1: b"`p50.",
			10: b"`p50.",
			20: b"`p65.",
			43: b"`p100.",
			55: b"`p135.",
			60: b"`p150.",
			75: b"`p200.",
			85: b"`p250.",
			100: b"`p250.",
			150: b"`p250.",
		}
		for rate, expected_bytes in expected.items():
			with self.subTest(rate=rate):
				self.assertEqual(
					_eloquence_text.break_fragment(50, self._options(rate=rate)),
					expected_bytes,
				)

	def test_trailing_pause_policy(self):
		for pause_mode, expected in ((0, b"`p0 "), (1, b"`p1 "), (2, b"`p1 ")):
			with self.subTest(pause_mode=pause_mode):
				self.assertEqual(
					_eloquence_text.trailing_pause(b"letter", self._options(pause_mode=pause_mode)),
					expected,
				)

	def test_trailing_pause_ignores_ascii_trailing_space(self):
		self.assertIsNone(_eloquence_text.trailing_pause(b"done!   ", self._options()))

	def test_trailing_pause_recognizes_every_punctuation_tail(self):
		for punctuation in b",.?:;)(?!":
			with self.subTest(punctuation=punctuation):
				self.assertIsNone(
					_eloquence_text.trailing_pause(b"tail" + bytes((punctuation,)), self._options())
				)

	def test_trailing_pause_accepts_multibyte_tails(self):
		for encoding, text in (("gb18030", "中文"), ("cp932", "日本語"), ("cp949", "한국어")):
			with self.subTest(encoding=encoding):
				self.assertEqual(
					_eloquence_text.trailing_pause(text.encode(encoding), self._options()),
					b"`p1 ",
				)

	def test_engine_encoding_by_voice_id(self):
		cases = (
			(393216, "简体中文", "gb18030"),
			(524288, "日本語", "cp932"),
			(655360, "한국어", "cp949"),
		)
		for voice_id, text, encoding in cases:
			with self.subTest(voice_id=voice_id):
				self.assertEqual(
					_eloquence_text.build(text, voice_id, self._options()),
					(f"`pp0 `da0 `ts0 `vv80 {text}").encode(encoding, errors="replace"),
				)

	def test_mbcs_engine_encoding_uses_best_fit(self):
		# CP_ACP maps Đ to the single-byte Ð best fit on this system; preserve the helper's bytes.
		self.assertEqual(
			_eloquence_text.build("Đ ł", 65536, self._options()),
			b"`pp0 `da0 `ts0 `vv80 \xd0 l",
		)

	def test_invalid_voice_id_is_passed_raw_to_preprocessing_and_uses_mbcs(self):
		with mock.patch.object(
			_eloquence_text._text_preprocessing,
			"preprocess",
			wraps=_eloquence_text._text_preprocessing.preprocess,
		) as preprocess:
			built = _eloquence_text.build("Đ ł", "invalid", self._options())

		preprocess.assert_called_once_with("Đ ł", "invalid")
		self.assertEqual(built, b"`pp0 `da0 `ts0 `vv80 \xd0 l")


if __name__ == "__main__":
	unittest.main()
