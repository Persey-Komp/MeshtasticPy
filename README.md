# Meshtastic USB connection from a Linux laptop

Instructions and a graphical chat client for talking to a Heltec LoRa 32 board
running Meshtastic firmware over a USB cable.

## What you need

- Meshtastic board or device with Meshtastic firmware flashed
- A USB **data** cable (micro-USB on V2, USB-C on V3)
- A Linux laptop with Python 3.8 or newer

## 1. Plug the device in

Connect the device or board to your laptop with the USB cable. If the board's
OLED screen lights up, the cable is probably fine — charge-only cables will
not create a serial port.

## 2. Find the serial port

    ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
    dmesg | tail

Heltec boards usually appear as:

- `/dev/ttyUSB0` — V2 boards (CP2102 USB-serial chip)
- `/dev/ttyACM0` — V3 boards (native USB)

## 3. Fix serial permissions

Add yourself to the `dialout` group so you can open the port without `sudo`:

    sudo usermod -a -G dialout $USER

Log out and back in (or reboot) for the change to take effect. You can check
with:

    groups

## 4. Install the Python library

    python3 -m venv venv
    source venv/bin/activate
    pip install meshtastic

The GUI uses `tkinter`, which ships with most Python installs. On
Debian/Ubuntu install it with `sudo apt install python3-tk` if it is missing.

## 5. Run the chat client

    python3 meshtastic_connect.py

Then in the window:

1. Enter the port (e.g. `/dev/ttyUSB0`) or leave it empty to auto-detect, and
   click **Connect**.
2. Pick a node from the list on the left to send it a direct message, or keep
   **everyone (broadcast)** selected to message the whole mesh.
3. Type a message at the bottom and press **Enter** or click **Send**.
4. Incoming messages appear in the chat area with a timestamp.

You can pre-fill the port from the command line:

    python3 meshtastic_connect.py --port /dev/ttyUSB0

## 6. Terminal-only mode (optional)

If you prefer the original command-line behaviour:

    python3 meshtastic_connect.py --cli                 # listen for packets
    python3 meshtastic_connect.py --cli --send "hello"  # send, then exit

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Permission denied` on the port | Make sure you are in the `dialout` group (step 3) |
| No serial port appears | Try another cable — charge-only cables do not work |
| `Failed to connect` | Close other programs using the port (e.g. the Meshtastic web client, `screen`, `minicom`) |
| `tkinter is not available` | Install it with `sudo apt install python3-tk`, or use `--cli` mode |
| Board not responding | Hold the PRG button while plugging in to force bootloader mode, then reflash firmware |
# MeshtasticPy
