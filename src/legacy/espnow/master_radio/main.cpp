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
        - After a failed TX, that slave backs off before trying the latest message.
          If no newer message arrived, the retained message is tried again.
        - Backoff applies separately to each slave; other slaves remain eligible.


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

// Per-slave waiting time after consecutive transmission failures.
//
// First failure: 100 ms. Second failure: 250 ms.
// Third and further failures: 1000 ms.
// A successful transmission returns that slave to normal operation.

const uint32_t TX_BACKOFF_MS[] = {100, 250, 1000};

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
    // {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x1A},
    // {0x68, 0xEE, 0x8F, 0x4B, 0x5B, 0x1B}
};

// Derive the number of destinations from the MAC address array.

const size_t SLAVE_COUNT = sizeof(slave_macs) / sizeof(slave_macs[0]);

// Serial input buffer for PC -> ESP-NOW.
// Holds only the latest message.
char latest_tx_message_buffer[MAX_MESSAGE_LENGTH];
size_t latest_tx_buffer_length = 0;
char incoming_tx_message_buffer[MAX_MESSAGE_LENGTH];
// Char counter for storing bytes from PC until newline is received.
size_t incoming_buffer_length = 0;

// True when a slave still needs the latest computer message.
// Clear its flag when submitting a transmission, NOT when it completes.
// This allows a newer computer message to set the flag again during TX.

bool new_message_to_send[4] = {false, false, false, false};

// Independent backoff state for each slave.
// failure_stage selects a delay from TX_BACKOFF_MS.
// next_attempt_ms stores the millis() deadline for that slave's cooldown.

uint8_t failure_stage[SLAVE_COUNT] = {};
uint32_t next_attempt_ms[SLAVE_COUNT] = {};
bool cooling_down[SLAVE_COUNT] = {};

// Round-robin starting position for the next search for an eligible slave.

size_t next_slave = 0;

// Only one ESP-NOW transmission may be outstanding at a time.
// Keep its destination until its callback is processed.

size_t active_slave = 0;
bool tx_busy = false;

// Once a serial line exceeds the buffer size, ignore it until the newline.

bool discard_incoming_line = false;

// Send callback -> loop() completion queue.
// One outstanding send requires only one completion slot.
// Only loop() changes the transmission flags and backoff state.

QueueHandle_t tx_result_queue;

// Queue for ESP-NOW -> PC traffic.
QueueHandle_t rx_queue;

// =============================================================================
// ESP-NOW SEND CALLBACK
// =============================================================================
//
// IMPORTANT:
// This callback runs in the Wi-Fi task.
// Do not print, wait, or submit the next transmission here.
// Just copy the result into the queue and return as quickly as possible.
//
// The destination is already stored in active_slave, so info is not needed.
// Success confirms MAC-layer delivery, not application processing by the slave.

void onSend(const esp_now_send_info_t *info, esp_now_send_status_t status)
{
    // Non-blocking queue insertion. loop() handles this result on its next pass.

    xQueueSend(tx_result_queue, &status, 0);
}

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
    // Create ESP-NOW receive and send-completion queues
    // -------------------------------------------------------------------------

    rx_queue = xQueueCreate(
        RX_QUEUE_LENGTH,
        sizeof(RadioMessage));

    // Create the completion queue before registering the send callback.

    tx_result_queue = xQueueCreate(1, sizeof(esp_now_send_status_t));

    if (rx_queue == nullptr || tx_result_queue == nullptr)
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
    // Without a send callback, the scheduler cannot know when the radio is free.

    if (esp_now_register_send_cb(onSend) != ESP_OK)
    {
        while (true)
            delay(1000);
    }

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
    // PROCESS THE PREVIOUS TRANSMISSION RESULT
    // =========================================================================

    esp_now_send_status_t result;
    if (xQueueReceive(tx_result_queue, &result, 0) == pdTRUE)
    {
        if (result == ESP_NOW_SEND_SUCCESS)
        {
            // Restore normal service for this slave after a successful TX.

            failure_stage[active_slave] = 0;
            cooling_down[active_slave] = false;
            // Do not clear pending here: a newer message may have arrived.
        }
        else
        {
            // Restore the pending flag so this slave can try again after backoff.
            // The next attempt uses the latest buffer contents.

            new_message_to_send[active_slave] = true;
            // Start this slave's cooldown without pausing the other slaves.
            // Advance the delay for its next failure, capped at the last entry.

            next_attempt_ms[active_slave] = millis() + TX_BACKOFF_MS[failure_stage[active_slave]];
            cooling_down[active_slave] = true;
            if (failure_stage[active_slave] + 1 < sizeof(TX_BACKOFF_MS) / sizeof(TX_BACKOFF_MS[0]))
                failure_stage[active_slave]++;
        }
        // The previous send is complete. Scheduling below may submit another.

        tx_busy = false;
    }
    // =========================================================================
    // ESP-NOW -> PC
    // =========================================================================

    RadioMessage message;

    // Limit forwarding work per pass so incoming telemetry cannot keep loop()
    // from reaching the computer input and transmission scheduler.

    for (size_t forwarded = 0; forwarded < RX_QUEUE_LENGTH &&
                               xQueueReceive(
                                   rx_queue,
                                   &message,
                                   0) == pdTRUE;
         ++forwarded)
    {

        Serial.write(
            message.data,
            message.length);

        Serial.write('\n');
    }

    // =========================================================================
    // PC -> ESP-NOW
    // =========================================================================

    // Read only the bytes available at the start of this pass.
    // Newly arriving bytes wait for the next pass, giving TX scheduling a turn.

    int available_bytes = Serial.available();
    while (available_bytes-- > 0)
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

            if (incoming_buffer_length > 0 && !discard_incoming_line)
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
            discard_incoming_line = false;

            continue;
        }

        // ---------------------------------------------------------------------
        // Accumulate serial message
        // ---------------------------------------------------------------------

        if (discard_incoming_line)
            continue;

        if (incoming_buffer_length < MAX_MESSAGE_LENGTH)
        {

            incoming_tx_message_buffer[incoming_buffer_length] = c;
            incoming_buffer_length++;
        }

        else
        {

            // Line exceeded maximum ESP-NOW message size.
            // Drop the entire oversized line, never forward a truncated command.
            discard_incoming_line = true;
        }
    }

    // =========================================================================
    // SEND THE LATEST MESSAGE TO THE NEXT ELIGIBLE SLAVE
    // =========================================================================
    //
    // Submit at most one transmission per pass.
    // While waiting for its callback, subsequent passes still process USB input
    // and telemetry, but do not submit another radio transmission.

    if (!tx_busy)
    {
        const uint32_t now = millis();
        for (size_t checked = 0; checked < SLAVE_COUNT; ++checked)
        {
            // Continue from the previous selection rather than always starting
            // with slave 1. A busy input stream must not starve the later slaves.

            const size_t slave = next_slave;
            next_slave = (next_slave + 1) % SLAVE_COUNT;
            if (!new_message_to_send[slave])
                continue;
            // Skip this destination until its cooldown expires.
            // Signed subtraction handles millis() rollover for these short waits.

            if (cooling_down[slave] && static_cast<int32_t>(now - next_attempt_ms[slave]) < 0)
                continue;

            // Record the active destination before submitting the send.

            cooling_down[slave] = false;
            active_slave = slave;
            tx_busy = true;
            // Clear only the flag for the message being submitted.
            // New computer input may set it again while the radio is working.

            new_message_to_send[slave] = false;

            // ESP-NOW copies the payload before returning; the latest buffer
            // can therefore be replaced while awaiting the send callback.
            esp_err_t error = esp_now_send(
                slave_macs[slave],
                reinterpret_cast<const uint8_t *>(latest_tx_message_buffer),
                latest_tx_buffer_length);
            if (error != ESP_OK)
            {
                // Submission failed: no completion is expected. Do not classify
                // a local driver/resource error as a failed radio delivery.
                // Restore pending state and use the shortest cooldown without
                // increasing the slave's consecutive-failure stage.
                tx_busy = false;
                new_message_to_send[slave] = true;
                cooling_down[slave] = true;
                next_attempt_ms[slave] = now + TX_BACKOFF_MS[0];
            }
            // Give loop() another pass before selecting the next destination.

            break;
        }
    }
}
