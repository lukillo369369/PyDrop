"""Serial behavior without requiring a USB device or the optional pyserial package."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serial_runtime as runtime


class FakeSerialException(Exception):
    pass


class FakeTransport:
    def __init__(self, **settings):
        self.settings = settings
        self.timeout = settings["timeout"]
        self.is_open = True
        self.input = bytearray()
        self.writes = []
        self.read_error = None
        self.write_error = None
        self.close_error = None
        self.write_count = None
        self.reset_count = 0
        self.read_timeouts = []

    def reset_input_buffer(self):
        self.reset_count += 1
        self.input.clear()

    def read(self, size):
        self.read_timeouts.append(self.timeout)
        if self.read_error:
            raise self.read_error
        result = bytes(self.input[:size])
        del self.input[:size]
        return result

    def write(self, data):
        if self.write_error:
            raise self.write_error
        self.writes.append(data)
        return len(data) if self.write_count is None else self.write_count

    def close(self):
        self.is_open = False
        if self.close_error:
            raise self.close_error


class SerialRuntimeTests(unittest.TestCase):
    def setUp(self):
        runtime._serial_cleanup()
        self.events = []
        self.status_patch = patch.object(runtime, "_serial_status", side_effect=lambda *args: self.events.append(args))
        self.status_patch.start()
        self.addCleanup(self.status_patch.stop)
        self.addCleanup(runtime._serial_cleanup)

    def connect_hardware(self, timeout="1"):
        self.transport = None

        def factory(**settings):
            self.transport = FakeTransport(**settings)
            return self.transport

        library = types.SimpleNamespace(Serial=factory, SerialException=FakeSerialException)
        with patch.object(runtime._serial_importlib, "import_module", return_value=library), \
                patch.object(runtime, "_serial_STARTUP_SECONDS", 0):
            result = runtime._serial_connect("COM7", "115200", timeout, "Hardware")
        self.assertTrue(result["connected"])
        return self.transport

    def test_send_and_read_require_explicit_connection(self):
        with patch.object(runtime._serial_importlib, "import_module") as loader:
            for operation in (lambda: runtime._serial_send("READ"), lambda: runtime._serial_read_line(0)):
                with self.assertRaisesRegex(runtime.SerialError, "explicit Serial Connect"):
                    operation()
            loader.assert_not_called()

    def test_simulation_dashboard_without_pyserial(self):
        with patch.object(runtime._serial_importlib, "import_module", side_effect=ImportError("no pyserial")):
            result = runtime._serial_connect()
            self.assertTrue(result["connected"])
            for unused in range(3):
                self.assertTrue(runtime._serial_send("READ", "LF")["sent"])
                result = runtime._serial_read_line(0)
                self.assertTrue(result["received"])
                self.assertEqual(result["line"], "temperature=23.5,light=512")
                self.assertEqual(runtime._serial_parse_value(result["line"], "temperature"), 23.5)
                self.assertEqual(runtime._serial_parse_value(result["line"], "light"), 512.0)
        self.assertTrue(all(event[2] == "Simulation" and event[3] == "SIMULATED" for event in self.events))

    def test_simulated_led_and_unknown_commands_match_protocol(self):
        runtime._serial_connect()
        for command, expected in (("LED ON", "led=1"), ("LED OFF", "led=0"), ("BOGUS", "error=unknown_command")):
            runtime._serial_send(command, "CRLF")
            self.assertEqual(runtime._serial_read_line(0)["line"], expected)

    def test_simulation_waits_for_newline(self):
        runtime._serial_connect()
        runtime._serial_send("READ", "None")
        self.assertFalse(runtime._serial_read_line(0)["received"])
        runtime._serial_send("", "LF")
        self.assertEqual(runtime._serial_read_line(0)["line"], "temperature=23.5,light=512")

    def test_timeout_keeps_session_connected_and_returns_no_value(self):
        runtime._serial_connect(timeout="0.001")
        result = runtime._serial_read_line()
        self.assertEqual(result["line"], "")
        self.assertFalse(result["received"])
        self.assertIn("timed out", result["status"])
        self.assertTrue(self.events[-1][1])
        runtime._serial_send("READ")
        self.assertTrue(runtime._serial_read_line(0)["received"])

    def test_disconnect_is_idempotent_and_reconnect_is_explicit(self):
        runtime._serial_connect()
        runtime._serial_send("READ")
        self.assertFalse(runtime._serial_disconnect()["connected"])
        self.assertFalse(runtime._serial_disconnect()["connected"])
        with self.assertRaisesRegex(runtime.SerialError, "explicit Serial Connect"):
            runtime._serial_send("READ")
        runtime._serial_connect()
        self.assertFalse(runtime._serial_read_line(0)["received"])

    def test_connect_does_not_silently_replace_connection(self):
        runtime._serial_connect()
        with self.assertRaisesRegex(runtime.SerialError, "Serial Disconnect"):
            runtime._serial_connect()
        self.assertTrue(runtime._serial_session["connected"])

    def test_hardware_settings_and_startup_status(self):
        transport = self.connect_hardware(timeout="30")
        self.assertEqual(transport.settings["port"], "COM7")
        self.assertEqual(transport.settings["baudrate"], 115200)
        self.assertLessEqual(transport.settings["timeout"], 0.05)
        self.assertLessEqual(transport.settings["write_timeout"], 1)
        self.assertGreater(transport.settings["write_timeout"], 0)
        self.assertEqual(transport.reset_count, 1)
        self.assertIn("startup", self.events[0][0])
        self.assertFalse(self.events[0][1])
        self.assertTrue(self.events[-1][1])

    def test_hardware_send_is_utf8_and_newline_terminated(self):
        transport = self.connect_hardware()
        runtime._serial_send("LED ON", "CRLF")
        runtime._serial_send("µ", "LF")
        self.assertEqual(transport.writes, [b"LED ON\r\n", "µ\n".encode("utf-8")])

    def test_hardware_receives_complete_crlf_and_retains_next_line(self):
        transport = self.connect_hardware()
        transport.input.extend(b"temperature=23.5\r\nlight=512\n")
        self.assertEqual(runtime._serial_read_line(0)["line"], "temperature=23.5")
        self.assertEqual(runtime._serial_read_line(0)["line"], "light=512")
        self.assertFalse(runtime._serial_read_line(0)["received"])

    def test_hardware_timeout_never_reports_partial_success(self):
        transport = self.connect_hardware()
        transport.input.extend(b"temperature=23.")
        result = runtime._serial_read_line(0)
        self.assertFalse(result["received"])
        self.assertEqual(result["line"], "")
        transport.input.extend(b"5\n")
        self.assertEqual(runtime._serial_read_line(0)["line"], "temperature=23.5")
        self.assertLessEqual(max(transport.read_timeouts), 0.05)

    def test_hardware_read_timeout_is_sliced_and_bounded(self):
        transport = self.connect_hardware()
        result = runtime._serial_read_line(0.001)
        self.assertFalse(result["received"])
        self.assertTrue(transport.read_timeouts)
        self.assertLessEqual(max(transport.read_timeouts), 0.001)

    def test_nonblocking_discard_cannot_spin_on_an_endless_device(self):
        transport = self.connect_hardware()
        runtime._serial_session["discard"] = True
        with patch.object(transport, "read", return_value=b"x") as reader:
            result = runtime._serial_read_line(0)
        self.assertFalse(result["received"])
        self.assertEqual(reader.call_count, 4097)
        self.assertEqual(len(runtime._serial_session["buffer"]), 0)

    def test_unplug_read_cleans_up_and_requires_reconnect(self):
        transport = self.connect_hardware()
        transport.read_error = FakeSerialException("USB removed")
        transport.close_error = OSError("gone")
        with self.assertRaisesRegex(runtime.SerialError, "unplugged.*Serial Connect"):
            runtime._serial_read_line(0)
        self.assertFalse(transport.is_open)
        self.assertFalse(runtime._serial_session["connected"])
        status = runtime._serial_session["status"]
        runtime._serial_cleanup()
        self.assertEqual(runtime._serial_session["status"], status)
        self.assertFalse(self.events[-1][1])
        with self.assertRaisesRegex(runtime.SerialError, "explicit Serial Connect"):
            runtime._serial_send("READ")
        runtime._serial_connect()
        runtime._serial_send("READ")
        self.assertTrue(runtime._serial_read_line(0)["received"])

    def test_unplug_send_and_closed_port_clean_up(self):
        for error in (OSError("device removed"), FakeSerialException("device removed")):
            with self.subTest(error=error):
                runtime._serial_disconnect()
                transport = self.connect_hardware()
                transport.write_error = error
                with self.assertRaisesRegex(runtime.SerialError, "Serial send failed"):
                    runtime._serial_send("READ")
                self.assertFalse(transport.is_open)
        transport = self.connect_hardware()
        transport.is_open = False
        with self.assertRaisesRegex(runtime.SerialError, "port is closed"):
            runtime._serial_send("READ")

    def test_partial_write_is_failure_and_closes_session(self):
        transport = self.connect_hardware()
        transport.write_count = 2
        with self.assertRaisesRegex(runtime.SerialError, "2 of 5"):
            runtime._serial_send("READ")
        self.assertFalse(transport.is_open)

    def test_hardware_connect_error_is_actionable(self):
        library = types.SimpleNamespace(Serial=lambda **settings: (_ for _ in ()).throw(OSError("port busy")))
        with patch.object(runtime._serial_importlib, "import_module", return_value=library):
            with self.assertRaisesRegex(runtime.SerialError, "port busy.*Check the cable and port"):
                runtime._serial_connect("COM7", mode="Hardware")
        self.assertFalse(runtime._serial_session["connected"])

    def test_missing_and_wrong_serial_packages_explain_fix(self):
        with patch.object(runtime._serial_importlib, "import_module", side_effect=ImportError()):
            with self.assertRaisesRegex(runtime.SerialError, "pip install pyserial"):
                runtime._serial_connect("COM7", mode="Hardware")
            self.assertEqual(runtime._serial_ports(), [])
        with patch.object(runtime._serial_importlib, "import_module", return_value=types.SimpleNamespace()):
            with self.assertRaisesRegex(runtime.SerialError, "not pyserial"):
                runtime._serial_connect("COM7", mode="Hardware")
            self.assertEqual(runtime._serial_ports(), [])

    def test_port_detection_lists_device_names(self):
        library = types.SimpleNamespace(Serial=FakeTransport)
        ports = types.SimpleNamespace(comports=lambda: [types.SimpleNamespace(device="COM7"),
                                                       types.SimpleNamespace(device="COM3"),
                                                       types.SimpleNamespace(device="COM7")])
        with patch.object(runtime._serial_importlib, "import_module", side_effect=[library, ports]):
            self.assertEqual(runtime._serial_ports(), ["COM3", "COM7"])

    def test_oversize_and_invalid_utf8_lines_do_not_become_values(self):
        transport = self.connect_hardware()
        transport.input.extend(b"x" * 4097 + b"tail\n24.5\n")
        with self.assertRaisesRegex(runtime.SerialError, "exceeds 4096"):
            runtime._serial_read_line(0)
        self.assertLessEqual(len(runtime._serial_session["buffer"]), 4096)
        self.assertEqual(runtime._serial_read_line(0)["line"], "24.5")
        transport.input.extend(b"\xff\n25.5\n")
        with self.assertRaisesRegex(runtime.SerialError, "invalid UTF-8"):
            runtime._serial_read_line(0)
        self.assertEqual(runtime._serial_read_line(0)["line"], "25.5")

    def test_simulation_receive_queue_is_bounded(self):
        runtime._serial_connect()
        for unused in range(128):
            runtime._serial_send("READ")
        with self.assertRaisesRegex(runtime.SerialError, "receive buffer is full"):
            runtime._serial_send("READ")
        self.assertEqual(len(runtime._serial_session["responses"]), 128)
        self.assertTrue(runtime._serial_read_line(0)["received"])
        self.assertTrue(runtime._serial_send("READ")["sent"])

    def test_invalid_settings_and_send_bounds(self):
        for value in (-1, 31, "nan", "inf", "no", True):
            with self.subTest(timeout=value), self.assertRaisesRegex(runtime.SerialError, "0 to 30"):
                runtime._serial_connect(timeout=value)
        for value in (0, -9600, "9600.0", "nan", 4000001, True):
            with self.subTest(baud=value), self.assertRaisesRegex(runtime.SerialError, "Baud rate"):
                runtime._serial_connect(baud=value)
        with self.assertRaisesRegex(runtime.SerialError, "hardware port"):
            runtime._serial_connect(mode="Hardware")
        with self.assertRaisesRegex(runtime.SerialError, "mode"):
            runtime._serial_connect(mode="Other")
        runtime._serial_connect()
        with self.assertRaisesRegex(runtime.SerialError, "4096"):
            runtime._serial_send("x" * 4096)
        with self.assertRaisesRegex(runtime.SerialError, "newline"):
            runtime._serial_send("READ", "invalid")

    def test_parse_bare_values_and_custom_separator(self):
        self.assertEqual(runtime._serial_parse_value(" -1.25e2 "), -125.0)
        self.assertEqual(runtime._serial_parse_value("temperature = 23.5 | light = 512", "light", "|"), 512.0)

    def test_parse_rejects_malformed_nonfinite_or_missing_values(self):
        cases = [("", "", ","), ("nan", "", ","), ("inf", "", ","), ("1e999", "", ","),
                 ("1_000", "", ","), ("23.5 C", "", ","), ("temperature=23.5", "", ","),
                 ("temperature=23.5", "light", ","), ("temperature=23.5,temperature=24", "temperature", ","),
                 ("temperature=23.5,light", "temperature", ","), ("temperature=", "temperature", ","),
                 ("temperature=NaN", "temperature", ","), ("x=1", "x", ""), ("x=1", "x", "=")]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(runtime.SerialError):
                runtime._serial_parse_value(*args)


if __name__ == "__main__":
    unittest.main()
