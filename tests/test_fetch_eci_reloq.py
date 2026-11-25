"""Taking ReLoq C's engine out of the right add-on.

ReLoq C publishes no DLL of its own, so fetch_eci.py takes it out of ReLoq C's
NVDA add-on.  From 2.1.0 a release carries two of them,
reloq-c-synth-linux-x64-2.1.0.nvda-addon and reloq-c-synth-x64-2.1.0.nvda-addon,
and the Linux one comes first and has no eci.dll.  Taking the first add-on
failed the fetch with "the add-on layout changed", which would have failed the
next published release of this add-on too.
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import fetch_eci

LINUX = "reloq-c-synth-linux-x64-2.1.0.nvda-addon"
WINDOWS = "reloq-c-synth-x64-2.1.0.nvda-addon"

_WINDOWS_MEMBERS = {
	"manifest.ini": b"name = reloqC\n",
	"synthDrivers/reloqC/eci.dll": b"the engine",
	"doc/licenses/ReLoq-C-LICENSE.txt": b"GPL",
}
_LINUX_MEMBERS = {
	"manifest.ini": b"name = reloqC\n",
	"synthDrivers/reloqC/libeci.so": b"not for Windows",
	"doc/licenses/ReLoq-C-LICENSE.txt": b"GPL",
}


def _write_addon(path, members):
	with zipfile.ZipFile(path, "w") as zf:
		for name, data in members.items():
			zf.writestr(name, data)


class ReloqAddonChoiceTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
		self.dest = os.path.join(self.tmp, "reloq")
		mock.patch.object(fetch_eci, "RELOQ_DEST_DIR", self.dest).start()
		# The stand-in DLL is not a PE image, and that check is not what is tested.
		mock.patch.object(fetch_eci, "_is_pe32_plus", return_value=True).start()
		self.addCleanup(mock.patch.stopall)
		self.downloaded = []

	def _serve_release(self, assets):
		"""Make the newest release offer these {asset name: zip members}."""
		release = {
			"tag_name": "v2.1.0",
			"assets": [
				{"name": name, "browser_download_url": f"https://example.test/{name}"}
				for name in assets
			],
		}
		mock.patch.object(fetch_eci, "_github_json", return_value=release).start()

		def download(url, dest, **kwargs):
			name = url.rsplit("/", 1)[1]
			self.downloaded.append(name)
			_write_addon(dest, assets[name])

		mock.patch.object(fetch_eci, "_download", side_effect=download).start()

	def _installed(self, name):
		with open(os.path.join(self.dest, name), "rb") as f:
			return f.read()

	def test_the_windows_addon_is_taken_when_the_linux_one_is_listed_first(self):
		self._serve_release({LINUX: _LINUX_MEMBERS, WINDOWS: _WINDOWS_MEMBERS})

		fetch_eci.fetch_reloq_release()

		self.assertEqual(self._installed("eci.dll"), b"the engine")
		self.assertEqual(self._installed("ReLoq-C-LICENSE.txt"), b"GPL")
		self.assertEqual(fetch_eci.installed_reloq_version(), "v2.1.0")
		# The name is enough to go to the right one first, without the other download.
		self.assertEqual(self.downloaded, [WINDOWS])

	def test_the_contents_decide_when_the_names_do_not(self):
		# Neither name says which platform it is for; the one without the DLL sorts first.
		self._serve_release({"a.nvda-addon": _LINUX_MEMBERS, "b.nvda-addon": _WINDOWS_MEMBERS})

		fetch_eci.fetch_reloq_release()

		self.assertEqual(self._installed("eci.dll"), b"the engine")
		self.assertEqual(self.downloaded, ["a.nvda-addon", "b.nvda-addon"])

	def test_a_single_addon_is_still_taken(self):
		# Every release before 2.1.0.
		self._serve_release({"reloq-c-synth-x64-1.4.1.nvda-addon": _WINDOWS_MEMBERS})

		fetch_eci.fetch_reloq_release()

		self.assertEqual(self._installed("eci.dll"), b"the engine")

	def test_no_addon_with_the_engine_names_what_was_tried(self):
		self._serve_release({LINUX: _LINUX_MEMBERS})

		with self.assertRaises(SystemExit) as raised:
			fetch_eci.fetch_reloq_release()

		self.assertIn(LINUX, str(raised.exception))
		self.assertFalse(os.path.exists(os.path.join(self.dest, "eci.dll")))

	def test_a_release_with_no_addon_lists_its_assets(self):
		self._serve_release({"reloq-c-sapi5-2.1.0-setup.exe": {}})

		with self.assertRaises(SystemExit) as raised:
			fetch_eci.fetch_reloq_release()

		self.assertIn("reloq-c-sapi5-2.1.0-setup.exe", str(raised.exception))


class ReloqCiArtifactTests(unittest.TestCase):
	"""A CI artifact is a zip of add-ons, and may come to hold both as well."""

	def setUp(self):
		self.tmp = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
		self.dest = os.path.join(self.tmp, "reloq")
		mock.patch.object(fetch_eci, "RELOQ_DEST_DIR", self.dest).start()
		mock.patch.object(fetch_eci, "_is_pe32_plus", return_value=True).start()
		mock.patch.object(fetch_eci, "_github_token", return_value="token").start()
		self.addCleanup(mock.patch.stopall)

	def _serve_artifact(self, addons):
		run = {"id": 7, "head_branch": "codex/release-x", "head_sha": "0123456789abcdef"}
		artifact = {"archive_download_url": "https://example.test/artifact.zip"}
		mock.patch.object(
			fetch_eci, "_newest_reloq_ci_artifact", return_value=(run, artifact)
		).start()

		def download(url, dest, **kwargs):
			with zipfile.ZipFile(dest, "w") as outer:
				for name, members in addons.items():
					inner = os.path.join(self.tmp, "inner.zip")
					_write_addon(inner, members)
					outer.write(inner, name)

		mock.patch.object(fetch_eci, "_download", side_effect=download).start()

	def test_the_addon_holding_the_engine_is_taken(self):
		self._serve_artifact({LINUX: _LINUX_MEMBERS, WINDOWS: _WINDOWS_MEMBERS})

		fetch_eci.fetch_reloq_ci()

		with open(os.path.join(self.dest, "eci.dll"), "rb") as f:
			self.assertEqual(f.read(), b"the engine")
		self.assertEqual(
			fetch_eci.installed_reloq_version(),
			"codex/release-x@01234567 (build run 7)",
		)

	def test_an_artifact_without_the_engine_is_an_error(self):
		self._serve_artifact({LINUX: _LINUX_MEMBERS})

		with self.assertRaises(SystemExit) as raised:
			fetch_eci.fetch_reloq_ci()

		self.assertIn("build run 7", str(raised.exception))


if __name__ == "__main__":
	unittest.main()
