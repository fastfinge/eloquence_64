"""The Manage dictionaries dialog driven by real key presses.

Tab and Alt+letter are posted to the shown dialog the way the keyboard sends them, and wx and the
Windows dialog manager decide where they go. That is the only way to catch a field Tab skips, or an
access key that presses a different control from the one it labels: both looked right from the
labels and the creation order alone.

The dialog has to be shown and focused for that, so _dialog_keyboard_child.py shows it on a new Win32
desktop that is never switched to. Nothing appears on the user's screen, and a screen reader running
there hears nothing.

Needs what test_dictionary_dialog needs (wxPython, and NVDA_SOURCE naming NVDA's source folder).
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import unittest
from ctypes import wintypes

import test_dictionary_dialog as dialog_tests

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dialog_keyboard_child.py")


class STARTUPINFOW(ctypes.Structure):
	_fields_ = [
		("cb", wintypes.DWORD),
		("lpReserved", wintypes.LPWSTR),
		("lpDesktop", wintypes.LPWSTR),
		("lpTitle", wintypes.LPWSTR),
		("dwX", wintypes.DWORD),
		("dwY", wintypes.DWORD),
		("dwXSize", wintypes.DWORD),
		("dwYSize", wintypes.DWORD),
		("dwXCountChars", wintypes.DWORD),
		("dwYCountChars", wintypes.DWORD),
		("dwFillAttribute", wintypes.DWORD),
		("dwFlags", wintypes.DWORD),
		("wShowWindow", wintypes.WORD),
		("cbReserved2", wintypes.WORD),
		("lpReserved2", ctypes.c_void_p),
		("hStdInput", wintypes.HANDLE),
		("hStdOutput", wintypes.HANDLE),
		("hStdError", wintypes.HANDLE),
	]


class PROCESS_INFORMATION(ctypes.Structure):
	_fields_ = [
		("hProcess", wintypes.HANDLE),
		("hThread", wintypes.HANDLE),
		("dwProcessId", wintypes.DWORD),
		("dwThreadId", wintypes.DWORD),
	]


def run_on_a_desktop_of_its_own(arguments, timeout):
	"""Run a process on a new desktop that is never shown, wait for it and return its exit code.

	On timeout, the process started here is terminated through its own handle, and nothing else.
	"""
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	user32 = ctypes.WinDLL("user32", use_last_error=True)
	user32.CreateDesktopW.restype = wintypes.HANDLE
	user32.CreateDesktopW.argtypes = [
		wintypes.LPCWSTR,
		wintypes.LPCWSTR,
		ctypes.c_void_p,
		wintypes.DWORD,
		wintypes.DWORD,
		ctypes.c_void_p,
	]
	user32.CloseDesktop.argtypes = [wintypes.HANDLE]
	kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
	kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
	kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	generic_all, create_no_window, wait_object_0 = 0x10000000, 0x08000000, 0

	name = f"eloquence-dialog-test-{os.getpid()}"
	desktop = user32.CreateDesktopW(name, None, None, 0, generic_all, None)
	if not desktop:
		raise ctypes.WinError(ctypes.get_last_error())
	try:
		startup = STARTUPINFOW(cb=ctypes.sizeof(STARTUPINFOW), lpDesktop=f"WinSta0\\{name}")
		process = PROCESS_INFORMATION()
		command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
		if not kernel32.CreateProcessW(
			None,
			command,
			None,
			None,
			False,
			create_no_window,
			None,
			None,
			ctypes.byref(startup),
			ctypes.byref(process),
		):
			raise ctypes.WinError(ctypes.get_last_error())
		try:
			if kernel32.WaitForSingleObject(process.hProcess, timeout * 1000) != wait_object_0:
				kernel32.TerminateProcess(process.hProcess, 1)
				kernel32.WaitForSingleObject(process.hProcess, 5000)
				raise TimeoutError(f"{arguments} took more than {timeout} seconds")
			code = wintypes.DWORD()
			kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
			return code.value
		finally:
			kernel32.CloseHandle(process.hThread)
			kernel32.CloseHandle(process.hProcess)
	finally:
		user32.CloseDesktop(desktop)


@unittest.skipUnless(sys.platform == "win32", "Win32 desktops")
@unittest.skipUnless(dialog_tests.wx is not None and hasattr(dialog_tests.wx, "App"), "needs wxPython")
@unittest.skipUnless(os.path.isfile(dialog_tests._GUI_HELPER), "needs NVDA's source: set NVDA_SOURCE")
class KeyboardTests(unittest.TestCase):
	"""American English and Canadian French could not be built; German could."""

	@classmethod
	def setUpClass(cls):
		with tempfile.TemporaryDirectory() as directory:
			out = os.path.join(directory, "result.json")
			code = run_on_a_desktop_of_its_own([sys.executable, CHILD, out], timeout=120)
			with open(out + ".log", encoding="utf-8") as f:
				log = f.read()
			try:
				with open(out, encoding="utf-8") as f:
					cls.result = json.load(f)
			except OSError:
				raise AssertionError(f"the child wrote no result, exit code {code}:\n{log}") from None
		if "error" in cls.result:
			raise AssertionError(f"the child failed:\n{cls.result['error']}\n{log}")

	def test_tab_goes_from_the_language_to_its_problem(self):
		self.assertEqual(
			self.result["tab"]["frc"],
			[
				"languageChoice",
				"problemField",
				"stackList",
				"addFileButton",
				"openFolderButton",
				"dialectCheckBox",
				"sourcesList",
				"addSourceButton",
				"removeSourceButton",
				"updateButton",
				"OK",
				"Cancel",
				"languageChoice",
			],
		)

	def test_tab_skips_the_problem_field_when_there_is_no_problem(self):
		self.assertEqual(
			self.result["tab"]["deu"],
			[
				"languageChoice",
				"stackList",
				"addFileButton",
				"openFolderButton",
				"sourcesList",
				"addSourceButton",
				"removeSourceButton",
				"updateButton",
				"OK",
				"Cancel",
				"languageChoice",
			],
		)

	def test_alt_p_goes_to_the_problem(self):
		self.assertEqual(
			self.result["access_keys"]["frc"]["p"], {"control": "problemField", "result": "problemField"}
		)
		self.assertEqual(
			self.result["access_keys"]["enu"]["p"], {"control": "problemField", "result": "problemField"}
		)

	def test_alt_a_presses_the_dialect_checkbox_and_alt_f_add_file_in_canadian_french(self):
		keys = self.result["access_keys"]["frc"]
		self.assertEqual(keys["a"], {"control": "dialectCheckBox", "result": ["dialectCheckBox"]})
		self.assertEqual(keys["f"], {"control": "addFileButton", "result": ["addFileButton"]})

	def test_every_access_key_reaches_the_control_it_labels_in_every_language(self):
		wrong = {}
		for code, keys in self.result["access_keys"].items():
			for key, outcome in keys.items():
				if outcome["result"] not in (outcome["control"], [outcome["control"]]):
					wrong[f"{code} Alt+{key}"] = outcome
		self.assertEqual(sorted(self.result["access_keys"]), sorted(dialog_tests.library.LANGUAGES))
		self.assertEqual(wrong, {})

	def test_a_key_the_language_does_not_show_does_nothing(self):
		# Each press starts on Cancel, so staying there means nothing happened. A hidden control
		# keeps its access key unless its label goes too.
		self.assertEqual(self.result["unused_keys"]["deu"], {"a": "Cancel", "p": "Cancel"})
		acted = {
			f"{code} Alt+{key}": outcome
			for code, keys in self.result["unused_keys"].items()
			for key, outcome in keys.items()
			if outcome != "Cancel"
		}
		self.assertEqual(acted, {})


if __name__ == "__main__":
	unittest.main()
