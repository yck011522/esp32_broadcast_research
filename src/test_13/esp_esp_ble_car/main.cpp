// Test 13 car peripheral: synthetic commands and telemetry, no motor outputs.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <esp_esp_ble_protocol.h>
#include <atomic>
#include <cstring>

#ifndef BLE_CAR_ID
#define BLE_CAR_ID 1
#endif
static_assert(BLE_CAR_ID >= 1 && BLE_CAR_ID <= 4, "Car ID must be 1..4");
using namespace EspEspBle;
using WorldState = World;
using RobotState = Telemetry;

portMUX_TYPE worldMux = portMUX_INITIALIZER_UNLOCKED;
WorldState latest{};
bool haveWorld = false;
uint32_t lastWorldMs = 0;
uint32_t received = 0, lost = 0, duplicates = 0, oldPackets = 0, malformed = 0;
uint32_t totalReceived = 0, totalLost = 0;
std::atomic<bool> connected{false}, subscribed{false};
std::atomic<uint16_t> mtu{23};
std::atomic<uint32_t> connectEvents{0}, disconnectEvents{0}, notifyErrors{0};
std::atomic<int> disconnectReason{0};
NimBLEServer* server = nullptr;
NimBLECharacteristic* robot = nullptr;

void printLine(const char* line) {
    // USB output never becomes part of the BLE timing path.
    const size_t length = strlen(line);
    if (Serial.availableForWrite() >= static_cast<int>(length))
        Serial.write(reinterpret_cast<const uint8_t*>(line), length);
}

class WorldCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo&) override {
        const auto value = characteristic->getValue();
        portENTER_CRITICAL(&worldMux);
        if (value.size() != sizeof(WorldState)) {
            ++malformed;
            portEXIT_CRITICAL(&worldMux);
            return;
        }
        WorldState candidate;
        memcpy(&candidate, value.data(), sizeof(candidate));
        if (haveWorld) {
            const uint16_t delta = static_cast<uint16_t>(candidate.sequence - latest.sequence);
            if (delta == 0 || delta >= 0x8000) {
                if (delta == 0) ++duplicates;
                else ++oldPackets;
                portEXIT_CRITICAL(&worldMux);
                return;
            }
            lost += delta - 1;
            totalLost += delta - 1;
        }
        latest = candidate;
        haveWorld = true;
        lastWorldMs = millis();
        ++received;
        ++totalReceived;
        portEXIT_CRITICAL(&worldMux);
    }
} worldCallbacks;

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer*, NimBLEConnInfo& info) override {
        portENTER_CRITICAL(&worldMux);
        haveWorld = false;
        received = lost = duplicates = oldPackets = malformed = 0;
        lastWorldMs = millis();
        portEXIT_CRITICAL(&worldMux);
        mtu = info.getMTU();
        subscribed = false;
        connected = true;
        ++connectEvents;
        // The modem central selects the link interval. No server-side request.
    }
    void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int reason) override {
        subscribed = false;
        connected = false;
        disconnectReason = reason;
        ++disconnectEvents;
    }
    void onMTUChange(uint16_t value, NimBLEConnInfo&) override { mtu = value; }
} serverCallbacks;

class RobotCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t value) override {
        subscribed = (value & 1) != 0;
    }
} robotCallbacks;

void setup() {
    Serial.begin(115200);
    const String name = "esp_esp_ble_car_" + String(BLE_CAR_ID);
    NimBLEDevice::init(name.c_str());
    NimBLEDevice::setMTU(64); // 21-byte telemetry requires ATT MTU >= 24.
    server = NimBLEDevice::createServer();
    server->setCallbacks(&serverCallbacks);
    server->advertiseOnDisconnect(true);
    auto* service = server->createService(SERVICE_UUID);
    auto* world = service->createCharacteristic(WORLD_UUID, NIMBLE_PROPERTY::WRITE_NR, sizeof(WorldState));
    world->setCallbacks(&worldCallbacks);
    world->setValue(reinterpret_cast<const uint8_t*>(&latest), sizeof(latest));
    robot = service->createCharacteristic(TELEMETRY_UUID, NIMBLE_PROPERTY::NOTIFY, sizeof(RobotState));
    robot->setCallbacks(&robotCallbacks);
    // Advertising starts/registers the GATT server in NimBLE-Arduino 2.5.
    auto* advertising = NimBLEDevice::getAdvertising();
    advertising->setName(name.c_str());
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->enableScanResponse(true);
    advertising->start();
}

void loop() {
    static uint32_t lastNotify = millis(), lastReport = millis();
    static uint32_t printedConnects = 0, printedDisconnects = 0;
    static uint16_t telemetrySequence = 0;
    static uint8_t lossLastWindow = 0;
    const uint32_t now = millis();
    char line[320];
    if (printedConnects != connectEvents.load() || printedDisconnects != disconnectEvents.load()) {
        printedConnects = connectEvents.load();
        printedDisconnects = disconnectEvents.load();
        snprintf(line, sizeof(line), "car event: car=%d connected=%d connects=%lu disconnects=%lu reason=%d\n",
                 BLE_CAR_ID, int(connected.load()), (unsigned long)printedConnects,
                 (unsigned long)printedDisconnects, disconnectReason.load());
        printLine(line);
    }
    if (now - lastReport >= 5000) {
        uint32_t rx, gaps, dup, old, bad, last, totalRx, totalGaps;
        bool seen;
        portENTER_CRITICAL(&worldMux);
        rx = received; gaps = lost; dup = duplicates; old = oldPackets; bad = malformed;
        received = lost = duplicates = oldPackets = malformed = 0;
        last = lastWorldMs; seen = haveWorld;
        totalRx = totalReceived; totalGaps = totalLost;
        portEXIT_CRITICAL(&worldMux);
        lossLastWindow = gaps > 255 ? 255 : gaps;
        snprintf(line, sizeof(line), "car world: car=%d window=%.3f rate=%.2f seq_lost=%lu silence=%.3f have_packet=%d duplicates=%lu old=%lu malformed=%lu total_received=%lu total_lost=%lu\n",
                 BLE_CAR_ID, (now-lastReport)/1000.0, rx*1000.0/(now-lastReport),
                 (unsigned long)gaps, (now-last)/1000.0, int(seen),
                 (unsigned long)dup, (unsigned long)old, (unsigned long)bad,
                 (unsigned long)totalRx, (unsigned long)totalGaps);
        printLine(line);
        snprintf(line, sizeof(line), "car health: car=%d library=NimBLE-Arduino-2.5.1 connected=%d subscribed=%d advertising=%d mtu=%u notify_errors=%lu command_watchdog=0 heap=%lu uptime_ms=%lu\n",
                 BLE_CAR_ID, int(connected.load()), int(subscribed.load()),
                 int(NimBLEDevice::getAdvertising()->isAdvertising()), mtu.load(),
                 (unsigned long)notifyErrors.load(), (unsigned long)ESP.getFreeHeap(), (unsigned long)now);
        printLine(line);
        lastReport = now;
    }
    if (now - lastNotify >= 50) {
        lastNotify += 50;
        if (now - lastNotify >= 50) lastNotify = now; // No catch-up bursts.
        if (connected && subscribed && mtu >= sizeof(RobotState) + 3) {
            RobotState packet{};
            packet.car = BLE_CAR_ID;
            packet.sequence = telemetrySequence++;
            portENTER_CRITICAL(&worldMux);
            packet.worldSequence = haveWorld ? latest.sequence : 0;
            portEXIT_CRITICAL(&worldMux);
            packet.worldLostPrevious5s = lossLastWindow;
            packet.battery = 12420;
            packet.leftSpeed = packet.rightSpeed = 150;
            robot->setValue(reinterpret_cast<const uint8_t*>(&packet), sizeof(packet));
            if (!robot->notify()) ++notifyErrors;
        }
    }
    delay(1);
}
