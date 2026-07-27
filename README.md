RNode AX.25 Packet Terminal

A lightweight Python client for talking **AX.25 packet radio** (connected-mode
BBS/node sessions, plus optional UI/broadcast frames like APRS) through an
**RNode** device running in **TNC mode**, over a plain KISS serial connection.

No AGWPE, no BPQ32, no soundcard TNC — just Python and a serial port.

Two interfaces are included:

- **`rnode_ax25_tnc.py`** — a simple single-line command prompt.
- **`rnode_ax25_tnc_ui.py`** — a split-screen terminal in the style of classic
  packet radio software (Linpac and similar): a scrolling traffic pane on
  top, status bar and command line at the bottom.

Both share the same protocol engine (`ax25_core.py`) and produce timestamped
session log files.

[WATCH VIDEO](https://www.youtube.com/watch?v=uz7uIuNDnCc)

## What is RNode?

[RNode](https://unsigned.io/rnode/) is an open-source firmware/hardware
project (part of the [Reticulum](https://reticulum.network/) ecosystem) that
turns a LoRa radio module into a general-purpose packet radio interface,
exposed to the host computer as a serial [KISS](https://en.wikipedia.org/wiki/KISS_(TNC))
TNC. Besides its native Reticulum ("Normal") mode, recent RNode firmware
versions include a dedicated **TNC mode**, which makes the device behave as
a standards-compliant AX.25 KISS TNC — exactly what classic packet radio
software (BPQ32, AGWPE, Linpac, `ax25-tools` on Linux, etc.) expects.

This project talks directly to an RNode in **TNC mode**, using standard
AX.25 framing over KISS.

### Tested hardware

- **Heltec WiFi LoRa 32 V3** (ESP32-S3 + Semtech **SX1262**), flashed with
  RNode firmware, **Device mode: TNC**, radio parameters (frequency,
  bandwidth, spreading factor, coding rate, TX power) already configured on
  the device (e.g. via [`rnodeconf`](https://github.com/markqvist/Reticulum)).

It should work with any RNode-compatible board (Heltec, LilyGO T-Beam,
RAK, etc.) that runs RNode firmware in TNC mode, since the software only
relies on the standard KISS/AX.25 layer — nothing board-specific.

> **Important:** if the RNode is already in TNC mode with valid radio
> parameters, this tool does **not** need to (and by default does not) send
> any radio configuration commands. Only pass `--freq/--bw/--sf/--cr/--power`
> if you specifically need to (re)configure the radio from this tool — RNode
> reuses the same low command bytes (0x01–0x06) for radio configuration that
> classic KISS TNCs use for TXDELAY/Persistence/SlotTime/etc. Sending the
> wrong value on the wrong byte can set an out-of-range parameter (e.g. an
> invalid coding rate) and trigger the firmware's "radio lock", silently
> blocking transmission until the device is reset. When in doubt, leave
> these options unset.

## Features

- Full AX.25 connected-mode session (`SABM` → `UA`, numbered `I`-frames,
  `RR` acknowledgements, `DISC` → `UA`) — real BBS/node "converse mode"
  sessions, not just one-shot UI frames.
- Optional standalone UI frames (e.g. for APRS-style traffic).
- Classic TNC-style commands: `C CALLSIGN` to connect, `D` to disconnect —
  the same syntax used by BPQ32/AGWPE terminals.
- CP437 decoding for node banners/menus that use box-drawing ASCII art.
- Command history (arrow up/down) in both interfaces.
- Per-session timestamped log file, written next to the scripts.
- No automatic/accidental transmissions: when not connected, plain text is
  **not** sent anywhere unless you explicitly connect (`C CALLSIGN`) or use
  the explicit UI-send command (`U <text>`).

## Requirements

- Python 3.8+
- An RNode device, in **TNC mode**, connected over USB serial.

Install dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` installs `pyserial` everywhere, and automatically adds
`windows-curses` and `pyreadline3` on Windows only (needed for the
split-screen UI and for command history, respectively). No extra steps are
needed on Linux/macOS — `curses` and `readline` are already part of the
standard library there.

## Installation

```bash
git clone https://github.com/iz3mez/RNode-AX25-Packet-Terminal.git
cd <repository-folder>
pip install -r requirements.txt
```

Make sure `ax25_core.py` stays in the same folder as the two scripts — both
interfaces import it directly.

## Usage

### Find your serial port

- Windows: check Device Manager (e.g. `COM2`)
- Linux: usually `/dev/ttyUSB0` or `/dev/ttyACM0`
- macOS: usually `/dev/tty.usbserial-XXXX`

### Single-line CLI

```bash
python3 rnode_ax25_tnc.py COM8 --mycall CALL-1
```

### Split-screen UI (Linpac-style)

```bash
python3 rnode_ax25_tnc_ui.py COM8 --mycall CALL-1
```

Both accept the same options:

| Option           | Description                                              |
|------------------|------------------------------------------------------------|
| `port`           | Serial port (positional, default `COM8`)                  |
| `--baud`         | Serial baud rate (default `115200`)                       |
| `--mycall`       | Your callsign, with optional `-SSID` (**required**)        |
| `--dest`         | Default destination for UI frames (default `APRS`)        |
| `--digi`         | Comma-separated digipeaters for UI frames                 |
| `--freq/--bw/--sf/--cr/--power` | Radio config, only if you need to override what's already on the device — see warning above |
| `--force-online` | Force RNode "Normal mode" radio-online command (rarely needed) |
| `--debug`        | Print every outgoing KISS frame in hex                    |

### Commands

| Command                | Action                                                        |
|-------------------------|----------------------------------------------------------------|
| `C CALLSIGN[-SSID]`     | Connect to a node (sends `SABM`), e.g. `C IZ3MEZ-7`            |
| `D`                     | Disconnect (`DISC`)                                            |
| `<text>`                | While connected: send as data to the remote node (adds `<CR>`) |
| `U <text>`              | Send an explicit UI frame (only while **not** connected)       |
| `/dest CALL-SSID`       | Set the destination used for UI frames                         |
| `/digi CALL,CALL2`      | Set digipeaters used for UI frames                              |
| `/connect CALL-SSID`    | Long form of `C`                                                |
| `/disconnect`           | Long form of `D`                                                |
| `/quit`                 | Exit                                                            |
| ↑ / ↓ (arrow keys)      | Recall previous commands                                        |

A bare `Enter` while connected sends an empty `<CR>` — useful to answer a
node's `Continue...>` pagination prompt.

### Example session

```
> C IZ3MEZ-7
[TX] IZ3MEZ-1 > IZ3MEZ-7 (attempt 1/3)
*** Connected to IZ3MEZ-7
+-----------------------------------------------+
|   NODE BPQ  LoRa IZ3MEZ-7 JN55XK PADOVA ITA    |
+-----------------------------------------------+
Type ? to see the commands or INFO for information.
MEZNOD:IZ3MEZ-7}
> ?
[TX I N(S)=0] IZ3MEZ-1 > IZ3MEZ-7: ?
> D
[TX] IZ3MEZ-1 > IZ3MEZ-7 DISC
*** Disconnected from IZ3MEZ-7
```

[WATCH VIDEO](https://www.youtube.com/watch?v=uz7uIuNDnCc)

## Log files

Every run creates a file named `rnode_ax25_YYYYMMDD_HHMMSS.log` in the same
directory as the scripts, containing a timestamped copy of everything shown
on screen (status messages and received/sent data) — useful for reviewing
sessions or DX cluster spots later.

## Project structure

```
ax25_core.py           Protocol engine: KISS framing, AX.25 encode/decode,
                        connected-mode session state machine, RX thread,
                        logging helper. No UI code.
rnode_ax25_tnc.py       Single-line CLI front-end.
rnode_ax25_tnc_ui.py    Split-screen (curses) front-end.
requirements.txt        pip dependencies (with platform markers).
```

## Limitations

- One AX.25 session at a time (no multi-connect).
- Simplified connected-mode: no automatic retransmission on timeout, no
  `SREJ`, and incoming connection requests (someone else connecting to you)
  are always rejected with `DM` — this client only originates connections.
- Not a general-purpose AX.25 stack: no digipeating, no AXIP, no auto-answer.

## License

MIT License — see [LICENSE](LICENSE). Edit the copyright line in that file
with your name/callsign before publishing.
