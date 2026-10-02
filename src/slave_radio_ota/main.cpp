/*
===============================================================================
slave_radio_ota/main.cpp
===============================================================================

PURPOSE
-------
ESP-NOW slave/car radio for Seeed Studio XIAO ESP32-S3,
with Over-The-Air (OTA) firmware update support.

This is a copy of slave_radio with one addition: the slave connects
to the offline lab router ("M7026 Lab's ASUS Router") so that new
firmware can be uploaded over Wi-Fi from PlatformIO (espota),
without a USB cable.

NETWORK SETUP
-------------
Router SSID:     M7026 Lab's ASUS Router
Router channel:  6 (fixed in the router admin page)
Slave address:   192.168.50.200 (static, no DHCP)
Gateway:         192.168.50.1

Because the slave is connected to the router, its radio follows the
router's channel (6). ESP-NOW therefore also runs on channel 6, and
the master firmware (master_radio_ota) is fixed to channel 6 to match.

OTA
---
Hostname:        esp32-car2
OTA password:    ota7026
Upload command:  pio run -e slave_radio_ota -t upload

ArduinoOTA.handle() is called at the TOP of loop() so that OTA
requests are serviced even when the radio test is idle or in
diagnostic mode. During an upload the board reboots automatically
into the new firmware.

IDLE STATUS PRINT
-----------------
While no radio test is running, a one-line status report is printed
every 10 s (Wi-Fi state, IP, channel, RSSI, OTA state). This lets a
USB serial monitor attached AFTER boot still see the connection
state. During an active test the slave stays silent, as before.

RADIO BEHAVIOR (unchanged from slave_radio)
-------------------------------------------
World message:

    W,<world_seq>

Telemetry message (20 Hz, unicast to master MAC learned from
the first world packet):

    T,car_id,telemetry_seq,last_world_seq,
      world_packets_lost,world_gap_events,max_world_gap

W,-1 resets all test statistics.

If no valid world packet has been received for 1 second, telemetry
stops and a diagnostic report is printed once to USB serial.

===============================================================================
*/

#include <Arduino.h>   
#include <WiFi.h>
#include <ArduinoOTA.h>
#include <esp_now.h>

// =============================================================================
// CONFIGURATION
// =============================================================================

#define CAR_ID 2

// Firmware version tag, printed in the idle status line so that an OTA
// update can be verified visually on the USB serial monitor.

#define FW_VERSION "v2"

// Offline lab router used for OTA.

const char *WIFI_SSID = "M7026 Lab's ASUS Router";
const char *WIFI_PASSWORD = "70267026";

// Static IP so PlatformIO's upload_port never changes.

const IPAddress STATIC_IP(192, 168, 50, 200);
const IPAddress GATEWAY(192, 168, 50, 1);
const IPAddress SUBNET(255, 255, 255, 0);

// OTA identity. The password is required by PlatformIO (upload_flags --auth).

const char *OTA_HOSTNAME = "esp32-car2";
const char *OTA_PASSWORD = "ota7026";

const uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;

const uint32_t TELEMETRY_INTERVAL_MS = 50; // 20 Hz
const uint32_t TEST_TIMEOUT_MS = 1000;     // stop after 1 s without world data
const uint32_t STATUS_INTERVAL_MS = 10000; // idle status print every 10 s
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

uint32_t last_status_ms = 0;

// =============================================================================
// ESP-NOW SEND CALLBACK
// =============================================================================

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
// PRINT IDLE STATUS
// =============================================================================
// Printed every STATUS_INTERVAL_MS while no radio test is running, so a
// USB serial monitor attached after boot can still see the connection state.

void printStatus()
{
    Serial.print("STATUS | FW: ");
    Serial.print(FW_VERSION);

    Serial.print(" | WiFi: ");

    if (WiFi.status() == WL_CONNECTED)
        Serial.print("connected");
    else
        Serial.print("DISCONNECTED");

    Serial.print(" | IP: ");
    Serial.print(WiFi.localIP());

    Serial.print(" | Channel: ");
    Serial.print(WiFi.channel());

    Serial.print(" | RSSI: ");
    Serial.print(WiFi.RSSI());
    Serial.print(" dBm");

    Serial.print(" | OTA: ready (");
    Serial.print(OTA_HOSTNAME);
    Serial.print(")");

    Serial.print(" | Test: ");
    Serial.print(test_started ? "started" : "waiting for world packets");

    Serial.print(" | Last world seq: ");
    Serial.println(last_world_seq);
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
    Serial.println("ESP-NOW SLAVE RADIO (OTA)");
    Serial.println("================================");

    Serial.print("Firmware: ");
    Serial.println(FW_VERSION);

    Serial.print("Car ID: ");
    Serial.println(CAR_ID);

    // -------------------------------------------------------------------------
    // Connect to the offline lab router (static IP, no DHCP).
    //
    // After connecting, the radio is locked to the router's channel (6),
    // and ESP-NOW will operate on that channel as well.
    // -------------------------------------------------------------------------

    WiFi.mode(WIFI_STA);

    WiFi.config(STATIC_IP, GATEWAY, SUBNET);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    Serial.print("Connecting to router");

    uint32_t connect_start = millis();

    while (WiFi.status() != WL_CONNECTED)
    {

        if (millis() - connect_start > WIFI_CONNECT_TIMEOUT_MS)
        {

            Serial.println();
            Serial.println("ERROR: Router connection failed.");

            while (true)
                delay(1000);
        }

        Serial.print(".");
        delay(500);
    }

    Serial.println();

    Serial.print("IP:      ");
    Serial.println(WiFi.localIP());

    Serial.print("MAC:     ");
    Serial.println(WiFi.macAddress());

    Serial.print("Channel: ");
    Serial.println(WiFi.channel());

    // -------------------------------------------------------------------------
    // OTA
    // -------------------------------------------------------------------------

    ArduinoOTA.setHostname(OTA_HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASSWORD);

    ArduinoOTA.onStart([]()
                       { Serial.println("OTA update started..."); });

    ArduinoOTA.onEnd([]()
                     { Serial.println("OTA update finished. Rebooting."); });

    ArduinoOTA.onError([](ota_error_t error)
                       {
        Serial.print("OTA error: ");
        Serial.println(error); });

    ArduinoOTA.begin();

    Serial.println("OTA ready.");

    // -------------------------------------------------------------------------
    // ESP-NOW
    // -------------------------------------------------------------------------

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
    // OTA must be serviced even when the radio test is idle or in
    // diagnostic mode, so this call stays at the top of loop().
    // -------------------------------------------------------------------------

    ArduinoOTA.handle();

    // -------------------------------------------------------------------------
    // IDLE STATUS
    //
    // While no test is running, print a one-line status report every
    // STATUS_INTERVAL_MS. This stays silent during an active test, so a
    // USB serial monitor attached after boot can still see whether the
    // slave is connected to the router and ready for OTA.
    // -------------------------------------------------------------------------

    bool test_active =
        test_started &&
        millis() - last_world_receive_ms <= TEST_TIMEOUT_MS;

    if (
        !test_active &&
        millis() - last_status_ms >= STATUS_INTERVAL_MS)
    {

        last_status_ms = millis();
        printStatus();
    }

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

    if (result != ESP_OK)
        telemetry_send_call_errors++;
}
