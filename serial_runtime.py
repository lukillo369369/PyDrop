"""Bounded Arduino serial operations, also embedded in exported PyDrop scripts.

Simulation uses the same newline protocol as examples/arduino_sensor.
Importing this module never opens a device or imports the optional pyserial package.
The editor runs generated scripts in its existing background process; these functions
are deliberately synchronous and have bounded reads and writes.
"""

import atexit as _serial_atexit
import collections as _serial_collections
import importlib as _serial_importlib
import math as _serial_math
import re as _serial_re
import sys as _serial_sys
import time as _serial_time


SERIAL_KINDS = ("Serial Connect", "Serial Send", "Serial Read Line", "Parse Value", "Serial Disconnect")
_serial_MAX_LINE = 4096
_serial_MAX_RESPONSES = 128
_serial_STARTUP_SECONDS = 2.0
_serial_IO_SLICE = 0.05
_serial_io_errors = (OSError,)
_serial_session = {
    "connected": False, "transport": None, "mode": "Simulation", "port": "SIMULATED",
    "baud": 9600, "timeout": 1.0, "responses": _serial_collections.deque(),
    "buffer": bytearray(), "send_buffer": bytearray(), "discard": False, "led": False,
    "status": "Disconnected.",
}


class SerialError(RuntimeError):
    """An actionable connection, protocol, or configuration error."""


def _serial_status(status, connected, mode, port):
    """Status hook replaced by the compiler when live editor tracing is enabled."""
    print("[Serial " + mode + " / " + port + "] " + status, file=_serial_sys.stderr, flush=True)


def _serial_emit(status):
    _serial_session["status"] = status
    _serial_status(status, _serial_session["connected"], _serial_session["mode"], _serial_session["port"])
    return status


def _serial_error(message):
    _serial_emit(message)
    raise SerialError(message)


def _serial_timeout(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        _serial_error("Read timeout must be a finite number from 0 to 30 seconds.")
    if not _serial_math.isfinite(result) or not 0 <= result <= 30:
        _serial_error("Read timeout must be a finite number from 0 to 30 seconds.")
    return result


def _serial_library():
    global _serial_io_errors
    try:
        library = _serial_importlib.import_module("serial")
    except ImportError:
        _serial_error("Hardware mode requires pyserial. Install it with: pip install pyserial; or choose Simulation.")
    if not callable(getattr(library, "Serial", None)):
        _serial_error("The imported 'serial' package is not pyserial. Remove the 'serial' package or local serial.py, "
                      "then install pyserial with: pip install pyserial; or choose Simulation.")
    exception = getattr(library, "SerialException", OSError)
    if isinstance(exception, type) and issubclass(exception, BaseException):
        _serial_io_errors = (OSError, exception)
    return library


def _serial_ports():
    """Return detected device names, without requiring pyserial for simulation."""
    try:
        library = _serial_importlib.import_module("serial")
        if not callable(getattr(library, "Serial", None)):
            return []
        ports = _serial_importlib.import_module("serial.tools.list_ports")
        return sorted({str(item.device) for item in ports.comports() if getattr(item, "device", None)})
    except (ImportError, OSError, AttributeError):
        return []


def _serial_cleanup():
    """Close resources silently, preserving the last connection/error status."""
    transport = _serial_session["transport"]
    _serial_session.update(connected=False, transport=None, discard=False, led=False)
    _serial_session["responses"].clear()
    _serial_session["buffer"].clear()
    _serial_session["send_buffer"].clear()
    if transport is not None:
        try:
            transport.close()
        except Exception:
            # Closing an unplugged USB device may itself fail. State is already reset.
            pass


def _serial_transport_error(operation, error):
    port = _serial_session["port"]
    _serial_cleanup()
    message = ("Serial " + operation + " failed on " + port + ": " + str(error)
               + ". The device may be unplugged or busy. Check the cable and port, then run Serial Connect again.")
    _serial_emit(message)
    raise SerialError(message) from error


def _serial_require_connected(operation):
    if not _serial_session["connected"]:
        _serial_error(operation + " requires an explicit Serial Connect first.")
    transport = _serial_session["transport"]
    if _serial_session["mode"] == "Hardware":
        try:
            if transport is None or not transport.is_open:
                raise OSError("the serial port is closed")
        except _serial_io_errors as error:
            _serial_transport_error(operation, error)


def _serial_connect(port="SIMULATED", baud="9600", timeout="1", mode="Simulation"):
    """Explicitly connect one session. Hardware waits for common Arduino auto-reset."""
    timeout = _serial_timeout(timeout)
    try:
        if isinstance(baud, bool) or not _serial_re.fullmatch(r"[0-9]+", str(baud).strip()):
            raise ValueError()
        baud = int(str(baud).strip())
        if not 1 <= baud <= 4000000:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        _serial_error("Baud rate must be a whole number from 1 to 4000000, such as 9600 or 115200.")
    mode_text = str(mode).strip().lower()
    if mode_text not in ("simulation", "hardware"):
        _serial_error("Serial mode must be Simulation or Hardware.")
    mode = "Simulation" if mode_text == "simulation" else "Hardware"
    port = "SIMULATED" if mode == "Simulation" else str(port).strip()
    if not port or (mode == "Hardware" and port.upper() == "SIMULATED"):
        _serial_error("Choose a hardware port, such as COM3 or /dev/ttyACM0, before connecting.")
    if _serial_session["connected"]:
        _serial_error("Already connected. Run Serial Disconnect before opening a new connection.")
    _serial_cleanup()
    _serial_session.update(mode=mode, port=port, baud=baud, timeout=timeout)
    if mode == "Hardware":
        library = _serial_library()
        _serial_emit("Connecting to " + port + "; waiting for Arduino startup...")
        try:
            transport = library.Serial(port=port, baudrate=baud, timeout=_serial_IO_SLICE,
                                       write_timeout=min(1.0, max(0.1, timeout)))
            _serial_session["transport"] = transport
            deadline = _serial_time.monotonic() + _serial_STARTUP_SECONDS
            while _serial_time.monotonic() < deadline:
                if not transport.is_open:
                    raise OSError("the serial port closed during startup")
                _serial_time.sleep(min(_serial_IO_SLICE, max(0, deadline - _serial_time.monotonic())))
            # Arduino sketches may print startup messages after their auto-reset.
            # Drain these before sending the first request; this also detects removal.
            transport.reset_input_buffer()
        except _serial_io_errors + (ValueError,) as error:
            _serial_transport_error("connect", error)
        status = "Connected to " + port + " (" + str(baud) + " baud)."
    else:
        status = "Connected to simulated Arduino (" + str(baud) + " baud)."
    _serial_session["connected"] = True
    return {"connected": True, "status": _serial_emit(status)}


def _serial_newline(value):
    values = {"LF": "\n", "CRLF": "\r\n", "CR": "\r", "None": "", "NONE": "",
              "\\n": "\n", "\\r\\n": "\r\n", "\\r": "\r", "\n": "\n", "\r\n": "\r\n", "\r": "\r", "": ""}
    if value is None:
        return ""
    if not isinstance(value, str) or value not in values:
        _serial_error("Send newline must be LF, CRLF, CR, or None.")
    return values[value]


def _serial_simulate_send(data):
    parts = (bytes(_serial_session["send_buffer"]) + data).split(b"\n")
    if any(len(part) > _serial_MAX_LINE for part in parts):
        _serial_error("Simulation command exceeds 4096 bytes. Send a shorter newline-terminated command.")
    commands = [part.rstrip(b"\r").decode("utf-8").strip() for part in parts[:-1]]
    commands = [command for command in commands if command]
    if len(_serial_session["responses"]) + len(commands) > _serial_MAX_RESPONSES:
        _serial_error("Simulation receive buffer is full. Run Serial Read Line before sending more commands.")
    _serial_session["send_buffer"][:] = parts[-1]
    for command in commands:
        if command == "READ":
            response = "temperature=23.5,light=512"
        elif command in ("LED ON", "LED OFF"):
            _serial_session["led"] = command == "LED ON"
            response = "led=" + ("1" if _serial_session["led"] else "0")
        else:
            response = "error=unknown_command"
        _serial_session["responses"].append(response)


def _serial_send(command, newline="LF"):
    _serial_require_connected("Serial Send")
    ending = _serial_newline(newline)
    try:
        data = (str(command) + ending).encode("utf-8")
    except UnicodeError:
        _serial_error("Serial Send requires valid UTF-8 text.")
    if len(data) > _serial_MAX_LINE:
        _serial_error("Serial Send is limited to 4096 UTF-8 bytes. Send a shorter command.")
    if _serial_session["mode"] == "Simulation":
        _serial_simulate_send(data)
    else:
        try:
            count = _serial_session["transport"].write(data)
            if count != len(data):
                raise OSError("write timed out after " + str(count) + " of " + str(len(data)) + " bytes")
        except _serial_io_errors as error:
            _serial_transport_error("send", error)
    status = _serial_emit("Sent " + str(len(data)) + " UTF-8 bytes.")
    return {"sent": True, "status": status}


def _serial_line_result(line):
    return {"line": line, "received": True, "status": _serial_emit("Received a serial line.")}


def _serial_read_line(timeout=None):
    _serial_require_connected("Serial Read Line")
    timeout = _serial_session["timeout"] if timeout is None or timeout == "" else _serial_timeout(timeout)
    deadline = _serial_time.monotonic() + timeout
    if _serial_session["mode"] == "Simulation":
        while not _serial_session["responses"] and _serial_time.monotonic() < deadline:
            _serial_time.sleep(min(_serial_IO_SLICE, max(0, deadline - _serial_time.monotonic())))
        if _serial_session["responses"]:
            return _serial_line_result(_serial_session["responses"].popleft())
    else:
        transport = _serial_session["transport"]
        bytes_read = 0
        while True:
            remaining = max(0.0, deadline - _serial_time.monotonic())
            try:
                transport.timeout = min(_serial_IO_SLICE, remaining)
                piece = transport.read(1)
            except _serial_io_errors as error:
                _serial_transport_error("read", error)
            if piece:
                bytes_read += len(piece)
                if piece == b"\n":
                    if _serial_session["discard"]:
                        _serial_session["discard"] = False
                    else:
                        raw = bytes(_serial_session["buffer"]).removesuffix(b"\r")
                        _serial_session["buffer"].clear()
                        try:
                            return _serial_line_result(raw.decode("utf-8"))
                        except UnicodeDecodeError:
                            _serial_error("Received invalid UTF-8. Check the baud rate and Arduino text protocol, then read again.")
                elif not _serial_session["discard"]:
                    _serial_session["buffer"].extend(piece)
                    if len(_serial_session["buffer"]) > _serial_MAX_LINE:
                        _serial_session["buffer"].clear()
                        _serial_session["discard"] = True
                        _serial_error("Serial line exceeds 4096 bytes. Check the baud rate and send newline-terminated lines.")
            if timeout == 0:
                # A nonblocking poll must finish even if a broken device streams
                # endless bytes while we discard an overlong, unterminated line.
                if not piece or bytes_read >= _serial_MAX_LINE + 1:
                    break
            elif _serial_time.monotonic() >= deadline:
                break
    status = _serial_emit("Read timed out after " + format(timeout, "g") + " s; no complete line received.")
    return {"line": "", "received": False, "status": status}


def _serial_parse_value(line, key="", separator=","):
    """Parse one finite decimal value, without evaluating received text as code."""
    line, key, separator = str(line).strip(), str(key).strip(), str(separator)
    if not separator or "=" in separator or "\n" in separator or "\r" in separator:
        _serial_error("Parse Value separator must be nonempty and cannot contain '=' or a line break.")
    if len(line.encode("utf-8", errors="replace")) > _serial_MAX_LINE:
        _serial_error("Parse Value input exceeds 4096 UTF-8 bytes.")
    value = line
    if key:
        if "=" in key or separator in key or "\n" in key or "\r" in key:
            _serial_error("Parse Value key cannot contain '=' or the selected separator.")
        fields = {}
        for field in line.split(separator):
            if field.count("=") != 1:
                _serial_error("Parse Value expected key=value fields separated by " + repr(separator) + ".")
            name, text = (part.strip() for part in field.split("=", 1))
            if not name or not text or name in fields:
                _serial_error("Parse Value found an empty or duplicate field.")
            fields[name] = text
        if key not in fields:
            _serial_error("Parse Value could not find key " + repr(key) + " in the received line.")
        value = fields[key]
    if not _serial_re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        _serial_error("Parse Value expected a finite number" + (" for " + repr(key) if key else "") + ".")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        _serial_error("Parse Value expected a finite number.")
    if not _serial_math.isfinite(result):
        _serial_error("Parse Value expected a finite number.")
    return result


def _serial_disconnect():
    _serial_cleanup()
    return {"connected": False, "status": _serial_emit("Disconnected.")}


_serial_atexit.register(_serial_cleanup)
