import unittest

from addon.synthDrivers._text_preprocessing import preprocess


class TextPreprocessingTests(unittest.TestCase):
	def test_phrase_final_em_dash_is_rewritten_for_legacy_engine(self):
		self.assertEqual(preprocess("You don't have to—  ", 65536), "You don't have to,  ")

	def test_chs_preprocessing_rewrites_known_traditional_characters(self):
		self.assertEqual(preprocess("選設檢", 393216), "选设检")

	def test_chs_script_conversion_uses_phrase_context(self):
		self.assertEqual(preprocess("乾隆乾杯穿著藉口", 393216), "乾隆干杯穿著借口")

	def test_chs_preprocessing_converts_traditional_mixed_text(self):
		self.assertEqual(preprocess("請選設定檢查", 393216), "请选设定检查")

	def test_chs_script_conversion_preserves_simplified_latin_and_written_cantonese(self):
		self.assertEqual(preprocess("检查 NVDA 嘅哋咗", 393216), "检查 NVDA 嘅哋咗")

	def test_script_conversion_does_not_apply_to_other_asian_voices(self):
		for voice_id in (524288, 655360):
			with self.subTest(voice_id=voice_id):
				self.assertEqual(preprocess("選設檢", voice_id), "選設檢")

	def test_capital_sharp_s_falls_back_to_lowercase(self):
		# ẞ (U+1E9E) should become ß (U+00DF), not "?"
		self.assertEqual(preprocess("STRASSE \u1e9e", 65536), "STRASSE \u00df")

	def test_month_prefix_words_are_split_from_preceding_numbers(self):
		# ECI date parser bug: "03 Marble" would read as "March thirdble".
		# The extra space stops the engine fusing the number and the word.
		self.assertEqual(preprocess("03 Marble", 65536), "03  Marble")

	def test_genuine_dates_keep_their_single_space(self):
		# A real date inside one speech chunk should still reach the engine
		# untouched so it reads as a date.
		self.assertEqual(
			preprocess("I arrived on 14 March 2020", 65536),
			"I arrived on 14 March 2020",
		)

	def test_nvda_chunk_separator_before_month_is_preserved(self):
		# NVDA joins separate speech chunks (e.g. the "row 14" announcement
		# and a "March" table cell) with CHUNK_SEPARATOR, two spaces.  The
		# double space stops the ECI date parser fusing the chunks; removing
		# it makes "row 14  March" read as "row March 14".  Preprocessing
		# must never collapse it.
		for month in (
			"January",
			"February",
			"March",
			"April",
			"May",
			"June",
			"July",
			"August",
			"September",
			"October",
			"November",
			"December",
		):
			with self.subTest(month=month):
				self.assertEqual(
					preprocess(f"row 14  {month}  ", 65536),
					f"row 14  {month}  ",
				)


if __name__ == "__main__":
	unittest.main()
