"""Routing between the in-process engine and the Eloquence Host Process.

openevv ships US English only today, so a Voice Identity it does not have must
fall back to the host.  The available set is asked of the engine at run time, so
these tests drive it with a fake set rather than asserting a hardcoded list.
"""

import importlib.util
import queue
import sys
import types
import unittest
from pathlib import Path


def _load_client_module():
	config_module = types.ModuleType("config")
	config_module.conf = {}
	nvwave_module = types.ModuleType("nvwave")
	nvwave_module.WavePlayer = object
	build_version_module = types.ModuleType("buildVersion")
	build_version_module.version_year = 2026

	stubs = {
		"config": config_module,
		"nvwave": nvwave_module,
		"buildVersion": build_version_module,
	}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	module_name = "addon.synthDrivers._eloquence_routing_test"
	try:
		path = Path(__file__).parents[1] / "addon" / "synthDrivers" / "_eloquence.py"
		spec = importlib.util.spec_from_file_location(module_name, path)
		module = importlib.util.module_from_spec(spec)
		sys.modules[module_name] = module
		spec.loader.exec_module(module)
		return module
	finally:
		sys.modules.pop(module_name, None)
		for name, old_module in previous.items():
			if old_module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = old_module


ENU = 65536
DEU = 262144
JPN = 524288


class _FakeBackend:
	"""Records the Host Commands a backend would execute.

	Mirrors EngineClient's surface rather than only the parts a given test
	touches, so the fake cannot quietly diverge from the contract _activate()
	relies on.
	"""

	def __init__(self, name, pipeline, sample_rate=11025, supported=(8000, 11025)):
		self.name = name
		self.pipeline = pipeline
		self.commands = []
		self.started = False
		self.sample_rate = sample_rate
		self.supported_sample_rates = tuple(supported)

	def ensure_started(self):
		self.started = True

	def send_command(self, command, wait=True, **payload):
		self.commands.append((command, payload))
		return {
			"params": {},
			"voiceParams": {},
			"sampleRate": self.sample_rate,
			"supportedSampleRates": self.supported_sample_rates,
		}

	def absorb_state(self, response):
		rate = response.get("sampleRate")
		if rate:
			self.sample_rate = int(rate)
		supported = response.get("supportedSampleRates")
		if supported:
			self.supported_sample_rates = tuple(sorted(int(r) for r in supported))
		return response

	def set_sample_rate(self, rate):
		# An engine runs at the best rate it has at or below the request, which is
		# what makes the host answer 44100 with 11025.
		candidates = [r for r in self.supported_sample_rates if r <= int(rate)]
		self.sample_rate = (
			max(candidates) if candidates else min(self.supported_sample_rates or (11025,))
		)
		self.commands.append(("setSampleRate", {"rate": int(rate)}))
		return self.sample_rate

	def stop(self):
		self.pipeline.cancel()

	def shutdown(self):
		self.started = False


class BackendRoutingTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()
		self.pipeline = self.module.AudioPipeline()
		self.host = _FakeBackend("host", self.pipeline)
		self.direct = _FakeBackend("direct", self.pipeline)
		self.module._pipeline = self.pipeline
		self.module._client = self.host
		self.module._direct_client = self.direct
		self.module._active = self.host
		self.module._direct_languages = frozenset({ENU})
		self.module._engine_initialize_payload = {}

	def test_a_language_openevv_has_routes_to_the_in_process_engine(self):
		self.assertIs(self.module.backend_for_voice(ENU), self.direct)

	def test_a_language_openevv_lacks_falls_back_to_the_host(self):
		for voice in (DEU, JPN):
			with self.subTest(voice=voice):
				self.assertIs(self.module.backend_for_voice(voice), self.host)

	def test_no_in_process_engine_routes_everything_to_the_host(self):
		self.module._direct_client = None
		for voice in (ENU, DEU, JPN):
			with self.subTest(voice=voice):
				self.assertIs(self.module.backend_for_voice(voice), self.host)

	def test_a_failed_language_enumeration_degrades_to_host_only(self):
		# available_languages() returns an empty set when the library cannot be
		# loaded or does not implement the enumeration.  That must mean "host for
		# everything", never "nothing can speak".
		self.module._direct_languages = frozenset()
		for voice in (ENU, DEU, JPN):
			with self.subTest(voice=voice):
				self.assertIs(self.module.backend_for_voice(voice), self.host)

	def test_an_unparsable_voice_id_falls_back_to_the_host(self):
		for voice in (None, "not-a-voice", object()):
			with self.subTest(voice=voice):
				self.assertIs(self.module.backend_for_voice(voice), self.host)

	def test_a_voice_id_arriving_as_a_string_still_routes_to_openevv(self):
		# NVDA carries voice ids around as strings, so set_voice() can be handed
		# either form.
		self.assertIs(self.module.backend_for_voice(str(ENU)), self.direct)

	def test_openevv_languages_are_reported_as_the_engine_gave_them(self):
		self.assertEqual(self.module.direct_languages(), frozenset({ENU}))


class BackendActivationTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()
		self.pipeline = self.module.AudioPipeline()
		self.host = _FakeBackend("host", self.pipeline)
		self.direct = _FakeBackend("direct", self.pipeline)
		self.module._pipeline = self.pipeline
		self.module._client = self.host
		self.module._direct_client = self.direct
		self.module._active = self.direct
		self.direct.started = True
		self.module._direct_languages = frozenset({ENU})
		self.module._engine_initialize_payload = {
			"eciPath": "C:\\eci.dll",
			"dataDirectory": "C:\\",
			"language": "enu",
		}

	def test_the_host_is_not_started_until_a_fallback_language_needs_it(self):
		# An English-only user should never pay for the Eloquence Host Process.
		self.assertFalse(self.host.started)
		self.module._activate(self.module.backend_for_voice(ENU))
		self.assertFalse(self.host.started)

	def test_falling_back_starts_and_initializes_the_host_once(self):
		self.module._activate(self.module.backend_for_voice(DEU))
		self.assertTrue(self.host.started)
		self.assertEqual([c for c, _ in self.host.commands], ["initialize"])
		# Returning to openevv and back must not initialize it a second time.
		self.module._activate(self.module.backend_for_voice(ENU))
		self.module._activate(self.module.backend_for_voice(DEU))
		self.assertEqual([c for c, _ in self.host.commands], ["initialize"])

	def test_the_host_is_initialized_with_the_proprietary_engine_path(self):
		self.module._activate(self.module.backend_for_voice(DEU))
		_command, payload = self.host.commands[0]
		self.assertEqual(payload["eciPath"], "C:\\eci.dll")
		# The proprietary ECI.INI carries C:\dummy\ placeholders that do need
		# rewriting, so the host must not inherit openevv's opt-out.
		self.assertNotEqual(payload.get("rewriteIni"), False)
		# Nor openevv's eciWideband, which the proprietary engine does not have.
		self.assertFalse(payload.get("wideband", False))

	def test_the_in_process_engine_is_initialized_for_openevv_not_the_host_dll(self):
		self.module._active = self.host
		self.host.started = True
		self.direct.started = False
		self.module._activate(self.module.backend_for_voice(ENU))
		_command, payload = self.direct.commands[0]
		self.assertTrue(payload["eciPath"].lower().endswith("openevv\\eci.dll"))
		# openevv ships an eci.ini that needs no rewriting, but it reads .dic files
		# from the same directory the host does, so that is passed through.
		self.assertEqual(payload["dataDirectory"], "C:\\")
		self.assertFalse(payload["rewriteIni"])
		self.assertTrue(payload["wideband"])

	def test_switching_backend_is_a_no_op_when_it_is_already_active(self):
		self.module._activate(self.direct)
		self.assertEqual(self.direct.commands, [])


class MixedLanguageOrderingTests(unittest.TestCase):
	"""A mixed-language utterance must stay in order across the two backends."""

	def setUp(self):
		self.module = _load_client_module()
		self.pipeline = self.module.AudioPipeline()
		self.host = _FakeBackend("host", self.pipeline)
		self.direct = _FakeBackend("direct", self.pipeline)
		self.module._pipeline = self.pipeline
		self.module._client = self.host
		self.module._direct_client = self.direct
		self.module._active = self.direct
		self.direct.started = True
		self.host.started = True
		self.module._direct_languages = frozenset({ENU})
		self.module._engine_initialize_payload = {}

	def test_fragments_reach_the_backend_that_owns_their_language(self):
		self.module._activate(self.module.backend_for_voice(ENU))
		self.module.speak(b"english")
		self.module._activate(self.module.backend_for_voice(DEU))
		self.module.speak(b"deutsch")
		self.module._activate(self.module.backend_for_voice(ENU))
		self.module.speak(b"english again")

		self.assertEqual(
			[p["text"] for c, p in self.direct.commands if c == "addText"],
			[b"english", b"english again"],
		)
		self.assertEqual(
			[p["text"] for c, p in self.host.commands if c == "addText"],
			[b"deutsch"],
		)

	def test_audio_from_both_backends_shares_one_pipeline_and_generation(self):
		# One queue and one Speech Generation, or the two engines would race for
		# the output device and for cancellation.
		self.assertIs(self.direct.pipeline, self.host.pipeline)
		self.pipeline.current_seq = 7
		self.pipeline.handle_event("audio", {"data": b"from-direct", "index": None, "final": False})
		self.pipeline.handle_event("audio", {"data": b"from-host", "index": 3, "final": False})
		chunks = []
		while not self.pipeline.queue.empty():
			chunks.append(self.pipeline.queue.get_nowait())
		self.assertEqual(
			chunks,
			[(b"from-direct", None, False, 7), (b"from-host", 3, False, 7)],
		)

	def test_cancelling_stops_every_live_backend_and_advances_once(self):
		before = self.pipeline.sequence
		self.module.stop()
		# Both backends were asked to stop, but the Speech Generation advanced
		# once per backend cancel; what matters is that it moved, so stale Audio
		# Chunks from either engine are discarded.
		self.assertGreater(self.pipeline.sequence, before)

	def test_queued_audio_from_an_older_generation_is_discarded(self):
		self.pipeline.current_seq = 1
		self.pipeline.handle_event("audio", {"data": b"stale", "index": None, "final": False})
		self.pipeline.sequence = 5
		chunk = self.pipeline.queue.get_nowait()
		self.assertLess(chunk[3], self.pipeline.sequence)


class EngineSelectionDefaultTests(unittest.TestCase):
	"""The Eloquence Host Process is the default, and stays it on a bad value."""

	def setUp(self):
		self.module = _load_client_module()

	def _with_conf(self, section):
		self.module.config.conf = {"eloquence": section} if section is not None else {}
		return self.module.openevv_enabled()

	def test_a_fresh_install_uses_the_host(self):
		# No config at all, and a config section that predates the setting.
		self.assertFalse(self._with_conf(None))
		self.assertFalse(self._with_conf({}))
		self.assertFalse(self._with_conf({"dictionary_name": "IBM TTS Dictionaries"}))

	def test_a_real_boolean_is_honoured(self):
		self.assertTrue(self._with_conf({"use_openevv": True}))
		self.assertFalse(self._with_conf({"use_openevv": False}))

	def test_a_stringly_typed_false_does_not_enable_it(self):
		# NVDA stores this section without a confspec, so values come back as
		# strings.  bool("False") is True, which would have turned the engine on
		# for someone who had just turned it off.
		for value in ("False", "false", "FALSE", "no", "off", "0", ""):
			with self.subTest(value=value):
				self.assertFalse(self._with_conf({"use_openevv": value}))

	def test_a_stringly_typed_true_does_enable_it(self):
		for value in ("True", "true", "yes", "on", "1", " True "):
			with self.subTest(value=value):
				self.assertTrue(self._with_conf({"use_openevv": value}))

	def test_an_unrecognised_value_falls_back_to_the_host(self):
		for value in ("maybe", "2", None):
			with self.subTest(value=value):
				self.assertFalse(self._with_conf({"use_openevv": value}))


class DirectInitializePayloadTests(unittest.TestCase):
	"""What the in-process backend changes about an initialize payload, and what not."""

	def setUp(self):
		self.module = _load_client_module()

	def test_the_dictionary_directory_is_passed_through(self):
		# It was cleared while openevv v0.3 was what shipped, because its
		# eciLoadDict returned 6 for every file the proprietary engine accepts with
		# 0.  Measured on main@7ee8c572: the add-on's own dictionaries load with 0
		# and change the audio exactly as they do on the proprietary engine, so
		# clearing this would now be throwing a working feature away.
		payload = self.module._direct_initialize_payload(
			{"eciPath": r"C:\eci.dll", "dataDirectory": r"C:\dicts", "language": "enu"}
		)
		self.assertEqual(payload["dataDirectory"], r"C:\dicts")

	def test_the_engine_path_and_ini_handling_are_retargeted(self):
		payload = self.module._direct_initialize_payload(
			{"eciPath": r"C:\eci.dll", "dataDirectory": r"C:\dicts", "language": "enu"}
		)
		self.assertNotEqual(payload["eciPath"], r"C:\eci.dll")
		self.assertFalse(payload["rewriteIni"])

	def test_the_callers_payload_is_not_mutated(self):
		# The host is initialized from the same stored payload and must still see
		# its own eciPath and ECI.INI rewriting.
		original = {"eciPath": r"C:\eci.dll", "dataDirectory": r"C:\dicts", "language": "enu"}
		self.module._direct_initialize_payload(original)
		self.assertEqual(original["eciPath"], r"C:\eci.dll")
		self.assertNotIn("rewriteIni", original)
		self.assertNotIn("wideband", original)


class DirectCancellationTests(unittest.TestCase):
	"""stop() must not touch the engine from NVDA's thread."""

	def setUp(self):
		self.module = _load_client_module()
		self.pipeline = self.module.AudioPipeline()
		self.client = self.module.DirectEngineClient(self.pipeline, "C:/nope/eci.dll")
		self.stopped = []

		class _Engine:
			def stop(_self):
				self.stopped.append(True)

		class _Dispatcher:
			engine = _Engine()

			def knows(_self, command):
				return True

			def handle(_self, command, payload):
				return {"command": command}

		self.client._dispatcher = _Dispatcher()

	def test_cancelling_does_not_call_the_engine_inline(self):
		# It runs on NVDA's thread, where blocking on the command lock an in-flight
		# synthesize() holds would freeze the screen reader.
		self.client.stop()
		self.assertEqual(self.stopped, [])
		self.assertTrue(self.client._reset_pending)

	def test_cancelling_advances_the_generation_immediately(self):
		before = self.pipeline.sequence
		self.client.stop()
		self.assertGreater(self.pipeline.sequence, before)

	def test_the_next_command_performs_the_reset_on_the_worker_thread(self):
		self.client.stop()
		self.client.send_command("addText", text=b"next utterance")
		self.assertEqual(self.stopped, [True])
		self.assertFalse(self.client._reset_pending)

	def test_the_reset_happens_only_once_per_cancellation(self):
		self.client.stop()
		self.client.send_command("addText", text=b"a")
		self.client.send_command("addText", text=b"b")
		self.assertEqual(self.stopped, [True])

	def test_no_cancellation_means_no_reset(self):
		self.client.send_command("addText", text=b"a")
		self.assertEqual(self.stopped, [])


class AudioPipelineSingletonTests(unittest.TestCase):
	def test_both_client_types_accept_the_shared_pipeline(self):
		module = _load_client_module()
		pipeline = module.AudioPipeline()
		host = module.EloquenceHostClient(pipeline)
		direct = module.DirectEngineClient(pipeline, "C:\\nope\\eci.dll")
		self.assertIs(host.pipeline, pipeline)
		self.assertIs(direct.pipeline, pipeline)
		self.assertFalse(host.started)
		self.assertFalse(direct.started)

	def test_a_missing_openevv_engine_fails_with_a_useful_message(self):
		module = _load_client_module()
		direct = module.DirectEngineClient(module.AudioPipeline(), "C:\\nope\\eci.dll")
		with self.assertRaises(RuntimeError) as caught:
			direct.ensure_started()
		self.assertIn("nope", str(caught.exception))

	def test_commands_before_start_are_refused_rather_than_silently_dropped(self):
		module = _load_client_module()
		direct = module.DirectEngineClient(module.AudioPipeline(), "C:\\nope\\eci.dll")
		with self.assertRaises(RuntimeError):
			direct.send_command("synthesize")


class EngineQueueTests(unittest.TestCase):
	def test_the_synthesis_worker_reads_the_pipeline_generation(self):
		# The worker used to read the host client's counter directly; with two
		# backends that counter has to live on the shared pipeline instead.
		module = _load_client_module()
		pipeline = module.AudioPipeline()
		module._pipeline = pipeline
		pipeline.sequence = 4
		calls = []
		module.synth_queue = queue.Queue()
		module.synth_queue.put(([(lambda: calls.append("stale"), ())], 1))
		module.synth_queue.put(([(lambda: calls.append("current"), ())], 9))
		module.synth_queue.put(None)
		module._synth_worker_loop()
		self.assertEqual(calls, ["current"])


if __name__ == "__main__":
	unittest.main()
