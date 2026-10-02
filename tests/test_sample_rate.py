"""The user-selectable output sample rate.

ECI parameter 5 selects the rate as an index, and what each engine accepts
differs, so it is probed at run time rather than hardcoded.  Measured 2026-10-02:

	proprietary ECI.DLL   8000, 11025          (2-6 rejected with -1)
	openevv main@7ee8c572 8000, 11025, 16000, 22050, 32000, 44100, 48000

Those are genuine rate changes rather than slower speech, confirmed by measuring
F0 on a sustained vowel against each claimed rate: 103.0-103.9 Hz across all
seven, within 0.9%.  eciSetParam answers with the parameter's *previous* value,
or -1 when it rejects the one offered, which is the only way to tell what an
engine has -- there is no enumeration call as there is for languages.

The combo box offers the union across backends, not the intersection, because an
engine that cannot do the chosen rate runs at its own best one and the single
Audio Playback Pipeline follows whichever backend is speaking.  The cost is a
brief gap when an utterance crosses backends, which is why the rate change
travels *through* the pipeline queue: audio the outgoing engine already produced
is still queued behind it, and reopening the device on the caller's thread would
play that tail at the new rate.
"""

import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

from addon.synthDrivers import _eci_engine as engine


def _load_client_module():
	config_module = types.ModuleType("config")
	config_module.conf = {"audio": {"outputDevice": "default"}, "speech": {"eci": {}}}
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
	module_name = "addon.synthDrivers._eloquence_rate_test"
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


class _RateDll:
	"""A DLL that accepts only the sample-rate values it is told to.

	eciSetParam returns the previous value, or -1 on refusal, as both real
	engines do.
	"""

	def __init__(self, accepted=(0, 1)):
		self.accepted = set(accepted)
		self.params = {engine.ECI_SAMPLE_RATE: 1}
		self.output_buffers = []

	def eciSetParam(self, handle, param, value):
		if param == engine.ECI_SAMPLE_RATE and value not in self.accepted:
			return -1
		previous = self.params.get(param, 0)
		self.params[param] = value
		return previous

	def eciGetParam(self, handle, param):
		return self.params.get(param, 0)

	def eciSetOutputBuffer(self, handle, samples, buffer):
		self.output_buffers.append(samples)
		return 1

	def __getattr__(self, name):
		def anything(*args):
			return 1

		return anything


def _engine_with(accepted=(0, 1), requested=engine.SAMPLE_RATE):
	config = engine.EngineConfig(
		eci_path="",
		data_directory="",
		language_code="enu",
		enable_abbrev_dict=False,
		enable_phrase_prediction=False,
		voice_variant=0,
		rewrite_ini=False,
		sample_rate=requested,
	)
	instance = engine.EciEngine(lambda event, **payload: None, config)
	instance._dll = _RateDll(accepted)
	instance._handle = "eci"
	instance._supported_sample_rates = instance._probe_sample_rates()
	return instance


class ChooseSampleRateTests(unittest.TestCase):
	def test_an_exactly_supported_rate_is_used_as_is(self):
		self.assertEqual(engine.choose_sample_rate(22050, (8000, 11025, 22050)), 22050)

	def test_an_unsupported_rate_falls_back_downwards(self):
		# Downwards specifically: running an engine faster than asked would make
		# every utterance from it play slow and low, where running it slower only
		# costs fidelity.
		self.assertEqual(engine.choose_sample_rate(44100, (8000, 11025)), 11025)
		self.assertEqual(engine.choose_sample_rate(16000, (8000, 11025)), 11025)

	def test_a_rate_below_everything_supported_takes_the_lowest(self):
		self.assertEqual(engine.choose_sample_rate(4000, (11025, 22050)), 11025)

	def test_an_engine_reporting_nothing_gets_the_universal_rate(self):
		self.assertEqual(engine.choose_sample_rate(44100, ()), engine.SAMPLE_RATE)


class OutputBufferTests(unittest.TestCase):
	def test_the_default_rate_is_unchanged(self):
		# Scaling by the ratio rather than recomputing from milliseconds keeps
		# this exact, so nothing moves for existing users.
		self.assertEqual(engine.output_buffer_samples(engine.SAMPLE_RATE), 1100)

	def test_the_chunk_duration_is_held_roughly_constant(self):
		for rate in (8000, 16000, 22050, 44100, 48000):
			with self.subTest(rate=rate):
				samples = engine.output_buffer_samples(rate)
				seconds = samples / rate
				self.assertAlmostEqual(seconds, 1100 / engine.SAMPLE_RATE, places=3)

	def test_it_never_returns_a_useless_buffer(self):
		self.assertGreaterEqual(engine.output_buffer_samples(1), 1)


class ProbeTests(unittest.TestCase):
	def test_only_the_accepted_values_are_reported(self):
		instance = _engine_with(accepted=(0, 1))
		self.assertEqual(instance.supported_sample_rates(), (8000, 11025))

	def test_an_engine_taking_everything_reports_everything(self):
		instance = _engine_with(accepted=tuple(engine.SAMPLE_RATE_VALUES))
		self.assertEqual(
			instance.supported_sample_rates(),
			tuple(sorted(engine.SAMPLE_RATE_VALUES.values())),
		)

	def test_the_probe_puts_the_original_value_back(self):
		instance = _engine_with(accepted=(0, 1))
		self.assertEqual(instance._dll.params[engine.ECI_SAMPLE_RATE], 1)

	def test_an_engine_without_the_parameter_reports_nothing(self):
		instance = _engine_with(accepted=())
		self.assertEqual(instance.supported_sample_rates(), ())
		# And then the parameter is left alone entirely rather than guessed at.
		self.assertEqual(instance._apply_sample_rate(44100), engine.SAMPLE_RATE)


class SetSampleRateTests(unittest.TestCase):
	def test_a_supported_rate_is_applied_and_sizes_the_buffer(self):
		instance = _engine_with(accepted=tuple(engine.SAMPLE_RATE_VALUES))
		self.assertEqual(instance.set_sample_rate(22050), 22050)
		self.assertEqual(instance.sample_rate, 22050)
		self.assertEqual(instance._samples, engine.output_buffer_samples(22050))
		# The engine has to be told about the new buffer, or it keeps writing into
		# the old one.
		self.assertEqual(instance._dll.output_buffers[-1], instance._samples)

	def test_an_unsupported_rate_is_clamped_rather_than_refused(self):
		instance = _engine_with(accepted=(0, 1))
		self.assertEqual(instance.set_sample_rate(48000), 11025)

	def test_the_state_reports_the_rate_and_the_supported_set(self):
		instance = _engine_with(accepted=(0, 1))
		instance._apply_sample_rate(8000)
		instance._sample_rate = 8000
		state = instance.get_state()
		self.assertEqual(state["sampleRate"], 8000)
		self.assertEqual(state["supportedSampleRates"], (8000, 11025))


class DispatcherTests(unittest.TestCase):
	def test_the_initialize_payload_carries_the_rate(self):
		captured = {}

		class _Probe(engine.EciEngine):
			def start(self):
				captured["rate"] = self._config.sample_rate

		original = engine.EciEngine
		engine.EciEngine = _Probe
		try:
			dispatcher = engine.EciDispatcher(lambda *a, **k: None)
			dispatcher.handle(
				"initialize",
				{
					"eciPath": "",
					"dataDirectory": "",
					"language": "enu",
					"sampleRate": 22050,
				},
			)
		finally:
			engine.EciEngine = original
		self.assertEqual(captured["rate"], 22050)

	def test_omitting_the_rate_keeps_the_universal_one(self):
		captured = {}

		class _Probe(engine.EciEngine):
			def start(self):
				captured["rate"] = self._config.sample_rate

		original = engine.EciEngine
		engine.EciEngine = _Probe
		try:
			dispatcher = engine.EciDispatcher(lambda *a, **k: None)
			dispatcher.handle(
				"initialize", {"eciPath": "", "dataDirectory": "", "language": "enu"}
			)
		finally:
			engine.EciEngine = original
		self.assertEqual(captured["rate"], engine.SAMPLE_RATE)

	def test_set_sample_rate_is_a_host_command(self):
		dispatcher = engine.EciDispatcher(lambda *a, **k: None)
		self.assertTrue(dispatcher.knows("setSampleRate"))


class _Player:
	opened = []

	def __init__(self, channels, rate, bits, **kwargs):
		self.rate = rate
		self.fed = []
		self.closed = False
		self.synced = 0
		_Player.opened.append(self)

	def feed(self, data, onDone=None):
		self.fed.append(data)
		if onDone:
			onDone()

	def sync(self):
		self.synced += 1

	def idle(self):
		pass

	def stop(self):
		pass

	def close(self):
		self.closed = True


class PipelineRateTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()
		self.module.nvwave.WavePlayer = _Player
		_Player.opened = []
		self.pipeline = self.module.AudioPipeline()

	def tearDown(self):
		self.pipeline.close_audio()

	def test_the_device_opens_at_the_requested_rate(self):
		self.pipeline.initialize_audio(22050)
		self.assertEqual(self.pipeline.rate, 22050)
		self.assertEqual(_Player.opened[0].rate, 22050)

	def test_asking_for_the_rate_already_in_use_opens_nothing_new(self):
		self.pipeline.initialize_audio(22050)
		self.pipeline.request_rate(22050)
		self.assertEqual(len(_Player.opened), 1)

	def test_a_rate_change_before_the_worker_exists_just_moves_the_target(self):
		self.pipeline.request_rate(16000)
		self.pipeline.initialize_audio()
		self.assertEqual(self.pipeline.rate, 16000)
		self.assertEqual(len(_Player.opened), 1)

	def test_queued_audio_is_fed_before_the_device_changes(self):
		# The invariant the whole design exists for: a chunk the previous engine
		# produced must not reach a player opened at the new rate.
		self.pipeline.initialize_audio(11025)
		self.pipeline.handle_event("audio", {"data": b"\x00" * 100, "index": None, "final": False})
		self.pipeline.request_rate(22050)
		self.pipeline.handle_event("audio", {"data": b"\x11" * 40, "index": None, "final": False})
		# The engine marks the end with a separate empty event, which is also what
		# releases the chunk the worker holds back to carry a trailing index.
		self.pipeline.handle_event("audio", {"data": b"", "index": None, "final": True})
		deadline = time.monotonic() + 5
		while time.monotonic() < deadline and len(_Player.opened) < 2:
			time.sleep(0.01)
		self.assertEqual([player.rate for player in _Player.opened], [11025, 22050])
		self.assertEqual(b"".join(_Player.opened[0].fed), b"\x00" * 100)
		self.assertEqual(b"".join(_Player.opened[1].fed), b"\x11" * 40)

	def test_the_old_device_is_synced_and_closed(self):
		self.pipeline.initialize_audio(11025)
		first = _Player.opened[0]
		self.pipeline.request_rate(22050)
		deadline = time.monotonic() + 5
		while time.monotonic() < deadline and not first.closed:
			time.sleep(0.01)
		# sync() before close(), or the tail of the outgoing engine's audio is
		# swallowed rather than played.
		self.assertGreaterEqual(first.synced, 1)
		self.assertTrue(first.closed)

	def test_rebuilding_to_the_rate_already_open_reuses_the_player(self):
		self.pipeline.initialize_audio(11025)
		first = _Player.opened[0]
		self.assertIs(self.pipeline.rebuild_player(11025), first)
		self.assertEqual(len(_Player.opened), 1)


class BackendRateTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()

	def test_a_client_learns_its_rate_from_any_response(self):
		client = self.module.EngineClient(self.module.AudioPipeline())
		client.absorb_state({"sampleRate": 22050, "supportedSampleRates": [11025, 22050]})
		self.assertEqual(client.sample_rate, 22050)
		self.assertEqual(client.supported_sample_rates, (11025, 22050))

	def test_a_response_without_rate_information_changes_nothing(self):
		client = self.module.EngineClient(self.module.AudioPipeline())
		client.absorb_state({"params": {}})
		self.assertEqual(client.sample_rate, engine.SAMPLE_RATE)
		self.assertEqual(client.supported_sample_rates, ())

	def test_a_backend_that_has_not_started_is_not_sent_commands(self):
		# A sleeping backend has no engine to configure, and waking one just to
		# set its rate would defeat the lazy start the whole routing design rests
		# on.  It picks the rate up from the initialize payload instead.
		class _Sleeping(self.module.EngineClient):
			@property
			def started(self):
				return False

			def send_command(self, command, wait=True, **payload):
				raise AssertionError("a sleeping backend was sent %r" % command)

		client = _Sleeping(self.module.AudioPipeline())
		self.assertEqual(client.set_sample_rate(44100), engine.SAMPLE_RATE)

	def test_the_offered_set_is_the_union_not_the_intersection(self):
		self.module._direct_sample_rates = (8000, 11025, 22050, 44100)
		self.module._client.supported_sample_rates = (8000, 11025)
		self.assertEqual(
			self.module.supported_sample_rates(), (8000, 11025, 22050, 44100)
		)

	def test_knowing_nothing_offers_the_rate_every_engine_has(self):
		self.module._direct_sample_rates = ()
		self.module._client.supported_sample_rates = ()
		self.assertEqual(self.module.supported_sample_rates(), (engine.SAMPLE_RATE,))

	def test_an_unknown_rate_is_refused_rather_than_passed_to_an_engine(self):
		self.module._requested_sample_rate = 11025
		self.assertEqual(self.module.set_sample_rate(9999), 11025)
		self.assertEqual(self.module.requested_sample_rate(), 11025)

	def test_the_engine_work_is_queued_for_the_worker_not_done_inline(self):
		# Changing a rate replaces the engine's PCM output buffer, and this is
		# called from NVDA's thread while the worker may be inside synthesize().
		# Doing it inline would hand the engine a buffer it is writing into.
		self.module.process = lambda: None

		class _Loud(self.module.EngineClient):
			@property
			def started(self):
				return True

			def send_command(self, command, wait=True, **payload):
				raise AssertionError("the engine was touched on the calling thread")

		self.module._client = _Loud(self.module.AudioPipeline())
		self.module._direct_client = None
		self.module._active = None
		while not self.module.synth_queue.empty():
			self.module.synth_queue.get_nowait()

		self.assertEqual(self.module.set_sample_rate(22050), 22050)
		self.assertEqual(self.module.synth_queue.qsize(), 1)
		jobs, _seq = self.module.synth_queue.get_nowait()
		self.assertEqual([args for _func, args in jobs], [(22050,)])

	def test_the_queued_job_is_what_reconfigures_the_backends(self):
		self.module.process = lambda: None
		applied = []

		class _Recording(self.module.EngineClient):
			@property
			def started(self):
				return True

			def set_sample_rate(self, rate):
				applied.append(int(rate))
				self.sample_rate = int(rate)
				return self.sample_rate

		self.module._client = _Recording(self.module.AudioPipeline())
		self.module._direct_client = None
		self.module._active = None
		while not self.module.synth_queue.empty():
			self.module.synth_queue.get_nowait()

		self.module.set_sample_rate(16000)
		jobs, _seq = self.module.synth_queue.get_nowait()
		for func, args in jobs:
			func(*args)
		self.assertEqual(applied, [16000])

	def test_choosing_a_rate_updates_the_payload_later_backends_start_from(self):
		# Otherwise a backend woken by a language change would come up on the rate
		# the session began with.
		self.module._engine_initialize_payload = {"sampleRate": 11025, "language": "enu"}
		self.module._client.supported_sample_rates = (8000, 11025)
		self.module.set_sample_rate(8000)
		self.assertEqual(self.module._engine_initialize_payload["sampleRate"], 8000)


if __name__ == "__main__":
	unittest.main()
