"""
===============================================================================
test_radio.py
===============================================================================

ESP-NOW communication test.

The PC controls the master ESP32 over USB serial.

TEST SEQUENCE
-------------
1. Send -1 five times to reset the slave.
2. Clear any telemetry collected during the reset phase.
3. Send numbered world packets at SEND_FREQUENCY_HZ.
4. Continue for exactly SEND_DURATION_S.
5. Stop transmitting.
6. Continue listening for TAIL_OBSERVATION_TIME_S.
7. Print automatic packet-loss statistics.


USER PARAMETERS
---------------
SEND_FREQUENCY_HZ
    Number of world packets transmitted per second.

SEND_DURATION_S
    Duration of the transmission phase.

TAIL_OBSERVATION_TIME_S
    Additional receive-only time after transmission stops.


PACKET COUNT
------------
The number of world packets transmitted is:

    round(SEND_FREQUENCY_HZ * SEND_DURATION_S)

Example:

    50 Hz * 1.0 s = 50 packets

Sequence numbers:

    0 ... 49


PC -> MASTER
------------
One signed integer terminated by newline:

    -1\n
    0\n
    1\n
    ...


MASTER -> PC
------------
Telemetry CSV:

    car_id,telemetry_seq,last_world_seq,world_packets_lost

Example:

    1,15,27,0


REQUIREMENT
-----------
    pip install pyserial

===============================================================================
"""

import serial
import threading
import time

# =============================================================================
# USER SETTINGS
# =============================================================================

PORT = "COM4"
BAUD = 115200

SEND_FREQUENCY_HZ = 100
SEND_DURATION_S = 600.0
TAIL_OBSERVATION_TIME_S = 0.1

RESET_COUNT = 5
RESET_INTERVAL_S = 0.1


# =============================================================================
# SHARED STATE
# =============================================================================

running = True
collect_telemetry = False

telemetry_records = []


# =============================================================================
# RECEIVE THREAD
# =============================================================================


def receive_loop(ser):
    global running
    global collect_telemetry

    while running:

        try:
            line = ser.readline()

        except serial.SerialException:
            break

        if not line:
            continue

        text = line.decode("utf-8", errors="replace").strip()

        if not text:
            continue

        # print(f"RX: {text}")

        # Do not include reset-phase telemetry in the measured test.
        if not collect_telemetry:
            continue

        parts = text.split(",")

        if len(parts) != 4:
            continue

        try:
            record = {
                "car_id": int(parts[0]),
                "telemetry_seq": int(parts[1]),
                "last_world_seq": int(parts[2]),
                "world_packets_lost": int(parts[3]),
            }

            telemetry_records.append(record)

        except ValueError:
            pass


# =============================================================================
# SEND ONE INTEGER
# =============================================================================


def send_value(ser, value):
    # print(f"TX: {value}")

    ser.write(f"{value}\n".encode("utf-8"))
    ser.flush()


# =============================================================================
# ANALYSIS
# =============================================================================


def analyze(packet_count):

    print()
    print("=" * 60)
    print("TEST RESULT")
    print("=" * 60)

    print(f"Configured frequency:       {SEND_FREQUENCY_HZ} Hz")
    print(f"Sending duration:           {SEND_DURATION_S:.3f} s")
    print(f"Tail observation:           {TAIL_OBSERVATION_TIME_S:.3f} s")
    print(
        f"Total observation:          "
        f"{SEND_DURATION_S + TAIL_OBSERVATION_TIME_S:.3f} s"
    )

    print()
    print(f"World packets sent:         {packet_count}")

    if packet_count > 0:
        print(f"Final sequence sent:        {packet_count - 1}")

    if not telemetry_records:
        print()
        print("No valid telemetry received.")
        return

    # -------------------------------------------------------------------------
    # Analyze each car independently
    # -------------------------------------------------------------------------

    car_ids = sorted(set(record["car_id"] for record in telemetry_records))

    for car_id in car_ids:

        records = [record for record in telemetry_records if record["car_id"] == car_id]

        print()
        print(f"Slave {car_id}")
        print("-" * 60)

        # ---------------------------------------------------------------------
        # World packet reception
        # ---------------------------------------------------------------------

        final_record = records[-1]

        last_world_seq = final_record["last_world_seq"]
        world_packets_lost = final_record["world_packets_lost"]

        if packet_count > 0:

            world_loss_percent = world_packets_lost / packet_count * 100.0

        else:
            world_loss_percent = 0.0

        print(f"Last world sequence:        {last_world_seq}")
        print(f"World packets lost:         {world_packets_lost}")
        print(f"World packet loss:          {world_loss_percent:.3f} %")

        # ---------------------------------------------------------------------
        # Telemetry reception
        # ---------------------------------------------------------------------

        telemetry_received = len(records)

        telemetry_first = records[0]["telemetry_seq"]
        telemetry_last = records[-1]["telemetry_seq"]

        telemetry_lost = 0

        previous_seq = telemetry_first

        for record in records[1:]:

            current_seq = record["telemetry_seq"]

            if current_seq > previous_seq + 1:
                telemetry_lost += current_seq - previous_seq - 1

            previous_seq = current_seq

        telemetry_expected = telemetry_received + telemetry_lost

        if telemetry_expected > 0:

            telemetry_loss_percent = telemetry_lost / telemetry_expected * 100.0

        else:
            telemetry_loss_percent = 0.0

        print()
        print(f"Telemetry received:         {telemetry_received}")
        print(f"Telemetry seq first:        {telemetry_first}")
        print(f"Telemetry seq last:         {telemetry_last}")
        print(f"Telemetry packets lost:     {telemetry_lost}")
        print(f"Telemetry packet loss:      {telemetry_loss_percent:.3f} %")


# =============================================================================
# MAIN
# =============================================================================


def main():

    global running
    global collect_telemetry
    global telemetry_records

    # -------------------------------------------------------------------------
    # Configure serial port while closed.
    #
    # RTS and DTR are explicitly kept inactive so that opening/closing the
    # serial connection does not intentionally reset the ESP32.
    # -------------------------------------------------------------------------

    ser = serial.Serial()

    ser.port = PORT
    ser.baudrate = BAUD
    ser.timeout = 0.05

    ser.rtscts = False
    ser.dsrdtr = False

    ser.rts = False
    ser.dtr = False

    ser.open()

    try:

        # Give the USB serial connection a short settling period.
        time.sleep(0.2)

        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # Start receive thread
        # ---------------------------------------------------------------------

        receiver = threading.Thread(target=receive_loop, args=(ser,), daemon=True)

        receiver.start()

        # ---------------------------------------------------------------------
        # RESET PHASE
        # ---------------------------------------------------------------------

        print()
        print("RESET")
        print("-" * 60)

        collect_telemetry = False

        for _ in range(RESET_COUNT):

            send_value(ser, -1)

            time.sleep(RESET_INTERVAL_S)

        # Remove reset-phase telemetry from our analysis.
        telemetry_records.clear()
        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # TEST CONFIGURATION
        # ---------------------------------------------------------------------

        packet_count = round(SEND_FREQUENCY_HZ * SEND_DURATION_S)

        period = 1.0 / SEND_FREQUENCY_HZ

        print()
        print("TEST")
        print("-" * 60)

        print(f"Sending {packet_count} packets " f"at {SEND_FREQUENCY_HZ} Hz")

        print()

        # ---------------------------------------------------------------------
        # START MEASURED TEST
        # ---------------------------------------------------------------------

        collect_telemetry = True

        test_start = time.perf_counter()

        # ---------------------------------------------------------------------
        # TRANSMISSION PHASE
        #
        # Packets are scheduled relative to test_start.
        #
        # This avoids accumulating timing error from repeated sleep(period).
        # ---------------------------------------------------------------------

        for sequence in range(packet_count):

            target_time = test_start + sequence * period

            while True:

                remaining = target_time - time.perf_counter()

                if remaining <= 0:
                    break

                time.sleep(remaining)

            send_value(ser, sequence)

        # ---------------------------------------------------------------------
        # OBSERVATION PHASE
        #
        # The test ends at:
        #
        # test_start
        # + SEND_DURATION_S
        # + TAIL_OBSERVATION_TIME_S
        #
        # Notice that the final packet occurs one period before the nominal
        # end of the transmission phase.
        # ---------------------------------------------------------------------

        test_end = test_start + SEND_DURATION_S + TAIL_OBSERVATION_TIME_S

        while True:

            remaining = test_end - time.perf_counter()

            if remaining <= 0:
                break

            time.sleep(remaining)

        collect_telemetry = False

        # ---------------------------------------------------------------------
        # ANALYZE
        # ---------------------------------------------------------------------

        analyze(packet_count)

    except KeyboardInterrupt:

        print()
        print("Test stopped.")

    finally:

        running = False

        ser.rts = False
        ser.dtr = False

        ser.close()


# =============================================================================

if __name__ == "__main__":
    main()
