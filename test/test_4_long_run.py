"""
===============================================================================
test_radio.py
===============================================================================

Long-duration ESP-NOW communication test.

ARCHITECTURE
------------

TX THREAD
    Sends W,<sequence> at the requested frequency.
    Does nothing else.

RX THREAD
    Continuously receives and parses telemetry.

LOG THREAD
    Every LOG_INTERVAL_S:
        - snapshots current statistics
        - writes CSV
        - flushes file
        - prints heartbeat

This separation prevents logging / console / file I/O from disturbing the
world-packet transmission schedule.


WORLD MESSAGE
-------------

    W,<world_seq>


TELEMETRY MESSAGE
-----------------

    T,
    car_id,
    telemetry_seq,
    last_world_seq,
    world_packets_lost,
    world_gap_events,
    max_world_gap


ADDITIONAL TX DIAGNOSTICS
-------------------------

The TX thread measures its own scheduling lateness.

Example:

    max_tx_lateness_ms = 3.2

means the worst packet was transmitted approximately 3.2 ms later than its
ideal scheduled time.

If a large world-packet gap coincides with a large TX lateness, the interruption
likely originated on the PC side rather than the ESP-NOW radio path.

===============================================================================
"""

import csv
import serial
import threading
import time
from datetime import datetime

# =============================================================================
# USER SETTINGS
# =============================================================================

PORT = "COM4"
BAUD = 115200

SEND_FREQUENCY_HZ = 100

# 8 hours
SEND_DURATION_S = 8 * 60 * 60

TAIL_OBSERVATION_TIME_S = 1.0

LOG_INTERVAL_S = 60.0

RESET_COUNT = 5
RESET_INTERVAL_S = 0.1


# =============================================================================
# GLOBAL STATE
# =============================================================================

running = True
collect_telemetry = False

stats_lock = threading.Lock()
tx_lock = threading.Lock()

stats = {}

tx_packets_sent = 0
tx_max_lateness_ms = 0.0

test_start = None


# =============================================================================
# SEND WORLD MESSAGE
# =============================================================================


def send_world(ser, world_seq):
    """
    Send one newline-terminated world message.

    No ser.flush() is used here.

    pyserial writes into the OS serial buffer. Blocking until that buffer has
    physically drained after every 10 ms packet is unnecessary and may introduce
    timing jitter.
    """

    ser.write(f"W,{world_seq}\n".encode("utf-8"))


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
            running = False
            break

        if not line:
            continue

        if not collect_telemetry:
            continue

        text = line.decode("utf-8", errors="replace").strip()

        if not text:
            continue

        parts = text.split(",")

        if len(parts) != 7:
            continue

        if parts[0] != "T":
            continue

        try:

            car_id = int(parts[1])
            telemetry_seq = int(parts[2])
            last_world_seq = int(parts[3])

            world_packets_lost = int(parts[4])
            world_gap_events = int(parts[5])
            max_world_gap = int(parts[6])

        except ValueError:
            continue

        with stats_lock:

            # -----------------------------------------------------------------
            # First telemetry packet from this car
            # -----------------------------------------------------------------

            if car_id not in stats:

                stats[car_id] = {
                    "last_world_seq": last_world_seq,
                    "world_packets_lost": world_packets_lost,
                    "world_gap_events": world_gap_events,
                    "max_world_gap": max_world_gap,
                    "telemetry_received": 1,
                    "telemetry_lost": 0,
                    "telemetry_first_seq": telemetry_seq,
                    "telemetry_last_seq": telemetry_seq,
                }

                continue

            car = stats[car_id]

            # -----------------------------------------------------------------
            # Detect missing telemetry packets
            # -----------------------------------------------------------------

            previous_seq = car["telemetry_last_seq"]

            if telemetry_seq > previous_seq + 1:

                car["telemetry_lost"] += telemetry_seq - previous_seq - 1

            car["telemetry_received"] += 1
            car["telemetry_last_seq"] = telemetry_seq

            car["last_world_seq"] = last_world_seq

            car["world_packets_lost"] = world_packets_lost

            car["world_gap_events"] = world_gap_events

            car["max_world_gap"] = max_world_gap


# =============================================================================
# TRANSMIT THREAD
# =============================================================================


def transmit_loop(ser, packet_count):

    global running
    global tx_packets_sent
    global tx_max_lateness_ms

    period = 1.0 / SEND_FREQUENCY_HZ

    for sequence in range(packet_count):

        if not running:
            break

        target_time = test_start + sequence * period

        # ---------------------------------------------------------------------
        # Wait until scheduled transmission time.
        # ---------------------------------------------------------------------

        while running:

            now = time.perf_counter()

            remaining = target_time - now

            if remaining <= 0:
                break

            # Sleep most of the remaining interval.
            #
            # Keeping the maximum sleep short allows reasonable timing
            # responsiveness on Windows.

            if remaining > 0.002:
                time.sleep(remaining - 0.001)

            else:
                time.sleep(0)

        if not running:
            break

        actual_time = time.perf_counter()

        lateness_ms = max(0.0, (actual_time - target_time) * 1000.0)

        with tx_lock:

            if lateness_ms > tx_max_lateness_ms:
                tx_max_lateness_ms = lateness_ms

        # ---------------------------------------------------------------------
        # Send packet
        # ---------------------------------------------------------------------

        send_world(ser, sequence)

        with tx_lock:
            tx_packets_sent += 1


# =============================================================================
# COPY CURRENT STATISTICS
# =============================================================================


def get_snapshot():

    with stats_lock:

        return {car_id: car.copy() for car_id, car in stats.items()}


# =============================================================================
# LOG THREAD
# =============================================================================


def log_loop(writer, log_file):

    global running

    next_log_time = test_start + LOG_INTERVAL_S

    while running:

        now = time.perf_counter()

        if now < next_log_time:

            time.sleep(min(next_log_time - now, 0.5))

            continue

        write_log_snapshot(writer, log_file)

        next_log_time += LOG_INTERVAL_S


# =============================================================================
# WRITE ONE LOG SNAPSHOT
# =============================================================================


def write_log_snapshot(writer, log_file):

    elapsed = time.perf_counter() - test_start

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    snapshot = get_snapshot()

    with tx_lock:

        packets_sent = tx_packets_sent
        max_lateness = tx_max_lateness_ms

    # -------------------------------------------------------------------------
    # CSV
    # -------------------------------------------------------------------------

    for car_id in sorted(snapshot):

        car = snapshot[car_id]

        writer.writerow(
            [
                timestamp,
                f"{elapsed:.1f}",
                car_id,
                packets_sent,
                car["last_world_seq"],
                car["world_packets_lost"],
                car["world_gap_events"],
                car["max_world_gap"],
                car["telemetry_received"],
                car["telemetry_lost"],
                car["telemetry_last_seq"],
                f"{max_lateness:.3f}",
            ]
        )

    # Push Python buffers to the operating system.
    log_file.flush()

    # -------------------------------------------------------------------------
    # Console heartbeat
    # -------------------------------------------------------------------------

    print()
    print(f"[{timestamp}] " f"{elapsed / 3600:.2f} h elapsed")

    for car_id in sorted(snapshot):

        car = snapshot[car_id]

        if packets_sent > 0:

            world_loss_percent = car["world_packets_lost"] / packets_sent * 100.0

        else:

            world_loss_percent = 0.0

        telemetry_total = car["telemetry_received"] + car["telemetry_lost"]

        if telemetry_total > 0:

            telemetry_loss_percent = car["telemetry_lost"] / telemetry_total * 100.0

        else:

            telemetry_loss_percent = 0.0

        print(
            f"  Slave {car_id}: "
            f"world loss {world_loss_percent:.3f}% | "
            f"max gap {car['max_world_gap']} | "
            f"telemetry loss {telemetry_loss_percent:.3f}%"
        )

    print(
        f"  TX packets: {packets_sent:,} | " f"max TX lateness: {max_lateness:.3f} ms"
    )


# =============================================================================
# FINAL SUMMARY
# =============================================================================


def print_final_summary(configured_packet_count):

    elapsed = time.perf_counter() - test_start

    snapshot = get_snapshot()

    with tx_lock:

        packets_sent = tx_packets_sent
        max_lateness = tx_max_lateness_ms

    print()
    print("=" * 70)
    print("FINAL TEST RESULT")
    print("=" * 70)

    print(f"Configured frequency:       " f"{SEND_FREQUENCY_HZ} Hz")

    print(f"Configured send duration:   " f"{SEND_DURATION_S:.1f} s")

    print(f"Configured packet count:    " f"{configured_packet_count:,}")

    print(f"Actual packets sent:        " f"{packets_sent:,}")

    print(f"Actual elapsed time:        " f"{elapsed:.1f} s")

    print(f"Maximum TX lateness:        " f"{max_lateness:.3f} ms")

    for car_id in sorted(snapshot):

        car = snapshot[car_id]

        print()
        print(f"Slave {car_id}")
        print("-" * 70)

        if packets_sent > 0:

            world_loss_percent = car["world_packets_lost"] / packets_sent * 100.0

        else:

            world_loss_percent = 0.0

        print(f"Last world sequence:        " f"{car['last_world_seq']}")

        print(f"World packets lost:         " f"{car['world_packets_lost']}")

        print(f"World packet loss:          " f"{world_loss_percent:.4f} %")

        print(f"Loss events:                " f"{car['world_gap_events']}")

        print(f"Maximum consecutive loss:   " f"{car['max_world_gap']}")

        if car["world_gap_events"] > 0:

            average_gap = car["world_packets_lost"] / car["world_gap_events"]

            print(f"Average loss/event:         " f"{average_gap:.3f}")

        telemetry_total = car["telemetry_received"] + car["telemetry_lost"]

        if telemetry_total > 0:

            telemetry_loss_percent = car["telemetry_lost"] / telemetry_total * 100.0

        else:

            telemetry_loss_percent = 0.0

        print()

        print(f"Telemetry received:         " f"{car['telemetry_received']}")

        print(f"Telemetry packets lost:     " f"{car['telemetry_lost']}")

        print(f"Telemetry packet loss:      " f"{telemetry_loss_percent:.4f} %")


# =============================================================================
# MAIN
# =============================================================================


def main():

    global running
    global collect_telemetry
    global stats
    global test_start

    # -------------------------------------------------------------------------
    # Automatic log filename
    # -------------------------------------------------------------------------

    log_filename = "radio_test_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"

    configured_packet_count = round(SEND_FREQUENCY_HZ * SEND_DURATION_S)

    print()
    print("ESP-NOW LONG-DURATION TEST")
    print("=" * 70)

    print(f"Frequency:          " f"{SEND_FREQUENCY_HZ} Hz")

    print(f"Duration:           " f"{SEND_DURATION_S / 3600:.2f} hours")

    print(f"Packets:            " f"{configured_packet_count:,}")

    print(f"Logging interval:   " f"{LOG_INTERVAL_S:.0f} seconds")

    print(f"Log file:           " f"{log_filename}")

    print()

    # -------------------------------------------------------------------------
    # Serial port
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
        # RX thread
        # ---------------------------------------------------------------------

        receiver = threading.Thread(target=receive_loop, args=(ser,), daemon=True)

        receiver.start()

        # ---------------------------------------------------------------------
        # Reset slaves
        # ---------------------------------------------------------------------

        print("Resetting slaves...")

        collect_telemetry = False

        for _ in range(RESET_COUNT):

            send_world(ser, -1)

            time.sleep(RESET_INTERVAL_S)

        with stats_lock:
            stats.clear()

        ser.reset_input_buffer()

        # ---------------------------------------------------------------------
        # Open log
        # ---------------------------------------------------------------------

        with open(log_filename, "w", newline="", encoding="utf-8") as log_file:

            writer = csv.writer(log_file)

            writer.writerow(
                [
                    "timestamp",
                    "elapsed_s",
                    "car_id",
                    "world_packets_sent",
                    "last_world_seq",
                    "world_packets_lost",
                    "world_gap_events",
                    "max_world_gap",
                    "telemetry_received",
                    "telemetry_lost",
                    "telemetry_last_seq",
                    "max_tx_lateness_ms",
                ]
            )

            log_file.flush()

            # -----------------------------------------------------------------
            # Start measured test
            # -----------------------------------------------------------------

            collect_telemetry = True

            test_start = time.perf_counter()

            transmitter = threading.Thread(
                target=transmit_loop, args=(ser, configured_packet_count), daemon=True
            )

            logger = threading.Thread(
                target=log_loop, args=(writer, log_file), daemon=True
            )

            transmitter.start()
            logger.start()

            print(f"Starting test: " f"{configured_packet_count:,} packets")

            # -----------------------------------------------------------------
            # Wait for TX to finish
            # -----------------------------------------------------------------

            transmitter.join()

            # -----------------------------------------------------------------
            # Tail observation
            # -----------------------------------------------------------------

            time.sleep(TAIL_OBSERVATION_TIME_S)

            collect_telemetry = False

            # -----------------------------------------------------------------
            # Final snapshot
            # -----------------------------------------------------------------

            write_log_snapshot(writer, log_file)

            running = False

            logger.join(timeout=1.0)

        # ---------------------------------------------------------------------
        # Final results
        # ---------------------------------------------------------------------

        print_final_summary(configured_packet_count)

        print()
        print(f"Log saved to: {log_filename}")

    except KeyboardInterrupt:

        running = False
        collect_telemetry = False

        print()
        print("Test stopped manually.")

    finally:

        running = False

        ser.rts = False
        ser.dtr = False

        ser.close()


# =============================================================================

if __name__ == "__main__":
    main()
