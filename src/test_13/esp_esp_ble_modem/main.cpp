// USB serial <-> up to four BLE car links. Commands come only from the PC.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <esp_esp_ble_protocol.h>
#include <atomic>
#include <cstdarg>
#include <cstring>

using namespace EspEspBle;
static_assert(NIMBLE_MAX_CONNECTIONS >= MAX_CARS, "Modem host needs four connection slots");
static_assert(CONFIG_BT_CTRL_BLE_MAX_ACT >= MAX_CARS + 2, "Controller needs connection and scan capacity");

struct Target {
    uint8_t car;
    const char* mac;
    NimBLEAddress address{};
    bool found = false;
    NimBLEClient* client = nullptr;
    NimBLERemoteCharacteristic* world = nullptr;
    SemaphoreHandle_t operations = nullptr;
    std::atomic<bool> ready{false};
    std::atomic<uint32_t> tx{0}, rx{0}, writeErrors{0}, connects{0}, disconnects{0};
};
// Public BLE addresses, not USB serial MACs. Add entries here for more cars.
#if BLE_MODEM_FOUR_CARS
Target targets[] = {{2, "68:ee:8f:46:33:45"}, {3, "68:ee:8f:4b:5b:19"},
                    {1, "1c:db:d4:43:73:ed"}, {4, "68:ee:8f:4b:4f:35"}};
#else
Target targets[] = {{2, "68:ee:8f:46:33:45"}, {3, "68:ee:8f:4b:5b:19"}};
#endif
constexpr size_t TARGET_COUNT = sizeof(targets) / sizeof(targets[0]);
static_assert(TARGET_COUNT <= MAX_CARS, "Too many target cars");

struct Command { uint8_t data[WORLD_SIZE]; };
struct Packet { uint8_t data[TELEMETRY_SIZE]; };
struct Event { char text[192]; };
QueueHandle_t commands, packets, events;
std::atomic<bool> enabled{false};
std::atomic<uint32_t> commandDrops{0}, telemetryDrops{0}, malformedTelemetry{0}, eventDrops{0};
uint32_t serialReceived = 0, serialLost = 0, serialSequenceErrors = 0;
uint32_t serialMalformed = 0, serialIgnored = 0, partialWrites = 0;
uint16_t lastSerialSequence = 0;
bool haveSerialSequence = false;

void event(const char* format, ...) {
    Event message{};
    va_list args;
    va_start(args, format);
    vsnprintf(message.text, sizeof(message.text), format, args);
    va_end(args);
    if (xQueueSend(events, &message, 0) != pdTRUE) ++eventDrops;
}

Target* targetFor(NimBLEClient* client) {
    for (auto& target : targets) if (target.client == client) return &target;
    return nullptr;
}

class ClientCallbacks : public NimBLEClientCallbacks {
    void onConnect(NimBLEClient* client) override {
        auto* target = targetFor(client);
        if (target) ++target->connects;
    }
    void onDisconnect(NimBLEClient* client, int reason) override {
        auto* target = targetFor(client);
        if (!target) return;
        target->ready = false;
        ++target->disconnects;
        event("#EVENT car=%u event=disconnected reason=%d\n", target->car, reason);
    }
} clientCallbacks;

void notification(NimBLERemoteCharacteristic* characteristic, uint8_t* data, size_t length, bool isNotify) {
    auto* target = targetFor(characteristic->getClient());
    if (!target || !isNotify || length != TELEMETRY_SIZE || data[0] != target->car) {
        ++malformedTelemetry;
        return;
    }
    ++target->rx;
    Packet packet;
    memcpy(packet.data, data, sizeof(packet.data));
    if (xQueueSend(packets, &packet, 0) != pdTRUE) ++telemetryDrops;
}

void scanTargets() {
    auto* scan = NimBLEDevice::getScan();
    event("#EVENT event=scanning\n");
    const auto results = scan->getResults(2000);
    for (const auto* device : results) {
        if (!device->isAdvertisingService(NimBLEUUID(SERVICE_UUID))) continue;
        for (auto& target : targets) {
            if (device->getAddress() == NimBLEAddress(target.mac, BLE_ADDR_PUBLIC)) {
                target.address = device->getAddress(); // Preserve address type.
                target.found = true;
                event("#EVENT car=%u event=found mac=%s\n", target.car, target.mac);
            }
        }
    }
    scan->clearResults(); // Only copied addresses are retained.
}

void manageConnections(void*) {
    for (;;) {
        if (!enabled) {
            for (auto& target : targets) {
                target.ready = false;
                if (target.client->isConnected()) {
                    xSemaphoreTake(target.operations, portMAX_DELAY);
                    target.client->disconnect();
                    target.world = nullptr;
                    xSemaphoreGive(target.operations);
                }
            }
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        bool needScan = false;
        for (const auto& target : targets) if (!target.found) needScan = true;
        if (needScan) scanTargets();
        for (auto& target : targets) {
            if (!enabled) break;
            if (target.ready || !target.found) continue;
            xSemaphoreTake(target.operations, portMAX_DELAY);
            target.world = nullptr;
            event("#EVENT car=%u event=connecting mac=%s\n", target.car, target.mac);
            bool good = target.client->connect(target.address, true);
            if (good) {
                auto* service = target.client->getService(SERVICE_UUID);
                auto* world = service ? service->getCharacteristic(WORLD_UUID) : nullptr;
                auto* telemetry = service ? service->getCharacteristic(TELEMETRY_UUID) : nullptr;
                good = world && world->canWriteNoResponse() && telemetry && telemetry->canNotify()
                       && target.client->getMTU() >= TELEMETRY_SIZE + 3;
                if (good) good = telemetry->subscribe(true, notification);
                if (good && enabled && target.client->isConnected()) {
                    target.world = world;
                    target.ready = true;
                    event("#EVENT car=%u event=ready mtu=%u interval_ms=%.2f\n", target.car,
                          target.client->getMTU(), target.client->getConnInfo().getConnInterval()*1.25);
                } else good = false;
            }
            if (!good) {
                target.ready = false;
                event("#EVENT car=%u event=connect_failed error=%d\n", target.car, target.client->getLastError());
                if (target.client->isConnected()) target.client->disconnect();
            }
            xSemaphoreGive(target.operations);
            if (!good) vTaskDelay(pdMS_TO_TICKS(1000));
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void broadcastCommands(void*) {
    Command command;
    for (;;) {
        if (xQueueReceive(commands, &command, portMAX_DELAY) != pdTRUE) continue;
        if (!enabled) continue;
        for (auto& target : targets) {
            if (!target.ready) continue;
            // Connection setup cannot stall the other established links.
            if (xSemaphoreTake(target.operations, 0) != pdTRUE) {
                ++target.writeErrors;
                continue;
            }
            if (target.ready && target.client->isConnected() && target.world) {
                if (target.world->writeValue(command.data, WORLD_SIZE, false)) ++target.tx;
                else ++target.writeErrors;
            }
            xSemaphoreGive(target.operations);
        }
    }
}

void status() {
    unsigned links = 0, ready = 0;
    for (auto& target : targets) {
        links += target.client->isConnected();
        ready += target.ready.load();
    }
    event("#MODEM enabled=%d targets=%u links=%u ready=%u max_links=%u host_limit=%u uptime_ms=%lu\n",
          int(enabled.load()), unsigned(TARGET_COUNT), links, ready, unsigned(MAX_CARS),
          unsigned(NIMBLE_MAX_CONNECTIONS), (unsigned long)millis());
    event("#IO received=%lu seq_lost=%lu seq_errors=%lu malformed=%lu ignored=%lu command_drops=%lu telemetry_drops=%lu malformed_telemetry=%lu partial_writes=%lu\n",
          (unsigned long)serialReceived, (unsigned long)serialLost,
          (unsigned long)serialSequenceErrors, (unsigned long)serialMalformed,
          (unsigned long)serialIgnored, (unsigned long)commandDrops.load(), (unsigned long)telemetryDrops.load(),
          (unsigned long)malformedTelemetry.load(), (unsigned long)partialWrites);
    for (auto& target : targets) {
        const bool connected = target.client->isConnected();
        const double interval = connected ? target.client->getConnInfo().getConnInterval()*1.25 : 0.0;
        event("#LINK car=%u ready=%d connected=%d mtu=%u interval_ms=%.2f tx=%lu rx=%lu errors=%lu connects=%lu disconnects=%lu\n",
              target.car, int(target.ready.load()), int(connected), connected ? target.client->getMTU() : 0,
              interval, (unsigned long)target.tx.load(),
              (unsigned long)target.rx.load(), (unsigned long)target.writeErrors.load(),
              (unsigned long)target.connects.load(), (unsigned long)target.disconnects.load());
    }
}

void inputLine(char* line, size_t size) {
    line[size] = '\0';
    if (strcmp(line, "#START") == 0) {
        haveSerialSequence = false;
        enabled = true;
        event("#EVENT event=started\n");
        status();
        return;
    }
    if (strcmp(line, "#STOP") == 0) {
        enabled = false;
        xQueueReset(commands);
        event("#EVENT event=stopped\n");
        status();
        return;
    }
    if (strcmp(line, "#STATUS") == 0) { status(); return; }
    Command command;
    if (!decodeHex(line, size, command.data, WORLD_SIZE)) { ++serialMalformed; return; }
    if (!enabled) { ++serialIgnored; return; }
    const uint16_t sequence = uint16_t(command.data[0]) | (uint16_t(command.data[1]) << 8);
    if (haveSerialSequence) {
        const uint16_t delta = uint16_t(sequence - lastSerialSequence);
        if (delta == 0 || delta >= 0x8000) ++serialSequenceErrors;
        else serialLost += delta - 1;
    }
    lastSerialSequence = sequence;
    haveSerialSequence = true;
    ++serialReceived;
    // The modem transports every correctly framed payload, even a repeated
    // sequence. Its statistics must never change the bytes/delivery contract.
    if (xQueueSend(commands, &command, 0) != pdTRUE) ++commandDrops;
}

void setup() {
    Serial.begin(115200);
    commands = xQueueCreate(32, sizeof(Command));
    packets = xQueueCreate(128, sizeof(Packet));
    events = xQueueCreate(32, sizeof(Event));
    configASSERT(commands && packets && events);
    NimBLEDevice::init("esp_esp_ble_modem");
    NimBLEDevice::setMTU(64);
    auto* scan = NimBLEDevice::getScan();
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(50);
    for (auto& target : targets) {
        target.operations = xSemaphoreCreateMutex();
        target.client = NimBLEDevice::createClient();
        configASSERT(target.operations && target.client);
        target.client->setClientCallbacks(&clientCallbacks, false);
        target.client->setConnectionParams(12, 12, 0, 400); // 15 ms, no latency, 4 s supervision.
        target.client->setConnectTimeout(5000);
        target.client->setConnectRetries(0);
    }
    configASSERT(xTaskCreate(manageConnections, "ble_connect", 6144, nullptr, 1, nullptr) == pdPASS);
    configASSERT(xTaskCreate(broadcastCommands, "ble_write", 4096, nullptr, 2, nullptr) == pdPASS);
    event("#BOOT role=esp_esp_ble_modem host_limit=%u targets=%u\n", unsigned(NIMBLE_MAX_CONNECTIONS), unsigned(TARGET_COUNT));
    status();
}

void loop() {
    static char line[WORLD_SIZE*2 + 1];
    static size_t length = 0;
    static bool overflow = false;
    static uint32_t lastReport = millis();
    for (unsigned budget = 0; budget < 256 && Serial.available(); ++budget) {
        const char c = char(Serial.read());
        if (c == '\r') continue;
        if (c == '\n') {
            if (overflow) ++serialMalformed;
            else if (length) inputLine(line, length);
            length = 0; overflow = false;
        } else if (length < sizeof(line)-1 && !overflow) line[length++] = c;
        else overflow = true; // Recover only at the next newline.
    }
    static Packet packet;
    static bool pendingPacket = false;
    for (unsigned budget = 0; budget < 8; ++budget) {
        if (!pendingPacket) pendingPacket = xQueueReceive(packets, &packet, 0) == pdTRUE;
        if (!pendingPacket || Serial.availableForWrite() < int(TELEMETRY_SIZE*2+1)) break;
        char hex[TELEMETRY_SIZE*2+1];
        encodeHexLine(packet.data, TELEMETRY_SIZE, hex);
        if (Serial.write(reinterpret_cast<uint8_t*>(hex), sizeof(hex)) != sizeof(hex)) ++partialWrites;
        pendingPacket = false;
    }
    static Event message;
    static bool pendingEvent = false;
    if (!pendingEvent) pendingEvent = xQueueReceive(events, &message, 0) == pdTRUE;
    if (pendingEvent) {
        const size_t size = strlen(message.text);
        if (Serial.availableForWrite() >= int(size)) {
            if (Serial.write(reinterpret_cast<uint8_t*>(message.text), size) != size) ++partialWrites;
            pendingEvent = false;
        }
    }
    if (millis() - lastReport >= 5000) { status(); lastReport = millis(); }
    delay(1);
}
