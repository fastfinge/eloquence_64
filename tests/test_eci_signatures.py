"""Every ECI entry point the wrapper calls must have a declared ctypes signature.

An ECI handle is a pointer.  Called without argtypes, ctypes marshals the Python
int that handle has become as a C int: a handle above 2 GiB is silently
truncated, and on 64-bit the call raises "int too long to convert" instead.  That
is not hypothetical -- it is how a larger openevv build broke the Direct Backend
at eciSetParam, having been mapped above the boundary the previous build happened
to sit below, with nothing changed on this side.  The proprietary 32-bit engine
is one unlucky allocation away from the same fault.

So this walks the wrapper's own call sites rather than trusting that a new one
remembered the table: a self._dll.eciSomething(...) call with no entry in
ECI_SIGNATURES fails here, at the point it is added.
"""

import ast
import pathlib
import unittest

from addon.synthDrivers import _eci_engine as engine

_SOURCE = pathlib.Path(engine.__file__)


def _called_entry_points():
	"""Every name called as ``self._dll.<name>(...)`` in the wrapper."""
	tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
	names = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		function = node.func
		if not isinstance(function, ast.Attribute):
			continue
		owner = function.value
		if (
			isinstance(owner, ast.Attribute)
			and owner.attr == "_dll"
			and isinstance(owner.value, ast.Name)
			and owner.value.id == "self"
		):
			names.add(function.attr)
	return names


class EciSignatureCoverageTests(unittest.TestCase):
	def test_every_call_site_has_a_signature(self):
		missing = sorted(_called_entry_points() - set(engine.ECI_SIGNATURES))
		self.assertEqual(
			missing,
			[],
			f"these are called without a ctypes signature: {missing}; "
			"add them to ECI_SIGNATURES in _eci_engine.py",
		)

	def test_the_call_sites_were_actually_found(self):
		# Guards the walk above: if it ever stops matching, the test over it would
		# pass vacuously and the truncation bug would come back unnoticed.
		found = _called_entry_points()
		self.assertIn("eciSetParam", found)
		self.assertIn("eciSynthesize", found)
		self.assertGreater(len(found), 10)

	def test_the_handle_is_declared_as_a_pointer_everywhere(self):
		# The whole point: argument 0 of every entry point is the ECI handle, and
		# declaring it as anything int-sized is what truncates it.
		for name, (argtypes, _restype) in engine.ECI_SIGNATURES.items():
			if name == "eciNewEx":
				continue  # Takes a language id and returns the handle.
			with self.subTest(entry_point=name):
				self.assertTrue(argtypes, f"{name} declares no arguments")
				self.assertIs(argtypes[0], engine.c_void_p)

	def test_the_table_declares_nothing_unused(self):
		# A signature for an entry point nobody calls is dead weight that outlives
		# whatever removed the call.
		unused = sorted(set(engine.ECI_SIGNATURES) - _called_entry_points())
		self.assertEqual(unused, [])


if __name__ == "__main__":
	unittest.main()
