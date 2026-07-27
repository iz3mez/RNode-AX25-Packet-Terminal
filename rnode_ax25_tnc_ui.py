#!/usr/bin/env python3
"""
rnode_ax25_tnc_ui.py
---------------------
Split-screen interface, in the style of old packet radio terminals
(Linpac and similar): scrollable received/sent traffic area at the top,
status bar + command line at the bottom. Uses the same AX.25/KISS engine
as rnode_ax25_tnc.py (ax25_core.py module).

Requirements:
    pip install -r requirements.txt
    - On Windows you also need: pip install windows-curses
    - On Linux/macOS 'curses' is already included in the standard library.

Usage:
    python3 rnode_ax25_tnc_ui.py COM8 --mycall IZ3MEZ-1

Commands (same as the single-line version):
    C CALLSIGN[-SSID]   connect (SABM)
    D                   disconnect (DISC)
    U <text>            send an explicit UI frame (only when not connected)
    /dest CALL-SSID     set UI destination
    /digi CALL,CALL2    set UI digipeaters
    /quit or Ctrl+C     exit

A timestamped log file is written to the same directory as this script.

73 de Francesco IZ3MEZ
"""

import sys
import argparse
import threading
import time
import curses

try:
    import serial
except ImportError:
    print("You need to install pyserial: pip install pyserial")
    sys.exit(1)

import ax25_core as core


def sanitize_for_display(text: str) -> str:
    """Rimuove caratteri di controllo"""
    cleaned = []
    for ch in text:
        if ch in ("\n", "\t"):
            cleaned.append(ch)
        elif ch == "\r":
            continue
        elif ord(ch) < 32 or ord(ch) == 127:
            continue  # scarta i caratteri di controllo non stampabili (es. BEL)
        else:
            cleaned.append(ch)
    result = "".join(cleaned)
    return "\n".join(line.rstrip(" ") for line in result.split("\n"))


class TerminalUI:
    """Gestisce il rendering a schermo diviso"""

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.lock = threading.Lock()
        self.log_lines = []          # righe COMPLETE (terminate da un vero \n)
        self.pending = ""            # riga in corso, non ancora terminata da \n
        self.input_buf = ""
        self.mycall = ""
        self.state_label = "DISCONNECTED"
        self.history = []            # comandi inviati, dal piu' vecchio al piu' recente
        self.history_pos = None      # indice corrente durante la navigazione, None = non in navigazione
        self.history_stash = ""      # testo che si stava digitando prima di navigare la cronologia

        curses.curs_set(1)
        stdscr.nodelay(False)
        self._build_windows()

    def _build_windows(self):
        self.height, self.width = self.stdscr.getmaxyx()
        self.log_h = max(3, self.height - 2)
        self.log_win = curses.newwin(self.log_h, self.width, 0, 0)
        self.status_win = curses.newwin(1, self.width, self.log_h, 0)
        self.input_win = curses.newwin(1, self.width, self.log_h + 1, 0)
        self.log_win.scrollok(True)
        self.input_win.keypad(True)
        self.input_win.timeout(100)  # non-blocking con poll ogni 100ms

    def resize(self):
        with self.lock:
            self._build_windows()
            self._redraw_log()
            self._redraw_status()
            self._redraw_input()

    def _wrapped(self, line):
        w = max(1, self.width - 1)
        if line == "":
            return [""]
        return [line[i:i + w] for i in range(0, len(line), w)] or [""]

    def _redraw_log(self):
        self.log_win.erase()
        visible_lines = self.log_lines + ([self.pending] if self.pending else [])
        all_wrapped = []
        for line in visible_lines:
            all_wrapped.extend(self._wrapped(line))
        visible = all_wrapped[-self.log_h:]
        for i, line in enumerate(visible):
            try:
                self.log_win.addnstr(i, 0, line, self.width - 1)
            except curses.error:
                pass
        self.log_win.noutrefresh()

    def _redraw_status(self):
        self.status_win.erase()
        label = f" {self.mycall}  |  {self.state_label} "
        try:
            self.status_win.addnstr(0, 0, label.ljust(self.width - 1), self.width - 1, curses.A_REVERSE)
        except curses.error:
            pass
        self.status_win.noutrefresh()

    def _redraw_input(self):
        self.input_win.erase()
        text = "> " + self.input_buf
        try:
            self.input_win.addnstr(0, 0, text, self.width - 1)
        except curses.error:
            pass
        self.input_win.move(0, min(len(text), self.width - 1))
        self.input_win.noutrefresh()

    def _commit(self):
        curses.doupdate()

    def _push_complete_line(self, line: str):
        self.log_lines.append(line)
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-2000:]

    def add_status(self, msg: str):
        with self.lock:
            if self.pending:
                self._push_complete_line(self.pending)
                self.pending = ""
            for line in msg.split("\n"):
                self._push_complete_line(line)
            self._redraw_log()
            self._redraw_input()
            self._commit()

    def add_data(self, text: str):
        with self.lock:
            combined = self.pending + text
            parts = combined.split("\n")
            for complete in parts[:-1]:
                self._push_complete_line(complete)
            self.pending = parts[-1]
            self._redraw_log()
            self._redraw_input()
            self._commit()

    def set_status(self, mycall: str, state_label: str):
        with self.lock:
            self.mycall = mycall
            self.state_label = state_label
            self._redraw_status()
            self._redraw_input()
            self._commit()

    def get_key(self):
        with self.lock:
            ch = self.input_win.getch()
        return None if ch == -1 else ch

    def edit_input(self, ch):
        with self.lock:
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                self.input_buf = self.input_buf[:-1]
            elif 32 <= ch <= 126:
                self.input_buf += chr(ch)
            self.history_pos = None  # digitare a mano esce dalla navigazione della cronologia
            self._redraw_input()
            self._commit()

    def history_add(self, line: str):
        with self.lock:
            if not self.history or self.history[-1] != line:
                self.history.append(line)
            if len(self.history) > 200:
                self.history = self.history[-200:]
            self.history_pos = None

    def history_navigate(self, direction: int):
        with self.lock:
            if not self.history:
                return
            if self.history_pos is None:
                self.history_stash = self.input_buf
                self.history_pos = len(self.history)
            self.history_pos += direction
            if self.history_pos < 0:
                self.history_pos = 0
            if self.history_pos >= len(self.history):
                self.history_pos = len(self.history)
                self.input_buf = self.history_stash
            else:
                self.input_buf = self.history[self.history_pos]
            self._redraw_input()
            self._commit()

    def pop_input(self):
        with self.lock:
            line = self.input_buf
            self.input_buf = ""
            self._redraw_input()
            self._commit()
            return line


STATE_LABELS = {
    "DISCONNECTED": "DISCONNECTED",
    "CONNECTING": "CONNECTING...",
    "CONNECTED": "CONNECTED",
    "DISCONNECTING": "DISCONNECTING...",
}


def run(stdscr, args):
    ui = TerminalUI(stdscr)
    ui.set_status(args.mycall, STATE_LABELS["DISCONNECTED"])

    log_path = core.default_log_path()
    logger = core.SessionLogger(log_path)

    def on_status(msg):
        ui.add_status(msg)
        logger.write(msg)

    def on_data(text):
        cleaned = sanitize_for_display(text)
        if cleaned:
            ui.add_data(cleaned)
            logger.write(cleaned)

    ui.add_status(f"Log file: {log_path}")
    ui.add_status(core.WELCOME_TEXT)
    logger.write(core.WELCOME_TEXT)

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as e:
        curses.endwin()
        print(f"Could not open {args.port}: {e}")
        sys.exit(1)

    time.sleep(1.0)
    core.send_rnode_config(ser, args.freq, args.bw, args.sf, args.cr, args.power,
                            force_online=args.force_online, on_log=on_status)

    ui_state = {"dest": args.dest, "digis": [d.strip() for d in args.digi.split(",") if d.strip()]}
    sess = core.Ax25Session(args.mycall)
    ser_write_lock = threading.Lock()

    stop_event = threading.Event()
    rx_thread = threading.Thread(
        target=core.rx_loop, args=(ser, stop_event, sess, ser_write_lock, on_status, on_data),
        daemon=True)
    rx_thread.start()

    # Periodically updates the status bar based on the session state
    def status_watcher():
        last = None
        while not stop_event.is_set():
            with sess.lock:
                cur = sess.state
            if cur != last:
                ui.set_status(args.mycall, STATE_LABELS.get(cur, cur))
                last = cur
            time.sleep(0.2)

    threading.Thread(target=status_watcher, daemon=True).start()

    try:
        while True:
            ch = ui.get_key()
            if ch is None:
                continue
            if ch == curses.KEY_RESIZE:
                ui.resize()
                continue
            if ch == curses.KEY_UP:
                ui.history_navigate(-1)
                continue
            if ch == curses.KEY_DOWN:
                ui.history_navigate(1)
                continue
            if ch in (curses.KEY_ENTER, 10, 13):
                line = ui.pop_input()
                with sess.lock:
                    currently_connected = (sess.state == "CONNECTED")
                if not line and not currently_connected:
                    continue
                if line:
                    ui.history_add(line)
                ui.add_status(f"> {line}")

                is_meta = line.startswith("/") or (
                    not currently_connected and (
                        line.upper() == "D" or line.upper().startswith("C ")
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
                        on_status("Usage: C CALLSIGN-SSID")
                        continue
                    target = parts[1].strip().upper()
                    core.do_connect(ser, ser_write_lock, sess, args.mycall, target,
                                     ui_state["digis"], on_status, debug=args.debug)

                elif is_meta and (line.startswith("/disconnect") or line.upper() == "D"):
                    core.do_disconnect(ser, ser_write_lock, sess, args.mycall, on_status)

                elif (not currently_connected) and line.upper().startswith("U "):
                    text = line[2:]
                    frame = core.build_ui_frame(ui_state["dest"], args.mycall, ui_state["digis"], text)
                    with ser_write_lock:
                        ser.write(core.kiss_build_frame(frame))
                    on_status(f"[TX UI] {args.mycall} > {ui_state['dest']}: {text}")

                else:
                    if currently_connected:
                        core.do_send(ser, ser_write_lock, sess, args.mycall, line,
                                     ui_state["dest"], ui_state["digis"], on_status)
                    else:
                        on_status("*** Not connected: text NOT transmitted. "
                                  "Use 'C CALLSIGN' or 'U <text>'.")
            else:
                ui.edit_input(ch)

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        time.sleep(0.3)
        ser.close()
        logger.close()


def main():
    parser = argparse.ArgumentParser(description="Split-screen UI for RNode TNC mode")
    parser.add_argument("port", nargs="?", default="COM8")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--mycall", required=True)
    parser.add_argument("--dest", default="APRS")
    parser.add_argument("--digi", default="")
    parser.add_argument("--freq", type=int, default=None)
    parser.add_argument("--bw", type=int, default=None)
    parser.add_argument("--sf", type=int, default=None)
    parser.add_argument("--cr", type=int, default=None)
    parser.add_argument("--power", type=int, default=None)
    parser.add_argument("--force-online", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    curses.wrapper(run, args)
    print("Serial connection closed.")


if __name__ == "__main__":
    main()
