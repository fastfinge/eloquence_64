import os
import shutil

import addonHandler
from logHandler import log


def _copy_user_dictionaries(source_dir, target_dir):
	if not os.path.isdir(source_dir):
		return 0

	copied = 0
	os.makedirs(target_dir, exist_ok=True)
	for filename in os.listdir(source_dir):
		if not filename.lower().endswith(".dic"):
			continue

		source_path = os.path.join(source_dir, filename)
		if not os.path.isfile(source_path):
			continue

		shutil.copy2(source_path, os.path.join(target_dir, filename))
		copied += 1
	return copied


def onInstall():
	addon = addonHandler.getCodeAddon()
	if os.path.normcase(os.path.normpath(addon.path)) == os.path.normcase(
		os.path.normpath(addon.installPath)
	):
		return

	source_dir = os.path.join(addon.installPath, "synthDrivers", "eloquence")
	target_dir = os.path.join(addon.path, "synthDrivers", "eloquence")
	copied = _copy_user_dictionaries(source_dir, target_dir)
	if copied:
		log.info("Preserved %s Eloquence dictionary file(s) for pending add-on install", copied)
