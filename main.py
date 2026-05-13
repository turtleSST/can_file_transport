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
DEFAULT_CAN_BITRATE = 500000

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
WRITE_BUFFER_SIZE = 64 * 1024

BITRATE_OPTIONS = {
    "1": 500000,
    "2": 1000000,
}


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
    print(
        f"Initializing ControlCAN: VCI_USBCAN2, channel {channel}, bitrate {bitrate}..."
    )
    print(f"Driver root: {library_root}")

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

        start_time = time.time()
        bus.transmit_raw_frames(build_meta_frames(file_size, frame_count), channel=channel)

        sent_frames = 0
        sequence = 0
        read_size = DATA_PAYLOAD_SIZE * TX_BATCH_FRAMES
        with open(file_path, "rb") as file_obj:
            while True:
                chunk = file_obj.read(read_size)
                if not chunk:
                    break

                frames = build_data_frames(chunk, sequence)
                bus.transmit_raw_frames(
                    frames,
                    channel=channel,
                    max_batch=TX_BATCH_FRAMES,
                )

                sequence += len(frames)
                sent_frames += len(frames)

                if frame_count and (
                    sent_frames == frame_count or sent_frames % 5000 == 0
                ):
                    percent = (sent_frames / frame_count) * 100
                    print(
                        f"Sent {sent_frames}/{frame_count} CAN data frames "
                        f"({percent:.1f}%)"
                    )

        bus.transmit_raw_frames([build_end_frame(frame_count)], channel=channel)

        cost = time.time() - start_time
        speed = (file_size / 1024) / cost if cost > 0 else 0

        print("\nSend completed.")
        print(f"Elapsed: {cost:.2f} s | Average speed: {speed:.2f} KB/s\n")
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

        with open(save_path, "wb") as file_obj:
            while not transfer_done:
                for arbitration_id, payload in bus.receive_raw_frames(
                    channel=channel,
                    max_frames=RX_BATCH_FRAMES,
                    timeout=0,
                ):
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
                            if write_buffer:
                                file_obj.write(write_buffer)
                                write_buffer.clear()
                            file_obj.truncate(expected_size)
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
                            file_obj.write(write_buffer)
                            write_buffer.clear()

                    expected_sequence += 1
                    if expected_frames and (
                        expected_sequence == expected_frames
                        or expected_sequence % 5000 == 0
                    ):
                        percent = (expected_sequence / expected_frames) * 100
                        print(
                            f"Received {expected_sequence}/{expected_frames} "
                            f"CAN data frames ({percent:.1f}%)"
                        )

                    if received_size >= expected_size and expected_sequence >= expected_frames:
                        if write_buffer:
                            file_obj.write(write_buffer)
                            write_buffer.clear()
                        file_obj.truncate(expected_size)
                        transfer_done = True
                        break

        if transfer_done:
            cost = time.time() - start_time if start_time else 0
            speed = (received_size / 1024) / cost if cost > 0 else 0

            print("\nReceive completed.")
            print(
                f"File size: {received_size} bytes | "
                f"Elapsed: {cost:.2f} s | Average speed: {speed:.2f} KB/s\n"
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
