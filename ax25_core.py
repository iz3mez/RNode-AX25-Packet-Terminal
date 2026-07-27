#!/usr/bin/env python3
"""
ax25_core.py
------------
Logica di protocollo condivisa: framing KISS, codifica/decodifica AX.25,
stato di sessione connessa (SABM/UA/I/RR/DISC) e thread di ricezione.
Non contiene nulla di specifico per l'interfaccia utente: le funzioni di
callback (on_status, on_data) vengono fornite da chi lo usa (CLI o UI
a schermo diviso).
73 de Francesco, IZ3MEZ
"""

import time
import threading
import os

try:
    import serial
except ImportError:
    serial = None

WELCOME_TEXT = """Welcome! Use 'C CALLSIGN' to connect, or 'U <text>' to send a UI frame.
Available commands:
  C CALLSIGN[-SSID]   Connect to a node
  D                   Disconnect
  U <text>            Send an explicit UI frame (only when not connected)
  /dest CALL-SSID     Set UI destination (default: APRS)
  /digi CALL,CALL2    Set UI digipeaters
  /quit               Exit
"""


class SessionLogger:
    """Writes every status/data line to a timestamped log file in the same
    directory as the script, so packet sessions can be reviewed later."""

    def __init__(self, path):
        self.lock = threading.Lock()
        self.path = path
        self.f = open(path, "a", encoding="utf-8")

    def write(self, text: str):
        with self.lock:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            for line in text.split("\n"):
                self.f.write(f"[{ts}] {line}\n")
            self.f.flush()

    def close(self):
        with self.lock:
            self.f.close()


def default_log_path():
    """Log file path: same directory as this module, one file per run."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(script_dir, f"rnode_ax25_{ts}.log")

# ---------------------------------------------------------------------------
# Costanti KISS
# ---------------------------------------------------------------------------
FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
KISS_CMD_DATA = 0x00

# Comandi RNode "Normal mode" (Reticulum) - da usare solo se il dispositivo
# NON e' gia' in modalita' TNC. Questi STESSI numeri di comando NON
# corrispondono ai comandi KISS storici (TXDELAY/Persistence/ecc): RNode non
# li implementa affatto. Non aggiungere comandi extra su questi byte senza
# sapere esattamente cosa fanno in RNode (un CR fuori range puo' attivare
# il "radio lock" del firmware e bloccare silenziosamente la TX).
CMD_FREQUENCY    = 0x01
CMD_BANDWIDTH    = 0x02
CMD_TXPOWER      = 0x03
CMD_SF           = 0x04
CMD_CR           = 0x05
CMD_RADIO_STATE  = 0x06
RADIO_STATE_ON   = 0x01


def kiss_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    return bytes(out)


def kiss_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == FESC and i + 1 < len(data):
            nxt = data[i + 1]
            if nxt == TFEND:
                out.append(FEND); i += 2; continue
            elif nxt == TFESC:
                out.append(FESC); i += 2; continue
        out.append(b)
        i += 1
    return bytes(out)


def kiss_build_frame(payload: bytes, port: int = 0, cmd: int = KISS_CMD_DATA) -> bytes:
    frame = bytearray()
    frame.append(FEND)
    frame.append(((port & 0x0F) << 4) | (cmd & 0x0F))
    frame += kiss_escape(payload)
    frame.append(FEND)
    return bytes(frame)


class KissDeframer:
    def __init__(self):
        self._buf = bytearray()
        self._in_frame = False

    def feed(self, data: bytes):
        frames = []  # lista di (cmd, payload)
        for b in data:
            if b == FEND:
                if self._in_frame and len(self._buf) > 0:
                    raw = kiss_unescape(bytes(self._buf))
                    if len(raw) > 0:
                        cmd = raw[0] & 0x0F
                        frames.append((cmd, raw[1:]))
                self._buf = bytearray()
                self._in_frame = True
            else:
                if self._in_frame:
                    self._buf.append(b)
        return frames


def send_rnode_config(ser, freq=None, bw=None, sf=None, cr=None, power=None,
                       force_online=False, on_log=print):
    """Invia (se specificati) i comandi di configurazione radio RNode 'Normal mode'.
    Se il dispositivo e' gia' in 'Device mode: TNC', questi comandi normalmente
    NON servono: il radio si auto-configura e si attiva da solo all'accensione."""
    def send_cmd(cmd, payload: bytes):
        ser.write(kiss_build_frame(payload, port=0, cmd=cmd))
        time.sleep(0.05)

    if freq is not None:
        send_cmd(CMD_FREQUENCY, int(freq).to_bytes(4, "big"))
        on_log(f"[CFG] Frequency set to {freq} Hz")
    if bw is not None:
        send_cmd(CMD_BANDWIDTH, int(bw).to_bytes(4, "big"))
        on_log(f"[CFG] Bandwidth set to {bw} Hz")
    if sf is not None:
        send_cmd(CMD_SF, bytes([int(sf)]))
        on_log(f"[CFG] Spreading factor set to {sf}")
    if cr is not None:
        send_cmd(CMD_CR, bytes([int(cr)]))
        on_log(f"[CFG] Coding rate set to 4/{cr}")
    if power is not None:
        send_cmd(CMD_TXPOWER, bytes([int(power)]))
        on_log(f"[CFG] TX power set to {power} dBm")
    if force_online:
        send_cmd(CMD_RADIO_STATE, bytes([RADIO_STATE_ON]))
        on_log("[CFG] Radio set ONLINE ('Normal mode' command, forced)")


# ---------------------------------------------------------------------------
# AX.25 - indirizzi e frame
# ---------------------------------------------------------------------------
def encode_callsign(callsign: str, final: bool = False, command_bit: bool = False) -> bytes:
    call = callsign.strip().upper()
    ssid = 0
    if "-" in call:
        call, ssid_str = call.split("-", 1)
        try:
            ssid = int(ssid_str)
        except ValueError:
            ssid = 0
    call = (call + "      ")[:6]
    out = bytearray(ord(c) << 1 for c in call)
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1)
    if command_bit:
        ssid_byte |= 0x80
    if final:
        ssid_byte |= 0x01
    out.append(ssid_byte)
    return bytes(out)


def decode_callsign(addr7: bytes):
    call = "".join(chr(b >> 1) for b in addr7[0:6]).strip()
    ssid = (addr7[6] >> 1) & 0x0F
    final = bool(addr7[6] & 0x01)
    label = f"{call}-{ssid}" if ssid else call
    return label, final


def build_address_field(dest: str, source: str, digis, command_bit=True) -> bytes:
    digis = digis or []
    out = bytearray()
    out += encode_callsign(dest, final=False, command_bit=command_bit)
    out += encode_callsign(source, final=(len(digis) == 0), command_bit=not command_bit)
    for i, d in enumerate(digis):
        out += encode_callsign(d, final=(i == len(digis) - 1))
    return bytes(out)


def decode_ax25_frame(frame: bytes):
    if len(frame) < 15:
        return None
    pos = 0
    addrs = []
    while pos + 7 <= len(frame):
        addr7 = frame[pos:pos + 7]
        label, final = decode_callsign(addr7)
        addrs.append(label)
        pos += 7
        if final:
            break
    if pos + 1 > len(frame):
        return None
    control = frame[pos]; pos += 1
    if (control & 0x01) == 0:
        pid = frame[pos]; pos += 1
    else:
        pid = None
    info = frame[pos:]

    dest = addrs[0] if len(addrs) > 0 else "?"
    source = addrs[1] if len(addrs) > 1 else "?"
    digis = addrs[2:]
    return dest, source, digis, control, pid, info


def parse_control(control: int):
    if (control & 0x01) == 0:
        return {"type": "I", "ns": (control >> 1) & 0x07, "nr": (control >> 5) & 0x07,
                "pf": (control >> 4) & 0x01}
    elif (control & 0x03) == 0x01:
        names = {0: "RR", 1: "RNR", 2: "REJ", 3: "SREJ"}
        return {"type": "S", "stype": names[(control >> 2) & 0x03],
                "nr": (control >> 5) & 0x07, "pf": (control >> 4) & 0x01}
    else:
        base = control & ~0x10
        names = {0x2F: "SABM", 0x6F: "SABME", 0x43: "DISC", 0x0F: "DM",
                 0x63: "UA", 0x87: "FRMR", 0x03: "UI"}
        return {"type": "U", "utype": names.get(base, f"UNKNOWN(0x{base:02X})"),
                "pf": (control >> 4) & 0x01}


def build_u_frame(dest, source, digis, utype, pf=1, command_bit=True):
    bases = {"SABM": 0x2F, "DISC": 0x43, "UA": 0x63, "DM": 0x0F}
    control = bases[utype] | (pf << 4)
    frame = bytearray(build_address_field(dest, source, digis, command_bit=command_bit))
    frame.append(control)
    return bytes(frame)


def build_i_frame(dest, source, digis, ns, nr, payload: bytes, pf=0, pid=0xF0):
    control = (nr << 5) | (pf << 4) | (ns << 1)
    frame = bytearray(build_address_field(dest, source, digis, command_bit=True))
    frame.append(control)
    frame.append(pid)
    frame += payload
    return bytes(frame)


def build_s_frame(dest, source, digis, stype, nr, pf=0):
    bases = {"RR": 0x01, "RNR": 0x05, "REJ": 0x09}
    control = bases[stype] | (nr << 5) | (pf << 4)
    frame = bytearray(build_address_field(dest, source, digis, command_bit=True))
    frame.append(control)
    return bytes(frame)


def build_ui_frame(dest, source, digis, info: str, pid=0xF0):
    frame = bytearray(build_address_field(dest, source, digis, command_bit=True))
    frame.append(0x03)
    frame.append(pid)
    frame += info.encode("utf-8", errors="replace")
    return bytes(frame)


# ---------------------------------------------------------------------------
# Stato connessione AX.25 (semplificato: una sessione alla volta)
# ---------------------------------------------------------------------------
class Ax25Session:
    def __init__(self, mycall):
        self.mycall = mycall
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.state = "DISCONNECTED"   # DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING
        self.peer = None
        self.digis = []
        self.vs = 0   # nostro N(S) prossimo da inviare
        self.vr = 0   # prossimo N(S) atteso dal peer
        self.event = threading.Event()


# ---------------------------------------------------------------------------
# RX thread - generico, usa callback per non dipendere dalla UI
# ---------------------------------------------------------------------------
def rx_loop(ser, stop_event, sess: Ax25Session, ser_write_lock, on_status, on_data):
    """on_status(msg): status messages (*** connected, disconnected, errors...)
       on_data(text): raw text received from the node while connected"""
    deframer = KissDeframer()
    while not stop_event.is_set():
        try:
            data = ser.read(256)
        except serial.SerialException as e:
            on_status(f"*** [Serial read error] {e}")
            break
        if not data:
            continue
        for cmd, raw in deframer.feed(data):
            if cmd != KISS_CMD_DATA:
                continue
            decoded = decode_ax25_frame(raw)
            if decoded is None:
                continue
            dest, source, digis, control, pid, info = decoded
            c = parse_control(control)

            with sess.lock:
                is_our_dest = (dest == sess.mycall)
                is_from_peer = (sess.peer is not None and source == sess.peer)

                if c["type"] == "U" and c["utype"] == "UA":
                    if sess.state == "CONNECTING" and is_from_peer:
                        sess.state = "CONNECTED"
                        sess.vs = 0
                        sess.vr = 0
                        on_status(f"*** Connected to {sess.peer}")
                        sess.event.set()
                    elif sess.state == "DISCONNECTING" and is_from_peer:
                        sess.state = "DISCONNECTED"
                        on_status(f"*** Disconnected from {sess.peer}")
                        sess.event.set()

                elif c["type"] == "U" and c["utype"] == "DM":
                    if sess.state in ("CONNECTING",) and is_from_peer:
                        on_status(f"*** Connection refused by {sess.peer} (DM)")
                        sess.state = "DISCONNECTED"
                        sess.event.set()
                    elif sess.state == "CONNECTED" and is_from_peer:
                        on_status(f"*** {sess.peer} reported disconnection (DM)")
                        sess.state = "DISCONNECTED"

                elif c["type"] == "U" and c["utype"] == "SABM" and is_our_dest:
                    on_status(f"*** Connection request from {source} rejected "
                              f"(only outgoing connections are supported)")
                    dm = build_u_frame(source, sess.mycall, [], "DM", pf=c["pf"], command_bit=False)
                    with ser_write_lock:
                        ser.write(kiss_build_frame(dm))

                elif c["type"] == "U" and c["utype"] == "DISC" and is_from_peer:
                    ua = build_u_frame(source, sess.mycall, [], "UA", pf=c["pf"], command_bit=False)
                    with ser_write_lock:
                        ser.write(kiss_build_frame(ua))
                    on_status(f"*** {sess.peer} closed the connection")
                    sess.state = "DISCONNECTED"

                elif c["type"] == "I" and sess.state == "CONNECTED" and is_from_peer:
                    if c["ns"] == sess.vr:
                        try:
                            # CP437: the classic encoding used by packet/BPQ nodes
                            # for menu box-drawing characters.
                            text = info.decode("cp437", errors="replace") if info else ""
                        except Exception:
                            text = info.hex()
                        text = text.replace("\r\n", "\n").replace("\r", "\n")
                        if text:
                            on_data(text)
                        sess.vr = (sess.vr + 1) % 8
                        rr = build_s_frame(source, sess.mycall, [], "RR", sess.vr, pf=0)
                        with ser_write_lock:
                            ser.write(kiss_build_frame(rr))
                    else:
                        on_status(f"*** Out-of-sequence I-frame from {source} "
                                  f"(expected N(S)={sess.vr}, got {c['ns']}), ignored")

                elif c["type"] == "S" and c["stype"] == "RR" and is_from_peer:
                    pass  # ack received, retransmission not handled in this simplified version


# ---------------------------------------------------------------------------
# Azioni di alto livello, riusabili da qualsiasi interfaccia
# ---------------------------------------------------------------------------
def do_connect(ser, ser_write_lock, sess: Ax25Session, mycall, target, digis,
                on_status, debug=False, attempts=3, timeout=5.0):
    """Performs the SABM/UA handshake towards 'target'. Returns True if connected."""
    with sess.lock:
        sess.peer = target
        sess.digis = digis
        sess.state = "CONNECTING"
        sess.event.clear()

    connected = False
    for attempt in range(attempts):
        sabm = build_u_frame(target, mycall, digis, "SABM", pf=1)
        kf = kiss_build_frame(sabm)
        with ser_write_lock:
            ser.write(kf)
        on_status(f"[TX] {mycall} > {target} (attempt {attempt + 1}/{attempts})"
                   + (f"\n[DEBUG TX] {kf.hex(' ')}" if debug else ""))
        if sess.event.wait(timeout=timeout):
            connected = (sess.state == "CONNECTED")
            break
    if not connected and sess.state != "CONNECTED":
        on_status(f"*** No response from {target}: connection failed")
        with sess.lock:
            sess.state = "DISCONNECTED"
    return connected


def do_disconnect(ser, ser_write_lock, sess: Ax25Session, mycall, on_status, timeout=5.0):
    with sess.lock:
        if sess.state != "CONNECTED":
            on_status("Not connected to any node")
            return
        target, digis = sess.peer, sess.digis
        sess.state = "DISCONNECTING"
        sess.event.clear()
    disc = build_u_frame(target, mycall, digis, "DISC", pf=1)
    with ser_write_lock:
        ser.write(kiss_build_frame(disc))
    on_status(f"[TX] {mycall} > {target} DISC")
    if not sess.event.wait(timeout=timeout):
        on_status("*** No response to DISC, closing locally anyway")
        with sess.lock:
            sess.state = "DISCONNECTED"


def do_send(ser, ser_write_lock, sess: Ax25Session, mycall, text, ui_dest, ui_digis, on_status):
    """Sends 'text': as an I-frame if connected, otherwise as a UI frame to ui_dest."""
    with sess.lock:
        connected = (sess.state == "CONNECTED")
        if connected:
            target, digis, ns, nr = sess.peer, sess.digis, sess.vs, sess.vr
            sess.vs = (sess.vs + 1) % 8

    if connected:
        payload = text.encode("utf-8") + b"\r"
        frame = build_i_frame(target, mycall, digis, ns, nr, payload)
        with ser_write_lock:
            ser.write(kiss_build_frame(frame))
        on_status(f"[TX I N(S)={ns}] {mycall} > {target}: {text}")
    else:
        frame = build_ui_frame(ui_dest, mycall, ui_digis, text)
        with ser_write_lock:
            ser.write(kiss_build_frame(frame))
        digi_str = f" via {','.join(ui_digis)}" if ui_digis else ""
        on_status(f"[TX UI] {mycall} > {ui_dest}{digi_str}: {text}")
