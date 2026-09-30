"""The Synth Driver against the *real* _eloquence module.

Every other driver test substitutes a fake `_eloquence`, which is fast and
focused but cannot catch the driver calling something the real module does not
have.  Exactly that shipped once: `speak()` read `_eloquence._client._sequence`
after the Speech Generation counter had moved to the shared Audio Playback
Pipeline, and the fake happened to define the old private attribute, so 141
tests passed while NVDA died with

	AttributeError: 'EloquenceHostClient' object has no attribute '_sequence'

on the first utterance.  These tests wire the real module to the real driver with
only NVDA itself stubbed, so a mismatch fails here instead of at run time.
"""

import builtins
import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]


class _FakePlayer:
	def __init__(self, *args, **kwargs):
		pass

	def feed(self, data, onDone=None):
		if onDone:
			onDone()

	def sync(self):
		pass

	def idle(self):
		pass

	def stop(self):
		pass

	def pause(self, switch):
		pass

	def close(self):
		pass


class _Notification:
	def __init__(self):
		self.calls = []

	def notify(self, **kwargs):
		self.calls.append(kwargs)


class _SynthBase:
	"""Stands in for NVDA's SynthDriver base, as tests/test_language_scope.py does."""

	@staticmethod
	def VoiceSetting():
		return object()

	@staticmethod
	def VariantSetting():
		return object()

	@staticmethod
	def RateSetting():
		return object()

	@staticmethod
	def PitchSetting():
		return object()

	@staticmethod
	def InflectionSetting():
		return object()

	@staticmethod
	def VolumeSetting():
		return object()

	def __init__(self, *args, **kwargs):
		pass

	def _percentToParam(self, value, min_value, max_value):
		return int(min_value + (max_value - min_value) * value / 100)

	def _paramToPercent(self, value, min_value, max_value):
		return int((value - min_value) * 100 / (max_value - min_value))

	def initSettings(self):
		pass


def _install_nvda_stubs():
	config_module = types.ModuleType("config")
	config_module.conf = {
		"audio": {"outputDevice": "default"},
		"speech": {"outputDevice": "default", "eci": {}},
		"eloquence": {},
	}
	nvwave_module = types.ModuleType("nvwave")
	nvwave_module.WavePlayer = _FakePlayer
	build_version_module = types.ModuleType("buildVersion")
	build_version_module.version_year = 2026

	speech_commands = types.ModuleType("speech.commands")

	class _Command:
		def __init__(self, *args, **kwargs):
			for key, value in kwargs.items():
				setattr(self, key, value)

	class IndexCommand(_Command):
		def __init__(self, index):
			self.index = index

	class LangChangeCommand(_Command):
		def __init__(self, lang):
			self.lang = lang

	class BreakCommand(_Command):
		def __init__(self, time):
			self.time = time

	class CharacterModeCommand(_Command):
		def __init__(self, state):
			self.state = state

	class _Prosody(_Command):
		def __init__(self, multiplier=1, offset=0):
			self._multiplier = multiplier
			self._offset = offset
			self.multiplier = multiplier
			self.offset = offset

	class PitchCommand(_Prosody):
		pass

	class RateCommand(_Prosody):
		pass

	class VolumeCommand(_Prosody):
		pass

	class PhonemeCommand(_Command):
		def __init__(self, ipa, text=None):
			self.ipa = ipa
			self.text = text

	for module in (speech_commands,):
		module.IndexCommand = IndexCommand
		module.LangChangeCommand = LangChangeCommand
		module.BreakCommand = BreakCommand
		module.CharacterModeCommand = CharacterModeCommand
		module.PitchCommand = PitchCommand
		module.RateCommand = RateCommand
		module.VolumeCommand = VolumeCommand
		module.PhonemeCommand = PhonemeCommand

	speech_module = types.ModuleType("speech")
	speech_module.commands = speech_commands

	synth_driver_handler = types.ModuleType("synthDriverHandler")
	synth_driver_handler.SynthDriver = _SynthBase
	synth_driver_handler.synthIndexReached = _Notification()
	synth_driver_handler.synthDoneSpeaking = _Notification()
	synth_driver_handler.VoiceInfo = lambda *args, **kwargs: types.SimpleNamespace(
		id=args[0] if args else None, displayName=args[1] if len(args) > 1 else None
	)

	gui_module = types.ModuleType("gui")
	gui_module.settingsDialogs = types.SimpleNamespace(SettingsPanel=object)
	gui_module.guiHelper = types.SimpleNamespace()
	gui_module.messageBox = lambda *args, **kwargs: None

	driver_handler = types.ModuleType("driverHandler")
	driver_handler.NumericDriverSetting = lambda *args, **kwargs: object()
	driver_handler.BooleanDriverSetting = lambda *args, **kwargs: object()
	driver_handler.DriverSetting = lambda *args, **kwargs: object()

	addon_handler = types.ModuleType("addonHandler")
	addon_handler.initTranslation = lambda: None
	# NVDA's initTranslation() installs the gettext marker into builtins; the
	# driver uses _() at class-body time, so it has to exist before import.
	if not hasattr(builtins, "_"):
		builtins._ = lambda text: text

	stubs = {
		"config": config_module,
		"nvwave": nvwave_module,
		"buildVersion": build_version_module,
		"speech": speech_module,
		"speech.commands": speech_commands,
		"synthDriverHandler": synth_driver_handler,
		"gui": gui_module,
		"wx": types.ModuleType("wx"),
		"winsound": types.ModuleType("winsound"),
		"core": types.SimpleNamespace(postNvdaStartup=types.SimpleNamespace(register=lambda f: None)),
		"globalVars": types.SimpleNamespace(appArgs=types.SimpleNamespace(secure=False)),
		"driverHandler": driver_handler,
		"addonHandler": addon_handler,
		"logHandler": types.ModuleType("logHandler"),
	}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	return previous, config_module


class _RecordingBackend:
	"""Accepts Host Commands without an engine behind them."""

	def __init__(self, pipeline):
		self.pipeline = pipeline
		self.commands = []
		self.started = True

	def ensure_started(self):
		self.started = True

	def send_command(self, command, wait=True, **payload):
		self.commands.append((command, payload))
		return {"params": {}, "voiceParams": {}}

	def stop(self):
		self.pipeline.cancel()

	def shutdown(self):
		self.started = False


class DriverAgainstRealModuleTests(unittest.TestCase):
	def setUp(self):
		self._previous, self._config = _install_nvda_stubs()
		self.addCleanup(self._restore)
		for name in list(sys.modules):
			if name.startswith("addon.synthDrivers"):
				del sys.modules[name]
		self.eloquence = importlib.import_module("addon.synthDrivers._eloquence")
		self.driver_module = importlib.import_module("addon.synthDrivers.eloquence")
		# Real module, real driver, fake transport: the engine is what we do not
		# want here, not the wiring between the two Python modules.
		self.backend = _RecordingBackend(self.eloquence._pipeline)
		self.eloquence._client = self.backend
		self.eloquence._active = self.backend
		self.eloquence._direct_client = None
		self.eloquence.voice_params.update({1: 50, 2: 65, 3: 30, 4: 0, 5: 0, 6: 50, 7: 92})
		# Keep the synthesis worker from draining synth_queue, so the tests can
		# inspect exactly what speak() queued.  The worker itself is covered by
		# tests/test_backend_routing.py.
		self.eloquence.process = lambda: None

	def _restore(self):
		for name, module in self._previous.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module
		for name in list(sys.modules):
			if name.startswith("addon.synthDrivers"):
				del sys.modules[name]

	def _new_driver(self):
		driver = self.driver_module.SynthDriver.__new__(self.driver_module.SynthDriver)
		driver._pause_mode = 1
		driver._backquoteVoiceTags = False
		driver._ABRDICT = False
		driver._phrasePrediction = False
		driver._defaultVoice = "65536"
		driver.curvoice = "65536"
		driver._lastEngineVoice = "65536"
		driver._languageOverrideActive = False
		driver._variant = "1"
		driver.rate = 50
		return driver

	def test_speak_uses_only_public_module_api(self):
		# The regression this file exists for: speak() reached into
		# _eloquence._client._sequence, which stopped existing.
		driver = self._new_driver()
		driver.speak(["hello world"])
		queued = []
		while not self.eloquence.synth_queue.empty():
			queued.append(self.eloquence.synth_queue.get_nowait())
		self.assertEqual(len(queued), 1)
		_outlist, seq = queued[0]
		self.assertEqual(seq, self.eloquence.current_generation())

	def test_speak_with_an_index_and_a_language_change_does_not_raise(self):
		driver = self._new_driver()
		commands = self.driver_module
		driver.speak(
			[
				commands.LangChangeCommand("en-US"),
				"english",
				commands.IndexCommand(7),
				commands.LangChangeCommand("de-DE"),
				"deutsch",
				commands.BreakCommand(100),
			]
		)
		self.assertFalse(self.eloquence.synth_queue.empty())

	def test_the_queued_operations_are_all_callable_module_attributes(self):
		# Each queued entry is (callable, args) executed later on the synthesis
		# worker.  A stale reference here would fail only at speech time.
		driver = self._new_driver()
		driver.speak(["hello", self.driver_module.IndexCommand(1)])
		outlist, _seq = self.eloquence.synth_queue.get_nowait()
		for func, args in outlist:
			with self.subTest(func=getattr(func, "__name__", func)):
				self.assertTrue(callable(func))
				self.assertIsInstance(args, tuple)

	def test_running_the_queued_operations_reaches_the_backend(self):
		# Actually execute what speak() queued, which is what the synthesis
		# worker does, so a bad call signature cannot hide behind a lambda.
		driver = self._new_driver()
		driver.speak(["hello world"])
		outlist, _seq = self.eloquence.synth_queue.get_nowait()
		for func, args in outlist:
			func(*args)
		self.assertIn("addText", [command for command, _payload in self.backend.commands])
		self.assertIn("synthesize", [command for command, _payload in self.backend.commands])

	def test_cancelling_advances_the_generation_the_driver_reads(self):
		before = self.eloquence.current_generation()
		self.eloquence.stop()
		self.assertGreater(self.eloquence.current_generation(), before)

	def test_pause_reaches_the_shared_pipeline(self):
		self.eloquence._pipeline.player = _FakePlayer()
		self.eloquence.pause(True)  # must not raise


if __name__ == "__main__":
	unittest.main()
