"""
===============================================================================
test_radio.py
===============================================================================

ESP-NOW communication test.

The master ESP32 is a transparent radio bridge.

Python defines the application protocol.

WORLD MESSAGE
-------------

    W,<world_seq>

Examples:

    W,-1
    W,0
    W,1


TELEMETRY MESSAGE
-----------------

    T,
    car_id,
    telemetry_seq,
    last_world_seq,
    world_packets_lost,
    world_gap_events,
    max_world_gap

Example:

    T,1,502,10000,35,33,2


TEST
----

1. Send W,-1 five times.
2. Start measured test.
3. Send numbered world packets at SEND_FREQUENCY_HZ.
4. Continue for SEND_DURATION_S.
5. Stop transmitting.
6. Continue receiving for TAIL_OBSERVATION_TIME_S.
7. Analyze each slave independently.

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
SEND_DURATION_S = 30.0
TAIL_OBSERVATION_TIME_S = 0.1

RESET_COUNT = 5
RESET_INTERVAL_S = 0.1


# =============================================================================
# STATE
# =============================================================================

running = True
collect_telemetry = False

telemetry_records = []


# =============================================================================
# RECEIVE
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

        # Print everything received.
        # print(f"RX: {text}")

        if not collect_telemetry:
            continue

        parts = text.split(",")

        # Expected:
        #
        # T,
        # car_id,
        # telemetry_seq,
        # last_world_seq,
        # world_packets_lost,
        # world_gap_events,
        # max_world_gap

        if len(parts) != 7:
            continue

        if parts[0] != "T":
            continue

        try:

            record = {
                "car_id": int(parts[1]),
                "telemetry_seq": int(parts[2]),
                "last_world_seq": int(parts[3]),
                "world_packets_lost": int(parts[4]),
                "world_gap_events": int(parts[5]),
                "max_world_gap": int(parts[6]),
            }

            telemetry_records.append(record)

        except ValueError:
            pass


# =============================================================================
# SEND WORLD MESSAGE
# =============================================================================


def send_world(ser, world_seq):

    message = f"W,{world_seq}\n"

    # print(f"TX: W,{world_seq}")

    ser.write(message.encode("utf-8"))

    ser.flush()


# =============================================================================
# ANALYSIS
# =============================================================================


def analyze(packet_count):

    print()
    print("=" * 60)
    print("TEST RESULT")
    print("=" * 60)

    print(f"Configured frequency:       " f"{SEND_FREQUENCY_HZ} Hz")

    print(f"Sending duration:           " f"{SEND_DURATION_S:.3f} s")

    print(f"Tail observation:           " f"{TAIL_OBSERVATION_TIME_S:.3f} s")

    print(
        f"Total observation:          "
        f"{SEND_DURATION_S + TAIL_OBSERVATION_TIME_S:.3f} s"
    )

    print()

    print(f"World packets sent:         " f"{packet_count}")

    if packet_count > 0:

        print(f"Final sequence sent:        " f"{packet_count - 1}")

    if not telemetry_records:

        print()
        print("No valid telemetry received.")

        return

    # -------------------------------------------------------------------------
    # Analyze each slave
    # -------------------------------------------------------------------------

    car_ids = sorted(set(record["car_id"] for record in telemetry_records))

    for car_id in car_ids:

        records = [record for record in telemetry_records if record["car_id"] == car_id]

        final = records[-1]

        print()
        print(f"Slave {car_id}")
        print("-" * 60)

        # ---------------------------------------------------------------------
        # World packet statistics
        # ---------------------------------------------------------------------

        world_packets_lost = final["world_packets_lost"]

        if packet_count > 0:

            world_loss_percent = world_packets_lost / packet_count * 100.0

        else:

            world_loss_percent = 0.0

        print(f"Last world sequence:        " f"{final['last_world_seq']}")

        print(f"World packets lost:         " f"{world_packets_lost}")

        print(f"World packet loss:          " f"{world_loss_percent:.3f} %")

        print(f"Loss events:                " f"{final['world_gap_events']}")

        print(f"Maximum consecutive loss:   " f"{final['max_world_gap']}")

        if final["world_gap_events"] > 0:

            average_gap = world_packets_lost / final["world_gap_events"]

            print(f"Average loss/event:         " f"{average_gap:.3f}")

        # ---------------------------------------------------------------------
        # Telemetry statistics
        # ---------------------------------------------------------------------

        telemetry_received = len(records)

        telemetry_first = records[0]["telemetry_seq"]

        telemetry_last = records[-1]["telemetry_seq"]

        telemetry_lost = 0

        previous = telemetry_first

        for record in records[1:]:

            current = record["telemetry_seq"]

            if current > previous + 1:

                telemetry_lost += current - previous - 1

            previous = current

        telemetry_expected = telemetry_received + telemetry_lost

        if telemetry_expected > 0:

            telemetry_loss_percent = telemetry_lost / telemetry_expected * 100.0

        else:

            telemetry_loss_percent = 0.0

        print()

        print(f"Telemetry received:         " f"{telemetry_received}")

        print(f"Telemetry seq first:        " f"{telemetry_first}")

        print(f"Telemetry seq last:         " f"{telemetry_last}")

        print(f"Telemetry packets lost:     " f"{telemetry_lost}")

        print(f"Telemetry packet loss:      " f"{telemetry_loss_percent:.3f} %")


# =============================================================================
# MAIN
# =============================================================================


def main():

    global running
    global collect_telemetry
    global telemetry_records

    # -------------------------------------------------------------------------
    # Serial configuration
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

        time.sleep(0.2)

        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # Start receiver
        # ---------------------------------------------------------------------

        receiver = threading.Thread(target=receive_loop, args=(ser,), daemon=True)

        receiver.start()

        # ---------------------------------------------------------------------
        # Reset slaves
        # ---------------------------------------------------------------------

        print()
        print("RESET")
        print("-" * 60)

        collect_telemetry = False

        for _ in range(RESET_COUNT):

            send_world(ser, -1)

            time.sleep(RESET_INTERVAL_S)

        telemetry_records.clear()

        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # Test parameters
        # ---------------------------------------------------------------------

        packet_count = round(SEND_FREQUENCY_HZ * SEND_DURATION_S)

        period = 1.0 / SEND_FREQUENCY_HZ

        print()
        print("TEST")
        print("-" * 60)

        print(f"Sending {packet_count} packets " f"at {SEND_FREQUENCY_HZ} Hz")

        print()

        # ---------------------------------------------------------------------
        # Begin measured test
        # ---------------------------------------------------------------------

        collect_telemetry = True

        test_start = time.perf_counter()

        for sequence in range(packet_count):

            target_time = test_start + sequence * period

            while True:

                remaining = target_time - time.perf_counter()

                if remaining <= 0:
                    break

                time.sleep(remaining)

            send_world(ser, sequence)

        # ---------------------------------------------------------------------
        # Tail observation
        # ---------------------------------------------------------------------

        test_end = test_start + SEND_DURATION_S + TAIL_OBSERVATION_TIME_S

        while True:

            remaining = test_end - time.perf_counter()

            if remaining <= 0:
                break

            time.sleep(remaining)

        collect_telemetry = False

        # ---------------------------------------------------------------------
        # Results
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
