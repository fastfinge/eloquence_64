import hashlib
import unittest

from addon.synthDrivers import _script_conversion


_EXPECTED_OPENCC_SHA256 = {
	"TSCharacters.txt": "ad870b4feeb494cfa7b3b05242bd79af574b22f6b2bdeb89a1633e4b50ed0a3c",
	"TSPhrases.txt": "a0db90f746c9efd0c43baa4670abcf033cadf39a7f99ae78a8738a79c4c004fc",
	"LICENSE": "b534e465949558eec2597b04f5092b5e161236a68dfbfd04d547592ac3964308",
}


class ScriptConversionDataTests(unittest.TestCase):
	def test_opencc_t2s_tables_parse_and_have_sane_key_lengths(self):
		for table_name in _script_conversion._TABLE_NAMES:
			with self.subTest(table_name=table_name):
				mappings, max_key_length = _script_conversion._parse_opencc_table(
					_script_conversion._DATA_DIR / table_name
				)

				self.assertGreater(len(mappings), 0)
				self.assertGreaterEqual(max_key_length, 1)
				self.assertLessEqual(max_key_length, 16)

	def test_opencc_data_has_license_and_pinned_provenance(self):
		provenance = (_script_conversion._DATA_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
		self.assertIn("ver.1.3.1", provenance)

		for file_name, expected_hash in _EXPECTED_OPENCC_SHA256.items():
			with self.subTest(file_name=file_name):
				path = _script_conversion._DATA_DIR / file_name
				self.assertTrue(path.is_file())
				self.assertIn(expected_hash, provenance)
				self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)


if __name__ == "__main__":
	unittest.main()
