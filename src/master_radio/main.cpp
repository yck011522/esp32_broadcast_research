/*
===============================================================================
master_radio_ota/main.cpp
===============================================================================

PURPOSE
-------
Schema-agnostic ESP-NOW <-> USB serial bridge.

Identical to master_radio, except that the Wi-Fi channel is fixed to
channel 6.

WHY CHANNEL 6?
--------------
In the OTA test setup the slave connects to the lab router
("M7026 Lab's ASUS Router"), which is fixed to Wi-Fi channel 6.

When an ESP32 connects to an access point, its radio follows the
access point's channel, and ESP-NOW on the slave therefore also
operates on channel 6.

The master is not connected to any access point, so it would
otherwise stay on channel 1 and the two radios would not hear
each other. Fixing the master to channel 6 keeps ESP-NOW working.

PC -> MASTER -> SLAVES
----------------------
PC sends one newline-terminated serial message:

    W,1234\n

Master:
    1. Reads bytes until '\n'
    2. Removes the newline
    3. Broadcasts the raw message over ESP-NOW


SLAVE -> MASTER -> PC
---------------------
Slave sends one ESP-NOW packet:

    T,2,123,456,0,0,0

Master:
    1. ESP-NOW receive callback copies the packet into a queue
    2. loop() reads packets from the queue
    3. Writes the raw message to USB serial
    4. Appends '\n'


MESSAGE SIZE
------------
Maximum raw message length:

    250 bytes

One serial line corresponds to exactly one ESP-NOW packet.

===============================================================================
*/

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>

// =============================================================================
// CONFIGURATION
// =============================================================================

// Must match the lab router's fixed Wi-Fi channel so that ESP-NOW
// works between this master and the router-connected slave.

const uint8_t ESP_NOW_CHANNEL = 6;

const size_t MAX_MESSAGE_LENGTH = 250;

// Number of received ESP-NOW packets that can wait for USB forwarding.

const size_t RX_QUEUE_LENGTH = 32;

// =============================================================================
// MESSAGE TYPE USED INTERNALLY BY MASTER
// =============================================================================

struct RadioMessage
{
    uint16_t length;
    uint8_t data[MAX_MESSAGE_LENGTH];
};

// =============================================================================
// STATE
// =============================================================================

// ESP-NOW broadcast MAC address.

// uint8_t broadcast_mac[] = {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x18};
uint8_t broadcast_mac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Serial input buffer for PC -> ESP-NOW.

char serial_buffer[MAX_MESSAGE_LENGTH];
size_t serial_length = 0;

// Queue for ESP-NOW -> PC traffic.

QueueHandle_t rx_queue;

// =============================================================================
// ESP-NOW RECEIVE CALLBACK
// =============================================================================
//
// IMPORTANT:
//
// Do not perform Serial.write() here.
//
// Just copy the packet into the queue and return as quickly as possible.

void onReceive(
    const uint8_t *mac,
    const uint8_t *data,
    int len)
{
    if (len <= 0 || len > MAX_MESSAGE_LENGTH)
        return;

    RadioMessage message;

    message.length = len;

    memcpy(
        message.data,
        data,
        len);

    // Non-blocking queue insertion.
    //
    // If the queue is full, this packet is dropped.

    xQueueSend(
        rx_queue,
        &message,
        0);
}

// =============================================================================
// SETUP
// =============================================================================

void setup()
{
    Serial.begin(115200);

    // -------------------------------------------------------------------------
    // Create ESP-NOW receive queue
    // -------------------------------------------------------------------------

    rx_queue = xQueueCreate(
        RX_QUEUE_LENGTH,
        sizeof(RadioMessage));

    if (rx_queue == nullptr)
    {

        while (true)
            delay(1000);
    }

    // -------------------------------------------------------------------------
    // Wi-Fi / ESP-NOW
    // -------------------------------------------------------------------------

    WiFi.mode(WIFI_STA);

    // Fix the radio to the lab router's channel BEFORE initializing ESP-NOW.

    esp_wifi_set_channel(
        ESP_NOW_CHANNEL,
        WIFI_SECOND_CHAN_NONE);

    if (esp_now_init() != ESP_OK)
    {

        while (true)
            delay(1000);
    }

    esp_now_register_recv_cb(onReceive);

    // -------------------------------------------------------------------------
    // Register broadcast address
    // -------------------------------------------------------------------------

    esp_now_peer_info_t peer = {};

    memcpy(
        peer.peer_addr,
        broadcast_mac,
        6);

    peer.channel = ESP_NOW_CHANNEL;
    peer.encrypt = false;

    esp_now_add_peer(&peer);
}

// =============================================================================
// LOOP
// =============================================================================

void loop()
{
    // =========================================================================
    // ESP-NOW -> PC
    // =========================================================================

    RadioMessage message;

    while (
        xQueueReceive(
            rx_queue,
            &message,
            0) == pdTRUE)
    {

        Serial.write(
            message.data,
            message.length);

        Serial.write('\n');
    }

    // =========================================================================
    // PC -> ESP-NOW
    // =========================================================================

    while (Serial.available())
    {

        char c = Serial.read();

        // Ignore Windows carriage return.

        if (c == '\r')
            continue;

        // ---------------------------------------------------------------------
        // Complete newline-terminated message received
        // ---------------------------------------------------------------------

        if (c == '\n')
        {

            if (serial_length > 0)
            {

                esp_now_send(
                    broadcast_mac,
                    reinterpret_cast<uint8_t *>(serial_buffer),
                    serial_length);
            }

            serial_length = 0;

            continue;
        }

        // ---------------------------------------------------------------------
        // Accumulate serial message
        // ---------------------------------------------------------------------

        if (serial_length < MAX_MESSAGE_LENGTH)
        {

            serial_buffer[serial_length] = c;
            serial_length++;
        }

        else
        {

            // Line exceeded maximum ESP-NOW message size.
            // Drop it.

            serial_length = 0;
        }
    }
}
