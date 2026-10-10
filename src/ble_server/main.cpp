#include <Arduino.h>
#include <BLEDevice.h>
#if defined(CONFIG_BLUEDROID_ENABLED)
#include <BLE2902.h>
#endif
#include <BLEServer.h>
#include <BLEUtils.h>
#include <atomic>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

namespace
{
constexpr char DEVICE_NAME[] = "ble_server";
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char WORLD_CHARACTERISTIC_UUID[] = "7a1e0003-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr size_t WORLD_SIZE = 20;
constexpr uint32_t PACKET_INTERVAL_MS = 50; // Car telemetry: 20 Hz.
constexpr uint32_t REPORT_INTERVAL_MS = 5000;
std::atomic<bool> connected{false};
BLECharacteristic *packetCharacteristic = nullptr;
portMUX_TYPE worldMux = portMUX_INITIALIZER_UNLOCKED;
uint16_t latestWorldSequence = 0;
bool haveWorld = false;
uint32_t lastWorldMs = 0;
uint32_t worldReceived = 0;
uint32_t worldLost = 0;
uint32_t worldWindowStartMs = 0;

class WorldCallbacks : public BLECharacteristicCallbacks
{
    void onWrite(BLECharacteristic *characteristic) override
    {
        // Reject malformed commands; they do not affect telemetry or statistics.
        if (characteristic->getLength() != WORLD_SIZE)
            return;
        const uint8_t *data = characteristic->getData();
        const uint16_t sequence = static_cast<uint16_t>(data[0]) |
            (static_cast<uint16_t>(data[1]) << 8);
        const uint32_t now = millis();
        portENTER_CRITICAL(&worldMux);
        if (haveWorld)
        {
            const uint16_t delta = static_cast<uint16_t>(sequence - latestWorldSequence);
            if (delta == 0 || delta >= 0x8000U)
            {
                portEXIT_CRITICAL(&worldMux);
                return;
            }
            worldLost += delta - 1;
        }
        latestWorldSequence = sequence;
        haveWorld = true;
        lastWorldMs = now;
        ++worldReceived;
        portEXIT_CRITICAL(&worldMux);
    }
};
#if defined(CONFIG_BLUEDROID_ENABLED)
BLE2902 *notificationConfiguration = nullptr;
#else
std::atomic<bool> subscribed{false};
class PacketCallbacks : public BLECharacteristicCallbacks
{
    void onSubscribe(BLECharacteristic *, ble_gap_conn_desc *, uint16_t value) override
    {
        subscribed.store((value & 0x01) != 0);
    }
};
#endif

bool notificationsEnabled()
{
#if defined(CONFIG_BLUEDROID_ENABLED)
    return notificationConfiguration->getNotifications();
#else
    return subscribed.load();
#endif
}

class ServerCallbacks : public BLEServerCallbacks
{
    void onConnect(BLEServer *) override
    {
        portENTER_CRITICAL(&worldMux);
        haveWorld = false;
        latestWorldSequence = 0;
        worldReceived = 0;
        worldLost = 0;
        worldWindowStartMs = millis();
        lastWorldMs = worldWindowStartMs;
        portEXIT_CRITICAL(&worldMux);
        connected.store(true);
    }

    void onDisconnect(BLEServer *server) override
    {
        connected.store(false);
#if defined(CONFIG_BLUEDROID_ENABLED)
        notificationConfiguration->setNotifications(false);
#else
        subscribed.store(false);
#endif
        server->startAdvertising();
    }
};
}

void setup()
{
    Serial.begin(115200);
    // USB diagnostics must not pause car telemetry when no monitor is reading.
    Serial.setTxTimeoutMs(1);
    BLEDevice::init(DEVICE_NAME);
    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());
    BLEService *service = server->createService(SERVICE_UUID);
    BLECharacteristic *worldCharacteristic = service->createCharacteristic(
        WORLD_CHARACTERISTIC_UUID, BLECharacteristic::PROPERTY_WRITE_NR);
    uint8_t initialWorld[WORLD_SIZE] = {};
    worldCharacteristic->setValue(initialWorld, sizeof(initialWorld));
    worldCharacteristic->setCallbacks(new WorldCallbacks());
    packetCharacteristic = service->createCharacteristic(
        PACKET_CHARACTERISTIC_UUID, BLECharacteristic::PROPERTY_NOTIFY);
#if defined(CONFIG_BLUEDROID_ENABLED)
    notificationConfiguration = new BLE2902();
    packetCharacteristic->addDescriptor(notificationConfiguration);
#else
    // NimBLE creates the CCCD automatically for notification characteristics.
    packetCharacteristic->setCallbacks(new PacketCallbacks());
#endif
    service->start();
    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->start();
    worldWindowStartMs = millis();
    lastWorldMs = worldWindowStartMs;
    Serial.println("Car BLE server ready; waiting for notification subscription (20 Hz)");
}

void loop()
{
    static uint16_t sequence = 0;
    static uint32_t lastSendMs = millis();
    const uint32_t now = millis();

    if (!connected.load() || !notificationsEnabled())
    {
        lastSendMs = now;
    }
    else if (now - lastSendMs >= PACKET_INTERVAL_MS)
    {
        // Avoid catch-up bursts if BLE scheduling stalls the loop.
        lastSendMs = now;
        portENTER_CRITICAL(&worldMux);
        const uint16_t worldSequence = latestWorldSequence;
        portEXIT_CRITICAL(&worldMux);
        uint8_t packet[] = {
            static_cast<uint8_t>(sequence),
            static_cast<uint8_t>(sequence >> 8),
            static_cast<uint8_t>(worldSequence),
            static_cast<uint8_t>(worldSequence >> 8),
        };
        ++sequence;
        packetCharacteristic->setValue(packet, sizeof(packet));
        packetCharacteristic->notify();
    }

    portENTER_CRITICAL(&worldMux);
    const uint32_t reportNow = millis();
    const uint32_t elapsedMs = reportNow - worldWindowStartMs;
    if (elapsedMs >= REPORT_INTERVAL_MS)
    {
        const uint32_t received = worldReceived;
        const uint32_t lost = worldLost;
        const uint32_t silenceMs = reportNow - lastWorldMs;
        worldReceived = 0;
        worldLost = 0;
        // Before the first world packet, silence is time since session start.
        worldWindowStartMs = reportNow;
        portEXIT_CRITICAL(&worldMux);
        char report[192];
        snprintf(report, sizeof(report),
                 "world stats: rate=%.2f Hz seq_lost=%" PRIu32 " silence=%.3f s\r\n",
                 received * 1000.0 / elapsedMs, lost, silenceMs / 1000.0);
        const size_t reportLength = strlen(report);
        if (Serial.availableForWrite() >= static_cast<int>(reportLength))
            Serial.write(reinterpret_cast<const uint8_t *>(report), reportLength);
    }
    else
        portEXIT_CRITICAL(&worldMux);
    delay(1);
}
