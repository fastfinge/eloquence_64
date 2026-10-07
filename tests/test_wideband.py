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
	"""Records every eciSetParam; refuses the wideband parameter if told to."""

	def __init__(self, knows_wideband=True):
		self.knows_wideband = knows_wideband
		self.set_params = []

	def eciSetParam(self, handle, param, value):
		self.set_params.append((param, value))
		if param == engine.ECI_WIDEBAND and not self.knows_wideband:
			return -1
		return 0

	def eciNewEx(self, language):
		return 1

	def __getattr__(self, name):
		def anything(*args):
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
