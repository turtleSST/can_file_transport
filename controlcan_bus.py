from __future__ import annotations

import ctypes
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Optional, Sequence

import can
from can import CanError, CanInitializationError, Message
from can.bus import LOG

_DLL_DIR_HANDLES = []

VCI_PCI5121 = 1
VCI_PCI9810 = 2
VCI_USBCAN1 = 3
VCI_USBCAN2 = 4
VCI_USBCAN2A = 4
VCI_PCI9820 = 5
VCI_CAN232 = 6
VCI_PCI5110 = 7
VCI_CANLITE = 8
VCI_ISA9620 = 9
VCI_ISA5420 = 10
VCI_PC104CAN = 11
VCI_CANETUDP = 12
VCI_CANETE = 12
VCI_DNP9810 = 13
VCI_PCI9840 = 14
VCI_PC104CAN2 = 15
VCI_PCI9820I = 16
VCI_CANETTCP = 17
VCI_PEC9920 = 18
VCI_PCIE_9220 = 18
VCI_PCI5010U = 19
VCI_USBCAN_E_U = 20
VCI_USBCAN_2E_U = 21
VCI_PCI5020U = 22
VCI_EG20T_CAN = 23
VCI_PCIE9221 = 24
VCI_WIFICAN_TCP = 25
VCI_WIFICAN_UDP = 26
VCI_PCIe9120 = 27
VCI_PCIe9110 = 28
VCI_PCIe9140 = 29
VCI_USBCAN_4E_U = 31
VCI_CANDTU_200UR = 32
VCI_CANDTU_MINI = 33
VCI_USBCAN_8E_U = 34
VCI_CANREPLAY = 35
VCI_CANDTU_NET = 36
VCI_CANDTU_100UR = 37

STANDARD_BITRATES = {
    5000: (0xBF, 0xFF),
    10000: (0x31, 0x1C),
    20000: (0x18, 0x1C),
    40000: (0x87, 0xFF),
    50000: (0x09, 0x1C),
    80000: (0x83, 0xFF),
    100000: (0x04, 0x1C),
    125000: (0x03, 0x1C),
    200000: (0x81, 0xFA),
    250000: (0x01, 0x1C),
    400000: (0x80, 0xFA),
    500000: (0x00, 0x1C),
    800000: (0x00, 0x16),
    1000000: (0x00, 0x14),
}

CONTROL_CAN_ERROR_FLAGS = {
    0x0001: "CAN controller FIFO overflow",
    0x0002: "CAN controller error alarm",
    0x0004: "CAN controller error passive",
    0x0008: "CAN arbitration lost",
    0x0010: "CAN bus error",
    0x0020: "CAN bus off",
    0x0040: "CAN controller buffer overflow",
    0x0100: "Device already opened",
    0x0200: "Open device failed",
    0x0400: "Device not opened",
    0x0800: "Device buffer overflow",
    0x1000: "Device does not exist",
    0x2000: "Load kernel DLL failed",
    0x4000: "Command failed",
    0x8000: "Buffer create failed",
}


CONTROL_CAN_PERF_DEFAULTS = {
    "tx_dll_calls": 0,
    "tx_dll_frames_requested": 0,
    "tx_dll_frames_accepted": 0,
    "tx_partial_results": 0,
    "tx_zero_results": 0,
    "tx_error_results": 0,
    "tx_driver_seconds": 0.0,
    "tx_retry_sleep_seconds": 0.0,
    "rx_dll_calls": 0,
    "rx_requested_capacity": 0,
    "rx_frames_received": 0,
    "rx_frames_returned": 0,
    "rx_empty_results": 0,
    "rx_error_results": 0,
    "rx_driver_seconds": 0.0,
    "rx_buffer_allocations": 0,
}


def new_controlcan_perf_counters() -> dict[str, int | float]:
    return dict(CONTROL_CAN_PERF_DEFAULTS)


class VCI_CAN_OBJ(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint32),
        ("TimeStamp", ctypes.c_uint32),
        ("TimeFlag", ctypes.c_ubyte),
        ("SendType", ctypes.c_ubyte),
        ("RemoteFlag", ctypes.c_ubyte),
        ("ExternFlag", ctypes.c_ubyte),
        ("DataLen", ctypes.c_ubyte),
        ("Data", ctypes.c_ubyte * 8),
        ("Reserved", ctypes.c_ubyte * 3),
    ]


class VCI_INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("AccCode", ctypes.c_uint32),
        ("AccMask", ctypes.c_uint32),
        ("Reserved", ctypes.c_uint32),
        ("Filter", ctypes.c_ubyte),
        ("Timing0", ctypes.c_ubyte),
        ("Timing1", ctypes.c_ubyte),
        ("Mode", ctypes.c_ubyte),
    ]


class VCI_CAN_STATUS(ctypes.Structure):
    _fields_ = [
        ("ErrInterrupt", ctypes.c_ubyte),
        ("regMode", ctypes.c_ubyte),
        ("regStatus", ctypes.c_ubyte),
        ("regALCapture", ctypes.c_ubyte),
        ("regECCapture", ctypes.c_ubyte),
        ("regEWLimit", ctypes.c_ubyte),
        ("regRECounter", ctypes.c_ubyte),
        ("regTECounter", ctypes.c_ubyte),
        ("Reserved", ctypes.c_uint32),
    ]


class VCI_ERR_INFO(ctypes.Structure):
    _fields_ = [
        ("ErrCode", ctypes.c_uint32),
        ("Passive_ErrData", ctypes.c_ubyte * 3),
        ("ArLost_ErrData", ctypes.c_ubyte),
    ]


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return app_base_dir().joinpath(*parts)


def add_dll_directory(path: Path) -> None:
    path = Path(path)
    if os.name != "nt" or not path.is_dir():
        return

    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(path) + os.pathsep + current_path

    if hasattr(os, "add_dll_directory"):
        _DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))


def has_controlcan_layout(root: Path) -> bool:
    root = Path(root)
    return (
        root.is_dir()
        and (
            (root / "ControlCAN.dll").is_file()
            or (root / "windows" / "x86_64" / "ControlCAN.dll").is_file()
            or (root / "windows" / "x64" / "ControlCAN.dll").is_file()
        )
    )


def candidate_library_roots() -> Iterable[Path]:
    env_path = os.environ.get("ZLG_LIBRARY_DIR") or os.environ.get("ZCAN_LIBRARY")
    if env_path:
        yield Path(env_path)

    yield resource_path("library")
    yield resource_path("ControlCANx64")

    cwd = Path.cwd()
    yield cwd / "library"
    yield cwd / "ControlCANx64"


def find_library_root() -> Path:
    for root in candidate_library_roots():
        if has_controlcan_layout(root):
            return root
    return resource_path("library")


def resolve_driver_root(library_root: Path) -> Path:
    library_root = Path(library_root)
    candidates = [
        library_root,
        library_root / "windows" / "x86_64",
        library_root / "windows" / "x64",
        library_root / "ControlCANx64",
        resource_path("ControlCANx64"),
        Path.cwd() / "ControlCANx64",
    ]

    for root in candidates:
        if (root / "ControlCAN.dll").is_file():
            return root

    raise CanInitializationError(f"ControlCAN.dll was not found under: {library_root}")


def prepare_runtime_paths(library_root: Path) -> Path:
    driver_root = resolve_driver_root(library_root)
    for path in (
        driver_root,
        driver_root / "kerneldlls",
    ):
        add_dll_directory(path)
    return driver_root


def load_controlcan_library(driver_root: Path) -> ctypes.WinDLL:
    dll_path = Path(driver_root) / "ControlCAN.dll"
    if not dll_path.is_file():
        raise CanInitializationError(f"ControlCAN.dll was not found: {dll_path}")
    if not hasattr(ctypes, "WinDLL"):
        raise CanInitializationError("ControlCAN.dll can only be loaded on Windows")

    dll = ctypes.WinDLL(str(dll_path))
    dll.VCI_OpenDevice.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    dll.VCI_OpenDevice.restype = ctypes.c_uint32

    dll.VCI_CloseDevice.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    dll.VCI_CloseDevice.restype = ctypes.c_uint32

    dll.VCI_InitCAN.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(VCI_INIT_CONFIG),
    ]
    dll.VCI_InitCAN.restype = ctypes.c_uint32

    dll.VCI_StartCAN.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    dll.VCI_StartCAN.restype = ctypes.c_uint32

    dll.VCI_ReadErrInfo.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(VCI_ERR_INFO),
    ]
    dll.VCI_ReadErrInfo.restype = ctypes.c_uint32

    dll.VCI_ReadCANStatus.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(VCI_CAN_STATUS),
    ]
    dll.VCI_ReadCANStatus.restype = ctypes.c_uint32

    dll.VCI_GetReceiveNum.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    dll.VCI_GetReceiveNum.restype = ctypes.c_uint32

    dll.VCI_ResetCAN.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    dll.VCI_ResetCAN.restype = ctypes.c_uint32

    dll.VCI_ClearBuffer.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    dll.VCI_ClearBuffer.restype = ctypes.c_uint32

    dll.VCI_Transmit.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(VCI_CAN_OBJ),
        ctypes.c_uint32,
    ]
    dll.VCI_Transmit.restype = ctypes.c_uint32

    dll.VCI_Receive.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(VCI_CAN_OBJ),
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    dll.VCI_Receive.restype = ctypes.c_uint32

    return dll


def bitrate_to_timing(bitrate: int) -> tuple[int, int]:
    try:
        return STANDARD_BITRATES[int(bitrate)]
    except KeyError as exc:
        available = ", ".join(str(rate) for rate in sorted(STANDARD_BITRATES))
        raise CanInitializationError(
            f"Unsupported bitrate {bitrate}. Available values: {available}"
        ) from exc


def build_init_config(bitrate: int) -> VCI_INIT_CONFIG:
    timing0, timing1 = bitrate_to_timing(bitrate)
    return VCI_INIT_CONFIG(
        AccCode=0x00000000,
        AccMask=0xFFFFFFFF,
        Reserved=0x00000000,
        Filter=1,
        Timing0=timing0,
        Timing1=timing1,
        Mode=0,
    )


def build_channel_configs(channel_count: int, bitrate: int) -> list[dict[str, int]]:
    return [
        {"bitrate": bitrate, "resistance": 1}
        for _ in range(max(1, channel_count))
    ]


def to_message(channel: int, frame: VCI_CAN_OBJ) -> Message:
    payload_len = min(int(frame.DataLen), 8)
    payload = bytes(frame.Data[:payload_len])
    return Message(
        arbitration_id=int(frame.ID),
        is_extended_id=bool(frame.ExternFlag),
        is_remote_frame=bool(frame.RemoteFlag),
        is_error_frame=False,
        channel=channel,
        data=payload,
        timestamp=time.time(),
        dlc=payload_len,
    )


def from_message(msg: Message) -> VCI_CAN_OBJ:
    payload = bytes(msg.data)
    if len(payload) > 8:
        raise CanError("ControlCAN only supports classic CAN frames up to 8 bytes")

    frame = VCI_CAN_OBJ()
    frame.ID = int(msg.arbitration_id)
    frame.TimeStamp = 0
    frame.TimeFlag = 0
    frame.SendType = 0
    frame.RemoteFlag = 1 if msg.is_remote_frame else 0
    frame.ExternFlag = 1 if msg.is_extended_id else 0
    frame.DataLen = len(payload)
    for index, byte in enumerate(payload):
        frame.Data[index] = byte
    return frame


def raw_to_frame(arbitration_id: int, data: bytes, is_extended_id: bool = False) -> VCI_CAN_OBJ:
    payload = bytes(data)
    if len(payload) > 8:
        raise CanError("ControlCAN only supports classic CAN frames up to 8 bytes")

    frame = VCI_CAN_OBJ()
    frame.ID = int(arbitration_id)
    frame.TimeStamp = 0
    frame.TimeFlag = 0
    frame.SendType = 0
    frame.RemoteFlag = 0
    frame.ExternFlag = 1 if is_extended_id else 0
    frame.DataLen = len(payload)
    for index, byte in enumerate(payload):
        frame.Data[index] = byte
    return frame


class ControlCANBus(can.BusABC):
    def __init__(
        self,
        channel: int = 0,
        *,
        libpath: str = "library/",
        device_type: int,
        device_index: int = 0,
        derive: object = None,
        rx_queue_size: Optional[int] = None,
        configs: Sequence[dict] | None = None,
        can_filters: Optional[can.typechecking.CanFilters] = None,
        **kwargs: object,
    ):
        super().__init__(channel=channel, can_filters=can_filters, **kwargs)

        self._library_root = Path(libpath)
        self._device_type = int(device_type)
        self._device_index = int(device_index)
        self._derive = derive
        self._default_channel = int(channel)
        self._rx_queue: Deque[Message] = deque(
            maxlen=rx_queue_size if rx_queue_size and rx_queue_size > 0 else None
        )
        self._channel_configs = list(configs or [{"bitrate": 500000, "resistance": 1}])
        self._opened_channels: list[int] = []
        self._dll: ctypes.WinDLL | None = None
        self._device_opened = False
        self._driver_root: Path | None = None
        self._original_cwd: Path | None = None
        self._cwd_restored = False
        self._perf_counters = new_controlcan_perf_counters()
        self._receive_buffers: dict[int, tuple[int, object]] = {}

        try:
            self._driver_root = prepare_runtime_paths(self._library_root)
            self._original_cwd = Path.cwd()
            try:
                os.chdir(self._driver_root)
            except OSError:
                pass

            self._dll = load_controlcan_library(self._driver_root)
            self._open_device()
            self._initialize_channels()
        except Exception:
            self.shutdown()
            raise
        finally:
            self._restore_cwd()

    @property
    def channel_info(self) -> str:
        opened = ",".join(str(item) for item in self._opened_channels) or "none"
        return f"ControlCAN(device_type={self._device_type}, channels={opened})"

    def _restore_cwd(self) -> None:
        if self._cwd_restored or self._original_cwd is None:
            return
        try:
            os.chdir(self._original_cwd)
        except OSError:
            pass
        self._cwd_restored = True

    def _require_dll(self) -> ctypes.WinDLL:
        if self._dll is None:
            raise CanError("ControlCAN DLL not loaded")
        return self._dll

    def reset_performance_counters(self) -> None:
        self._perf_counters = new_controlcan_perf_counters()

    def performance_snapshot(self) -> dict[str, int | float]:
        return dict(self._perf_counters)

    def _receive_buffer(self, batch_size: int) -> object:
        cached = self._receive_buffers.get(batch_size)
        if cached is not None:
            return cached[1]

        frame_array = (VCI_CAN_OBJ * batch_size)()
        self._receive_buffers[batch_size] = (batch_size, frame_array)
        self._perf_counters["rx_buffer_allocations"] += 1
        return frame_array

    def _config_for_channel(self, channel: int) -> dict:
        if self._channel_configs and channel < len(self._channel_configs):
            return self._channel_configs[channel]
        if self._channel_configs:
            return self._channel_configs[-1]
        return {"bitrate": 500000, "resistance": 1}

    def _open_device(self) -> None:
        dll = self._require_dll()
        result = dll.VCI_OpenDevice(self._device_type, self._device_index, 0)
        if result == 0:
            raise CanInitializationError(
                "VCI_OpenDevice failed: "
                f"device_type={self._device_type}, device_index={self._device_index}"
            )
        self._device_opened = True

    def _initialize_channels(self) -> None:
        dll = self._require_dll()
        channel = self._default_channel
        cfg = self._config_for_channel(channel)
        bitrate = int(cfg.get("bitrate", 500000))
        init_cfg = build_init_config(bitrate)

        result = dll.VCI_InitCAN(
            self._device_type,
            self._device_index,
            channel,
            ctypes.byref(init_cfg),
        )
        if result == 0:
            raise CanInitializationError(
                f"VCI_InitCAN failed: channel={channel}, bitrate={bitrate}"
            )

        result = dll.VCI_StartCAN(self._device_type, self._device_index, channel)
        if result == 0:
            raise CanInitializationError(
                f"VCI_StartCAN failed: channel={channel}, bitrate={bitrate}"
            )

        dll.VCI_ClearBuffer(self._device_type, self._device_index, channel)
        self._opened_channels.append(channel)

    def send(
        self,
        msg: Message,
        timeout: Optional[float] = None,
        *,
        tx_mode: object = None,
    ) -> None:
        dll = self._require_dll()
        channel = self._default_channel if msg.channel is None else int(msg.channel)
        if channel not in self._opened_channels:
            raise CanError(f"CAN channel {channel} is not initialized")

        frame = from_message(msg)
        if tx_mode is not None:
            frame.SendType = int(tx_mode)
        frame_array = (VCI_CAN_OBJ * 1)()
        frame_array[0] = frame

        wait_timeout = 1.0 if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + wait_timeout
        attempts = 0
        last_result = 0

        while True:
            attempts += 1
            call_start = time.perf_counter()
            result = dll.VCI_Transmit(
                self._device_type,
                self._device_index,
                channel,
                frame_array,
                1,
            )
            call_elapsed = time.perf_counter() - call_start
            self._perf_counters["tx_dll_calls"] += 1
            self._perf_counters["tx_dll_frames_requested"] += 1
            self._perf_counters["tx_driver_seconds"] += call_elapsed
            last_result = int(result)
            if result == 1:
                self._perf_counters["tx_dll_frames_accepted"] += 1
                return
            if result == 0:
                self._perf_counters["tx_zero_results"] += 1
            else:
                self._perf_counters["tx_error_results"] += 1
            if time.monotonic() >= deadline:
                diagnostics = self._transmit_diagnostics(channel)
                raise CanError(
                    "VCI_Transmit failed: "
                    f"channel={channel}, arbitration_id=0x{msg.arbitration_id:X}, "
                    f"result={last_result}, attempts={attempts}. {diagnostics}"
                )
            sleep_start = time.perf_counter()
            time.sleep(0.001)
            self._perf_counters["tx_retry_sleep_seconds"] += (
                time.perf_counter() - sleep_start
            )

    def _read_can_status(self, channel: int) -> VCI_CAN_STATUS | None:
        dll = self._require_dll()
        status = VCI_CAN_STATUS()
        try:
            result = dll.VCI_ReadCANStatus(
                self._device_type,
                self._device_index,
                channel,
                ctypes.byref(status),
            )
        except Exception:
            return None
        return status if result else None

    def _read_err_info(self, channel: int) -> VCI_ERR_INFO | None:
        dll = self._require_dll()
        err_info = VCI_ERR_INFO()
        try:
            result = dll.VCI_ReadErrInfo(
                self._device_type,
                self._device_index,
                channel,
                ctypes.byref(err_info),
            )
        except Exception:
            return None
        return err_info if result else None

    @staticmethod
    def _format_error_flags(err_code: int) -> str:
        if err_code == 0:
            return "none"
        names = [
            name for flag, name in CONTROL_CAN_ERROR_FLAGS.items() if err_code & flag
        ]
        return ", ".join(names) if names else "unknown"

    def _transmit_diagnostics(self, channel: int) -> str:
        parts = []
        status = self._read_can_status(channel)
        if status is not None:
            parts.append(
                "status="
                f"ErrInterrupt=0x{status.ErrInterrupt:02X}, "
                f"regStatus=0x{status.regStatus:02X}, "
                f"rx_errors={status.regRECounter}, "
                f"tx_errors={status.regTECounter}"
            )

        err_info = self._read_err_info(channel)
        if err_info is not None:
            err_code = int(err_info.ErrCode)
            passive_data = " ".join(
                f"{int(value):02X}" for value in err_info.Passive_ErrData
            )
            parts.append(
                "error="
                f"code=0x{err_code:08X} ({self._format_error_flags(err_code)}), "
                f"passive_data={passive_data}, "
                f"arbitration_lost=0x{int(err_info.ArLost_ErrData):02X}"
            )

        if not parts:
            parts.append(
                "No status was returned by ControlCAN. Check cable, termination, "
                "bitrate, channel selection, and whether another CAN node is online."
            )

        return " | ".join(parts)

    def transmit_raw_frames(
        self,
        frames: Sequence[tuple[int, bytes]],
        *,
        channel: Optional[int] = None,
        timeout: float = 1.0,
        max_batch: int = 128,
    ) -> None:
        if not frames:
            return

        dll = self._require_dll()
        target_channel = self._default_channel if channel is None else int(channel)
        if target_channel not in self._opened_channels:
            raise CanError(f"CAN channel {target_channel} is not initialized")

        frame_items = list(frames)
        offset = 0
        total = len(frame_items)
        batch_size = max(1, int(max_batch))

        while offset < total:
            chunk = frame_items[offset : offset + batch_size]
            frame_array = (VCI_CAN_OBJ * len(chunk))()
            for index, (arbitration_id, payload) in enumerate(chunk):
                frame_array[index] = raw_to_frame(arbitration_id, payload)

            deadline = time.monotonic() + max(0.0, float(timeout))
            while True:
                call_start = time.perf_counter()
                result = dll.VCI_Transmit(
                    self._device_type,
                    self._device_index,
                    target_channel,
                    frame_array,
                    len(chunk),
                )
                call_elapsed = time.perf_counter() - call_start
                self._perf_counters["tx_dll_calls"] += 1
                self._perf_counters["tx_dll_frames_requested"] += len(chunk)
                self._perf_counters["tx_driver_seconds"] += call_elapsed
                sent = int(result)
                if 0 < sent != 0xFFFFFFFF:
                    accepted = min(sent, len(chunk))
                    self._perf_counters["tx_dll_frames_accepted"] += accepted
                    if accepted < len(chunk):
                        self._perf_counters["tx_partial_results"] += 1
                    offset += accepted
                    break

                if sent == 0:
                    self._perf_counters["tx_zero_results"] += 1
                else:
                    self._perf_counters["tx_error_results"] += 1

                if time.monotonic() >= deadline:
                    diagnostics = self._transmit_diagnostics(target_channel)
                    raise CanError(
                        "VCI_Transmit batch failed: "
                        f"channel={target_channel}, sent={offset}/{total}, "
                        f"result={sent}. {diagnostics}"
                    )
                sleep_start = time.perf_counter()
                time.sleep(0)
                self._perf_counters["tx_retry_sleep_seconds"] += (
                    time.perf_counter() - sleep_start
                )

    def receive_raw_frames(
        self,
        *,
        channel: Optional[int] = None,
        max_frames: int = 1024,
        timeout: Optional[float] = 0,
    ) -> list[tuple[int, bytes]]:
        dll = self._require_dll()
        target_channel = self._default_channel if channel is None else int(channel)
        if target_channel not in self._opened_channels:
            raise CanError(f"CAN channel {target_channel} is not initialized")

        batch_size = max(1, int(max_frames))
        wait_ms = -1 if timeout is None else max(0, int(timeout * 1000))
        frame_array = self._receive_buffer(batch_size)

        call_start = time.perf_counter()
        count = dll.VCI_Receive(
            self._device_type,
            self._device_index,
            target_channel,
            frame_array,
            batch_size,
            wait_ms,
        )
        call_elapsed = time.perf_counter() - call_start
        self._perf_counters["rx_dll_calls"] += 1
        self._perf_counters["rx_requested_capacity"] += batch_size
        self._perf_counters["rx_driver_seconds"] += call_elapsed
        count = int(count)
        if count == 0xFFFFFFFF:
            self._perf_counters["rx_error_results"] += 1
            raise CanError(
                f"VCI_Receive failed: channel={target_channel}. "
                f"{self._transmit_diagnostics(target_channel)}"
            )

        received = min(count, batch_size)
        self._perf_counters["rx_frames_received"] += received
        if received == 0:
            self._perf_counters["rx_empty_results"] += 1

        result: list[tuple[int, bytes]] = []
        for frame_index in range(received):
            frame = frame_array[frame_index]
            if frame.RemoteFlag:
                continue
            payload_len = min(int(frame.DataLen), 8)
            result.append((int(frame.ID), bytes(frame.Data[:payload_len])))
        self._perf_counters["rx_frames_returned"] += len(result)
        return result

    def _read_messages(self, timeout: Optional[float]) -> None:
        dll = self._require_dll()
        wait_ms = -1 if timeout is None else max(0, int(timeout * 1000))
        batch_size = 64
        frame_array = (VCI_CAN_OBJ * batch_size)()

        for index, channel in enumerate(self._opened_channels):
            channel_wait = wait_ms if index == 0 else 0
            count = dll.VCI_Receive(
                self._device_type,
                self._device_index,
                channel,
                frame_array,
                batch_size,
                channel_wait,
            )
            count = int(count)
            if count == 0xFFFFFFFF:
                raise CanError(
                    f"VCI_Receive failed: channel={channel}. "
                    f"{self._transmit_diagnostics(channel)}"
                )
            count = min(count, batch_size)
            for frame_index in range(count):
                self._rx_queue.append(to_message(channel, frame_array[frame_index]))

    def _recv_from_queue(self) -> tuple[Optional[Message], bool]:
        if not self._rx_queue:
            return None, False
        return self._rx_queue.popleft(), False

    def _recv_internal(self, timeout: Optional[float]) -> tuple[Optional[Message], bool]:
        if self._rx_queue:
            return self._recv_from_queue()

        if timeout is not None and timeout <= 0:
            self._read_messages(0)
            return self._recv_from_queue()

        if timeout is None:
            while not self._rx_queue:
                self._read_messages(None)
            return self._recv_from_queue()

        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                return None, False

            self._read_messages(remaining)
            if self._rx_queue:
                return self._recv_from_queue()

            time.sleep(0.001)

    def shutdown(self) -> None:
        LOG.debug("ControlCAN - shutdown.")
        super().shutdown()

        dll = self._dll
        if dll is not None and self._device_opened:
            for channel in reversed(self._opened_channels):
                try:
                    dll.VCI_ResetCAN(self._device_type, self._device_index, channel)
                except Exception:
                    pass
            try:
                dll.VCI_CloseDevice(self._device_type, self._device_index)
            except Exception:
                pass

        self._device_opened = False
        self._opened_channels.clear()
        self._restore_cwd()
