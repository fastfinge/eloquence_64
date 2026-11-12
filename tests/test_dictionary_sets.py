import os
import tempfile
import unittest

from addon.synthDrivers import _dictionary_sets as sets

COMMUNITY = "github.eigencrow.ibmtts-dictionaries"
# What EloquenceDictionaryManager ships for its bundled set, at commit 9da9c89.
PROVIDER_SET_INI = f"""[set]
id = {COMMUNITY}
name = IBMTTSDictionaries
source_url = https://github.com/eigencrow/IBMTTSDictionaries
source_version = v26.11
source_revision = 9c6c401a7aa12f28a41829dddb007fdc46b97730
attribution = Maintained by amirsol81, ultrasound1372 (x0), and thunderdrop, with contributions from many.
license = CC0-1.0
license_url = https://github.com/eigencrow/IBMTTSDictionaries/blob/9c6c401a7aa12f28a41829dddb007fdc46b97730/LICENSE.md
"""
CONTRACT_INI = "[contract]\nformat = eci-dictionary-sets\nversion = 1\n"


def _write(path, text):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8", newline="\n") as f:
		f.write(text)


class DictionarySetTestCase(unittest.TestCase):
	def setUp(self):
		temporary = tempfile.TemporaryDirectory()
		self.addCleanup(temporary.cleanup)
		self.root = temporary.name
		self.provider = os.path.join(self.root, "addons", "eloquenceDictionaryManager")
		self.downloads = os.path.join(self.root, "sources")

	def _provider_set(self, set_id=COMMUNITY, set_ini=PROVIDER_SET_INI, files=("enumain.dic", "enuroot.dic")):
		_write(os.path.join(self.provider, "dictionaries", "contract.ini"), CONTRACT_INI)
		directory = os.path.join(self.provider, "dictionaries", "sets", set_id)
		_write(os.path.join(directory, "set.ini"), set_ini)
		for name in files:
			_write(os.path.join(directory, name), "cat\tcat\n")
		return directory

	def _downloaded_set(self, set_id, files=("enuroot.dic",)):
		directory = os.path.join(self.downloads, set_id)
		os.makedirs(directory)
		sets.write_set_ini(directory, set_id, "Some/Repo", "https://github.com/some/repo", "a" * 40)
		for name in files:
			_write(os.path.join(directory, name), "cat\tcat\n")
		return directory

	def _discover(self):
		return sets.discover_sets([self.provider], self.downloads)


class ProviderTests(DictionarySetTestCase):
	def test_a_provider_set_is_found_with_its_metadata_and_files(self):
		directory = self._provider_set()
		found = self._discover()[COMMUNITY]
		self.assertEqual((found.name, found.version, found.installed), ("IBMTTSDictionaries", "v26.11", True))
		self.assertEqual(found.revision, "9c6c401a7aa12f28a41829dddb007fdc46b97730")
		self.assertEqual(
			found.files,
			{
				("enu", "main"): os.path.join(directory, "enumain.dic"),
				("enu", "root"): os.path.join(directory, "enuroot.dic"),
			},
		)
		self.assertEqual(found.languages, {"enu"})

	def test_files_the_contract_does_not_name_are_ignored(self):
		self._provider_set(files=("enumain.dic", "enuext.dic", "readme.txt", "main.dic", "enumain.dic.bak"))
		self.assertEqual(list(self._discover()[COMMUNITY].files), [("enu", "main")])

	def test_an_add_on_without_the_marker_provides_nothing(self):
		self._provider_set()
		os.remove(os.path.join(self.provider, "dictionaries", "contract.ini"))
		self.assertEqual(self._discover(), {})

	def test_an_unsupported_or_malformed_marker_is_ignored(self):
		for marker in (
			"[contract]\nformat = eci-dictionary-sets\nversion = 2\n",
			"[contract]\nformat = something-else\nversion = 1\n",
			"[contract]\nformat = eci-dictionary-sets\nversion = one\n",
			"[contract]\nformat = eci-dictionary-sets\n",
			"not an ini file",
		):
			with self.subTest(marker=marker):
				self._provider_set()
				_write(os.path.join(self.provider, "dictionaries", "contract.ini"), marker)
				with self.assertLogs(sets.log, "WARNING"):
					self.assertEqual(self._discover(), {})

	def test_a_provider_set_missing_a_required_field_is_ignored_alone(self):
		self._provider_set()
		other = "github.example.other"
		incomplete = PROVIDER_SET_INI.replace(COMMUNITY, other).replace("license = CC0-1.0\n", "")
		self._provider_set(set_id=other, set_ini=incomplete)
		with self.assertLogs(sets.log, "WARNING"):
			self.assertEqual(list(self._discover()), [COMMUNITY])

	def test_a_set_whose_id_is_not_its_folder_is_ignored(self):
		self._provider_set(set_id="github.example.renamed")
		with self.assertLogs(sets.log, "WARNING"):
			self.assertEqual(self._discover(), {})

	def test_a_missing_add_on_folder_is_not_an_error(self):
		self.assertEqual(sets.discover_sets([os.path.join(self.root, "nowhere")], self.downloads), {})


class DownloadTests(DictionarySetTestCase):
	def test_a_download_needs_only_the_short_metadata(self):
		set_id = "github.some.repo"
		self._downloaded_set(set_id)
		found = self._discover()[set_id]
		self.assertEqual((found.name, found.installed, found.version), ("Some/Repo", False, ""))
		self.assertEqual(found.revision, "a" * 40)
		self.assertEqual(found.source_url, "https://github.com/some/repo")

	def test_a_revision_that_is_not_a_commit_is_not_recorded(self):
		directory = os.path.join(self.downloads, "github.some.repo")
		os.makedirs(directory)
		sets.write_set_ini(directory, "github.some.repo", "Repo", "https://github.com/some/repo", "main")
		self.assertEqual(sets.read_set(directory, installed=False).revision, "")

	def test_the_short_metadata_is_not_enough_for_a_provider(self):
		directory = self._downloaded_set("github.some.repo")
		with self.assertLogs(sets.log, "WARNING"):
			self.assertIsNone(sets.read_set(directory, installed=True))

	def test_an_installed_copy_wins_over_a_download_of_the_same_set(self):
		self._provider_set()
		self._downloaded_set(COMMUNITY)
		self._downloaded_set("github.some.repo")
		found = self._discover()
		self.assertEqual(sorted(found), [COMMUNITY, "github.some.repo"])
		self.assertTrue(found[COMMUNITY].installed)
		self.assertFalse(found["github.some.repo"].installed)

	def test_no_downloads_folder_is_not_an_error(self):
		self.assertEqual(sets.discover_sets([], self.downloads), {})


if __name__ == "__main__":
	unittest.main()
