"""Dictionary Sources are downloaded into Managed Dictionary Sets, never merged into anything."""

import contextlib
import http.server
import io
import os
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

from addon.synthDrivers import _dictionary_sets as sets
from addon.synthDrivers import _dictionary_update as dictionary_update
from addon.synthDrivers._dictionary_update import FileStatus, UpdateStage

COMMIT = "96bcd0009e2351cae0394ea561898be5780a071e"
CAT, DOG = "`[.1kAt]", "`[.1dOg]"


def _lines(*lines):
	return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def _archive(files, repository="Repo", comment=COMMIT):
	"""Build an archive the way GitHub does: one top folder, and the commit as the zip comment."""
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for path, data in files.items():
			archive.writestr(f"{repository}-{comment}/{path}", data)
		archive.comment = comment.encode("ascii")
	return buffer.getvalue()


@contextlib.contextmanager
def _github(archives):
	"""Serve {"owner/name": archive bytes or an HTTP status} the way github.com serves default branches."""
	requested = []

	class Handler(http.server.BaseHTTPRequestHandler):
		def do_GET(self):
			requested.append(self.path)
			owner_and_name = self.path.strip("/").removesuffix("/archive/HEAD.zip")
			body = archives.get(owner_and_name, 404)
			if isinstance(body, int) or not self.path.endswith("/archive/HEAD.zip"):
				self.send_response(body if isinstance(body, int) else 404)
				self.end_headers()
				return
			self.send_response(200)
			self.end_headers()
			self.wfile.write(body)

		def log_message(self, *args):
			pass

	server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
	server.daemon_threads = True
	threading.Thread(target=server.serve_forever, daemon=True).start()
	try:
		yield f"http://127.0.0.1:{server.server_address[1]}", requested
	finally:
		server.shutdown()
		server.server_close()


class SourceTestCase(unittest.TestCase):
	def setUp(self):
		root = tempfile.TemporaryDirectory()
		self.addCleanup(root.cleanup)
		self.sources = os.path.join(root.name, "sources")

	def update(self, files, repository="example/Repo", **kwargs):
		with _github({repository: _archive(files)}) as (base_url, requested):
			self.requested = requested
			return dictionary_update.update_source(repository, self.sources, base_url=base_url, **kwargs)

	def stored(self, set_id, filename):
		with open(os.path.join(self.sources, set_id, filename), "rb") as f:
			return f.read()


class RepositoryNameTests(unittest.TestCase):
	def test_a_repository_can_be_given_as_a_name_or_a_url(self):
		for text in (
			"Mohamed00/AltIBMTTSDictionaries",
			" Mohamed00/AltIBMTTSDictionaries ",
			"https://github.com/Mohamed00/AltIBMTTSDictionaries",
			"https://github.com/Mohamed00/AltIBMTTSDictionaries/",
			"http://github.com/Mohamed00/AltIBMTTSDictionaries.git",
		):
			with self.subTest(text=text):
				self.assertEqual(
					dictionary_update.parse_repository(text), ("Mohamed00", "AltIBMTTSDictionaries")
				)

	def test_anything_else_is_refused(self):
		for text in ("", "justaname", "https://example.com/owner/repo", "owner/repo/extra", "own er/repo"):
			with self.subTest(text=text):
				self.assertIsNone(dictionary_update.parse_repository(text))


class UpdateSourceTests(SourceTestCase):
	def test_the_default_branch_is_asked_for_whatever_it_is_called(self):
		self.update({"ENUmain.dic": _lines("WHO\twho")})
		self.assertEqual(self.requested, ["/example/Repo/archive/HEAD.zip"])

	def test_the_top_level_files_become_one_set_with_canonical_names(self):
		result = self.update({"ENUmain.dic": _lines("WHO\twho"), "DEURoot.dic": _lines("hund\thund")})
		self.assertEqual([stored.set_id for stored in result.sets], ["github.example.repo"])
		self.assertEqual(
			[(f.filename, f.status) for f in result.sets[0].files],
			[("deuroot.dic", FileStatus.CREATED), ("enumain.dic", FileStatus.CREATED)],
		)
		self.assertEqual(self.stored("github.example.repo", "enumain.dic"), _lines("WHO\twho"))
		found = sets.discover_sets([], self.sources)["github.example.repo"]
		self.assertEqual((found.name, found.revision, found.installed), ("Repo", COMMIT, False))
		self.assertTrue(found.source_url.endswith("/example/Repo"))

	def test_a_folder_of_dictionaries_is_its_own_set(self):
		result = self.update(
			{
				"enuroot.dic": _lines(f"cat\t{CAT}"),
				"IBMTTS 6.7/enuroot.dic": _lines(f"cat\t{DOG}"),
				"docs/readme.md": b"not a dictionary",
			},
			repository="Mohamed00/AltIBMTTSDictionaries",
		)
		top, folder = (
			"github.mohamed00.alt-ibmtts-dictionaries",
			"github.mohamed00.alt-ibmtts-dictionaries..ibmtts.206.2e7",
		)
		self.assertEqual([stored.set_id for stored in result.sets], [top, folder])
		self.assertEqual(result.sets[1].name, "AltIBMTTSDictionaries/IBMTTS 6.7")
		self.assertEqual(self.stored(top, "enuroot.dic"), _lines(f"cat\t{CAT}"))
		self.assertEqual(self.stored(folder, "enuroot.dic"), _lines(f"cat\t{DOG}"))
		self.assertEqual(sorted(os.listdir(self.sources)), [top, folder])

	def test_files_without_a_language_in_their_name_are_ignored(self):
		result = self.update(
			{"main.dic": _lines("WHO\twho"), "enumain.dic": _lines("WHO\twho"), "notes.dic": b"x"}
		)
		self.assertEqual([f.filename for f in result.sets[0].files], ["enumain.dic"])

	def test_a_second_update_replaces_the_set_so_upstream_changes_arrive(self):
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}", f"dog\t{DOG}"), "enumain.dic": _lines("WHO\twho")})
		result = self.update({"enuroot.dic": _lines(f"cat\t{DOG}"), "enuabbr.dic": _lines("Dr.\tdoctor")})
		self.assertEqual(
			{f.filename: f.status for f in result.sets[0].files},
			{
				"enuroot.dic": FileStatus.UPDATED,
				"enuabbr.dic": FileStatus.CREATED,
				"enumain.dic": FileStatus.REMOVED,
			},
		)
		# The entry upstream removed is gone, and so is the file upstream stopped publishing.
		self.assertEqual(self.stored("github.example.repo", "enuroot.dic"), _lines(f"cat\t{DOG}"))
		self.assertFalse(os.path.exists(os.path.join(self.sources, "github.example.repo", "enumain.dic")))

	def test_an_unchanged_file_is_reported_as_unchanged(self):
		files = {"enuroot.dic": _lines(f"cat\t{CAT}")}
		self.update(files)
		self.assertEqual(self.update(files).sets[0].files[0].status, FileStatus.UNCHANGED)

	def test_entries_that_may_not_take_effect_come_back_for_review(self):
		result = self.update({"enuabbr.dic": _lines("Dr.\tdoctor", "IDK\tI don't know")})
		(report,) = result.sets[0].files[0].reports
		self.assertEqual(
			(report.line, report.key, report.problems), (2, "IDK", ("abbreviationValueInvalid",))
		)
		self.assertEqual(
			self.stored("github.example.repo", "enuabbr.dic"), _lines("Dr.\tdoctor", "IDK\tI don't know")
		)

	def test_a_rejected_file_keeps_its_previous_copy(self):
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}"), "enumain.dic": _lines("WHO\twho")})
		with self.assertLogs(dictionary_update.log, "ERROR"):
			result = self.update(
				{
					"enuroot.dic": _lines(f"dog\t{DOG}", "no tab on this line"),
					"enumain.dic": _lines("WHO\thoo"),
				}
			)
		outcomes = {f.filename: f for f in result.sets[0].files}
		self.assertEqual(
			(
				outcomes["enuroot.dic"].status,
				outcomes["enuroot.dic"].error,
				outcomes["enuroot.dic"].error_line,
			),
			(FileStatus.FAILED, "noTab", 2),
		)
		self.assertEqual(self.stored("github.example.repo", "enuroot.dic"), _lines(f"cat\t{CAT}"))
		self.assertEqual(self.stored("github.example.repo", "enumain.dic"), _lines("WHO\thoo"))

	def test_a_rejected_file_with_no_previous_copy_is_simply_absent(self):
		with self.assertLogs(dictionary_update.log, "ERROR"):
			result = self.update({"enuroot.dic": b"binary\x00data"})
		self.assertEqual(result.sets[0].files[0].error, "notText")
		self.assertFalse(os.path.exists(os.path.join(self.sources, "github.example.repo")))

	def test_asian_files_are_stored_without_being_parsed(self):
		data = "単語\tたんご\r\n".encode("cp932")
		result = self.update({"JPNmain.dic": data})
		self.assertEqual(result.sets[0].files[0].status, FileStatus.CREATED)
		self.assertEqual(self.stored("github.example.repo", "jpnmain.dic"), data)

	def test_a_dataset_an_installed_add_on_provides_is_not_downloaded(self):
		result = self.update(
			{"enuroot.dic": _lines(f"cat\t{CAT}")},
			repository="eigencrow/IBMTTSDictionaries",
			installed_ids={"github.eigencrow.ibmtts-dictionaries"},
		)
		self.assertTrue(result.provided_by_add_on)
		self.assertEqual(self.requested, [])
		self.assertFalse(os.path.exists(self.sources))

	def test_progress_names_the_stage_and_the_repository(self):
		seen = []
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}")}, report_progress=lambda *step: seen.append(step))
		self.assertEqual(
			seen, [(UpdateStage.DOWNLOADING, "example/Repo"), (UpdateStage.CHECKING, "example/Repo")]
		)

	def test_cancelling_during_the_download_stores_nothing(self):
		result = self.update({"enuroot.dic": _lines(f"cat\t{CAT}")}, is_cancelled=lambda: True)
		self.assertTrue(result.cancelled)
		self.assertFalse(os.path.exists(self.sources))

	def test_no_temporary_folders_are_left_in_sources(self):
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}")})
		self.update({"enuroot.dic": _lines(f"cat\t{DOG}")})
		self.assertEqual(os.listdir(self.sources), ["github.example.repo"])


class FailureTests(SourceTestCase):
	def test_a_source_with_no_dictionaries_is_an_error(self):
		with self.assertRaises(dictionary_update.DictionaryUpdateError):
			self.update({"readme.md": b"hello", "main.dic": b"a\tb\r\n"})

	def test_something_that_is_not_an_archive_is_an_error(self):
		with (
			_github({"example/Repo": b"<html>not a zip</html>"}) as (base_url, _),
			self.assertRaises(dictionary_update.DictionaryUpdateError),
		):
			dictionary_update.update_source("example/Repo", self.sources, base_url=base_url)

	def test_a_repository_that_would_take_a_registered_id_is_refused(self):
		with self.assertRaises(dictionary_update.DictionaryUpdateError):
			dictionary_update.update_source(
				"eigencrow/ibmtts-dictionaries", self.sources, base_url="http://127.0.0.1:9"
			)

	def test_a_stalled_download_times_out_instead_of_hanging(self):
		release = threading.Event()
		self.addCleanup(release.set)

		class Stalling(http.server.BaseHTTPRequestHandler):
			def do_GET(self):
				self.send_response(200)
				self.end_headers()
				self.wfile.write(b"PK")
				self.wfile.flush()
				release.wait(30)

			def log_message(self, *args):
				pass

		server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stalling)
		server.daemon_threads = True
		threading.Thread(target=server.serve_forever, daemon=True).start()
		self.addCleanup(server.server_close)
		self.addCleanup(server.shutdown)
		base_url = f"http://127.0.0.1:{server.server_address[1]}"
		with (
			mock.patch.object(dictionary_update, "NETWORK_TIMEOUT_SECONDS", 0.2),
			self.assertRaises(TimeoutError),
		):
			dictionary_update.update_source("example/Repo", self.sources, base_url=base_url)
		release.set()
		self.assertFalse(os.path.exists(self.sources))

	def test_one_failing_source_does_not_stop_the_others(self):
		archives = {"example/Missing": 404, "example/Good": _archive({"enuroot.dic": _lines(f"cat\t{CAT}")})}
		with _github(archives) as (base_url, _), self.assertLogs(dictionary_update.log, "ERROR"):
			results = dictionary_update.update_sources(
				["example/Missing", "not a repository", "example/Good"], self.sources, base_url=base_url
			)
		self.assertEqual([bool(result.error) for result in results], [True, True, False])
		self.assertEqual(results[2].sets[0].set_id, "github.example.good")

	def test_a_failed_update_leaves_what_was_stored_before(self):
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}")})
		with (
			_github({"example/Repo": 500}) as (base_url, _),
			self.assertLogs(dictionary_update.log, "ERROR"),
		):
			(result,) = dictionary_update.update_sources(["example/Repo"], self.sources, base_url=base_url)
		self.assertTrue(result.error)
		self.assertEqual(self.stored("github.example.repo", "enuroot.dic"), _lines(f"cat\t{CAT}"))


class RemoveSourceTests(SourceTestCase):
	def test_removing_a_source_deletes_its_sets_and_only_its_sets(self):
		self.update(
			{"enuroot.dic": _lines(f"cat\t{CAT}"), "Old/enuroot.dic": _lines(f"cat\t{DOG}")},
			repository="example/Repo",
		)
		self.update({"enuroot.dic": _lines(f"cat\t{CAT}")}, repository="example/Repo2")
		removed = dictionary_update.remove_source("example/Repo", self.sources)
		self.assertEqual(removed, ("github.example.repo", "github.example.repo..old"))
		self.assertEqual(os.listdir(self.sources), ["github.example.repo2"])

	def test_removing_a_source_that_was_never_downloaded_is_harmless(self):
		self.assertEqual(dictionary_update.remove_source("example/Repo", self.sources), ())


if __name__ == "__main__":
	unittest.main()
