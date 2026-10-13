// Test 14 synthetic car. No motor outputs. Wire format is payload.md.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <ble14_protocol.h>
#include <ble14_watchdog.h>
#include <atomic>
#include <cstring>
#include <cstdlib>
using namespace Ble14;
#ifndef BLE_CAR_ID
#define BLE_CAR_ID 1
#endif
static_assert(BLE_CAR_ID >= 1 && BLE_CAR_ID <= CARS, "Logical car ID");

portMUX_TYPE worldMux = portMUX_INITIALIZER_UNLOCKED;
World latest{};
bool haveWorld = false;
uint32_t lastWorldMs = 0, windowRx = 0, windowLost = 0, totalRx = 0, totalLost = 0;
uint32_t sequenceChanges = 0;
std::atomic<bool> connected{false}, subscribed{false};
std::atomic<bool> receiveActivity{false};
std::atomic<uint16_t> connectionHandle{BLE_HS_CONN_HANDLE_NONE};
std::atomic<uint32_t> connects{0}, disconnects{0}, notifyErrors{0};
NimBLEServer* server;
NimBLECharacteristic* robot;
uint32_t advertisingPauseUntil = 0, telemetryPauseUntil = 0;

void printLine(const char* line) {
    const size_t length = strlen(line);
    if (Serial.availableForWrite() >= int(length)) Serial.write(reinterpret_cast<const uint8_t*>(line), length);
}
class WorldCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo&) override {
        const auto value = characteristic->getValue();
        if (value.size() != sizeof(World)) return;
        World incoming;
        memcpy(&incoming, value.data(), sizeof(incoming));
        const uint32_t now = millis();
        portENTER_CRITICAL(&worldMux);
        if (haveWorld) {
            const uint16_t delta = uint16_t(incoming.sequence-latest.sequence);
            // Sequence numbers measure delivery; they never reject valid intent.
            const uint32_t plausible = (now-lastWorldMs)/20+20;
            if (delta > 0 && delta < 0x8000 && delta <= plausible) {
                windowLost += delta-1;
                totalLost += delta-1;
            } else if (delta >= 0x8000 || delta > plausible) ++sequenceChanges;
        }
        latest = incoming; haveWorld = true; lastWorldMs = now;
        receiveActivity = true;
        ++windowRx; ++totalRx;
        portEXIT_CRITICAL(&worldMux);
    }
} worldCallbacks;
class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer*, NimBLEConnInfo& info) override {
        portENTER_CRITICAL(&worldMux);
        haveWorld = false;
        portEXIT_CRITICAL(&worldMux);
        connectionHandle = info.getConnHandle();
        connected = true; subscribed = false; ++connects;
    }
    void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) override {
        connected = false; subscribed = false;
        connectionHandle = BLE_HS_CONN_HANDLE_NONE; ++disconnects;
    }
} serverCallbacks;
class RobotCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t value) override { subscribed = (value&1) != 0; }
} robotCallbacks;

void serialControl(uint32_t now) {
    static char input[40];
    static size_t length = 0;
    static bool overflow = false;
    for (unsigned budget = 0; budget < 64 && Serial.available(); ++budget) {
        char c = char(Serial.read());
        if (c == '\r') continue;
        if (c != '\n') {
            if (length < sizeof(input)-1 && !overflow) input[length++] = c;
            else overflow = true;
            continue;
        }
        input[length] = 0;
        if (!overflow) {
            unsigned seconds = 0;
            char trailing = 0;
            if (sscanf(input, "DISCONNECT %u %c", &seconds, &trailing) == 1 && seconds >= 1 && seconds <= 300) {
                advertisingPauseUntil = now+seconds*1000;
                NimBLEDevice::getAdvertising()->stop();
                if (connected) server->disconnect(connectionHandle.load());
                printLine("car14 test: disconnect_requested\n");
            } else if (sscanf(input, "SILENCE %u %c", &seconds, &trailing) == 1 && seconds >= 1 && seconds <= 300) {
                telemetryPauseUntil = now+seconds*1000;
                printLine("car14 test: telemetry_paused\n");
            } else if (strcmp(input, "HANG") == 0) {
                printLine("car14 test: watchdog_injection\n");
                for (;;) delay(1); // Development-only serial test, watchdog is deliberately not fed.
            }
        }
        length = 0; overflow = false;
    }
}

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, HIGH); // XIAO user LED is active low.
    Serial.begin(115200);
    initializeWatchdog();
    const String name = "ble14_car_"+String(BLE_CAR_ID);
    NimBLEDevice::init(name.c_str());
    NimBLEDevice::setMTU(64);
    server = NimBLEDevice::createServer();
    server->setCallbacks(&serverCallbacks);
    server->advertiseOnDisconnect(false); // loop owns restart and artificial outage timing.
    auto* service = server->createService(SERVICE_UUID);
    auto* world = service->createCharacteristic(WORLD_UUID, NIMBLE_PROPERTY::WRITE_NR, sizeof(World));
    world->setCallbacks(&worldCallbacks);
    robot = service->createCharacteristic(TELEMETRY_UUID, NIMBLE_PROPERTY::NOTIFY, sizeof(Robot));
    robot->setCallbacks(&robotCallbacks);
    NimBLEAdvertisementData advertisement, scanResponse;
    const uint8_t identity[]{VERSION, BLE_CAR_ID};
    advertisement.setFlags(0x06);
    advertisement.setServiceData(NimBLEUUID(SERVICE_UUID), identity, sizeof(identity));
    scanResponse.setName(name.c_str());
    auto* advertising = NimBLEDevice::getAdvertising();
    advertising->setAdvertisementData(advertisement);
    advertising->setScanResponseData(scanResponse);
    advertising->enableScanResponse(true);
    advertising->setAdvertisingInterval(160); // 100 ms.
    advertising->start();
}
void loop() {
    static uint32_t lastNotify = millis(), lastReport = millis(), nextLedEdge = 0;
    static uint16_t telemetrySequence = 0;
    static uint8_t lossPrevious5s = 0;
    static bool ledOn = false;
    const uint32_t now = millis();
    serialControl(now);
    if (advertisingPauseUntil != 0 && due(now, advertisingPauseUntil)) advertisingPauseUntil = 0;
    if (telemetryPauseUntil != 0 && due(now, telemetryPauseUntil)) telemetryPauseUntil = 0;
    auto* advertising = NimBLEDevice::getAdvertising();
    const bool shouldAdvertise = !connected && advertisingPauseUntil == 0;
    if (shouldAdvertise && !advertising->isAdvertising()) advertising->start();
    else if (!shouldAdvertise && advertising->isAdvertising()) advertising->stop();
    World world;
    bool seen;
    uint32_t last;
    portENTER_CRITICAL(&worldMux);
    world = latest; seen = haveWorld; last = lastWorldMs;
    portEXIT_CRITICAL(&worldMux);
    if (now-last >= 100) receiveActivity = false;
    const bool active = receiveActivity.load();
    bool desiredLed = ledOn;
    if (!active) { desiredLed = false; nextLedEdge = now; }
    else if (due(now, nextLedEdge)) { desiredLed = !ledOn; nextLedEdge = now+100; }
    if (desiredLed != ledOn) { ledOn = desiredLed; digitalWrite(LED_BUILTIN, ledOn ? LOW : HIGH); }
    if (now-lastNotify >= 50) {
        lastNotify += 50;
        if (now-lastNotify >= 50) lastNotify = now;
        if (connected && subscribed && telemetryPauseUntil == 0) {
            Robot packet{};
            packet.car = BLE_CAR_ID; packet.sequence = telemetrySequence++;
            packet.worldSequence = seen ? world.sequence : 0;
            packet.worldLostPrevious5s = lossPrevious5s;
            packet.leftSpeed = packet.rightSpeed = 150; packet.battery = 12420;
            robot->setValue(reinterpret_cast<const uint8_t*>(&packet), sizeof(packet));
            if (!robot->notify()) ++notifyErrors;
        }
    }
    if (now-lastReport >= 5000) {
        uint32_t rx, gaps, total, totalGaps, changes;
        portENTER_CRITICAL(&worldMux);
        rx = windowRx; gaps = windowLost; total = totalRx; totalGaps = totalLost; changes = sequenceChanges;
        windowRx = windowLost = 0;
        portEXIT_CRITICAL(&worldMux);
        lossPrevious5s = uint8_t(gaps > 255 ? 255 : gaps);
        char line[256];
        snprintf(line, sizeof(line), "car14: car=%u connected=%u subscribed=%u rx_hz=%.2f seq_lost=%lu total_rx=%lu total_lost=%lu seq_changes=%lu connects=%lu disconnects=%lu notify_errors=%lu uptime_ms=%lu last_world=%u silence_ms=%lu led=%u\n",
                 BLE_CAR_ID, unsigned(connected.load()), unsigned(subscribed.load()), rx*1000.0/(now-lastReport),
                 (unsigned long)gaps, (unsigned long)total, (unsigned long)totalGaps, (unsigned long)changes,
                 (unsigned long)connects.load(), (unsigned long)disconnects.load(), (unsigned long)notifyErrors.load(),
                 (unsigned long)now, seen ? world.sequence : 0, (unsigned long)(seen ? now-last : 0), unsigned(ledOn));
        printLine(line);
        lastReport = now;
    }
    feedWatchdog();
    delay(1);
}
