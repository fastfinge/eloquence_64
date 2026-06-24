import os
import tempfile
import unittest

import host_eloquence32 as host
from addon.synthDrivers import _secure_screen


class HostEciIniTests(unittest.TestCase):
	def test_rewrite_eci_ini_paths_updates_existing_and_dummy_paths(self):
		original_get_short_path = host.get_short_path
		host.get_short_path = (
			lambda path: r"C:\Program Files\NVDA\systemConfig\addons\Eloquence\synthDrivers\eloquence"
		)
		try:
			content = (
				"[7.0]\n"
				r"Path=C:\Users\andrew\AppData\Roaming\nvda\addons\ELOQUE~1\SYNTHD~1\ELOQUE~1\ptb.syn"
				"\n"
				r"Path_Rom=C:\dummy\jpnrom.dll"
				"\n"
				r"Other=C:\dummy\untouched.dat"
				"\n"
			)

			rewritten = host._rewrite_eci_ini_paths(content, r"C:\ignored")
		finally:
			host.get_short_path = original_get_short_path

		self.assertIn(
			r"Path=C:\Program Files\NVDA\systemConfig\addons\Eloquence\synthDrivers\eloquence\ptb.syn",
			rewritten,
		)
		self.assertIn(
			r"Path_Rom=C:\Program Files\NVDA\systemConfig\addons\Eloquence\synthDrivers\eloquence\jpnrom.dll",
			rewritten,
		)
		self.assertIn(
			r"Other=C:\Program Files\NVDA\systemConfig\addons\Eloquence\synthDrivers\eloquence\untouched.dat",
			rewritten,
		)
		self.assertNotIn(r"C:\Users\andrew\AppData", rewritten)
		self.assertNotIn(r"C:\dummy", rewritten)

	def test_rewrite_eci_ini_paths_rejects_empty_ini(self):
		with self.assertRaisesRegex(RuntimeError, "ECI.INI is empty"):
			host._rewrite_eci_ini_paths("", r"C:\Program Files\NVDA\systemConfig")


class SecureScreenCopyPlanTests(unittest.TestCase):
	def test_read_manifest_version(self):
		with tempfile.TemporaryDirectory() as root:
			with open(os.path.join(root, "manifest.ini"), "w", encoding="utf-8") as manifest:
				manifest.write("name = Eloquence\nversion = v18\n")

			self.assertEqual(_secure_screen.read_manifest_version(root), "v18")

	def test_build_copy_plan_includes_host_and_eci_ini(self):
		with tempfile.TemporaryDirectory() as root:
			source_synth = os.path.join(root, "source", "synthDrivers")
			source_eci = os.path.join(source_synth, "eloquence")
			target_addon = os.path.join(root, "systemConfig", "addons", "Eloquence")
			os.makedirs(source_eci)
			os.makedirs(target_addon)
			with open(os.path.join(source_synth, "eloquence_host32.exe"), "w", encoding="utf-8") as host_exe:
				host_exe.write("host")
			with open(os.path.join(source_eci, "ECI.INI"), "w", encoding="utf-8") as ini:
				ini.write("[7.0]\n")

			plan = _secure_screen.build_copy_plan(source_synth, target_addon)

		self.assertTrue(plan.source_host.endswith(os.path.join("synthDrivers", "eloquence_host32.exe")))
		self.assertTrue(plan.source_ini.endswith(os.path.join("synthDrivers", "eloquence", "ECI.INI")))
		self.assertTrue(plan.dest_host.endswith(os.path.join("synthDrivers", "eloquence_host32.exe")))
		self.assertTrue(plan.dest_ini.endswith(os.path.join("synthDrivers", "eloquence", "ECI.INI")))
		self.assertIn("eloquence_host32.exe", plan.command)
		self.assertIn("ECI.INI", plan.command)


if __name__ == "__main__":
	unittest.main()
