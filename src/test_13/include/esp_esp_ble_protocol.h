#pragma once
#include <cstddef>
#include <cstdint>

namespace EspEspBle {
constexpr char SERVICE_UUID[] = "12345678-1234-5678-1234-567812345678";
constexpr char WORLD_UUID[] = "12345678-1234-5678-1234-567812345679";
constexpr char TELEMETRY_UUID[] = "12345678-1234-5678-1234-56781234567a";
constexpr size_t WORLD_SIZE = 20;
constexpr size_t TELEMETRY_SIZE = 21;
constexpr size_t MAX_CARS = 4;

#pragma pack(push, 1)
struct World {
    uint16_t sequence;
    uint8_t topology;
    uint8_t movement[4];
    uint8_t padding[13];
};
struct Telemetry {
    uint8_t car;
    uint16_t sequence;
    uint16_t worldSequence;
    uint8_t worldLostPrevious5s;
    uint8_t nfc[6];
    uint16_t nfcAge;
    uint8_t followState;
    int16_t leftSpeed;
    int16_t rightSpeed;
    uint16_t battery;
};
#pragma pack(pop)
static_assert(sizeof(World) == WORLD_SIZE, "World wire format must be 20 bytes");
static_assert(sizeof(Telemetry) == TELEMETRY_SIZE, "Telemetry wire format must be 21 bytes");

inline int hexDigit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
inline bool decodeHex(const char* input, size_t length, uint8_t* output, size_t size) {
    if (length != size * 2) return false;
    for (size_t i = 0; i < size; ++i) {
        const int high = hexDigit(input[i*2]), low = hexDigit(input[i*2+1]);
        if (high < 0 || low < 0) return false;
        output[i] = static_cast<uint8_t>((high << 4) | low);
    }
    return true;
}
inline void encodeHexLine(const uint8_t* data, size_t size, char* output) {
    constexpr char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < size; ++i) {
        output[i*2] = digits[data[i] >> 4];
        output[i*2+1] = digits[data[i] & 15];
    }
    output[size*2] = '\n';
}
}
