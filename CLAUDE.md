# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an NVDA add-on that provides the Eloquence speech synthesizer for 64-bit NVDA. The project uses an Eloquence Host Process: a 32-bit process that loads and controls the legacy Eloquence Engine and communicates with the NVDA-facing Synth Driver via local IPC.

`CONTEXT.md` is the canonical glossary for this repository. Use those terms in new docs, issues, diagnostics, and architecture discussions. In particular, prefer "Eloquence Host Process" over "helper process", "Host Channel" over generic IPC wording when discussing the domain relationship, and "Speech Progress Notification" when discussing NVDA index/completion reporting.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the default five-label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain-doc layout. See `docs/agents/domain.md`.

## Build Commands

### Initial Setup
```bash
winget install --id astral-sh.uv
py install 3.13-32
python fetch_eci.py          # Downloads proprietary ECI.DLL + .SYN files, plus openevv's 64-bit eci.dll
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

This compiles the Eloquence Host Process with PyInstaller from the `uv` `host-build` dependency group (requires 32-bit Python 3.13) and copies the resulting tree to `addon/synthDrivers/eloquence_host32/`.

The build is `--onedir`, not `--onefile`. A onefile build re-extracts its whole archive to `%TEMP%` on every launch, costing 1.1-3.5s before the Host Channel even opens, and is the shape antivirus heuristics most often flag. The exe resolves `_internal/` relative to itself, so the tree must be kept together.

### Full Rebuild from Scratch
```bash
python fetch_eci.py          # One-time: get proprietary files
build_host.cmd               # If host changed
scons.bat
```

## Architecture

### Two engine backends

The add-on can reach an Eloquence Engine two ways, selected by a checkbox in the Eloquence settings category:

- **Eloquence Host Process (32-bit)**: `host_eloquence32.py`, compiled to the `eloquence_host32/` onedir tree. Loads the proprietary `ECI.DLL`, which is 32-bit only, and talks to NVDA over the **Host Channel**. Supports all 13 languages.
- **Direct Backend (in-process, 64-bit)**: loads openevv's 64-bit `eci.dll` inside NVDA itself. No process, no IPC. openevv v0.3 supports **US English only**.

Because openevv's language coverage is narrower, both backends can be live at once: a Voice Identity openevv reports is spoken in process, and anything else falls back to the Eloquence Host Process. The available set is read from `eciGetAvailableLanguages` at run time, never hardcoded, so an openevv release that adds a language starts serving it with no code change here. An empty or failed enumeration degrades to host-only rather than to silence.

The Eloquence Host Process is started lazily, so a user who only speaks a language openevv has never spawns it.

### Shared engine wrapper

`addon/synthDrivers/_eci_engine.py` is the single ctypes ECI wrapper, imported by **both** backends. Two rules keep one file serving two bitnesses, and `tests/test_eci_engine_sharing.py` asserts both:

- **No NVDA imports** - the Eloquence Host Process has none of them.
- **No relative imports** - the Synth Driver side imports it as `from . import _eci_engine`, while the frozen host imports it as a top-level `import _eci_engine`.

PyInstaller freezes a copy into the host executable via `--paths addon\synthDrivers` in `build_host.cmd`. Without that flag the host builds clean and then fails to import the engine on launch, because PyInstaller does not execute the runtime `sys.path.append`.

The Host Command protocol also lives there, in `EciDispatcher`, so neither backend can answer a command the other would answer differently. `HostController` is pure transport over the Host Channel.

Text handling is entirely on the Synth Driver side - `_eloquence_text.build()` hands either backend pre-encoded bytes - so the whole text pipeline is shared by both for free.

### Key Components

**`addon/synthDrivers/eloquence.py`**: Main NVDA synth driver implementing `SynthDriver`. Handles:
- Voice management and language switching (via `_resolve_voice_for_language`)
- Text preprocessing with language-specific fixes (crash prevention patterns)
- Speech command processing (IndexCommand, LangChangeCommand, BreakCommand, prosody)
- Dictionary settings GUI panel (`EloquenceSettingsPanel`)

**`addon/synthDrivers/_eloquence.py`**: Synth Driver side wrapper. Provides:
- `AudioPipeline`: the single Audio Playback Pipeline both backends feed - one queue, one `nvwave.WavePlayer`, one Speech Generation counter. There is deliberately only ever one, because two would mean two players competing for the output device and two counters deciding what to discard.
- `EngineClient`: base class holding the pipeline reference; subclasses supply only transport.
- `EloquenceHostClient`: manages subprocess lifecycle, Host Commands, and response handling.
- `DirectEngineClient`: drives `_eci_engine` in process for openevv.
- `backend_for_voice()` / `_activate()`: per-fragment routing between the two.
- `AudioWorker`: threading for audio playback.
- Public API functions (`initialize`, `speak`, `index`, `synth`, `stop`, etc.), which target whichever backend is active.

**`addon/synthDrivers/_eci_engine.py`**: the shared ECI wrapper (see above). `EciEngine` wraps the DLL, `EciDispatcher` executes Host Commands, and `available_languages()` reports what an engine can actually speak.

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
│       ├── _eloquence_updater.py       # Add-on Update: release check, download, install
│       ├── _dictionary_update.py       # Dictionary Update: download, merge rules, atomic writes
│       ├── _background_work.py         # Runs update work off NVDA's UI thread behind a progress dialog
│       ├── _eci_engine.py              # Shared ECI wrapper (both backends)
│       ├── _eloquence_ipc.py           # Host Channel helpers
│       ├── eloquence_host32/           # BUILT by build_host.cmd (gitignored)
│       │   ├── eloquence_host32.exe    # PyInstaller onedir launcher
│       │   └── _internal/              # Its runtime; the exe will not run without it
│       ├── openevv/                    # FETCHED by fetch_eci.py (gitignored)
│       │   ├── eci.dll                 # openevv 64-bit engine, loaded in process
│       │   ├── eci.ini                 # Needs no rewriting, unlike ECI.INI
│       │   └── openevv-version.txt     # Which release this build carries
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

**Watch out:** loading the proprietary engine rewrites `addon/synthDrivers/eloquence/ECI.INI` in place, replacing its `C:\dummy\` placeholders with absolute paths for the machine that ran it. That is correct in an installed add-on and wrong in a checkout, so run `git checkout -- addon/synthDrivers/eloquence/ECI.INI` after driving the host or the engine against the repo copy, and never commit the rewritten file. openevv needs none of this and opts out with `EngineConfig.rewrite_ini=False`.

Known openevv quirks, both measured against the proprietary engine rather than assumed:
- A bracket separated from its text by whitespace has its *name* spoken (`( x )` runs 2.83x longer than `(x)`; brackets 2.21x, braces 2.00x, double quotes 1.56x). Worked around in `_text_preprocessing.attach_spaced_brackets()`, applied only on the Direct Backend because the proprietary engine does not have the bug. Colons do **not** have it either, despite openevv-nvda 0.1.3 naming them.
- Returning 2 from the audio callback (ECI's abort) during `eciSynchronize` **segfaults the process**, where the proprietary engine tolerates it. Never cancel an in-flight utterance that way; advance the Speech Generation and stop the player instead, which is what the host path does anyway.
- **openevv has no usable stop.** `eciStop` on an *idle* engine corrupts it — and a cancellation always finds it idle, because `eciSynthesize` has already drained by then. The second such call leaves the next utterance silent; the third segfaults. During synthesis it is safe but aborts nothing (identical audio length with and without). `eciClearInput` is safe but does not discard queued text. So `EngineConfig.supports_eci_stop=False` for openevv and `eciStop` is never called there; the proprietary engine keeps it. Nothing is lost, because cancellation is carried by the Speech Generation and the player, not the engine. The symptom of getting this wrong is one `Eloquence skipped index callback N` ERROR per cancelled utterance, from a wedged engine that has stopped delivering index callbacks — that log line is deliberately loud and worth keeping.
- openevv **does** deliver every inserted Speech Index correctly when it is healthy; this was checked against the proprietary engine across seven insertion patterns, and the two agree exactly. An index that goes missing means the engine is wedged, not that indexes are unreliable.

When adding language support:
- Update `LANGS` in `addon/synthDrivers/_eci_engine.py` (both backends read it from there)
- Add BCP47 language tag mapping in `VOICE_BCP47`
- Add encoding to `LANG_ENCODINGS` if it's a multi-byte language
- Place `.syn` and ROM DLL files in `addon/synthDrivers/eloquence/`
