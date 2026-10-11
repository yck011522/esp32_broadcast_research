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
#include <esp_system.h>
#if defined(CONFIG_NIMBLE_ENABLED)
#include <os/os_mbuf.h>
#endif

namespace
{
#ifndef BLE_CAR_ID
#define BLE_CAR_ID 1
#endif
#if BLE_CAR_ID == 1
constexpr char DEVICE_NAME[] = "ble_car_1";
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001";
#elif BLE_CAR_ID == 2
constexpr char DEVICE_NAME[] = "ble_car_2";
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0002";
#elif BLE_CAR_ID == 3
constexpr char DEVICE_NAME[] = "ble_car_3";
constexpr char SERVICE_UUID[] = "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0003";
#else
#error BLE_CAR_ID must be 1, 2, or 3
#endif
constexpr char PACKET_CHARACTERISTIC_UUID[] = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr char WORLD_CHARACTERISTIC_UUID[] = "7a1e0003-4b6d-4f7a-9c2e-6d3b1a5f0001";
constexpr size_t WORLD_SIZE = 20;
constexpr uint32_t PACKET_INTERVAL_MS = 50; // Car telemetry: 20 Hz.
constexpr uint32_t REPORT_INTERVAL_MS = 5000;
constexpr uint32_t COMMAND_TIMEOUT_MS = 5000;
constexpr uint32_t RECOVERY_TIMEOUT_MS = 15000;
std::atomic<bool> connected{false};
std::atomic<uint32_t> connectionEpoch{0};
std::atomic<uint32_t> notifyErrors{0};
std::atomic<int> lastNotifyError{0};
std::atomic<uint32_t> lastNoMemoryMs{0};
std::atomic<bool> hadNoMemory{false};
uint32_t skippedNotifications = 0;
BLEServer *bleServer = nullptr;
BLEAdvertising *advertising = nullptr;
// Survives a software restart, so recovery cannot look like an uninterrupted run.
RTC_DATA_ATTR uint32_t recoveryRestarts = 0;
RTC_DATA_ATTR uint32_t lastRecoveryReason = 0;
BLECharacteristic *packetCharacteristic = nullptr;
portMUX_TYPE worldMux = portMUX_INITIALIZER_UNLOCKED;
uint16_t latestWorldSequence = 0;
bool haveWorld = false;
uint32_t lastWorldMs = 0;
uint32_t worldReceived = 0;
uint32_t worldLost = 0;
uint32_t worldWindowStartMs = 0;

void printDiagnostic(const char *message)
{
    const size_t length = strlen(message);
    if (Serial.availableForWrite() >= static_cast<int>(length))
        Serial.write(reinterpret_cast<const uint8_t *>(message), length);
}

// BLE callbacks only queue diagnostics; serial output happens in loop().
struct BleEvent
{
    const char *name;
    uint32_t atMs;
    int status;
    uint16_t interval;
    uint16_t latency;
    uint16_t timeout;
};
QueueHandle_t events = nullptr;

void queueEvent(const char *name, int status = 0, uint16_t interval = 0,
                uint16_t latency = 0, uint16_t timeout = 0)
{
    const BleEvent event{name, millis(), status, interval, latency, timeout};
    xQueueSend(events, &event, 0);
}

#if defined(CONFIG_NIMBLE_ENABLED)
int gapDiagnostic(ble_gap_event *event, void *)
{
    if (event->type == BLE_GAP_EVENT_DISCONNECT)
        queueEvent("gap_disconnect", event->disconnect.reason);
    else if (event->type == BLE_GAP_EVENT_ADV_COMPLETE)
        queueEvent("advertising_complete", event->adv_complete.reason);
    return 0;
}
#endif

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
#endif
// Track submission/transmission errors; success is not an application-level ACK.
class PacketCallbacks : public BLECharacteristicCallbacks
{
    void onStatus(BLECharacteristic *, Status status, uint32_t code) override
    {
        if (status != SUCCESS_NOTIFY && status != SUCCESS_INDICATE)
        {
            ++notifyErrors;
            lastNotifyError.store(static_cast<int>(code));
#if defined(CONFIG_NIMBLE_ENABLED)
            if (code == BLE_HS_ENOMEM)
            {
                lastNoMemoryMs.store(millis());
                hadNoMemory.store(true);
            }
#endif
        }
    }
#if defined(CONFIG_NIMBLE_ENABLED)
public:
    static std::atomic<bool> subscribed;
    void onSubscribe(BLECharacteristic *, ble_gap_conn_desc *, uint16_t value) override
    {
        subscribed.store((value & 0x01) != 0);
    }
#endif
};
#if defined(CONFIG_NIMBLE_ENABLED)
std::atomic<bool> PacketCallbacks::subscribed{false};
#endif

bool notificationsEnabled()
{
#if defined(CONFIG_BLUEDROID_ENABLED)
    return notificationConfiguration->getNotifications();
#else
    return PacketCallbacks::subscribed.load();
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
        ++connectionEpoch;
        connected.store(true);
        queueEvent("connected");
    }

    void onDisconnect(BLEServer *) override
    {
        connected.store(false);
#if defined(CONFIG_BLUEDROID_ENABLED)
        notificationConfiguration->setNotifications(false);
#else
        PacketCallbacks::subscribed.store(false);
#endif
        // Restart from loop, after the host has finished processing disconnect.
        queueEvent("disconnected");
    }
#if defined(CONFIG_NIMBLE_ENABLED)
    void onConnParamsUpdate(uint16_t, uint16_t interval, uint16_t latency,
                            uint16_t timeout, uint8_t status) override
    {
        queueEvent("connection_parameters", status, interval, latency, timeout);
    }
#endif
};

void recoverBle(uint32_t now)
{
    static uint32_t epoch = 0;
    static bool disconnectRequested = false;
    static uint32_t disconnectAt = 0;
    static uint32_t lastAdvertiseAttempt = 0;
    static uint32_t advertisingMissingSince = 0;
    if (epoch != connectionEpoch.load())
    {
        epoch = connectionEpoch.load();
        disconnectRequested = false;
    }
    if (connected.load())
    {
        advertisingMissingSince = 0;
        portENTER_CRITICAL(&worldMux);
        const uint32_t lastCommand = lastWorldMs;
        const bool receivedCommand = haveWorld;
        portEXIT_CRITICAL(&worldMux);
        // Read the clock after the callback-owned timestamp to avoid unsigned
        // underflow if a write arrived just after loop() sampled `now`.
        const uint32_t timeout = receivedCommand ? COMMAND_TIMEOUT_MS : 10000;
        if (!disconnectRequested && millis() - lastCommand >= timeout)
        {
            queueEvent("command_timeout_disconnect");
            disconnectRequested = true;
            disconnectAt = now;
            bleServer->disconnect(bleServer->getConnId());
        }
        // A stuck BLE host may never deliver the disconnect callback.
        if (disconnectRequested && now - disconnectAt >= RECOVERY_TIMEOUT_MS)
        {
            ++recoveryRestarts;
            lastRecoveryReason = 1;
            printDiagnostic("ble recovery: restarting; disconnect did not complete\r\n");
            ESP.restart();
        }
    }
    else
    {
        disconnectRequested = false;
        if (advertising->isAdvertising())
        {
            advertisingMissingSince = 0;
            return;
        }
        if (advertisingMissingSince == 0)
            advertisingMissingSince = now;
        // Advertising stops slightly before onConnect runs. Give that event
        // time to arrive, instead of reopening advertising during connection.
        if (now - advertisingMissingSince >= 500 && now - lastAdvertiseAttempt >= 1000)
        {
            lastAdvertiseAttempt = now;
            advertising->start();
            queueEvent("advertising_restart", advertising->isAdvertising() ? 0 : -1);
        }
        if (!advertising->isAdvertising() &&
            now - advertisingMissingSince >= RECOVERY_TIMEOUT_MS)
        {
            ++recoveryRestarts;
            lastRecoveryReason = 2;
            printDiagnostic("ble recovery: restarting; advertising did not recover\r\n");
            ESP.restart();
        }
    }
}
}

void setup()
{
    Serial.begin(115200);
    // USB diagnostics must not pause car telemetry when no monitor is reading.
    Serial.setTxTimeoutMs(1);
    events = xQueueCreate(16, sizeof(BleEvent));
    if (events == nullptr)
        ESP.restart();
    BLEDevice::init(DEVICE_NAME);
#if defined(CONFIG_NIMBLE_ENABLED)
    BLEDevice::setCustomGapHandler(gapDiagnostic);
#endif
    BLEServer *server = BLEDevice::createServer();
    bleServer = server;
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
#endif
    // NimBLE creates the CCCD automatically for notification characteristics.
    packetCharacteristic->setCallbacks(new PacketCallbacks());
    service->start();
    advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->start();
    worldWindowStartMs = millis();
    lastWorldMs = worldWindowStartMs;
    char boot[192];
    snprintf(boot, sizeof(boot),
             "ble boot: car=%d reset=%d recovery_restarts=%" PRIu32 " recovery_reason=%" PRIu32 "\r\n",
             BLE_CAR_ID, static_cast<int>(esp_reset_reason()), recoveryRestarts, lastRecoveryReason);
    printDiagnostic(boot);
    // The platform's 5-second task watchdog also covers a blocked BLE API call.
    enableLoopWDT();
}

void loop()
{
    static uint16_t sequence = 0;
    static uint32_t lastSendMs = millis();
    const uint32_t now = millis();
    recoverBle(now);
    BleEvent event;
    while (xQueueReceive(events, &event, 0) == pdTRUE)
    {
        char line[192];
        snprintf(line, sizeof(line),
                 "ble event: uptime_ms=%" PRIu32 " event=%s status=%d interval=%.2f ms latency=%u timeout=%u ms\r\n",
                 event.atMs, event.name, event.status, event.interval * 1.25,
                 event.latency, event.timeout * 10);
        printDiagnostic(line);
    }

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
        bool canNotify = true;
#if defined(CONFIG_NIMBLE_ENABLED)
        // Keep packet buffers available for incoming commands and link control.
        // Do not queue obsolete telemetry indefinitely while a link is stalled.
        canNotify = os_msys_num_free() > 4 &&
            (!hadNoMemory.load() || millis() - lastNoMemoryMs.load() >= 200);
#endif
        if (canNotify)
        {
            packetCharacteristic->setValue(packet, sizeof(packet));
            packetCharacteristic->notify();
        }
        else
            ++skippedNotifications;
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
        printDiagnostic(report);
#if defined(CONFIG_NIMBLE_ENABLED)
        snprintf(report, sizeof(report), "ble buffers: free=%d total=%d skipped_notifications=%" PRIu32 "\r\n",
                 os_msys_num_free(), os_msys_count(), skippedNotifications);
        printDiagnostic(report);
#endif
        snprintf(report, sizeof(report),
                 "ble health: uptime_ms=%" PRIu32 " connected=%d subscribed=%d advertising=%d heap=%u min_heap=%u notify_errors=%" PRIu32 " last_error=%d restarts=%" PRIu32 "\r\n",
                 reportNow, connected.load(), notificationsEnabled(), advertising->isAdvertising(),
                 ESP.getFreeHeap(), ESP.getMinFreeHeap(), notifyErrors.load(), lastNotifyError.load(), recoveryRestarts);
        printDiagnostic(report);
    }
    else
        portEXIT_CRITICAL(&worldMux);
    delay(1);
}
