import os
import tempfile
import unittest

import host_eloquence32 as host


class FakeDll:
	def __init__(self):
		self.calls = []
		self._next_dictionary = 1

	def eciNewDict(self, handle):
		dictionary_handle = f"dict-{self._next_dictionary}"
		self._next_dictionary += 1
		self.calls.append(("newDict", handle, dictionary_handle))
		return dictionary_handle

	def eciSetDict(self, handle, dictionary_handle):
		self.calls.append(("setDict", handle, dictionary_handle))

	def eciLoadDict(self, handle, dictionary_handle, index, path):
		self.calls.append(("loadDict", handle, dictionary_handle, index, os.path.basename(path.decode("mbcs"))))

	def eciSetParam(self, handle, param_id, value):
		self.calls.append(("setParam", handle, param_id, value))

	def eciInsertIndex(self, handle, index):
		self.calls.append(("insertIndex", handle, index))

	def eciGetVoiceParam(self, handle, voice, param_id):
		return param_id


class RecordingConnection:
	def __init__(self):
		self.messages = []

	def send(self, message):
		self.messages.append(message)


def make_runtime(data_directory, language_code="enu", conn=None):
	runtime = host.EloquenceRuntime(
		conn=conn,
		config=host.HostConfig(
			eci_path="",
			data_directory=data_directory,
			language_code=language_code,
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
		),
	)
	runtime._dll = FakeDll()
	runtime._handle = "eci"
	return runtime


class SpeechIndexTests(unittest.TestCase):
	def test_final_index_reports_latest_engine_skipped_index(self):
		connection = RecordingConnection()
		runtime = make_runtime("", conn=connection)
		runtime.insert_index(41)
		runtime.insert_index(42)
		runtime._speaking = True

		runtime._on_callback(None, 2, host.FINAL_INDEX, None)

		self.assertEqual(
			connection.messages,
			[
				{
					"type": "event",
					"event": "audio",
					"payload": {"data": b"", "index": 42, "final": False},
				},
				{
					"type": "event",
					"event": "audio",
					"payload": {"data": b"", "index": None, "final": True},
				},
			],
		)

	def test_reached_index_clears_it_and_any_skipped_predecessors(self):
		connection = RecordingConnection()
		runtime = make_runtime("", conn=connection)
		runtime.insert_index(41)
		runtime.insert_index(42)
		runtime._speaking = True

		runtime._on_callback(None, 2, 42, None)
		runtime._on_callback(None, 2, host.FINAL_INDEX, None)

		self.assertEqual(
			connection.messages,
			[
				{
					"type": "event",
					"event": "audio",
					"payload": {"data": b"", "index": 42, "final": False},
				},
				{
					"type": "event",
					"event": "audio",
					"payload": {"data": b"", "index": None, "final": True},
				},
			],
		)


class DictionaryLoadingTests(unittest.TestCase):
	def test_dictionary_candidates_do_not_use_generic_fallback_for_non_english(self):
		self.assertEqual(
			host.get_dictionary_candidates("esp"),
			(
				("espmain.dic",),
				("esproot.dic",),
				("espabbr.dic",),
			),
		)

	def test_dictionary_candidates_allow_generic_fallback_for_english(self):
		self.assertEqual(
			host.get_dictionary_candidates("eng"),
			(
				("engmain.dic", "enumain.dic", "main.dic"),
				("engroot.dic", "enuroot.dic", "root.dic"),
				("engabbr.dic", "enuabbr.dic", "abbr.dic"),
			),
		)

	def test_dictionary_candidates_allow_regional_language_fallbacks(self):
		self.assertEqual(
			host.get_dictionary_candidates("esm"),
			(
				("esmmain.dic", "espmain.dic"),
				("esmroot.dic", "esproot.dic"),
				("esmabbr.dic", "espabbr.dic"),
			),
		)

	def test_dictionary_candidates_allow_english_fallback_for_chinese(self):
		self.assertEqual(
			host.get_dictionary_candidates("chs"),
			(
				("chsmain.dic", "enumain.dic", "main.dic"),
				("chsroot.dic", "enuroot.dic", "root.dic"),
				("chsabbr.dic", "enuabbr.dic", "abbr.dic"),
			),
		)

	def test_loads_current_language_dictionaries_not_hard_coded_enu(self):
		with tempfile.TemporaryDirectory() as data_directory:
			for name in ("enumain.dic", "espmain.dic", "esproot.dic"):
				open(os.path.join(data_directory, name), "w").close()

			runtime = make_runtime(data_directory, "esp")
			runtime._load_dictionaries()

		self.assertIn(("loadDict", "eci", "dict-1", 0, "espmain.dic"), runtime._dll.calls)
		self.assertIn(("loadDict", "eci", "dict-1", 1, "esproot.dic"), runtime._dll.calls)
		self.assertNotIn(("loadDict", "eci", "dict-1", 0, "enumain.dic"), runtime._dll.calls)
		self.assertIn(("setDict", "eci", "dict-1"), runtime._dll.calls)

	def test_voice_change_loads_dictionary_for_new_language(self):
		with tempfile.TemporaryDirectory() as data_directory:
			for name in ("enumain.dic", "espmain.dic"):
				open(os.path.join(data_directory, name), "w").close()

			runtime = make_runtime(data_directory, "enu")
			runtime._load_dictionaries()
			runtime.set_param(9, host.LANGS["esp"])

		self.assertIn(("loadDict", "eci", "dict-1", 0, "enumain.dic"), runtime._dll.calls)
		self.assertIn(("loadDict", "eci", "dict-2", 0, "espmain.dic"), runtime._dll.calls)
		self.assertLess(
			runtime._dll.calls.index(("setParam", "eci", 9, host.LANGS["esp"])),
			runtime._dll.calls.index(("newDict", "eci", "dict-2")),
		)


if __name__ == "__main__":
	unittest.main()
