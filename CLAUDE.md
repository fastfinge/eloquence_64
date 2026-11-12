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
python fetch_eci.py          # Downloads proprietary ECI.DLL + .SYN files, plus openevv's and ReLoq C's 64-bit eci.dll
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

### Building Eloquence Contours
```bash
build_contours.cmd           # the pinned eloquence-contours submodule commit
build_contours.cmd --latest  # move the submodule to upstream master first
```

Builds the 64-bit `eloquence_contours.dll` from the `eloquence-contours` submodule (Nick6489/eloquence-contours, GPL-2.0-only Rust) with cargo, and copies it, upstream's Python adapter, its LICENSE and `contours-version.txt` into `addon/synthDrivers/contours/` (gitignored). Needs Rust with the `x86_64-pc-windows-msvc` target. Only the 64-bit DLL: contours run in NVDA's process, whichever engine made the audio.

Update policy mirrors openevv: a published release builds the pinned commit; every other CI build passes `--latest`; `.github/dependabot.yml` opens PRs bumping the pin.

### Full Rebuild from Scratch
```bash
git submodule update --init  # One-time: get eloquence-contours
python fetch_eci.py          # One-time: get proprietary files
build_host.cmd               # If host changed
build_contours.cmd           # If eloquence-contours changed
scons.bat
```

## Architecture

### Two engine backends

The add-on can reach an Eloquence Engine two ways, selected by the "64-bit engine" choice in the Eloquence settings category (`config.conf["eloquence"]["direct_engine"]`: `none`, `openevv` or `reloq`; an older config's `use_openevv` checkbox still counts as `openevv`):

- **Eloquence Host Process (32-bit)**: `host_eloquence32.py`, compiled to the `eloquence_host32/` onedir tree. Loads the proprietary `ECI.DLL`, which is 32-bit only, and talks to NVDA over the **Host Channel**. Supports all 13 languages.
- **Direct Backend (in-process, 64-bit)**: loads openevv's 64-bit `eci.dll` inside NVDA itself. No process, no IPC. openevv supports **US English only** so far. Published releases take openevv's newest release (v0.4 is the first with the `eciStop` fix); every other build, local or CI, takes openevv's newest CI artifact so an upstream change shows up before a release ships it. See `fetch_eci.py`.

Because openevv's language coverage is narrower, both backends can be live at once: a Voice Identity openevv reports is spoken in process, and anything else falls back to the Eloquence Host Process. The available set is read from `eciGetAvailableLanguages` at run time, never hardcoded, so an openevv release that adds a language starts serving it with no code change here. An empty or failed enumeration degrades to host-only rather than to silence.

The Eloquence Host Process is started lazily, so a user who only speaks a language openevv has never spawns it.

**ReLoq C** ([hozosch/Reloq-C](https://github.com/hozosch/Reloq-C)) is the Direct Backend's other engine, and covers all 13 languages, so with it chosen the host is never started. It is an independent C engine with a 64-bit `eci.dll`, and **from 1.1 it is driven exactly like openevv**, through the standard ECI calls alone, with no adapter. 1.0.1 needed one (`EngineConfig.reloq`, removed): no voice-parameter calls, one handle per language, UTF-8-only text, `eciInsertPitch` for the capital-letter raise, its own dictionary call, and fragments without `` `vv ``. Each of those was re-measured on 1.1.1 before the adapter went, and `tests/test_reloq_engine.py` pins them against the real DLL (64-bit Python):
- `eciSetVoiceParam`/`eciGetVoiceParam`/`eciCopyVoice` exist and work; preset 2 loads Shelly's values. `eciCopyVoice` resets rate to 50, which `_set_variant()` already re-applies.
- `eciSetParam(9)` switches language on a live handle, giving audio identical to a fresh handle for that language.
- `eciAddText` takes the per-language Engine Encodings: cp1252, cp932, gb18030 and cp949 text all produce audio identical to `eciAddTextUtf8`.
- `ECI_INPUT_TYPE` turns on backquote commands; the generic voice is on by default.
- `eciGetAvailableLanguages` reports 15 Voice IDs, every one in `LANGS` among them.
- A voice change between `eciAddText` calls applies from that point (hozosch/Reloq-C#6): NVDA's capital-letter raise is heard, and a mid-utterance rate or volume change takes effect there.
- An inline `` `vv ``/`` `vs `` opening a later fragment adds no pause (#5): `Alt+` then `` `vv100 a `` measures identical to `Alt+` then `a`.
- Volume works across its whole range in every language (#2), not only 80-100.
- `eciNewDict`/`eciLoadDict` load the `ENUmain`/`ENURoot`/`ENUabbr` files with 0, and the audio is identical to its own `eciSetDictionaryPath`.
- With dictionaries loaded, `eciSetDict` and a language change are instant from 1.1.2 (hozosch/Reloq-C#17). 1.1.1 rebuilt the tables on every call, 90-140 ms each in front of the next utterance. `_activate_dictionary()` still asks `eciGetDict` before setting, which costs nothing.

What remains different is harmless: it refuses parameters 41/42 with `-1` (the inline `` `da ``/`` `pp `` carry them), it has no wideband (so `_direct_initialize_payload()` sends it no `wideband`), and it has none of openevv's bracket-naming bug, so neither bracket rewrite is applied. All seven sample rates are accepted on a live handle.

One difference is not harmless: ReLoq C joins one `eciAddText` to the next with nothing between them, where the proprietary engine and openevv treat the call boundary as a separator. So `zqkey` then `` `p0 `` (the trailing pause, which the driver sends separately) was heard exactly as ``zqkey`p0``: a dictionary word at the end of an utterance was not looked up, and a phonetic string there ran 6.1 s instead of 0.95 s. `trailing_pause()` now opens with a space, which fixes it and is byte-identical on the other two engines. A text fragment after a Speech Index still opens with `` `pp ``, glued to the word before the index the same way; that is not worked around.

ReLoq C publishes no standalone DLL. `fetch_eci.py` takes `synthDrivers/reloqC/eci.dll` and `doc/licenses/*` out of its `.nvda-addon` into `addon/synthDrivers/reloq/` (gitignored), with `reloq-version.txt`. The update policy mirrors openevv's (`--reloq-release` on a published release), except that its CI only builds the add-on on its `codex/release*` branches with 14-day retention, so a development build falls back to its newest release when no artifact or token is available. It has been GPL-3.0-or-later since 1.1 (hozosch/Reloq-C#4); it stays out of source control because it is a fetched build output, like openevv's DLL. A checkout still holding a 1.0.x DLL needs `python fetch_eci.py --force`, since nothing here adapts to 1.0.x any more.

### Shared engine wrapper

`addon/synthDrivers/_eci_engine.py` is the single ctypes ECI wrapper, imported by **both** backends. Two rules keep one file serving two bitnesses, and `tests/test_eci_engine_sharing.py` asserts both:

- **No NVDA imports** - the Eloquence Host Process has none of them.
- **No relative imports** - the Synth Driver side imports it as `from . import _eci_engine`, while the frozen host imports it as a top-level `import _eci_engine`.

A third rule is about ctypes rather than packaging, and `tests/test_eci_signatures.py` asserts it: **every ECI entry point called here must have an entry in `ECI_SIGNATURES`**. An ECI handle is a pointer, and a call with no `argtypes` marshals it as a C int - silently truncating a handle above 2 GiB, and on 64-bit raising `int too long to convert` instead. A larger openevv build hit exactly that at `eciSetParam`, having been mapped above the boundary the previous build happened to sit below, with nothing changed on this side.

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
- The host serves the Host Channel single threaded, so a `synthesize` already sent when NVDA cancels would otherwise be spoken to nobody before the next utterance starts. The client publishes each new Speech Generation on the **Cancel Board** (`_eci_engine.CancelBoard`, one word of named shared memory, named in the `initialize` payload), and the host discards any `synthesize` whose generation is already stale (eciStop before it starts). Over 200 rapid cancels this cut next-utterance delays (>20 ms) from 21 to 4 and p90 keystroke-to-audio from 388 ms to 3.7 ms. It cannot stop an utterance already under way: measured on the proprietary engine, neither `eciStop` from another thread nor returning 2 from the callback shortens `eciSynchronize` (a 1400-character block took 300-340 ms either way). Never enabled on the Direct Backend.
- Speech Progress Notifications fire when audio completes playback

**Sample rate**: selectable, and the one thing the two backends can genuinely differ on. ECI parameter 5 picks the rate as an index, and what each engine accepts is probed at run time (`_eci_engine.available_sample_rates()`), never hardcoded — `eciSetParam` answers with the parameter's *previous* value, or `-1` when it refuses, which is the only way to ask. Measured:

| | 8000 | 11025 | 16000 | 22050 | 32000 | 44100 | 48000 |
|---|---|---|---|---|---|---|---|
| proprietary `ECI.DLL` | ✓ | ✓ (default) | — | — | — | — | — |
| openevv `main@7ee8c572` | ✓ | ✓ (default) | ✓ | ✓ | ✓ | ✓ | ✓ |

These are real rate changes, not slower speech: F0 on a sustained vowel measured against each claimed rate gives 103.0–103.9 Hz across all seven. Both engines also accept a change on a live handle, so no engine rebuild is needed.

The combo box offers the **union**, not the intersection. An engine that cannot do the chosen rate runs at its own best one at or below it (`choose_sample_rate()` — downwards, because running an engine faster than asked plays it slow and low), and the single Audio Playback Pipeline follows whichever backend is speaking. The cost is a brief gap when an utterance crosses backends, which only happens when a language openevv lacks appears mid-sentence.

Two things are load-bearing and easy to undo by accident:
- **The rate change travels through the pipeline queue** as a `RateChange` item, not applied where it is decided. Audio the outgoing engine already produced is still queued behind it, and reopening the device on the caller's thread would play that tail at the new rate. The worker `sync()`s before closing the old player, or the tail is swallowed instead.
- **`set_sample_rate()` queues the engine work onto the EloquenceSynthWorker** rather than doing it inline. Changing a rate replaces the engine's PCM output buffer, and the setting changes on NVDA's thread while the worker may be inside `synthesize()`.

**openevv's `eciWideband` (parameter 32) is always on for the Direct Backend**, and is not a user setting. Above 11025 Hz openevv still synthesises at 11025 and resamples, so without it every higher rate is just the 11025 Hz voice upsampled, with nothing above ~5.5 kHz. With it on, a second synthesiser supplies the top band. Measured on `main@c3a253fb`: energy above 6 kHz goes from about -83 dB to -28 dB at 22050/44100/48000, with identical duration; at 8000/11025 it changes nothing. It survives rate, language and voice changes, so it is set once at engine start (`EngineConfig.wideband`, set by `_direct_initialize_payload()`).
- **Setting it locks the rate until the engine has synthesised once**, and that once crashed NVDA. After parameter 32 is set, to anything, openevv silently drops every sample-rate change (`eciSetParam` still answers as if it took) and refuses every `eciSetOutputBuffer`. NVDA applies the configured rate while loading the driver's settings, before anything is spoken, so it always hit this. So `_enable_wideband()` runs one textless `eciSynthesize` + `eciSynchronize` straight after, which clears it and makes no sound. Language changes and `eciCopyVoice` don't bring the lock back; setting parameter 32 again does, so it is set exactly once.
- **`set_sample_rate()` adopts a new output buffer only once the engine has accepted it.** An engine that refuses keeps writing into its old buffer, and the old code dropped that one anyway. That freed memory openevv was still filling, and NVDA crashed natively inside `nvwave.feed` with nothing useful in the log. A refusal is now logged as a WARNING.

The proprietary engine has no such parameter and is never offered it. openevv v0.4 predates it and refuses it with `-1`, which is logged at INFO and otherwise harmless.

`OUTPUT_BUFFER_SAMPLES` is scaled with the rate by `output_buffer_samples()`, so a chunk stays ~100 ms instead of becoming 23 ms at 48 kHz; the scaling is by ratio so 11025 Hz still yields exactly 1100 and nothing moves at the default.

**Eloquence Contours** (`_contours.py`, the "Audio contour" Speech setting): post-synthesis PCM shaping, applied **once, in `AudioWorker`**, after stale-generation rejection and before `WavePlayer.feed`, so both backends get it and nothing is processed twice. This is upstream's integration contract (its README), and the parts easy to break are:
- `RateChange.rate` is the **engine input rate**. The worker recreates the contour at it and opens the player at `contour.output_rate` (classic 11025 Hz contours output 22050). Input rate and player rate are tracked separately.
- A contour change travels through the pipeline queue as `ContourChange`, so queued audio finishes under the contour it was produced with.
- The contour is reset when an accepted chunk's Speech Generation differs from the last, never per chunk or per index — that would break filter history and Say All continuity. Index-only events are never processed.
- Upstream supports only 11025 and 16000 Hz; `_contours.MODES` maps each setting to a mode per rate, and anything unmapped plays raw rather than being reinterpreted. Raw never loads the DLL.
- `contours/` has no `__init__.py`, because NVDA treats every package under `synthDrivers` as a synthesizer; the adapter is loaded by path.
- The unit-test venv is 32-bit, so the real-DLL tests in `tests/test_contours.py` skip there; run them with 64-bit Python (`python -m unittest tests.test_contours`).

**Voice Switching**:
- `LangChangeCommand` triggers voice changes via `_resolve_voice_for_language`
- Maintains `_defaultVoice` vs `curvoice` to track language overrides
- Falls back intelligently: exact match → primary language match → default voice

**Crash Prevention**:
- `english_fixes`, `spanish_fixes`, etc. contain regex patterns
- These prevent known crash-inducing text patterns from reaching the DLL
- Text preprocessing in `xspeakText()` applies fixes before synthesis

**Pronunciation dictionary values** reach the engine with their backquote codes live, whatever backquote voice tags says, and `tests/test_dictionary_value_speech.py` measures what follows on all three engines:
- A value that leaves spelling on (`` `ts1 abc ``, or a glued `` `ts1abc`ts0 ``, whose `` `ts0 `` the engines ignore) spelled every later utterance on the proprietary engine and openevv. So `_eloquence_text.build()` opens every fragment with `` `ts0 `` (every fragment, because an utterance can cross backends and each engine keeps its own spelling mode). Ordinary speech, and a sentence split across fragments, are byte-identical with and without it.
- `SynthDriver.previewEciPronunciation()` is Eloquence Dictionary Manager's optional preview method. It speaks one value through `speak()`'s pipeline with `Backquotes.DICTIONARY_CODES`, which keeps only the codes EDM's validator allows, where it allows them, and turns every other backquote into a space. The rule is applied to the text as given and again after preprocessing, which maps fullwidth lookalikes onto ASCII. The result is the entry's own audio, byte for byte, on all three engines.
- A value of 529 bytes or more kills the proprietary engine inside `eciLoadDict`, and nothing restarts the Eloquence Host Process: a bad default-language file stops the driver loading, and a bad file for a language switched to later leaves it silent in every language until NVDA restarts. Dictionary Update skips any translation over 512 bytes (`MAX_TRANSLATION_BYTES`); a hand-edited file is not checked.

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
│       ├── _contours.py                # Eloquence Contours: setting -> mode, adapter loading
│       ├── contours/                   # BUILT by build_contours.cmd (gitignored)
│       ├── _eloquence_ipc.py           # Host Channel helpers
│       ├── eloquence_host32/           # BUILT by build_host.cmd (gitignored)
│       │   ├── eloquence_host32.exe    # PyInstaller onedir launcher
│       │   └── _internal/              # Its runtime; the exe will not run without it
│       ├── reloq/                      # FETCHED by fetch_eci.py (gitignored)
│       │   ├── eci.dll                     # ReLoq C 64-bit engine, loaded in process
│       │   ├── *-LICENSE.txt, *-NOTICE.txt # Notices shipped with it upstream
│       │   └── reloq-version.txt           # Which ReLoq C build or tag this build carries
│       ├── openevv/                    # FETCHED by fetch_eci.py (gitignored)
│       │   ├── eci.dll                 # openevv 64-bit engine, loaded in process
│       │   ├── eci.ini                 # Needs no rewriting, unlike ECI.INI
│       │   └── openevv-version.txt     # Which openevv commit or tag this build carries
│       └── eloquence/
│           ├── ECI.DLL                 # PROPRIETARY (gitignored, via fetch_eci.py)
│           ├── ECI.INI                 # Eloquence config
│           ├── _multiprocessing.pyd    # 64-bit multiprocessing (gitignored)
│           ├── *.SYN                   # Voice data (western ones gitignored)
│           ├── chs.syn, jpn.syn, kor.syn           # Asian voice data (in repo)
│           ├── chsrom.dll, jpnrom.dll, korrom.dll  # Asian ROM DLLs (in repo)
│           └── multiprocessing/        # Bundled multiprocessing package
├── site_scons/                         # SCons build tools (NVDATool)
├── build_contours.cmd                  # Builds Eloquence Contours from the submodule with cargo
├── eloquence-contours/                 # Git submodule: Nick6489/eloquence-contours
└── .gitignore
```

### Proprietary Files

`ECI.DLL` and the 10 western `.SYN` files (DEU, ENG, ENU, ESM, ESP, FIN, FRA, FRC, ITA, PTB) are IBM proprietary and excluded from source control. Run `python fetch_eci.py` to download them from the upstream release artifact. The build will error with a clear message if they're missing.

## Common Development Patterns

When modifying synthesis behavior:
1. Check if changes belong in the Synth Driver side (`addon/synthDrivers/eloquence.py`) or Eloquence Host Process (`host_eloquence32.py`)
2. If adding new Host Commands, update both the Synth Driver side (`addon/synthDrivers/_eloquence.py`) and `HostController` handlers
3. Run `build_host.cmd` after changing `host_eloquence32.py` **or `_eci_engine.py`** - PyInstaller freezes a copy of the shared wrapper into the host, so the running host keeps the old one until it is rebuilt
4. Run `scons.bat` to package changes into the add-on

When debugging IPC issues:
- Check `eloquence-host.log` in the add-on directory
- Verify authentication key matches between the Synth Driver side and the Eloquence Host Process
- Ensure Speech Generations are properly advanced to prevent stale audio

**Watch out:** loading the proprietary engine rewrites `addon/synthDrivers/eloquence/ECI.INI` in place, replacing its `C:\dummy\` placeholders with absolute paths for the machine that ran it. That is correct in an installed add-on and wrong in a checkout, so run `git checkout -- addon/synthDrivers/eloquence/ECI.INI` after driving the host or the engine against the repo copy, and never commit the rewritten file. openevv needs none of this and opts out with `EngineConfig.rewrite_ini=False`.

Known openevv quirks, both measured against the proprietary engine rather than assumed:
- A bracket separated from its text has its *name* spoken (`( x )` runs 2.83x longer than `(x)`; brackets 2.21x, braces 2.00x, double quotes 1.56x). Worked around in `_text_preprocessing.attach_spaced_brackets()`, applied only on the Direct Backend because the proprietary engine does not have the bug. Colons do **not** have it either, despite openevv-nvda 0.1.3 naming them.
  - **Whitespace is not the only separator — a backquote command counts too**, and that bit once: the Pause Policy inserts `` `p0 `` before each punctuation mark, `)` included, *after* `attach_spaced_brackets()` has run, so `configuration)` became ``configuration `p0)`` and openevv named the bracket again. Heard as a doubled "right paren right parenthesis", because NVDA's own symbol processing supplies the words and *preserves* the character after them. So `_eloquence_text._insert_pause_commands()` skips a closing bracket that is up against its text. Only closing ones need it: `` `p0(letter`` measures 1.00x. A bracket with no text to belong to (character navigation) keeps its command and is still announced, which is correct there.
  - Both rewrites are gated on `BuildOptions.openevv_bracket_fixes`, set per fragment, because a mixed-language utterance can cross backends mid-sentence and the proprietary engine must not get either.
- Returning 2 from the audio callback (ECI's abort) during `eciSynchronize` **segfaults the process**, where the proprietary engine tolerates it. Never cancel an in-flight utterance that way; advance the Speech Generation and stop the player instead, which is what the host path does anyway.
- **`eciStop` was unusable on openevv v0.3, and is fine from v0.4.** On v0.3, `eciStop` on an *idle* engine corrupted it — and a cancellation always finds it idle, because `eciSynthesize` has already drained by then — leaving the next utterance silent and segfaulting on the third call ([openevv#35](https://github.com/Mudb0y/openevv/issues/35)). That is fixed in v0.4, which measures the same as the main@7ee8c572 CI build it was cut from; the `supports_eci_stop` flag that skipped the call is gone and there is one code path again. `eciClearInput` is still no substitute for discarding queued text: it is safe but does not actually discard it. Cancellation is carried by the Speech Generation and the player, not the engine, so `eciStop` aborting nothing mid-utterance costs nothing. The symptom of a wedged engine is one `Eloquence skipped index callback N` ERROR per cancelled utterance, from an engine that has stopped delivering index callbacks — that log line is deliberately loud and worth keeping.
- **Pronunciation dictionaries were unusable on v0.3, and work from v0.4.** `eciLoadDict` returned 6 for every file the proprietary engine accepts with 0, and calling it anyway left `eciDelete` raising an access violation, so `_direct_initialize_payload()` used to clear `dataDirectory`. Re-measured on main@7ee8c572 against the add-on's own ENUmain/ENURoot/ENUabbr: all three load with 0, and the audio matches the proprietary engine sample for sample (`omg` 10472 → 12485, `WWII` 22528 → 14707, `postfixes` 14432 → 14124, an unlisted control unchanged at 11165), over repeated loads and six engine lifecycles. So `dataDirectory` is passed straight through now and both backends honour dictionaries. The 2 MB root dictionary costs ~88 ms once at engine start, against the proprietary engine's ~108 ms.
- openevv **does** deliver every inserted Speech Index correctly when it is healthy; this was checked against the proprietary engine across seven insertion patterns, and the two agree exactly. An index that goes missing means the engine is wedged, not that indexes are unreliable.

When adding language support:
- Update `LANGS` in `addon/synthDrivers/_eci_engine.py` (both backends read it from there)
- Add BCP47 language tag mapping in `VOICE_BCP47`
- Add encoding to `LANG_ENCODINGS` if it's a multi-byte language
- Place `.syn` and ROM DLL files in `addon/synthDrivers/eloquence/`
