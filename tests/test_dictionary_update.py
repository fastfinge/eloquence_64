import contextlib
import http.server
import io
import os
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from unittest import mock

from addon.synthDrivers import _dictionary_update as dictionary_update
from addon.synthDrivers._dictionary_update import FileStatus


def _build_archive(files):
	"""Build a GitHub-style source archive: every path sits under one root folder."""
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for path, data in files.items():
			archive.writestr(f"Source-master/{path}", data)
	return buffer.getvalue()


@contextlib.contextmanager
def _dictionary_source(handler):
	"""Serve one request handler on localhost and yield the Dictionary Source URL."""

	class Handler(http.server.BaseHTTPRequestHandler):
		def do_GET(self):
			handler(self)

		def log_message(self, *args):
			pass

	server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
	server.daemon_threads = True
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	thread.start()
	try:
		yield f"http://127.0.0.1:{server.server_address[1]}/owner/repo"
	finally:
		server.shutdown()
		server.server_close()


def _serve_archive(files):
	body = _build_archive(files)

	def handler(request):
		assert request.path == "/owner/repo/archive/master.zip"
		request.send_response(200)
		request.end_headers()
		request.wfile.write(body)

	return _dictionary_source(handler)


class DictionaryUpdateTestCase(unittest.TestCase):
	def setUp(self):
		root = tempfile.TemporaryDirectory()
		self.addCleanup(root.cleanup)
		self.dictionary_dir = os.path.join(root.name, "eloquence")
		os.makedirs(self.dictionary_dir)
		# Route the module's temporary files somewhere the tests can inspect.
		self.temp_dir = os.path.join(root.name, "temp")
		os.makedirs(self.temp_dir)
		patcher = mock.patch.object(tempfile, "tempdir", self.temp_dir)
		patcher.start()
		self.addCleanup(patcher.stop)

	def write_local(self, filename, data):
		with open(os.path.join(self.dictionary_dir, filename), "wb") as f:
			f.write(data)

	def read_local(self, filename):
		with open(os.path.join(self.dictionary_dir, filename), "rb") as f:
			return f.read()

	def update(self, files, **kwargs):
		with _serve_archive(files) as url:
			return dictionary_update.update_dictionaries(url, self.dictionary_dir, **kwargs)

	def assertNoTemporaryFiles(self):
		self.assertEqual(os.listdir(self.temp_dir), [])
		self.assertEqual([name for name in os.listdir(self.dictionary_dir) if name.endswith(".tmp")], [])


class MergeTests(DictionaryUpdateTestCase):
	def test_creates_missing_dictionary_with_tab_separated_ansi_entries(self):
		result = self.update({"enumain.dic": "NVDA\ten vee dee ay\nGUI  gooey\n\nnotranslation\n"})

		self.assertEqual(self.read_local("enumain.dic"), b"NVDA\ten vee dee ay\r\nGUI\tgooey\r\n")
		self.assertEqual([(o.filename, o.status, o.entries_added) for o in result.files], [
			("enumain.dic", FileStatus.CREATED, 2)
		])  # fmt: skip
		self.assertNoTemporaryFiles()

	def test_repeated_update_reports_unchanged_and_leaves_files_alone(self):
		files = {"enumain.dic": "NVDA\ten vee dee ay\n", "enuroot.dic": "roof\t`[rUf]\n"}
		self.update(files)
		before = {name: self.read_local(name) for name in files}

		result = self.update(files)

		self.assertEqual({o.status for o in result.files}, {FileStatus.UNCHANGED})
		self.assertEqual(result.entries_added, 0)
		self.assertEqual({name: self.read_local(name) for name in files}, before)

	def test_local_entries_win_and_keep_their_exact_bytes(self):
		# No trailing newline, a byte cp1252 leaves undefined, bare CR line endings, and keys the source
		# also defines, one of them with a cp1252-only character.
		local = b"NVDA\tmy own way\r\ncaf\xe9\tcoffee\ncan\x92t\tmine\rMac\told style\rodd\x81key\tkept"
		self.write_local("enumain.dic", local)

		result = self.update(
			{
				"enumain.dic": "NVDA\ten vee dee ay\ncan\u2019t\tsource\nMac\tsource\nGUI\tgooey\n".encode(
					"utf-8"
				)
			}
		)

		self.assertEqual(self.read_local("enumain.dic"), local + b"\r\nGUI\tgooey\r\n")
		self.assertEqual(result.files[0].status, FileStatus.UPDATED)
		self.assertEqual(result.files[0].entries_added, 1)

	def test_main_dictionary_keys_are_case_sensitive_and_root_keys_are_not(self):
		self.update(
			{
				"enumain.dic": "Airbnb\tAir bea en bea\nairbnb\tair bea en bea\n",
				"enuroot.dic": "Roof\t`[rUf]\nroof\t`[ruf]\n",
			}
		)

		self.assertEqual(
			self.read_local("enumain.dic"), b"Airbnb\tAir bea en bea\r\nairbnb\tair bea en bea\r\n"
		)
		self.assertEqual(self.read_local("enuroot.dic"), b"Roof\t`[rUf]\r\n")

	def test_duplicate_file_names_use_the_copy_closest_to_the_archive_root(self):
		result = self.update(
			{
				"IBMTTS 6.7/enumain.dic": "variant\tfrom the subfolder\n",
				"enumain.dic": "general\tfrom the root\n",
			}
		)

		self.assertEqual(self.read_local("enumain.dic"), b"general\tfrom the root\r\n")
		self.assertEqual(len(result.files), 1)

	def test_source_file_name_merges_into_local_file_with_different_case(self):
		self.write_local("enumain.dic", b"NVDA\tlocal\r\n")

		result = self.update({"ENUmain.dic": "GUI\tgooey\n"})

		self.assertEqual(os.listdir(self.dictionary_dir), ["enumain.dic"])
		self.assertEqual(self.read_local("enumain.dic"), b"NVDA\tlocal\r\nGUI\tgooey\r\n")
		self.assertEqual(result.files[0].filename, "enumain.dic")

	def test_mixed_source_encodings_end_up_as_ansi(self):
		self.update(
			{
				"utf8main.dic": "\ufeffcafé\tcoffee\nŁódź\tcity\nćma\tmoth\n日本\tJapan\n".encode("utf-8"),
				"ansimain.dic": "café\tcoffee\n".encode("cp1252"),
			}
		)

		# "é" exists in cp1252. "ć" loses its accent. "Ł" and the kanji have no ANSI form, so those entries are skipped.
		self.assertEqual(self.read_local("utf8main.dic"), b"caf\xe9\tcoffee\r\ncma\tmoth\r\n")
		self.assertEqual(self.read_local("ansimain.dic"), b"caf\xe9\tcoffee\r\n")


class FailureTests(DictionaryUpdateTestCase):
	def test_partial_write_failure_is_reported_and_leaves_no_temporary_files(self):
		self.write_local("enumain.dic", b"NVDA\tlocal\r\n")
		real_replace = os.replace

		def replace(src, dst):
			if os.path.basename(dst) == "enumain.dic":
				raise PermissionError("file is locked")
			real_replace(src, dst)

		with mock.patch.object(os, "replace", replace):
			result = self.update({"enumain.dic": "GUI\tgooey\n", "enuroot.dic": "roof\t`[rUf]\n"})

		self.assertEqual(
			[(o.filename, o.status) for o in result.failed], [("enumain.dic", FileStatus.FAILED)]
		)
		self.assertIn("file is locked", result.failed[0].error)
		self.assertEqual([o.filename for o in result.changed], ["enuroot.dic"])
		self.assertEqual(self.read_local("enumain.dic"), b"NVDA\tlocal\r\n")
		self.assertNoTemporaryFiles()

	def test_unrelated_tmp_file_next_to_a_dictionary_is_left_alone(self):
		self.write_local("enumain.dic.tmp", b"someone else's file")

		self.update({"enumain.dic": "GUI\tgooey\n"})

		self.assertEqual(self.read_local("enumain.dic.tmp"), b"someone else's file")
		self.assertEqual(sorted(os.listdir(self.dictionary_dir)), ["enumain.dic", "enumain.dic.tmp"])

	def test_binary_source_file_fails_instead_of_adding_garbage_entries(self):
		self.write_local("enumain.dic", b"NVDA\tlocal\r\n")

		result = self.update({"enumain.dic": "GUI\tgooey\n".encode("utf-16")})

		self.assertEqual([o.status for o in result.files], [FileStatus.FAILED])
		self.assertEqual(self.read_local("enumain.dic"), b"NVDA\tlocal\r\n")

	def test_archive_without_dictionaries_is_an_error_not_up_to_date(self):
		with self.assertRaises(dictionary_update.DictionaryUpdateError):
			self.update({"README.md": "no dictionaries here"})
		self.assertNoTemporaryFiles()

	def test_response_that_is_not_an_archive_is_an_error(self):
		def handler(request):
			request.send_response(200)
			request.end_headers()
			request.wfile.write(b"<html>rate limited</html>")

		with _dictionary_source(handler) as url:
			with self.assertRaises(dictionary_update.DictionaryUpdateError):
				dictionary_update.update_dictionaries(url, self.dictionary_dir)
		self.assertNoTemporaryFiles()

	def test_failed_download_raises_and_touches_nothing(self):
		self.write_local("enumain.dic", b"NVDA\tlocal\r\n")

		with _dictionary_source(lambda request: request.send_error(404)) as url:
			with self.assertRaises(urllib.error.HTTPError):
				dictionary_update.update_dictionaries(url, self.dictionary_dir)

		self.assertEqual(self.read_local("enumain.dic"), b"NVDA\tlocal\r\n")
		self.assertNoTemporaryFiles()

	def test_stalled_download_times_out(self):
		release = threading.Event()
		self.addCleanup(release.set)

		def handler(request):
			request.send_response(200)
			request.end_headers()
			request.wfile.write(b"PK")
			request.wfile.flush()
			release.wait(30)

		with mock.patch.object(dictionary_update, "NETWORK_TIMEOUT_SECONDS", 0.2):
			with _dictionary_source(handler) as url:
				with self.assertRaises(TimeoutError):
					dictionary_update.update_dictionaries(url, self.dictionary_dir)
				release.set()
		self.assertNoTemporaryFiles()


class CancellationTests(DictionaryUpdateTestCase):
	def test_cancel_during_download_touches_nothing(self):
		result = self.update({"enumain.dic": "GUI\tgooey\n"}, is_cancelled=lambda: True)

		self.assertTrue(result.cancelled)
		self.assertEqual(result.files, ())
		self.assertEqual(os.listdir(self.dictionary_dir), [])
		self.assertNoTemporaryFiles()

	def test_cancel_while_merging_reports_the_files_already_finished(self):
		stages = []

		def is_cancelled():
			# Cancel once the first dictionary has been written.
			return os.path.exists(os.path.join(self.dictionary_dir, "enumain.dic"))

		result = self.update(
			{"enumain.dic": "GUI\tgooey\n", "enuroot.dic": "roof\t`[rUf]\n"},
			is_cancelled=is_cancelled,
			report_progress=stages.append,
		)

		self.assertTrue(result.cancelled)
		self.assertEqual([o.filename for o in result.changed], ["enumain.dic"])
		self.assertEqual(os.listdir(self.dictionary_dir), ["enumain.dic"])
		self.assertEqual(
			stages, [dictionary_update.UpdateStage.DOWNLOADING, dictionary_update.UpdateStage.MERGING]
		)
		self.assertNoTemporaryFiles()


if __name__ == "__main__":
	unittest.main()
