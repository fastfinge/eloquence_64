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
from typing import Any, Callable, Dict, Optional, Sequence, Set, Tuple, Union

import config
import nvwave
from buildVersion import version_year

LOGGER = logging.getLogger(__name__)

HOST_EXECUTABLE = "eloquence_host32.exe"
HOST_SCRIPT = "host_eloquence32.py"
AUTH_KEY_BYTES = 16

onIndexReached = None
_queue_handler_module = None


def _dispatch_index_callback(value: Optional[int]) -> None:
    """Invoke the registered index callback on NVDA's event queue when available."""

    global _queue_handler_module
    if not onIndexReached:
        return
    if _queue_handler_module is None:
        try:
            # queueHandler is only available inside NVDA. Import lazily so
            # development environments without NVDA modules can still import
            # this helper.
            import queueHandler  # type: ignore

            _queue_handler_module = queueHandler
        except Exception:
            _queue_handler_module = False
    if _queue_handler_module:
        try:
            _queue_handler_module.queueFunction(
                _queue_handler_module.eventQueue, _safe_invoke_index, value
            )
            return
        except Exception:
            LOGGER.exception("Failed to queue index callback")
    _safe_invoke_index(value)


def _safe_invoke_index(value: Optional[int]) -> None:
    if not onIndexReached:
        return
    try:
        onIndexReached(value)
    except Exception:
        LOGGER.exception("Index callback failed")


def _patch_waveplayer_idle_check() -> None:
    """Ensure nvwave's idle check iterates over a stable snapshot."""

    sentinel_attr = "_eloquenceIdleCheckPatched"
    if getattr(nvwave.WavePlayer, sentinel_attr, False):
        return

    original = nvwave.WavePlayer._idleCheck

    @classmethod
    def _safe_idle_check(cls):
        """Thread-safe wrapper around nvwave.WavePlayer._idleCheck."""

        cls._isIdleCheckPending = False
        threshold = time.time() - cls._IDLE_TIMEOUT
        stillActiveStream = False
        # Copy the dictionary to avoid race conditions when the weak reference
        # set is modified by another thread while we iterate over it.
        debug_nvwave = getattr(nvwave, "_isDebugForNvWave", lambda: False)
        for attempt in range(3):
            try:
                players = list(cls._instances.copy().values())
            except RuntimeError:
                if debug_nvwave():
                    LOGGER.debug("Retrying idle check snapshot due to concurrent modification")
                continue
            break
        else:
            if debug_nvwave():
                LOGGER.debug("Falling back to empty idle check snapshot due to repeated failures")
            players = []

        for player in players:
            if player is None or not player._lastActiveTime or player._isPaused:
                continue
            if player._lastActiveTime <= threshold:
                try:
                    nvwave.NVDAHelper.localLib.wasPlay_idle(player._player)
                    if player._enableTrimmingLeadingSilence:
                        player.startTrimmingLeadingSilence()
                except OSError:
                    nvwave.log.exception("Error calling wasPlay_idle")
                player._lastActiveTime = None
            else:
                stillActiveStream = True
        if stillActiveStream:
            cls._scheduleIdleCheck()

    _safe_idle_check.__doc__ = original.__doc__
    nvwave.WavePlayer._idleCheck = _safe_idle_check
    setattr(nvwave.WavePlayer, sentinel_attr, True)


_patch_waveplayer_idle_check()

# Audio handling -----------------------------------------------------------------
AudioChunk = Tuple[bytes, Optional[int], bool]
ControlPayload = Tuple[Callable[[], None], Optional[threading.Event]]
AudioQueueItem = Tuple[str, Union[AudioChunk, ControlPayload]]
CommandQueueItem = Tuple[int, str, Dict[str, Any], threading.Event]

_AUDIO_BACKLOG_LIMIT = 8


class AudioWorker(threading.Thread):
    _CHANNELS = 1
    _BITS_PER_SAMPLE = 16
    _SAMPLE_RATE = 11025
    _MAX_INFLIGHT_FEEDS = 24
    _CAPACITY_IDLE_INTERVAL = 0.01
    _CAPACITY_IDLE_TIMEOUT = 2.0

    def __init__(
        self,
        player_factory: Callable[[], nvwave.WavePlayer],
        audio_queue: "queue.Queue[Optional[AudioQueueItem]]",
    ):
        super().__init__(daemon=True)
        self._player_factory = player_factory
        self._queue = audio_queue
        self._running = True
        self._final_lock = threading.Lock()
        self._final_emitted = False
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._player: Optional[nvwave.WavePlayer] = None
        self._player_init_error: Optional[BaseException] = None
        self._state_lock = threading.Lock()
        self._feed_ids = itertools.count(1)
        self._inflight_feeds: Set[int] = set()
        self._pending_final_waiting: Set[int] = set()

    def run(self) -> None:
        self._thread_id = threading.get_ident()
        try:
            player = self._player_factory()
        except BaseException as exc:
            LOGGER.exception("Failed to create WavePlayer")
            self._player_init_error = exc
            self._ready.set()
            self._running = False
            return
        self._player = player
        self._ready.set()
        while self._running:
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                self._queue.task_done()
                break
            kind = chunk[0]
            if kind == "control":
                func, event = chunk[1]  # type: ignore[assignment]
                try:
                    func()
                except Exception:
                    LOGGER.exception("WavePlayer control command failed")
                finally:
                    if event:
                        event.set()
                    self._queue.task_done()
                continue
            if kind != "chunk":
                LOGGER.warning("Unknown audio queue item type: %s", kind)
                self._queue.task_done()
                continue
            data, index, is_final = chunk[1]  # type: ignore[assignment]
            self._prepare_for_chunk(is_final)
            if index is not None:
                self._invoke_index_callback(index)
            if not data:
                if is_final:
                    self._mark_final_pending()
                self._queue.task_done()
                continue
            self._wait_for_capacity()
            feed_id = next(self._feed_ids)
            wrapped_on_done = self._make_on_done(is_final, feed_id)
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
            if fed:
                with self._state_lock:
                    self._inflight_feeds.add(feed_id)
            elif is_final:
                self._mark_final_pending()
            self._queue.task_done()
        self._thread_id = None
        self._player = None

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)

    def wait_until_ready(self, timeout: Optional[float] = None) -> nvwave.WavePlayer:
        if not self._ready.wait(timeout):
            raise RuntimeError("Timed out waiting for audio worker")
        if self._player_init_error:
            raise RuntimeError("WavePlayer failed to initialize") from self._player_init_error
        if not self._player:
            raise RuntimeError("WavePlayer unavailable")
        return self._player

    def _prepare_for_chunk(self, is_final: bool) -> None:
        with self._final_lock:
            if self._final_emitted or not is_final:
                self._final_emitted = False

    def _emit_final(self) -> None:
        with self._final_lock:
            if self._final_emitted:
                return
            self._final_emitted = True
        self.enqueue_control(self._idle_player)
        self._invoke_index_callback(None)

    def _make_on_done(self, is_final: bool, feed_id: int):
        def _on_done() -> None:
            try:
                self._on_feed_done(is_final, feed_id)
            except Exception:
                LOGGER.exception("Audio finalization failed")

        return _on_done

    def _on_feed_done(self, is_final: bool, feed_id: int) -> None:
        emit_final = False
        with self._state_lock:
            self._inflight_feeds.discard(feed_id)
            if is_final:
                emit_final = True
                self._pending_final_waiting.clear()
            elif self._pending_final_waiting:
                self._pending_final_waiting.discard(feed_id)
                if not self._pending_final_waiting:
                    emit_final = True
        if emit_final:
            self._emit_final()

    def _mark_final_pending(self) -> None:
        emit_now = False
        with self._state_lock:
            if not self._inflight_feeds:
                emit_now = True
                self._pending_final_waiting.clear()
            else:
                self._pending_final_waiting = set(self._inflight_feeds)
        if emit_now:
            self._emit_final()

    def _invoke_index_callback(self, value: Optional[int]) -> None:
        _dispatch_index_callback(value)

    def _idle_player(self) -> None:
        player = self._player
        if not player:
            return
        try:
            player.idle()
        except Exception:
            LOGGER.exception("WavePlayer idle failed")

    def _wait_for_capacity(self) -> None:
        deadline = time.time() + self._CAPACITY_IDLE_TIMEOUT
        while self._running:
            with self._state_lock:
                inflight = len(self._inflight_feeds)
            if inflight < self._MAX_INFLIGHT_FEEDS:
                return
            self._idle_player()
            if time.time() >= deadline:
                LOGGER.warning(
                    "WavePlayer backlog still %d feeds after %.1fs; continuing",
                    inflight,
                    self._CAPACITY_IDLE_TIMEOUT,
                )
                return
            time.sleep(self._CAPACITY_IDLE_INTERVAL)

    def wait_for_backlog(self, limit: int, timeout: Optional[float] = None) -> None:
        if limit <= 0:
            return
        deadline = time.time() + timeout if timeout else None
        start = time.time()
        while self._running:
            with self._state_lock:
                inflight = len(self._inflight_feeds)
            with self._queue.mutex:
                queue_backlog = self._queue.unfinished_tasks
            total = inflight + queue_backlog
            if total <= limit:
                return
            self._idle_player()
            if deadline and time.time() >= deadline:
                LOGGER.warning(
                    "Audio backlog still %d (limit %d) after %.1fs; continuing",
                    total,
                    limit,
                    time.time() - start,
                )
                return
            time.sleep(self._CAPACITY_IDLE_INTERVAL)

    def enqueue_control(self, func: Callable[[], None], wait: bool = False) -> None:
        self._ready.wait()
        if not self._player:
            LOGGER.debug("Ignoring WavePlayer control command: player unavailable")
            return
        if threading.get_ident() == self._thread_id:
            try:
                func()
            except Exception:
                LOGGER.exception("WavePlayer control command failed")
            return
        if not self.is_alive() or not self._running:
            LOGGER.debug("Dropping WavePlayer control command: worker not running")
            return
        event: Optional[threading.Event] = threading.Event() if wait else None
        self._queue.put(("control", (func, event)))
        if event:
            event.wait()

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
        self._audio_queue: "queue.Queue[Optional[AudioQueueItem]]" = queue.Queue()
        self._player: Optional[nvwave.WavePlayer] = None
        self._audio_worker: Optional[AudioWorker] = None
        self._start_lock = threading.Lock()
        self._command_queue: "queue.Queue[Optional[CommandQueueItem]]" = queue.Queue()
        self._command_thread: Optional[threading.Thread] = None
        self._command_thread_stop = threading.Event()
        self._audio_backlog_limit = _AUDIO_BACKLOG_LIMIT

    # ------------------------------------------------------------------
    def ensure_started(self) -> None:
        if self._host:
            if not self._command_thread or not self._command_thread.is_alive():
                self._start_command_thread()
            return
        with self._start_lock:
            if self._host:
                if not self._command_thread or not self._command_thread.is_alive():
                    self._start_command_thread()
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
            try:
                conn = listener.accept()
            except Exception:
                proc.terminate()
                listener.close()
                raise
            self._host = HostProcess(process=proc, connection=conn, listener=listener)
            self._receiver = threading.Thread(target=self._receiver_loop, daemon=True)
            self._receiver.start()
            self._start_command_thread()

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
            def create_player() -> nvwave.WavePlayer:
                return nvwave.WavePlayer(
                    1,
                    11025,
                    16,
                    outputDevice=device,
                    wantDucking=ducking,
                )
        else:
            device = config.conf["speech"]["outputDevice"]
            nvwave.WavePlayer.MIN_BUFFER_MS = 1500
            def create_player() -> nvwave.WavePlayer:
                return nvwave.WavePlayer(
                    1,
                    11025,
                    16,
                    outputDevice=device,
                    buffered=True,
                )
        worker = AudioWorker(create_player, self._audio_queue)
        worker.start()
        try:
            player = worker.wait_until_ready(timeout=5)
        except Exception:
            worker.stop()
            worker.join(timeout=1)
            raise
        self._audio_worker = worker
        self._player = player

    def pause_player(self, switch: bool) -> None:
        player = self._player
        if not player:
            return
        self._execute_on_player(lambda: player.pause(switch), wait=True)

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
            self._audio_queue.put(("chunk", (data, index, is_final)))
        elif event == "stopped":
            player = self._player
            if player:
                self._execute_on_player(player.stop)
        else:
            LOGGER.debug("Unhandled host event %s", event)

    # ------------------------------------------------------------------
    def send_command(self, command: str, **payload: Any) -> Dict[str, Any]:
        if not self._host:
            raise RuntimeError("Host not started")
        if not self._command_thread or not self._command_thread.is_alive():
            raise RuntimeError("Command dispatcher not running")
        msg_id = next(self._id_counter)
        response_ready = threading.Event()
        self._pending[msg_id] = response_ready
        ack = threading.Event()
        self._command_queue.put((msg_id, command, payload, ack))
        if not ack.wait(timeout=5):
            raise RuntimeError("Command dispatcher unresponsive")
        response_ready.wait()
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
            player = self._player
            if player:
                self._execute_on_player(player.stop, wait=True)
                self._execute_on_player(player.idle, wait=True)

    def wait_for_audio_capacity(self, limit: Optional[int] = None, timeout: Optional[float] = None) -> None:
        worker = self._audio_worker
        if not worker or not worker.is_alive():
            return
        backlog_limit = limit if limit is not None else self._audio_backlog_limit
        if backlog_limit <= 0:
            return
        worker.wait_for_backlog(backlog_limit, timeout=timeout)

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
            kind = item[0]
            if kind == "control":
                _, payload = item
                _, event = payload  # type: ignore[assignment]
                if event:
                    event.set()
            elif kind == "chunk":
                cleared += 1
            self._audio_queue.task_done()
        if cleared:
            LOGGER.debug("Dropped %d pending audio chunk(s)", cleared)

    # ------------------------------------------------------------------
    def _execute_on_player(self, action: Callable[[], None], wait: bool = False) -> None:
        worker = self._audio_worker
        if worker and worker.is_alive():
            worker.enqueue_control(action, wait=wait)
            return
        LOGGER.debug("Skipping WavePlayer command because audio worker is unavailable")

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if not self._host:
            return
        try:
            self.send_command("delete")
        except Exception:
            LOGGER.exception("Failed to delete host cleanly")
        worker = self._audio_worker
        player = self._player
        if player:
            if worker and worker.is_alive():
                worker.enqueue_control(player.close, wait=True)
            else:
                try:
                    player.close()
                except Exception:
                    LOGGER.exception("WavePlayer close failed")
            self._player = None
        if worker:
            worker.stop()
            worker.join(timeout=1)
            self._audio_worker = None
        self._stop_command_thread()
        self._host.connection.close()
        self._host.listener.close()
        self._host.process.terminate()
        if self._receiver:
            self._receiver.join(timeout=1)
            self._receiver = None
        self._host = None

    def _start_command_thread(self) -> None:
        if self._command_thread and self._command_thread.is_alive():
            return
        self._command_thread_stop.clear()
        self._command_queue = queue.Queue()
        self._command_thread = threading.Thread(
            target=self._command_loop,
            name="EloquenceCommandDispatcher",
            daemon=True,
        )
        self._command_thread.start()

    def _stop_command_thread(self) -> None:
        thread = self._command_thread
        if not thread:
            return
        self._command_thread_stop.set()
        self._command_queue.put(None)
        thread.join(timeout=1)
        if thread.is_alive():
            LOGGER.warning("Command dispatcher failed to terminate cleanly")
        self._command_thread = None
        self._command_thread_stop.clear()
        for msg_id, event in list(self._pending.items()):
            self._responses[msg_id] = {"error": "shutdown"}
            event.set()
        self._pending.clear()
        self._command_queue = queue.Queue()

    def _command_loop(self) -> None:
        while True:
            try:
                item = self._command_queue.get(timeout=0.1)
            except queue.Empty:
                if self._command_thread_stop.is_set():
                    break
                continue
            if item is None:
                self._command_queue.task_done()
                break
            msg_id, command, payload, ack = item
            try:
                if not self._host:
                    raise RuntimeError("Host not started")
                self._host.connection.send(
                    {"type": "command", "id": msg_id, "command": command, "payload": payload}
                )
            except Exception as exc:
                LOGGER.exception("Failed to dispatch command %s", command)
                self._responses[msg_id] = {"error": str(exc)}
                event = self._pending.pop(msg_id, None)
                if event:
                    event.set()
            finally:
                ack.set()
                self._command_queue.task_done()


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
    _client.pause_player(switch)


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
            _client.wait_for_audio_capacity(timeout=2.0)
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

