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
		backquote_tags=False,
		abbreviation_dict=False,
		phrase_prediction=False,
	):
		return _eloquence_text.BuildOptions(
			volume=volume,
			rate=rate,
			pause_mode=pause_mode,
			backquote_tags=backquote_tags,
			abbreviation_dict=abbreviation_dict,
			phrase_prediction=phrase_prediction,
		)

	def test_annotation_prefix_order(self):
		built = _eloquence_text.build(
			"Hello",
			65536,
			self._options(volume=77, abbreviation_dict=True, phrase_prediction=True),
		)

		self.assertEqual(built, b"`pp1 `da1 `vv77 Hello")

	def test_pause_policy_injection(self):
		expected = {
			0: b"`pp0 `da0 `vv80 Hello `p0, world `p0!",
			1: b"`pp0 `da0 `vv80 Hello, world!",
			2: b"`pp0 `da0 `vv80 Hello `p1, world `p1!",
		}
		for pause_mode, expected_bytes in expected.items():
			with self.subTest(pause_mode=pause_mode):
				self.assertEqual(
					_eloquence_text.build("Hello, world!", 65536, self._options(pause_mode=pause_mode)),
					expected_bytes,
				)

	def test_backquote_stripping_when_tags_disabled(self):
		self.assertEqual(
			_eloquence_text.build("literal ` tag", 65536, self._options(backquote_tags=False)),
			b"`pp0 `da0 `vv80 literal   tag",
		)
		self.assertEqual(
			_eloquence_text.build("literal ` tag", 65536, self._options(backquote_tags=True)),
			b"`pp0 `da0 `vv80 literal ` tag",
		)

	def test_time_fix(self):
		self.assertEqual(
			_eloquence_text.build("Meet at 3:45:12", 65536, self._options()),
			b"`pp0 `da0 `vv80 Meet at 3:45 12",
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
					(f"`pp0 `da0 `vv80 {text}").encode(encoding, errors="replace"),
				)

	def test_mbcs_engine_encoding_uses_best_fit(self):
		# CP_ACP maps Đ to the single-byte Ð best fit on this system; preserve the helper's bytes.
		self.assertEqual(
			_eloquence_text.build("Đ ł", 65536, self._options()),
			b"`pp0 `da0 `vv80 \xd0 l",
		)

	def test_invalid_voice_id_is_passed_raw_to_preprocessing_and_uses_mbcs(self):
		with mock.patch.object(
			_eloquence_text._text_preprocessing,
			"preprocess",
			wraps=_eloquence_text._text_preprocessing.preprocess,
		) as preprocess:
			built = _eloquence_text.build("Đ ł", "invalid", self._options())

		preprocess.assert_called_once_with("Đ ł", "invalid")
		self.assertEqual(built, b"`pp0 `da0 `vv80 \xd0 l")


if __name__ == "__main__":
	unittest.main()
