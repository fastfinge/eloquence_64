import tempfile
import unittest
import zipfile
from pathlib import Path

from site_scons.site_tools.NVDATool.addon import createAddonBundleFromPath


_REPO_ROOT = Path(__file__).resolve().parents[1]


class AddonPackagingTests(unittest.TestCase):
	def test_nvda_addon_bundle_includes_script_conversion_data(self):
		with tempfile.TemporaryDirectory() as root:
			addon_path = Path(root) / "Eloquence-test.nvda-addon"
			createAddonBundleFromPath(_REPO_ROOT / "addon", addon_path, excludePatterns=())

			with zipfile.ZipFile(addon_path) as addon:
				bundled_files = set(addon.namelist())

		for file_name in ("TSCharacters.txt", "TSPhrases.txt", "LICENSE", "PROVENANCE.md"):
			with self.subTest(file_name=file_name):
				self.assertIn(f"synthDrivers/t2s_data/{file_name}", bundled_files)

	def test_nvda_addon_bundle_includes_the_shared_engine_module(self):
		# The Synth Driver side imports this at run time; the Eloquence Host
		# Process gets its own frozen copy, so both must be accounted for.
		with tempfile.TemporaryDirectory() as root:
			addon_path = Path(root) / "Eloquence-test.nvda-addon"
			createAddonBundleFromPath(_REPO_ROOT / "addon", addon_path, excludePatterns=())
			with zipfile.ZipFile(addon_path) as addon:
				bundled_files = set(addon.namelist())
		self.assertIn("synthDrivers/_eci_engine.py", bundled_files)

	def test_nvda_addon_bundle_ships_no_bytecode(self):
		# Bytecode left by a dev run or the test suite is compiled by whichever
		# Python imported the module, not the one inside NVDA, which recompiles
		# from source anyway.  SConstruct excludes it; this is the assertion that
		# the exclusion still works.
		with tempfile.TemporaryDirectory() as root:
			addon_path = Path(root) / "Eloquence-test.nvda-addon"
			createAddonBundleFromPath(
				_REPO_ROOT / "addon",
				addon_path,
				excludePatterns=("*.pyc", "*.pyo", "__pycache__/*"),
			)
			with zipfile.ZipFile(addon_path) as addon:
				bundled_files = set(addon.namelist())
		stale = [name for name in bundled_files if name.endswith((".pyc", ".pyo"))]
		self.assertEqual(stale, [])


class OpenevvPackagingTests(unittest.TestCase):
	"""The openevv engine is fetched at build time, so it may legitimately be absent."""

	def setUp(self):
		self.openevv_dir = _REPO_ROOT / "addon" / "synthDrivers" / "openevv"
		if not (self.openevv_dir / "eci.dll").exists():
			self.skipTest("openevv not fetched; run `python fetch_eci.py`")

	def test_a_fetched_engine_is_bundled_with_its_upstream_notices(self):
		with tempfile.TemporaryDirectory() as root:
			addon_path = Path(root) / "Eloquence-test.nvda-addon"
			createAddonBundleFromPath(_REPO_ROOT / "addon", addon_path, excludePatterns=())
			with zipfile.ZipFile(addon_path) as addon:
				bundled_files = set(addon.namelist())
		# The DLL is useless to the driver without eci.ini beside it, and its
		# lang/enus data is IBM-derived, so the notices travel with it.
		for name in ("eci.dll", "eci.ini", "LICENSE", "NOTICE", "openevv-version.txt"):
			with self.subTest(name=name):
				self.assertIn(f"synthDrivers/openevv/{name}", bundled_files)

	def test_the_bundled_engine_is_64_bit(self):
		# A 32-bit DLL here would package cleanly and then fail to load inside
		# 64-bit NVDA with nothing but an OSError.
		from fetch_eci import _is_pe32_plus

		self.assertTrue(_is_pe32_plus(self.openevv_dir / "eci.dll"))


if __name__ == "__main__":
	unittest.main()
