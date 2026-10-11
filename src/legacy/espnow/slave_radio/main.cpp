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

LOCAL SERIAL OUTPUT
-------------------
Startup and OTA event messages are printed over USB serial. Periodic
status and timeout diagnostics remain disabled during radio-loss testing.
ESP-NOW telemetry and OTA remain active. Wi-Fi modem sleep is disabled
when station mode starts to keep the radio awake for ESP-NOW reception.

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
stops. Local USB diagnostics are disabled during this test.

===============================================================================
*/

#include <Arduino.h>
#include <atomic>
#include <WiFi.h>
#include <ArduinoOTA.h>
#include <esp_now.h>

// =============================================================================
// CONFIGURATION
// =============================================================================

#if !defined(CAR_ID)
#define CAR_ID 2
#endif

// Wifi used for OTA.
#if !defined(WIFI_SSID)
#define WIFI_SSID "M7026 Lab ASUS Router"
#define WIFI_PASSWORD "70267026"
#endif

#if !defined(STATIC_IP_ADDRESS)
#define STATIC_IP_ADDRESS 192, 168, 50, 200
#endif
#if !defined(GATEWAY_ADDRESS)
#define GATEWAY_ADDRESS 192, 168, 50, 1
#endif

// Static IP so PlatformIO's upload_port never changes.
const IPAddress STATIC_IP(STATIC_IP_ADDRESS);
const IPAddress GATEWAY(GATEWAY_ADDRESS);
const IPAddress SUBNET(255, 255, 255, 0);
const uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;

// OTA identity. The password is required by PlatformIO (upload_flags --auth).

#if !defined(OTA_HOSTNAME)
#define OTA_HOSTNAME "esp32-car1"
#endif
#if !defined(OTA_PASSWORD)
#define OTA_PASSWORD "ota7026"
#endif

const uint32_t TELEMETRY_INTERVAL_MS = 50; // 20 Hz
const uint32_t TEST_TIMEOUT_MS = 1000;     // stop after 1 s without world data
// const uint32_t STATUS_INTERVAL_MS = 10000; // local status disabled
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

std::atomic<uint32_t> telemetry_attempted{0};
std::atomic<uint32_t> telemetry_send_call_errors{0};
std::atomic<uint32_t> telemetry_send_success{0};
std::atomic<uint32_t> telemetry_send_failure{0};

// =============================================================================
// RADIO STATE
// =============================================================================

uint8_t master_mac[6] = {0};

uint32_t last_telemetry_ms = 0;
uint32_t last_world_receive_ms = 0;

bool test_started = false;
bool ota_initialized = false;
// bool diagnostics_printed = false; // local diagnostics disabled

// uint32_t last_status_ms = 0; // local status disabled

// =============================================================================
// ESP-NOW SEND CALLBACK
// =============================================================================

void onSend(
    const esp_now_send_info_t *tx_info,
    esp_now_send_status_t status)
{
    if (status == ESP_NOW_SEND_SUCCESS)
        telemetry_send_success.fetch_add(1, std::memory_order_relaxed);
    else
        telemetry_send_failure.fetch_add(1, std::memory_order_relaxed);
}

// =============================================================================
// ESP-NOW CALLBACK - After receiving a world packet.
// =============================================================================

void onReceive(
    const esp_now_recv_info_t *esp_now_info,
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
    memcpy(master_mac, esp_now_info->src_addr, 6);

    last_world_receive_ms = millis();
    test_started = true;
    // diagnostics_printed = false; // local diagnostics disabled

    int32_t world_seq = atoi(message + 2);

    // -------------------------------------------------------------------------
    // RESET when world_seq == -1. This is a special packet sent by the master
    // to reset all test statistics.
    // -------------------------------------------------------------------------

    if (world_seq == -1)
    {

        last_world_seq = -1;

        world_packets_lost = 0;
        world_gap_events = 0;
        max_world_gap = 0;

        telemetry_seq = 0;

        telemetry_attempted.store(0, std::memory_order_relaxed);
        telemetry_send_call_errors.store(0, std::memory_order_relaxed);
        telemetry_send_success.store(0, std::memory_order_relaxed);
        telemetry_send_failure.store(0, std::memory_order_relaxed);

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
// LOCAL SERIAL STATUS (disabled during radio-loss tests)
// =============================================================================
// Printed every STATUS_INTERVAL_MS while no radio test is running, so a
// USB serial monitor attached after boot can still see the connection state.

#if 0
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
#endif

// =============================================================================
// SETUP
// =============================================================================

void OTA_onStart();

void initialize_OTA()
{
    ArduinoOTA.setHostname(OTA_HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASSWORD);

    ArduinoOTA.onStart([]()
                       { OTA_onStart(); });

    ArduinoOTA.onEnd([]()
                     { Serial.println("OTA update finished. Rebooting."); });

    ArduinoOTA.onError([](ota_error_t error)
                       {
        Serial.print("OTA error: ");
        Serial.println(error); });

    ArduinoOTA.begin();
    Serial.println("OTA ready.");
}

void OTA_onStart()
{
    // After integration: Turn off motors and stuff here.
    Serial.println("OTA update started...");
}

void setup()
{
    // -------------------------------------------------------------------------
    // Local USB serial output is used during setup only.
    // -------------------------------------------------------------------------

    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("================================");
    Serial.println("ESP-NOW SLAVE RADIO (OTA)");
    Serial.println("================================");

    Serial.print("Car ID: ");
    Serial.println(CAR_ID);

    // -------------------------------------------------------------------------
    // Connect to Wifi router for OTA upload (static IP, no DHCP).
    //
    // After connecting, the radio is locked to the router's channel (6),
    // and ESP-NOW will operate on that channel as well.
    // -------------------------------------------------------------------------

    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false); // To keep the radio awake for low latency ESP-NOW reception.

    WiFi.config(STATIC_IP, GATEWAY, SUBNET);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    Serial.print("Connecting to router");

    uint32_t connect_start = millis();

    // Wait for Wifi connection. If the router is not available beyond timeout, continue controller anyways.
    while (1)
    {
        // If timeout, print an error message and move on.
        if (millis() - connect_start > WIFI_CONNECT_TIMEOUT_MS)
        {
            Serial.println();
            Serial.println("ERROR: Router connection timeout.");
            break;
        }
        // If successful, print a newline and the IP address.
        if (WiFi.status() == WL_CONNECTED)
        {
            Serial.println();

            Serial.print("IP:      ");
            Serial.println(WiFi.localIP());

            Serial.print("MAC:     ");
            Serial.println(WiFi.macAddress());

            Serial.print("Channel: ");
            Serial.println(WiFi.channel());

            // Initialize OTA on if Wi-Fi connection is successful.
            initialize_OTA();
            ota_initialized = true;
            break;
        }
        // While not yet timeout, print a dot every 500 ms to indicate progress.
        Serial.print(".");
        delay(500);
    }

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
    Serial.println("Setup complete. Waiting for world packets...");
}

// =============================================================================
// LOOP
// =============================================================================

void loop()
{
    // The initial router attempt can time out while Wi-Fi is still connecting.
    // Complete OTA setup if Wi-Fi connects later. Sleep was disabled in setup().
    bool wifi_connected = WiFi.status() == WL_CONNECTED;
    if (wifi_connected && !ota_initialized)
    {
        initialize_OTA();
        ota_initialized = true;
    }

    // -------------------------------------------------------------------------
    // OTA must be serviced even when the radio test is idle or in
    // diagnostic mode, so this call stays at the top of loop().
    // -------------------------------------------------------------------------

    ArduinoOTA.handle();

    // -------------------------------------------------------------------------
    // IDLE STATUS (local serial output disabled)
    //
    // While no test is running, print a one-line status report every
    // STATUS_INTERVAL_MS. This stays silent during an active test, so a
    // USB serial monitor attached after boot can still see whether the
    // slave is connected to the router and ready for OTA.
    // -------------------------------------------------------------------------

#if 0
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
#endif

    // -------------------------------------------------------------------------
    // TEST TIMEOUT
    //
    // If world packets stop for more than 1 second:
    //
    // - stop transmitting telemetry
    // - local serial diagnostics are disabled for this test
    // -------------------------------------------------------------------------

    if (
        test_started &&
        millis() - last_world_receive_ms > TEST_TIMEOUT_MS)
    {

#if 0
        if (!diagnostics_printed)
        {

            printDiagnostics();
            diagnostics_printed = true;
        }
#endif

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

    telemetry_attempted.fetch_add(1, std::memory_order_relaxed);

    esp_err_t result = esp_now_send(
        master_mac,
        reinterpret_cast<uint8_t *>(message),
        length);

    // This means ESP-NOW rejected the send request locally.

    if (result != ESP_OK)
        telemetry_send_call_errors.fetch_add(1, std::memory_order_relaxed);
}
