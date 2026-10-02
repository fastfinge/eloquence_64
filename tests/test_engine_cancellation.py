"""Cancellation resets the engine's own state and calls eciStop on every engine.

What makes a cancellation audible is upstream of the engine -- the Speech
Generation advances and the player is stopped -- so stop() is only about not
carrying state into the next utterance.  eciStop aborts nothing either way: a
cancellation always finds the engine idle, because eciSynthesize has already
drained by the time one arrives.

Some history, because it was load-bearing for a while and should not be
reinvented.  openevv v0.3 could not survive eciStop at all: called on an idle
engine it wedged, the second call left the next utterance producing no audio and
the third segfaulted the process (Mudb0y/openevv#35; the proprietary ECI.DLL ran
the same sequence ten times over as a control).  The add-on therefore carried an
EngineConfig.supports_eci_stop flag and a "supportsEciStop" key in the in-process
initialize payload, skipping the call on openevv only.

That is fixed upstream, and both are gone.  Measured against the build artifact
for openevv main@7ee8c572 -- the same reproducer from the issue, ten rounds of
add text, two indexes, synthesize, drain, eciStop -- every round returns 64768
samples and both indexes, draining with eciSynchronize and with eciSpeaking
polling alike, where v0.3 returns 0 samples on round 3.  Three consecutive
eciStop calls per round, eciStop from another thread mid-synthesis, and reuse of
the engine afterwards are all clean too.  So there is one code path again.
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


def _engine():
	config = engine.EngineConfig(
		eci_path="",
		data_directory="",
		language_code="enu",
		enable_abbrev_dict=False,
		enable_phrase_prediction=False,
		voice_variant=0,
		rewrite_ini=False,
	)
	events = []
	instance = engine.EciEngine(lambda event, **payload: events.append(event), config)
	instance._dll = _FakeDll()
	instance._handle = "eci"
	return instance, events


class CancellationTests(unittest.TestCase):
	def test_stopping_calls_eci_stop(self):
		instance, _events = _engine()
		instance.stop()
		self.assertIn("eciStop", instance._dll.names())

	def test_the_python_side_reset_happens_too(self):
		instance, events = _engine()
		instance._pending_indexes.extend([1, 2, 3])
		instance._audio_buffer.write(b"stale audio")
		instance._speaking = True
		instance._saw_final_index = True

		instance.stop()

		self.assertEqual(instance._pending_indexes, [])
		self.assertEqual(instance._audio_buffer.getvalue(), b"")
		self.assertFalse(instance._speaking)
		# Cleared so the next utterance cannot inherit a stale "we already saw the
		# final index" and skip its own completion notification.
		self.assertFalse(instance._saw_final_index)
		self.assertIn("stopped", events)


class PendingIndexBookkeepingTests(unittest.TestCase):
	"""The bookkeeping whose failure surfaced the wedged engine."""

	def test_a_reported_index_clears_itself_and_earlier_ones(self):
		instance, _events = _engine()
		instance.insert_index(1)
		instance.insert_index(2)
		instance.insert_index(3)
		instance._discard_pending_indexes_through(2)
		self.assertEqual(instance._pending_indexes, [3])

	def test_the_final_index_is_never_treated_as_pending(self):
		instance, _events = _engine()
		instance.insert_index(engine.FINAL_INDEX)
		self.assertEqual(instance._pending_indexes, [])

	def test_a_repeated_index_value_clears_only_one_occurrence(self):
		# NVDA reuses index numbers across utterances, so the list can legitimately
		# hold the same value twice; clearing must not drop both.
		instance, _events = _engine()
		instance.insert_index(4)
		instance.insert_index(4)
		instance._discard_pending_indexes_through(4)
		self.assertEqual(instance._pending_indexes, [4])

	def test_an_unknown_reported_index_leaves_the_list_alone(self):
		instance, _events = _engine()
		instance.insert_index(5)
		instance._discard_pending_indexes_through(99)
		self.assertEqual(instance._pending_indexes, [5])


if __name__ == "__main__":
	unittest.main()
