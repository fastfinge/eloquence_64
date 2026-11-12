"""Checks on the add-on's source that only fail, otherwise, when a user reaches the line.

Both come from mistakes that have shipped in NVDA add-ons before: a wx name that exists in
wxWidgets' C++ API and not in wxPython, and a module used in a file that never imports it. Python
finds neither at import; it finds them when the line runs, which for a dialog is when somebody
opens it.
"""

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

_SOURCES = sorted(
	path
	for path in (Path(__file__).parents[1] / "addon").rglob("*.py")
	# The vendored standard-library package and the files copied from the contours submodule.
	if "multiprocessing" not in path.parts and "contours" not in path.parts
)

# Asks a real wxPython which of these names it lacks. In its own process on purpose: other tests put a
# stand-in for wx into sys.modules, and asking that would fail every correct name.
_PROBE = """
import sys
try:
	import wx
except Exception:
	print("NOWX")
else:
	print(wx.__version__)
	print(" ".join(name for name in sys.argv[1:] if not hasattr(wx, name)))
"""

# Modules the add-on reaches for by name. One missing from this list is simply not checked.
_WATCHED = frozenset(
	"""
	logging os sys re io struct time threading queue subprocess ctypes json math shutil tempfile
	zipfile hashlib itertools functools importlib types wx gui nvwave globalVars config addonHandler
	speech winreg configparser unicodedata string enum
	""".split()
)


class WxNameTests(unittest.TestCase):
	def test_every_wx_name_used_exists_in_wxpython(self):
		used = {}
		for path in _SOURCES:
			for name in re.findall(r"\bwx\.([A-Za-z_][A-Za-z0-9_]*)", path.read_text(encoding="utf-8")):
				used.setdefault(name, path.name)
		# A pattern that matched nothing would pass every time, so prove it is looking at something.
		self.assertIn("Dialog", used)
		done = subprocess.run(
			[sys.executable, "-c", _PROBE, *sorted(used)], capture_output=True, text=True, timeout=120
		)
		lines = done.stdout.strip().split("\n")
		if not lines or lines[0].strip() == "NOWX":
			self.skipTest("needs wxPython")
		missing = lines[1].split() if len(lines) > 1 else []
		self.assertEqual(
			{name: used[name] for name in missing},
			{},
			f"wxPython {lines[0].strip()} has no such names",
		)


class ImportTests(unittest.TestCase):
	def test_every_module_a_file_uses_is_imported_there(self):
		self.assertTrue(_SOURCES)
		for path in _SOURCES:
			with self.subTest(file=path.name):
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
				bound = _names_bound(tree)
				used = {}
				for node in ast.walk(tree):
					if (
						isinstance(node, ast.Attribute)
						and isinstance(node.value, ast.Name)
						and node.value.id in _WATCHED
					):
						used.setdefault(node.value.id, node.lineno)
				missing = sorted((name, line) for name, line in used.items() if name not in bound)
				self.assertEqual(missing, [], "used but never imported; this only fails when the line runs")


def _names_bound(tree):
	"""Every name the file binds anywhere: imports, assignments, definitions and parameters."""
	names = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
		elif isinstance(node, ast.ImportFrom):
			names |= {alias.asname or alias.name for alias in node.names}
		elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			names.add(node.name)
			arguments = getattr(node, "args", None)
			if arguments is not None:
				for group in (arguments.args, arguments.posonlyargs, arguments.kwonlyargs):
					names |= {argument.arg for argument in group}
				names |= {extra.arg for extra in (arguments.vararg, arguments.kwarg) if extra is not None}
		elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
			names.add(node.id)
		elif isinstance(node, ast.ExceptHandler) and node.name:
			names.add(node.name)
	return names


if __name__ == "__main__":
	unittest.main()
