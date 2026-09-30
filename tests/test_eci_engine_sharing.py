"""The properties that let one engine module serve two bitnesses.

``_eci_engine`` is imported both by the Synth Driver side inside 64-bit NVDA and
by the 32-bit Eloquence Host Process, where PyInstaller has frozen a copy of it
into the executable.  Two things make that work, and both are easy to break
without noticing until the host fails to launch, so they are asserted here
rather than left to a comment:

* it imports no NVDA module, because the Eloquence Host Process has none;
* it uses no relative imports, because the host imports it as a top-level module
  while the add-on imports it as part of a package.
"""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

_ENGINE_PATH = Path(__file__).parents[1] / "addon" / "synthDrivers" / "_eci_engine.py"

# Modules that only exist inside NVDA.  The host process would die importing any.
_NVDA_ONLY = {
	"nvwave",
	"config",
	"gui",
	"globalVars",
	"synthDriverHandler",
	"speech",
	"logHandler",
	"addonHandler",
	"core",
	"buildVersion",
	"driverHandler",
	"autoSettingsUtils",
	"languageHandler",
	"characterProcessing",
}


def _load_engine_standalone():
	"""Import the engine the way the frozen host does: as a top-level module."""
	spec = importlib.util.spec_from_file_location("_eci_engine_standalone", _ENGINE_PATH)
	module = importlib.util.module_from_spec(spec)
	sys.modules["_eci_engine_standalone"] = module
	try:
		spec.loader.exec_module(module)
		return module
	finally:
		sys.modules.pop("_eci_engine_standalone", None)


class EngineImportIsolationTests(unittest.TestCase):
	def test_the_engine_imports_with_no_package_and_no_nvda(self):
		# If this fails, the Eloquence Host Process will not start.
		module = _load_engine_standalone()
		self.assertTrue(hasattr(module, "EciEngine"))
		self.assertTrue(hasattr(module, "EciDispatcher"))

	def test_the_engine_pulls_in_no_nvda_module(self):
		before = set(sys.modules)
		_load_engine_standalone()
		newly_imported = {name.split(".")[0] for name in set(sys.modules) - before}
		self.assertEqual(newly_imported & _NVDA_ONLY, set())

	def test_the_engine_uses_no_relative_imports(self):
		# A relative import would resolve on the add-on side and fail in the
		# frozen host, where this is a top-level module.
		tree = ast.parse(_ENGINE_PATH.read_text(encoding="utf-8"))
		relative = [
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
		]
		self.assertEqual(relative, [], "relative imports break the frozen host")

	def test_the_engine_imports_only_the_standard_library(self):
		tree = ast.parse(_ENGINE_PATH.read_text(encoding="utf-8"))
		roots = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				roots.update(alias.name.split(".")[0] for alias in node.names)
			elif isinstance(node, ast.ImportFrom) and node.module:
				roots.add(node.module.split(".")[0])
		self.assertTrue(roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names))


class EngineContractTests(unittest.TestCase):
	"""The pieces both backends rely on."""

	def setUp(self):
		self.engine = _load_engine_standalone()

	def test_the_pcm_format_is_stated_once(self):
		# The Audio Playback Pipeline reads these rather than keeping its own
		# copy, which is what stops the two disagreeing.
		self.assertEqual(self.engine.SAMPLE_RATE, 11025)
		self.assertEqual(self.engine.CHANNELS, 1)
		self.assertEqual(self.engine.BITS_PER_SAMPLE, 16)

	def test_every_language_id_round_trips(self):
		for code, voice_id in self.engine.LANGS.items():
			with self.subTest(code=code):
				self.assertEqual(self.engine.LANG_BY_ID[voice_id], code)

	def test_the_dispatcher_knows_the_host_command_protocol(self):
		dispatcher = self.engine.EciDispatcher(lambda *a, **k: None)
		for command in (
			"initialize",
			"addText",
			"insertIndex",
			"synthesize",
			"stop",
			"delete",
			"setParam",
			"setVoiceParam",
			"copyVoice",
		):
			with self.subTest(command=command):
				self.assertTrue(dispatcher.knows(command))
		self.assertFalse(dispatcher.knows("nonsense"))

	def test_an_unknown_command_raises_rather_than_passing_silently(self):
		dispatcher = self.engine.EciDispatcher(lambda *a, **k: None)
		with self.assertRaises(KeyError):
			dispatcher.handle("nonsense", {})

	def test_available_languages_is_empty_for_a_library_that_cannot_be_loaded(self):
		# Callers treat an empty set as "use the Eloquence Host Process for
		# everything", so a load failure must degrade rather than raise.
		self.assertEqual(self.engine.available_languages("C:\\nope\\eci.dll"), frozenset())

	def test_available_languages_is_empty_for_a_library_without_the_export(self):
		self.assertEqual(
			self.engine.available_languages("C:\\Windows\\System32\\kernel32.dll"), frozenset()
		)

	def test_the_config_can_opt_out_of_ini_rewriting(self):
		# openevv ships an eci.ini that needs no C:\dummy\ substitution, unlike
		# the proprietary ECI.INI.
		config = self.engine.EngineConfig(
			eci_path="x",
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
			rewrite_ini=False,
		)
		self.assertFalse(config.rewrite_ini)
		# ...but the proprietary path still gets it by default.
		self.assertTrue(
			self.engine.EngineConfig(
				eci_path="x",
				data_directory="",
				language_code="enu",
				enable_abbrev_dict=False,
				enable_phrase_prediction=False,
				voice_variant=0,
			).rewrite_ini
		)


if __name__ == "__main__":
	unittest.main()
