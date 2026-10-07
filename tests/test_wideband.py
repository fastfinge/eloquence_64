"""openevv's eciWideband, which the Direct Backend always turns on.

Above 11025 Hz openevv still synthesises at 11025 and resamples, so with the
parameter off every higher rate is the 11025 Hz voice upsampled.  Measured
2026-10-06 against openevv main@c3a253fb, energy above 6 kHz relative to the
whole utterance:

	rate     off       on
	11025    none      none
	22050    -84.6 dB  -27.7 dB
	44100    -82.3 dB  -27.7 dB
	48000    -82.1 dB  -27.7 dB

with identical sample counts either way.  It survives changes to the sample
rate, the language, and the voice.  The proprietary engine has no such
parameter, so it is set only when EngineConfig.wideband asks for it.
"""

import ctypes
import unittest
from unittest import mock

from addon.synthDrivers import _eci_engine as engine


class _Dll:
	"""Records every call in order; refuses the wideband parameter if told to."""

	def __init__(self, knows_wideband=True):
		self.knows_wideband = knows_wideband
		self.set_params = []
		self.calls = []

	def eciSetParam(self, handle, param, value):
		self.calls.append(("eciSetParam", param))
		self.set_params.append((param, value))
		if param == engine.ECI_WIDEBAND and not self.knows_wideband:
			return -1
		return 0

	def eciNewEx(self, language):
		return 1

	def __getattr__(self, name):
		def anything(*args):
			self.calls.append((name,))
			return 1

		return anything


def _start(wideband, knows_wideband=True):
	config = engine.EngineConfig(
		eci_path="eci.dll",
		data_directory="",
		language_code="enu",
		enable_abbrev_dict=False,
		enable_phrase_prediction=False,
		voice_variant=0,
		rewrite_ini=False,
		wideband=wideband,
	)
	instance = engine.EciEngine(lambda event, **payload: None, config)
	dll = _Dll(knows_wideband)
	with mock.patch.object(ctypes.windll, "LoadLibrary", return_value=dll), mock.patch.object(
		engine.EciEngine, "_declare_signatures"
	), mock.patch.object(engine.EciEngine, "_load_dictionaries"):
		instance.start()
	return instance, dll


class EngineTests(unittest.TestCase):
	def test_it_is_turned_on_when_asked_for(self):
		instance, dll = _start(wideband=True)
		self.assertIn((engine.ECI_WIDEBAND, 1), dll.set_params)
		self.assertEqual(instance.get_state()["params"].get(engine.ECI_WIDEBAND), 1)

	def test_it_is_never_touched_otherwise(self):
		# The proprietary engine has no parameter 32, and nothing it does not
		# have should be offered to it.
		_instance, dll = _start(wideband=False)
		self.assertNotIn(engine.ECI_WIDEBAND, [param for param, _value in dll.set_params])

	def test_it_is_off_by_default(self):
		config = engine.EngineConfig(
			eci_path="",
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
		)
		self.assertFalse(config.wideband)

	def test_an_engine_that_refuses_it_still_starts(self):
		# openevv v0.4, the newest release, predates the parameter.
		instance, _dll = _start(wideband=True, knows_wideband=False)
		self.assertNotIn(engine.ECI_WIDEBAND, instance.get_state()["params"])


class PrimingTests(unittest.TestCase):
	"""Once parameter 32 is set, openevv drops every sample-rate change and refuses
	every eciSetOutputBuffer until it has synthesised once.  NVDA applies the
	configured rate before speaking anything, so the engine is primed with a
	textless synthesis straight after.  Measured 2026-10-06 on main@c3a253fb,
	samples for one utterance at 11025 = 13002:

		wideband, rate 44100, speak             13002  (rate dropped)
		wideband, synth no text, rate 44100     52008
		rate 44100, wideband, speak             52008
	"""

	def test_the_engine_synthesises_once_straight_after_wideband(self):
		_instance, dll = _start(wideband=True)
		after = dll.calls[dll.calls.index(("eciSetParam", engine.ECI_WIDEBAND)) :]
		self.assertEqual(after[1:3], [("eciSynthesize",), ("eciSynchronize",)])

	def test_wideband_comes_after_the_output_buffer(self):
		_instance, dll = _start(wideband=True)
		self.assertLess(
			dll.calls.index(("eciSetOutputBuffer",)),
			dll.calls.index(("eciSetParam", engine.ECI_WIDEBAND)),
		)

	def test_nothing_is_synthesised_without_wideband(self):
		_instance, dll = _start(wideband=False)
		self.assertNotIn(("eciSynthesize",), dll.calls)

	def test_nothing_is_synthesised_when_the_engine_refuses_it(self):
		_instance, dll = _start(wideband=True, knows_wideband=False)
		self.assertNotIn(("eciSynthesize",), dll.calls)


class RefusedBufferTests(unittest.TestCase):
	"""An engine that refuses a new output buffer keeps writing into its old one,
	so that one must stay alive.  Dropping it freed memory openevv was still
	filling, and NVDA crashed inside nvwave a moment later."""

	def _engine(self, accept):
		instance, dll = _start(wideband=False)
		instance._supported_sample_rates = {11025: 1, 44100: 5}
		dll.eciSetOutputBuffer = lambda handle, samples, buffer: int(accept)
		return instance

	def test_a_refused_buffer_leaves_the_old_one_in_place(self):
		instance = self._engine(accept=False)
		buffer, samples = instance._buffer, instance._samples
		with self.assertLogs(engine.LOGGER, "WARNING"):
			self.assertEqual(instance.set_sample_rate(44100), 44100)
		self.assertIs(instance._buffer, buffer)
		self.assertEqual(instance._samples, samples)

	def test_an_accepted_buffer_replaces_it(self):
		instance = self._engine(accept=True)
		buffer = instance._buffer
		instance.set_sample_rate(44100)
		self.assertIsNot(instance._buffer, buffer)
		self.assertEqual(instance._samples, engine.output_buffer_samples(44100))


class DispatcherTests(unittest.TestCase):
	def _initialize(self, payload):
		captured = {}

		class _Probe(engine.EciEngine):
			def start(self):
				captured["wideband"] = self._config.wideband

		with mock.patch.object(engine, "EciEngine", _Probe):
			engine.EciDispatcher(lambda *a, **k: None).handle(
				"initialize",
				{"eciPath": "", "dataDirectory": "", "language": "enu", **payload},
			)
		return captured["wideband"]

	def test_the_initialize_payload_carries_it(self):
		self.assertTrue(self._initialize({"wideband": True}))

	def test_omitting_it_leaves_it_off(self):
		self.assertFalse(self._initialize({}))


if __name__ == "__main__":
	unittest.main()
