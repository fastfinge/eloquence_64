import builtins
import importlib
import sys
import types
import unittest


class _Command:
	def __init__(self, **kwargs):
		self.__dict__.update(kwargs)


class IndexCommand(_Command):
	def __init__(self, index):
		super().__init__(index=index)


class LangChangeCommand(_Command):
	def __init__(self, lang):
		super().__init__(lang=lang)


class BreakCommand(_Command):
	def __init__(self, time):
		super().__init__(time=time)


class CharacterModeCommand(_Command):
	pass


class PitchCommand(_Command):
	pass


class RateCommand(_Command):
	pass


class VolumeCommand(_Command):
	pass


class PhonemeCommand(_Command):
	pass


class _Notification:
	def __init__(self):
		self.calls = []

	def notify(self, **kwargs):
		self.calls.append(kwargs)


class _SynthBase:
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

	def _percentToParam(self, value, min_value, max_value):
		return int(min_value + (max_value - min_value) * value / 100)

	def _paramToPercent(self, value, min_value, max_value):
		return int((value - min_value) * 100 / (max_value - min_value))


class _SpeechQueue:
	def __init__(self):
		self.items = []

	def put(self, item):
		self.items.append(item)


class _EloquenceStub(types.ModuleType):
	def __init__(self):
		super().__init__("addon.synthDrivers._eloquence")
		self.params = {9: 262144}
		self.voice_params = {7: 80}
		self.lastindex = None
		self.hsz = 1
		self.pitch = 2
		self.fluctuation = 3
		self.rgh = 4
		self.bth = 5
		self.rate = 6
		self.vlm = 7
		self.eciPath = "C:\\eci.dll"
		self.langs = {
			"enu": (65536, "American English"),
			"eng": (65537, "British English"),
			"deu": (262144, "German"),
			"esp": (131072, "Castilian Spanish"),
			"esm": (131073, "Latin American Spanish"),
			"fra": (196608, "French"),
			"frc": (196609, "French Canadian"),
			"ptb": (458752, "Brazilian Portuguese"),
			"fin": (589824, "Finnish"),
			"ita": (327680, "Italian"),
			"chs": (393216, "Mandarin Chinese"),
			"jpn": (524288, "Japanese"),
			"kor": (655360, "Korean"),
		}
		self.synth_queue = _SpeechQueue()
		self._client = types.SimpleNamespace(_sequence=1)
		self.stopped = False
		self.processed = False
		self.immediate_calls = []

	def cmdProsody(self, *args):
		self.immediate_calls.append(("cmdProsody", args))

	def set_voice(self, voice_id):
		self.params[9] = int(voice_id)
		self.immediate_calls.append(("set_voice", (int(voice_id),)))

	def speak(self, text):
		self.immediate_calls.append(("speak", (text,)))

	def index(self, index):
		self.immediate_calls.append(("index", (index,)))

	def synth(self):
		self.immediate_calls.append(("synth", ()))

	def process(self):
		self.processed = True

	def stop(self):
		self.stopped = True

	def getVParam(self, param):
		return self.voice_params.get(param, 0)


def _install_nvda_stubs():
	builtins._ = lambda text: text
	speech = types.ModuleType("speech")
	speech.IndexCommand = IndexCommand
	speech.CharacterModeCommand = CharacterModeCommand
	speech.LangChangeCommand = LangChangeCommand
	speech.BreakCommand = BreakCommand
	speech.PitchCommand = PitchCommand
	speech.RateCommand = RateCommand
	speech.VolumeCommand = VolumeCommand
	speech.PhonemeCommand = PhonemeCommand
	sys.modules["speech"] = speech

	driver_handler = types.ModuleType("driverHandler")
	driver_handler.NumericDriverSetting = lambda *args, **kwargs: object()
	driver_handler.BooleanDriverSetting = lambda *args, **kwargs: object()
	driver_handler.DriverSetting = lambda *args, **kwargs: object()
	sys.modules["driverHandler"] = driver_handler

	synth_driver_handler = types.ModuleType("synthDriverHandler")
	synth_driver_handler.SynthDriver = _SynthBase
	synth_driver_handler.synthIndexReached = _Notification()
	synth_driver_handler.synthDoneSpeaking = _Notification()
	synth_driver_handler.VoiceInfo = lambda *args, **kwargs: types.SimpleNamespace(
		id=args[0] if args else None,
		name=args[1] if len(args) > 1 else None,
		language=args[2] if len(args) > 2 else None,
	)
	sys.modules["synthDriverHandler"] = synth_driver_handler

	gui = types.ModuleType("gui")
	gui.settingsDialogs = types.SimpleNamespace(SettingsPanel=object)
	gui.guiHelper = types.SimpleNamespace()
	gui.messageBox = lambda *args, **kwargs: None
	sys.modules["gui"] = gui
	sys.modules["wx"] = types.ModuleType("wx")
	sys.modules["winsound"] = types.ModuleType("winsound")
	sys.modules["config"] = types.SimpleNamespace(conf={})
	sys.modules["core"] = types.SimpleNamespace(
		postNvdaStartup=types.SimpleNamespace(register=lambda f: None)
	)
	sys.modules["globalVars"] = types.SimpleNamespace(appArgs=types.SimpleNamespace(secure=False))
	sys.modules["addonHandler"] = types.SimpleNamespace(initTranslation=lambda: None)


def _load_driver():
	_install_nvda_stubs()
	eloquence_stub = _EloquenceStub()
	sys.modules["addon.synthDrivers._eloquence"] = eloquence_stub
	sys.modules.pop("addon.synthDrivers.eloquence", None)
	module = importlib.import_module("addon.synthDrivers.eloquence")
	preprocess_calls = []

	def preprocess(text, voice_id):
		preprocess_calls.append((text, voice_id))
		return f"{voice_id}:{text}"

	module._text_preprocessing.preprocess = preprocess
	return module, eloquence_stub, preprocess_calls


def _new_driver(module):
	driver = module.SynthDriver.__new__(module.SynthDriver)
	driver._defaultVoice = "262144"
	driver.curvoice = "262144"
	driver._lastEngineVoice = "262144"
	driver._languageOverrideActive = False
	driver._pause_mode = 1
	driver._backquoteVoiceTags = False
	driver._ABRDICT = False
	driver._phrasePrediction = False
	return driver


def _queued_calls(eloquence_stub):
	return eloquence_stub.synth_queue.items[-1][0]


def _queued_voice_ids(calls, eloquence_stub):
	return [
		args[0]
		for func, args in calls
		if getattr(func, "__self__", None) is eloquence_stub and getattr(func, "__name__", "") == "set_voice"
	]


def _queued_text(calls, eloquence_stub):
	return [
		args[0]
		for func, args in calls
		if getattr(func, "__self__", None) is eloquence_stub and getattr(func, "__name__", "") == "speak"
	]


class LanguageScopeTests(unittest.TestCase):
	def test_english_document_language_does_not_replace_default_voice(self):
		module, eloquence_stub, preprocess_calls = _load_driver()
		driver = _new_driver(module)

		driver.speak([LangChangeCommand("en-US"), "Collapse tree"])

		calls = _queued_calls(eloquence_stub)
		self.assertEqual(_queued_voice_ids(calls, eloquence_stub), [65536])
		self.assertEqual(preprocess_calls, [("Collapse tree", 65536)])
		self.assertEqual(driver._get_voice(), "262144")
		self.assertEqual(driver.curvoice, "262144")

	def test_cancel_does_not_suppress_next_english_language_command(self):
		module, eloquence_stub, preprocess_calls = _load_driver()
		driver = _new_driver(module)

		driver.speak([LangChangeCommand("en-US"), "Repository"])
		driver.cancel()
		driver.speak([LangChangeCommand("en-US"), "Collapse tree"])

		second_calls = eloquence_stub.synth_queue.items[-1][0]
		self.assertEqual(_queued_voice_ids(second_calls, eloquence_stub), [262144, 65536])
		self.assertEqual(preprocess_calls[-1], ("Collapse tree", 65536))
		self.assertEqual(driver._get_voice(), "262144")

	def test_german_ui_without_language_command_uses_default_after_english_content(self):
		module, eloquence_stub, preprocess_calls = _load_driver()
		driver = _new_driver(module)

		driver.speak([LangChangeCommand("en-US"), "Repository"])
		driver.speak(["Desktop"])

		second_calls = eloquence_stub.synth_queue.items[-1][0]
		self.assertEqual(_queued_voice_ids(second_calls, eloquence_stub), [262144])
		self.assertEqual(preprocess_calls[-1], ("Desktop", 262144))
		self.assertEqual(driver._get_voice(), "262144")

	def test_language_reset_uses_default_for_say_all_structural_text(self):
		module, eloquence_stub, preprocess_calls = _load_driver()
		driver = _new_driver(module)

		driver.speak([LangChangeCommand("en-US"), "README", LangChangeCommand(None), "list end"])

		calls = _queued_calls(eloquence_stub)
		self.assertEqual(_queued_voice_ids(calls, eloquence_stub), [65536, 262144])
		self.assertEqual(
			preprocess_calls,
			[("README", 65536), ("list end", 262144)],
		)
		self.assertEqual(driver._get_voice(), "262144")

	def test_index_only_sequence_still_notifies_progress_and_done(self):
		module, eloquence_stub, _preprocess_calls = _load_driver()
		driver = _new_driver(module)

		driver.speak([IndexCommand(42)])

		self.assertEqual(module.synthIndexReached.calls, [{"synth": driver, "index": 42}])
		self.assertEqual(module.synthDoneSpeaking.calls, [{"synth": driver}])
		self.assertEqual(eloquence_stub.synth_queue.items, [])
		self.assertFalse(eloquence_stub.processed)


if __name__ == "__main__":
	unittest.main()
