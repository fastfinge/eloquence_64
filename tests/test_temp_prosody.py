"""Regression tests for issue #130: temporary prosody must survive a voice switch.

set_voice() restores the user's base voice params after eciSetParam(9); before
the fix this silently cancelled a caps pitch raise applied just before the
language change, so capitals in auto-switched languages spoke at normal pitch.
"""

import importlib.util
import os
import sys
import types
import unittest


def _install_nvda_stubs():
	config = types.ModuleType("config")
	config.conf = {"speech": {"eci": {}}, "audio": {"outputDevice": "default"}}
	sys.modules["config"] = config

	nvwave = types.ModuleType("nvwave")
	nvwave.WavePlayer = type("WavePlayer", (), {"MIN_BUFFER_MS": 0})
	sys.modules["nvwave"] = nvwave

	build_version = types.ModuleType("buildVersion")
	build_version.version_year = 2026
	sys.modules["buildVersion"] = build_version


def _import_real_eloquence():
	_install_nvda_stubs()
	# Load the real module under a private name so we never disturb the
	# "addon.synthDrivers._eloquence" entry other test modules stub out
	# (test_language_scope replaces it with a fake).
	path = os.path.abspath(
		os.path.join(os.path.dirname(__file__), "..", "addon", "synthDrivers", "_eloquence.py")
	)
	spec = importlib.util.spec_from_file_location("addon.synthDrivers._eloquence_real_i130", path)
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


_eloquence = _import_real_eloquence()


class _FakeClient:
	"""Records every command that would go to the Eloquence Host Process."""

	def __init__(self):
		self.commands = []
		self._sequence = 0
		self._player = None

	def send_command(self, command, wait=True, **payload):
		self.commands.append((command, payload))
		return {"params": {}, "voiceParams": {}}

	def stop(self):
		self._sequence += 1


BASE_PITCH = 65
PITCH = _eloquence.pitch
DEU = 262144


class TempProsodyAcrossVoiceSwitchTests(unittest.TestCase):
	def setUp(self):
		self.client = _FakeClient()
		_eloquence._client = self.client
		_eloquence._active_temp_prosody.clear()
		_eloquence.voice_params.clear()
		_eloquence.voice_params.update(
			{
				_eloquence.rate: 50,
				PITCH: BASE_PITCH,
				_eloquence.vlm: 92,
				_eloquence.fluctuation: 30,
				_eloquence.hsz: 50,
				_eloquence.rgh: 0,
				_eloquence.bth: 0,
			}
		)

	def _pitch_values_sent(self):
		return [
			(payload["value"], payload.get("temporary", False))
			for command, payload in self.client.commands
			if command == "setVoiceParam" and payload["paramId"] == PITCH
		]

	def test_caps_pitch_survives_language_change(self):
		# The order a caps pitch raise and a language change can reach the
		# synthesis worker in: PitchCommand(offset=30), then LangChangeCommand.
		_eloquence.cmdProsody(PITCH, 1, 30)
		_eloquence.set_voice(DEU)
		values = self._pitch_values_sent()
		self.assertEqual(
			values[-1],
			(BASE_PITCH + 30, True),
			"temporary caps pitch must be re-applied after the language "
			"change, not stomped by the base-param restore: %r" % values,
		)

	def test_no_reapply_after_prosody_reset(self):
		_eloquence.cmdProsody(PITCH, 1, 30)
		_eloquence.cmdProsody(PITCH, 1, 0)  # PitchCommand() revert
		self.client.commands.clear()
		_eloquence.set_voice(DEU)
		values = self._pitch_values_sent()
		self.assertEqual(
			values[-1],
			(BASE_PITCH, False),
			"after a prosody revert the language change must leave pitch at base: %r" % values,
		)

	def test_stop_clears_pending_prosody(self):
		_eloquence.cmdProsody(PITCH, 1, 30)
		_eloquence.stop()
		self.client.commands.clear()
		_eloquence.set_voice(DEU)
		values = self._pitch_values_sent()
		self.assertEqual(
			values[-1],
			(BASE_PITCH, False),
			"cancelled speech must not leak its temporary pitch into the next voice switch: %r" % values,
		)

	def test_reapplied_pitch_clamped_to_param_max(self):
		_eloquence.voice_params[PITCH] = 90
		_eloquence.cmdProsody(PITCH, 1, 30)
		_eloquence.set_voice(DEU)
		self.assertEqual(self._pitch_values_sent()[-1], (100, True))


if __name__ == "__main__":
	unittest.main()
