# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an NVDA add-on that provides the Eloquence speech synthesizer for 64-bit NVDA. The project uses an Eloquence Host Process: a 32-bit process that loads and controls the legacy Eloquence Engine and communicates with the NVDA-facing Synth Driver via local IPC.

`CONTEXT.md` is the canonical glossary for this repository. Use those terms in new docs, issues, diagnostics, and architecture discussions. In particular, prefer "Eloquence Host Process" over "helper process", "Host Channel" over generic IPC wording when discussing the domain relationship, and "Speech Progress Notification" when discussing NVDA index/completion reporting.

## Build Commands

### Initial Setup
```bash
winget install --id astral-sh.uv
py install 3.13-32
python fetch_eci.py          # Downloads proprietary ECI.DLL + .SYN files from upstream
```

### Building the Add-on
```bash
scons.bat
```

This produces `eloquence-12.nvda-addon` (version number comes from `buildVars.py`).

### Building the 32-bit Host (only needed if `host_eloquence32.py` changes)
```bash
build_host.cmd
```

This compiles the Eloquence Host Process executable with PyInstaller from the `uv` `host-build` dependency group (requires 32-bit Python 3.13) and copies it into `addon/synthDrivers/`.

### Full Rebuild from Scratch
```bash
python fetch_eci.py          # One-time: get proprietary files
build_host.cmd               # If host changed
scons.bat
```

## Architecture

### Inter-Process Architecture
The add-on uses a host-process architecture to bridge 64-bit and 32-bit code:

- **Synth Driver side (64-bit NVDA)**: `addon/synthDrivers/eloquence.py`, `_eloquence.py`, `_eloquence_ipc.py`
- **Eloquence Host Process (32-bit)**: `host_eloquence32.py` (compiled to `eloquence_host32.exe`)
- **Host Channel**: local authenticated IPC between the Synth Driver side and the Eloquence Host Process

The Synth Driver side spawns the Eloquence Host Process, which loads the Eloquence Engine (`eci.dll`) directly. Audio Chunks and synthesis events flow back to NVDA through the Host Channel.

### Key Components

**`addon/synthDrivers/eloquence.py`**: Main NVDA synth driver implementing `SynthDriver`. Handles:
- Voice management and language switching (via `_resolve_voice_for_language`)
- Text preprocessing with language-specific fixes (crash prevention patterns)
- Speech command processing (IndexCommand, LangChangeCommand, BreakCommand, prosody)
- Dictionary settings GUI panel (`EloquenceSettingsPanel`)

**`addon/synthDrivers/_eloquence.py`**: Synth Driver side wrapper. Provides:
- `EloquenceHostClient`: Manages subprocess lifecycle, Host Commands, and response handling
- `AudioWorker`: Threading for audio playback via `nvwave.WavePlayer`
- Public API functions (`initialize`, `speak`, `index`, `synth`, `stop`, etc.)
- Audio Playback Pipeline management and Speech Generation tracking for proper cancellation

**`host_eloquence32.py`**: Eloquence Host Process source (stays in repo root). Contains:
- `EloquenceRuntime`: Wraps the Eloquence DLL with ctypes
- `HostController`: Handles incoming Host Commands from the Synth Driver side
- DLL callback handling for audio data and index markers
- Dictionary loading and parameter management

**`addon/synthDrivers/_eloquence_ipc.py`**: Simple IPC helpers with length-prefixed pickle protocol.

### Critical Implementation Details

**Language Encoding**: Asian languages (Chinese, Japanese, Korean) require special encoding handling:
- Text must be encoded with language-specific codecs (`gb18030`, `cp932`, `cp949`)
- The `_current_lang` global tracks the active voice to select proper encoding
- Text normalization is skipped for multi-byte Asian characters

**Audio Pipeline**:
- The Eloquence Host Process sends Audio Chunks immediately via Host Channel events
- `AudioWorker` thread feeds chunks to `nvwave.WavePlayer`
- Speech Generations prevent stale audio after `stop()` calls
- Speech Progress Notifications fire when audio completes playback

**Voice Switching**:
- `LangChangeCommand` triggers voice changes via `_resolve_voice_for_language`
- Maintains `_defaultVoice` vs `curvoice` to track language overrides
- Falls back intelligently: exact match → primary language match → default voice

**Crash Prevention**:
- `english_fixes`, `spanish_fixes`, etc. contain regex patterns
- These prevent known crash-inducing text patterns from reaching the DLL
- Text preprocessing in `xspeakText()` applies fixes before synthesis

## Python Environment

This project requires **32-bit Python 3.13** for building the Eloquence Host Process executable. The Python Manager (`.msix`) is recommended for managing multiple Python versions side-by-side. SCons runs under any Python 3.8+.

## Directory Structure

```
eloquence_64/
├── SConstruct                          # SCons build script
├── buildVars.py                        # Addon metadata (name, version, etc.)
├── manifest.ini.tpl                    # Manifest template
├── fetch_eci.py                        # Downloads proprietary ECI.DLL + .SYN files
├── build_host.cmd                      # Compiles Eloquence Host Process via PyInstaller
├── host_eloquence32.py                 # Eloquence Host Process source (PyInstaller input)
├── _multiprocessing.pyd                # 32-bit multiprocessing (used by the Eloquence Host Process at dev time)
├── addon/                              # Addon source tree (becomes the .nvda-addon zip)
│   ├── manifest.ini                    # GENERATED by SCons from template
│   └── synthDrivers/
│       ├── eloquence.py                # Main synth driver
│       ├── _eloquence.py               # Synth Driver side wrapper
│       ├── _eloquence_updater.py       # Auto-updater
│       ├── _eloquence_ipc.py           # Host Channel helpers
│       ├── eloquence_host32.exe        # BUILT by build_host.cmd (gitignored)
│       └── eloquence/
│           ├── ECI.DLL                 # PROPRIETARY (gitignored, via fetch_eci.py)
│           ├── ECI.INI                 # Eloquence config
│           ├── _multiprocessing.pyd    # 64-bit multiprocessing (gitignored)
│           ├── *.SYN                   # Voice data (western ones gitignored)
│           ├── chs.syn, jpn.syn, kor.syn           # Asian voice data (in repo)
│           ├── chsrom.dll, jpnrom.dll, korrom.dll  # Asian ROM DLLs (in repo)
│           └── multiprocessing/        # Bundled multiprocessing package
├── site_scons/                         # SCons build tools (NVDATool)
├── AltIBMTTSDictionaries/              # Git submodule with pronunciation dictionaries
└── .gitignore
```

### Proprietary Files

`ECI.DLL` and the 10 western `.SYN` files (DEU, ENG, ENU, ESM, ESP, FIN, FRA, FRC, ITA, PTB) are IBM proprietary and excluded from source control. Run `python fetch_eci.py` to download them from the upstream release artifact. The build will error with a clear message if they're missing.

## Common Development Patterns

When modifying synthesis behavior:
1. Check if changes belong in the Synth Driver side (`addon/synthDrivers/eloquence.py`) or Eloquence Host Process (`host_eloquence32.py`)
2. If adding new Host Commands, update both the Synth Driver side (`addon/synthDrivers/_eloquence.py`) and `HostController` handlers
3. Run `build_host.cmd` after changing `host_eloquence32.py`
4. Run `scons.bat` to package changes into the add-on

When debugging IPC issues:
- Check `eloquence-host.log` in the add-on directory
- Verify authentication key matches between the Synth Driver side and the Eloquence Host Process
- Ensure Speech Generations are properly advanced to prevent stale audio

When adding language support:
- Update `LANGS` dictionary in both `addon/synthDrivers/eloquence.py` and `host_eloquence32.py`
- Add BCP47 language tag mapping in `VOICE_BCP47`
- Add encoding to `LANG_ENCODINGS` if it's a multi-byte language
- Place `.syn` and ROM DLL files in `addon/synthDrivers/eloquence/`
