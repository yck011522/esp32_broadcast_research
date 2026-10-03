/*
===============================================================================
master_radio_ota/main.cpp
===============================================================================

PURPOSE
-------
Schema-agnostic ESP-NOW <-> USB serial bridge.

Identical to master_radio, except that it is using unicast.


PC -> MASTER -> SLAVES
----------------------
PC sends one newline-terminated serial message:

    W,1234\n

Master:
    1. Reads bytes until '\n'
    2. Removes the newline
    3. Sends the raw message to each slave over ESP-NOW
        - If a new message arrives from PC before the previous TX
          completes, the new message will wait in a latest_message buffer.
        - If the previous TX failed, there will be no retry. The next TX will send the latest_message buffer.
        - After a few consecutive Tx Failure,
          the master will enter a back-off state where it will not send any more messages for a specified period.


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

// Slave mac address x 4

uint8_t slave_macs[4][6] = {
    {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x4C},
    {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x18},
    {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x1A},
    {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x1B}};

// Serial input buffer for PC -> ESP-NOW.
// Holds only the latest message.
char latest_tx_message_buffer[MAX_MESSAGE_LENGTH];
size_t latest_tx_buffer_length = 0;
char incoming_tx_message_buffer[MAX_MESSAGE_LENGTH];
// Char counter for storing bytes from PC until newline is received.
size_t incoming_buffer_length = 0;

bool new_message_to_send[4] = {false, false, false, false};

// Queue for ESP-NOW -> PC traffic.
QueueHandle_t rx_queue;

// =============================================================================
// ESP-NOW RECEIVE CALLBACK
// =============================================================================
//
// IMPORTANT:
// Do not perform Serial.write() here.
// Just copy the packet into the queue and return as quickly as possible.

void onReceive(
    const esp_now_recv_info_t *info,
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
    // Register four slave peers
    // -------------------------------------------------------------------------

    for (int i = 0; i < 4; i++)
    {
        esp_now_peer_info_t peerInfo = {};

        memcpy(
            peerInfo.peer_addr,
            slave_macs[i],
            6);

        peerInfo.channel = ESP_NOW_CHANNEL;
        peerInfo.encrypt = false;
        if (esp_now_add_peer(&peerInfo) != ESP_OK)
        {
            Serial.print("Failed to add peer");
            Serial.println(i);
        }
    }
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

            if (incoming_buffer_length > 0)
            {

                // Copy the incoming message into the latest message buffer for sending to slaves.
                memcpy(
                    latest_tx_message_buffer,
                    incoming_tx_message_buffer,
                    incoming_buffer_length);
                latest_tx_buffer_length = incoming_buffer_length;
                // Mark flags for all four slaves to send the latest message.
                new_message_to_send[0] = true;
                new_message_to_send[1] = true;
                new_message_to_send[2] = true;
                new_message_to_send[3] = true;
            }

            incoming_buffer_length = 0;

            continue;
        }

        // ---------------------------------------------------------------------
        // Accumulate serial message
        // ---------------------------------------------------------------------

        if (incoming_buffer_length < MAX_MESSAGE_LENGTH)
        {

            incoming_tx_message_buffer[incoming_buffer_length] = c;
            incoming_buffer_length++;
        }

        else
        {

            // Line exceeded maximum ESP-NOW message size.
            // Truncate the message by discarding the rest of the line.

            // incoming_buffer_length = 0;
        }
    }
}
