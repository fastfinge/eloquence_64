"""The Cancel Board: letting the host skip an utterance that is already cancelled.

The Eloquence Host Process reads Host Commands in order, so a synthesize the
worker sent before NVDA cancelled is still spoken, to nobody, before the next
utterance can start.  The Synth Driver side publishes each new Speech Generation
on a shared board, and the host checks it as each synthesize starts.
"""

import ctypes
import unittest
import uuid

if not hasattr(ctypes, "WINFUNCTYPE"):
	ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE  # type: ignore[attr-defined]

import host_eloquence32 as host
from addon.synthDrivers import _eci_engine as engine


def _board_name():
	return f"Local\\eloquence-cancel-test-{uuid.uuid4().hex}"


class CancelBoardTests(unittest.TestCase):
	def test_a_published_generation_is_seen_through_another_mapping(self):
		# The two processes each open the board by name.
		name = _board_name()
		writer, reader = engine.CancelBoard(name), engine.CancelBoard(name)
		self.addCleanup(writer.close)
		self.addCleanup(reader.close)
		self.assertEqual(reader.latest(), 0)
		writer.publish(7)
		self.assertEqual(reader.latest(), 7)

	def test_only_older_generations_are_stale(self):
		board = engine.CancelBoard(_board_name())
		self.addCleanup(board.close)
		board.publish(3)
		self.assertTrue(board.is_stale(2))
		self.assertFalse(board.is_stale(3))
		self.assertFalse(board.is_stale(4))


class _Dll:
	def __init__(self):
		self.calls = []

	def eciSynthesize(self, handle):
		self.calls.append("synthesize")
		return 1

	def eciSynchronize(self, handle):
		self.calls.append("synchronize")
		return 1

	def eciStop(self, handle):
		self.calls.append("stop")
		return 1

	def eciInsertIndex(self, handle, index):
		return 1


def _engine(abort_check):
	events = []
	instance = engine.EciEngine(
		lambda event, **payload: events.append((event, payload)),
		engine.EngineConfig(
			eci_path="",
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
		),
	)
	instance._dll = _Dll()
	instance._handle = "eci"
	instance.abort_check = abort_check
	return instance, events


class EngineSkipTests(unittest.TestCase):
	def test_a_stale_utterance_is_discarded_not_synthesized(self):
		instance, events = _engine(lambda generation: generation < 5)
		instance.insert_index(1)
		instance.synthesize(generation=4)
		# eciStop before synthesis begins discards the queued text, so it cannot
		# reach the next utterance; measured on the proprietary engine.
		self.assertEqual(instance._dll.calls, ["stop"])
		self.assertEqual(instance._pending_indexes, [])
		self.assertEqual(events, [("audio", {"data": b"", "index": None, "final": True})])

	def test_a_current_utterance_is_synthesized(self):
		instance, _events = _engine(lambda generation: generation < 5)
		instance.synthesize(generation=5)
		self.assertEqual(instance._dll.calls, ["synthesize", "synchronize"])

	def test_without_a_board_nothing_is_ever_skipped(self):
		# The Direct Backend sets no abort_check, and an older client sends no
		# generation.
		for abort_check, generation in ((None, 0), (lambda generation: True, None)):
			with self.subTest(abort_check=abort_check, generation=generation):
				instance, _events = _engine(abort_check)
				instance.synthesize(generation=generation)
				self.assertEqual(instance._dll.calls, ["synthesize", "synchronize"])


class _Dispatcher:
	def __init__(self):
		self.handled = []
		self.abort_check = None

	def knows(self, command):
		return True

	def handle(self, command, payload):
		self.handled.append((command, payload))
		return {"params": {}}


class HostAttachTests(unittest.TestCase):
	def setUp(self):
		self.controller = host.HostController(conn=None)
		self.dispatcher = _Dispatcher()
		self.controller._dispatcher = self.dispatcher

	def test_initialize_attaches_the_named_board_and_says_so(self):
		name = _board_name()
		client_side = engine.CancelBoard(name)
		self.addCleanup(client_side.close)
		response = self.controller._handle("initialize", {"language": "enu", "cancelBoard": name})
		self.assertTrue(response["cancelBoard"])
		# The engine never sees the board's name.
		self.assertEqual(self.dispatcher.handled, [("initialize", {"language": "enu"})])
		client_side.publish(2)
		self.assertTrue(self.dispatcher.abort_check(1))
		self.assertFalse(self.dispatcher.abort_check(2))

	def test_initialize_without_a_board_is_answered_as_before(self):
		response = self.controller._handle("initialize", {"language": "enu"})
		self.assertNotIn("cancelBoard", response)
		self.assertIsNone(self.dispatcher.abort_check)

	def test_other_commands_pass_straight_through(self):
		self.controller._handle("synthesize", {"generation": 3})
		self.assertEqual(self.dispatcher.handled, [("synthesize", {"generation": 3})])


if __name__ == "__main__":
	unittest.main()
