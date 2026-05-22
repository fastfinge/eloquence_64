# Eloquence for NVDA

Eloquence synthesizer add-on for NVDA with full 64-bit support.

## 64-bit support

The Eloquence DLL is 32-bit only. This add-on launches the Eloquence Host
Process (`eloquence_host32.exe`) to load the Eloquence Engine and stream audio
back to 64-bit NVDA. The integration is transparent — no additional Python
installation or manual steps are required.

For development scenarios where the prebuilt Eloquence Host Process executable is unavailable,
the `ELOQUENCE_HOST_COMMAND` environment variable can be set to the command that
launches a compatible 32-bit Python interpreter with `host_eloquence32.py`.

## Eloquence on secure screens (logon, UAC, start-up)

NVDA does **not** copy `*.exe` files to its Secure Screen configuration for
security reasons, so the Eloquence Host Process is missing after you click
**"Use currently saved settings during sign-in"** in NVDA's General settings.

The easiest way to fix this is the built-in button in the add-on:

1. Open **NVDA Settings > Eloquence**.
2. Click **"Copy Helper to System Config (for Logon Screen)"**.
3. Accept the UAC elevation prompt.

Eloquence should now load on Secure Screens. You only need to do this
once per add-on update.

## Troubleshooting

### "Could not load the synthesizer" after upgrading

If you upgraded from v16 (or earlier) to v17+ and NVDA reports **"Could not load
the synthesizer"** when you select Eloquence, the NVDA log most likely shows:

```
AttributeError: module 'synthDrivers._ipc' has no attribute 'create_listener'
```

This is caused by one or more of:

- Stale Python bytecode (`__pycache__`) left over from the previous version.
- A half-finished NVDA upgrade leaving an `Eloquence.delete` folder alongside
  the new install.
- The IBMTTS add-on also being installed — running both at the same time is
  not supported.

To recover, do a clean reinstall:

1. In NVDA, open **Tools → Manage Add-ons**, disable Eloquence, and restart
   NVDA so the disable takes effect.
2. In File Explorer, open `%APPDATA%\nvda\addons\` and delete the entire
   `Eloquence` folder. While you're there, delete any sibling folders whose
   names end in `.delete`.
3. If the IBMTTS add-on is installed, disable or remove it as well.
4. Restart NVDA, then install the latest Eloquence release fresh.
5. As a last resort, back up `%APPDATA%\nvda` and remove it to start with a
   clean NVDA config.

See [issue #101](https://github.com/fastfinge/eloquence_64/issues/101) for the
background.

## Building

### Prerequisites

- [Python Install Manager](https://www.python.org/ftp/python/pymanager/python-manager-25.0.msix) (`.msix`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 32-bit Python 3.13: `py install 3.13-32`

### Build steps

```bash
git submodule init && git submodule update   # fetch pronunciation dictionaries
python fetch_eci.py                          # one-time: download proprietary ECI.DLL + voice data
build_host.cmd                               # compile Eloquence Host Process (only needed if host_eloquence32.py changes)
scons.bat                                    # package everything into the .nvda-addon file
```

**Note:** `scons.bat` validates that proprietary files and the Eloquence Host
Process executable exist, but does not fetch or build them — steps 2 and 3 must
be done first.

### Development checks

```bash
runlint.bat      # run Ruff using the locked uv environment
runpytest.bat    # run pytest using the locked uv environment
```

Tooling dependencies are pinned in `pyproject.toml` and `uv.lock`, following
NVDA's current dependency-group pattern. The Eloquence Host Process build uses a
separate `.venv32` environment so PyInstaller can run under 32-bit Python
without replacing the normal development `.venv`.
