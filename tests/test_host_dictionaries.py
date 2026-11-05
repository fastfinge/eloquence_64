import os
import tempfile
import unittest

import host_eloquence32 as host
from addon.synthDrivers import _eci_engine as engine


class FakeDll:
	"""Records calls, and keeps an active dictionary set per language.

	Measured on the proprietary engine and ReLoq C 1.1.1 alike: once a set has
	been made active for a language, the engine re-selects it whenever that
	language is selected again, and eciGetDict reports it.
	"""

	def __init__(self):
		self.calls = []
		self._next_dictionary = 1
		self._language = None
		self._active = {}

	def eciNewDict(self, handle):
		dictionary_handle = f"dict-{self._next_dictionary}"
		self._next_dictionary += 1
		self.calls.append(("newDict", handle, dictionary_handle))
		return dictionary_handle

	def eciSetDict(self, handle, dictionary_handle):
		self.calls.append(("setDict", handle, dictionary_handle))
		self._active[self._language] = dictionary_handle

	def eciGetDict(self, handle):
		return self._active.get(self._language)

	def eciLoadDict(self, handle, dictionary_handle, index, path):
		self.calls.append(
			("loadDict", handle, dictionary_handle, index, os.path.basename(path.decode("mbcs")))
		)

	def eciSetParam(self, handle, param_id, value):
		self.calls.append(("setParam", handle, param_id, value))
		if param_id == engine.ECI_LANGUAGE_PARAM:
			self._language = value

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
	# The engine reports through a sink rather than a transport.  Wiring the real
	# HostController._send_event in as that sink keeps these tests asserting the
	# actual Host Channel wire shape instead of a copy of it.
	sink = host.HostController(conn)._send_event if conn is not None else (lambda *a, **k: None)
	runtime = engine.EciEngine(
		sink,
		config=engine.EngineConfig(
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

		runtime._on_callback(None, 2, engine.FINAL_INDEX, None)

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
		runtime._on_callback(None, 2, engine.FINAL_INDEX, None)

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
			engine.get_dictionary_candidates("esp"),
			(
				("espmain.dic",),
				("esproot.dic",),
				("espabbr.dic",),
			),
		)

	def test_dictionary_candidates_allow_generic_fallback_for_english(self):
		self.assertEqual(
			engine.get_dictionary_candidates("eng"),
			(
				("engmain.dic", "enumain.dic", "main.dic"),
				("engroot.dic", "enuroot.dic", "root.dic"),
				("engabbr.dic", "enuabbr.dic", "abbr.dic"),
			),
		)

	def test_dictionary_candidates_allow_regional_language_fallbacks(self):
		self.assertEqual(
			engine.get_dictionary_candidates("esm"),
			(
				("esmmain.dic", "espmain.dic"),
				("esmroot.dic", "esproot.dic"),
				("esmabbr.dic", "espabbr.dic"),
			),
		)

	def test_dictionary_candidates_allow_english_fallback_for_chinese(self):
		self.assertEqual(
			engine.get_dictionary_candidates("chs"),
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
			runtime.set_param(9, engine.LANGS["esp"])

		self.assertIn(("loadDict", "eci", "dict-1", 0, "enumain.dic"), runtime._dll.calls)
		self.assertIn(("loadDict", "eci", "dict-2", 0, "espmain.dic"), runtime._dll.calls)
		self.assertLess(
			runtime._dll.calls.index(("setParam", "eci", 9, engine.LANGS["esp"])),
			runtime._dll.calls.index(("newDict", "eci", "dict-2")),
		)

	def _set_dict_calls(self, runtime):
		return [call for call in runtime._dll.calls if call[0] == "setDict"]

	def test_reselecting_the_same_language_does_not_set_its_dictionary_again(self):
		# eciSetDict cost 100-120 ms on ReLoq C 1.1.1 even for the set already
		# active (hozosch/Reloq-C#17, fixed in 1.1.2), and set_param(9) arrives
		# after every cancellation.
		with tempfile.TemporaryDirectory() as data_directory:
			open(os.path.join(data_directory, "enumain.dic"), "w").close()
			runtime = make_runtime(data_directory, "enu")
			runtime.set_param(9, engine.LANGS["enu"])
			runtime.set_param(9, engine.LANGS["enu"])
			runtime.set_param(9, engine.LANGS["enu"])

		self.assertEqual(self._set_dict_calls(runtime), [("setDict", "eci", "dict-1")])

	def test_returning_to_a_language_relies_on_the_engine_reselecting_its_set(self):
		with tempfile.TemporaryDirectory() as data_directory:
			for name in ("enumain.dic", "espmain.dic"):
				open(os.path.join(data_directory, name), "w").close()
			runtime = make_runtime(data_directory, "enu")
			for language in ("enu", "esp", "enu", "esp"):
				runtime.set_param(9, engine.LANGS[language])

		self.assertEqual(
			self._set_dict_calls(runtime),
			[("setDict", "eci", "dict-1"), ("setDict", "eci", "dict-2")],
		)

	def test_an_engine_without_eci_get_dict_still_gets_its_set(self):
		class NoGetDict(FakeDll):
			def __getattribute__(self, name):
				if name == "eciGetDict":
					raise AttributeError(name)
				return super().__getattribute__(name)

		with tempfile.TemporaryDirectory() as data_directory:
			open(os.path.join(data_directory, "enumain.dic"), "w").close()
			runtime = make_runtime(data_directory, "enu")
			runtime._dll = NoGetDict()
			runtime.set_param(9, engine.LANGS["enu"])
			runtime.set_param(9, engine.LANGS["enu"])

		self.assertEqual(len(self._set_dict_calls(runtime)), 2)


if __name__ == "__main__":
	unittest.main()
