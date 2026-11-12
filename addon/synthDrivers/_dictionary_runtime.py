"""Keeps the engine's dictionary files current while the Synth Driver runs.

The engine only ever loads the generated files in active/ (see _dictionary_library). This module is
the NVDA-facing glue around that: where NVDA's configuration is, which add-ons might provide
dictionary sets, and when to rebuild.

- prepare() runs before the engine starts. It compares time stamps and rebuilds only if something
  changed, so an ordinary start costs about a millisecond.
- While the synth runs, a watcher repeats that check every couple of seconds. That is how an edit
  saved in a dictionary editor reaches speech without restarting anything: the files are rebuilt
  and the synth is reloaded, but only when the generated files really changed.
- In secure mode nothing is written. The secure copy of the configuration already holds the active/
  folder NVDA copied there.

NVDA modules are imported inside the functions that need them, so the watcher can be tested alone.
"""

import logging
import os
import threading

from . import _dictionary_library as library

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
SYNTH_NAME = "eloquence"

# One build at a time: the watcher, the engine start and the dictionary dialog can all ask for one.
_build_lock = threading.RLock()
_watcher = None


def paths():
	import globalVars

	return library.DictionaryPaths(os.path.abspath(globalVars.appArgs.configPath))


def is_secure():
	import globalVars

	return bool(globalVars.appArgs.secure)


def provider_directories():
	"""Folders of the add-ons that are fully installed, whether or not they are enabled.

	A disabled add-on's dictionary data is still on disk and still meant to be used; one waiting to
	be installed or removed at the next restart is not.
	"""
	import addonHandler

	return [
		addon.path
		for addon in addonHandler.getAvailableAddons()
		if not addon.isPendingInstall and not addon.isPendingRemove
	]


def active_directory():
	"""The folder the engine loads dictionaries from, or "" when it cannot be worked out."""
	try:
		return paths().active
	except Exception:
		log.exception("Could not locate the dictionary folder; speaking without dictionaries")
		return ""


def refresh(force=False):
	"""Rebuild active/ if it is out of date. Returns True when a language's files changed."""
	if is_secure():
		return False
	with _build_lock:
		return library.refresh(paths(), provider_directories(), force=force)


def prepare():
	"""Make active/ current before the engine loads it. Never raises: speech matters more."""
	try:
		refresh()
	except Exception:
		log.exception("Could not rebuild the dictionaries; using the ones built before")


def reload_synth():
	"""Reload the Synth Driver so the engine reads the rebuilt files. Safe to call from any thread."""
	import wx

	def reload():
		import synthDriverHandler

		synth = synthDriverHandler.getSynth()
		if synth is not None and synth.name == SYNTH_NAME:
			synthDriverHandler.setSynth(synth.name)

	wx.CallAfter(reload)


def start_watching():
	global _watcher
	stop_watching()
	if is_secure():
		return
	_watcher = DictionaryWatcher(refresh, reload_synth)
	_watcher.start()


def stop_watching():
	global _watcher
	watcher, _watcher = _watcher, None
	if watcher is not None:
		watcher.stop()


class DictionaryWatcher:
	"""Calls refresh() every interval seconds and on_change() whenever it reports a change."""

	def __init__(self, refresh, on_change, interval=POLL_SECONDS):
		self._refresh = refresh
		self._on_change = on_change
		self._interval = interval
		self._stopped = threading.Event()
		self._last_error = None
		self._thread = threading.Thread(target=self._run, name="EloquenceDictionaryWatcher", daemon=True)

	def start(self):
		self._thread.start()

	def stop(self):
		self._stopped.set()
		# Not from the watcher's own thread: on_change may be what led here.
		if self._thread.is_alive() and threading.current_thread() is not self._thread:
			self._thread.join(timeout=5)

	def _run(self):
		while not self._stopped.wait(self._interval):
			try:
				changed = self._refresh()
				self._last_error = None
			except Exception as error:
				# The same failure every two seconds would bury the log, so say it once.
				if repr(error) != self._last_error:
					log.exception("Could not rebuild the dictionaries")
					self._last_error = repr(error)
				continue
			if changed and not self._stopped.is_set():
				self._on_change()
