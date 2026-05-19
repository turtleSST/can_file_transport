from __future__ import annotations

import math
import os
import struct
import time
from pathlib import Path

from controlcan_bus import (
    ControlCANBus,
    VCI_USBCAN2,
    build_channel_configs,
    find_library_root,
)


CAN_DEVICE_TYPE = VCI_USBCAN2
DEFAULT_CAN_CHANNEL = 0
DEFAULT_CAN_BITRATE = 1000000

META_ID = 0x700
DATA_ID_BASE = 0x701
DATA_ID_MASK = 0x7F
END_ID = 0x6FF

APP_MAGIC = b"CTF2"
APP_VERSION = 2
META_FRAME = struct.Struct("<4sBBH")
META_DETAIL = struct.Struct("<QI")
END_FRAME = struct.Struct("<4sI")
DATA_PAYLOAD_SIZE = 8
TX_BATCH_FRAMES = 512
RX_BATCH_FRAMES = 1024
RX_WAIT_TIMEOUT_SECONDS = 0.005
WRITE_BUFFER_SIZE = 64 * 1024
CAN_STANDARD_8B_FRAME_BITS_NO_STUFF = 111
CAN_STANDARD_8B_FRAME_BITS_WITH_STUFF_ESTIMATE = 130
PROGRESS_STEP_PERCENT = 10
SHOW_PERFORMANCE_COUNTERS = False

BITRATE_OPTIONS = {
    "1": 500000,
    "2": 1000000,
}


def new_send_perf_counters() -> dict[str, int | float]:
    return {
        "file_read_calls": 0,
        "file_read_bytes": 0,
        "file_read_seconds": 0.0,
        "build_calls": 0,
        "build_frames": 0,
        "build_seconds": 0.0,
        "tx_submit_calls": 0,
        "tx_submit_frames": 0,
        "tx_submit_seconds": 0.0,
    }


def new_recv_perf_counters() -> dict[str, int | float]:
    return {
        "rx_fetch_calls": 0,
        "rx_fetch_frames": 0,
        "rx_empty_batches": 0,
        "rx_fetch_seconds": 0.0,
        "rx_process_seconds": 0.0,
        "file_write_calls": 0,
        "file_write_bytes": 0,
        "file_write_seconds": 0.0,
        "file_truncate_calls": 0,
        "file_truncate_seconds": 0.0,
    }


def counter_value(counters: dict[str, int | float], key: str) -> float:
    return float(counters.get(key, 0))


def counter_int(counters: dict[str, int | float], key: str) -> int:
    return int(counters.get(key, 0))


def format_rate(bytes_count: int, seconds: float) -> str:
    if seconds <= 0:
        return "0.00 KB/s"
    return f"{(bytes_count / 1024) / seconds:.2f} KB/s"


def format_frames_per_second(frame_count: int, seconds: float) -> str:
    if seconds <= 0:
        return "0.00 frames/s"
    return f"{frame_count / seconds:.2f} frames/s"


def format_bus_load(frame_count: int, seconds: float, bitrate: int) -> str:
    if seconds <= 0 or bitrate <= 0:
        return "0.0%-0.0%"
    frame_rate = frame_count / seconds
    low = (frame_rate * CAN_STANDARD_8B_FRAME_BITS_NO_STUFF / bitrate) * 100
    high = (frame_rate * CAN_STANDARD_8B_FRAME_BITS_WITH_STUFF_ESTIMATE / bitrate) * 100
    return f"{low:.1f}%-{high:.1f}%"


def print_send_performance(
    app_perf: dict[str, int | float],
    driver_perf: dict[str, int | float],
    elapsed_seconds: float,
    file_size: int,
    bitrate: int,
) -> None:
    tx_submit_seconds = counter_value(app_perf, "tx_submit_seconds")
    driver_seconds = counter_value(driver_perf, "tx_driver_seconds")
    retry_sleep_seconds = counter_value(driver_perf, "tx_retry_sleep_seconds")
    wrapper_seconds = max(0.0, tx_submit_seconds - driver_seconds - retry_sleep_seconds)

    print("\nPerformance counters (send):")
    print(
        "  App: "
        f"read={counter_int(app_perf, 'file_read_calls')} calls/"
        f"{counter_int(app_perf, 'file_read_bytes')} bytes/"
        f"{counter_value(app_perf, 'file_read_seconds'):.6f}s, "
        f"build={counter_int(app_perf, 'build_frames')} frames/"
        f"{counter_value(app_perf, 'build_seconds'):.6f}s, "
        f"submit={counter_int(app_perf, 'tx_submit_calls')} calls/"
        f"{counter_int(app_perf, 'tx_submit_frames')} frames/"
        f"{tx_submit_seconds:.6f}s"
    )
    print(
        "  ControlCAN TX: "
        f"dll_calls={counter_int(driver_perf, 'tx_dll_calls')}, "
        f"requested={counter_int(driver_perf, 'tx_dll_frames_requested')}, "
        f"accepted={counter_int(driver_perf, 'tx_dll_frames_accepted')}, "
        f"partial={counter_int(driver_perf, 'tx_partial_results')}, "
        f"zero={counter_int(driver_perf, 'tx_zero_results')}, "
        f"errors={counter_int(driver_perf, 'tx_error_results')}"
    )
    print(
        "  Time split: "
        f"elapsed={elapsed_seconds:.6f}s ({format_rate(file_size, elapsed_seconds)}), "
        f"data_frame_rate={format_frames_per_second(counter_int(app_perf, 'build_frames'), elapsed_seconds)}, "
        f"bus_load_est={format_bus_load(counter_int(app_perf, 'build_frames'), elapsed_seconds, bitrate)}@{bitrate}bps, "
        f"dll={driver_seconds:.6f}s, "
        f"retry_sleep={retry_sleep_seconds:.6f}s, "
        f"wrapper_est={wrapper_seconds:.6f}s"
    )


def print_recv_performance(
    app_perf: dict[str, int | float],
    driver_perf: dict[str, int | float],
    elapsed_seconds: float,
    received_size: int,
    bitrate: int,
) -> None:
    rx_fetch_seconds = counter_value(app_perf, "rx_fetch_seconds")
    driver_seconds = counter_value(driver_perf, "rx_driver_seconds")
    wrapper_seconds = max(0.0, rx_fetch_seconds - driver_seconds)

    print("\nPerformance counters (receive):")
    print(
        "  App: "
        f"fetch={counter_int(app_perf, 'rx_fetch_calls')} calls/"
        f"{counter_int(app_perf, 'rx_fetch_frames')} frames/"
        f"{rx_fetch_seconds:.6f}s, "
        f"empty_batches={counter_int(app_perf, 'rx_empty_batches')}, "
        f"process={counter_value(app_perf, 'rx_process_seconds'):.6f}s"
    )
    print(
        "  File: "
        f"writes={counter_int(app_perf, 'file_write_calls')} calls/"
        f"{counter_int(app_perf, 'file_write_bytes')} bytes/"
        f"{counter_value(app_perf, 'file_write_seconds'):.6f}s, "
        f"truncate={counter_int(app_perf, 'file_truncate_calls')} calls/"
        f"{counter_value(app_perf, 'file_truncate_seconds'):.6f}s"
    )
    print(
        "  ControlCAN RX: "
        f"dll_calls={counter_int(driver_perf, 'rx_dll_calls')}, "
        f"capacity={counter_int(driver_perf, 'rx_requested_capacity')}, "
        f"received={counter_int(driver_perf, 'rx_frames_received')}, "
        f"returned={counter_int(driver_perf, 'rx_frames_returned')}, "
        f"empty={counter_int(driver_perf, 'rx_empty_results')}, "
        f"errors={counter_int(driver_perf, 'rx_error_results')}, "
        f"buffer_allocations={counter_int(driver_perf, 'rx_buffer_allocations')}"
    )
    print(
        "  Time split: "
        f"elapsed={elapsed_seconds:.6f}s ({format_rate(received_size, elapsed_seconds)}), "
        f"data_frame_rate={format_frames_per_second(counter_int(app_perf, 'rx_fetch_frames'), elapsed_seconds)}, "
        f"bus_load_est={format_bus_load(counter_int(app_perf, 'rx_fetch_frames'), elapsed_seconds, bitrate)}@{bitrate}bps, "
        f"dll={driver_seconds:.6f}s, "
        f"wrapper_est={wrapper_seconds:.6f}s"
    )


def timed_write_buffer(
    file_obj,
    write_buffer: bytearray,
    perf: dict[str, int | float],
) -> None:
    if not write_buffer:
        return
    bytes_to_write = len(write_buffer)
    write_start = time.perf_counter()
    file_obj.write(write_buffer)
    perf["file_write_seconds"] += time.perf_counter() - write_start
    perf["file_write_calls"] += 1
    perf["file_write_bytes"] += bytes_to_write
    write_buffer.clear()


def timed_truncate(
    file_obj,
    size: int,
    perf: dict[str, int | float],
) -> None:
    truncate_start = time.perf_counter()
    file_obj.truncate(size)
    perf["file_truncate_seconds"] += time.perf_counter() - truncate_start
    perf["file_truncate_calls"] += 1


def normalize_input_path(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return str(Path(value).expanduser().resolve())


def ask_can_channel(default: int = DEFAULT_CAN_CHANNEL) -> int:
    value = input(f"Enter CAN channel (0/1, default {default}): ").strip()
    if not value:
        return default
    try:
        channel = int(value, 10)
    except ValueError:
        print(f"Invalid channel, using default {default}.")
        return default
    if channel < 0:
        print(f"Invalid channel, using default {default}.")
        return default
    return channel


def ask_can_bitrate(default: int = DEFAULT_CAN_BITRATE) -> int:
    print("Choose CAN bitrate:")
    print("1. 500 kbps")
    print("2. 1 Mbps")
    default_option = "1" if default == 500000 else "2"
    value = input(f"Enter bitrate option (1/2, default {default_option}): ").strip()
    if not value:
        return default
    bitrate = BITRATE_OPTIONS.get(value)
    if bitrate is None:
        print(f"Invalid bitrate, using default {default}.")
        return default
    return bitrate


def init_can_bus(channel: int, bitrate: int):
    library_root = find_library_root()
    print(f"Initializing ControlCAN: channel {channel}, bitrate {bitrate}...")

    try:
        bus = ControlCANBus(
            channel=channel,
            libpath=str(library_root),
            device_type=CAN_DEVICE_TYPE,
            device_index=0,
            configs=build_channel_configs(channel + 1, bitrate),
        )
        print("Device initialized successfully.\n")
        return bus
    except Exception as exc:
        print("\n[Initialization failed] Check:")
        print("1. The target PC has the ZLG USBCAN-I/II driver and VC++ runtime.")
        print("2. The exe should include `library/windows/x86_64/ControlCAN.dll` and `kerneldlls`.")
        print("3. If error 126 remains, ControlCAN.dll dependencies or the driver are still missing.")
        print(f"Details: {exc}\n")
        return None


def build_meta_frames(file_size: int, frame_count: int) -> list[tuple[int, bytes]]:
    header = META_FRAME.pack(APP_MAGIC, APP_VERSION, 0, frame_count & 0xFFFF)
    detail = META_DETAIL.pack(int(file_size), int(frame_count))
    return [
        (META_ID, header),
        (META_ID, detail[:8]),
        (META_ID, detail[8:]),
    ]


def parse_meta_payload(parts: list[bytes]) -> tuple[int, int]:
    if len(parts) != 3:
        raise ValueError(f"Invalid metadata frame count: {len(parts)}")

    magic, version, _reserved, frame_count_low = META_FRAME.unpack(parts[0])
    if magic != APP_MAGIC or version != APP_VERSION:
        raise ValueError("Invalid metadata header")

    detail = parts[1] + parts[2]
    if len(detail) != META_DETAIL.size:
        raise ValueError(f"Invalid metadata detail size: {len(detail)}")

    file_size, frame_count = META_DETAIL.unpack(detail)
    if frame_count_low != (frame_count & 0xFFFF):
        raise ValueError("Metadata frame count check failed")
    return int(file_size), int(frame_count)


def build_data_frames(chunk: bytes, start_sequence: int) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    sequence = start_sequence
    for offset in range(0, len(chunk), DATA_PAYLOAD_SIZE):
        piece = chunk[offset : offset + DATA_PAYLOAD_SIZE]
        arbitration_id = DATA_ID_BASE + (sequence & DATA_ID_MASK)
        frames.append((arbitration_id, piece))
        sequence += 1
    return frames


def build_end_frame(frame_count: int) -> tuple[int, bytes]:
    return (END_ID, END_FRAME.pack(APP_MAGIC, int(frame_count)))


def total_data_frames(file_size: int) -> int:
    if file_size <= 0:
        return 0
    return math.ceil(file_size / DATA_PAYLOAD_SIZE)


def progress_step_frames(total_frames: int) -> int:
    if total_frames <= 0:
        return 0
    return max(1, math.ceil(total_frames * PROGRESS_STEP_PERCENT / 100))


def send_mode():
    file_path = normalize_input_path(
        input("Enter the file path to send, e.g. test.bin: ")
    )

    if not os.path.exists(file_path):
        print(f"Error: file not found: {file_path}\n")
        return

    channel = ask_can_channel()
    bitrate = ask_can_bitrate()
    bus = init_can_bus(channel, bitrate)
    if not bus:
        return

    try:
        file_size = os.path.getsize(file_path)
        frame_count = total_data_frames(file_size)
        print(
            f"Preparing to send: {os.path.basename(file_path)} "
            f"(size: {file_size} bytes, CAN data frames: {frame_count})"
        )

        app_perf = new_send_perf_counters()
        bus.reset_performance_counters()
        start_time = time.time()
        meta_frames = build_meta_frames(file_size, frame_count)
        submit_start = time.perf_counter()
        bus.transmit_raw_frames(meta_frames, channel=channel)
        app_perf["tx_submit_seconds"] += time.perf_counter() - submit_start
        app_perf["tx_submit_calls"] += 1
        app_perf["tx_submit_frames"] += len(meta_frames)

        sent_frames = 0
        sequence = 0
        next_progress = progress_step_frames(frame_count)
        progress_step = next_progress
        read_size = DATA_PAYLOAD_SIZE * TX_BATCH_FRAMES
        with open(file_path, "rb") as file_obj:
            while True:
                read_start = time.perf_counter()
                chunk = file_obj.read(read_size)
                app_perf["file_read_seconds"] += time.perf_counter() - read_start
                app_perf["file_read_calls"] += 1
                if not chunk:
                    break
                app_perf["file_read_bytes"] += len(chunk)

                build_start = time.perf_counter()
                frames = build_data_frames(chunk, sequence)
                app_perf["build_seconds"] += time.perf_counter() - build_start
                app_perf["build_calls"] += 1
                app_perf["build_frames"] += len(frames)

                submit_start = time.perf_counter()
                bus.transmit_raw_frames(
                    frames,
                    channel=channel,
                    max_batch=TX_BATCH_FRAMES,
                )
                app_perf["tx_submit_seconds"] += time.perf_counter() - submit_start
                app_perf["tx_submit_calls"] += 1
                app_perf["tx_submit_frames"] += len(frames)

                sequence += len(frames)
                sent_frames += len(frames)

                if frame_count and (
                    sent_frames >= next_progress or sent_frames == frame_count
                ):
                    percent = (sent_frames / frame_count) * 100
                    print(
                        f"Sent {sent_frames}/{frame_count} CAN data frames "
                        f"({percent:.1f}%)"
                    )
                    while next_progress <= sent_frames:
                        next_progress += progress_step

        end_frames = [build_end_frame(frame_count)]
        submit_start = time.perf_counter()
        bus.transmit_raw_frames(end_frames, channel=channel)
        app_perf["tx_submit_seconds"] += time.perf_counter() - submit_start
        app_perf["tx_submit_calls"] += 1
        app_perf["tx_submit_frames"] += len(end_frames)

        cost = time.time() - start_time
        speed = (file_size / 1024) / cost if cost > 0 else 0

        print("\nSend completed.")
        print(f"Elapsed: {cost:.2f} s | Average speed: {speed:.2f} KB/s\n")
        if SHOW_PERFORMANCE_COUNTERS:
            print_send_performance(
                app_perf,
                bus.performance_snapshot(),
                cost,
                file_size,
                bitrate,
            )
    except Exception as exc:
        print(f"\nError during send: {exc}\n")
    finally:
        bus.shutdown()


def recv_mode():
    save_path = normalize_input_path(
        input("Enter output file name or path, e.g. received_test.bin: ")
    )
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)

    channel = ask_can_channel()
    bitrate = ask_can_bitrate()
    bus = init_can_bus(channel, bitrate)
    if not bus:
        return

    print(f"Waiting for data... (will save to: {save_path})")

    try:
        meta_parts: list[bytes] = []
        meta_received = False
        expected_size = 0
        expected_frames = 0
        expected_sequence = 0
        received_size = 0
        start_time = None
        transfer_done = False
        write_buffer = bytearray()
        next_progress = 0
        progress_step = 0
        app_perf = new_recv_perf_counters()
        bus.reset_performance_counters()

        with open(save_path, "wb") as file_obj:
            while not transfer_done:
                fetch_start = time.perf_counter()
                batch = bus.receive_raw_frames(
                    channel=channel,
                    max_frames=RX_BATCH_FRAMES,
                    timeout=RX_WAIT_TIMEOUT_SECONDS,
                )
                app_perf["rx_fetch_seconds"] += time.perf_counter() - fetch_start
                app_perf["rx_fetch_calls"] += 1
                app_perf["rx_fetch_frames"] += len(batch)
                if not batch:
                    app_perf["rx_empty_batches"] += 1

                process_start = time.perf_counter()
                for arbitration_id, payload in batch:
                    if arbitration_id == META_ID:
                        if meta_received:
                            continue
                        meta_parts.append(payload)
                        if len(meta_parts) == 3:
                            expected_size, expected_frames = parse_meta_payload(meta_parts)
                            meta_received = True
                            start_time = time.time()
                            print(
                                "Sender detected, receiving... "
                                f"file size: {expected_size} bytes, "
                                f"CAN data frames: {expected_frames}"
                            )
                            progress_step = progress_step_frames(expected_frames)
                            next_progress = progress_step
                            if expected_size == 0:
                                return
                        continue

                    if arbitration_id == END_ID:
                        if not meta_received:
                            continue
                        _magic, end_frame_count = END_FRAME.unpack(payload)
                        if _magic != APP_MAGIC:
                            raise ValueError("Invalid end frame")
                        if end_frame_count != expected_frames:
                            raise ValueError(
                                "End frame count mismatch: "
                                f"got {end_frame_count}, expected {expected_frames}"
                            )
                        if received_size >= expected_size:
                            timed_write_buffer(file_obj, write_buffer, app_perf)
                            timed_truncate(file_obj, expected_size, app_perf)
                            transfer_done = True
                            break
                        continue

                    if not meta_received:
                        continue
                    if not (DATA_ID_BASE <= arbitration_id <= DATA_ID_BASE + DATA_ID_MASK):
                        continue
                    if not payload:
                        continue

                    sequence_low = (arbitration_id - DATA_ID_BASE) & DATA_ID_MASK
                    expected_sequence_low = expected_sequence & DATA_ID_MASK
                    if sequence_low != expected_sequence_low:
                        raise ValueError(
                            "Unexpected CAN frame sequence: "
                            f"got low byte {sequence_low}, "
                            f"expected {expected_sequence_low}"
                        )

                    remaining = max(0, expected_size - received_size)
                    if remaining:
                        write_buffer.extend(payload[:remaining])
                        received_size += min(len(payload), remaining)
                        if len(write_buffer) >= WRITE_BUFFER_SIZE:
                            timed_write_buffer(file_obj, write_buffer, app_perf)

                    expected_sequence += 1
                    if expected_frames and (
                        expected_sequence >= next_progress
                        or expected_sequence == expected_frames
                    ):
                        percent = (expected_sequence / expected_frames) * 100
                        print(
                            f"Received {expected_sequence}/{expected_frames} "
                            f"CAN data frames ({percent:.1f}%)"
                        )
                        while next_progress <= expected_sequence:
                            next_progress += progress_step

                    if received_size >= expected_size and expected_sequence >= expected_frames:
                        timed_write_buffer(file_obj, write_buffer, app_perf)
                        timed_truncate(file_obj, expected_size, app_perf)
                        transfer_done = True
                        break
                app_perf["rx_process_seconds"] += time.perf_counter() - process_start

        if transfer_done:
            cost = time.time() - start_time if start_time else 0
            speed = (received_size / 1024) / cost if cost > 0 else 0

            print("\nReceive completed.")
            print(
                f"File size: {received_size} bytes | "
                f"Elapsed: {cost:.2f} s | Average speed: {speed:.2f} KB/s\n"
            )
            if SHOW_PERFORMANCE_COUNTERS:
                print_recv_performance(
                    app_perf,
                    bus.performance_snapshot(),
                    cost,
                    received_size,
                    bitrate,
                )
    except KeyboardInterrupt:
        print("\nReceive cancelled by user.\n")
    except Exception as exc:
        print(f"\nError during receive: {exc}\n")
    finally:
        bus.shutdown()


def main():
    while True:
        print("=" * 45)
        print("  CAN file transfer tool (raw CAN)")
        print("  Hardware: ZLG USBCAN-II")
        print("=" * 45)
        print("1. Send file")
        print("2. Receive file")
        print("3. Exit")
        print("=" * 45)

        choice = input("Choose an option (1/2/3): ").strip()

        if choice == "1":
            send_mode()
        elif choice == "2":
            recv_mode()
        elif choice == "3":
            print("Exit program.")
            break
        else:
            print("Invalid option, please try again.\n")


if __name__ == "__main__":
    main()
