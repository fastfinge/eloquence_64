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
import queueHandler
from buildVersion import version_year

LOGGER = logging.getLogger(__name__)

HOST_EXECUTABLE = "eloquence_host32.exe"
HOST_SCRIPT = "host_eloquence32.py"
AUTH_KEY_BYTES = 16

onIndexReached = None


@dataclass
class _AudioTask:
    kind: str
    generation: int
    data: bytes = b""
    index: Optional[int] = None
    is_final: bool = False


class AudioWorker(threading.Thread):
    def __init__(self, player: nvwave.WavePlayer) -> None:
        super().__init__(name="EloquenceAudioWorker", daemon=True)
        self._player = player
        self._tasks: "queue.PriorityQueue[Tuple[int, int, _AudioTask]]" = queue.PriorityQueue()
        self._task_counter = itertools.count()
        self._state_lock = threading.Lock()
        self._generation = 0
        self._awaiting_final = False
        self._final_generation = -1

    # ------------------------------------------------------------------
    def submit_chunk(self, data: bytes, index: Optional[int], is_final: bool) -> None:
        with self._state_lock:
            generation = self._generation
        self._put_task(10, _AudioTask("chunk", generation, data, index, is_final))

    def request_stop(self) -> None:
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._awaiting_final = False
            self._final_generation = -1
        self._put_task(0, _AudioTask("stop", generation))

    def shutdown(self) -> None:
        self.request_stop()
        self._put_task(-10, _AudioTask("shutdown", -1))
        if threading.current_thread() is not self and self.ident is not None:
            self.join(timeout=1)

    # ------------------------------------------------------------------
    def run(self) -> None:
        while True:
            _priority, _counter, task = self._tasks.get()
            try:
                if task.kind == "chunk":
                    self._handle_chunk(task)
                elif task.kind == "complete":
                    self._handle_completion(task)
                elif task.kind == "stop":
                    self._handle_stop(task)
                elif task.kind == "shutdown":
                    self._handle_stop(task)
                    break
                else:
                    LOGGER.warning("Unknown audio task %s", task.kind)
            except Exception:
                LOGGER.exception("Audio worker task failed")
            finally:
                if task.kind == "shutdown":
                    break

    # ------------------------------------------------------------------
    def _put_task(self, priority: int, task: _AudioTask) -> None:
        self._tasks.put((priority, next(self._task_counter), task))

    def _handle_chunk(self, task: _AudioTask) -> None:
        with self._state_lock:
            current_gen = self._generation
            if task.generation != current_gen:
                return
            if task.is_final:
                self._awaiting_final = True
                self._final_generation = task.generation

        if not task.data:
            self._handle_completion(
                _AudioTask("complete", task.generation, index=task.index, is_final=task.is_final)
            )
            return

        def _on_done(idx: Optional[int] = task.index, final: bool = task.is_final, gen: int = task.generation) -> None:
            self._put_task(-1, _AudioTask("complete", gen, index=idx, is_final=final))

        try:
            self._player.feed(task.data, onDone=_on_done)
        except Exception:
            LOGGER.exception("WavePlayer feed failed")
            self._handle_completion(
                _AudioTask("complete", task.generation, index=task.index, is_final=task.is_final)
            )

    def _handle_completion(self, task: _AudioTask) -> None:
        with self._state_lock:
            current_gen = self._generation
            awaiting_final = self._awaiting_final
            final_generation = self._final_generation
        if task.generation != current_gen and task.generation != final_generation:
            return
        if task.index is not None and task.generation == current_gen:
            self._queue_index_callback(task.index)
        if task.is_final and awaiting_final and task.generation == final_generation:
            self._finalize(task.generation)

    def _handle_stop(self, task: _AudioTask) -> None:
        with self._state_lock:
            if task.generation >= 0 and task.generation > self._generation:
                self._generation = task.generation
            self._awaiting_final = False
            self._final_generation = -1
        try:
            self._player.stop()
        except Exception:
            LOGGER.exception("WavePlayer stop failed")
        try:
            self._player.idle()
        except Exception:
            LOGGER.exception("WavePlayer idle failed")

    def _finalize(self, generation: int) -> None:
        with self._state_lock:
            if not self._awaiting_final or self._final_generation != generation:
                return
            self._awaiting_final = False
            self._final_generation = -1
        try:
            self._player.idle()
        except Exception:
            LOGGER.exception("WavePlayer idle failed")
        self._queue_index_callback(None)

    def _queue_index_callback(self, value: Optional[int]) -> None:
        if not onIndexReached:
            return

        def _invoke() -> None:
            try:
                onIndexReached(value)
            except Exception:
                LOGGER.exception("Index callback failed")

        try:
            queueHandler.queueFunction(queueHandler.eventQueue, _invoke)
        except Exception:
            LOGGER.exception("Failed to queue index callback")
            _invoke()


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
        self._player: Optional[nvwave.WavePlayer] = None
        self._audio_worker: Optional[AudioWorker] = None
        self._command_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._id_lock = threading.Lock()

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
        self._audio_worker = AudioWorker(player)
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
                with self._pending_lock:
                    for msg_id, event in list(self._pending.items()):
                        self._responses[msg_id] = {"error": "connectionClosed"}
                        event.set()
                    self._pending.clear()
                break
            msg_type = message.get("type")
            if msg_type == "response":
                msg_id = message["id"]
                with self._pending_lock:
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
            worker = self._audio_worker
            if worker:
                worker.submit_chunk(data, index, is_final)
            else:
                LOGGER.debug("Dropping audio chunk because audio worker is not available")
        elif event == "stopped":
            worker = self._audio_worker
            if worker:
                worker.request_stop()
        else:
            LOGGER.debug("Unhandled host event %s", event)

    # ------------------------------------------------------------------
    def send_command(self, command: str, **payload: Any) -> Dict[str, Any]:
        if not self._host:
            raise RuntimeError("Host not started")
        with self._id_lock:
            msg_id = next(self._id_counter)
        event = threading.Event()
        with self._pending_lock:
            self._pending[msg_id] = event
        try:
            with self._command_lock:
                self._host.connection.send({"type": "command", "id": msg_id, "command": command, "payload": payload})
        except Exception:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            raise
        event.wait()
        with self._pending_lock:
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
            if self._audio_worker:
                self._audio_worker.request_stop()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if not self._host:
            return
        try:
            self.send_command("delete")
        except Exception:
            LOGGER.exception("Failed to delete host cleanly")
        if self._audio_worker:
            self._audio_worker.shutdown()
            if self._audio_worker.is_alive():
                LOGGER.warning("Audio worker failed to terminate cleanly")
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

