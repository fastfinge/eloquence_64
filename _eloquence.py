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
try:
    import queueHandler
except ImportError:
    queueHandler = None
from buildVersion import version_year

LOGGER = logging.getLogger(__name__)

HOST_EXECUTABLE = "eloquence_host32.exe"
HOST_SCRIPT = "host_eloquence32.py"
AUTH_KEY_BYTES = 16

onIndexReached = None

# Audio handling -----------------------------------------------------------------
class AudioWorker(threading.Thread):
    _CHANNELS = 1
    _BITS_PER_SAMPLE = 16
    _SAMPLE_RATE = 11025

    def __init__(self, player: nvwave.WavePlayer, audio_queue: "queue.Queue[Optional[AudioChunk]]"):
        super().__init__(daemon=True)
        self._player = player
        self._queue = audio_queue
        self._running = True
        self._final_lock = threading.Lock()
        self._final_emitted = False

    def run(self) -> None:
        while self._running:
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            data, index, is_final = chunk
            self._prepare_for_chunk(is_final)
            if not data:
                if index is not None:
                    self._invoke_index_callback(index)
                if is_final:
                    self._emit_final()
                self._queue.task_done()
                continue
            on_done = None
            if index is not None:

                def _callback(i=index):
                    self._invoke_index_callback(i)

                on_done = _callback
            wrapped_on_done = self._make_on_done(on_done, is_final)
            tries = 0
            fed = False
            while tries < 10:
                try:
                    self._player.feed(data, onDone=wrapped_on_done)
                    if tries:
                        LOGGER.warning("Audio feed retries=%d", tries)
                    fed = True
                    break
                except Exception:
                    LOGGER.exception("WavePlayer feed failed, retrying")
                    self._player.idle()
                    time.sleep(0.02)
                    tries += 1
            if not fed and is_final:
                self._emit_final()
            self._queue.task_done()

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)

    def _prepare_for_chunk(self, is_final: bool) -> None:
        with self._final_lock:
            if self._final_emitted or not is_final:
                self._final_emitted = False

    def _emit_final(self) -> None:
        with self._final_lock:
            if self._final_emitted:
                return
            self._final_emitted = True
        if self._player:
            try:
                self._player.idle()
            except Exception:
                LOGGER.exception("WavePlayer idle failed")
        self._invoke_index_callback(None)

    def _make_on_done(self, callback, is_final: bool):
        def _on_done() -> None:
            try:
                if callback:
                    callback()
            except Exception:
                LOGGER.exception("Index callback failed")
            if is_final:
                self._emit_final()

        return _on_done

    def _invoke_index_callback(self, value: Optional[int]) -> None:
        if onIndexReached:
            try:
                if queueHandler is not None:
                    queueHandler.queueFunction(queueHandler.eventQueue, onIndexReached, value)
                else:
                    onIndexReached(value)
            except Exception:
                LOGGER.exception("Index callback failed")


AudioChunk = Tuple[bytes, Optional[int], bool]

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
        self._audio_worker = AudioWorker(player, self._audio_queue)
        self._audio_worker.start()

    # ------------------------------------------------------------------
    def _receiver_loop(self) -> None:
        connection = self._host.connection if self._host else None
        if connection is None:
            return
        while True:
            try:
                message = connection.recv()
            except EOFError:
                LOGGER.info("Host connection closed")
                for msg_id, event in list(self._pending.items()):
                    self._responses[msg_id] = {"error": "connectionClosed"}
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
            self._audio_queue.put((data, index, is_final))
        elif event == "stopped":
            if self._player:
                self._player.stop()
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
            self._host.connection.send({"type": "command", "id": msg_id, "command": command, "payload": payload})
            event.wait()
            response = self._responses.pop(msg_id)
            if "error" in response:
                raise RuntimeError(response["error"])
            return response.get("payload", {})

    def stop(self) -> None:
        if not self._host:
            return
        try:
            self.send_command("stop")
        finally:
            self._clear_audio_queue()
            if self._player:
                try:
                    self._player.stop()
                except Exception:
                    LOGGER.exception("WavePlayer stop failed")
                try:
                    self._player.idle()
                except Exception:
                    LOGGER.exception("WavePlayer idle failed")

    def _clear_audio_queue(self) -> None:
        cleared = 0
        while True:
            try:
                item = self._audio_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                # Preserve sentinel used to shut down the worker thread.
                self._audio_queue.task_done()
                self._audio_queue.put(None)
                break
            self._audio_queue.task_done()
            cleared += 1
        if cleared:
            LOGGER.debug("Dropped %d pending audio chunk(s)", cleared)

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if not self._host:
            return
        try:
            self.send_command("delete")
        except Exception:
            LOGGER.exception("Failed to delete host cleanly")
        if self._audio_worker:
            self._audio_worker.stop()
            self._audio_worker.join(timeout=1)
            self._audio_worker = None
        if self._player:
            self._player.close()
            self._player = None
        self._host.connection.close()
        self._host.listener.close()
        self._host.process.terminate()
        if self._receiver:
            self._receiver.join(timeout=1)
            self._receiver = None
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
'eng': (65537, 'British English')}
    
def initialize(indexCallback=None):
    global onIndexReached
    _client.ensure_started()
    _client.initialize_audio()
    _ensure_synth_worker()
    onIndexReached = indexCallback
    eci_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "eloquence", "eci.dll"))
    voice_conf = config.conf.get("speech", {}).get("eci", {})
    payload = {
        "eciPath": eci_path,
        "dataDirectory": os.path.join(os.path.dirname(eci_path)),
        "language": voice_conf.get("voice", "enu"),
        "enableAbbreviationDict": config.conf.get("speech", {}).get("eci", {}).get("ABRDICT", False),
        "enablePhrasePrediction": config.conf.get("speech", {}).get("eci", {}).get("phrasePrediction", False),
        "voiceVariant": int(voice_conf.get("variant", 0) or 0),
    }
    response = _client.send_command("initialize", **payload)
    params.update(response.get("params", {}))
    voice_params.update(response.get("voiceParams", {}))


def speak(text):
    text_bytes = text.encode("mbcs")
    _client.send_command("addText", text=text_bytes)


def index(idx):
    _client.send_command("insertIndex", value=int(idx))


def cmdProsody(param, multiplier):
    value = voice_params.get(param, 0)
    if multiplier is not None:
        new_value = int(value * multiplier)
    else:
        new_value = value
    setVParam(param, new_value, temporary=True)


def synth():
    _client.send_command("synthesize")


def stop():
    _client.stop()
    _clear_synth_queue()


def _clear_synth_queue() -> None:
    cleared = 0
    while True:
        try:
            synth_queue.get_nowait()
        except queue.Empty:
            break
        synth_queue.task_done()
        cleared += 1
    if cleared:
        LOGGER.debug("Dropped %d pending synthesis request(s)", cleared)


def pause(switch):
    if _client._player:
        _client._player.pause(switch)


def terminate():
    _client.shutdown()
    _stop_synth_worker()


def set_voice(vl):
    response = _client.send_command("setParam", paramId=9, value=int(vl))
    params.update(response.get("params", {}))


def getVParam(pr):
    return voice_params.get(pr, 0)


def setVParam(pr, vl, temporary=False):
    response = _client.send_command(
        "setVoiceParam", paramId=int(pr), value=int(vl), temporary=bool(temporary)
    )
    if not temporary:
        voice_params[pr] = response.get("voiceParams", {}).get(pr, vl)


def setVariant(v):
    response = _client.send_command("copyVoice", variant=int(v))
    voice_params.update(response.get("voiceParams", {}))


def process():
    _ensure_synth_worker()


def _synth_worker_loop() -> None:
    while True:
        try:
            lst = synth_queue.get(timeout=0.1)
        except queue.Empty:
            if _synth_worker_stop.is_set():
                break
            continue
        if lst is None:
            synth_queue.task_done()
            break
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

