CAN File Transport Tool (ISO-TP / Raw CAN)
============================================

Project Overview
----------------
This project is a Windows command-line file transfer tool based on CAN bus.
It is designed for physically isolated intranet environments where two computers
need to exchange files through ZLG USBCAN-I/II hardware.

The current program uses the ZLG ControlCAN driver through a direct Python ctypes
wrapper, and provides a high-performance raw CAN batch transfer mode.

Main features:
- Send files over CAN bus.
- Receive files over CAN bus.
- Support ZLG USBCAN-II hardware.
- Support CAN channel selection: channel 0 or channel 1.
- Support bitrate selection: 500 kbps or 1 Mbps.
- Can be packaged as a standalone Windows exe with PyInstaller.

Supported Environment
---------------------
- Operating system: Windows 10 / Windows 11
- Hardware: ZLG USBCAN-I / USBCAN-II compatible adapter
- Required driver: ZLG USBCAN driver / ControlCAN runtime
- Recommended runtime: Microsoft Visual C++ Redistributable

Driver Download
---------------
Before running the program on a target computer, install the official ZLG USBCAN
driver first.

Driver download URL:
https://manual.zlg.cn/server/index.php?s=/api/attachment/visitFile/sign/06a7c5543726f10bafb61ebd734a196a

For public releases, it is recommended to provide the driver download link instead
of redistributing the driver installer directly, unless you have confirmed that
redistribution is allowed by the driver vendor.

How To Run
----------
Run the packaged executable:

    main.exe

Then select one of the menu options:

    1. Send file
    2. Receive file
    3. Exit

Both sender and receiver should use the same CAN bitrate and matching CAN channel
settings. In normal use, start the receiver first, then start the sender.

Build From Source
-----------------
Install Python dependencies:

    pip install pyinstaller

Build the executable:

    python -m PyInstaller --clean --noconfirm main.spec

The generated executable will be placed in the dist directory.

Important Notes
---------------
- The target computer must have the ZLG USBCAN driver installed.
- The target computer should have the Microsoft VC++ runtime installed.
- If the program cannot initialize the device, check the USB-CAN adapter, driver,
  CAN channel, bitrate, and wiring.
- Sender and receiver must use the same bitrate, such as 500 kbps or 1 Mbps.
- For better throughput, use 1 Mbps when the CAN hardware, cable length, and bus
  quality allow it.
- This tool is designed for controlled environments. Always verify received files
  before using them in production.

Project Files
-------------
- main.py: command-line application and file transfer protocol.
- controlcan_bus.py: direct wrapper for ZLG ControlCAN.dll.
- main.spec: PyInstaller build configuration.
- library/: driver runtime files used during packaging/runtime lookup.
- ControlCANx64/: local ZLG ControlCAN reference files.

License
-------
See LICENSE for the project license.

Third-party drivers and vendor libraries are subject to their own licenses and
redistribution terms.
