#pragma once
// Force-included only by the modem profile, before library compilation.
// NimBLE-Arduino compiles its host from source; do not edit the installed SDK.
#include "sdkconfig.h"
#undef CONFIG_BT_NIMBLE_MAX_CONNECTIONS
#define CONFIG_BT_NIMBLE_MAX_CONNECTIONS 4
#undef CONFIG_NIMBLE_MAX_CONNECTIONS
#define CONFIG_NIMBLE_MAX_CONNECTIONS CONFIG_BT_NIMBLE_MAX_CONNECTIONS
