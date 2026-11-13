"""Shows the Manage dictionaries dialog and presses keys in it, for test_dictionary_dialog_keyboard.

python _dialog_keyboard_child.py <result.json>

Run it only on a desktop of its own, as test_dictionary_dialog_keyboard does: the dialog is shown and
takes focus. Each key is posted to the focused window as the keyboard delivers it, so wx's message
loop and the Windows dialog manager decide what it does. Writes what happened to <result.json>, and
anything logged to <result.json>.log.

Every button and the dialect checkbox only record that they were pressed: what they would really do
(open a file dialog or a folder, download, save) never runs.
"""

import builtins
import ctypes
import importlib
import json
import os
import sys
import tempfile
import traceback

OUT = sys.argv[1]
sys.stderr = open(OUT + ".log", "w", encoding="utf-8")
sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_dictionary_dialog as harness  # noqa: E402  # This file's own folder is sys.path[0].
from test_dictionary_dialog import wx  # noqa: E402

from addon.synthDrivers import _dictionary_library as library  # noqa: E402
from addon.synthDrivers import _dictionary_manager as manager_module  # noqa: E402

user32 = ctypes.windll.user32
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetFocus.restype = ctypes.c_void_p
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
VK_TAB = 0x09
# Posted keys and what they cause are handled before a timer fires, so this only paces the steps.
STEP_MS = 10


def post(*messages):
	hwnd = user32.GetFocus()
	for message, wparam, lparam in messages:
		user32.PostMessageW(hwnd, message, wparam, lparam)


def tab():
	post((WM_KEYDOWN, VK_TAB, 0x000F0001), (WM_KEYUP, VK_TAB, 0xC00F0001))


def alt(letter):
	"""Alt+letter, down and up; the message loop's TranslateMessage makes the WM_SYSCHAR."""
	vk = ord(letter.upper())
	scan = user32.MapVirtualKeyW(vk, 0) << 16
	post((WM_SYSKEYDOWN, vk, 0x20000001 | scan), (WM_SYSKEYUP, vk, 0xE0000001 | scan))


def drive(dialog, result):
	"""The keys pressed, as a generator: each yield lets the dialog handle what was just posted."""
	names = {
		dialog.FindWindow(wx.ID_OK).GetHandle(): "OK",
		dialog.FindWindow(wx.ID_CANCEL).GetHandle(): "Cancel",
	}
	for attribute, value in vars(dialog).items():
		if isinstance(value, wx.Window) and value is not dialog:
			names[value.GetHandle()] = attribute
	name = lambda window: names.get(window.GetHandle(), repr(window)) if window else None  # noqa: E731
	pressed = []
	for control in harness.controls(dialog):
		if isinstance(control, (wx.Button, wx.CheckBox)):
			# Bound last, so called first; not skipping stops the dialog's own handler.
			event = wx.EVT_BUTTON if isinstance(control, wx.Button) else wx.EVT_CHECKBOX
			control.Bind(event, lambda evt, control=control: pressed.append(name(control)))

	def choose(code):
		dialog.languageChoice.SetSelection(list(library.LANGUAGES).index(code))
		dialog.languageChoice.GetEventHandler().ProcessEvent(
			wx.CommandEvent(wx.wxEVT_CHOICE, dialog.languageChoice.GetId())
		)

	def tab_round(code):
		choose(code)
		dialog.languageChoice.SetFocus()
		yield
		order = [name(wx.Window.FindFocus())]
		for _ in range(30):
			tab()
			yield
			order.append(name(wx.Window.FindFocus()))
			if order[-1] == order[0]:
				break
		result["tab"][code] = order

	def press(letter):
		"""Alt+letter from Cancel, which has no access key: what it pressed, or else where focus went."""
		dialog.FindWindow(wx.ID_CANCEL).SetFocus()
		yield
		pressed.clear()
		alt(letter)
		yield
		result["last"] = list(pressed) or name(wx.Window.FindFocus())

	result["tab"] = {}
	yield from tab_round("frc")
	yield from tab_round("deu")
	result["access_keys"], result["unused_keys"] = {}, {}
	for code in library.LANGUAGES:
		choose(code)
		keys = {}
		for control in harness.controls(dialog):
			key = harness.access_key(control) if control.IsShown() else None
			# A label's key goes to the control after it.
			target = control.GetNextSibling() if isinstance(control, wx.StaticText) else control
			if key and target.IsShown() and target.IsEnabled():
				yield from press(key)
				keys[key] = {"control": name(target), "result": result.pop("last")}
		result["access_keys"][code] = keys
	# The keys other languages use, such as the dialect checkbox's, after every language has been
	# shown: a hidden control could still be keeping one.
	every_key = {key for keys in result["access_keys"].values() for key in keys}
	for code in library.LANGUAGES:
		choose(code)
		unused = {}
		for key in sorted(every_key - set(result["access_keys"][code])):
			yield from press(key)
			unused[key] = result.pop("last")
		result["unused_keys"][code] = unused


def main():
	result = {}
	app = wx.App(False)
	builtins._ = lambda text: text
	sys.modules.update(harness.nvda_gui_modules(lambda *args, **kwargs: None))
	dialog_module = importlib.import_module("addon.synthDrivers._dictionary_dialog")
	with tempfile.TemporaryDirectory() as root:
		paths = library.DictionaryPaths(root)
		harness.fail_build(paths, "enu", "frc")
		dialog = dialog_module.ManageDictionariesDialog(
			None, manager_module.DictionaryManager(paths, []), "frc"
		)
		steps = drive(dialog, result)

		def step():
			try:
				next(steps)
			except StopIteration:
				pass
			except Exception:
				result["error"] = traceback.format_exc()
			else:
				wx.CallLater(STEP_MS, step)
				return
			dialog.Destroy()
			app.ExitMainLoop()

		dialog.Show()
		dialog.Raise()
		wx.CallLater(200, step)
		app.MainLoop()
	return result


if __name__ == "__main__":
	try:
		result = main()
	except Exception:
		result = {"error": traceback.format_exc()}
	with open(OUT, "w", encoding="utf-8") as f:
		json.dump(result, f, indent=1)
