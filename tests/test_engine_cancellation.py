"""Cancellation must not call eciStop on an engine that cannot survive it.

Measured against openevv v0.3, with the proprietary ECI.DLL as the control:

* eciStop on an **idle** engine corrupts openevv.  A cancellation always finds it
  idle, because eciSynthesize has already drained by the time one arrives.  The
  second such call leaves the next utterance producing no audio at all, and the
  third segfaults the process.  The proprietary engine ran the same sequence ten
  times over with no ill effect.
* eciStop **during** synthesis is safe on openevv but does not abort anything:
  measured identical audio length with and without it.
* eciClearInput is safe on openevv but does not discard queued text there, so it
  is no substitute.

So openevv has no usable stop, and nothing is lost by not calling it: what makes
cancellation audible is the Speech Generation advancing and the player stopping,
neither of which touches the engine.

The visible symptom was an ERROR per cancelled utterance --
"Eloquence skipped index callback N; reporting it at completion" -- because a
wedged engine stopped delivering index callbacks entirely.  That log line was
right, and it is kept: a silent engine failure should be loud.
"""

import unittest

from addon.synthDrivers import _eci_engine as engine


class _FakeDll:
	"""Records the ECI calls an engine makes."""

	def __init__(self):
		self.calls = []

	def __getattr__(self, name):
		def record(*args):
			self.calls.append(name)
			return 1

		return record

	def names(self):
		return self.calls


def _engine_with(supports_eci_stop):
	config = engine.EngineConfig(
		eci_path="",
		data_directory="",
		language_code="enu",
		enable_abbrev_dict=False,
		enable_phrase_prediction=False,
		voice_variant=0,
		rewrite_ini=False,
		supports_eci_stop=supports_eci_stop,
	)
	events = []
	instance = engine.EciEngine(lambda event, **payload: events.append(event), config)
	instance._dll = _FakeDll()
	instance._handle = "eci"
	return instance, events


class EciStopSuppressionTests(unittest.TestCase):
	def test_an_engine_that_cannot_survive_eci_stop_is_not_sent_it(self):
		instance, _events = _engine_with(supports_eci_stop=False)
		instance.stop()
		self.assertNotIn("eciStop", instance._dll.names())

	def test_the_proprietary_engine_still_gets_eci_stop(self):
		# The host path works and must keep working; this is the default.
		instance, _events = _engine_with(supports_eci_stop=True)
		instance.stop()
		self.assertIn("eciStop", instance._dll.names())

	def test_supporting_eci_stop_is_the_default(self):
		config = engine.EngineConfig(
			eci_path="",
			data_directory="",
			language_code="enu",
			enable_abbrev_dict=False,
			enable_phrase_prediction=False,
			voice_variant=0,
		)
		self.assertTrue(config.supports_eci_stop)

	def test_the_python_side_reset_happens_either_way(self):
		for supports in (True, False):
			with self.subTest(supports_eci_stop=supports):
				instance, events = _engine_with(supports_eci_stop=supports)
				instance._pending_indexes.extend([1, 2, 3])
				instance._audio_buffer.write(b"stale audio")
				instance._speaking = True
				instance._saw_final_index = True

				instance.stop()

				self.assertEqual(instance._pending_indexes, [])
				self.assertEqual(instance._audio_buffer.getvalue(), b"")
				self.assertFalse(instance._speaking)
				# Cleared so the next utterance cannot inherit a stale "we already
				# saw the final index" and skip its own completion notification.
				self.assertFalse(instance._saw_final_index)
				self.assertIn("stopped", events)

	def test_the_dispatcher_passes_the_flag_through(self):
		dispatcher = engine.EciDispatcher(lambda *a, **k: None)
		captured = {}

		class _Probe(engine.EciEngine):
			def start(self):
				captured["supports"] = self._config.supports_eci_stop

		original = engine.EciEngine
		engine.EciEngine = _Probe
		try:
			dispatcher.handle(
				"initialize",
				{
					"eciPath": "",
					"dataDirectory": "",
					"language": "enu",
					"supportsEciStop": False,
				},
			)
		finally:
			engine.EciEngine = original
		self.assertFalse(captured["supports"])


class PendingIndexBookkeepingTests(unittest.TestCase):
	"""The bookkeeping whose failure surfaced the wedged engine."""

	def test_a_reported_index_clears_itself_and_earlier_ones(self):
		instance, _events = _engine_with(supports_eci_stop=False)
		instance.insert_index(1)
		instance.insert_index(2)
		instance.insert_index(3)
		instance._discard_pending_indexes_through(2)
		self.assertEqual(instance._pending_indexes, [3])

	def test_the_final_index_is_never_treated_as_pending(self):
		instance, _events = _engine_with(supports_eci_stop=False)
		instance.insert_index(engine.FINAL_INDEX)
		self.assertEqual(instance._pending_indexes, [])

	def test_a_repeated_index_value_clears_only_one_occurrence(self):
		# NVDA reuses index numbers across utterances, so the list can legitimately
		# hold the same value twice; clearing must not drop both.
		instance, _events = _engine_with(supports_eci_stop=False)
		instance.insert_index(4)
		instance.insert_index(4)
		instance._discard_pending_indexes_through(4)
		self.assertEqual(instance._pending_indexes, [4])

	def test_an_unknown_reported_index_leaves_the_list_alone(self):
		instance, _events = _engine_with(supports_eci_stop=False)
		instance.insert_index(5)
		instance._discard_pending_indexes_through(99)
		self.assertEqual(instance._pending_indexes, [5])


if __name__ == "__main__":
	unittest.main()
