#!/usr/bin/env python3
"""Download the engine binaries the build needs but source control excludes.

Two engines, fetched for different reasons:

* The proprietary Eloquence Engine (``ECI.DLL`` + the western ``.SYN`` voice
  data) is IBM proprietary, and comes from the same upstream release artifact
  the old build system used.  This is what the Eloquence Host Process loads.
* openevv's 64-bit ``eci.dll`` is an MIT-licensed reimplementation exporting the
  same ECI names, which 64-bit NVDA can load in its own process.  Its newest
  release is resolved at build time, so a rebuild picks up a new openevv without
  any change here.  Note that openevv's own NOTICE excludes its ``lang/enus``
  data from that MIT grant, so this binary is treated like the proprietary files
  and kept out of source control too.

Usage:
    python fetch_eci.py                # downloads whatever is missing
    python fetch_eci.py --force        # re-downloads even if files exist
    python fetch_eci.py --openevv-only # skip the proprietary files
    python fetch_eci.py --no-openevv   # skip openevv
"""

import json
import os
import sys
import shutil
import struct
import tempfile
import urllib.request
import zipfile

UPSTREAM_URL = (
	"https://github.com/pumper42nickel/eloquence_threshold"
	"/releases/download/v0.20210417.01/eloquence.nvda-addon"
)

DEST_DIR = os.path.join("addon", "synthDrivers", "eloquence")

OPENEVV_REPO = "Mudb0y/openevv"
OPENEVV_DEST_DIR = os.path.join("addon", "synthDrivers", "openevv")
# Records which openevv release the tree currently holds.  Because the newest
# release is resolved at build time rather than pinned, without this a built
# add-on could not say which engine it shipped.
OPENEVV_VERSION_FILE = "openevv-version.txt"
# The asset carrying the Windows builds, matched by suffix so a version bump in
# the filename does not need a change here.
OPENEVV_ASSET_SUFFIX = "-windows-x86_64.zip"
# Where the 64-bit engine sits inside that asset.  openevv ships both bitnesses
# in folders that say which is which; we want the one NVDA's own process can load.
OPENEVV_DLL_MEMBER = "eci-x86_64/eci.dll"
# Upstream notices travel with the binary rather than being summarised here.
OPENEVV_EXTRA_MEMBERS = ("eci-x86_64/eci.ini", "LICENSE", "NOTICE", "README.md")

# The proprietary files we need from the upstream addon zip.
# Keys are paths inside the zip; values are destination filenames.
PROPRIETARY_FILES = {
	"synthDrivers/eloquence/ECI.DLL": "ECI.DLL",
	"synthDrivers/eloquence/DEU.SYN": "DEU.SYN",
	"synthDrivers/eloquence/ENG.SYN": "ENG.SYN",
	"synthDrivers/eloquence/ENU.SYN": "ENU.SYN",
	"synthDrivers/eloquence/ESM.SYN": "ESM.SYN",
	"synthDrivers/eloquence/ESP.SYN": "ESP.SYN",
	"synthDrivers/eloquence/FIN.SYN": "FIN.SYN",
	"synthDrivers/eloquence/FRA.SYN": "FRA.SYN",
	"synthDrivers/eloquence/FRC.SYN": "FRC.SYN",
	"synthDrivers/eloquence/ITA.SYN": "ITA.SYN",
	"synthDrivers/eloquence/PTB.SYN": "PTB.SYN",
}


def files_present():
	"""Check whether all proprietary files already exist."""
	return all(os.path.exists(os.path.join(DEST_DIR, fname)) for fname in PROPRIETARY_FILES.values())


def fetch():
	os.makedirs(DEST_DIR, exist_ok=True)

	print(f"Downloading upstream addon from:\n  {UPSTREAM_URL}")
	tmpfd, tmppath = tempfile.mkstemp(suffix=".nvda-addon")
	os.close(tmpfd)
	try:
		urllib.request.urlretrieve(UPSTREAM_URL, tmppath)
		print("Extracting proprietary files...")
		with zipfile.ZipFile(tmppath, "r") as zf:
			for zip_path, dest_name in PROPRIETARY_FILES.items():
				dest_path = os.path.join(DEST_DIR, dest_name)
				with zf.open(zip_path) as src, open(dest_path, "wb") as dst:
					shutil.copyfileobj(src, dst)
				print(f"  {dest_name}")
		print("Done.")
	finally:
		os.unlink(tmppath)


def _is_pe32_plus(path):
	"""True when *path* is a 64-bit PE image.

	openevv ships both bitnesses under similar names, and loading a 32-bit DLL
	into 64-bit NVDA fails with nothing but an OSError at synth start-up, so the
	architecture is checked here where the message can still be useful.
	"""
	try:
		with open(path, "rb") as f:
			data = f.read(0x400)
		if data[:2] != b"MZ":
			return False
		pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
		if data[pe_offset : pe_offset + 4] != b"PE\0\0":
			return False
		return struct.unpack_from("<H", data, pe_offset + 4)[0] == 0x8664
	except (OSError, struct.error, IndexError):
		return False


def _github_json(url):
	"""Read a GitHub API response, using GITHUB_TOKEN when one is available.

	CI runs this on every build and the unauthenticated limit is 60 requests an
	hour per IP, which a busy runner can exhaust.
	"""
	request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
	token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
	if token:
		request.add_header("Authorization", f"Bearer {token}")
	with urllib.request.urlopen(request) as response:
		return json.load(response)


def openevv_present():
	return os.path.exists(os.path.join(OPENEVV_DEST_DIR, "eci.dll"))


def installed_openevv_version():
	try:
		with open(os.path.join(OPENEVV_DEST_DIR, OPENEVV_VERSION_FILE), encoding="utf-8") as f:
			return f.read().strip()
	except OSError:
		return None


def fetch_openevv():
	"""Install the newest openevv Windows release's 64-bit engine."""
	print(f"Resolving the newest openevv release from {OPENEVV_REPO}...")
	release = _github_json(f"https://api.github.com/repos/{OPENEVV_REPO}/releases/latest")
	tag = release.get("tag_name") or "unknown"
	asset = next(
		(a for a in release.get("assets", ()) if a.get("name", "").endswith(OPENEVV_ASSET_SUFFIX)),
		None,
	)
	if asset is None:
		names = ", ".join(a.get("name", "?") for a in release.get("assets", ())) or "none"
		raise SystemExit(
			f"ERROR: openevv release {tag} has no *{OPENEVV_ASSET_SUFFIX} asset.\n"
			f"       Assets offered: {names}"
		)

	os.makedirs(OPENEVV_DEST_DIR, exist_ok=True)
	print(f"Downloading openevv {tag}:\n  {asset['browser_download_url']}")
	tmpfd, tmppath = tempfile.mkstemp(suffix=".zip")
	os.close(tmpfd)
	try:
		urllib.request.urlretrieve(asset["browser_download_url"], tmppath)
		with zipfile.ZipFile(tmppath, "r") as zf:
			members = {name.lower(): name for name in zf.namelist()}
			dll_member = members.get(OPENEVV_DLL_MEMBER.lower())
			if dll_member is None:
				raise SystemExit(
					f"ERROR: openevv {tag} does not contain {OPENEVV_DLL_MEMBER}.\n"
					"       The release layout changed; fetch_eci.py needs updating."
				)
			dest_dll = os.path.join(OPENEVV_DEST_DIR, "eci.dll")
			with zf.open(dll_member) as src, open(dest_dll, "wb") as dst:
				shutil.copyfileobj(src, dst)
			print("  eci.dll")
			for member in OPENEVV_EXTRA_MEMBERS:
				actual = members.get(member.lower())
				if actual is None:
					continue
				dest_name = os.path.basename(member)
				with zf.open(actual) as src, open(os.path.join(OPENEVV_DEST_DIR, dest_name), "wb") as dst:
					shutil.copyfileobj(src, dst)
				print(f"  {dest_name}")
	finally:
		os.unlink(tmppath)

	if not _is_pe32_plus(dest_dll):
		raise SystemExit(
			f"ERROR: the eci.dll from openevv {tag} is not a 64-bit PE image.\n"
			"       64-bit NVDA cannot load it; the release layout may have changed."
		)
	with open(os.path.join(OPENEVV_DEST_DIR, OPENEVV_VERSION_FILE), "w", encoding="utf-8") as f:
		f.write(tag + "\n")
	print(f"Done. openevv {tag} installed in {OPENEVV_DEST_DIR}.")


def main():
	force = "--force" in sys.argv
	want_proprietary = "--openevv-only" not in sys.argv
	want_openevv = "--no-openevv" not in sys.argv

	if want_proprietary:
		if not force and files_present():
			print("All proprietary files already present. Use --force to re-download.")
		else:
			fetch()

	if want_openevv:
		if not force and openevv_present():
			version = installed_openevv_version() or "unknown version"
			print(f"openevv already present ({version}). Use --force to re-download.")
		else:
			fetch_openevv()


if __name__ == "__main__":
	main()
