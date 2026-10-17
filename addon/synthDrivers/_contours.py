"""Eloquence Contours: post-synthesis PCM shaping from Nick6489/eloquence-contours.

The DLL and its Python adapter are built from the ``eloquence-contours``
submodule by ``build_contours.cmd`` into ``contours/``, which is gitignored like
``openevv/``.  The directory has no ``__init__.py`` on purpose: NVDA lists every
package under synthDrivers as a candidate synthesizer, so the adapter is loaded
from its path instead of imported.

What a contour does depends on the rate the engine produced, so the user picks a
*setting* and this module turns it into the module's numeric mode per input
rate.  The upstream module supports only 11025 and 16000 Hz, and is clear that
other rates must not be reinterpreted as one of those.  A setting with no mode at
the rate in use is played raw, outside the module.

Like ``_eci_engine``, nothing here imports NVDA, so it is testable as it stands.
"""

import importlib.util
import logging
import os
import threading
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

CONTOURS_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contours")
DLL_NAME = "eloquence_contours.dll"
ADAPTER_NAME = "eloquence_contours.py"
VERSION_FILE = "contours-version.txt"

RAW = "raw"
DEFAULT_SETTING = RAW

# Setting -> {engine input rate: module mode}.  Raw is absent from the module's
# side entirely: mode 0 is an exact passthrough anyway, and bypassing means a
# user who never picks a contour never loads the DLL.
#
# At 11025 Hz modes 1 and 2 are the same Classic Presence, so "presence" is one
# setting with a mode per rate rather than two settings that sound alike.
MODES = {
	RAW: {},
	"presence": {11025: 1, 16000: 2},
	"smooth": {16000: 1},
	"treble": {11025: 3},
}

_adapter = None
_adapter_failed = False
_adapter_lock = threading.Lock()


def mode_for(setting: str, rate: int) -> Optional[int]:
	"""The module mode for *setting* at *rate*, or None to play raw."""
	return MODES.get(setting, {}).get(rate)


def dll_path() -> str:
	return os.path.join(CONTOURS_DIRECTORY, DLL_NAME)


def available() -> bool:
	return os.path.isfile(dll_path()) and os.path.isfile(os.path.join(CONTOURS_DIRECTORY, ADAPTER_NAME))


def version() -> Optional[str]:
	"""Which eloquence-contours build is bundled, for the log."""
	try:
		with open(os.path.join(CONTOURS_DIRECTORY, VERSION_FILE), encoding="utf-8") as f:
			return f.read().strip() or None
	except OSError:
		return None


def _load_adapter():
	"""The upstream adapter module, loaded once, or None if it is not usable."""
	global _adapter, _adapter_failed
	with _adapter_lock:
		if _adapter is not None or _adapter_failed:
			return _adapter
		path = os.path.join(CONTOURS_DIRECTORY, ADAPTER_NAME)
		try:
			spec = importlib.util.spec_from_file_location("_eloquence_contours_adapter", path)
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
		except Exception:
			# Logged once: the pipeline asks again on every rate change.
			LOGGER.exception("Could not load the Eloquence Contours adapter from %s", path)
			_adapter_failed = True
			return None
		_adapter = module
		LOGGER.info("Eloquence Contours %s loaded", version() or "of unknown version")
		return _adapter


def create(setting: str, rate: int) -> Optional[Any]:
	"""A contour stream for *setting* at engine rate *rate*, or None for raw.

	None also covers a missing or broken DLL, which degrades to raw audio rather
	than to silence.  The caller owns the returned stream and must close it.
	"""
	mode = mode_for(setting, rate)
	if mode is None:
		return None
	if not available():
		LOGGER.warning("Contour %r selected but no contour DLL is bundled; playing raw audio", setting)
		return None
	adapter = _load_adapter()
	if adapter is None:
		return None
	try:
		return adapter.Contour(dll_path(), rate, mode)
	except Exception:
		LOGGER.exception("Could not create contour %r at %d Hz; playing raw audio", setting, rate)
		return None
