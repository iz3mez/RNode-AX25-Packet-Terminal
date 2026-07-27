#!/usr/bin/env python3
"""
rnode_ax25_tnc.py
------------------
Single-line AX.25 client (connected mode SABM/UA/I/RR/DISC + optional UI)
over KISS for an RNode module in TNC mode.

Requirements:
    pip install -r requirements.txt

Usage:
    python3 rnode_ax25_tnc.py COM8 --mycall IZ3MEZ-1

Commands (classic TNC style, e.g. BPQ/AGWPE):
    C CALLSIGN[-SSID]     connect (SABM) to a node, e.g.: C IZ3MEZ-7
    D                     disconnect (DISC) the current session
    <text>                if connected: send as data (I-frame) to the remote node
                           if NOT connected: transmits nothing (to avoid
                           accidental transmissions); use "U <text>" to
                           explicitly send a UI frame instead
    U <text>              send <text> as a UI frame (works even while
                           connected, for "broadcast" traffic like APRS)
    /dest CALL-SSID       set the destination for UI frames
    /digi CALL,CALL2      set digipeaters for UI frames
    /connect CALL-SSID    long alias for "C"
    /disconnect           long alias for "D"
    /quit                 exit

For the split-screen, Linpac-style interface, see rnode_ax25_tnc_ui.py.
A timestamped log file is written to the same directory as this script.

73 de Francesco IZ3MEZ
"""

import sys
import argparse
import threading
import time

try:
    import serial
except ImportError:
    print("You need to install pyserial: pip install pyserial")
    sys.exit(1)

import ax25_core as core

try:
    import readline  # noqa: F401  # enables arrow-key command history (Linux/macOS)
except ImportError:
    try:
        import pyreadline3  # noqa: F401  # Windows equivalent, if installed: pip install pyreadline3
    except ImportError:
        pass  # on Windows the console still provides basic recall with the up arrow

# ---------------------------------------------------------------------------
# Console rendering: avoids double prompts / broken text during async I/O
# ---------------------------------------------------------------------------
console_lock = threading.Lock()
prompt_state = {"active": True}


def _async_prefix():
    if prompt_state["active"]:
        prompt_state["active"] = False
        return "\n"
    return ""


def status_print(msg: str):
    with console_lock:
        sys.stdout.write(_async_prefix() + msg + "\n")
        sys.stdout.flush()


def data_print(text: str):
    with console_lock:
        sys.stdout.write(_async_prefix() + text)
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="AX.25/KISS client for RNode TNC mode")
    parser.add_argument("port", nargs="?", default="COM8")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--mycall", required=True)
    parser.add_argument("--dest", default="APRS", help="Default destination for UI frames")
    parser.add_argument("--digi", default="", help="Digipeaters for UI frames, comma-separated")
    parser.add_argument("--freq", type=int, default=None, help="Frequency in Hz, e.g. 433375000")
    parser.add_argument("--bw", type=int, default=None, help="Bandwidth in Hz, e.g. 125000")
    parser.add_argument("--sf", type=int, default=None, help="Spreading factor, e.g. 9")
    parser.add_argument("--cr", type=int, default=None, help="Coding rate denominator (4/N), e.g. 5")
    parser.add_argument("--power", type=int, default=None, help="TX power in dBm, e.g. 14")
    parser.add_argument("--force-online", action="store_true",
                         help="Force the RNode 'Normal mode' CMD_RADIO_STATE=ON command "
                              "(usually NOT needed if the device is already in Device mode: TNC)")
    parser.add_argument("--debug", action="store_true", help="Print every KISS frame sent, in hex")
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}")
        sys.exit(1)

    log_path = core.default_log_path()
    logger = core.SessionLogger(log_path)

    def on_status(msg: str):
        status_print(msg)
        logger.write(msg)

    def on_data(text: str):
        data_print(text)
        logger.write(text)

    time.sleep(1.0)  # allow time for USB enumeration / board reset
    core.send_rnode_config(ser, args.freq, args.bw, args.sf, args.cr, args.power,
                            force_online=args.force_online, on_log=on_status)

    print(f"\nConnected to {args.port} @ {args.baud} baud. Mycall={args.mycall}")
    print(f"Log file: {log_path}\n")
    print(core.WELCOME_TEXT)
    logger.write(core.WELCOME_TEXT)

    ui_state = {"dest": args.dest, "digis": [d.strip() for d in args.digi.split(",") if d.strip()]}
    sess = core.Ax25Session(args.mycall)
    ser_write_lock = threading.Lock()

    stop_event = threading.Event()
    rx_thread = threading.Thread(
        target=core.rx_loop, args=(ser, stop_event, sess, ser_write_lock, on_status, on_data),
        daemon=True)
    rx_thread.start()

    try:
        while True:
            with console_lock:
                prompt_state["active"] = True
            line = input("> ")
            if not line:
                with sess.lock:
                    connected_now = (sess.state == "CONNECTED")
                if not connected_now:
                    continue
                # if connected, a bare Enter is a valid <CR> to send
                # (needed to answer "Continue...>"-style node prompts)

            with sess.lock:
                currently_connected = (sess.state == "CONNECTED")

            # In converse mode (connected) ONLY "/" meta-commands are
            # intercepted: everything else is data sent to the remote node.
            is_meta = line.startswith("/") or (
                not currently_connected and (
                    line.upper() == "D" or
                    line.upper().startswith("C ")
                )
            )

            if line.startswith("/quit"):
                break

            elif is_meta and line.startswith("/dest"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    ui_state["dest"] = parts[1].strip()
                    on_status(f"UI destination set to {ui_state['dest']}")

            elif is_meta and line.startswith("/digi"):
                parts = line.split(maxsplit=1)
                ui_state["digis"] = [d.strip() for d in parts[1].split(",")] if len(parts) == 2 else []
                on_status(f"UI digipeaters set: {ui_state['digis']}")

            elif is_meta and (line.startswith("/connect") or line.upper().startswith("C ")):
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    on_status("Usage: C CALLSIGN-SSID   (or /connect CALLSIGN-SSID)")
                    continue
                target = parts[1].strip().upper()
                core.do_connect(ser, ser_write_lock, sess, args.mycall, target,
                                 ui_state["digis"], on_status, debug=args.debug)

            elif is_meta and (line.startswith("/disconnect") or line.upper() == "D"):
                core.do_disconnect(ser, ser_write_lock, sess, args.mycall, on_status)

            elif (not currently_connected) and line.upper().startswith("U "):
                # explicit UI frame send (broadcast, does not require a connection)
                text = line[2:]
                frame = core.build_ui_frame(ui_state["dest"], args.mycall, ui_state["digis"], text)
                with ser_write_lock:
                    ser.write(core.kiss_build_frame(frame))
                digi_str = f" via {','.join(ui_state['digis'])}" if ui_state["digis"] else ""
                on_status(f"[TX UI] {args.mycall} > {ui_state['dest']}{digi_str}: {text}")

            else:
                if currently_connected:
                    core.do_send(ser, ser_write_lock, sess, args.mycall, line,
                                 ui_state["dest"], ui_state["digis"], on_status)
                else:
                    on_status("*** Not connected to any node: text was NOT transmitted.")
                    on_status("    Use 'C CALLSIGN' to connect, or 'U <text>' to "
                              "explicitly send a UI frame.")

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop_event.set()
        time.sleep(0.3)
        ser.close()
        logger.close()
        print("\nSerial connection closed.")


if __name__ == "__main__":
    main()
