#include <Arduino.h>
#include <atomic>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#if !defined(BLE_DEVICE_ID)
#define BLE_DEVICE_ID 1
#endif

static_assert(BLE_DEVICE_ID >= 0 && BLE_DEVICE_ID <= 255,
              "BLE_DEVICE_ID must fit in one byte");

namespace
{
constexpr uint32_t PACKET_INTERVAL_MS = 100;
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";

std::atomic<bool> deviceConnected{false};
std::atomic<bool> advertisingRestartNeeded{false};

BLECharacteristic *packetCharacteristic = nullptr;
uint32_t packetSequence = 0;
uint32_t lastPacketTime = 0;

class ServerCallbacks : public BLEServerCallbacks
{
    void onConnect(BLEServer *) override
    {
        deviceConnected.store(true);
    }

    void onDisconnect(BLEServer *) override
    {
        deviceConnected.store(false);
        advertisingRestartNeeded.store(true);
    }
};
}

void setup()
{
    Serial.begin(115200);

    const String deviceName = "ble_slave_" + String(BLE_DEVICE_ID);
    BLEDevice::init(deviceName.c_str());

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService *service = server->createService(SERVICE_UUID);
    packetCharacteristic = service->createCharacteristic(
        PACKET_CHARACTERISTIC_UUID,
        BLECharacteristic::PROPERTY_NOTIFY);
    packetCharacteristic->addDescriptor(new BLE2902());
    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->start();

    Serial.println("BLE packet server ready");
    Serial.print("Device name: ");
    Serial.println(deviceName);
    Serial.println("Advertising; connect from the computer and enable notifications.");
}

void loop()
{
    const bool connected = deviceConnected.load();
    if (advertisingRestartNeeded.exchange(false) && !connected)
    {
        BLEDevice::startAdvertising();
        Serial.println("Client disconnected; advertising restarted");
    }

    const uint32_t now = millis();
    static bool wasConnected = false;
    if (connected && !wasConnected)
        lastPacketTime = now;

    wasConnected = connected;

    if (connected && now - lastPacketTime >= PACKET_INTERVAL_MS)
    {
        lastPacketTime = now;

        const uint32_t sequence = packetSequence++;
        const uint8_t packet[] = {
            static_cast<uint8_t>(BLE_DEVICE_ID),
            static_cast<uint8_t>(sequence),
            static_cast<uint8_t>(sequence >> 8),
            static_cast<uint8_t>(sequence >> 16),
            static_cast<uint8_t>(sequence >> 24),
        };

        packetCharacteristic->setValue(packet, sizeof(packet));
        packetCharacteristic->notify();
    }

    delay(1);
}
