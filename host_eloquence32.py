"""32-bit host process for Eloquence synthesis.

This module is executed as a separate helper process under a 32-bit
Python runtime.  It loads the ETI-Eloquence DLL directly and exposes a
simple RPC protocol over a `multiprocessing.connection` channel so that
64-bit NVDA builds can continue to make use of the original synthesizer.

The helper deliberately avoids importing NVDA modules to keep the
runtime self contained.  All configuration required to load the DLL,
open dictionaries and select the initial voice is provided by the
controller process as part of the `initialize` command.
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from multiprocessing.connection import Client
from typing import Dict, Optional

import ctypes
from ctypes import (
    POINTER,
    c_int,
    c_short,
    c_void_p,
    cast,
)

# Constants mirrored from the old in-process implementation.
Callback = ctypes.WINFUNCTYPE(c_int, c_int, c_int, c_int, c_void_p)

# Eloquence parameter identifiers.
HSZ = 1
PITCH = 2
FLUCTUATION = 3
RGH = 4
BTH = 5
RATE = 6
VLM = 7

# Synthesis state parameters.
ECI_INPUT_TYPE = 1

# A sentinel index value used by Eloquence to mark the end of a chunk.
FINAL_INDEX = 0xFFFF

LANGS: Dict[str, int] = {
    "esm": 131073,
    "esp": 131072,
    "ptb": 458752,
    "frc": 196609,
    "fra": 196608,
    "fin": 589824,
    "deu": 262144,
    "ita": 327680,
    "enu": 65536,
    "eng": 65537,
}

LOGGER = logging.getLogger("eloquence.host")


def configure_logging(log_dir: Optional[str]) -> None:
    """Initialise logging for the helper."""
    logging.basicConfig(
        filename=os.path.join(log_dir, "eloquence-host.log") if log_dir else None,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
    )


@dataclass
class HostConfig:
    eci_path: str
    data_directory: str
    language_code: str
    enable_abbrev_dict: bool
    enable_phrase_prediction: bool
    voice_variant: int


class EloquenceRuntime:
    """Wraps access to the 32-bit Eloquence DLL."""

    def __init__(self, conn: Client, config: HostConfig):
        self._conn = conn
        self._config = config
        self._dll = None  # type: ignore[assignment]
        self._handle = None  # type: ignore[assignment]
        self._dictionary_handle = None
        self._callback = Callback(self._on_callback)
        self._audio_buffer = BytesIO()
        self._samples = 3300
        # eciSetOutputBuffer expects a pointer to 16-bit PCM samples.  Using a
        # c_short array keeps the data in the correct format and avoids the
        # char* semantics of create_string_buffer which truncate at the first
        # NUL byte when passed as c_char_p.
        self._buffer = (c_short * self._samples)()
        self._params: Dict[int, int] = {}
        self._voice_params: Dict[int, int] = {}
        self._speaking = False

    # ------------------------------------------------------------------
    # Communication helpers
    def _send_event(self, event: str, **payload: object) -> None:
        LOGGER.debug("Sending event %s", event)
        try:
            self._conn.send({"type": "event", "event": event, "payload": payload})
        except Exception:
            LOGGER.exception("Failed to send event %s", event)

    def _send_response(self, msg_id: int, **payload: object) -> None:
        LOGGER.debug("Sending response for %s", msg_id)
        self._conn.send({"type": "response", "id": msg_id, "payload": payload})

         
    # ------------------------------------------------------------------
    # Eloquence management
    def start(self) -> None:
        LOGGER.debug("Starting Eloquence runtime")
        self._load_dll()

    def _load_dll(self) -> None:
        LOGGER.info("Loading Eloquence library from %s", self._config.eci_path)
        ini=open(self._config.eci_path[:-3]+"ini","r+")
        ini.seek(12)
        tml=ini.readline()
        if tml[:-9] != self._config.eci_path[:-8]:
          ini.seek(12)
          tmp=ini.read()
          ini.seek(12)
          ini.write(tmp.replace(tml[:-9], self._config.eci_path[:-8]))
          ini.truncate()
        ini.close()
        self._dll = ctypes.windll.LoadLibrary(self._config.eci_path)
        self._dll.eciRegisterCallback.argtypes = [c_void_p, Callback, c_void_p]
        self._dll.eciRegisterCallback.restype = None
        self._dll.eciSetOutputBuffer.argtypes = [c_void_p, c_int, POINTER(c_short)]
        self._dll.eciSetOutputBuffer.restype = c_int

        language_id = LANGS.get(self._config.language_code, LANGS["enu"])
        LOGGER.debug("Creating Eloquence handle for language %s -> %s", self._config.language_code, language_id)
        self._dll.eciNewEx.argtypes = [c_int]
        self._dll.eciNewEx.restype = c_void_p
        handle = self._dll.eciNewEx(language_id)
        if not handle:
            raise RuntimeError("Failed to create Eloquence handle")
        self._handle = handle
        self._dll.eciRegisterCallback(handle, self._callback, None)
        result = self._dll.eciSetOutputBuffer(handle, self._samples, self._buffer)
        if not result:
            raise RuntimeError("eciSetOutputBuffer failed")
        self._dictionary_handle = self._dll.eciNewDict(handle)
        self._dll.eciSetDict(handle, self._dictionary_handle)
        # Allow annotated input so that backquote commands are interpreted instead of spoken.
        self._dll.eciSetParam(handle, ECI_INPUT_TYPE, 1)
        self._params[ECI_INPUT_TYPE] = 1
        self._params[9] = self._dll.eciGetParam(handle, 9)
        self._voice_params[RATE] = self._dll.eciGetVoiceParam(handle, 0, RATE)
        self._voice_params[PITCH] = self._dll.eciGetVoiceParam(handle, 0, PITCH)
        self._voice_params[VLM] = self._dll.eciGetVoiceParam(handle, 0, VLM)
        self._voice_params[FLUCTUATION] = self._dll.eciGetVoiceParam(handle, 0, FLUCTUATION)
        self._load_dictionaries()
        if self._config.voice_variant:
            self.copy_voice(self._config.voice_variant)
        if self._config.enable_phrase_prediction:
            LOGGER.debug("Enabling phrase prediction")
            self._dll.eciSetParam(handle, 42, 1)
        if self._config.enable_abbrev_dict:
            LOGGER.debug("Enabling abbreviation dictionary")
            self._dll.eciSetParam(handle, 41, 1)

    def _load_dictionaries(self) -> None:
        dictionary_dir = self._config.data_directory
        LOGGER.debug("Loading dictionaries from %s", dictionary_dir)
        main_candidates = ["enumain.dic", "main.dic"]
        root_candidates = ["enuroot.dic", "root.dic"]
        abbr_candidates = ["enuabbr.dic", "abbr.dic"]

        for index, candidates in enumerate((main_candidates, root_candidates, abbr_candidates)):
            for candidate in candidates:
                path = os.path.join(dictionary_dir, candidate)
                if os.path.exists(path):
                    LOGGER.debug("Loading dictionary index=%s file=%s", index, path)
                    self._dll.eciLoadDict(self._handle, self._dictionary_handle, index, path.encode("mbcs"))
                    break

    # ------------------------------------------------------------------
    # Public API invoked from the controller
    def add_text(self, text: bytes) -> None:
        LOGGER.debug("Adding %d bytes of text", len(text))
        self._dll.eciAddText(self._handle, text)

    def insert_index(self, index: int) -> None:
        LOGGER.debug("Inserting index %s", index)
        self._dll.eciInsertIndex(self._handle, index)

    def synthesize(self) -> None:
        LOGGER.debug("Starting synthesis")
        self._speaking = True
        try:
            self._dll.eciSynthesize(self._handle)
            if not self._dll.eciSynchronize(self._handle):
                LOGGER.warning("eciSynchronize reported failure")
        finally:
            self._speaking = False
            # Ensure any buffered audio is pushed even if the final index was not
            # delivered (for example if the controller stops early).
            self._flush_audio()

    def stop(self) -> None:
        LOGGER.debug("Stopping synthesis")
        self._dll.eciStop(self._handle)
        self._audio_buffer.seek(0)
        self._audio_buffer.truncate(0)
        self._speaking = False
        self._send_event("stopped")

    def delete(self) -> None:
        LOGGER.debug("Deleting Eloquence handle")
        if self._handle:
            self._dll.eciDelete(self._handle)
            self._handle = None

    def set_param(self, param_id: int, value: int) -> None:
        LOGGER.debug("Setting param %s=%s", param_id, value)
        self._dll.eciSetParam(self._handle, param_id, value)
        self._params[param_id] = value

    def set_voice_param(self, param_id: int, value: int, temporary: bool = False) -> None:
        LOGGER.debug("Setting voice param %s=%s temporary=%s", param_id, value, temporary)
        self._dll.eciSetVoiceParam(self._handle, 0, param_id, value)
        if not temporary:
            self._voice_params[param_id] = value

    def copy_voice(self, variant: int) -> None:
        LOGGER.debug("Copying voice variant %s", variant)
        self._dll.eciCopyVoice(self._handle, variant, 0)
        for param in (RATE, PITCH, VLM, FLUCTUATION, HSZ, RGH, BTH):
            self._voice_params[param] = self._dll.eciGetVoiceParam(self._handle, 0, param)

    def get_state(self) -> Dict[str, Dict[int, int]]:
        return {"params": dict(self._params), "voiceParams": dict(self._voice_params)}

    # ------------------------------------------------------------------
    # Callbacks from Eloquence
    def _on_callback(self, handle, message, length, user_data):
        if not self._speaking:
            return 2
        LOGGER.debug("Callback message=%s length=%s", message, length)
        if message == 0:
            if self._audio_buffer.tell() >= self._samples * 2:
                self._flush_audio()
            data = ctypes.string_at(cast(self._buffer, c_void_p), length * ctypes.sizeof(c_short))
            self._audio_buffer.write(data)
        elif message == 2:
            is_final = length == FINAL_INDEX
            index_value = length if not is_final else None
            self._flush_audio(index_value, force=True, final=is_final)
            if is_final:
                self._speaking = False
        return 1

    def _flush_audio(self, index: Optional[int] = None, force: bool = False, final: bool = False) -> None:
        if self._audio_buffer.tell() == 0:
            if force or final:
                self._send_event("audio", data=b"", index=index, final=final)
            return
        payload = self._audio_buffer.getvalue()
        self._audio_buffer.seek(0)
        self._audio_buffer.truncate(0)
        self._send_event("audio", data=payload, index=index, final=final)


class HostController:
    def __init__(self, conn: Client):
        self._conn = conn
        self._runtime: Optional[EloquenceRuntime] = None
        self._handlers = {
            "initialize": self._handle_initialize,
            "addText": self._handle_add_text,
            "insertIndex": self._handle_insert_index,
            "synthesize": self._handle_synthesize,
            "stop": self._handle_stop,
            "delete": self._handle_delete,
            "setParam": self._handle_set_param,
            "setVoiceParam": self._handle_set_voice_param,
            "copyVoice": self._handle_copy_voice,
        }

    def serve_forever(self) -> None:
        LOGGER.info("Host controller waiting for commands")
        while True:
            try:
                message = self._conn.recv()
            except (EOFError, ConnectionError, OSError) as exc:
                LOGGER.info("Connection closed, stopping host controller: %s", exc)
                break
            if not isinstance(message, dict):
                LOGGER.warning("Unexpected message %r", message)
                continue
            msg_type = message.get("type")
            if msg_type != "command":
                LOGGER.warning("Unsupported message %s", msg_type)
                continue
            msg_id = message.get("id")
            command = message.get("command")
            handler = self._handlers.get(command)
            if handler is None:
                LOGGER.error("Unknown command %s", command)
                self._conn.send({"type": "response", "id": msg_id, "error": "unknownCommand"})
                continue
            try:
                payload = handler(**message.get("payload", {}))
                self._conn.send({"type": "response", "id": msg_id, "payload": payload})
            except Exception as exc:
                LOGGER.exception("Command %s failed", command)
                self._conn.send({"type": "response", "id": msg_id, "error": str(exc)})

    # ------------------------------------------------------------------
    # Command handlers
    def _handle_initialize(self, **payload):
        config = HostConfig(
            eci_path=payload["eciPath"],
            data_directory=payload["dataDirectory"],
            language_code=payload["language"],
            enable_abbrev_dict=payload.get("enableAbbreviationDict", False),
            enable_phrase_prediction=payload.get("enablePhrasePrediction", False),
            voice_variant=payload.get("voiceVariant", 0),
        )
        self._runtime = EloquenceRuntime(self._conn, config)
        self._runtime.start()
        return self._runtime.get_state()

    def _handle_add_text(self, text: bytes):
        self._runtime.add_text(text)
        return {"status": "ok"}

    def _handle_insert_index(self, value: int):
        self._runtime.insert_index(value)
        return {"status": "ok"}

    def _handle_synthesize(self):
        self._runtime.synthesize()
        return {"status": "ok"}

    def _handle_stop(self):
        self._runtime.stop()
        return {"status": "ok"}

    def _handle_delete(self):
        if self._runtime:
            self._runtime.delete()
        return {"status": "ok"}

    def _handle_set_param(self, paramId: int, value: int):
        self._runtime.set_param(paramId, value)
        return self._runtime.get_state()

    def _handle_set_voice_param(self, paramId: int, value: int, temporary: bool = False):
        self._runtime.set_voice_param(paramId, value, temporary=temporary)
        if temporary:
            return {"voiceParams": {paramId: value}}
        return self._runtime.get_state()

    def _handle_copy_voice(self, variant: int):
        self._runtime.copy_voice(variant)
        return self._runtime.get_state()


def main() -> None:
    parser = argparse.ArgumentParser(description="Eloquence 32-bit helper")
    parser.add_argument("--address", required=True)
    parser.add_argument("--authkey", required=True)
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()

    configure_logging(args.log_dir)
    LOGGER.info("Connecting to controller at %s", args.address)

    host, port_str = args.address.split(":")
    address = (host, int(port_str))
    authkey = bytes.fromhex(args.authkey)
    conn = Client(address, authkey=authkey)
    controller = HostController(conn)
    controller.serve_forever()


if __name__ == "__main__":
    main()
