// Transparent USB/BLE modem. W<14 hex digits>\n -> seven unchanged bytes.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <ble14_protocol.h>
#include <ble14_watchdog.h>
#include <atomic>
#include <cstring>
#include <esp_system.h>
using namespace Ble14;
static_assert(NIMBLE_MAX_CONNECTIONS >= CARS, "Four host slots required");

enum Phase : uint8_t { MISSING, CONNECTING, SETUP, ACTIVE };
struct Peer {
    uint8_t car;
    NimBLEClient* client = nullptr;
    NimBLERemoteCharacteristic* world = nullptr;
    SemaphoreHandle_t operations = nullptr;
    std::atomic<Phase> phase{MISSING};
    std::atomic<bool> ready{false};
    std::atomic<uint32_t> started{0}, connectionGeneration{0}, notificationGeneration{0};
    std::atomic<uint16_t> echo{0};
#ifdef BLE14_MODEM_DEBUG
    std::atomic<int> lastFailure{0};
#endif
    ble_addr_t candidate{};
    bool found = false;
    uint32_t foundRound = 0, attemptedRound = 0;
};
Peer peers[] = {{1}, {2}, {3}, {4}};
portMUX_TYPE candidateMux = portMUX_INITIALIZER_UNLOCKED;
uint32_t scanRound = 0;
#ifdef BLE14_MODEM_DEBUG
std::atomic<uint32_t> advertisements{0}, scansCompleted{0};
std::atomic<bool> scanAccepted{false};
#endif
struct Mailbox { World packet{}; uint32_t receivedMs = 0, generation = 0; bool valid = false; } mailbox;
portMUX_TYPE mailboxMux = portMUX_INITIALIZER_UNLOCKED;
QueueHandle_t feedback;
#ifdef BLE14_TEST_STALL_WORKER_AFTER_MS
RTC_NOINIT_ATTR uint32_t workerFaultCookie;
constexpr uint32_t FAULT_COOKIE = 0x14b1e5aa;
#endif

Peer* peerFor(NimBLEClient* client) {
    for (auto& peer : peers) if (peer.client == client) return &peer;
    return nullptr;
}
void forgetCandidate(Peer& peer) {
    portENTER_CRITICAL(&candidateMux);
    peer.found = false;
    portEXIT_CRITICAL(&candidateMux);
}
class ClientCallbacks : public NimBLEClientCallbacks {
    void onConnect(NimBLEClient* client) override {
        auto* peer = peerFor(client);
        if (!peer) return;
        ++peer->connectionGeneration;
        peer->started = millis(); peer->phase = SETUP;
    }
    void onConnectFail(NimBLEClient* client, int reason) override {
        auto* peer = peerFor(client);
        if (!peer) return;
#ifdef BLE14_MODEM_DEBUG
        peer->lastFailure = reason;
#endif
        peer->ready = false; peer->phase = MISSING; forgetCandidate(*peer);
    }
    void onDisconnect(NimBLEClient* client, int) override {
        auto* peer = peerFor(client);
        if (!peer) return;
        peer->ready = false; peer->phase = MISSING; forgetCandidate(*peer);
    }
} clientCallbacks;
class ScanCallbacks : public NimBLEScanCallbacks {
    void onResult(const NimBLEAdvertisedDevice* device) override {
#ifdef BLE14_MODEM_DEBUG
        ++advertisements;
#endif
        const auto identity = device->getServiceData(NimBLEUUID(SERVICE_UUID));
        if (identity.size() != 2 || uint8_t(identity[0]) != VERSION) return;
        const uint8_t car = uint8_t(identity[1]);
        if (car < 1 || car > CARS) return;
        auto& peer = peers[car-1];
        if (peer.phase != MISSING) return;
        const auto address = device->getAddress();
        portENTER_CRITICAL(&candidateMux);
        if (!peer.found) {
            peer.candidate = *address.getBase();
            peer.foundRound = scanRound; peer.found = true;
        }
        portEXIT_CRITICAL(&candidateMux);
    }
#ifdef BLE14_MODEM_DEBUG
    void onScanEnd(const NimBLEScanResults&, int) override { ++scansCompleted; }
#endif
} scanCallbacks;
void notification(NimBLERemoteCharacteristic* characteristic, uint8_t* data, size_t length, bool isNotify) {
    auto* peer = peerFor(characteristic->getClient());
    if (!peer || !isNotify || length != sizeof(Robot) || data[0] != peer->car) return;
    Robot packet;
    memcpy(&packet, data, sizeof(packet));
    peer->echo = packet.worldSequence;
    ++peer->notificationGeneration;
    xQueueSend(feedback, &packet, 0); // USB stalls never block BLE callbacks.
}

void manageConnections(void*) {
    watchThisTask();
    auto* scan = NimBLEDevice::getScan();
    uint32_t lastScan = 0;
    bool scanned = false;
    for (;;) {
        const uint32_t now = millis();
        bool busy = false, missing = false;
        for (auto& peer : peers) {
            const Phase phase = peer.phase.load();
            missing |= phase == MISSING;
            busy |= phase == CONNECTING || phase == SETUP;
            if (phase == CONNECTING && now-peer.started >= 4500) {
                // Normal controller timeout is 3 s. A missing completion must
                // never leave the modem permanently believing it is connecting.
                peer.client->cancelConnect();
                if (now-peer.started >= 6500 && peer.phase == CONNECTING) esp_restart();
            }
            if (phase != SETUP) continue;
            xSemaphoreTake(peer.operations, portMAX_DELAY);
            auto* service = peer.client->getService(SERVICE_UUID);
            auto* world = service ? service->getCharacteristic(WORLD_UUID) : nullptr;
            auto* robot = service ? service->getCharacteristic(TELEMETRY_UUID) : nullptr;
            bool good = world && world->canWriteNoResponse() && robot && robot->canNotify()
                        && peer.client->getMTU() >= sizeof(Robot)+3;
            if (good) good = robot->subscribe(true, notification);
            if (good && peer.client->isConnected() && peer.phase == SETUP) {
                peer.world = world; peer.ready = true; peer.phase = ACTIVE;
            } else {
                peer.world = nullptr; peer.ready = false;
                if (peer.client->isConnected()) peer.client->disconnect();
                else { peer.phase = MISSING; forgetCandidate(peer); }
            }
            xSemaphoreGive(peer.operations);
        }
        if (!busy && !scan->isScanning()) {
            // One attempt per candidate per scan; fresh advertisements can be
            // another physical device with the same logical car ID.
            for (auto& peer : peers) {
                ble_addr_t candidate{};
                bool attempt = false;
                portENTER_CRITICAL(&candidateMux);
                if (peer.phase == MISSING && peer.found && peer.foundRound != peer.attemptedRound) {
                    candidate = peer.candidate; peer.attemptedRound = peer.foundRound;
                    peer.found = false; attempt = true;
                }
                portEXIT_CRITICAL(&candidateMux);
                if (!attempt) continue;
                xSemaphoreTake(peer.operations, portMAX_DELAY);
                peer.world = nullptr; peer.ready = false;
                peer.started = millis(); peer.phase = CONNECTING;
                if (!peer.client->connect(NimBLEAddress(candidate), true, true)) {
#ifdef BLE14_MODEM_DEBUG
                    peer.lastFailure = peer.client->getLastError();
#endif
                    peer.phase = MISSING; forgetCandidate(peer);
                }
                xSemaphoreGive(peer.operations);
                busy = true;
                break;
            }
            if (!busy && missing && (!scanned || now-lastScan >= SCAN_PERIOD_MS)) {
                portENTER_CRITICAL(&candidateMux);
                ++scanRound;
                for (auto& peer : peers) if (peer.phase == MISSING) peer.found = false;
                portEXIT_CRITICAL(&candidateMux);
#ifdef BLE14_MODEM_DEBUG
                scanAccepted = scan->start(SCAN_DURATION_MS);
#else
                scan->start(SCAN_DURATION_MS);
#endif
                lastScan = now; scanned = true;
            }
        }
        feedWatchdog();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void transmitWorld(void*) {
    watchThisTask();
    // These are sequence numbers of already-submitted writes, not payload
    // queues. Cap unconfirmed submissions to prevent one weak link filling
    // shared BLE buffers. Only the single mailbox holds a pending WorldState.
    constexpr size_t MAX_IN_FLIGHT = 8;
    struct Delivery {
        uint32_t connection = 0, mailboxGeneration = 0, notification = 0, retryAt = 0;
        uint16_t sent[MAX_IN_FLIGHT]{};
        size_t count = 0;
    } deliveries[CARS];
    size_t first = 0;
    for (;;) {
#ifdef BLE14_TEST_STALL_WORKER_AFTER_MS
        if (workerFaultCookie != FAULT_COOKIE && millis() >= BLE14_TEST_STALL_WORKER_AFTER_MS) {
            workerFaultCookie = FAULT_COOKIE;
            for (;;) vTaskDelay(1); // Main loop still runs; this worker must trip WDT.
        }
#endif
        const uint32_t now = millis();
        Mailbox current;
        portENTER_CRITICAL(&mailboxMux);
        if (mailbox.valid && now-mailbox.receivedMs > WORLD_EXPIRY_MS) mailbox.valid = false;
        current = mailbox;
        portEXIT_CRITICAL(&mailboxMux);
        for (size_t offset = 0; offset < CARS; ++offset) {
            const size_t index = (first+offset)%CARS;
            auto& peer = peers[index]; auto& delivery = deliveries[index];
            if (!peer.ready) continue;
            if (delivery.connection != peer.connectionGeneration) {
                delivery = Delivery{}; delivery.connection = peer.connectionGeneration;
            }
            const uint32_t notificationVersion = peer.notificationGeneration.load();
            if (notificationVersion != delivery.notification) {
                delivery.notification = notificationVersion;
                size_t acknowledged = 0;
                const uint16_t echoed = peer.echo.load();
                for (size_t i = 0; i < delivery.count; ++i) if (delivery.sent[i] == echoed) acknowledged = i+1;
                if (acknowledged) {
                    delivery.count -= acknowledged;
                    memmove(delivery.sent, delivery.sent+acknowledged, delivery.count*sizeof(uint16_t));
                }
            }
            if (!current.valid || now-current.receivedMs > WORLD_EXPIRY_MS
                || current.generation == delivery.mailboxGeneration || delivery.count == MAX_IN_FLIGHT
                || (delivery.retryAt != 0 && !due(now, delivery.retryAt))) continue;
            if (xSemaphoreTake(peer.operations, 0) != pdTRUE) continue;
            if (peer.ready && peer.world && peer.client->isConnected()) {
                if (peer.world->writeValue(reinterpret_cast<const uint8_t*>(&current.packet), sizeof(World), false)) {
                    delivery.mailboxGeneration = current.generation;
                    delivery.retryAt = 0;
                    delivery.sent[delivery.count++] = current.packet.sequence;
                } else delivery.retryAt = now+20;
            }
            xSemaphoreGive(peer.operations);
        }
        first = (first+1)%CARS;
        feedWatchdog();
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

void setup() {
    Serial.begin(115200);
    initializeWatchdog();
#ifdef BLE14_TEST_STALL_WORKER_AFTER_MS
    const auto reset = esp_reset_reason();
    if (reset != ESP_RST_TASK_WDT && reset != ESP_RST_INT_WDT && reset != ESP_RST_WDT && reset != ESP_RST_PANIC)
        workerFaultCookie = 0;
#endif
    feedback = xQueueCreate(128, sizeof(Robot));
    configASSERT(feedback);
    NimBLEDevice::init("ble14_modem"); NimBLEDevice::setMTU(64);
    auto* scan = NimBLEDevice::getScan();
    // Slots deduplicate logical IDs, so repeated discovery does not depend on
    // the lifetime of a controller's duplicate-address cache.
    scan->setScanCallbacks(&scanCallbacks, true);
    scan->setActiveScan(false); // Identity is in primary advertising data.
    // Short receive windows fit around established connection events.
    scan->setInterval(30); scan->setWindow(5); scan->setMaxResults(0);
#if CONFIG_BT_NIMBLE_EXT_ADV
    scan->setPhy(NimBLEScan::SCAN_1M);
#endif
    for (auto& peer : peers) {
        peer.operations = xSemaphoreCreateMutex();
        peer.client = NimBLEDevice::createClient();
        configASSERT(peer.operations && peer.client);
        peer.client->setClientCallbacks(&clientCallbacks, false);
        peer.client->setConnectionParams(16, 16, 0, 400); // 20 ms; leave time for discovery with three active peers.
        peer.client->setConnectTimeout(3000); peer.client->setConnectRetries(0);
    }
    configASSERT(xTaskCreate(manageConnections, "ble14_connect", 6144, nullptr, 1, nullptr) == pdPASS);
    configASSERT(xTaskCreate(transmitWorld, "ble14_write", 4096, nullptr, 2, nullptr) == pdPASS);
}
void loop() {
    static char line[1+sizeof(World)*2];
    static size_t length = 0;
    static bool overflow = false;
    for (unsigned budget = 0; budget < 256 && Serial.available(); ++budget) {
        const char c = char(Serial.read());
        if (c == '\r') continue;
        if (c == '\n') {
            World world;
            if (!overflow && decodeWorld(line, length, world)) {
                const uint32_t now = millis();
                portENTER_CRITICAL(&mailboxMux);
                mailbox.packet = world; mailbox.receivedMs = now;
                ++mailbox.generation; mailbox.valid = true;
                portEXIT_CRITICAL(&mailboxMux);
            }
            length = 0; overflow = false;
        } else if (length < sizeof(line) && !overflow) line[length++] = c;
        else overflow = true;
    }
    static Robot packet;
    static bool pending = false;
    for (unsigned budget = 0; budget < 8; ++budget) {
        if (!pending) pending = xQueueReceive(feedback, &packet, 0) == pdTRUE;
        if (!pending || Serial.availableForWrite() < int(sizeof(Robot)*2+1)) break;
        char encoded[sizeof(Robot)*2+1];
        encodeRobot(packet, encoded);
        Serial.write(reinterpret_cast<uint8_t*>(encoded), sizeof(encoded));
        pending = false;
    }
    // GATT discovery/subscription APIs may wait internally. Disconnect a setup
    // that exceeds its deadline; a stuck host call is covered by the WDT.
    const uint32_t now = millis();
    for (auto& peer : peers) {
        if (peer.phase == SETUP && now-peer.started > 5000) peer.client->disconnect();
    }
#ifdef BLE14_MODEM_DEBUG
    static uint32_t lastDebug = 0;
    if (now-lastDebug >= 5000) {
        for (auto& peer : peers) {
            uint32_t round, foundRound, attempted;
            bool found;
            portENTER_CRITICAL(&candidateMux);
            round = scanRound; found = peer.found; foundRound = peer.foundRound; attempted = peer.attemptedRound;
            portEXIT_CRITICAL(&candidateMux);
            char debug[192];
            snprintf(debug, sizeof(debug), "#DEBUG car=%u phase=%u ready=%u scan=%lu found=%u round=%lu attempt=%lu fail=%d scan_ok=%u scan_end=%lu ads=%lu connected=%u\n",
                     peer.car, unsigned(peer.phase.load()), unsigned(peer.ready.load()), (unsigned long)round,
                     unsigned(found), (unsigned long)foundRound, (unsigned long)attempted, peer.lastFailure.load(),
                     unsigned(scanAccepted.load()), (unsigned long)scansCompleted.load(), (unsigned long)advertisements.load(),
                     unsigned(peer.client->isConnected()));
            const size_t length = strlen(debug);
            if (Serial.availableForWrite() >= int(length)) Serial.write(reinterpret_cast<uint8_t*>(debug), length);
        }
        lastDebug = now;
    }
#endif
    feedWatchdog();
    delay(1);
}
