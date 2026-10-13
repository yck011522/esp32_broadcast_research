#pragma once
#include <Arduino.h>
#include <esp_task_wdt.h>
#include <ble14_protocol.h>

inline void initializeWatchdog() {
    esp_task_wdt_config_t configuration{Ble14::WATCHDOG_MS, 0, true};
    esp_err_t result = esp_task_wdt_init(&configuration);
    if (result == ESP_ERR_INVALID_STATE) result = esp_task_wdt_reconfigure(&configuration);
    configASSERT(result == ESP_OK);
    configASSERT(esp_task_wdt_add(nullptr) == ESP_OK);
}
inline void watchThisTask() { configASSERT(esp_task_wdt_add(nullptr) == ESP_OK); }
inline void feedWatchdog() { esp_task_wdt_reset(); }
