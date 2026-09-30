/*
===============================================================================
slave_radio/main.cpp
===============================================================================

PURPOSE
-------
ESP-NOW slave/car radio for Seeed Studio XIAO ESP32-S3.

Current world message:

    W,<world_seq>

Examples:

    W,-1
    W,0
    W,1
    W,2


Telemetry message:

    T,car_id,telemetry_seq,last_world_seq,
      world_packets_lost,world_gap_events,max_world_gap


DIAGNOSTIC MODE
---------------
During an active test, the slave does NOT print diagnostic information.

If no valid world packet has been received for 1 second:

1. Telemetry transmission stops.
2. A diagnostic report is printed once to the slave USB serial port.

Example:

    === TEST DIAGNOSTICS ===
    Car ID:                  2
    Last world sequence:     2999
    World packets lost:      11
    World gap events:        11
    Maximum world gap:       1

    Telemetry attempted:     603
    Send call errors:        0
    Send success callbacks:  462
    Send failure callbacks:  141

This helps determine whether missing telemetry packets are being lost:

    - before ESP-NOW accepts the send request
    - during ESP-NOW unicast transmission
    - or later at the master / serial / Python side


RESET
-----
W,-1 resets all test statistics.

===============================================================================
*/

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>

// =============================================================================
// CONFIGURATION
// =============================================================================

#define CAR_ID 2

const uint32_t TELEMETRY_INTERVAL_MS = 50; // 20 Hz
const uint32_t TEST_TIMEOUT_MS = 1000;     // stop after 1 s without world data
const size_t MAX_MESSAGE_LENGTH = 250;

// =============================================================================
// WORLD STATE
// =============================================================================

int32_t last_world_seq = -1;

uint32_t world_packets_lost = 0;
uint32_t world_gap_events = 0;
uint32_t max_world_gap = 0;

// =============================================================================
// TELEMETRY STATE
// =============================================================================

uint32_t telemetry_seq = 0;

volatile uint32_t telemetry_attempted = 0;
volatile uint32_t telemetry_send_call_errors = 0;
volatile uint32_t telemetry_send_success = 0;
volatile uint32_t telemetry_send_failure = 0;

// =============================================================================
// RADIO STATE
// =============================================================================

uint8_t master_mac[6] = {0};

uint32_t last_telemetry_ms = 0;
uint32_t last_world_receive_ms = 0;

bool test_started = false;
bool diagnostics_printed = false;

// =============================================================================
// ESP-NOW SEND CALLBACK
// =============================================================================
// Called after a unicast telemetry transmission completes at the Wi-Fi layer.

void onSend(
    const uint8_t *mac_addr,
    esp_now_send_status_t status)
{
    if (status == ESP_NOW_SEND_SUCCESS)
        telemetry_send_success++;
    else
        telemetry_send_failure++;
}

// =============================================================================
// ESP-NOW RECEIVE CALLBACK
// =============================================================================

void onReceive(
    const uint8_t *mac,
    const uint8_t *data,
    int len)
{
    if (len <= 0 || len > MAX_MESSAGE_LENGTH)
        return;

    // -------------------------------------------------------------------------
    // Convert ESP-NOW payload into a null-terminated text message.
    // -------------------------------------------------------------------------

    char message[MAX_MESSAGE_LENGTH + 1];

    memcpy(message, data, len);
    message[len] = '\0';

    // Currently we only understand:
    //
    //     W,<sequence>
    // -------------------------------------------------------------------------

    if (message[0] != 'W' || message[1] != ',')
        return;

    // Valid world packet received.
    memcpy(master_mac, mac, 6);

    last_world_receive_ms = millis();
    test_started = true;
    diagnostics_printed = false;

    int32_t world_seq = atoi(message + 2);

    // -------------------------------------------------------------------------
    // RESET
    // -------------------------------------------------------------------------

    if (world_seq == -1)
    {

        last_world_seq = -1;

        world_packets_lost = 0;
        world_gap_events = 0;
        max_world_gap = 0;

        telemetry_seq = 0;

        telemetry_attempted = 0;
        telemetry_send_call_errors = 0;
        telemetry_send_success = 0;
        telemetry_send_failure = 0;

        return;
    }

    // -------------------------------------------------------------------------
    // First normal packet after reset
    // -------------------------------------------------------------------------

    if (last_world_seq == -1)
    {

        last_world_seq = world_seq;

        return;
    }

    // -------------------------------------------------------------------------
    // Normal packet
    // -------------------------------------------------------------------------

    if (world_seq > last_world_seq)
    {

        uint32_t gap =
            world_seq - last_world_seq - 1;

        if (gap > 0)
        {

            world_packets_lost += gap;
            world_gap_events++;

            if (gap > max_world_gap)
                max_world_gap = gap;
        }

        last_world_seq = world_seq;
    }

    // Duplicate / stale packets are ignored.
}

// =============================================================================
// PRINT DIAGNOSTICS
// =============================================================================

void printDiagnostics()
{
    Serial.println();
    Serial.println("================================");
    Serial.println("TEST DIAGNOSTICS");
    Serial.println("================================");

    Serial.print("Car ID:                  ");
    Serial.println(CAR_ID);

    Serial.print("Last world sequence:     ");
    Serial.println(last_world_seq);

    Serial.print("World packets lost:      ");
    Serial.println(world_packets_lost);

    Serial.print("World gap events:        ");
    Serial.println(world_gap_events);

    Serial.print("Maximum world gap:       ");
    Serial.println(max_world_gap);

    Serial.println();

    Serial.print("Telemetry attempted:     ");
    Serial.println(telemetry_attempted);

    Serial.print("Send call errors:        ");
    Serial.println(telemetry_send_call_errors);

    Serial.print("Send success callbacks:  ");
    Serial.println(telemetry_send_success);

    Serial.print("Send failure callbacks:  ");
    Serial.println(telemetry_send_failure);

    Serial.println("================================");
}

// =============================================================================
// SETUP
// =============================================================================

void setup()
{
    Serial.begin(115200);

    delay(1000);

    Serial.println();
    Serial.println("================================");
    Serial.println("ESP-NOW SLAVE RADIO");
    Serial.println("================================");

    Serial.print("Car ID: ");
    Serial.println(CAR_ID);

    WiFi.mode(WIFI_STA);

    Serial.print("MAC: ");
    Serial.println(WiFi.macAddress());

    if (esp_now_init() != ESP_OK)
    {

        Serial.println("ERROR: ESP-NOW initialization failed.");

        while (true)
            delay(1000);
    }

    esp_now_register_recv_cb(onReceive);
    esp_now_register_send_cb(onSend);

    Serial.println("ESP-NOW initialized.");
    Serial.println("Waiting for broadcasts...");
}

// =============================================================================
// LOOP
// =============================================================================

void loop()
{
    // -------------------------------------------------------------------------
    // TEST TIMEOUT
    //
    // If world packets stop for more than 1 second:
    //
    // - stop transmitting telemetry
    // - print diagnostics once
    // -------------------------------------------------------------------------

    if (
        test_started &&
        millis() - last_world_receive_ms > TEST_TIMEOUT_MS)
    {

        if (!diagnostics_printed)
        {

            printDiagnostics();
            diagnostics_printed = true;
        }

        return;
    }

    // -------------------------------------------------------------------------
    // Telemetry rate: 20 Hz
    // -------------------------------------------------------------------------

    if (
        millis() - last_telemetry_ms < TELEMETRY_INTERVAL_MS)
        return;

    last_telemetry_ms = millis();

    // Do not transmit before receiving a world packet.
    if (!test_started)
        return;

    // -------------------------------------------------------------------------
    // Check whether master MAC is known.
    // -------------------------------------------------------------------------

    bool master_known = false;

    for (int i = 0; i < 6; i++)
    {

        if (master_mac[i] != 0)
        {

            master_known = true;
            break;
        }
    }

    if (!master_known)
        return;

    // -------------------------------------------------------------------------
    // Add master as ESP-NOW peer if necessary.
    // -------------------------------------------------------------------------

    if (!esp_now_is_peer_exist(master_mac))
    {

        esp_now_peer_info_t peer = {};

        memcpy(
            peer.peer_addr,
            master_mac,
            6);

        peer.channel = 0;
        peer.encrypt = false;

        if (esp_now_add_peer(&peer) != ESP_OK)
            return;
    }

    // -------------------------------------------------------------------------
    // Build telemetry message.
    // -------------------------------------------------------------------------

    char message[128];

    int length = snprintf(
        message,
        sizeof(message),

        "T,%u,%lu,%ld,%lu,%lu,%lu",

        CAR_ID,
        telemetry_seq++,
        last_world_seq,
        world_packets_lost,
        world_gap_events,
        max_world_gap);

    if (
        length <= 0 ||
        length >= sizeof(message))
        return;

    // -------------------------------------------------------------------------
    // Attempt ESP-NOW transmission.
    // -------------------------------------------------------------------------

    telemetry_attempted++;

    esp_err_t result = esp_now_send(
        master_mac,
        reinterpret_cast<uint8_t *>(message),
        length);

    // This means ESP-NOW rejected the send request locally.
    //
    // The send callback is separate and tells us whether an accepted
    // transmission eventually succeeded or failed.

    if (result != ESP_OK)
        telemetry_send_call_errors++;
}