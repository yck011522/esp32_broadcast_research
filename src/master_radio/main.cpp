/*
===============================================================================
master_radio/main.cpp
===============================================================================

PURPOSE
-------
Schema-agnostic ESP-NOW <-> USB serial bridge.

The master knows nothing about the contents of messages.

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


WHY THE QUEUE?
--------------
Do NOT perform USB Serial.write() directly inside the ESP-NOW receive callback.

The callback should return quickly.

Instead:

    ESP-NOW callback
          |
          | copy packet
          v
       QUEUE
          |
          | loop()
          v
    USB SERIAL


MESSAGE SIZE
------------
Maximum raw message length:

    250 bytes

One serial line corresponds to exactly one ESP-NOW packet.

===============================================================================
*/

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>

// =============================================================================
// CONFIGURATION
// =============================================================================

const size_t MAX_MESSAGE_LENGTH = 250;

// Number of received ESP-NOW packets that can wait for USB forwarding.
//
// 32 packets is far more than necessary for normal 20 Hz telemetry,
// while still using very little memory.

const size_t RX_QUEUE_LENGTH = 32;

// =============================================================================
// MESSAGE TYPE USED INTERNALLY BY MASTER
// =============================================================================
// This is NOT an application protocol.
//
// It only lets the master temporarily store one arbitrary ESP-NOW packet.

struct RadioMessage
{
    uint16_t length;
    uint8_t data[MAX_MESSAGE_LENGTH];
};

// =============================================================================
// STATE
// =============================================================================

// ESP-NOW broadcast MAC address.

uint8_t broadcast_mac[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

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
    // For this initial test we simply drop it rather than blocking
    // the Wi-Fi callback.

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

    peer.channel = 0;
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
    //
    // Drain all received radio packets from the queue.
    //
    // USB serial operations happen here rather than inside the ESP-NOW callback.

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