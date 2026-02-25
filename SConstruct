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

# --- Validate required binaries -------------------------------------------

eci_dir = addonDir / "synthDrivers" / "eloquence"
host_exe = addonDir / "synthDrivers" / "eloquence_host32.exe"

required_proprietary = [eci_dir / "ECI.DLL"] + [
    eci_dir / f"{name}.SYN"
    for name in ("DEU", "ENG", "ENU", "ESM", "ESP", "FIN", "FRA", "FRC", "ITA", "PTB")
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
        f"ERROR: {host_exe} not found.\n"
        "Run `build_host.cmd` to compile the 32-bit host executable first.",
        file=sys.stderr,
    )
    Exit(1)

# --- Generate manifest ----------------------------------------------------

manifest = env.NVDAManifest(env.File(str(addonDir / "manifest.ini")), "manifest.ini.tpl")
env.Depends(manifest, "buildVars.py")

# --- Build addon bundle ----------------------------------------------------

addonFile = env.File("${addon_name}-${addon_version}.nvda-addon")

addon = env.NVDAAddon(addonFile, env.Dir(str(addonDir)))
env.Depends(addon, manifest)

# Depend on all source files in the addon tree so SCons rebuilds on changes.
for p in Path("addon").rglob("*"):
    if p.is_file():
        env.Depends(addon, str(p))

env.Default(addon)
env.Clean(addon, [".sconsign.dblite"])
