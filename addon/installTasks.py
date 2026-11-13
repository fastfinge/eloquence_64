"""Install tasks: back up the dictionary files an older version kept in the add-on folder.

Older versions kept Pronunciation Dictionaries in synthDrivers/eloquence inside the add-on, which
NVDA replaces on every update, so each update had to carry them across. Dictionaries now live in
NVDA's configuration instead, and this version never loads files from its own folder.

Two things are done here. The old files are copied, once, to eciDictionaries/legacy/eloquence in
the configuration, the place a dictionary editor looks for them (EloquenceDictionaryManager ADR
0002). And they are still carried into the new add-on folder, as older versions did, so that going
back to an older version finds them there. The first time the synth loads after an update, it
imports what no backup holds yet as the user's own files (_dictionary_library.import_legacy): the
backup on the first update, and on a later one any files an older version gone back to has made.

Kept deliberately small and self-contained: it runs from the incoming version before anything else
of it is importable, and an exception here would fail the whole installation.
"""

import os
import shutil

import addonHandler
import globalVars
from logHandler import log


def _back_up_old_dictionaries(source_dir, legacy_dir):
	"""Copy every .dic file in source_dir to legacy_dir. Returns how many were copied.

	Written once: if legacy_dir exists it is left exactly as it is, whatever the add-on folder holds.
	"""
	if os.path.exists(legacy_dir) or not os.path.isdir(source_dir):
		return 0
	names = [
		name
		for name in sorted(os.listdir(source_dir))
		if name.lower().endswith(".dic") and os.path.isfile(os.path.join(source_dir, name))
	]
	if not names:
		return 0
	# Copied beside the final folder and renamed, so a copy cut short never passes for a finished one.
	partial_dir = legacy_dir + ".partial"
	shutil.rmtree(partial_dir, ignore_errors=True)
	os.makedirs(partial_dir)
	for name in names:
		shutil.copy2(os.path.join(source_dir, name), os.path.join(partial_dir, name))
	os.rename(partial_dir, legacy_dir)
	return len(names)


def onInstall():
	try:
		addon = addonHandler.getCodeAddon()
		if os.path.normcase(os.path.normpath(addon.path)) == os.path.normcase(
			os.path.normpath(addon.installPath)
		):
			# A first installation: there is no older version to take anything from.
			return
		copied = _back_up_old_dictionaries(
			os.path.join(addon.installPath, "synthDrivers", "eloquence"),
			os.path.join(globalVars.appArgs.configPath, "eciDictionaries", "legacy", "eloquence"),
		)
		if copied:
			log.info("Backed up %s old Eloquence dictionary file(s) to the NVDA configuration", copied)
	except Exception:
		# Losing the backup must not cost the user the update itself.
		log.error("Could not back up the old Eloquence dictionary files", exc_info=True)
	_carry_old_dictionaries_forward()


def _carry_old_dictionaries_forward():
	"""Take the old files into the new add-on folder, as updates used to.

	NVDA deletes the outgoing add-on folder at the next restart, and the old files with it. In the
	new folder they are not loaded, but an older version installed over this one carries them on
	and uses them again, and the synth backs up from there any that no backup holds yet.
	"""
	try:
		addon = addonHandler.getCodeAddon()
		source_dir = os.path.join(addon.installPath, "synthDrivers", "eloquence")
		if not os.path.isdir(source_dir):
			return
		target_dir = os.path.join(addon.path, "synthDrivers", "eloquence")
		os.makedirs(target_dir, exist_ok=True)
		for name in os.listdir(source_dir):
			if name.lower().endswith(".dic") and os.path.isfile(os.path.join(source_dir, name)):
				shutil.copy2(os.path.join(source_dir, name), os.path.join(target_dir, name))
	except Exception:
		log.error("Could not carry the old Eloquence dictionary files forward", exc_info=True)
