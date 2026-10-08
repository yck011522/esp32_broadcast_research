#include <Arduino.h>
#include <BLEAdvertisedDevice.h>
#include <BLEClient.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLERemoteCharacteristic.h>
#include <BLERemoteService.h>
#include <BLEUtils.h>
#include <inttypes.h>

namespace
{
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr uint32_t PACKET_INTERVAL_US = 10000;
constexpr uint32_t SCAN_DURATION_SECONDS = 3;

BLEClient *client = nullptr;

bool findAndConnect(BLEClient *client, BLERemoteCharacteristic **packetCharacteristic)
{
    BLEScan *scanner = BLEDevice::getScan();
    scanner->setActiveScan(true);
    scanner->setInterval(100);
    scanner->setWindow(80);

    Serial.println("Scanning for ble_server");
    BLEScanResults *results = scanner->start(SCAN_DURATION_SECONDS, false);
    if (results == nullptr)
    {
        Serial.println("BLE scan failed");
        return false;
    }

    bool connected = false;
    for (int i = 0; i < results->getCount(); ++i)
    {
        BLEAdvertisedDevice device = results->getDevice(i);
        if (!device.haveServiceUUID() ||
            !device.isAdvertisingService(BLEUUID(SERVICE_UUID)))
            continue;

        Serial.print("Found ");
        Serial.println(SERVICE_UUID);
        scanner->stop();
        connected = client->connect(&device);
        break;
    }
    scanner->clearResults();

    if (!connected)
    {
        Serial.println("Server not found or connection failed; retrying scan");
        return false;
    }

    BLERemoteService *service = client->getService(SERVICE_UUID);
    if (service == nullptr)
    {
        Serial.println("Required BLE service not found");
        client->disconnect();
        return false;
    }

    *packetCharacteristic = service->getCharacteristic(PACKET_CHARACTERISTIC_UUID);
    if (*packetCharacteristic == nullptr ||
        !(*packetCharacteristic)->canWriteNoResponse())
    {
        Serial.println("Required write-without-response characteristic not found");
        client->disconnect();
        return false;
    }

    Serial.println("Connected; sending sequence packets at 100 Hz");
    return true;
}
}

void setup()
{
    Serial.begin(115200);
    BLEDevice::init("ble_client");
    client = BLEDevice::createClient();
}

void loop()
{
    BLERemoteCharacteristic *packetCharacteristic = nullptr;

    if (!findAndConnect(client, &packetCharacteristic))
    {
        delay(1000);
        return;
    }

    uint32_t sequence = 0;
    uint32_t lastSendUs = micros();
    uint32_t attempted = 0;
    uint32_t writeFailures = 0;
    uint32_t lastReportMs = millis();

    while (client->isConnected())
    {
        const uint32_t nowUs = micros();
        if (static_cast<uint32_t>(nowUs - lastSendUs) >= PACKET_INTERVAL_US)
        {
            lastSendUs = nowUs;
            uint8_t packet[] = {
                static_cast<uint8_t>(sequence),
                static_cast<uint8_t>(sequence >> 8),
                static_cast<uint8_t>(sequence >> 16),
                static_cast<uint8_t>(sequence >> 24),
            };
            ++sequence;
            ++attempted;

            if (!packetCharacteristic->writeValue(
                    packet, sizeof(packet), false))
            {
                ++writeFailures;
            }
        }

        const uint32_t nowMs = millis();
        if (nowMs - lastReportMs >= 5000)
        {
            lastReportMs += 5000;
            char report[96];
            snprintf(
                report,
                sizeof(report),
                "5s client stats: writes=%" PRIu32 " write_failures=%" PRIu32,
                attempted,
                writeFailures);
            Serial.println(report);
            attempted = 0;
            writeFailures = 0;
        }

        delay(1);
    }

    Serial.println("BLE server disconnected; restarting scan");
    delay(500);
}
