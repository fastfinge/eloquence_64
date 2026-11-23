"""Riding out GitHub's transient failures while fetching the engines.

raw.githubusercontent.com answered 503 for openevv's LICENSE in a CI run on
2026-11-23, and 200 for the same file a minute either side of it, which failed a
build whose only fault was the timing.  A fetch that fails once is retried;
one that is refused (401, 403, 404) is not, because repeating that answer only
wastes the build's time.
"""

import http.client
import io
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest import mock

import fetch_eci


def _http_error(code, url="https://example.test/LICENSE"):
	return urllib.error.HTTPError(url, code, "nope", Message(), None)


class _Response:
	"""Just enough of an HTTP response for shutil.copyfileobj."""

	def __init__(self, body):
		self.body = io.BytesIO(body)

	def read(self, size=-1):
		return self.body.read(size)

	def __enter__(self):
		return self

	def __exit__(self, *exc_info):
		return False


class RetryingTests(unittest.TestCase):
	def setUp(self):
		self.sleep = mock.patch.object(fetch_eci.time, "sleep").start()
		self.addCleanup(mock.patch.stopall)

	def test_a_transient_answer_is_retried_until_it_works(self):
		attempts = []

		def operation():
			attempts.append(1)
			if len(attempts) < 3:
				raise _http_error(503)
			return "fetched"

		self.assertEqual(fetch_eci._retrying(operation, "LICENSE"), "fetched")
		self.assertEqual(len(attempts), 3)

	def test_the_wait_doubles_between_attempts(self):
		def operation():
			raise _http_error(502)

		with self.assertRaises(urllib.error.HTTPError):
			fetch_eci._retrying(operation, "LICENSE")
		self.assertEqual(
			[call.args[0] for call in self.sleep.call_args_list],
			[2.0, 4.0, 8.0],
		)

	def test_a_refused_request_is_not_retried(self):
		for code in (401, 403, 404):
			with self.subTest(code=code):
				attempts = []

				def operation():
					attempts.append(1)
					raise _http_error(code)

				with self.assertRaises(urllib.error.HTTPError):
					fetch_eci._retrying(operation, "LICENSE")
				self.assertEqual(len(attempts), 1)

	def test_it_gives_up_after_the_last_attempt(self):
		attempts = []

		def operation():
			attempts.append(1)
			raise _http_error(503)

		with self.assertRaises(urllib.error.HTTPError):
			fetch_eci._retrying(operation, "LICENSE")
		self.assertEqual(len(attempts), fetch_eci.RETRY_ATTEMPTS)

	def test_a_dropped_connection_is_retried(self):
		attempts = []

		def operation():
			attempts.append(1)
			if len(attempts) == 1:
				raise urllib.error.URLError(ConnectionResetError("reset"))
			return "fetched"

		self.assertEqual(fetch_eci._retrying(operation, "LICENSE"), "fetched")
		self.assertEqual(len(attempts), 2)

	def test_a_truncated_body_is_retried(self):
		attempts = []

		def operation():
			attempts.append(1)
			if len(attempts) == 1:
				raise http.client.IncompleteRead(b"half a LICENSE")
			return "fetched"

		self.assertEqual(fetch_eci._retrying(operation, "LICENSE"), "fetched")
		self.assertEqual(len(attempts), 2)


class DownloadRetryTests(unittest.TestCase):
	"""The notices come through _download, so the retry has to cover it."""

	def setUp(self):
		mock.patch.object(fetch_eci.time, "sleep").start()
		self.addCleanup(mock.patch.stopall)

	def test_a_transient_answer_is_retried_and_the_file_written_once(self):
		opener = mock.Mock()
		opener.open.side_effect = [_http_error(503), _Response(b"MIT License")]

		with tempfile.TemporaryDirectory() as root:
			dest = root + "/LICENSE"
			with mock.patch.object(fetch_eci.urllib.request, "build_opener", return_value=opener):
				fetch_eci._download("https://example.test/LICENSE", dest)
			with open(dest, "rb") as f:
				self.assertEqual(f.read(), b"MIT License")
		self.assertEqual(opener.open.call_count, 2)


if __name__ == "__main__":
	unittest.main()
