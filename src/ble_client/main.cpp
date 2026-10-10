#include <Arduino.h>
#include <BLEAdvertisedDevice.h>
#include <BLEClient.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLERemoteCharacteristic.h>
#include <BLERemoteService.h>
#include <BLEUtils.h>
#include <inttypes.h>
#include <stdio.h>
#include <esp_random.h>
#include <string.h>

namespace
{
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char WORLD_CHARACTERISTIC_UUID[] = "7a1e0003-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr uint32_t WORLD_INTERVAL_MS = 20; // 50 Hz.
constexpr size_t WORLD_SIZE = 20;
constexpr uint32_t REPORT_INTERVAL_MS = 5000;
constexpr uint32_t SCAN_DURATION_SECONDS = 3;

BLEClient *client = nullptr;
portMUX_TYPE statsMux = portMUX_INITIALIZER_UNLOCKED;
uint16_t lastSequence = 0;
uint16_t latestWorldSequence = 0;
uint32_t lastReceiveTimeMs = 0;
uint32_t windowReceived = 0;
uint32_t windowLost = 0;
uint32_t windowMaxGapMs = 0;
bool haveLastSequence = false;
bool haveLastReceiveTime = false;
uint16_t latestAttemptedWorldSequence = 0;
bool haveWorldAttempt = false;
bool haveWorldEcho = false;
uint32_t windowRttSamples = 0;
uint64_t windowRttSumMs = 0;
uint32_t windowRttMaxMs = 0;

void onPacket(BLERemoteCharacteristic *, uint8_t *data, size_t length, bool)
{
    if (length != sizeof(uint32_t))
        return;
    const uint16_t sequence = static_cast<uint16_t>(data[0]) |
        (static_cast<uint16_t>(data[1]) << 8);
    const uint16_t worldSequence = static_cast<uint16_t>(data[2]) |
        (static_cast<uint16_t>(data[3]) << 8);
    const uint32_t now = millis();
    portENTER_CRITICAL(&statsMux);
    if (haveLastSequence)
    {
        const uint16_t delta = static_cast<uint16_t>(sequence - lastSequence);
        if (delta == 0 || delta >= 0x8000U)
        {
            portEXIT_CRITICAL(&statsMux);
            return;
        }
        windowLost += delta - 1;
    }
    lastSequence = sequence;
    latestWorldSequence = worldSequence;
    haveLastSequence = true;
    ++windowReceived;
    // Zero is also the server's initial value before receiving any world state.
    // Wait for a nonzero echo once per connection before estimating RTT.
    if (worldSequence != 0)
        haveWorldEcho = true;
    if (haveWorldAttempt && haveWorldEcho)
    {
        const uint16_t lag = static_cast<uint16_t>(
            latestAttemptedWorldSequence - worldSequence);
        if (lag < 0x8000U)
        {
            const uint32_t rttMs = static_cast<uint32_t>(lag) * WORLD_INTERVAL_MS;
            ++windowRttSamples;
            windowRttSumMs += rttMs;
            if (rttMs > windowRttMaxMs)
                windowRttMaxMs = rttMs;
        }
    }
    if (haveLastReceiveTime)
    {
        const uint32_t gapMs = now - lastReceiveTimeMs;
        if (gapMs > windowMaxGapMs)
            windowMaxGapMs = gapMs;
    }
    lastReceiveTimeMs = now;
    haveLastReceiveTime = true;
    portEXIT_CRITICAL(&statsMux);
}

void reportStats(uint32_t elapsedMs)
{
    portENTER_CRITICAL(&statsMux);
    const uint32_t now = millis();
    const uint32_t received = windowReceived;
    const uint32_t lost = windowLost;
    const uint32_t maxGapMs = windowMaxGapMs;
    const uint16_t worldSequence = latestWorldSequence;
    const bool havePacket = haveLastReceiveTime;
    const uint32_t silenceMs = havePacket ? now - lastReceiveTimeMs : 0;
    const uint32_t rttSamples = windowRttSamples;
    const uint64_t rttSumMs = windowRttSumMs;
    const uint32_t rttMaxMs = windowRttMaxMs;
    windowReceived = 0;
    windowLost = 0;
    windowMaxGapMs = 0;
    windowRttSamples = 0;
    windowRttSumMs = 0;
    windowRttMaxMs = 0;
    portEXIT_CRITICAL(&statsMux);
    const uint32_t total = received + lost;
    const double lossPercent = total == 0 ? 0.0 : (100.0 * lost / total);
    char rttAverage[24] = "NA";
    char rttMaximum[24] = "NA";
    if (rttSamples > 0)
    {
        snprintf(rttAverage, sizeof(rttAverage), "%.1f",
                 static_cast<double>(rttSumMs) / rttSamples);
        snprintf(rttMaximum, sizeof(rttMaximum), "%" PRIu32, rttMaxMs);
    }
    char report[384];
    snprintf(report, sizeof(report),
        "BLE_STATS window_s=%.3f received=%" PRIu32 " seq_lost=%" PRIu32
        " loss_percent=%.2f rate_hz=%.2f max_gap_s=%.3f silence_s=%.3f"
        " have_packet=%u world_seq=%u rtt_avg_ms=%s rtt_max_ms=%s rtt_samples=%" PRIu32 "\r\n",
        elapsedMs / 1000.0, received, lost, lossPercent,
        elapsedMs == 0 ? 0.0 : received * 1000.0 / elapsedMs,
        maxGapMs / 1000.0, silenceMs / 1000.0, static_cast<unsigned>(havePacket),
        static_cast<unsigned>(worldSequence), rttAverage, rttMaximum, rttSamples);
    if (Serial.availableForWrite() >= static_cast<int>(strlen(report)))
        Serial.print(report);
}

bool findAndConnect(BLEClient *client, BLERemoteCharacteristic **packetCharacteristic,
                    BLERemoteCharacteristic **worldCharacteristic)
{
    BLEScan *scanner = BLEDevice::getScan();
    scanner->setActiveScan(true);
    scanner->setInterval(100);
    scanner->setWindow(80);

    BLEScanResults *results = scanner->start(SCAN_DURATION_SECONDS, false);
    if (results == nullptr)
    {
        return false;
    }

    bool connected = false;
    for (int i = 0; i < results->getCount(); ++i)
    {
        BLEAdvertisedDevice device = results->getDevice(i);
        if (!device.haveServiceUUID() ||
            !device.isAdvertisingService(BLEUUID(SERVICE_UUID)))
            continue;

        scanner->stop();
        connected = client->connect(&device);
        break;
    }
    scanner->clearResults();

    if (!connected)
    {
        return false;
    }

    BLERemoteService *service = client->getService(SERVICE_UUID);
    if (service == nullptr)
    {
        client->disconnect();
        return false;
    }

    *packetCharacteristic = service->getCharacteristic(PACKET_CHARACTERISTIC_UUID);
    if (*packetCharacteristic == nullptr ||
        !(*packetCharacteristic)->canNotify())
    {
        client->disconnect();
        return false;
    }

    *worldCharacteristic = service->getCharacteristic(WORLD_CHARACTERISTIC_UUID);
    if (*worldCharacteristic == nullptr || !(*worldCharacteristic)->canWriteNoResponse())
    {
        client->disconnect();
        return false;
    }

    portENTER_CRITICAL(&statsMux);
    haveLastSequence = false;
    haveLastReceiveTime = false;
    windowReceived = 0;
    windowLost = 0;
    windowMaxGapMs = 0;
    latestWorldSequence = 0;
    haveWorldAttempt = false;
    haveWorldEcho = false;
    latestAttemptedWorldSequence = 0;
    windowRttSamples = 0;
    windowRttSumMs = 0;
    windowRttMaxMs = 0;
    portEXIT_CRITICAL(&statsMux);
#if defined(CONFIG_NIMBLE_ENABLED)
    if (!(*packetCharacteristic)->subscribe(true, onPacket, true))
    {
        client->disconnect();
        return false;
    }
#else
    BLERemoteDescriptor *configuration =
        (*packetCharacteristic)->getDescriptor(BLEUUID(static_cast<uint16_t>(0x2902)));
    if (configuration == nullptr)
    {
        client->disconnect();
        return false;
    }
    // Register the callback, then enable the CCCD with a checked GATT write.
    (*packetCharacteristic)->registerForNotify(onPacket, true, false);
    uint8_t enableNotifications[] = {0x01, 0x00};
    if (!configuration->writeValue(enableNotifications, sizeof(enableNotifications), true))
    {
        client->disconnect();
        return false;
    }
#endif
    if (!client->isConnected())
        return false;
    return true;
}
}

void setup()
{
    Serial.setTxBufferSize(512); // Room for one complete BLE_STATS line.
    Serial.begin(115200);
    Serial.setTxTimeoutMs(1);
    BLEDevice::init("ble_client");
    client = BLEDevice::createClient();
}

void loop()
{
    BLERemoteCharacteristic *packetCharacteristic = nullptr;
    BLERemoteCharacteristic *worldCharacteristic = nullptr;

    if (!findAndConnect(client, &packetCharacteristic, &worldCharacteristic))
    {
        delay(1000);
        return;
    }

    uint32_t lastReportMs = millis();
    uint32_t lastWorldSendMs = millis();
    uint16_t worldSequence = 0;

    while (client->isConnected())
    {
        const uint32_t nowMs = millis();
        if (nowMs - lastWorldSendMs >= WORLD_INTERVAL_MS)
        {
            lastWorldSendMs = nowMs;
            uint8_t world[WORLD_SIZE];
            world[0] = static_cast<uint8_t>(worldSequence);
            world[1] = static_cast<uint8_t>(worldSequence >> 8);
            esp_fill_random(world + 2, sizeof(world) - 2);
            portENTER_CRITICAL(&statsMux);
            latestAttemptedWorldSequence = worldSequence;
            haveWorldAttempt = true;
            portEXIT_CRITICAL(&statsMux);
            ++worldSequence; // Advance on every attempt, including local failures.
            worldCharacteristic->writeValue(world, sizeof(world), false);
        }
        if (nowMs - lastReportMs >= REPORT_INTERVAL_MS)
        {
            const uint32_t elapsedMs = nowMs - lastReportMs;
            lastReportMs = nowMs;
            reportStats(elapsedMs);
        }

        delay(1);
    }

    delay(500);
}
