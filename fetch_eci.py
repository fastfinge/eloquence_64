#!/usr/bin/env python3
"""Download proprietary Eloquence files (ECI.DLL + western .SYN voice data).

These files are IBM proprietary and intentionally excluded from source control.
This script extracts them from the same upstream release artifact that the old
build system used.

Usage:
    python fetch_eci.py          # downloads if files are missing
    python fetch_eci.py --force  # re-downloads even if files exist
"""

import os
import sys
import shutil
import tempfile
import urllib.request
import zipfile

UPSTREAM_URL = (
    "https://github.com/pumper42nickel/eloquence_threshold"
    "/releases/download/v0.20210417.01/eloquence.nvda-addon"
)

DEST_DIR = os.path.join("addon", "synthDrivers", "eloquence")

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
    return all(
        os.path.exists(os.path.join(DEST_DIR, fname))
        for fname in PROPRIETARY_FILES.values()
    )


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


def main():
    force = "--force" in sys.argv
    if not force and files_present():
        print("All proprietary files already present. Use --force to re-download.")
        return
    fetch()


if __name__ == "__main__":
    main()
