"""Client side helper for communicating with the 32-bit Eloquence host."""
from __future__ import annotations

import itertools
import logging
import os
import sys 
sys.path.append(os.path.join(os.path.dirname(__file__), "eloquence"))
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Listener
from typing import Any, Dict, Optional, Sequence, Tuple

import config
import nvwave
from buildVersion import version_year

LOGGER = logging.getLogger(__name__)

HOST_EXECUTABLE = "eloquence_host32.exe"
HOST_SCRIPT = "host_eloquence32.py"
AUTH_KEY_BYTES = 16


# Audio handling -----------------------------------------------------------------
class AudioWorker(threading.Thread):
    _CHANNELS = 1
    _BITS_PER_SAMPLE = 16
    _SAMPLE_RATE = 11025

    def __init__(self, player: nvwave.WavePlayer, queue: "queue.Queue[Optional[AudioChunk]]", client: "EloquenceHostClient"):
        super().__init__(daemon=True)
        self._player = player
        self._queue = queue
        self._client = client
        self._running = True
        self._stopping = False
        self._player_lock = threading.RLock()
        self._idle_timer: Optional[threading.Timer] = None
        self._idle_lock = threading.Lock()

    def run(self) -> None:
        while self._running:
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            data, index, is_final, seq = chunk
            if seq < self._client._sequence:
                self._queue.task_done()
                continue
            if not data:
                if index is not None:
                    self._invoke_index_callback(index)
                if is_final:
                    self._schedule_idle()
                self._queue.task_done()
                continue
            on_done = None
            if index is not None:

                def _callback(i=index):
                    self._invoke_index_callback(i)

                on_done = _callback
            wrapped_on_done = self._make_on_done(on_done, is_final)
            # Early exit if stopping - avoids unnecessary lock acquisition
            if self._stopping:
                self._queue.task_done()
                continue
            # Feed directly - blocks if buffer is full
            try:
                with self._player_lock:
                    if not self._stopping:
                        if self._player:
                            self._player.feed(data, onDone=wrapped_on_done)
            except FileNotFoundError:
                LOGGER.warning("Sound device not found during feed")
            except Exception:
                LOGGER.exception("WavePlayer feed failed")
            self._queue.task_done()

    def stop(self) -> None:
        self._stopping = True
        self._running = False
        # Cancel any pending idle timer
        with self._idle_lock:
            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None
        self._queue.put(None)

    def _schedule_idle(self) -> None:
        """Schedule idle() to be called after a delay."""
        if self._stopping:
            return
        with self._idle_lock:
            # Cancel any existing timer
            if self._idle_timer:
                self._idle_timer.cancel()
            # Schedule new idle
            self._idle_timer = threading.Timer(0.3, self._idle_player)
            self._idle_timer.start()
            LOGGER.debug("Idle scheduled for 0.3s from now")

    def _idle_player(self) -> None:
        """Called by timer after speech is complete."""
        # Do NOT call idle() - it blocks waiting for buffer to drain
        # Just notify done speaking
        if not self._stopping:
            self._invoke_index_callback(None)

    def _make_on_done(self, callback, is_final: bool):
        def _on_done() -> None:
            try:
                if callback:
                    callback()
            except Exception:
                LOGGER.exception("Index callback failed")
            if is_final:
                self._schedule_idle()
        return _on_done

    def _invoke_index_callback(self, value: Optional[int]) -> None:
        if onIndexReached:
            try:
                onIndexReached(value)
            except Exception:
                LOGGER.exception("Index callback failed")


AudioChunk = Tuple[bytes, Optional[int], bool, int]

# RPC client ---------------------------------------------------------------------
@dataclass
class HostProcess:
    process: subprocess.Popen
    connection: Any
    listener: Listener


class EloquenceHostClient:
    def __init__(self) -> None:
        self._host: Optional[HostProcess] = None
        self._pending: Dict[int, threading.Event] = {}
        self._responses: Dict[int, Dict[str, Any]] = {}
        self._receiver: Optional[threading.Thread] = None
        self._id_counter = itertools.count(1)
        self._audio_queue: "queue.Queue[Optional[AudioChunk]]" = queue.Queue()
        self._player: Optional[nvwave.WavePlayer] = None
        self._audio_worker: Optional[AudioWorker] = None
        self._running = threading.Event()
        self._command_lock = threading.Lock()
        self._stop_lock = threading.RLock()
        self._sequence = 0
        self._current_seq = 0
        self._speaking = False

    # ------------------------------------------------------------------
    def ensure_started(self) -> None:
        if self._host:
            return
        addon_dir = os.path.abspath(os.path.dirname(__file__))
        authkey = os.urandom(AUTH_KEY_BYTES)
        listener = Listener(("127.0.0.1", 0), authkey=authkey)
        address = listener.address
        port = address[1]
        cmd = list(self._resolve_host_executable(addon_dir))
        cmd.extend(["--address", f"127.0.0.1:{port}", "--authkey", authkey.hex(), "--log-dir", addon_dir])
        LOGGER.info("Launching Eloquence host: %s", cmd)
        proc = subprocess.Popen(cmd, cwd=addon_dir)
        conn = listener.accept()
        self._host = HostProcess(process=proc, connection=conn, listener=listener)
        self._receiver = threading.Thread(target=self._receiver_loop, daemon=True)
        self._receiver.start()

    def _resolve_host_executable(self, addon_dir: str) -> Sequence[str]:
        override = os.environ.get("ELOQUENCE_HOST_COMMAND")
        if override:
            return shlex.split(override)
        exe_path = os.path.join(addon_dir, HOST_EXECUTABLE)
        if os.path.exists(exe_path):
            return [exe_path]
        script_path = os.path.join(addon_dir, HOST_SCRIPT)
        if os.path.exists(script_path):
            raise RuntimeError(
                "Eloquence helper executable was not found."
                " Provide a 32-bit host via the ELOQUENCE_HOST_COMMAND environment"
                " variable when developing the add-on."
            )
        raise RuntimeError("Eloquence helper resources missing from add-on package")

    # ------------------------------------------------------------------
    def initialize_audio(self) -> None:
        if self._player:
            return
        if version_year >= 2025:
            device = config.conf["audio"]["outputDevice"]
            ducking = True if config.conf["audio"]["audioDuckingMode"] else False
            player = nvwave.WavePlayer(1, 11025, 16, outputDevice=device, wantDucking=ducking)
        else:
            device = config.conf["speech"]["outputDevice"]
            nvwave.WavePlayer.MIN_BUFFER_MS = 1500
            player = nvwave.WavePlayer(1, 11025, 16, outputDevice=device, buffered=True)
        self._player = player
        self._audio_worker = AudioWorker(player, self._audio_queue, self)
        self._audio_worker.start()

    # ------------------------------------------------------------------
    def _receiver_loop(self) -> None:
        connection = self._host.connection if self._host else None
        if connection is None:
            return
        while True:
            try:
                message = connection.recv()
            except (EOFError, ConnectionAbortedError, OSError):
                LOGGER.info("Host connection closed")
                for msg_id, event in list(self._pending.items()):
                    self._responses[msg_id] = {"error": "connectionClosed"}
                    event.set()
                self._pending.clear()
                break
            except Exception:
                LOGGER.exception("Unexpected error in receiver loop")
                for msg_id, event in list(self._pending.items()):
                    self._responses[msg_id] = {"error": "receiverException"}
                    event.set()
                self._pending.clear()
                break
            msg_type = message.get("type")
            if msg_type == "response":
                msg_id = message["id"]
                self._responses[msg_id] = message
                event = self._pending.pop(msg_id, None)
                if event:
                    event.set()
            elif msg_type == "event":
                self._handle_event(message["event"], message.get("payload", {}))
            else:
                LOGGER.warning("Unknown message type %s", msg_type)

    def _handle_event(self, event: str, payload: Dict[str, Any]) -> None:
        if event == "audio":
            data = payload.get("data", b"")
            index = payload.get("index")
            is_final = bool(payload.get("final", False))
            seq = self._current_seq
            self._audio_queue.put((data, index, is_final, seq))
        elif event == "stopped":
            # Don't call player.stop() from this thread to avoid race conditions
            # The stop() method will handle player cleanup properly
            LOGGER.debug("Host reported stopped event")
            self._speaking = False
        else:
            LOGGER.debug("Unhandled host event %s", event)

    # ------------------------------------------------------------------
    def send_command(self, command: str, **payload: Any) -> Dict[str, Any]:
        if not self._host:
            raise RuntimeError("Host not started")
        with self._command_lock:
            msg_id = next(self._id_counter)
            event = threading.Event()
            self._pending[msg_id] = event
            try:
                self._host.connection.send({"type": "command", "id": msg_id, "command": command, "payload": payload})
            except Exception:
                # Clean up if send fails
                self._pending.pop(msg_id, None)
                raise
            # Wait for response with timeout to avoid infinite blocking
            if not event.wait(timeout=5.0):
                self._pending.pop(msg_id, None)
                LOGGER.error("Command %s timed out after 5 seconds", command)
                raise RuntimeError(f"Command {command} timed out")
            response = self._responses.pop(msg_id, {"error": "no response received"})
            if "error" in response:
                raise RuntimeError(response["error"])
            return response.get("payload", {})

    def stop(self) -> None:
        if not self._host:
            return
        self._sequence += 1
        try:
            self.send_command("stop")
        finally:
            if self._player:
                try:
                    self._player.stop()
                except Exception:
                    LOGGER.exception("WavePlayer stop failed")
                try:
                    self._player.idle()
                except Exception:
                    LOGGER.exception("WavePlayer idle failed")


    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if not self._host:
            return
        # Stop audio worker first
        if self._audio_worker:
            self._audio_worker.stop()
            self._audio_worker.join(timeout=1)
            self._audio_worker = None
        if self._player:
            self._player.close()
            self._player = None
        # Send delete command to host (this will cause receiver to get EOFError)
        try:
            self.send_command("delete")
        except Exception:
            LOGGER.exception("Failed to delete host cleanly")
        # Wait for receiver thread to finish (it will get EOFError and exit)
        if self._receiver:
            self._receiver.join(timeout=2)
            self._receiver = None
        # Now close connections and terminate process
        try:
            self._host.connection.close()
        except Exception:
            pass
        try:
            self._host.listener.close()
        except Exception:
            pass
        try:
            self._host.process.terminate()
            self._host.process.wait(timeout=2)
        except Exception:
            LOGGER.exception("Failed to terminate host process")
            try:
                self._host.process.kill()
            except Exception:
                pass
        self._host = None


_client = EloquenceHostClient()
synth_queue = queue.Queue()
params: Dict[int, int] = {}
voice_params: Dict[int, int] = {}
_synth_worker: Optional[threading.Thread] = None
_synth_worker_lock = threading.Lock()
_synth_worker_stop = threading.Event()


# Public API ---------------------------------------------------------------------
hsz=1; pitch=2; fluctuation=3; rgh=4; bth=5; rate=6; vlm=7
eciPath = os.path.abspath(os.path.join(os.path.dirname(__file__), "eloquence", "eci.dll"))
langs={'esm': (131073, 'Latin American Spanish'),
'esp': (131072, 'Castilian Spanish'),
'ptb': (458752, 'Brazilian Portuguese'),
'frc': (196609, 'French Canadian'),
'fra': (196608, 'French'),
'fin': (589824, 'Finnish'),
'deu': (262144, 'German'),
'ita': (327680, 'Italian'),
'enu': (65536, 'American English'),
'eng': (65537, 'British English'),
'chs': (393216, 'Mandarin Chinese'),  # 0x00060000
'jpn': (524288, 'Japanese'),  # 0x00080000
'kor': (655360, 'Korean')}  # 0x000A0000

# Language to encoding mapping for Asian languages
# Using same codecs as IBMTTS which works correctly
LANG_ENCODINGS = {
    'chs': 'gb18030',  # Mandarin Chinese
    'jpn': 'cp932',    # Japanese (Shift-JIS compatible)
    'kor': 'cp949',    # Korean
}

# Voice ID to language code mapping (inverse of langs)
VOICE_ID_TO_LANG = {voice_id: lang_code for lang_code, (voice_id, _) in langs.items()}

# Current language code (updated when voice is set)
_current_lang = 'enu'
    
def initialize(indexCallback=None):
    global onIndexReached, _current_lang
    _client.ensure_started()
    _client.initialize_audio()
    _ensure_synth_worker()
    onIndexReached = indexCallback
    eci_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "eloquence", "eci.dll"))
    voice_conf = config.conf.get("speech", {}).get("eci", {})
    _current_lang = voice_conf.get("voice", "enu")
    payload = {
        "eciPath": eci_path,
        "dataDirectory": os.path.join(os.path.dirname(eci_path)),
        "language": _current_lang,
        "enableAbbreviationDict": config.conf.get("speech", {}).get("eci", {}).get("ABRDICT", False),
        "enablePhrasePrediction": config.conf.get("speech", {}).get("eci", {}).get("phrasePrediction", False),
        "voiceVariant": int(voice_conf.get("variant", 0) or 0),
    }
    response = _client.send_command("initialize", **payload)
    params.update(response.get("params", {}))
    voice_params.update(response.get("voiceParams", {}))


def speak(text):
    try:
        # Use appropriate encoding for Asian languages
        encoding = LANG_ENCODINGS.get(_current_lang, "mbcs")
        text_bytes = text.encode(encoding, errors='replace')
        _client.send_command("addText", text=text_bytes)
    except Exception:
        LOGGER.exception("Failed to send text to synthesizer")


def index(idx):
    try:
        _client.send_command("insertIndex", value=int(idx))
    except Exception:
        LOGGER.exception("Failed to insert index")


def cmdProsody(param, multiplier):
    value = voice_params.get(param, 0)
    if multiplier is not None:
        new_value = int(value * multiplier)
    else:
        new_value = value
    setVParam(param, new_value, temporary=True)


def synth():
    try:
        _client.send_command("synthesize")
    except Exception:
        LOGGER.exception("Failed to start synthesis")


def stop():
    _client.stop()



def pause(switch):
    if _client._player:
        _client._player.pause(switch)


def terminate():
    _client.shutdown()
    _stop_synth_worker()


def set_voice(vl):
    global _current_lang
    try:
        voice_id = int(vl)
        response = _client.send_command("setParam", paramId=9, value=voice_id)
        params.update(response.get("params", {}))
        voice_params.update(response.get("voiceParams", {}))
        # Update current language for proper encoding
        _current_lang = VOICE_ID_TO_LANG.get(voice_id, 'enu')
        LOGGER.debug("Voice changed to ID %d, language code: %s", voice_id, _current_lang)
    except Exception:
        LOGGER.exception("Failed to set voice")


def getVParam(pr):
    return voice_params.get(pr, 0)


def setVParam(pr, vl, temporary=False):
    try:
        response = _client.send_command(
            "setVoiceParam", paramId=int(pr), value=int(vl), temporary=bool(temporary)
        )
        if not temporary:
            voice_params[pr] = response.get("voiceParams", {}).get(pr, vl)
    except Exception:
        LOGGER.exception("Failed to set voice parameter")


def setVariant(v):
    try:
        response = _client.send_command("copyVoice", variant=int(v))
        voice_params.update(response.get("voiceParams", {}))
    except Exception:
        LOGGER.exception("Failed to set variant")


def process():
    _ensure_synth_worker()


def _synth_worker_loop() -> None:
    while True:
        try:
            item = synth_queue.get(timeout=0.1)
        except queue.Empty:
            if _synth_worker_stop.is_set():
                break
            continue
        if item is None:
            synth_queue.task_done()
            break
        lst, seq = item
        if seq < _client._sequence:
            synth_queue.task_done()
            continue
        _client._current_seq = seq
        try:
            for func, args in lst:
                try:
                    func(*args)
                except Exception:
                    LOGGER.exception("Synthesis command failed")
        finally:
            synth_queue.task_done()


def _ensure_synth_worker() -> None:
    global _synth_worker
    with _synth_worker_lock:
        if _synth_worker and _synth_worker.is_alive():
            return
        _synth_worker_stop.clear()
        _synth_worker = threading.Thread(
            target=_synth_worker_loop, name="EloquenceSynthWorker", daemon=True
        )
        _synth_worker.start()


def _stop_synth_worker() -> None:
    global _synth_worker
    with _synth_worker_lock:
        if not _synth_worker:
            return
        _synth_worker_stop.set()
        synth_queue.put(None)
        _synth_worker.join(timeout=1)
        if _synth_worker.is_alive():
            LOGGER.warning("Synthesis worker failed to terminate cleanly")
        _synth_worker = None
        _synth_worker_stop.clear()


def eciCheck() -> bool:
    eci_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "eloquence", "eci.dll"))
    return os.path.exists(eci_path)

