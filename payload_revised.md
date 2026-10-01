# ESP-NOW Payload Specification

Status: design draft for application protocol v1.

The PC broadcasts one shared **World State** at **50 Hz**. Each of the four cars sends its own **Robot State** at **20 Hz**. The PC sends high-level intent; each car performs line following, motor control, NFC-based localization, and intersection execution locally.

## Transport and Encoding

- Maximum ESP-NOW application payload: **250 bytes**.
- World State is broadcast from the master to all four cars.
- Robot State is sent from each car back to the master.
- Each packet is a self-contained snapshot.
- Encoding uses compact ASCII CSV.
- No spaces, quoted strings, embedded commas, or embedded newlines.
- Integers are decimal without padding.
- Commands are exactly two uppercase ASCII characters.
- One CSV message corresponds to one ESP-NOW packet.
- USB serial framing may add a newline, but that newline is not transmitted over ESP-NOW.

# World State

Format:

```text
W,<world_seq>,<topology_state>,<command_1>,<command_2>,<command_3>,<command_4>
```

The four command positions correspond permanently to Cars 1-4. Robot IDs are therefore not included in the World State.

| Name | Expected format | Total character (maximum character width) | Meaning of that property |
| --- | --- | ---: | --- |
| Message type | `W` | 1 | Identifies the packet as a World State snapshot. |
| `world_seq` | `0-999999` | 6 | Monotonically increasing World State sequence number. Used by each car to detect stale packets and missing broadcasts. |
| `topology_state` | `0-99` | 2 | ID of the active predefined map/topology configuration. Only eight normal configurations are currently required; the two-digit range leaves spare values for future use or special states. |
| `command_1` | `FL`, `FS`, `FR`, or `SS` | 2 | Command for Car 1. |
| `command_2` | `FL`, `FS`, `FR`, or `SS` | 2 | Command for Car 2. |
| `command_3` | `FL`, `FS`, `FR`, or `SS` | 2 | Command for Car 3. |
| `command_4` | `FL`, `FS`, `FR`, or `SS` | 2 | Command for Car 4. |

Maximum encoded length including six commas:

**23 characters / bytes**

Example:

```text
W,1500,3,FL,FS,SS,FR
```

This means World State packet 1500, topology configuration 3, followed by the commands for Cars 1-4.

## Command Encoding

| Position | Values | Meaning |
| --- | --- | --- |
| First character | `F`, `S` | `F` = FORWARD, `S` = STOP. |
| Second character | `L`, `S`, `R` | Desired branch at the next valid decision point: LEFT, STRAIGHT, or RIGHT. |

Examples:

- `FL` = forward and take the left branch.
- `FS` = forward and continue straight.
- `FR` = forward and take the right branch.
- `SS` = stop.

STOP takes precedence over the branch command. The car locally latches the branch command when entering an intersection so that later World State packets do not restart or alter a maneuver already being executed.

## Topology State

The topology is stored locally in every car.

`topology_state` selects one of the predefined map configurations. The car combines this topology information with its locally detected NFC marker to determine its navigation state without requiring an additional communication round trip to the PC.

The present system requires only **eight topology configurations**. The protocol retains a two-character field (`0-99`) so additional configurations or reserved special states can be introduced later.

# Robot State

Format:

```text
T,<robot_id>,<telemetry_seq>,<last_world_seq>,<world_packets_lost_1s>,<topology_state>,<marker_id>,<marker_age_ms>,<intersection_state>,<left_speed_mm_s>,<right_speed_mm_s>,<battery_mv>
```

Each car sends one Robot State packet at approximately **20 Hz**.

| Name | Expected format | Total character (maximum character width) | Meaning of that property |
| --- | --- | ---: | --- |
| Message type | `T` | 1 | Identifies the packet as Robot State telemetry. |
| `robot_id` | `1-4` | 1 | Identifies which car generated this telemetry packet. |
| `telemetry_seq` | `0-4294967295` | 10 | Sequence number incremented for every Robot State transmission. Allows the PC to detect missing telemetry packets. |
| `last_world_seq` | `0-999999` | 6 | Most recent valid World State sequence number received and applied by this car. May be empty before the first World State packet is received. |
| `world_packets_lost_1s` | `0-255` | 3 | Number of missing World State sequence numbers detected during the most recently completed one-second measurement window. The reported value remains fixed until the next one-second window completes. |
| `topology_state` | `0-99` | 2 | Topology configuration currently applied locally by the car. |
| `marker_id` | `0-65535` | 5 | Logical ID of the most recently detected NFC marker. `0` may be reserved for unknown/not yet detected. |
| `marker_age_ms` | `0-4294967295` | 10 | Milliseconds elapsed since the most recent valid NFC marker detection. |
| `intersection_state` | `0-4` | 1 | Current local intersection/navigation FSM state. |
| `left_speed_mm_s` | signed integer, `-32768` to `32767` | 6 | Current measured left-wheel speed in mm/s, derived from encoder feedback through the motor driver. Positive and negative signs indicate direction. |
| `right_speed_mm_s` | signed integer, `-32768` to `32767` | 6 | Current measured right-wheel speed in mm/s, derived from encoder feedback through the motor driver. Positive and negative signs indicate direction. |
| `battery_mv` | `0-65535` | 5 | Measured battery voltage in millivolts. Example: `7420` = 7.420 V. |

Maximum encoded length including eleven commas:

**67 characters / bytes**

Example:

```text
T,2,600,1499,1,3,127,85,2,483,476,7420
```

Interpretation:

- Car 2
- telemetry packet 600
- latest World State received: 1499
- one World State packet lost during the most recently completed one-second window
- topology configuration 3
- most recent NFC marker: 127
- marker detected 85 ms ago
- intersection state: INSIDE
- left-wheel measured speed: 483 mm/s
- right-wheel measured speed: 476 mm/s
- battery voltage: 7.420 V

## Intersection State

| Code | State | Meaning |
| --- | --- | --- |
| `0` | `FOLLOW_LINE` | Normal line following outside an intersection. |
| `1` | `APPROACH` | An intersection has been identified and the car is approaching its decision point. |
| `2` | `INSIDE` | The car is executing the latched branch maneuver inside the intersection. |
| `3` | `CLEARED` | The previous intersection has been cleared and normal line following has resumed. |
| `4` | `FAULT` | Local intersection execution cannot proceed normally. |

`CLEARED` should remain observable long enough to be captured by the 20 Hz telemetry stream rather than existing only as a short pulse.

# Packet-Loss Monitoring

## PC to Car

Each car compares incoming `world_seq` values.

If the sequence jumps forward by more than one, the missing sequence numbers are added to the current one-second loss counter.

Example:

```text
100 -> 101 -> 104
```

indicates two missing packets: 102 and 103.

The car maintains:

```text
current_window_loss
last_completed_window_loss
```

At the end of every one-second measurement window:

```text
last_completed_window_loss = current_window_loss
current_window_loss = 0
```

Robot State reports `last_completed_window_loss` as `world_packets_lost_1s`.

This value is therefore stable for one second rather than changing continuously during the active measurement window.

The value is intended as a communication-health diagnostic rather than an exact physical-layer loss measurement. If no World State packets arrive for an extended period, missing packets become observable only when a later sequence number is received.

If the detected loss exceeds 255 packets in one measurement window, the reported value should saturate at `255`.

## Car to PC

The PC detects missing Robot State packets by observing gaps in each car's `telemetry_seq`.

No additional telemetry-loss field is required inside Robot State.

# Freshness and Safety

`last_world_seq` allows the PC to see whether each car is operating on a recent World State.

Each car must also implement a local command-freshness timeout. If valid World State packets stop arriving for longer than the agreed timeout, the car should enter a safe STOP condition rather than continue indefinitely with the previous command.

The exact timeout value should be defined together with the vehicle controller implementation.
