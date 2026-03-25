# Eloquence for NVDA

Eloquence synthesizer add-on for NVDA with full 64-bit support.

## 64-bit support

The Eloquence DLL is 32-bit only. This add-on launches a lightweight helper
process (`eloquence_host32.exe`) that hosts the DLL and streams audio back to
64-bit NVDA over IPC. The integration is transparent — no additional Python
installation or manual steps are required.

For development scenarios where the prebuilt helper executable is unavailable,
the `ELOQUENCE_HOST_COMMAND` environment variable can be set to the command that
launches a compatible 32-bit Python interpreter with `host_eloquence32.py`.

## Eloquence on secure screens (logon, UAC, start-up)

NVDA does **not** copy `*.exe` files to its secure-screen configuration for
security reasons, so `eloquence_host32.exe` is missing after you click
**"Use currently saved settings during sign-in"** in NVDA's General settings.

The easiest way to fix this is the built-in button in the add-on:

1. Open **NVDA Settings > Eloquence**.
2. Click **"Copy Helper to System Config (for Logon Screen)"**.
3. Accept the UAC elevation prompt.

Eloquence should now load on secure and logon screens. You only need to do this
once per add-on update.

## Building

### Prerequisites

- [Python Install Manager](https://www.python.org/ftp/python/pymanager/python-manager-25.0.msix) (`.msix`)
- 32-bit Python 3.13: `py install 3.13-32`
- SCons: `pip install scons`
- PyInstaller for 32-bit: `py -3.13-32 -m pip install pyinstaller`

### Build steps

```bash
git submodule init && git submodule update   # fetch pronunciation dictionaries
python fetch_eci.py                          # one-time: download proprietary ECI.DLL + voice data
build_host.cmd                               # compile 32-bit host exe (only needed if host_eloquence32.py changes)
scons                                        # package everything into the .nvda-addon file
```

**Note:** `scons` validates that proprietary files and the host exe exist, but does not fetch or build them — steps 2 and 3 must be done first.

