"""
===============================================================================
test_radio.py
===============================================================================

Minimal ESP-NOW radio test.

TEST
----
1. Open the master ESP32 serial port.
2. Keep RTS and DTR disabled so opening/closing the port does not reset the ESP32.
3. Send -1 five times.
4. Send world sequence 0 through 10.
5. Send everything at 10 Hz.
6. Print every transmitted value.
7. Print every telemetry CSV line received from the master.

PC -> MASTER
------------
One integer per line:

    -1
    0
    1
    2
    ...

Each line is terminated by:

    \\n

The master immediately broadcasts each received integer over ESP-NOW.


MASTER -> PC
------------
Telemetry arrives as raw CSV:

    car_id,telemetry_seq,last_world_seq,world_packets_lost

Example:

    1,27,8,0


EXPECTED TEST LENGTH
--------------------
16 transmissions total:

    5 reset packets
    +
    11 packets from 0 to 10

At 10 Hz, this takes approximately 1.6 seconds.


REQUIREMENT
-----------
Install pyserial:

    pip install pyserial

===============================================================================
"""

import serial
import threading
import time

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PORT = "COM4"  # Change to the COM port of the master ESP32
BAUD = 115200

SEND_INTERVAL = 0.01  # 10 Hz

RESET_COUNT = 5


# -----------------------------------------------------------------------------
# Shared state
# -----------------------------------------------------------------------------

running = True


# -----------------------------------------------------------------------------
# Receive telemetry continuously
# -----------------------------------------------------------------------------


def receive_loop(ser):
    global running

    while running:
        try:
            line = ser.readline()

            if not line:
                continue

            text = line.decode("utf-8", errors="replace").strip()

            if text:
                print(f"RX: {text}")

        except serial.SerialException:
            break


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    global running

    ser = serial.Serial(
        port=PORT, baudrate=BAUD, timeout=0.1, rtscts=False, dsrdtr=False
    )

    # Explicitly keep both control lines inactive.
    # This helps prevent the serial connection from resetting the ESP32.
    ser.rts = False
    ser.dtr = False

    try:
        # Allow the serial connection to settle.
        time.sleep(0.2)

        # Remove anything that may already be sitting in the receive buffer.
        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # Start receive thread
        # ---------------------------------------------------------------------

        receiver = threading.Thread(target=receive_loop, args=(ser,), daemon=True)

        receiver.start()

        # ---------------------------------------------------------------------
        # Build test sequence
        # ---------------------------------------------------------------------

        messages = [-1] * RESET_COUNT + list(range(11))

        # ---------------------------------------------------------------------
        # Transmit
        # ---------------------------------------------------------------------

        for value in messages:

            print(f"TX: {value}")

            ser.write(f"{value}\n".encode("utf-8"))
            ser.flush()

            time.sleep(SEND_INTERVAL)

        # Give the final telemetry packets time to arrive.
        time.sleep(0.3)

    except KeyboardInterrupt:
        print("\nTest stopped.")

    finally:
        running = False

        # Keep RTS/DTR low before closing.
        ser.rts = False
        ser.dtr = False

        ser.close()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
