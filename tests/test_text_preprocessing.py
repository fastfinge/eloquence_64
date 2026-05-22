import unittest

from addon.synthDrivers._text_preprocessing import preprocess


class TextPreprocessingTests(unittest.TestCase):
	def test_chs_preprocessing_rewrites_known_traditional_characters(self):
		self.assertEqual(preprocess("選設檢", 393216), "选设检")

	def test_chs_preprocessing_preserves_unmapped_mixed_text(self):
		self.assertEqual(preprocess("請選設定檢查", 393216), "請选设定检查")

	def test_traditional_fallbacks_do_not_apply_to_other_asian_voices(self):
		for voice_id in (524288, 655360):
			with self.subTest(voice_id=voice_id):
				self.assertEqual(preprocess("選設檢", voice_id), "選設檢")


if __name__ == "__main__":
	unittest.main()
