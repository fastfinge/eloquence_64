"""Eloquence Contours in the Audio Playback Pipeline.

The DSP comes from the eloquence-contours submodule, built by build_contours.cmd.
Its README sets the contract for applying it, and these tests hold the add-on to
it: process once, in the one playback worker, after stale audio is discarded;
keep each chunk's Speech Index; reset on a new Speech Generation; and treat a
RateChange as the *engine* rate, opening the player at the contour's output rate.
"""

import importlib.util
import struct
import sys
import time
import types
import unittest
from pathlib import Path

from addon.synthDrivers import _contours


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
	module_name = "addon.synthDrivers._eloquence_contour_test"
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


class _Player:
	opened = []

	def __init__(self, channels, rate, bits, **kwargs):
		self.rate = rate
		self.fed = []
		self.closed = False
		_Player.opened.append(self)

	def feed(self, data, onDone=None):
		self.fed.append(data)
		if onDone:
			onDone()

	def sync(self):
		pass

	def idle(self):
		pass

	def stop(self):
		pass

	def close(self):
		self.closed = True


class _FakeContour:
	"""Doubles every byte, as an upsampler doubles every sample."""

	made = []

	def __init__(self, rate, setting):
		self.rate = rate
		self.setting = setting
		self.output_rate = rate * 2
		self.processed = []
		self.resets = 0
		self.closed = False
		_FakeContour.made.append(self)

	def process(self, pcm):
		self.processed.append(pcm)
		return bytes(b for b in pcm for _ in range(2))

	def reset(self):
		self.resets += 1

	def close(self):
		self.closed = True


def _wait_for(condition, timeout=5):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline and not condition():
		time.sleep(0.01)
	return condition()


class ModeMappingTests(unittest.TestCase):
	def test_presence_has_a_mode_at_both_supported_rates(self):
		self.assertEqual(_contours.mode_for("presence", 11025), 1)
		self.assertEqual(_contours.mode_for("presence", 16000), 2)

	def test_each_rate_specific_contour_exists_only_at_its_rate(self):
		self.assertEqual(_contours.mode_for("treble", 11025), 3)
		self.assertIsNone(_contours.mode_for("treble", 16000))
		self.assertEqual(_contours.mode_for("smooth", 16000), 1)
		self.assertIsNone(_contours.mode_for("smooth", 11025))

	def test_other_rates_play_raw_rather_than_being_reinterpreted(self):
		# Upstream is explicit that 44100 Hz PCM must not be fed as 16000 Hz.
		for setting in _contours.MODES:
			for rate in (8000, 22050, 44100, 48000):
				with self.subTest(setting=setting, rate=rate):
					self.assertIsNone(_contours.mode_for(setting, rate))
					self.assertIsNone(_contours.create(setting, rate))

	def test_raw_never_needs_the_dll(self):
		self.assertIsNone(_contours.create("raw", 11025))
		self.assertIsNone(_contours.create("raw", 16000))


class PipelineContourTests(unittest.TestCase):
	def setUp(self):
		self.module = _load_client_module()
		self.module.nvwave.WavePlayer = _Player
		_Player.opened = []
		_FakeContour.made = []
		self.pipeline = self.module.AudioPipeline()
		self.pipeline.make_contour = lambda rate, setting: (
			None if setting == "raw" else _FakeContour(rate, setting)
		)

	def tearDown(self):
		self.pipeline.close_audio()

	def _speak(self, *chunks):
		for data, index in chunks:
			self.pipeline.handle_event("audio", {"data": data, "index": index, "final": False})
		self.pipeline.handle_event("audio", {"data": b"", "index": None, "final": True})

	def test_raw_is_the_default_and_leaves_audio_untouched(self):
		self.pipeline.initialize_audio(11025)
		self._speak((b"ab", None))
		self.assertTrue(_wait_for(lambda: _Player.opened[0].fed))
		self.assertEqual(_Player.opened[0].fed, [b"ab"])
		self.assertEqual(_FakeContour.made, [])

	def test_the_device_opens_at_the_contour_output_rate(self):
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self.assertEqual([player.rate for player in _Player.opened], [22050])
		self.assertEqual(_FakeContour.made[0].rate, 11025)

	def test_audio_is_shaped_once_and_index_only_events_are_not(self):
		indexes = []
		self.module.onIndexReached = indexes.append
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self._speak((b"ab", None), (b"", 7))
		self.assertTrue(_wait_for(lambda: None in indexes))
		contour = _FakeContour.made[0]
		self.assertEqual(contour.processed, [b"ab"])
		self.assertEqual(_Player.opened[0].fed, [b"aabb"])
		# The index still rides on the audio that preceded it.
		self.assertEqual(indexes, [7, None])

	def test_a_new_speech_generation_resets_the_contour(self):
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self._speak((b"a", None))
		self.assertTrue(_wait_for(lambda: _Player.opened[0].fed))
		contour = _FakeContour.made[0]
		self.assertEqual(contour.resets, 1)
		self.pipeline.cancel()
		self.pipeline.current_seq = self.pipeline.sequence
		self._speak((b"b", None))
		self.assertTrue(_wait_for(lambda: len(contour.processed) == 2))
		self.assertEqual(contour.resets, 2)
		self.assertIs(_FakeContour.made[-1], contour)

	def test_a_rate_change_is_the_engine_rate_and_recreates_the_contour(self):
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self.pipeline.request_rate(16000)
		self.assertTrue(_wait_for(lambda: len(_Player.opened) == 2))
		first, second = _FakeContour.made
		self.assertEqual((first.rate, second.rate), (11025, 16000))
		self.assertTrue(first.closed)
		self.assertEqual(_Player.opened[1].rate, 32000)

	def test_queued_audio_keeps_the_contour_it_was_produced_under(self):
		self.pipeline.initialize_audio(11025)
		self.pipeline.handle_event("audio", {"data": b"x", "index": None, "final": False})
		self.pipeline.request_contour("presence")
		self._speak((b"y", None))
		self.assertTrue(_wait_for(lambda: len(_Player.opened) == 2 and _Player.opened[1].fed))
		self.assertEqual(_Player.opened[0].fed, [b"x"])
		self.assertEqual(_Player.opened[1].fed, [b"yy"])

	def test_turning_a_contour_off_reopens_at_the_engine_rate(self):
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self.pipeline.request_contour("raw")
		self.assertTrue(_wait_for(lambda: len(_Player.opened) == 2))
		self.assertEqual(_Player.opened[1].rate, 11025)
		self.assertTrue(_FakeContour.made[0].closed)

	def test_a_failing_contour_falls_back_to_raw_audio_at_the_engine_rate(self):
		def broken(pcm):
			raise RuntimeError("boom")

		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		_FakeContour.made[0].process = broken
		self._speak((b"ab", None))
		self.assertTrue(_wait_for(lambda: len(_Player.opened) == 2 and _Player.opened[1].fed))
		self.assertEqual(_Player.opened[1].rate, 11025)
		self.assertEqual(_Player.opened[1].fed, [b"ab"])

	def test_the_contour_is_closed_when_the_worker_exits(self):
		self.pipeline.request_contour("presence")
		self.pipeline.initialize_audio(11025)
		self.pipeline.close_audio()
		self.assertTrue(_FakeContour.made[0].closed)


class BuiltContourTests(unittest.TestCase):
	"""Against the real DLL, which build_contours.cmd may not have built yet."""

	def setUp(self):
		if not _contours.available():
			self.skipTest("Eloquence Contours not built; run `build_contours.cmd`")

	def test_the_bundled_dll_is_64_bit(self):
		from fetch_eci import _is_pe32_plus

		self.assertTrue(_is_pe32_plus(_contours.dll_path()))

	def _require_64_bit_python(self):
		# The suite's own venv is 32-bit (it shares the host's interpreter), and a
		# 32-bit process cannot load the 64-bit DLL NVDA does.
		if struct.calcsize("P") != 8:
			self.skipTest("loading the 64-bit contour DLL needs 64-bit Python")

	def test_every_offered_contour_can_be_created_where_it_has_a_mode(self):
		self._require_64_bit_python()
		for setting, modes in _contours.MODES.items():
			for rate in modes:
				with self.subTest(setting=setting, rate=rate):
					contour = _contours.create(setting, rate)
					self.assertIsNotNone(contour)
					try:
						out = contour.process(b"\x00\x10" * 100)
						expected = 2 if contour.output_rate == rate * 2 else 1
						self.assertEqual(len(out), 200 * expected)
					finally:
						contour.close()

	def test_classic_contours_upsample_and_native_ones_do_not(self):
		self._require_64_bit_python()
		for setting, rate, output in (("presence", 11025, 22050), ("treble", 11025, 22050),
				("presence", 16000, 16000), ("smooth", 16000, 16000)):
			with self.subTest(setting=setting, rate=rate):
				contour = _contours.create(setting, rate)
				try:
					self.assertEqual(contour.output_rate, output)
				finally:
					contour.close()


if __name__ == "__main__":
	unittest.main()
