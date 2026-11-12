"""The entry rules agree with EloquenceDictionaryManager's shared examples.

tests/data/entry-validation-examples.json is copied unchanged from
akj/EloquenceDictionaryManager@9da9c897c32f72d2c1391b70c8d4e11f432a8638, docs/entry-validation-examples.json.
It defines what a valid western dictionary entry is for every add-on that reads the shared
dictionary folder, so when it changes upstream, copy the new file here and fix whatever disagrees.
"""

import json
import unittest
from pathlib import Path

from addon.synthDrivers import _dictionary_entries as entries

_EXAMPLES = json.loads(
	(Path(__file__).parent / "data" / "entry-validation-examples.json").read_text(encoding="utf-8")
)


class SharedExampleTests(unittest.TestCase):
	def test_every_example_gets_its_expected_result(self):
		self.assertGreaterEqual(len(_EXAMPLES["examples"]), 85)
		for example in _EXAMPLES["examples"]:
			with self.subTest(**{k: example[k] for k in ("voice", "slot", "key", "value")}):
				problems = entries.entry_problems(
					example["voice"], example["slot"], example["key"], example["value"]
				)
				expected = () if example["expected"] == "valid" else (example["expected"],)
				self.assertEqual(problems, expected)

	def test_a_warning_is_given_exactly_where_an_example_names_one(self):
		for example in _EXAMPLES["examples"]:
			with self.subTest(**{k: example[k] for k in ("voice", "slot", "key", "value")}):
				warning = entries.entry_warning(
					example["voice"], example["slot"], example["key"], example["value"]
				)
				self.assertEqual(warning, example.get("warning"))

	def test_every_identifier_reported_is_one_the_examples_define(self):
		reported = {
			problem
			for example in _EXAMPLES["examples"]
			for problem in entries.entry_problems(
				example["voice"], example["slot"], example["key"], example["value"]
			)
		}
		self.assertLessEqual(reported, set(_EXAMPLES["reasons"]))
		self.assertIn(entries.NEVER_MATCHES, _EXAMPLES["warnings"])


class EntryRuleTests(unittest.TestCase):
	def test_an_entry_can_break_several_rules_at_once(self):
		self.assertEqual(
			entries.entry_problems("enu", "root", "Win32", "two words"),
			("rootKeyInvalid", "rootValueInvalid"),
		)

	def test_an_invalid_entry_carries_no_warning(self):
		self.assertIsNone(entries.entry_warning("enu", "abbr", "AT&T", "A T and T2"))

	def test_german_hyphens_after_a_period_carry_no_warning(self):
		self.assertEqual(entries.entry_problems("deu", "abbr", "Dipl.-Kfm.", "Diplomkaufmann"), ())
		self.assertIsNone(entries.entry_warning("deu", "abbr", "Dipl.-Kfm.", "Diplomkaufmann"))

	def test_each_spr_in_a_main_value_is_checked(self):
		self.assertEqual(
			entries.entry_problems("enu", "main", "word", "`[.1kAt] and `[.dOg.kAt]"),
			("sprPrimaryStressRequired",),
		)

	def test_the_real_community_shapes_are_valid(self):
		for slot, key, value in (
			("main", "billets-doux", "`[.2bI.0le.1du]"),
			("main", "mbox", "em `0 box"),
			("root", "d'état", "`[.2de.1ta]"),
			("root", "jiménez", "`[hXm1EnEz]"),
			("abbr", "WWII", "world war two"),
		):
			with self.subTest(slot=slot, key=key):
				self.assertEqual(entries.entry_problems("enu", slot, key, value), ())


if __name__ == "__main__":
	unittest.main()
