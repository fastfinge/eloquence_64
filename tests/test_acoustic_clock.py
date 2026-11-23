"""ReLoq C's acoustic clock, the "ReLoq C timing" Speech setting.

The original engine gives its synthesiser new acoustic targets every 5 ms.
ReLoq C can also do it every 1 ms, and that is what a fresh handle does.  Its
parameter 0x101b chooses: 0 for 1 ms, 1 for the original 5 ms, 2 for ReLoq C's
own choice per language (5 ms for Castilian Spanish and Greek, 1 ms otherwise).

This add-on asks for the original clock unless the user chooses otherwise,
because sounding like Eloquence 6.1 is what it is for.  Measured 2026-11-22 on
ReLoq C 1.4.1 (codex/release-1-4-1-names@c24fbd78), samples at 11025 Hz:

	language  rate  1 ms    5 ms    per language
	enu       50    40117   40117   same audio as 1 ms
	enu       90    18183   18183   same audio as 1 ms
	esp       50    26455   26455   same audio as 5 ms
	deu       50    21384   21384   same audio as 1 ms

The audio differs between the two clocks in every row and the length in none.
tests/test_reloq_engine.py pins that against the real DLL; these tests cover
the plumbing, which needs no engine.

Neither other engine has the parameter, so it is sent only to ReLoq C.
"""

import ctypes
import importlib.util
import queue
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from addon.synthDrivers import _eci_engine as engine


class _Dll:
	"""Records every parameter set; refuses the clock if told to."""

	def __init__(self, knows_clock=True):
		self.knows_clock = knows_clock
		self.set_params = []

	def eciSetParam(self, handle, param, value):
		self.set_params.append((param, value))
		if param == engine.RELOQ_ACOUSTIC_CLOCK and not self.knows_clock:
			return -1
		return 0

	def eciNewEx(self, language):
		return 1

	def __getattr__(self, name):
		return lambda *args: 1


def _start(knows_clock=True, **overrides):
	config = engine.EngineConfig(
		eci_path="eci.dll",
		data_directory="",
		language_code="enu",
		enable_abbrev_dict=False,
		enable_phrase_prediction=False,
		voice_variant=0,
		rewrite_ini=False,
		**overrides,
	)
	instance = engine.EciEngine(lambda event, **payload: None, config)
	dll = _Dll(knows_clock)
	with (
		mock.patch.object(ctypes.windll, "LoadLibrary", return_value=dll),
		mock.patch.object(engine.EciEngine, "_declare_signatures"),
		mock.patch.object(engine.EciEngine, "_load_dictionaries"),
	):
		instance.start()
	return instance, dll


class EngineTests(unittest.TestCase):
	def test_the_values_are_reloq_cs_own(self):
		# From include/reloq_eci.h; a renumbering here would silently pick the
		# wrong clock.
		self.assertEqual(engine.RELOQ_ACOUSTIC_CLOCK, 0x101B)
		self.assertEqual(
			(engine.RELOQ_CLOCK_FINE, engine.RELOQ_CLOCK_ORIGINAL, engine.RELOQ_CLOCK_PER_LANGUAGE),
			(0, 1, 2),
		)

	def test_it_is_set_at_start_when_asked_for(self):
		instance, dll = _start(acoustic_clock=engine.RELOQ_CLOCK_ORIGINAL)
		self.assertIn((engine.RELOQ_ACOUSTIC_CLOCK, engine.RELOQ_CLOCK_ORIGINAL), dll.set_params)
		self.assertEqual(
			instance.get_state()["params"][engine.RELOQ_ACOUSTIC_CLOCK], engine.RELOQ_CLOCK_ORIGINAL
		)

	def test_the_fine_clock_is_sent_too(self):
		# 0 is a choice, not "nothing to do": only None leaves the engine alone.
		_instance, dll = _start(acoustic_clock=engine.RELOQ_CLOCK_FINE)
		self.assertIn((engine.RELOQ_ACOUSTIC_CLOCK, engine.RELOQ_CLOCK_FINE), dll.set_params)

	def test_no_other_engine_is_offered_it(self):
		_instance, dll = _start()
		self.assertNotIn(engine.RELOQ_ACOUSTIC_CLOCK, [param for param, _value in dll.set_params])

	def test_a_reloq_c_without_it_is_left_as_it_was(self):
		# The parameter arrived in ReLoq C 1.3.1.  An older build answers -1 and
		# goes on speaking on its 1 ms clock, which must not be recorded as set.
		with self.assertLogs(engine.LOGGER, "INFO"):
			instance, _dll = _start(knows_clock=False, acoustic_clock=engine.RELOQ_CLOCK_ORIGINAL)
		self.assertNotIn(engine.RELOQ_ACOUSTIC_CLOCK, instance.get_state()["params"])

	def test_a_later_change_is_checked_the_same_way(self):
		instance, dll = _start(acoustic_clock=engine.RELOQ_CLOCK_ORIGINAL)
		instance.set_param(engine.RELOQ_ACOUSTIC_CLOCK, engine.RELOQ_CLOCK_FINE)
		self.assertEqual(dll.set_params[-1], (engine.RELOQ_ACOUSTIC_CLOCK, engine.RELOQ_CLOCK_FINE))
		self.assertEqual(instance.get_state()["params"][engine.RELOQ_ACOUSTIC_CLOCK], engine.RELOQ_CLOCK_FINE)
		dll.knows_clock = False
		with self.assertLogs(engine.LOGGER, "INFO"):
			instance.set_param(engine.RELOQ_ACOUSTIC_CLOCK, engine.RELOQ_CLOCK_ORIGINAL)
		self.assertEqual(instance.get_state()["params"][engine.RELOQ_ACOUSTIC_CLOCK], engine.RELOQ_CLOCK_FINE)


class DispatcherTests(unittest.TestCase):
	def _initialize(self, payload):
		captured = {}

		class _Probe(engine.EciEngine):
			def start(self):
				captured["clock"] = self._config.acoustic_clock

		with mock.patch.object(engine, "EciEngine", _Probe):
			engine.EciDispatcher(lambda *a, **k: None).handle(
				"initialize",
				{"eciPath": "", "dataDirectory": "", "language": "enu", **payload},
			)
		return captured["clock"]

	def test_the_initialize_payload_carries_it(self):
		self.assertEqual(self._initialize({"acousticClock": 1}), 1)
		self.assertEqual(self._initialize({"acousticClock": 0}), 0)

	def test_omitting_it_leaves_the_engine_alone(self):
		self.assertIsNone(self._initialize({}))


def _load_client_module():
	"""_eloquence with NVDA stubbed, as tests/test_backend_routing.py loads it."""
	config_module = types.ModuleType("config")
	config_module.conf = {}
	nvwave_module = types.ModuleType("nvwave")
	nvwave_module.WavePlayer = object
	build_version_module = types.ModuleType("buildVersion")
	build_version_module.version_year = 2026
	stubs = {"config": config_module, "nvwave": nvwave_module, "buildVersion": build_version_module}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	module_name = "addon.synthDrivers._eloquence_clock_test"
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


class _Direct:
	def __init__(self, started=True):
		self.started = started
		self.commands = []

	def send_command(self, command, wait=True, **payload):
		self.commands.append((command, payload))
		return {"params": {}, "voiceParams": {}}


class SettingTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()
		self.module.synth_queue = queue.Queue()
		# No worker thread: each test runs what was queued itself.
		self.module.process = lambda: None
		self.direct = _Direct()
		self.module._direct_client = self.direct
		self.module._direct_engine = self.module.RELOQ

	def _run_queued(self):
		ran = []
		while not self.module.synth_queue.empty():
			calls, generation = self.module.synth_queue.get_nowait()
			ran.append(generation)
			for function, arguments in calls:
				function(*arguments)
		return ran

	def test_the_default_is_the_original_clock(self):
		self.assertEqual(self.module.DEFAULT_ACOUSTIC_CLOCK, "original")
		self.assertEqual(self.module.requested_acoustic_clock(), "original")
		self.assertEqual(self.module.ACOUSTIC_CLOCKS["original"], engine.RELOQ_CLOCK_ORIGINAL)

	def test_every_setting_names_a_clock_the_engine_has(self):
		self.assertEqual(
			self.module.ACOUSTIC_CLOCKS,
			{
				"original": engine.RELOQ_CLOCK_ORIGINAL,
				"fine": engine.RELOQ_CLOCK_FINE,
				"language": engine.RELOQ_CLOCK_PER_LANGUAGE,
			},
		)

	def test_reloq_c_starts_on_the_chosen_clock(self):
		base = {"eciPath": r"C:\eci.dll", "dataDirectory": r"C:\dicts", "language": "enu"}
		payload = self.module._direct_initialize_payload(base, self.module.RELOQ)
		self.assertEqual(payload["acousticClock"], engine.RELOQ_CLOCK_ORIGINAL)
		self.module.set_acoustic_clock("fine")
		payload = self.module._direct_initialize_payload(base, self.module.RELOQ)
		self.assertEqual(payload["acousticClock"], engine.RELOQ_CLOCK_FINE)

	def test_openevv_is_never_sent_it(self):
		base = {"eciPath": r"C:\eci.dll", "dataDirectory": r"C:\dicts", "language": "enu"}
		self.assertNotIn("acousticClock", self.module._direct_initialize_payload(base, self.module.OPENEVV))

	def test_a_change_reaches_the_running_engine_on_the_worker(self):
		self.assertEqual(self.module.set_acoustic_clock("fine"), "fine")
		# Nothing on the caller's thread: the engine belongs to the worker.
		self.assertEqual(self.direct.commands, [])
		self._run_queued()
		self.assertEqual(
			self.direct.commands,
			[("setParam", {"paramId": engine.RELOQ_ACOUSTIC_CLOCK, "value": engine.RELOQ_CLOCK_FINE})],
		)
		self.assertEqual(self.module.requested_acoustic_clock(), "fine")

	def test_a_change_is_queued_outside_any_speech_generation(self):
		self.module.set_acoustic_clock("language")
		self.assertEqual(self._run_queued(), [None])

	def test_an_engine_not_yet_started_takes_it_from_its_initialize_payload(self):
		self.direct.started = False
		self.module.set_acoustic_clock("fine")
		self._run_queued()
		self.assertEqual(self.direct.commands, [])
		self.assertEqual(self.module.requested_acoustic_clock(), "fine")

	def test_it_is_remembered_but_not_sent_when_reloq_c_is_not_the_engine(self):
		for engine_name in (self.module.OPENEVV, None):
			with self.subTest(engine=engine_name):
				self.module._direct_engine = engine_name
				self.assertFalse(self.module.acoustic_clock_available())
				self.module.set_acoustic_clock("fine")
				self.assertTrue(self.module.synth_queue.empty())
				self.assertEqual(self.direct.commands, [])
				self.assertEqual(self.module.requested_acoustic_clock(), "fine")

	def test_an_unknown_setting_changes_nothing(self):
		with self.assertLogs(self.module.LOGGER, "WARNING"):
			self.assertEqual(self.module.set_acoustic_clock("7 ms"), "original")
		self.assertTrue(self.module.synth_queue.empty())

	def test_a_failing_engine_does_not_stop_the_worker(self):
		self.direct.send_command = mock.Mock(side_effect=RuntimeError("engine gone"))
		self.module.set_acoustic_clock("fine")
		with self.assertLogs(self.module.LOGGER, "ERROR"):
			self._run_queued()


class WorkerTests(unittest.TestCase):
	"""A setting change must outlive the cancellations NVDA makes around it."""

	def setUp(self):
		self.module = _load_client_module()
		self.module.synth_queue = queue.Queue()
		self.pipeline = self.module.AudioPipeline()
		self.module._pipeline = self.pipeline

	def _drain(self):
		self.module.synth_queue.put(None)
		self.module._synth_worker_loop()

	def test_work_with_no_generation_survives_a_cancellation(self):
		ran = []
		self.module.synth_queue.put(([(ran.append, ("speech",))], self.pipeline.sequence))
		self.module.synth_queue.put(([(ran.append, ("clock",))], None))
		self.pipeline.cancel()
		self._drain()
		self.assertEqual(ran, ["clock"])

	def test_work_with_no_generation_does_not_restamp_the_audio(self):
		# The worker stamps Audio Chunks with the generation of the utterance it
		# is on; housekeeping in between must leave that alone.
		self.pipeline.current_seq = 5
		self.module.synth_queue.put(([(lambda: None, ())], None))
		self._drain()
		self.assertEqual(self.pipeline.current_seq, 5)


if __name__ == "__main__":
	unittest.main()
