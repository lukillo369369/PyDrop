/*
 * PyDrop sensor dashboard companion sketch (Arduino Uno / 10-bit ADC).
 * Baud: 9600. Send a command followed by LF (CRLF also works).
 * READ -> temperature=23.5,light=512   (example values)
 * LED ON / LED OFF -> led=1 / led=0
 *
 * A0: light sensor or potentiometer analog output, 0..1023 ADC counts.
 * A1: demonstration temperature input, mapped from 0..1023 to 0..50 C.
 * Replace the A1 conversion for a calibrated temperature sensor.
 * The command reader uses a fixed buffer and never waits for a full line.
 */
#include <string.h>

const unsigned long SERIAL_BAUD = 9600;
const size_t COMMAND_CAPACITY = 40;
char command[COMMAND_CAPACITY];
size_t commandLength = 0;
bool commandTooLong = false;

void handleCommand(const char *text) {
  if (strcmp(text, "READ") == 0) {
    const float temperatureC = analogRead(A1) * (50.0f / 1023.0f);
    const int light = analogRead(A0);
    Serial.print("temperature=");
    Serial.print(temperatureC, 1);
    Serial.print(",light=");
    Serial.println(light);
  } else if (strcmp(text, "LED ON") == 0) {
    digitalWrite(LED_BUILTIN, HIGH);
    Serial.println("led=1");
  } else if (strcmp(text, "LED OFF") == 0) {
    digitalWrite(LED_BUILTIN, LOW);
    Serial.println("led=0");
  } else {
    Serial.println("error=unknown_command");
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(SERIAL_BAUD);
}

void loop() {
  while (Serial.available() > 0) {
    const char next = static_cast<char>(Serial.read());
    if (next == '\r') {
      continue;
    }
    if (next == '\n') {
      if (commandTooLong) {
        Serial.println("error=command_too_long");
      } else if (commandLength > 0) {
        command[commandLength] = '\0';
        handleCommand(command);
      }
      commandLength = 0;
      commandTooLong = false;
    } else if (!commandTooLong) {
      if (commandLength < COMMAND_CAPACITY - 1) {
        command[commandLength++] = next;
      } else {
        commandTooLong = true;
      }
    }
  }
}
