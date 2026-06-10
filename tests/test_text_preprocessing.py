import unittest

from addon.synthDrivers._text_preprocessing import preprocess


class TextPreprocessingTests(unittest.TestCase):
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


if __name__ == "__main__":
	unittest.main()
