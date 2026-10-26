# Arduino serial nodes

Open **Examples → Arduino | Simulated sensor dashboard**, then run it. The graph
connects to the simulated Arduino, sends `READ` with an LF newline, reads one
response, displays temperature and light in the Output panel, and disconnects.
Simulation requires no hardware or additional packages. Its repeatable response is:

```text
temperature=23.5,light=512
```

The dashboard prints `Temperature (C): 23.5` and `Light (ADC): 512.0`, along with
connection status. Run it again to request another sample. The editable graph is
[`examples/sensor_dashboard.nodeforge.json`](examples/sensor_dashboard.nodeforge.json).

## Nodes and execution order

| Node | Properties and behavior |
| --- | --- |
| Serial Connect | Choose Simulation or Hardware, a serial port, baud rate, and read timeout in seconds. Simulation uses `SIMULATED`; the example uses 9600 baud and a 1 second timeout. Outputs `connected` and readable `status`. |
| Serial Send | Send a command, with LF, CRLF, or no newline. The Arduino sketch needs a completed line. Outputs `sent` and `status`. |
| Serial Read Line | Wait up to the selected timeout for a complete line. Outputs `line`, `received`, and `status`. |
| Parse Value | Extract a numeric field from a line such as `temperature=23.5,light=512`. Set `key` to `temperature` or `light` and `separator` to `,`. Outputs a number. |
| Serial Disconnect | Close the active connection and report `connected` and `status`. |

Wire the white execution path through **Connect → Send → Read Line → output →
Disconnect**. Connect must execute successfully before Send; a data wire alone
does not establish a connection. Send never opens a port automatically. Parse
Value uses the Read Line `line` output after the read has executed.

A timeout produces `received=False`. The dashboard branches on that output,
reports the read status, and disconnects without parsing an empty line. Use the
same pattern when building your own graph. Missing fields and nonnumeric values
produce a readable parse error.

## Use a physical Arduino

1. Install the optional hardware dependency into the Python environment used to
   run PyDrop: `python -m pip install pyserial`. Simulation works without it.
2. Open [`examples/arduino_sensor/arduino_sensor.ino`](examples/arduino_sensor/arduino_sensor.ino)
   in the Arduino IDE, select your board and port, and upload it. The example
   targets an Uno with a 10-bit ADC. Close the IDE Serial Monitor before PyDrop
   opens the port.
3. Select Serial Connect in the dashboard. Change Mode to **Hardware**, select
   your board's port (for example, `COM3` on Windows), and leave baud at **9600**
   to match `Serial.begin(9600)` in the sketch. Refresh the port list after
   plugging in a board. A port can also be entered manually.
4. Keep a finite read timeout, such as **1 second**. Run the graph. Boards that
   reset when their serial port opens get a short startup wait before commands
   are sent.

The sketch reads A0 as raw light/input ADC counts. A1 maps 0–1023 ADC counts to a
demonstration temperature of 0–50 °C. For a controlled demo, connect potentiometer
wipers to A0 and A1, with their outer legs connected to the board's permitted
analog reference supply and GND. Replace the temperature conversion and light
wiring for your actual sensors. Unconnected analog pins float, so their readings
vary. Hardware readings depend on the circuit; the simulator always returns the
fixed sample above.

The sketch accepts `READ`, `LED ON`, and `LED OFF`, one command per line. LED
commands return `led=1` or `led=0`; other commands return
`error=unknown_command`. It ignores CR, bounds its command buffer, and processes
incoming bytes without blocking on a missing newline. Use another Send/Read pair
to consume each response when extending the example.

## Timeouts and lost connections

Serial work runs outside the UI thread, so the canvas remains usable while a
connection or read is pending. Read timeouts and connection status are visible
in node outputs. If a cable is unplugged or a port becomes unavailable, the run
reports the device error and releases the connection; PyDrop stays open. Plug
the board back in, refresh/select its port, and execute Connect again before
sending another command. Stop ends the running program and releases its serial
port. Exported Python also needs `pyserial` only when Hardware mode is used.

The simulator and exported dashboard can be checked without a board. The Arduino
sketch still needs to be uploaded and verified with your own board and circuit.
