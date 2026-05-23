# Eloquence NVDA Add-on

The Eloquence NVDA Add-on provides an NVDA synth driver for the 32-bit Eloquence/ECI engine, including voice control, speech sequencing, Eloquence Host Process communication, dictionary handling, and secure-screen support.

## Language

### NVDA Integration

**Synth Driver**:
The NVDA-facing component that receives Speech Sequences and exposes Eloquence voices, variants, settings, commands, and notifications through NVDA's synthesizer API.
_Avoid_: driver, synth, Eloquence driver, ECI driver

**Speech Sequence**:
The ordered input from NVDA containing text and speech commands that the Synth Driver must translate into Eloquence Engine operations while preserving NVDA's expected indexes, pauses, language changes, and prosody.
_Avoid_: utterance, text, queue item

**Speech Index**:
A marker carried through synthesis and audio playback so NVDA can observe progress through a Speech Sequence, including Say-All Reading advancement and final completion.
_Avoid_: index, marker, callback id, bookmark

**Speech Progress Notification**:
An NVDA notification emitted by the Synth Driver to report either a reached Speech Index or completion of the current Speech Generation.
_Avoid_: callback, index callback, done speaking

**Say-All Reading**:
NVDA's continuous-reading workflow where Speech Progress Notifications allow NVDA to keep advancing through content without user action.
_Avoid_: say all, continuous speech, read all

### Eloquence Runtime

**Eloquence Engine**:
The proprietary 32-bit speech synthesis runtime, including ECI.DLL and its voice data, that turns Eloquence commands and text into audio.
_Avoid_: DLL, ECI, host

**Eloquence Host Process**:
The 32-bit process that loads and controls the Eloquence Engine for 64-bit NVDA and communicates with the Synth Driver over local IPC.
_Avoid_: helper, host, server, bridge, worker, helper exe

**Host Channel**:
The local authenticated IPC connection between the Synth Driver side and the Eloquence Host Process.
_Avoid_: socket, RPC protocol, pipe, connection

**Host Command**:
A request sent from the Synth Driver side to the Eloquence Host Process to perform an Eloquence Engine operation such as initializing, adding text, setting a Voice Parameter, inserting a Speech Index, synthesizing, or stopping.
_Avoid_: RPC, message, IPC call

**Eloquence Text**:
Text prepared for the Eloquence Engine, including any inline Eloquence backquote commands for volume, pauses, dictionaries, phrase prediction, or voice behavior.
_Avoid_: raw text, speech text, xspeak text, marked-up text

**Text Preprocessing**:
The voice-aware transformation applied before Eloquence Text is sent to the Eloquence Engine, including crash-prevention rewrites and character normalization.
_Avoid_: sanitization, normalization, regex fixes

**Engine Encoding**:
The character encoding used when sending Eloquence Text bytes to the Eloquence Engine for the active Voice Identity.
_Avoid_: MBCS, language encoding, current lang

### Voice Model

**Voice Identity**:
The selected Eloquence voice as understood across NVDA, Eloquence language codes, Eloquence numeric voice IDs, and BCP-47 language tags.
_Avoid_: voice, language, locale

**Voice Code**:
The Eloquence language code for a voice, such as `enu`, `esp`, or `jpn`.
_Avoid_: language, lang, locale

**Voice ID**:
The numeric Eloquence parameter value that identifies a voice, such as `65536` for American English.
_Avoid_: voice, language id, param 9

**Language Tag**:
The BCP-47 language tag exposed to NVDA for a voice, such as `en-US`, `es-ES`, or `ja-JP`.
_Avoid_: locale, voice code, language code

**Voice Variant**:
An Eloquence timbre variant, such as Reed, Shelley, Bobby, or Rocko, copied onto the selected Voice Identity.
_Avoid_: voice, speaker, personality

**Voice Parameter**:
An Eloquence Engine setting that shapes the selected Voice Identity, such as rate, pitch, volume, inflection, head size, roughness, or breathiness.
_Avoid_: param, setting, prosody

**Saved Voice State**:
The persistent Voice Parameter state that should survive Prosody Overrides, Voice Identity changes, and Speech Generations without being replaced by temporary engine values.
_Avoid_: voice params, base values, defaults

**Prosody Override**:
A temporary Speech Sequence command that changes rate, pitch, or volume for part of the current speech without changing the user's saved Voice Parameters.
_Avoid_: Voice Parameter, prosody command, caps pitch

**Pause Policy**:
A rule for where the Synth Driver inserts Eloquence pause commands into Eloquence Text: never shorten pauses, shorten only the end of a text block, or shorten all punctuation pauses.
_Avoid_: pause mode, shorten pauses, dynamic pausing

### Audio

**Audio Chunk**:
A piece of synthesized audio from the Eloquence Host Process, optionally carrying a Speech Index and a final-completion flag for NVDA playback coordination.
_Avoid_: audio event, buffer, wave data

**Audio Playback Pipeline**:
The Synth Driver side flow that receives Audio Chunks, feeds synthesized audio to NVDA's wave player, and reports Speech Index and completion notifications back to NVDA.
_Avoid_: audio worker, WavePlayer, audio queue

**Speech Generation**:
One logical run of queued synthesis work; newer generations supersede older ones so stale Audio Chunks are ignored after stop or cancellation.
_Avoid_: sequence, session, request, generation id

### Dictionaries And Updates

**Pronunciation Dictionary**:
An Eloquence dictionary file loaded by the Eloquence Engine to influence how words, roots, or abbreviations are spoken.
_Avoid_: dictionary, IBM dictionary, custom dictionary

**Dictionary Source**:
A remote repository selected by the user as the origin for Pronunciation Dictionary updates.
_Avoid_: dictionary, source URL, provider

**Dictionary Update**:
A user-initiated refresh of local Pronunciation Dictionaries from the selected Dictionary Source.
_Avoid_: update, dictionary source update, merge

**Add-on Update**:
A user-approved update of the installed Eloquence NVDA Add-on package from a released add-on version.
_Avoid_: update, self-update, GitHub update, smart merge

**Add-on Package**:
The `.nvda-addon` build artifact that installs or updates the Eloquence NVDA Add-on.
_Avoid_: addon, release, zip

### Secure Screens

**Secure Screen**:
An elevated or pre-logon NVDA environment, such as sign-in, UAC, or start-up, where NVDA runs from system configuration and does not automatically copy add-on executables.
_Avoid_: logon screen, system config, secure mode

**System Configuration Copy**:
NVDA's copied add-on configuration used on Secure Screens; it may omit executable files unless the Eloquence Host Process is installed there separately.
_Avoid_: system config, secure copy, logon config

**Secure-Screen Host Installation**:
The user-initiated copy of the packaged Eloquence Host Process executable into the System Configuration Copy so Eloquence can run on Secure Screens.
_Avoid_: copy helper, helper installation, secure-screen fix

## Flagged Ambiguities

**Voice representation**:
The code uses several representations for the same selected voice: Voice Codes such as `enu`, Voice IDs such as `65536`, NVDA voice IDs, Language Tags such as `en-US`, and the current encoding language. Use **Voice Identity** for the domain concept, and name the specific representation only when it matters.

**ECI**:
ECI appears in filenames, API names, config keys, and the proprietary DLL, but domain docs should use **Eloquence Engine** unless they are specifically discussing the C API, `ECI.DLL`, ECI parameters, or compatibility with existing IBM/ETI assets.

**Host wording in UI**:
Use **Eloquence Host Process** in developer docs and detailed diagnostics. User-facing controls may use shorter wording such as "host" when paired with clear context like Secure Screens.

## Example Dialogue

Developer: Say-All Reading stops after a language change. Is that a voice bug?

Domain expert: First separate the concepts. The Speech Sequence contains a language change and Speech Index values. The Synth Driver should resolve the requested language to a Voice Identity, using the appropriate Voice Code, Voice ID, and Language Tag representations where needed.

Developer: The Voice ID changes correctly, but the next paragraph still does not advance.

Domain expert: Then check whether the Speech Index survives the full path. The Synth Driver turns preprocessed text into Eloquence Text, sends Host Commands over the Host Channel, and receives Audio Chunks from the Eloquence Host Process. The Audio Playback Pipeline must turn those chunks back into Speech Progress Notifications.

Developer: The audio plays, but no completion notification arrives.

Domain expert: Then the issue is not voice selection. It is a Speech Progress Notification problem, probably around the final Speech Index or stale Speech Generation handling.
