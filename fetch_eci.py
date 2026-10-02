#!/usr/bin/env python3
"""Download the engine binaries the build needs but source control excludes.

Two engines, fetched for different reasons:

* The proprietary Eloquence Engine (``ECI.DLL`` + the western ``.SYN`` voice
  data) is IBM proprietary, and comes from the same upstream release artifact
  the old build system used.  This is what the Eloquence Host Process loads.
* openevv's 64-bit ``eci.dll`` is an MIT-licensed reimplementation exporting the
  same ECI names, which 64-bit NVDA can load in its own process.  Note that
  openevv's own NOTICE excludes its ``lang/enus`` data from that MIT grant, so
  this binary is treated like the proprietary files and kept out of source
  control too.

openevv is taken from its CI rather than from a release, because the newest
release (v0.3, 21 August) predates the fix for Mudb0y/openevv#35 -- an ``eciStop``
on an idle engine that wedged it and then crashed the process.  Measured against
the ``build`` artifact for main@7ee8c572: v0.3 produces no audio on the third
cancellation, that build runs all ten rounds clean.  This is meant to be
temporary: ``--openevv-release`` still takes the newest release, and should
become the default again once one ships the fix.  Taking the head of main does
mean a rebuild can pick up an openevv commit nobody here has tried, which is why
the commit is recorded in ``openevv-version.txt``.

Downloading a CI artifact needs a GitHub token, unlike a release asset; see
_github_token().

Usage:
    python fetch_eci.py                   # downloads whatever is missing
    python fetch_eci.py --force           # re-downloads even if files exist
    python fetch_eci.py --openevv-only    # skip the proprietary files
    python fetch_eci.py --no-openevv      # skip openevv
    python fetch_eci.py --openevv-release # take the newest release, not CI
"""

import json
import os
import subprocess
import sys
import shutil
import struct
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

UPSTREAM_URL = (
	"https://github.com/pumper42nickel/eloquence_threshold"
	"/releases/download/v0.20210417.01/eloquence.nvda-addon"
)

DEST_DIR = os.path.join("addon", "synthDrivers", "eloquence")

OPENEVV_REPO = "Mudb0y/openevv"
OPENEVV_DEST_DIR = os.path.join("addon", "synthDrivers", "openevv")
# Records which openevv build the tree currently holds -- a release tag, or
# "<branch>@<short sha> (build run <id>)" for a CI artifact.  Because nothing is
# pinned, without this a built add-on could not say which engine it shipped.
OPENEVV_VERSION_FILE = "openevv-version.txt"
# The asset carrying the Windows builds, matched by suffix so a version bump in
# the filename does not need a change here.
OPENEVV_ASSET_SUFFIX = "-windows-x86_64.zip"
# Where the 64-bit engine sits inside that asset.  openevv ships both bitnesses
# in folders that say which is which; we want the one NVDA's own process can load.
OPENEVV_DLL_MEMBER = "eci-x86_64/eci.dll"
# Upstream notices travel with the binary rather than being summarised here.
OPENEVV_EXTRA_MEMBERS = ("eci-x86_64/eci.ini", "LICENSE", "NOTICE", "README.md")

# The CI build the engine normally comes from, and where things sit inside its
# artifact.  The artifact is flat -- both bitnesses as eci.dll and eci32.dll --
# so the 64-bit one is named exactly rather than matched by folder.
OPENEVV_CI_WORKFLOW = "build.yml"
OPENEVV_CI_BRANCH = "main"
OPENEVV_CI_ARTIFACT = "openevv-windows-x86_64"
OPENEVV_CI_DLL_MEMBER = "eci.dll"
OPENEVV_CI_MEMBERS = ("eci.ini",)
# The artifact carries no notices, so these come from the tree at the same
# commit.  They are not optional: the add-on ships openevv's LICENSE and NOTICE
# beside its binary, and tests/test_addon_packaging.py checks that it does.
OPENEVV_CI_NOTICES = ("LICENSE", "NOTICE", "README.md")

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


def _github_token():
	"""A GitHub token, or None.

	Reading the API unauthenticated is allowed but limited to 60 requests an hour
	per IP, which a busy runner can exhaust.  Downloading a CI artifact is not
	allowed at all without one: that endpoint answers 401 even for a public
	repository, and the token needs actions:read on the repository it belongs to.
	Release assets need nothing.

	The `gh` fallback is here because that is what a developer on this project
	already has set up, where an exported GITHUB_TOKEN is not.
	"""
	token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
	if token:
		return token
	gh = shutil.which("gh")
	if gh is None:
		return None
	try:
		result = subprocess.run(
			[gh, "auth", "token"], capture_output=True, text=True, timeout=30, check=False
		)
	except OSError:
		return None
	return result.stdout.strip() or None


def _github_json(url):
	"""Read a GitHub API response, authenticated when a token can be found."""
	request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
	token = _github_token()
	if token:
		request.add_header("Authorization", f"Bearer {token}")
	with urllib.request.urlopen(request) as response:
		return json.load(response)


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
	"""Strip Authorization when a redirect leaves the host it was meant for.

	The artifact endpoint answers 302 to a signed storage URL, which rejects a
	bearer token with 401 rather than ignoring it.  urllib carries the header
	across the redirect where curl and requests do not, so it is dropped here.
	"""

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
		if new_request is not None:
			same_host = urllib.parse.urlparse(newurl).netloc == urllib.parse.urlparse(
				req.full_url
			).netloc
			if not same_host:
				new_request.headers.pop("Authorization", None)
		return new_request


def _download(url, dest_path, token=None, accept=None):
	headers = {}
	if accept:
		headers["Accept"] = accept
	if token:
		headers["Authorization"] = f"Bearer {token}"
	request = urllib.request.Request(url, headers=headers)
	opener = urllib.request.build_opener(_DropAuthOnRedirect)
	with opener.open(request) as response, open(dest_path, "wb") as dst:
		shutil.copyfileobj(response, dst)


def _extract(zf, member, dest_dir, dest_name=None):
	"""Copy one zip member out, returning its destination path."""
	dest_name = dest_name or os.path.basename(member)
	dest_path = os.path.join(dest_dir, dest_name)
	with zf.open(member) as src, open(dest_path, "wb") as dst:
		shutil.copyfileobj(src, dst)
	print(f"  {dest_name}")
	return dest_path


def _verify_openevv_dll(dest_dll, source):
	if not _is_pe32_plus(dest_dll):
		raise SystemExit(
			f"ERROR: the eci.dll from openevv {source} is not a 64-bit PE image.\n"
			"       64-bit NVDA cannot load it; the layout may have changed."
		)


def _record_openevv_version(version):
	with open(os.path.join(OPENEVV_DEST_DIR, OPENEVV_VERSION_FILE), "w", encoding="utf-8") as f:
		f.write(version + "\n")
	print(f"Done. openevv {version} installed in {OPENEVV_DEST_DIR}.")


def openevv_present():
	return os.path.exists(os.path.join(OPENEVV_DEST_DIR, "eci.dll"))


def installed_openevv_version():
	try:
		with open(os.path.join(OPENEVV_DEST_DIR, OPENEVV_VERSION_FILE), encoding="utf-8") as f:
			return f.read().strip()
	except OSError:
		return None


def fetch_openevv_release():
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
			dest_dll = _extract(zf, dll_member, OPENEVV_DEST_DIR, "eci.dll")
			for member in OPENEVV_EXTRA_MEMBERS:
				actual = members.get(member.lower())
				if actual is not None:
					_extract(zf, actual, OPENEVV_DEST_DIR)
	finally:
		os.unlink(tmppath)

	_verify_openevv_dll(dest_dll, tag)
	_record_openevv_version(tag)


def _newest_openevv_ci_artifact():
	"""The newest successful CI run carrying a usable engine artifact, and it.

	A page of runs is sorted here rather than asking for one with ``per_page=1``:
	that was observed to answer with a week-old run, which would have installed an
	older engine with nothing to show that it had.  Runs are then tried
	newest-first because an artifact expires where a release asset does not, so
	the newest run is not necessarily the newest one still downloadable.
	"""
	runs = _github_json(
		f"https://api.github.com/repos/{OPENEVV_REPO}/actions/workflows/{OPENEVV_CI_WORKFLOW}"
		f"/runs?branch={OPENEVV_CI_BRANCH}&status=success&per_page=30"
	).get("workflow_runs", ())
	if not runs:
		raise SystemExit(
			f"ERROR: {OPENEVV_REPO} has no successful {OPENEVV_CI_WORKFLOW} run on "
			f"{OPENEVV_CI_BRANCH}.\n"
			"       Use --openevv-release to take the newest release instead."
		)
	runs.sort(key=lambda r: (r.get("run_number") or 0, r.get("created_at") or ""), reverse=True)

	skipped = []
	for run in runs:
		artifacts = _github_json(
			f"https://api.github.com/repos/{OPENEVV_REPO}/actions/runs/{run['id']}/artifacts"
		).get("artifacts", ())
		artifact = next((a for a in artifacts if a.get("name") == OPENEVV_CI_ARTIFACT), None)
		if artifact is None:
			skipped.append(f"{run['id']} (no {OPENEVV_CI_ARTIFACT} artifact)")
		elif artifact.get("expired"):
			skipped.append(f"{run['id']} (artifact expired)")
		else:
			for note in skipped:
				print(f"  skipping run {note}")
			return run, artifact
	raise SystemExit(
		f"ERROR: none of the last {len(runs)} successful openevv build runs still has a\n"
		f"       downloadable {OPENEVV_CI_ARTIFACT} artifact.\n"
		"       Use --openevv-release to take the newest release instead."
	)


def fetch_openevv_ci():
	"""Install the 64-bit engine from openevv's newest successful CI build.

	The default; why is in this module's docstring.
	"""
	print(f"Resolving the newest successful {OPENEVV_CI_WORKFLOW} run on {OPENEVV_REPO}...")
	run, artifact = _newest_openevv_ci_artifact()
	run_id = run["id"]
	head_sha = run.get("head_sha") or "unknown"
	version = f"{OPENEVV_CI_BRANCH}@{head_sha[:8]} (build run {run_id})"

	token = _github_token()
	if token is None:
		raise SystemExit(
			"ERROR: downloading a CI artifact needs a GitHub token, unlike a release.\n"
			"       Set GITHUB_TOKEN or GH_TOKEN, or run `gh auth login`.\n"
			"       Or use --openevv-release to take the newest release instead."
		)

	os.makedirs(OPENEVV_DEST_DIR, exist_ok=True)
	print(f"Downloading openevv {version}:\n  {artifact['archive_download_url']}")
	tmpfd, tmppath = tempfile.mkstemp(suffix=".zip")
	os.close(tmpfd)
	try:
		try:
			_download(
				artifact["archive_download_url"],
				tmppath,
				token=token,
				accept="application/vnd.github+json",
			)
		except urllib.error.HTTPError as error:
			if error.code not in (401, 403, 404):
				raise
			raise SystemExit(
				f"ERROR: GitHub refused the artifact download ({error.code}).\n"
				f"       The token needs actions:read on {OPENEVV_REPO}; a workflow's own\n"
				"       GITHUB_TOKEN does not carry that across repositories.\n"
				"       Or use --openevv-release to take the newest release instead."
			) from error
		with zipfile.ZipFile(tmppath, "r") as zf:
			members = {name.lower(): name for name in zf.namelist()}
			dll_member = members.get(OPENEVV_CI_DLL_MEMBER.lower())
			if dll_member is None:
				raise SystemExit(
					f"ERROR: openevv build run {run_id} does not contain "
					f"{OPENEVV_CI_DLL_MEMBER}.\n"
					"       The artifact layout changed; fetch_eci.py needs updating."
				)
			dest_dll = _extract(zf, dll_member, OPENEVV_DEST_DIR, "eci.dll")
			for member in OPENEVV_CI_MEMBERS:
				actual = members.get(member.lower())
				if actual is not None:
					_extract(zf, actual, OPENEVV_DEST_DIR)
	finally:
		os.unlink(tmppath)

	_verify_openevv_dll(dest_dll, version)

	# The notices are not in the artifact, so they come from the tree at the very
	# commit that built it rather than from main, which may have moved on.
	raw_base = f"https://raw.githubusercontent.com/{OPENEVV_REPO}/{head_sha}"
	for name in OPENEVV_CI_NOTICES:
		try:
			_download(f"{raw_base}/{name}", os.path.join(OPENEVV_DEST_DIR, name))
		except urllib.error.HTTPError as error:
			raise SystemExit(
				f"ERROR: could not fetch openevv's {name} for {head_sha[:8]} "
				f"({error.code}).\n"
				"       The add-on must ship it beside the binary; fetch_eci.py needs\n"
				"       updating if upstream moved or renamed it."
			) from error
		print(f"  {name}")

	_record_openevv_version(version)


def main():
	force = "--force" in sys.argv
	want_proprietary = "--openevv-only" not in sys.argv
	want_openevv = "--no-openevv" not in sys.argv
	from_release = "--openevv-release" in sys.argv

	if want_proprietary:
		if not force and files_present():
			print("All proprietary files already present. Use --force to re-download.")
		else:
			fetch()

	if want_openevv:
		if not force and openevv_present():
			version = installed_openevv_version() or "unknown version"
			print(f"openevv already present ({version}). Use --force to re-download.")
		elif from_release:
			fetch_openevv_release()
		else:
			fetch_openevv_ci()


if __name__ == "__main__":
	main()
