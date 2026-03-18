# Eloquence NVDA add-on SConstruct
# Generates manifest.ini from template + buildVars, then zips addon/ into .nvda-addon.

import os
import sys
from pathlib import Path

from SCons.Script import Environment, EnsurePythonVersion

EnsurePythonVersion(3, 8)

sys.dont_write_bytecode = True

import buildVars  # noqa: E402

env = Environment(ENV=os.environ, tools=["NVDATool"])
env.Append(addon_info=buildVars.addon_info)
env.Append(**buildVars.addon_info)

addonDir = Path("addon")

# --- Compile translations (.po -> .mo) -------------------------------------

import glob

# Find all .po files under addon/locale
poFiles = glob.glob("addon/locale/*/LC_MESSAGES/*.po")

moFiles = []

for po in poFiles:
	mo = po[:-3] + ".mo"
	moFile = env.Command(
		target=mo,
		source=po,
		action="msgfmt -o $TARGET $SOURCE",
	)
	moFiles.append(moFile)

# --- Validate required binaries -------------------------------------------

eci_dir = addonDir / "synthDrivers" / "eloquence"
host_exe = addonDir / "synthDrivers" / "eloquence_host32.exe"
upsampler_dll = addonDir / "synthDrivers" / "upsampler.dll"
required_proprietary = [eci_dir / "ECI.DLL"] + [
	eci_dir / f"{name}.SYN" for name in ("DEU", "ENG", "ENU", "ESM", "ESP", "FIN", "FRA", "FRC", "ITA", "PTB")
]

missing = [str(p) for p in required_proprietary if not p.exists()]
if missing:
	print(
		"ERROR: Missing proprietary Eloquence files:\n  "
		+ "\n  ".join(missing)
		+ "\n\nRun `python fetch_eci.py` to download them.",
		file=sys.stderr,
	)
	Exit(1)

if not host_exe.exists():
	print(
		f"ERROR: {host_exe} not found.\nRun `build_host.cmd` to compile the 32-bit host executable first.",
		file=sys.stderr,
	)
	Exit(1)

if not upsampler_dll.exists():
	print(
		f"ERROR: {upsampler_dll} not found.\nRun `build_upsampler.cmd` to compile the upsampler library first.",
		file=sys.stderr,
	)

	Exit(1)

# --- Generate manifest ----------------------------------------------------

manifest = env.NVDAManifest(env.File(str(addonDir / "manifest.ini")), "manifest.ini.tpl")
env.Depends(manifest, "buildVars.py")

# --- Build addon bundle ----------------------------------------------------

addonFile = env.File("${addon_name}-${addon_version}.nvda-addon")

addon = env.NVDAAddon(addonFile, env.Dir(str(addonDir)))
env.Depends(addon, moFiles)
env.Depends(addon, manifest)

# Depend on all source files in the addon tree so SCons rebuilds on changes.
for p in Path("addon").rglob("*"):
	if p.is_file():
		env.Depends(addon, str(p))

# --- Generate POT template --------------------------------------------------

potFile = Path(f"{env['addon_name']}.pot")

# Collect all Python sources inside addon/
pySources = [str(p) for p in addonDir.rglob("*.py")]
pySources = [str(p) for p in addonDir.rglob("*.py")]
pySources.append("buildVars.py")

pot = env.Command(
	target=str(potFile),
	source=pySources,
	action=(
		"xgettext "
		"--language=Python "
		"--keyword=_ "
		"--keyword=pgettext:1c,2 "
		"--from-code=UTF-8 "
		"--add-comments=Translators "
		"--package-name=${addon_name} "
		"--package-version=${addon_version} "
		"-o $TARGET $SOURCES"
	),
)

# Create explicit target: scons pot
env.Alias("pot", pot)

env.Default(addon)
env.Clean(addon, [".sconsign.dblite"])
