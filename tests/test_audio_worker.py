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
	module_name = "addon.synthDrivers._eloquence_audio_test"
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


class FakePlayer:
	def __init__(self, events):
		self.events = events
		self.on_done = []

	def feed(self, data, onDone=None):
		self.events.append(("feed", data))
		if onDone:
			self.on_done.append(onDone)

	def sync(self):
		self.events.append(("sync", None))
		while self.on_done:
			self.on_done.pop(0)()

	def idle(self):
		self.events.append(("idle", None))
		self.sync()


class FakeClient:
	_sequence = 0


class AudioWorkerTests(unittest.TestCase):
	def test_index_notification_waits_for_preceding_audio(self):
		# Index-only chunks must never reach WavePlayer.feed: degenerate tiny
		# buffers can cause audible clicks on some devices (see #127). Attach the
		# Speech Progress Notification to the preceding real Audio Chunk instead.
		module = _load_client_module()
		events = []
		module.onIndexReached = lambda index: events.append(("index", index))
		audio_queue = queue.Queue()
		audio_queue.put((b"audio", None, False, 0))
		audio_queue.put((b"", 42, False, 0))
		audio_queue.put(None)
		player = FakePlayer(events)
		worker = module.AudioWorker(player, audio_queue, FakeClient())

		worker.run()

		self.assertEqual(events, [("feed", b"audio")])
		self.assertEqual(len(player.on_done), 1)

		player.on_done[0]()

		self.assertEqual(events, [("feed", b"audio"), ("index", 42)])

	def test_index_notification_is_attached_to_last_preceding_audio_chunk(self):
		module = _load_client_module()
		events = []
		module.onIndexReached = lambda index: events.append(("index", index))
		audio_queue = queue.Queue()
		audio_queue.put((b"first", None, False, 0))
		audio_queue.put((b"last", None, False, 0))
		audio_queue.put((b"", 42, False, 0))
		audio_queue.put(None)
		player = FakePlayer(events)

		module.AudioWorker(player, audio_queue, FakeClient()).run()

		self.assertEqual(events, [("feed", b"first"), ("feed", b"last")])
		self.assertEqual(len(player.on_done), 1)
		player.on_done[0]()
		self.assertEqual(events[-1], ("index", 42))

	def test_completion_follows_preceding_index_and_audio(self):
		module = _load_client_module()
		events = []
		module.onIndexReached = lambda index: events.append(("index", index))
		audio_queue = queue.Queue()
		audio_queue.put((b"audio", None, False, 0))
		audio_queue.put((b"", 42, False, 0))
		audio_queue.put((b"", None, True, 0))
		audio_queue.put(None)
		player = FakePlayer(events)

		module.AudioWorker(player, audio_queue, FakeClient()).run()

		self.assertLess(events.index(("feed", b"audio")), events.index(("index", 42)))
		self.assertLess(events.index(("index", 42)), events.index(("index", None)))


if __name__ == "__main__":
	unittest.main()
