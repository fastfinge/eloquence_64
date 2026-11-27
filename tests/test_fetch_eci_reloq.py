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
import urllib.error
import zipfile
from email.message import Message
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
		mock.patch.object(fetch_eci, "_newest_reloq_release", return_value={"tag_name": "v9"}).start()
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
			"main@01234567 (build run 7, built on codex/release-x)",
		)

	def test_an_artifact_without_the_engine_is_an_error(self):
		self._serve_artifact({LINUX: _LINUX_MEMBERS})

		with self.assertRaises(SystemExit) as raised:
			fetch_eci.fetch_reloq_ci()

		self.assertIn("build run 7", str(raised.exception))


class ReloqMainOnlyTests(unittest.TestCase):
	"""Only unreleased work that is on ReLoq C's main branch is taken from CI.

	ReLoq C builds its add-on on codex/release* branches and never on main, and
	that prefix holds listening experiments too.  Taking the newest build installed
	codex/release-camel-dictionary-dev4-20261125 on 2026-11-25, a prototype seven
	commits off main (fastfinge/eloquence_64#187).
	"""

	API = "https://api.github.com/repos/hozosch/Reloq-C"

	def setUp(self):
		self.compare = {}
		self.runs = []
		self.artifacts = {}
		self.asked = []
		mock.patch.object(fetch_eci, "_github_json", side_effect=self._api).start()
		self.addCleanup(mock.patch.stopall)

	def _api(self, url):
		self.asked.append(url)
		path = url[len(self.API) :]
		if path.startswith("/compare/"):
			return {"status": self.compare[path[len("/compare/") :]]}
		if path.startswith("/actions/workflows/"):
			return {"workflow_runs": list(self.runs)}
		if path.startswith("/actions/runs/"):
			run_id = int(path.split("/")[3])
			return {"artifacts": self.artifacts.get(run_id, [{"name": "nvda-addon"}])}
		raise AssertionError(f"unexpected request: {url}")

	def _run(self, number, sha, in_main, past_release=True):
		"""A successful packaging run of this commit."""
		self.runs.append({"id": number, "run_number": number, "head_sha": sha})
		self.compare[f"main...{sha}"] = "behind" if in_main else "ahead"
		self.compare[f"v2.1.1...{sha}"] = "ahead" if past_release else "behind"

	def _found(self):
		run, artifact = fetch_eci._newest_reloq_ci_artifact("v2.1.1")
		return None if run is None else run["id"]

	def test_main_at_the_release_has_nothing_newer_to_take(self):
		# The usual state: upstream tags main as it moves it.
		self.compare["v2.1.1...main"] = "identical"
		self._run(3, "experiment", in_main=False)

		self.assertIsNone(self._found())
		# And it is settled without looking at a single build.
		self.assertEqual(len(self.asked), 1)

	def test_an_unmerged_experiment_is_passed_over_for_an_older_build_in_main(self):
		self.compare["v2.1.1...main"] = "ahead"
		self._run(1, "merged", in_main=True)
		self._run(2, "experiment", in_main=False)

		self.assertEqual(self._found(), 1)

	def test_a_branch_that_diverged_from_main_does_not_count(self):
		self.compare["v2.1.1...main"] = "ahead"
		self._run(1, "side", in_main=False)
		self.compare["main...side"] = "diverged"

		self.assertIsNone(self._found())

	def test_a_build_the_release_already_holds_is_not_taken(self):
		# It is in main, but so is the release, and the release is newer.
		self.compare["v2.1.1...main"] = "ahead"
		self._run(1, "old", in_main=True, past_release=False)

		self.assertIsNone(self._found())

	def test_the_build_of_mains_own_head_counts(self):
		self.compare["v2.1.1...main"] = "ahead"
		self._run(1, "head", in_main=True)
		self.compare["main...head"] = "identical"

		self.assertEqual(self._found(), 1)

	def test_an_expired_artifact_is_passed_over(self):
		self.compare["v2.1.1...main"] = "ahead"
		self._run(1, "older", in_main=True)
		self._run(2, "newer", in_main=True)
		self.artifacts[2] = [{"name": "nvda-addon", "expired": True}]

		self.assertEqual(self._found(), 1)

	def test_a_release_tag_github_cannot_compare_means_the_release(self):
		error = urllib.error.HTTPError("https://example.test", 404, "nope", Message(), None)
		fetch_eci._github_json.side_effect = error

		self.assertEqual(fetch_eci._newest_reloq_ci_artifact("unknown"), (None, None))


class ReloqDefaultFetchTests(unittest.TestCase):
	def setUp(self):
		mock.patch.object(fetch_eci, "_github_token", return_value="token").start()
		self.release = {"tag_name": "v2.1.1", "assets": []}
		mock.patch.object(fetch_eci, "_newest_reloq_release", return_value=self.release).start()
		self.install_release = mock.patch.object(fetch_eci, "fetch_reloq_release").start()
		self.addCleanup(mock.patch.stopall)

	def test_with_nothing_newer_on_main_the_release_is_installed(self):
		mock.patch.object(fetch_eci, "_newest_reloq_ci_artifact", return_value=(None, None)).start()

		fetch_eci.fetch_reloq_ci()

		# The release already looked up, not a second request for it.
		self.install_release.assert_called_once_with(self.release)

	def test_without_a_token_the_release_is_installed(self):
		fetch_eci._github_token.return_value = None

		fetch_eci.fetch_reloq_ci()

		self.install_release.assert_called_once_with()


if __name__ == "__main__":
	unittest.main()
