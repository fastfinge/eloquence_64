"""Helpers for installing Eloquence resources into NVDA's system configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os


HOST_EXECUTABLE = "eloquence_host32.exe"
ECI_INI = "ECI.INI"


@dataclass(frozen=True)
class SecureScreenCopyPlan:
	command: str
	source_host: str
	source_ini: str
	dest_host: str
	dest_ini: str


def read_manifest_version(addon_dir: str) -> str | None:
	manifest_path = os.path.join(addon_dir, "manifest.ini")
	try:
		with open(manifest_path, encoding="utf-8") as manifest:
			for line in manifest:
				key, separator, value = line.partition("=")
				if separator and key.strip().lower() == "version":
					return value.strip()
	except OSError:
		return None
	return None


def build_copy_plan(source_synth_dir: str, target_addon_dir: str) -> SecureScreenCopyPlan:
	source_host = os.path.normpath(os.path.join(source_synth_dir, HOST_EXECUTABLE))
	source_ini = os.path.normpath(os.path.join(source_synth_dir, "eloquence", ECI_INI))
	dest_synth_dir = os.path.normpath(os.path.join(target_addon_dir, "synthDrivers"))
	dest_eloquence_dir = os.path.normpath(os.path.join(dest_synth_dir, "eloquence"))
	dest_host = os.path.normpath(os.path.join(dest_synth_dir, HOST_EXECUTABLE))
	dest_ini = os.path.normpath(os.path.join(dest_eloquence_dir, ECI_INI))

	for required_path in (source_host, source_ini):
		if not os.path.exists(required_path):
			raise FileNotFoundError(required_path)

	command = (
		f'/d /c if not exist "{dest_synth_dir}" mkdir "{dest_synth_dir}"'
		f' && if not exist "{dest_eloquence_dir}" mkdir "{dest_eloquence_dir}"'
		f' && copy /y "{source_host}" "{dest_host}" >nul'
		f' && copy /y "{source_ini}" "{dest_ini}" >nul'
	)
	return SecureScreenCopyPlan(
		command=command,
		source_host=source_host,
		source_ini=source_ini,
		dest_host=dest_host,
		dest_ini=dest_ini,
	)
