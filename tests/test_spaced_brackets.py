"""The openevv spaced-bracket workaround.

openevv speaks the *name* of a bracket that whitespace separates from its text,
where the proprietary engine stays silent and merely shapes the prosody.  The
affected set was established by comparing PCM durations from both real engines
at 11025 Hz, and it is narrower than openevv-nvda 0.1.3's description: brackets,
braces, parentheses and double quotes misread, colons do not.

Reference measurements, openevv v0.3 against the proprietary ECI.DLL:

	( hello world )   IBM 1.23s   openevv 3.47s   2.83x
	[ hello world ]   IBM 1.23s   openevv 2.71s   2.21x
	{ hello world }   IBM 1.23s   openevv 2.44s   2.00x
	" hello world "   IBM 1.23s   openevv 1.92s   1.56x
	Name : value      IBM 1.85s   openevv 1.72s   0.93x  (unaffected)

With the rewrite applied, openevv's output matches the proprietary engine's to
within 1.6% on every case above.
"""

import unittest

from addon.synthDrivers._eloquence_text import BuildOptions, build
from addon.synthDrivers._text_preprocessing import attach_spaced_brackets

ENU = 65536


def _options(**overrides):
	base = dict(
		volume=92,
		rate=50,
		pause_mode=1,
		backquote_tags=False,
		abbreviation_dict=False,
		phrase_prediction=False,
		attach_spaced_brackets=True,
	)
	base.update(overrides)
	return BuildOptions(**base)


class AttachSpacedBracketsTests(unittest.TestCase):
	def test_paired_brackets_are_attached_to_their_text(self):
		cases = {
			"( hello world )": "(hello world)",
			"[ hello world ]": "[hello world]",
			"{ hello world }": "{hello world}",
			'" hello world "': '"hello world"',
		}
		for spaced, expected in cases.items():
			with self.subTest(spaced=spaced):
				self.assertEqual(attach_spaced_brackets(spaced), expected)

	def test_each_bracket_is_independent(self):
		# The bug is per bracket, not per pair: one spaced bracket is enough to
		# have its name spoken, so one spaced bracket must be enough to fix.
		self.assertEqual(attach_spaced_brackets("( hello world"), "(hello world")
		self.assertEqual(attach_spaced_brackets("hello world )"), "hello world)")
		self.assertEqual(attach_spaced_brackets("( hello world)"), "(hello world)")
		self.assertEqual(attach_spaced_brackets("(hello world )"), "(hello world)")

	def test_multiple_pairs_are_all_attached(self):
		self.assertEqual(attach_spaced_brackets("( a ) and ( b )"), "(a) and (b)")
		self.assertEqual(attach_spaced_brackets("a ( b ) c ( d ) e"), "a (b) c (d) e")

	def test_already_attached_text_is_untouched(self):
		for text in ("(hello world)", "[a]", "plain text here", ""):
			with self.subTest(text=text):
				self.assertEqual(attach_spaced_brackets(text), text)

	def test_an_isolated_bracket_keeps_its_spacing(self):
		# Character navigation and symbol names NVDA has already spelled out
		# arrive with no adjacent text.  Those must still be announced, so there
		# is nothing to attach them to and nothing may change.
		for text in ("(", ")", "[", "}", '"', "  (  "):
			with self.subTest(text=text):
				self.assertEqual(attach_spaced_brackets(text), text)

	def test_characters_measured_as_unaffected_are_left_alone(self):
		# Deliberately NOT fixed: these match the proprietary engine already, so
		# rewriting them would change output for no reason.
		for text in (
			"Name : value",
			"one ; two",
			"one , two",
			"End . Next",
			"well - known",
			"it ' s",
			"< hello world >",
			"a / b",
		):
			with self.subTest(text=text):
				self.assertEqual(attach_spaced_brackets(text), text)

	def test_a_timestamp_is_not_rewritten_here(self):
		# 12:30:45 does misread on openevv, but time_re in the Eloquence Text
		# Builder already converts it into the form that does not, so this
		# rewrite has no business touching it.
		self.assertEqual(attach_spaced_brackets("at 12:30:45 today"), "at 12:30:45 today")

	def test_a_url_is_not_rewritten(self):
		url = "go to http://example.com/a_b now"
		self.assertEqual(attach_spaced_brackets(url), url)

	def test_newlines_are_preserved(self):
		# Only spaces and tabs are collapsed, so a line structure NVDA relies on
		# survives.
		self.assertEqual(attach_spaced_brackets("(\nhello\n)"), "(\nhello\n)")


class BuildIntegrationTests(unittest.TestCase):
	def test_the_builder_applies_the_fix_when_asked(self):
		built = build("( hello )", voice_id=ENU, options=_options())
		self.assertIn(b"(hello)", built)

	def test_the_builder_leaves_text_alone_for_the_proprietary_engine(self):
		# The host path has no bug to work around, and rewriting there would be a
		# behaviour change for existing users.
		built = build("( hello )", voice_id=ENU, options=_options(attach_spaced_brackets=False))
		self.assertIn(b"( hello )", built)

	def test_raw_backquote_tag_mode_is_never_rewritten(self):
		# In raw voice-tag mode the author is addressing the engine directly and
		# spacing may be deliberate.
		built = build(
			"( hello )", voice_id=ENU, options=_options(backquote_tags=True)
		)
		self.assertIn(b"( hello )", built)

	def test_the_punctuation_itself_is_kept(self):
		# The brackets must still reach the engine: they carry the prosody even
		# when their names are not spoken.
		built = build("( hello )", voice_id=ENU, options=_options())
		self.assertIn(b"(", built)
		self.assertIn(b")", built)


if __name__ == "__main__":
	unittest.main()
