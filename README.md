# Eloquence for NVDA

Eloquence synthesizer add-on for NVDA with full 64-bit support.

## 64-bit support

The Eloquence DLL is 32-bit only. This add-on launches the Eloquence Host
Process (`eloquence_host32/eloquence_host32.exe`) to load the Eloquence Engine and stream audio
back to 64-bit NVDA. The integration is transparent — no additional Python
installation or manual steps are required.

For development scenarios where the prebuilt Eloquence Host Process executable is unavailable,
the `ELOQUENCE_HOST_COMMAND` environment variable can be set to the command that
launches a compatible 32-bit Python interpreter with `host_eloquence32.py`.

## Audio contours

The **Audio contour** setting in NVDA's Speech settings shapes Eloquence's audio
after it is synthesised, using
[Eloquence Contours](https://github.com/Nick6489/eloquence-contours). It applies
to both engines alike. The contours are tied to the sample rate the engine runs at:

| Contour | 11025 Hz | 16000 Hz |
| --- | --- | --- |
| Raw (default) | unchanged | unchanged |
| Presence | Classic Presence, played at 22050 Hz | native Presence |
| Presence treble | hozosch v19.1 treble, played at 22050 Hz | — |
| Smooth | — | native Smooth |

At any other sample rate, or with a contour that has no version for the rate in
use, audio plays raw.

## Pronunciation dictionaries

Eloquence can read words differently using pronunciation dictionaries. You choose
which ones each language uses in NVDA's settings, under **Eloquence**, with the
**Manage dictionaries...** button.

In that dialog you pick a language and get its list of dictionaries, each with a
checkbox:

- **Dictionaries from an installed add-on**, such as a dictionary editor that
  ships a community set.
- **Downloaded dictionaries**. The dialog lists the GitHub repositories to
  download from. Two community projects are listed to begin with, and you can
  add others with **Add source...**. **Update Dictionaries** downloads them
  all. Downloading never switches anything on.
- **Your own files**, added with **Add file...**. The add-on keeps its own copy,
  so you can move or delete the original.
- **Personal entries from a dictionary editor**, when you have any. They are
  always last.

Where two dictionaries have the same word, the lower one in the list wins. Use
**Move up** and **Move down** to change the order. Personal entries win over
everything.

British English, Latin American Spanish, Canadian French and Chinese have one
more checkbox, such as **Also use American English dictionaries**. It is on to
begin with, and the language's own dictionaries are applied on top.

A new installation has nothing switched on. Changes take effect when you choose
**OK**. An entry saved in a dictionary editor is picked up within a couple of
seconds while Eloquence is speaking, with no restart.

If a dictionary has entries that break the engine's rules, they are still used,
because most of them work in practice. The dialog tells you how many there are
and lists them in a field you can read and copy, so you can fix the ones that
do not take effect.

### Coming from an older version

Older versions kept dictionary files inside the add-on's own folder and merged
downloads into them. When you update:

- Those files are copied once to `eciDictionaries\legacy\eloquence` in your NVDA
  configuration folder, as a backup that is never changed afterwards.
- They appear in the dialog as your own files, marked as imported. The ones
  Eloquence was using are switched on, so speech stays as it was.
- To start clean, switch the imported files off and choose the dictionaries you
  want.

Two things can sound different afterwards. Entries after a blank line in an old
file are now used, where the engine used to stop reading at the blank line. And
a dialect now gets its main language's entries as well as its own, where it used
to get only its own file when it had one.

### Going back to an older version

You can install an older version over this one. Updates still carry the old
dictionary files along inside the add-on, where this version ignores them, so the
older version finds the files it had before you updated. It knows nothing about
the dialog, so changes you made there are not in those files.

Your choices in the dialog and the files they use stay in your NVDA configuration
folder, and apply again when you update. If you changed dictionaries in the older
version, the next update backs up its files to `eloquenceDictionaries\legacy` and
adds them to the dialog as imported files, below the earlier ones, so what you had
in the older version wins. Switch off any rows you no longer want.

### Where it is stored

Everything is stored in your NVDA configuration folder, in `eloquenceDictionaries`
and `eciDictionaries`, so it survives add-on updates. To use dictionaries on the
logon screen, copy your settings to it as usual with NVDA's **Use currently saved
settings during sign-in** button.

Your choices are saved in `eloquenceDictionaries\dictionaries.json`. If that file
cannot be read, Eloquence keeps the dictionaries it built last, never saves over
the file, and says why in the NVDA log. A damaged file is copied to
`dictionaries.unreadable.json` beside it. The dialog will not open until the file
can be read again.

## Traditional Chinese Script Conversion

When the Mandarin Chinese voice is selected, Text Preprocessing applies Script
Conversion before text is sent to the Eloquence Engine. Traditional Chinese
text is read via Traditional→Simplified conversion with the Mandarin Chinese
voice.

This is not zh-TW support, a Traditional Chinese voice, or Cantonese support.
The add-on's Chinese Voice Identity still advertises only `zh-CN`.

Known limitations:

- Hong Kong (`zh-HK`) users get Mandarin readings, not Cantonese.
- Colloquial written-Cantonese characters, such as `嘅`, `哋`, and `咗`,
  are unpronounceable.
- A zh-TW-localized NVDA install does not auto-select the Chinese voice on
  first run. Users need to pick the Chinese voice once manually.

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
- [Rust](https://rustup.rs/) with the `x86_64-pc-windows-msvc` target, for Eloquence Contours

### Build steps

```bash
git submodule init && git submodule update   # fetch submodules (Eloquence Contours)
python fetch_eci.py                          # one-time: download proprietary ECI.DLL + voice data
build_host.cmd                               # compile Eloquence Host Process (only needed if host_eloquence32.py changes)
build_contours.cmd                           # build Eloquence Contours (add --latest to take upstream master)
scons.bat                                    # package everything into the .nvda-addon file
```

**Note:** `scons.bat` validates that proprietary files, the Eloquence Host
Process executable and the contour DLL exist, but does not fetch or build them —
the earlier steps must be done first.

The `eloquence-contours` submodule is pinned, and published releases build
that commit. Every other CI build runs `build_contours.cmd --latest` against
upstream master, and Dependabot opens a pull request to move the pin whenever
upstream changes.

### Development checks

```bash
runlint.bat      # run Ruff using the locked uv environment
runpytest.bat    # run pytest using the locked uv environment
```

Tooling dependencies are pinned in `pyproject.toml` and `uv.lock`, following
NVDA's current dependency-group pattern. The Eloquence Host Process build uses a
separate `.venv32` environment so PyInstaller can run under 32-bit Python
without replacing the normal development `.venv`.
