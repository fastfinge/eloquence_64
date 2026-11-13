import unittest

from addon.synthDrivers import _dictionary_stack as stack
from addon.synthDrivers._dictionary_stack import Layer

CAT, DOG = "`[.1kAt]", "`[.1dOg]"


def _file(*lines, newline="\r\n", encoding="cp1252"):
	return (newline.join(lines) + newline).encode(encoding)


class ParseDictionaryTests(unittest.TestCase):
	def test_entries_come_back_in_file_order(self):
		parsed = stack.parse_dictionary(_file(f"cat\t{CAT}", f"dog\t{DOG}"), "enu", "main")
		self.assertEqual(parsed.entries, (("cat", CAT), ("dog", DOG)))
		self.assertEqual(parsed.reports, ())

	def test_utf8_and_ansi_files_read_the_same(self):
		ansi = stack.parse_dictionary(_file("café\tkafay"), "enu", "root")
		utf8 = stack.parse_dictionary(_file("café\tkafay", encoding="utf-8-sig"), "enu", "root")
		self.assertEqual(ansi.entries, utf8.entries)
		self.assertEqual(ansi.entries, (("café", "kafay"),))

	def test_blank_lines_are_dropped_and_do_not_end_the_file(self):
		# The proprietary engine stops reading at a blank line; older files still contain one.
		parsed = stack.parse_dictionary(_file(f"cat\t{CAT}", "", "   ", f"dog\t{DOG}"), "enu", "main")
		self.assertEqual([key for key, _ in parsed.entries], ["cat", "dog"])

	def test_every_line_ending_is_understood(self):
		for newline in ("\r\n", "\n", "\r"):
			with self.subTest(newline=newline):
				parsed = stack.parse_dictionary(
					_file(f"cat\t{CAT}", f"dog\t{DOG}", newline=newline), "enu", "main"
				)
				self.assertEqual(len(parsed.entries), 2)

	def test_doubled_tabs_and_stray_spaces_are_tidied(self):
		# Both shapes are in the published files: "kubernetes<TAB><TAB>..." and " portolés<TAB>...".
		parsed = stack.parse_dictionary(_file(f"kubernetes\t\t{CAT}", f" portoles\t{DOG} "), "enu", "root")
		self.assertEqual(parsed.entries, (("kubernetes", CAT), ("portoles", DOG)))
		self.assertEqual(parsed.reports, ())

	def test_an_entry_that_breaks_a_rule_is_kept_and_reported(self):
		# Measured on all three engines: these community entries break a rule and are still used.
		parsed = stack.parse_dictionary(_file("IDK\tI don't know", "Dr.\tdoctor"), "enu", "abbr")
		self.assertEqual(parsed.entries, (("IDK", "I don't know"), ("Dr.", "doctor")))
		self.assertEqual(
			parsed.reports,
			(stack.EntryReport(1, "IDK", ("abbreviationValueInvalid",), None, kept=True),),
		)

	def test_an_entry_that_never_matches_is_kept_with_a_warning(self):
		parsed = stack.parse_dictionary(_file("AT&T\tA T and T"), "enu", "abbr")
		self.assertEqual(len(parsed.entries), 1)
		self.assertEqual(parsed.reports, (stack.EntryReport(1, "AT&T", (), "abbreviationKeyNeverMatches"),))

	def test_an_entry_that_cannot_be_written_is_left_out_and_reported(self):
		parsed = stack.parse_dictionary(
			_file(f"cat\t{CAT}", "\tvalue only", "key only\t", f"a\tb\t{DOG}"), "enu", "main"
		)
		self.assertEqual(parsed.entries, (("cat", CAT),))
		self.assertEqual(
			[(report.line, report.kept) for report in parsed.reports],
			[(2, False), (3, False), (4, False)],
		)
		self.assertIn("emptyKey", parsed.reports[0].problems)
		self.assertIn("emptyValue", parsed.reports[1].problems)
		self.assertIn("valueTab", parsed.reports[2].problems)

	def test_a_file_is_rejected_whole_when_it_is_not_a_dictionary(self):
		cases = {
			"notText": b"cat\t\x00\r\n",
			"noTab": _file(f"cat\t{CAT}", "just some words"),
			"unencodableCharacter": _file(f"cat\t{CAT}", "漢\than", encoding="utf-8"),
			"keyTooLong": _file("a" * 129 + "\tword"),
			"valueTooLong": _file("word\t" + "a" * 513),
			"tooLarge": b"a\tb\r\n" * (stack.MAX_FILE_BYTES // 5 + 1),
		}
		for reason, data in cases.items():
			with self.subTest(reason=reason):
				with self.assertRaises(stack.DictionaryFileError) as raised:
					stack.parse_dictionary(data, "enu", "main")
				self.assertEqual(raised.exception.reason, reason)

	def test_a_rejection_names_the_line(self):
		with self.assertRaises(stack.DictionaryFileError) as raised:
			stack.parse_dictionary(_file(f"cat\t{CAT}", "", "no tab here"), "enu", "main")
		self.assertEqual(raised.exception.line, 3)

	def test_a_file_at_the_size_limit_is_read(self):
		line = b"a\t" + b"b" * 500 + b"\r\n"
		data = line * (stack.MAX_FILE_BYTES // len(line))
		data += b"c\t" + b"d" * (stack.MAX_FILE_BYTES - len(data) - 4) + b"\r\n"
		self.assertEqual(len(data), stack.MAX_FILE_BYTES)
		parsed = stack.parse_dictionary(data, "enu", "main")
		self.assertEqual(parsed.entries[-1][0], "c")
		with self.assertRaises(stack.DictionaryFileError):
			stack.parse_dictionary(data + b"\n", "enu", "main")


class MergeLayersTests(unittest.TestCase):
	def test_a_later_layer_wins_for_the_same_key(self):
		merged = stack.merge_layers([Layer(main=(("cat", "one"),)), Layer(main=(("cat", "two"),))])
		self.assertEqual(merged["main"], (("cat", "two"),))

	def test_entries_only_one_layer_has_all_survive(self):
		merged = stack.merge_layers([Layer(root=(("cat", CAT),)), Layer(root=(("dog", DOG),))])
		self.assertEqual(merged["root"], (("cat", CAT), ("dog", DOG)))

	def test_root_keys_compare_without_case_and_the_others_exactly(self):
		first = Layer(main=(("WHO", "one"),), root=(("Quinoa", "one"),), abbr=(("Dr", "one"),))
		second = Layer(main=(("who", "two"),), root=(("quinoa", "two"),), abbr=(("dr", "two"),))
		merged = stack.merge_layers([first, second])
		self.assertEqual(merged["main"], (("WHO", "one"), ("who", "two")))
		self.assertEqual(merged["root"], (("quinoa", "two"),))
		self.assertEqual(merged["abbr"], (("Dr", "one"), ("dr", "two")))

	def test_accented_and_plain_root_keys_stay_separate(self):
		# The proprietary engine and ReLoq C tell them apart, so the merge must not fold them.
		merged = stack.merge_layers([Layer(root=(("jimenez", "one"),)), Layer(root=(("jiménez", "two"),))])
		self.assertEqual(len(merged["root"]), 2)

	def test_a_second_copy_within_one_layer_wins_as_in_the_engine(self):
		merged = stack.merge_layers([Layer(root=(("cat", "one"), ("dog", DOG), ("cat", "two")))])
		self.assertEqual(merged["root"], (("dog", DOG), ("cat", "two")))

	def test_a_later_root_entry_removes_the_word_from_earlier_main(self):
		source = Layer(main=(("GUI", "gooey"), ("Gui", "gooey"), ("guide", "guide")))
		personal = Layer(root=(("gui", "`[.1gu.0i]"),))
		merged = stack.merge_layers([source, personal])
		self.assertEqual(merged["main"], (("guide", "guide"),))
		self.assertEqual(merged["root"], (("gui", "`[.1gu.0i]"),))

	def test_a_later_main_entry_is_kept_beside_an_earlier_root_entry(self):
		# Main already beats root in the engine, so this direction needs nothing dropped.
		merged = stack.merge_layers([Layer(root=(("gui", "one"),)), Layer(main=(("GUI", "two"),))])
		self.assertEqual(merged["main"], (("GUI", "two"),))
		self.assertEqual(merged["root"], (("gui", "one"),))

	def test_nothing_is_dropped_within_one_layer(self):
		merged = stack.merge_layers([Layer(main=(("GUI", "one"),), root=(("gui", "two"),))])
		self.assertEqual(merged["main"], (("GUI", "one"),))
		self.assertEqual(merged["root"], (("gui", "two"),))

	def test_a_root_entry_only_reaches_back_not_forward(self):
		layers = [
			Layer(main=(("GUI", "first"),)),
			Layer(root=(("gui", "second"),)),
			Layer(main=(("GUI", "third"),)),
		]
		self.assertEqual(stack.merge_layers(layers)["main"], (("GUI", "third"),))

	def test_no_layers_gives_nothing(self):
		self.assertEqual(stack.merge_layers([]), {"main": (), "root": (), "abbr": ()})


class GenerateFilesTests(unittest.TestCase):
	def test_files_are_ansi_with_crlf_and_only_for_slots_that_have_entries(self):
		files = stack.generate_files([Layer(root=(("café", CAT), ("dog", DOG)))])
		self.assertEqual(files, {"root": f"café\t{CAT}\r\ndog\t{DOG}\r\n".encode("cp1252")})

	def test_a_parsed_file_generates_back_to_the_same_entries(self):
		data = _file(f"cat\t{CAT}", f"dog\t{DOG}")
		parsed = stack.parse_dictionary(data, "enu", "main")
		self.assertEqual(stack.generate_files([Layer(main=parsed.entries)])["main"], data)

	def test_a_merged_file_over_the_limit_is_refused_by_slot(self):
		# Two layers that each fit can still add up to more than ReLoq C accepts.
		value = "a" * 500
		line_bytes = len("a0000000") + len("\t") + len(value) + len("\r\n")
		half = stack.MAX_FILE_BYTES // 2 // line_bytes + 1
		first = Layer(root=tuple((f"a{number:07d}".translate(_DIGITS), value) for number in range(half)))
		second = Layer(root=tuple((f"b{number:07d}".translate(_DIGITS), value) for number in range(half)))
		self.assertIn("root", stack.generate_files([first]))
		with self.assertRaises(stack.StackTooLargeError) as raised:
			stack.generate_files([first, second])
		self.assertEqual(raised.exception.slot, "root")
		self.assertGreater(raised.exception.size, stack.MAX_FILE_BYTES)


# Root keys are letters only, so number the test keys with letters.
_DIGITS = str.maketrans("0123456789", "abcdefghij")


class SetIdTests(unittest.TestCase):
	def test_ids_match_the_contract_examples(self):
		# From EloquenceDictionaryManager's spec and ADR 0003, at commit 9da9c89.
		cases = {
			("eigencrow", "IBMTTSDictionaries", ""): "github.eigencrow.ibmtts-dictionaries",
			("Mohamed00", "AltIBMTTSDictionaries", ""): "github.mohamed00.alt-ibmtts-dictionaries",
			(
				"Mohamed00",
				"AltIBMTTSDictionaries",
				"IBMTTS 6.7",
			): "github.mohamed00.alt-ibmtts-dictionaries..ibmtts.206.2e7",
			("fastfinge", "eloquence_64", ""): "github.fastfinge.eloquence.5f64",
			("example", "dict.js", ""): "github.example.dict.2ejs",
			("example", "dict-js", ""): "github.example.dict-js",
			("example", "dict_js", ""): "github.example.dict.5fjs",
			("example", ".github", ""): "github.example..2egithub",
			("example", "dicts", "British/v2"): "github.example.dicts..british.2fv2",
			("example", "a.b", "c"): "github.example.a.2eb..c",
			("example", "a", "b.c"): "github.example.a..b.2ec",
			("example", "a.2eb", ""): "github.example.a.2e2eb",
			("example", "a", ".hidden"): "github.example.a...2ehidden",
			("example", "dicts", "Español"): "github.example.dicts..espa.c3.b1ol",
		}
		for (owner, repository, folder), expected in cases.items():
			with self.subTest(owner=owner, repository=repository, folder=folder):
				self.assertEqual(stack.set_id_for_repository(owner, repository, folder), expected)

	def test_a_repository_that_would_take_a_registered_id_gets_none(self):
		self.assertIsNone(stack.set_id_for_repository("eigencrow", "ibmtts-dictionaries"))
		self.assertIsNone(stack.set_id_for_repository("mohamed00", "alt-ibmtts-dictionaries", "x"))

	def test_owner_and_repository_case_does_not_change_the_id(self):
		self.assertEqual(
			stack.set_id_for_repository("EIGENCROW", "ibmttsdictionaries"),
			"github.eigencrow.ibmtts-dictionaries",
		)

	def test_folder_separators_and_case_do_not_change_the_id(self):
		expected = stack.set_id_for_repository("example", "dicts", "British/v2")
		for folder in ("british\\V2", "/British/v2/", "BRITISH/V2"):
			with self.subTest(folder=folder):
				self.assertEqual(stack.set_id_for_repository("example", "dicts", folder), expected)


if __name__ == "__main__":
	unittest.main()
