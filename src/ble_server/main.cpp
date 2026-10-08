#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <atomic>
#include <inttypes.h>
#include <stdio.h>

namespace
{
constexpr char DEVICE_NAME[] = "ble_server";
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr uint32_t REPORT_INTERVAL_MS = 5000;

portMUX_TYPE statsMux = portMUX_INITIALIZER_UNLOCKED;
uint32_t lastSequence = 0;
uint32_t lastReceiveTimeMs = 0;
uint32_t windowReceived = 0;
uint32_t windowLost = 0;
uint32_t windowMaxGapMs = 0;
bool haveLastSequence = false;
bool haveLastReceiveTime = false;
std::atomic<bool> connectionChanged{false};
BLECharacteristic *packetCharacteristic = nullptr;

class ServerCallbacks : public BLEServerCallbacks
{
    void onConnect(BLEServer *) override
    {
        connectionChanged.store(true);
    }

    void onDisconnect(BLEServer *server) override
    {
        connectionChanged.store(true);
        server->startAdvertising();
    }
};

class PacketCallbacks : public BLECharacteristicCallbacks
{
    void onWrite(BLECharacteristic *characteristic) override
    {
        if (characteristic->getLength() != sizeof(uint32_t))
            return;

        const uint8_t *data = characteristic->getData();
        const uint32_t sequence =
            static_cast<uint32_t>(data[0]) |
            (static_cast<uint32_t>(data[1]) << 8) |
            (static_cast<uint32_t>(data[2]) << 16) |
            (static_cast<uint32_t>(data[3]) << 24);
        const uint32_t now = millis();

        portENTER_CRITICAL(&statsMux);
        if (haveLastSequence)
        {
            const uint32_t delta = sequence - lastSequence;
            if (delta == 0 || delta >= 0x80000000UL)
            {
                portEXIT_CRITICAL(&statsMux);
                return;
            }
            windowLost += delta - 1;
        }

        lastSequence = sequence;
        haveLastSequence = true;
        ++windowReceived;

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
};
}

void setup()
{
    Serial.begin(115200);
    BLEDevice::init(DEVICE_NAME);

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService *service = server->createService(SERVICE_UUID);
    packetCharacteristic = service->createCharacteristic(
        PACKET_CHARACTERISTIC_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    packetCharacteristic->setCallbacks(new PacketCallbacks());
    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->start();

    Serial.println("BLE server ready; waiting for ble_client");
}

void loop()
{
    static uint32_t lastReportMs = millis();

    if (connectionChanged.exchange(false))
    {
        portENTER_CRITICAL(&statsMux);
        haveLastSequence = false;
        haveLastReceiveTime = false;
        portEXIT_CRITICAL(&statsMux);
        Serial.println("BLE connection state changed");
    }

    const uint32_t now = millis();
    if (now - lastReportMs >= REPORT_INTERVAL_MS)
    {
        lastReportMs += REPORT_INTERVAL_MS;

        uint32_t received;
        uint32_t lost;
        uint32_t maxGapMs;
        portENTER_CRITICAL(&statsMux);
        received = windowReceived;
        lost = windowLost;
        maxGapMs = windowMaxGapMs;
        windowReceived = 0;
        windowLost = 0;
        windowMaxGapMs = 0;
        portEXIT_CRITICAL(&statsMux);

        const uint32_t total = received + lost;
        const double lossPercent =
            total == 0 ? 0.0 : (100.0 * static_cast<double>(lost) / total);
        char report[128];
        snprintf(
            report,
            sizeof(report),
            "5s stats: received=%" PRIu32 " lost=%" PRIu32
            " loss=%.2f%% max_gap=%.3f s",
            received,
            lost,
            lossPercent,
            maxGapMs / 1000.0);
        Serial.println(report);
    }

    delay(1);
}
