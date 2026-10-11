# PC Bluetooth outage investigation

The latest two-hour Test 11 run began at 23:26:10 on October 10, 2026
(Hong Kong time). All three cars stopped delivering notifications at
23:51:56, approximately 25 minutes 46 seconds into the run. Their last
notifications arrived within 60 ms. Each car had reconnected once after an
initial overload; all ended disconnected after unsuccessful retries.

The user also observed the Bluetooth mouse stop working. This materially
strengthens the shared PC adapter/driver hypothesis. ESP32 recovery alone
cannot restore a PC controller that is not communicating.

Windows System event BTHUSB, ID 3, at 00:44:50 on October 11 reports:
"A command sent to the adapter has timed out. The adapter did not respond."
This event is about 53 minutes after the notification outage, so it is
evidence of an adapter communication failure during the same run, not an
exactly synchronized event proving the initial trigger.

Current checks found:

- Intel(R) Wireless Bluetooth(R), driver 22.0.0.2, dated September 13, 2020.
- Lenovo computer model 81UH.
- Adapter PnP status OK, error code 0; WinRT radio state ON.
- An eight-second Bleak scan with normal Windows access found zero advertisers.
- No server serial logs were captured for this latest two-hour run; the most
  recent server capture belongs to the earlier 15-minute validation.

The best-supported explanation is a shared PC Bluetooth adapter/driver or
transport failure. The exact trigger (driver bug, controller firmware,
power management, radio conditions or concurrent-link load) remains unknown.
The device's OK status does not demonstrate successful radio communication.

Recover the PC Bluetooth subsystem by restarting Bluetooth or rebooting the
PC, then install the appropriate Bluetooth driver from Lenovo's support
tools/site. Microsoft recommends obtaining an updated driver from the PC
manufacturer when Windows driver updates do not resolve the problem:
[Microsoft Bluetooth driver guidance](https://support.microsoft.com/en-us/windows/hardware/bluetooth/update-bluetooth-drivers-in-windows).
After recovery, check ordinary mouse operation and BLE discovery before
another long test. A comparison run with the mouse using a USB receiver can
help isolate interaction with other Bluetooth connections.

Machine-readable observations and counter-wrap timestamps are saved in
[bluetooth_pc_outage_20261011.json](results/bluetooth_pc_outage_20261011.json).
