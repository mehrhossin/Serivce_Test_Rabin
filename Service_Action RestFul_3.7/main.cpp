/**
 * ============================================================================
 * PROJECT      : RBUS Industrial Node System
 * FILE         : main.cpp
 * HARDWARE     : ESP8266 (NodeMCU v2 / ESP-12F)
 * v7.2         : اکشن — فید‌اوت/فیداین لمس = 200ms قرینه + برگشت به رنگ
 * v7.3         : هاید — «00» لمس = سفید (هم‌رنگ حلقه) | سرویس: پل اکشن 0x26→0x42
 * v7.1         : جهت پیمایش آینه‌ای وایبرون/تسلا (آخرین→اولین پیکسل)
 * VERSION      : 7.17 (✅ حالت Polling: POLL_STATE 0x08 — latch آخرین وضعیت بین جاروب‌های 50ms سرویس
 *                 + پوش یدکی پس از 1s بی‌جاروبی · ادغام مجدد پچ‌های QC v7.12: SW-001/002/005/006
 *                 — پایه v7.16: ممیزی تداخل · v7.15: WS2811-LS · v7.14: ضدنویز تاچ)
 *
 * v6.4 CHANGES : + DROP_ARM (0x5A) / DROP_STATUS (0x5B) بازی قطره
 *                + MAX_BUFFER_CAP 64→128 (نوار 100 پیکسلی)
 *                + فیکس هشدارهای W-1..W-6
 * v6.4.1       : هم‌گام‌سازی کامل بازی قطره با مرجع ATmega64:
 *                بدنه 13px + دنباله 7px چهارسطحی، END/START DRAIN،
 *                لمس سطح‌محور، SOFT_ZONE، حلقه چرخان آبی/قرمز
 * v6.5         : حالت‌های گرید ATmega64 → opcodes جدید:
 *                SSS→GRID_SELECT(0x5C) NNN→GRID_LIGHTNING(0x5D)
 *                LLL→GRID_RAIN(0x5E) MMM→GRID_STOP(0x5F) EEE→GRID_ERROR(0x60)
 * v6.6         : سناریو ۱ — SET_KEY_DISPLAY با colorCode=0xFF →
 *                رنگ خودکار پالت از آدرس نود (1 + addr%6)
 * v6.7         : بلوک‌بندی opcodes + TESLA_START(0x70)/TESLA_STOP(0x71)
 *                DROP→0x72..0x74 ، GRID→0x60..0x63 (legacy تا v7 پذیرفته می‌شود)
 * v6.7.1       : throttle تطبیقی رندر نوارهای بزرگ (نصف‌شدن IRQ-off)
 *                + گارد EEPROM در SET_BRIGHTNESS (بدون تغییر رفتار پروتکل)
 * v6.8         : taskAutoDiscovery هنگام فعال‌بودن باس به تعویق می‌افتد —
 *                ریشه‌ی تصادم نودهای بی‌آدرس با ترافیک OTA (رفع مکث ۳s سرویس)
 * v6.9         : مشخصه فیدبک لمس — سون‌سگمنت: لمس → «00» ولی حلقه"
 *                روشن می‌ماند تا دستور جدید (طبق سناریوی کاربر)
 * v6.10        : فیدبک لمس هر ۴ سناریو: ۱و۲=چشمک «00»+حلقه (2×150ms)
 *                ۳=فلاش CH2 در HIT | ۴=فلاش CH2 سفید(HIT)/قرمز(MISS)
 * v6.11        : سناریو۱: 0x5A KEY_RING_RGB [key r g b] انحصاری + فیدبک لمس
 *                fade-out 250ms | سناریو۳: PIXELS_TIMER=20 | Legacy حذف شد
 *
 * BASED ON     : ESP8266 Firmware v5.0
 * EXTENDED WITH: ATmega64 "game_grid" pixel logic  (L / S / E / NNN / RRR)
 *
 * PIN MAP:
 *  GPIO2  - CH2: Seven-segment ONES digit (7px) - UART1
 *  GPIO13 - CH1: Seven-segment TENS digit (7px) / Timer bar - bitbang
 *  GPIO0  - CH3: Key LEDs (4px) - bitbang
 *  GPIO4  - Touch sensor CH1 (Key 1)
 *  GPIO5  - Touch sensor CH0 (Key 0)
 *  GPIO12 - Motion sensor 1
 *  GPIO14 - Motion sensor 2
 *  GPIO15 - RS485 DE/RE
 * ============================================================================
 */

#include <Arduino.h>
#include <EEPROM.h>
#include <ESP8266WiFi.h>
#include <Updater.h>
#include <string.h>

extern "C" {
#include <user_interface.h>
#include <eagle_soc.h>
}

// ============================================================================
// PIXEL COUNTS
// ============================================================================
#define PIXELS_CH1       7
#define PIXELS_CH2       7
#define PIXELS_CH3       4
#define PIXELS_TIMER     20   // ✅ v6.11 سناریو ۳: نوار تایمر گرید = ۲۰ پیکسل (SET_TIMER_COUNT قابل تغییر)

// ============================================================================
// HARDWARE & PROTOCOL CONSTANTS
// ============================================================================
#define FW_MAJOR 7
#define FW_MINOR 17    // ✅ v7.17 POLL: POLL_STATE 0x08 + latch + fallback | v7.12 QC: SW-001/002/005/006 (ادغام مجدد) — پایه: v7.9 K4=چپ/K1=راست + فید 100ms

#define PIN_DE_RE        15
#define PIN_LED_CH1      13
#define PIN_LED_CH2      2
#define PIN_LED_CH3      0

#define PIN_TOUCH_CH0    5
#define PIN_TOUCH_CH1    4
// ⚠️ (W-5) برای فعال‌سازی Key 1 روی GPIO4 این سه مورد را «همزمان» تغییر دهید:
//   1) TOUCH_CHANNELS → 2      2) MAX_KEYS → 2
//   3) ledCount3 باید 8 شود (4 پیکسل به‌ازای هر کلید) → SET_PIXEL_COUNT
// فعلاً Key 1 غیرفعال است چون سخت‌افزار فعلی فقط 4 پیکسل دور یک کلید دارد.
#define TOUCH_CHANNELS   1

#define PIN_MOTION_1     12
#define PIN_MOTION_2     14

#define PIN_ADC          A0
// ✅ v6.4: 64→128 — پشتیبانی نوار 100 پیکسلی (بازی قطره / نوار تایمر)
// اثر: ~+3KB BSS (بافرها/بکاپ‌ها) — روی ESP8266 مشکلی ایجاد نمی‌کند
#define MAX_BUFFER_CAP   128

#define UART_BAUD        115200
#define FRAME_START      0xAA
#define ADDR_BROADCAST   0xFF
#define ADDR_MASTER      0x00
#define RX_BUF_SIZE      320

#define TX_HOLD_US       120
#define RX_HOLD_US       120
#define PKT_TIMEOUT_MS   100UL
#define DISC_INTERVAL_MS 5000UL
#define ADC_POLL_MS      100UL
#define ADC_THRESHOLD    8
#define OTA_IDLE_MS      180000UL
#define OTA_PKT_MS       30000UL

// ============================================================================
// SEVEN-SEGMENT & KEY CONSTANTS
// ============================================================================
#define MAX_DISPLAYS      1
#define MAX_KEYS          1
#define PIXELS_PER_KEY    4
#define SEGS_PER_DIGIT    7
#define NUM_DISPLAYS      MAX_DISPLAYS
#define NUM_KEYS          MAX_KEYS

// ============================================================================
// EFFECT TIMING CONSTANTS
// ============================================================================
#define ERROR_DURATION_MS    200UL
#define PULSE_CYCLE_MS       1280UL
#define SPIN_STEP_MS         60UL
#define RAINBOW_STEP_MS      30UL
#define MATRIX_STEP_MS       60UL
#define MATRIX_TAIL_FADE     60

#define FADEOUT_STEP_MS      16UL
#define FADEOUT_STEPS        25
#define FADEIN_STEPS         14

#define LIGHTNING_MIN_FLASH  5
#define LIGHTNING_MAX_FLASH  8
#define LIGHTNING_FLASH_MS   45UL
#define LIGHTNING_GAP_MS     80UL
#define LIGHTNING_PAUSE_MS   900UL

#define RAIN_SPEED_MIN       40UL
#define RAIN_SPEED_MAX       140UL
#define RAIN_TAIL_FADE       100

#define MOTION_MONITOR_TIME   10000UL
#define MOTION_CHECK_INTERVAL 50UL

#define IDLE_KEY_FADE_PERIOD    4000UL
#define IDLE_KEY_FADE_RENDER_MS   32UL
#define IDLE_DROP_INTERVAL    100UL
#define TEST_INTERVAL_MS      500UL

#define TOUCH_BOOT_GRACE_MS   1200UL
#define BOOT_SPIN_MS          2500UL

// ============================================================================
// GAME ENGINE CONSTANTS
// ============================================================================
#define GAME_TIMER_LENGTH      20
#define GAME_PER_PIXEL_MS      30UL
#define GAME_REVERSE_MS        (GAME_PER_PIXEL_MS * GAME_TIMER_LENGTH)
#define GAME_RESET_DISPLAY_MS  80UL
#define GAME_FLASH_MS          80UL
#define GAME_DRIP_STEP_MS      30UL
#define GAME_FAIL_MS           500UL
#define GAME_MAX_SECONDS       99
#define GAME_TIMER_DIM         128
#define GAME_FLASH_LEVEL       40

#define MP_DROPS               3
#define MP_TRAIL               8
#define MP_MIN_SPEED           4
#define MP_MAX_SPEED           10
#define MP_STEP_MS             30UL

#define EQ_STEP_MS             30UL

// ============================================================================
// DROP GAME CONSTANTS (✅ v6.4) — بازی قطره | opcode 0x5A
// ============================================================================
// ============================================================================
// DROP GAME CONSTANTS — بازی قطره | opcode 0x72 (legacy 0x5A)
// هم‌گام با مرجع ATmega64: DROP_LEN=13, TAIL_LEN=7, THRESHOLD=85, SOFT_ZONE=15
// ============================================================================
#define DROP_MAX_DROPS      8       // حداکثر تعداد قطره همزمان (قابل تنظیم از فریم)
#define DROP_MAX_TRAIL      16      // حداکثر طول دنباله (قابل تنظیم از فریم)
#define DROP_MAX_LEN        32      // حداکثر طول بدنه قطره (قابل تنظیم از فریم)
#define DROP_DEF_TRAIL      7       // پیش‌فرض دنباله = TAIL_LEN در ATmega64
#define DROP_DEF_LEN        13      // پیش‌فرض بدنه  = DROP_LEN در ATmega64
#define DROP_DEF_ACTPCT     75      // پیش‌فرض درصد فعال‌سازی (ATmega64: 85)
#define DROP_SOFT_ZONE      15      // fade-in ابتدای حرکت رفت = SOFT_ZONE در ATmega64
#define DROP_MISS_MS        180UL   // فلاش قرمز لمس خارج از پنجره (فیدبک اختیاری)
// ✅ طول ثابت نوار بازی قطره — قطره دقیقاً در همین تعداد پیکسل اجرا می‌شود
#define DROP_BAR_PIXELS     100
static_assert(DROP_BAR_PIXELS >= 2 && DROP_BAR_PIXELS <= MAX_BUFFER_CAP,
              "DROP_BAR_PIXELS must be 2..MAX_BUFFER_CAP");

// ============================================================================
// GRID MODES CONSTANTS (✅ v6.5) — هم‌گام با ATmega64: SSS/NNN/LLL/MMM/EEE
// ============================================================================
#define GRID_RAIN_DROPS_DEF    5       // = RAIN_DROPS در ATmega64
#define GRID_RAIN_TAIL_DEF     2       // = RAIN_TAIL_LEN در ATmega64
#define GRID_RAIN_DROPS_MAX    8
#define GRID_RAIN_TAIL_MAX     8
#define GRID_RAIN_STEP_MS      20UL    // به‌روزرسانی موقعیت قطره‌های باران
#define GRID_RAIN_SPD_MIN      20.0f   // = RAIN_SPEED_MIN (px/s)
#define GRID_RAIN_SPD_MAX      50.0f   // = RAIN_SPEED_MAX
#define GRID_LIGHT_RESET_MS    2000UL  // سیکل رعد سفید
#define GRID_LIGHT_FADE        15      // عرض دنباله رعد
#define GRID_ERR_BLINK_MS      100UL   // = BLINK_HALF در ATmega64
#define GRID_SEL_PERIOD_MS     1200UL  // پریود پالس انتخاب (handle_start_mode)

// ============================================================================
// EEPROM LAYOUT
// ============================================================================
#define EE_MAGIC      0
#define EE_ADDR       1
#define EE_MAC        2
#define EE_BRIGHT1    8
#define EE_BRIGHT2    9
#define EE_CH1_COUNT  10
#define EE_CH2_COUNT  11
#define EE_BRIGHT3    12
#define EE_CH3_COUNT  13
#define EE_TIMER_COUNT 14
#define EE_MIGRATION   15   // ✅ v7.0: نشانگر مهاجرت
#define EE_FOOTMODE     16   // ✅ v7.6: حالت پا (نود ۱۱/۱۲) — ماندگار
#define EEPROM_SIZE   64
#define MAGIC_BYTE    0xB3

// ============================================================================
// COMMAND OPCODES
// ============================================================================
namespace CMD {
    constexpr uint8_t SET_COLOR         = 0x01;
    constexpr uint8_t RESET             = 0x02;
    constexpr uint8_t GET_STATUS        = 0x03;
    constexpr uint8_t PING              = 0x04;
    constexpr uint8_t SET_BRIGHTNESS    = 0x05;
    constexpr uint8_t CLEAR_ALL         = 0x06;
    constexpr uint8_t IDENTIFY          = 0x07;
    constexpr uint8_t POLL_STATE        = 0x08;   // ✅ v7.17 POLL: جاروب سرویس — پاسخ = snapshot آخرین وضعیت (18B)
    constexpr uint8_t TOUCH_EVENT       = 0x10;
    constexpr uint8_t ADC_EVENT         = 0x11;
    constexpr uint8_t MOTION_EVENT      = 0x12;
    constexpr uint8_t GAME_EVENT        = 0x13;
    constexpr uint8_t DISCOVERY_REQ     = 0x20;
    constexpr uint8_t DISCOVERY_RES     = 0x21;
    constexpr uint8_t SET_ADDRESS       = 0x22;
    constexpr uint8_t ADDRESS_ACK       = 0x23;
    constexpr uint8_t CLEAR_ADDRESS     = 0x24;
    constexpr uint8_t SET_PIXEL_COLOR   = 0x25;
    constexpr uint8_t SET_CHANNEL_COLOR = 0x26;
    constexpr uint8_t PIXEL_EFFECT      = 0x27;
    constexpr uint8_t STAGE_LOCK        = 0x28;
    constexpr uint8_t STAGE_UNLOCK      = 0x29;
    constexpr uint8_t SET_PIXEL_COUNT   = 0x40;
    // ── بلوک 0x4x: بازی اکشن (حلقه کلید RGB) ✅ v7.0 ──
    constexpr uint8_t ACTION_RING     = 0x42;   // [key][r][g][b]
    constexpr uint8_t ACTION_RING_OFF = 0x43;   // [key]
    constexpr uint8_t ACTION_FOOT_MODE = 0x44;
    constexpr uint8_t ACTION_STAGE_RGB = 0x45;  // ✅ v7.10: [R G B] — ۳۳px دور استیج (نود ۱۱) — ثابت تا دستور جدید  // ✅ v7.6: [enable] — نود پا (CH1=چپ16px, CH2=راست16px)
    constexpr uint8_t ENTER_BOOT        = 0x30;
    constexpr uint8_t BOOT_ACK          = 0x31;
    constexpr uint8_t UPDATE_START      = 0xA0;
    constexpr uint8_t UPDATE_DATA       = 0xA1;
    constexpr uint8_t UPDATE_END        = 0xA2;
    constexpr uint8_t UPDATE_ACK        = 0xA3;
    constexpr uint8_t BOOT_ERROR        = 0xA4;
    constexpr uint8_t ABORT_BOOT        = 0xA5;
    constexpr uint8_t ACK               = 0xAA;
    constexpr uint8_t NACK              = 0xBB;
    constexpr uint8_t SET_KEY_DISPLAY   = 0x50;
    constexpr uint8_t MOTION_MONITOR_1  = 0x54;
    constexpr uint8_t MOTION_MONITOR_2  = 0x55;
    constexpr uint8_t GAME_ARM          = 0x51;
    constexpr uint8_t GAME_START        = 0x52;
    constexpr uint8_t GAME_FAIL         = 0x53;
    constexpr uint8_t GAME_CANCEL       = 0x56;
    constexpr uint8_t GAME_WAIT         = 0x57;
    constexpr uint8_t GAME_STATUS       = 0x58;
    constexpr uint8_t SELF_TEST         = 0x59;
    constexpr uint8_t HEARTBEAT         = 0x14;
    constexpr uint8_t TIMER_KEY_START   = 0x65;
    constexpr uint8_t TIMER_STOP        = 0x66;
    constexpr uint8_t SET_TIMER_COUNT   = 0x67;
    // ================================================================
    // ✅ v6.7: بلوک‌بندی opcodes بر اساس سناریو:
    //   0x50-0x5F → سون‌سگمنت + دور کلید        (سناریو 1 و 2)
    //   0x60-0x6F → گرید: تایمر + کلید + فعال‌سازی (سناریو 3)
    //   0x70-0x7F → تسلا + قطره 100px + فعال‌سازی  (سناریو 4)
    // ================================================================
    // — بلوک 0x6x: گرید/تایمر —
    constexpr uint8_t GRID_ERROR     = 0x60;   // ≡ EEE — چشمک قرمز خطا
    constexpr uint8_t GRID_SELECT    = 0x61;   // ≡ SSS — فعال‌سازی/انتخاب کلید
    constexpr uint8_t GRID_RAIN      = 0x62;   // ≡ LLL — باران
    constexpr uint8_t GRID_STOP      = 0x63;   // ≡ MMM — توقف هر حالت گرید/تسلا
    constexpr uint8_t VIBRON_RING   = 0x64;   // ✅ v7.0 وایبرون — فعال‌سازی حلقه (بعد از تایمر) [key][r][g][b]
    // — بلوک 0x7x: تسلا + قطره —
    constexpr uint8_t TESLA_START    = 0x70;   // ≡ NNN — رعد/تسلا سفید
    constexpr uint8_t TESLA_STOP     = 0x71;   // توقف تسلا
    constexpr uint8_t DROP_ARM       = 0x72;   // بازی قطره (نوار 100px)
    constexpr uint8_t DROP_STATUS    = 0x73;   // وضعیت قطره
    constexpr uint8_t DROP_CANCEL    = 0x74;   // لغو قطره
    // (0x5A در v7.0 به ACTION_RING=0x42 منتقل شد)
}

namespace GEVT {
    constexpr uint8_t KEY_HIT     = 0x01;
    constexpr uint8_t TIMEOUT     = 0x02;
    constexpr uint8_t WRONG_KEY   = 0x03;
    constexpr uint8_t SUCCESS_END = 0x04;
    constexpr uint8_t FAIL_END    = 0x05;
    constexpr uint8_t ARMED       = 0x06;
    constexpr uint8_t STARTED     = 0x07;
    constexpr uint8_t CANCELED    = 0x08;
    // ✅ v6.4 NEW: رویدادهای بازی قطره (payload رویداد GAME_EVENT)
    constexpr uint8_t DROP_ARMED    = 0x10;  // بازی مسلح/شروع شد
    constexpr uint8_t DROP_ACTIVE   = 0x11;  // کلید فعال شد (پنجره لمس باز است)
    constexpr uint8_t DROP_HIT      = 0x12;  // لمس در پنجره فعال ✅
    constexpr uint8_t DROP_MISS     = 0x13;  // لمس خارج از پنجره فعال ❌
    constexpr uint8_t DROP_TIMEOUT  = 0x14;  // پایان رفت‌وبرگشت بدون لمس
    constexpr uint8_t DROP_END      = 0x15;  // پایان بصری بازی
    constexpr uint8_t DROP_CANCELED = 0x16;  // لغو توسط master
    // ✅ v6.5 NEW: رویدادهای گرید
    constexpr uint8_t GRID_KEY_DONE = 0x17;  // کلیدِ نودِ انتخاب‌شده فشرده شد (≡ start mode)
}

// ============================================================================
// ENUMS & STRUCTS
// ============================================================================
constexpr uint8_t  TOUCH_PRESSED        = 0x00;
constexpr uint8_t  TOUCH_RELEASED       = 0x11;
constexpr uint8_t  TOUCH_LONG           = 0x22;
constexpr uint16_t TOUCH_LONG_PRESS_MS  = 1000;

constexpr uint8_t ERR_INVALID_PARAM = 0x01;
constexpr uint8_t ERR_OUT_OF_BOUNDS = 0x02;
constexpr uint8_t ERR_STAGE_LOCKED  = 0x03;
constexpr uint8_t ERR_NOT_ARMED     = 0x04;
constexpr uint8_t ERR_AUTH_FAILED   = 0x11;

const uint8_t OTA_PASSWORD[4] = {0xDE, 0xAD, 0xBE, 0xEF};

enum class SysState : uint8_t {
    RAW, NO_ADDR, BOOT, ONLINE, IDENTIFY
};

enum class EffectType : uint8_t {
    NONE       = 0x00,
    RAINBOW    = 0x01,
    SPIN       = 0x02,
    PULSE      = 0x03,
    ERROR      = 0x04,
    FADE_OUT   = 0x05,
    MATRIX     = 0x06,
    LIGHTNING  = 0x07,
    RAIN       = 0x08,
    IDLE       = 0x09,
    TEST       = 0x0A,
    MATRIX_PRO = 0x0B,
    EQUALIZER  = 0x0C,
    SELF_TEST  = 0x0D,
    // ✅ v6.5: حالت‌های گرید (داخلی — از PIXEL_EFFECT قابل انتخاب نیستند)
    GRID_LIGHTNING = 0x0E,   // ≡ TESLA_START (0x70)
    GRID_RAIN      = 0x0F,   // ≡ LLL
    GRID_ERROR     = 0x10,   // ≡ EEE
    GRID_SELECT    = 0x11,   // ≡ SSS
    GAME       = 0x20
};

enum class GameState : uint8_t {
    IDLE      = 0,
    ARMED     = 1,
    ACTIVE    = 2,
    HIT_BLANK = 3,
    HIT_FLASH = 4,
    REVERSE   = 5,
    FAIL      = 6
};

struct Pixel { uint8_t r, g, b; };

const uint8_t sevenSegmentDigits[10] = {
    0b00111111, 0b00000110, 0b01011011, 0b01001111, 0b01100110,
    0b01101101, 0b01111101, 0b00000111, 0b01111111, 0b01101111
};

// v6.0 FIXED: پالت رنگ دقیقاً مطابق 0xRRGGBB
const uint32_t colorPalette[7] = {
    0x000000, // 0: Off
    0x0000FF, // 1: Blue
    0xFFFF00, // 2: Yellow
    0xFF3C64, // 3: Pink
    0xFF3C00, // 4: Orange
    0xFFFFFF, // 5: White
    0xFF0000  // 6: Red
};

static inline void paletteRGB(uint32_t color, uint8_t& r, uint8_t& g, uint8_t& b) {
    r = (uint8_t)((color >> 16) & 0xFF);
    g = (uint8_t)((color >>  8) & 0xFF);
    b = (uint8_t)( color        & 0xFF);
}

// ============================================================================
// GLOBAL STATE
// ============================================================================
static Pixel   ledBuf1[MAX_BUFFER_CAP];
static Pixel   ledBuf2[MAX_BUFFER_CAP];
static Pixel   ledBuf3[MAX_BUFFER_CAP];
static uint8_t ledCount1  = PIXELS_CH1;
static uint8_t ledCount2  = PIXELS_CH2;
static uint8_t ledCount3  = PIXELS_CH3;
static uint8_t ledCountTimer = PIXELS_TIMER;
static uint8_t ledBright1 = 128;
static uint8_t ledBright2 = 128;
static uint8_t ledBright3 = 128;

static volatile bool g_otaActive   = false;
static bool          g_userLedLock = false;
static bool          g_stageLocked = false;
static uint8_t       g_touchSeqNum = 0;
static uint32_t      g_discMs      = 0;
static uint32_t g_lastMasterRxMs = 0;
static bool     g_linkLost       = false;
static uint32_t g_lastHbCycle    = 0xFFFFFFFFUL;
static uint8_t  g_hbSeq          = 0;
static bool          g_discReplyPending = false;
static uint32_t      g_discReplyAtMs    = 0;
static bool          g_addrAckPending   = false;
static uint32_t      g_addrAckAtMs      = 0;
static uint8_t       g_addrAckValue     = 0;

static EffectType g_activeEffect   = EffectType::NONE;
static uint32_t   g_effectStartMs  = 0;
static uint32_t   g_lastFxRenderMs = 0;

static uint8_t g_effectColorR = 255;
static uint8_t g_effectColorG = 255;
static uint8_t g_effectColorB = 255;

struct ChannelFade {
    bool     active     = false;
    bool     returning  = false;
    uint8_t  step       = 0;
    Pixel    origin[MAX_BUFFER_CAP];
    uint32_t lastTick   = 0;
};
static ChannelFade g_fadeCh1;
static ChannelFade g_fadeCh2;

static uint8_t  g_matrixBrt1[MAX_BUFFER_CAP];
static uint8_t  g_matrixBrt2[MAX_BUFFER_CAP];
static int8_t   g_matrixHead1    = -1;
static int8_t   g_matrixHead2    = -1;
static uint32_t g_matrixNextDrop = 0;

static uint8_t  g_lightFlashTotal   = 0;
static uint8_t  g_lightFlashDone    = 0;
static bool     g_lightOn           = false;
static uint32_t g_lightNextEvent    = 0;

struct RainDrop {
    int8_t   pos;
    uint8_t  brt;
    uint32_t nextMs;
    uint16_t speed;
};
static RainDrop g_rain1[MAX_BUFFER_CAP];
static RainDrop g_rain2[MAX_BUFFER_CAP];
static uint8_t  g_rainTail1[MAX_BUFFER_CAP];
static uint8_t  g_rainTail2[MAX_BUFFER_CAP];
static uint32_t g_rainNextSpawn = 0;

static uint8_t  g_activeMotionSensor = 0;
static uint32_t g_motionMonitorStartMs = 0;
static uint32_t g_lastMotionCheckMs = 0;
static bool     g_motionDetected = false;
static bool     g_motionLastReported = false;

static uint32_t g_lastIdleDropMs = 0;
static uint32_t g_lastIdleKeyFadeMs = 0;
static uint8_t  g_idleDropFrame = 0;

static uint32_t g_lastTestTime = 0;
static uint8_t  g_testNumber = 0;

static uint32_t g_touchFeedbackStartMs = 0;
static bool     g_touchFeedbackActive  = false;
static uint8_t  g_touchFeedbackKey     = 0;

static Pixel   ledBuf1Backup[MAX_BUFFER_CAP];
static Pixel   ledBuf2Backup[MAX_BUFFER_CAP];
static Pixel   ledBuf3Backup[MAX_BUFFER_CAP];
static bool    g_errorBackedUp = false;

#define AUTO_IDLE_TIMEOUT_MS  30000UL
static uint32_t g_lastCommandMs = 0;

static uint32_t g_bootMs = 0;
static uint8_t  g_bootCount   = 0;
static uint8_t  g_resetReason = 0;
static bool     g_bootSpinActive = false;
static uint32_t g_bootSpinStartMs = 0;

static volatile bool g_commandProcessing = false;

// ---------------------------------------------------------------------------
// GAME STATE
// ---------------------------------------------------------------------------
static GameState g_gameState        = GameState::IDLE;
static uint8_t   g_gameKey          = 0;
static uint32_t  g_gameColor = 0x0000FF;   // ✅ v7.0: RGB
static uint16_t  g_gameTotalSec     = 0;
static uint32_t  g_gameTotalMs      = 0;
static uint32_t  g_gameRemainMs     = 0;
static uint32_t  g_gameLastTickMs   = 0;
static uint32_t  g_gamePhaseMs      = 0;
static uint8_t   g_gameDripPos      = 0;
static uint32_t  g_gameLastDripMs   = 0;
static int16_t   g_gameLastShownSec = -1;
static uint8_t   g_gameSeq          = 0;
static bool      g_gameEngaged      = false;

struct MpDrop {
    int32_t  pos_q8;
    int32_t  speed_q8;
    uint8_t  flicker;
    uint8_t  variant;
};
static MpDrop   g_mpDrop[MP_DROPS];
static bool     g_mpInit      = false;
static uint32_t g_mpLastMs    = 0;
static uint16_t g_mpFrame     = 0;

static uint8_t  g_eqHeight    = 0;
static uint8_t  g_eqDir       = 1;
static uint8_t  g_eqPhase     = 0;
static uint8_t  g_eqDripPos   = 0;
static uint8_t  g_eqDripColor = 1;
static uint32_t g_eqLastMs    = 0;

static uint32_t g_rngState = 0x1234ABCDUL;
static inline uint32_t rng32() {
    g_rngState ^= g_rngState << 13;
    g_rngState ^= g_rngState >> 17;
    g_rngState ^= g_rngState << 5;
    return g_rngState;
}
static inline uint8_t rng8(uint8_t lo, uint8_t hi) {
    if (hi <= lo) return lo;
    return (uint8_t)(lo + (rng32() % (uint32_t)(hi - lo + 1)));
}

// ============================================================================
// TIMER BAR STATE (v5.6)
// ============================================================================
enum class Ch1Mode : uint8_t {
    SEVENSEG = 0,
    TIMERBAR = 1
};
static Ch1Mode g_ch1Mode = Ch1Mode::SEVENSEG;

static bool     g_tmrActive     = false;
static uint8_t  g_tmrKey        = 0;
static uint32_t g_tmrColor     = 0x0000FF;   // ✅ v7.0: RGB مستقیم (بدون پالت)
static uint32_t g_tmrRingColor = 0x0000FF;   // ✅ v7.0: رنگ حلقه — با 0x64
static uint16_t g_tmrTotalSec   = 0;
static uint32_t g_tmrTotalMs    = 0;
static uint32_t g_tmrRemainMs   = 0;
static uint32_t g_tmrLastTickMs __attribute__((unused)) = 0;
static int16_t  g_tmrLastLit __attribute__((unused)) = -1;
static bool     g_tmrKeyArmed   = false;
static uint8_t  g_tmrSeq        = 0;

// ✅ v6.2: ماشین حالت تایمر — fade in/out تدریجی + reverse نرم
enum class TmrPhase : uint8_t {
    IDLE         = 0,   // غیرفعال
    RUNNING      = 1,   // fade in تدریجی نوار + کلید + شمارش
    HIT_FLASH    = 2,   // فلاش سفید پیکسل‌های کلید (100ms)
    REVERSE      = 3,   // نوار نرم خالی می‌شود (آخر→اول) + کلید fade out
    TIMEOUT_FADE = 4    // همه fade out بعد از timeout
};
static TmrPhase  g_tmrPhase      = TmrPhase::IDLE;
static uint32_t  g_tmrStartMs    = 0;   // زمان شروع تایمر (لحظه 0x65)
static uint32_t  g_tmrHitMs      = 0;   // زمان شروع HIT_FLASH
static uint32_t  g_tmrRevMs      = 0;   // زمان شروع REVERSE
static uint32_t g_tmrFadeMs     = 0;   // زمان شروع TIMEOUT_FADE
static uint16_t g_tmrRevLitPx   = 0;   // تعداد پیکسل روشن در لحظه شروع reverse
static uint32_t g_tmrLastRenderMs = 0;  // ✅ v6.3: throttle رندر نوار تایمر

// ✅ v6.3: flag برای SET_KEY_DISPLAY — تاچ → نمایش "00"
static bool     g_keyDisplayActive = false;
static uint8_t  g_keyDisplayNum    = 0;
static uint32_t g_keyDisplayColor  = 0;

// ✅ v7.0 اکشن (0x42): حلقه کلید RGB — لمس → فید‌اوت 180ms → بازگشت به رنگ
static bool     g_keyRingRgbActive = false;
static uint8_t  g_keyRingRgbPhase = 0;      // 0=ثابت 1=فید‌اوت 2=فید‌این
static uint8_t  g_keyRingRgbKey = 0;
static uint8_t  g_keyRingRgbR = 0, g_keyRingRgbG = 0, g_keyRingRgbB = 0;
static uint32_t g_keyRingRgbFadeMs = 0;
#define ACTION_FADE_MS 100UL   // ✅ v7.9: فید‌اوت 100ms → فیداین 100ms (کاربر: قابل‌لمس‌تر) — اکشن + پا

// ============================================================================
// ✅ v7.6: حالت پا — نود ۱۲ (استثنای اکشن): CH1=پای چپ 16px، CH2=پای راست 16px
// تاچ = BS814A-2 روی I2C (SDA=GPIO5, SCL=GPIO4) — کلید۱=چپ، کلید۴=راست
// نود ۱۱: بدون تغییر رفتاری (CH3=حلقه عادی، CH1=۳۳px تزئینی دور استیج)
// ============================================================================
#define FOOT_PIXELS     16
#define STAGE_PIXELS    33   // ✅ v7.10: نود ۱۱ — CH1 دور استیج (نشان‌گذار جایگاه بازیکن، ثابت)
#define BS814A_KEY1_BIT 0x01   // پد راست (K1)
#define BS814A_KEY4_BIT 0x08   // پد چپ (K4)
#ifndef RBUS_HOST_SIM
  // ✅ v7.7: پروتکل اختصاصی Holtek BS81xA (از کد مرجع اثبات‌شده کاربر) — نه I2C!
  // GPIO5=کلاک (مستر=میکرو، idle HIGH) · GPIO4=دیتا (ورودی) · LSB اول
  // فریم: bit7=Stop(1) | bit6..4=Checksum(=تعداد لمس‌ها) | bit3..0=K4..K1 (0=لمس)
  #define BS_CLK_PIN        5
  #define BS_DATA_PIN       4
  #define BS_T_LOW_US       20
  #define BS_T_HIGH_US      20
  #define BS_RETRY_GAP_US   200
  #define BS_READ_RETRIES   4
  #define BS_FRAME_SCAN_BITS 10
#endif
static bool     g_footMode = false;        // از EEPROM (EE_FOOTMODE)
static bool     g_footActive[2]  = {false, false};
static uint8_t  g_footPhase[2]   = {0, 0};   // 0=ثابت 1=فید‌اوت 2=فیداین
static uint8_t  g_footR[2] = {0}, g_footG[2] = {0}, g_footB[2] = {0};
static uint32_t g_footFadeMs[2]  = {0, 0};

#ifdef RBUS_HOST_SIM
extern uint8_t SIM_BS814A_KEYS;
static inline void bs814a_init() {}   // stub میزبان                  // شبیه‌ساز: بیت‌های کلید
static inline uint8_t bs814a_read_keys() { return SIM_BS814A_KEYS; }
#else
// ═══ ✅ v7.7: درایور BS814A-2 — پورت مستقیم از کد مرجع اثبات‌شده کاربر ═══
static uint32_t g_bsGoodFrames = 0, g_bsReadErrors = 0;

static void bs814a_init() {
    pinMode(BS_CLK_PIN, OUTPUT);
    digitalWrite(BS_CLK_PIN, HIGH);   // ایسل = HIGH
    pinMode(BS_DATA_PIN, INPUT);
}

static void bs_readRawStream(uint8_t numBits, uint8_t* out) {
    digitalWrite(BS_CLK_PIN, HIGH);
    delayMicroseconds(BS_T_HIGH_US);
    for (uint8_t i = 0; i < numBits; i++) {
        digitalWrite(BS_CLK_PIN, LOW);
        delayMicroseconds(BS_T_LOW_US / 2);
        out[i] = (digitalRead(BS_DATA_PIN) == HIGH) ? 1 : 0;
        delayMicroseconds(BS_T_LOW_US / 2);
        digitalWrite(BS_CLK_PIN, HIGH);
        delayMicroseconds(BS_T_HIGH_US);
    }
}

static uint8_t bs_bitsToByte(const uint8_t* bits, uint8_t start) {
    uint8_t v = 0;
    for (uint8_t i = 0; i < 8; i++)
        if (bits[start + i]) v |= (1 << i);
    return v;
}

static bool bs_isValidFrame(uint8_t raw) {
    if (!(raw & 0x80)) return false;              // Stop=1
    uint8_t touched = 0;
    for (uint8_t i = 0; i < 4; i++)
        if ((raw & (1 << i)) == 0) touched++;     // 0=لمس
    return (((raw >> 4) & 0x07) == touched);
}

// ماسک کلیدهای لمس‌شده (K1=bit0..K4=bit3) — 0 اگر فریم معتبر نیامد
static uint8_t bs814a_read_keys() {
    uint8_t bits[BS_FRAME_SCAN_BITS];
    for (uint8_t attempt = 0; attempt < BS_READ_RETRIES; attempt++) {
        bs_readRawStream(BS_FRAME_SCAN_BITS, bits);
        for (uint8_t w = 0; w + 8 <= BS_FRAME_SCAN_BITS; w++) {
            uint8_t b = bs_bitsToByte(bits, w);
            if (bs_isValidFrame(b)) { g_bsGoodFrames++; return (uint8_t)(~b & 0x0F); }
        }
        delayMicroseconds(BS_RETRY_GAP_US);
    }
    g_bsReadErrors++;
    return 0;
}
#endif
static void ws_show_strip(uint8_t pin, const Pixel* buf, uint8_t count, uint8_t brt);   // fwd

static void foot_onKeyPress(uint8_t side) {
    if (side > 1 || !g_footActive[side] || g_footPhase[side] != 0) return;
    g_footPhase[side]  = 1;        // فید‌اوت 200ms — مثل اکشن
    g_footFadeMs[side] = millis();
}

static void taskFoot() {
    if (g_otaActive || !g_footMode) return;
    for (uint8_t sd = 0; sd < 2; sd++) {
        if (!g_footActive[sd] || g_footPhase[sd] == 0) continue;
        uint32_t t = millis() - g_footFadeMs[sd];
        uint16_t k = 255;
        if (g_footPhase[sd] == 1) {
            if (t >= ACTION_FADE_MS) { g_footPhase[sd] = 2; g_footFadeMs[sd] = millis(); t = 0; }
            else k = (uint16_t)(255 - t * 255 / ACTION_FADE_MS);
        }
        if (g_footPhase[sd] == 2) {
            if (t >= ACTION_FADE_MS) { g_footPhase[sd] = 0; k = 255; }
            else k = (uint16_t)(t * 255 / ACTION_FADE_MS);
        }
        // ✅ v7.11 (تایید کاربر): چپ(sd=0)→CH2/GPIO2 · راست(sd=1)→CH1/GPIO13
        Pixel* buf = sd ? ledBuf1 : ledBuf2;
        for (uint8_t i = 0; i < FOOT_PIXELS; i++) {
            buf[i].r = (uint8_t)((uint16_t)g_footR[sd] * k / 255);
            buf[i].g = (uint8_t)((uint16_t)g_footG[sd] * k / 255);
            buf[i].b = (uint8_t)((uint16_t)g_footB[sd] * k / 255);
        }
        ws_show_strip(sd ? PIN_LED_CH1 : PIN_LED_CH2, buf, FOOT_PIXELS,
                      sd ? ledBright1 : ledBright2);
    }
}
// ✅ v7.0: شناسایی نود — قابل مشاهده برای کاربر
static uint32_t g_identGreenUntilMs = 0;
// ✅ v6.10: فلاش CH2 برای قطره (سناریو ۴) — HIT سفید / MISS قرمز
static uint32_t g_dropCh2FlashMs  = 0;
static uint32_t g_dropCh2FlashDur = 0;
static uint8_t  g_dropCh2R = 0, g_dropCh2G = 0, g_dropCh2B = 0;

// زمان‌بندی انیمیشن‌های تایمر
#define TMR_PIXEL_FADEIN_MS   350UL    // مدت fade in هر پیکسل نوار (نرم‌تر)
#define TMR_KEY_FADEIN_MS     800UL    // مدت fade in پیکسل‌های کلید (نرم‌تر)
#define TMR_HIT_FLASH_MS      250UL    // مدت فلاش بعد از لمس (قابل مشاهده‌تر)
#define TMR_REVERSE_PER_PX_MS 10UL     // فاصله خاموشی هر پیکسل در reverse (سریع‌تر)
#define TMR_PIXEL_FADEOUT_MS  200UL    // مدت fade out هر پیکسل
#define TMR_KEY_FADEOUT_MS    500UL    // مدت fade out پیکسل‌های کلید
#define TMR_TIMEOUT_FADE_MS   800UL    // مدت fade out بعد از timeout

// ============================================================================
// LINK SUPERVISION
// ============================================================================
#define HEARTBEAT_ENABLE      1
#define HEARTBEAT_PERIOD_MS   2000UL
#define HEARTBEAT_SLOT_MS     4UL
#define HEARTBEAT_MAX_NODES   64
#define LINK_LOST_MS          10000UL
#define REQUIRE_SERVICE_COMMAND   1

static volatile bool g_inDispatch = false;

// Forward declarations
static void comm_pump();
static void ws_boot_safe_before_restart();
static void ws_detach_uart1();
static void led_show(uint8_t ch);
static void led_setAll(Pixel* buf, uint8_t count, uint8_t r, uint8_t g, uint8_t b);
static void led_setPixel(Pixel* buf, uint8_t idx, uint8_t count,
                          uint8_t r, uint8_t g, uint8_t b);
static void led_clear(uint8_t ch);
static void effect_stop();
static void startChannelFade(uint8_t chNum);
static void startSpinEffect();
static void updateChannelFades(uint32_t now);
static void ws_show_strip(uint8_t pin, const Pixel* buf, uint8_t count, uint8_t brt);
static void exitIdleMode();
static bool game_onKeyPress(uint8_t keyNum);
static bool tmr_onKeyPress(uint8_t keyNum);
static void game_cancel(bool silent);
static void taskGame();
// ✅ v6.4: بازی قطره
static bool drop_onKeyPress(uint8_t keyNum);
static void drop_cancel(bool silent);
static void taskDrop();
static uint8_t touchHeldMask();   // ✅ v6.4.1: وضعیت لحظه‌ای کلیدها (لمس سطح‌محور)
// ✅ v6.5: حالت‌های گرید
static void grid_cancel(bool silent);
static bool grid_onSelectPress(uint8_t keyNum);
static void taskGrid();
static void keyRingRgbCancel();  // ✅ v6.11: سناریو ۱

// ============================================================================
// ✅ v7.15: انتخابگر پروتکل نوار — پیش‌فرض WS2812B (800kbps)
// نوارهای «WS2811» (به‌خصوص ۱۲ ولت، حالت کم‌سرعت 400kbps) تایمینگ کندتر و لچ
// بلندتر می‌خواهند؛ با WS2812B-تایمینگ بیت‌ها اشتباه لچ می‌شوند → رنگ تصادفی/نویز.
// فعال‌سازی: در این فایل =1 کنید یا در فلاگ‌های بیلد: -DLED_PROTO_WS2811_LS=1
// ============================================================================"
#ifndef LED_PROTO_WS2811_LS
#define LED_PROTO_WS2811_LS 0
#endif
#if LED_PROTO_WS2811_LS
  // ✅ v7.16 — کالیبره با دیتاشیت رسمی Worldsemi WS2811 (Low-Speed 400kbps):
  // T0H=0.5µs T1H=1.2µs T0L=2.0µs T1L=1.3µs (±150ns) · بیت=2.5µs · RES>50µs
  // مقیاس واحد: از ثابت‌های اثبات‌شده WS2812B (t0h=14≈350ns) → 1 واحد ≈ 25ns
  #define WS_T0H_80  20    // 20×25ns = 500ns  ✅ v7.15 دوبار بزرگ بود (40)
  #define WS_T0L_80  80    // 2000ns           ✅ (بود 160)
  #define WS_T1H_80  48    // 1200ns           ✅ (بود 96)
  #define WS_T1L_80  52    // 1300ns           ✅ (بود 104)
  #define LED_LATCH_US 300UL   // دیتاشیت: >50µs — محافظه‌کار برای نوار ۵متری
#else
  // WS2812B — 800kbps، بیت 1.25µs (مقادیر قبلی، دست‌نخورده)
  #define WS_T0H_80  14
  #define WS_T0L_80  40
  #define WS_T1H_80  34
  #define WS_T1L_80  28
  #define LED_LATCH_US 80UL
#endif

// ============================================================================
// WS2812B DRIVER
// ============================================================================
static void IRAM_ATTR ws_delay_cycles(uint32_t cycles) {
    uint32_t n = cycles / 4;
    while (n--) asm volatile("nop");
}

static void IRAM_ATTR ws_sendByte(uint8_t pin, uint8_t val, bool cpu80) {
    // ✅ v7.15: تایمینگ از انتخابگر پروتکل (WS2812B پیش‌فرض / WS2811-LS)
    const uint32_t t0h  = cpu80 ? WS_T0H_80 : (WS_T0H_80 * 2);
    const uint32_t t0l  = cpu80 ? WS_T0L_80 : (WS_T0L_80 * 2);
    const uint32_t t1h  = cpu80 ? WS_T1H_80 : (WS_T1H_80 * 2);
    const uint32_t t1l  = cpu80 ? WS_T1L_80 : (WS_T1L_80 * 2);
    const uint32_t mask = 1UL << pin;
    for (int8_t bit = 7; bit >= 0; bit--) {
        if (val & (1 << bit)) {
            GPIO_REG_WRITE(GPIO_OUT_W1TS_ADDRESS, mask);
            ws_delay_cycles(t1h);
            GPIO_REG_WRITE(GPIO_OUT_W1TC_ADDRESS, mask);
            ws_delay_cycles(t1l);
        } else {
            GPIO_REG_WRITE(GPIO_OUT_W1TS_ADDRESS, mask);
            ws_delay_cycles(t0h);
            GPIO_REG_WRITE(GPIO_OUT_W1TC_ADDRESS, mask);
            ws_delay_cycles(t0l);
        }
    }
}

#ifdef RBUS_HOST_SIM
// ✅ تست میزبان: به‌جای رجیسترهای سخت‌افزاری UART1 (آدرس‌های غیرمجاز روی PC)
extern volatile uint32_t SIM_UART1_FIFO, SIM_UART1_STATUS, SIM_UART1_CONF0;
#define UART1_FIFO_REG    SIM_UART1_FIFO
#define UART1_STATUS_REG  SIM_UART1_STATUS
#define UART1_CONF0_REG   SIM_UART1_CONF0
#else
#define UART1_BASE        0x60000F00u
#define UART1_FIFO_REG    (*(volatile uint32_t*)(UART1_BASE + 0x00))
#define UART1_STATUS_REG  (*(volatile uint32_t*)(UART1_BASE + 0x1C))
#define UART1_CONF0_REG   (*(volatile uint32_t*)(UART1_BASE + 0x20))
#endif
#define UART_TXFIFO_CNT_S 16
#define UART_TXD_INV_BIT  (1u << 22)

static bool g_wsPinsReady  = false;
// ✅ v7.14: ضدنویز/ضدتودرتو — پنجره آرام‌شدن تاچ + گارد پوش LED
static uint32_t g_lastNoiseMs = 0;            // آخرین نوشتن LED یا TX رادیو
#define TOUCH_SETTLE_MS 4UL                   // نمونه‌برداری تاچ در این پنجره ممنوع
static volatile bool g_inLedShow = false;     // پوش LED در جریان است؟
static uint8_t  g_ledDirtyCh       = 0;       // 1=CH1 2=CH2 4=CH3 — پوش تأخیری
static uint32_t g_ledNestedDeflects = 0;      // آمار انحراف (تست/تشخیص)
static bool g_ch2UartReady = false;

static void ws_ch2_uart_init() {
    Serial.setDebugOutput(false);
    system_set_os_print(0);
    Serial1.end();
    Serial1.begin(3200000, SERIAL_6N1, SERIAL_TX_ONLY);
    UART1_CONF0_REG |= UART_TXD_INV_BIT;
    delayMicroseconds(300);
    g_ch2UartReady = true;
}

static void ws_ch2_show(const Pixel* buf, uint8_t count, uint8_t brt) {
    if (!g_ch2UartReady) ws_ch2_uart_init();
    static const uint8_t lut[4] = {
        0b110111, 0b000111, 0b110100, 0b000100
    };
    uint8_t grb[MAX_BUFFER_CAP * 3];
    const uint16_t scale = brt;
    for (uint8_t i = 0; i < count; i++) {
        grb[i * 3 + 0] = (uint8_t)((uint16_t)buf[i].g * scale / 255);
        grb[i * 3 + 1] = (uint8_t)((uint16_t)buf[i].r * scale / 255);
        grb[i * 3 + 2] = (uint8_t)((uint16_t)buf[i].b * scale / 255);
    }
    const uint16_t nbytes = (uint16_t)count * 3;
    for (uint16_t i = 0; i < nbytes; i++) {
        uint32_t guard = 0;
        while (((UART1_STATUS_REG >> UART_TXFIFO_CNT_S) & 0xFF) > (128 - 4)) {
            if (++guard > 20000UL) return;
            ESP.wdtFeed();
        }
        const uint8_t sv = grb[i];
        UART1_FIFO_REG = lut[(sv >> 6) & 3];
        UART1_FIFO_REG = lut[(sv >> 4) & 3];
        UART1_FIFO_REG = lut[(sv >> 2) & 3];
        UART1_FIFO_REG = lut[sv & 3];
    }
    uint32_t drain = 0;
    while (((UART1_STATUS_REG >> UART_TXFIFO_CNT_S) & 0xFF) > 0) {
        if (++drain > 40000UL) break;
        ESP.wdtFeed();
    }
    delayMicroseconds(80);
}

static void ws_detach_uart1() {
    Serial.setDebugOutput(false);
    system_set_os_print(0);
    PIN_FUNC_SELECT(PERIPHS_IO_MUX_GPIO0_U, FUNC_GPIO0);
    g_ch2UartReady = false;
}

static void ws_pins_init() {
    ws_detach_uart1();
    pinMode(PIN_LED_CH1, OUTPUT);
    pinMode(PIN_LED_CH3, OUTPUT);
    GPIO_REG_WRITE(GPIO_ENABLE_W1TS_ADDRESS,
        (1UL << PIN_LED_CH1) | (1UL << PIN_LED_CH3));
    digitalWrite(PIN_LED_CH1, LOW);
    digitalWrite(PIN_LED_CH3, LOW);
    ws_ch2_uart_init();
    delayMicroseconds(80);
    g_wsPinsReady = true;
}

static void ws_boot_safe_before_restart() {
    Serial1.end();
    g_ch2UartReady = false;
    pinMode(PIN_LED_CH2, OUTPUT);
    pinMode(PIN_LED_CH3, OUTPUT);
    digitalWrite(PIN_LED_CH2, HIGH);
    digitalWrite(PIN_LED_CH3, HIGH);
    delay(2);
}

static void ws_show_strip(uint8_t pin, const Pixel* buf,
                           uint8_t count, uint8_t brt) {
    if (g_otaActive || count == 0 || count > MAX_BUFFER_CAP) return;
    if (pin != PIN_LED_CH1 && pin != PIN_LED_CH2 && pin != PIN_LED_CH3) return;
    if (!g_wsPinsReady) ws_pins_init();

    if (g_inLedShow) {   // ✅ v7.14: پوش تودرتو (dispatch وسط رندر) → به‌تعویق
        g_ledNestedDeflects++;
        g_ledDirtyCh |= (pin == PIN_LED_CH1) ? 1 : (pin == PIN_LED_CH2) ? 2 : 4;
        return;
    }
    g_inLedShow = true;

#if !LED_PROTO_WS2811_LS
    if (pin == PIN_LED_CH2) {
        ws_ch2_show(buf, count, brt);
        g_lastNoiseMs = millis(); g_inLedShow = false;
        return;
    }
#endif   // WS2811: CH2 هم از بیت‌بنگ عمومی با تایمینگ جدید می‌رود

    const bool     cpu80 = (system_get_cpu_freq() <= 80);
    const uint16_t scale = brt;
    const uint32_t mask  = 1UL << pin;

    GPIO_REG_WRITE(GPIO_OUT_W1TC_ADDRESS, mask);
    delayMicroseconds(LED_LATCH_US);   // ✅ v7.15: لچ مطابق پروتکل

    noInterrupts();
    for (uint8_t i = 0; i < count; i++) {
        const uint8_t g = (uint8_t)((uint16_t)buf[i].g * scale / 255);
        const uint8_t r = (uint8_t)((uint16_t)buf[i].r * scale / 255);
        const uint8_t b = (uint8_t)((uint16_t)buf[i].b * scale / 255);
        ws_sendByte(pin, g, cpu80);
        ws_sendByte(pin, r, cpu80);
        ws_sendByte(pin, b, cpu80);
    }
    GPIO_REG_WRITE(GPIO_OUT_W1TC_ADDRESS, mask);
    interrupts();
    delayMicroseconds(LED_LATCH_US);   // ✅ v7.15: لچ مطابق پروتکل
    g_lastNoiseMs = millis();     // شروع پنجره آرام‌شدن تاچ
    comm_pump();                  // dispatch ممکن است پوش تودرتو بخواهد → deflect
    g_inLedShow = false;          // ✅ v7.14: پایان پوش
}

// ============================================================================
// EEPROM MANAGER
// ============================================================================
class EepromMgr {
public:
    uint8_t nodeAddr = 0;
    uint8_t mac[6]   = {0};
    uint8_t bright1  = 128, bright2 = 128, bright3 = 128;
    bool    hasData  = false;

    void load() {
        EEPROM.begin(EEPROM_SIZE);
        getMac();
        ledCount1 = PIXELS_CH1;
        ledCount2 = PIXELS_CH2;
        ledCount3 = PIXELS_CH3;

        if (EEPROM.read(EE_MAGIC) != MAGIC_BYTE) {
            hasData = false; nodeAddr = 0; save(); return;
        }
        uint8_t sm[6];
        for (int i = 0; i < 6; i++) sm[i] = EEPROM.read(EE_MAC + i);
        for (int i = 0; i < 6; i++) {
            if (sm[i] != mac[i]) {
                hasData = false; nodeAddr = 0; save(); return;
            }
        }
        hasData  = true;
        nodeAddr = EEPROM.read(EE_ADDR);
        if (nodeAddr == ADDR_BROADCAST) nodeAddr = 0;
        bright1  = EEPROM.read(EE_BRIGHT1);
        bright2  = EEPROM.read(EE_BRIGHT2);
        bright3  = EEPROM.read(EE_BRIGHT3);
        // ✅ v6.3 FIX (BUG-2): تعداد پیکسل‌ها از EEPROM بازیابی می‌شود
        // قبلاً این فیلدها در هر load با ثابت‌های کامپایل بازنویسی می‌شدند و
        // تنظیمات SET_PIXEL_COUNT (0x40) بعد از ریست از بین می‌رفت
        {
            uint8_t c1 = EEPROM.read(EE_CH1_COUNT);
            uint8_t c2 = EEPROM.read(EE_CH2_COUNT);
            uint8_t c3 = EEPROM.read(EE_CH3_COUNT);
            ledCount1 = (c1 >= 1 && c1 <= MAX_BUFFER_CAP) ? c1 : PIXELS_CH1;
            ledCount2 = (c2 >= 1 && c2 <= MAX_BUFFER_CAP) ? c2 : PIXELS_CH2;
            ledCount3 = (c3 >= 1 && c3 <= MAX_BUFFER_CAP) ? c3 : PIXELS_CH3;
        }
        {
            uint8_t tc = EEPROM.read(EE_TIMER_COUNT);
            ledCountTimer = (tc >= 1 && tc <= MAX_BUFFER_CAP) ? tc : PIXELS_TIMER;
            // ✅ v7.0 MIGRATION: نوار وایبرون = ۲۰px — یک‌بار برای نودهای
            // آپگرید‌شده (آدرس نود حفظ می‌شود؛ فقط طول نوار ریست می‌شود)
            if (EEPROM.read(EE_MIGRATION) != 0x70) {
                ledCountTimer = PIXELS_TIMER;
                EEPROM.write(EE_MIGRATION, 0x70);
                EEPROM.commit();
            }
        }
        // ✅ v7.6: حالت پا — ماندگار بعد از ریست (تأیید کاربر)
        g_footMode = (EEPROM.read(EE_FOOTMODE) == 1);
        EEPROM.commit();
    }

    void save() {
        EEPROM.write(EE_MAGIC,     MAGIC_BYTE);
        EEPROM.write(EE_ADDR,      nodeAddr);
        EEPROM.write(EE_BRIGHT1,   bright1);
        EEPROM.write(EE_BRIGHT2,   bright2);
        EEPROM.write(EE_BRIGHT3,   bright3);
        EEPROM.write(EE_CH1_COUNT, ledCount1);
        EEPROM.write(EE_CH2_COUNT, ledCount2);
        EEPROM.write(EE_CH3_COUNT, ledCount3);
        EEPROM.write(EE_TIMER_COUNT, ledCountTimer);
        for (int i = 0; i < 6; i++) EEPROM.write(EE_MAC + i, mac[i]);
        EEPROM.commit();
        hasData = true;
    }

    void clearAddress() {
        nodeAddr = 0;
        EEPROM.write(EE_MAGIC, MAGIC_BYTE);
        EEPROM.write(EE_ADDR,  0);
        for (int i = 0; i < 6; i++) EEPROM.write(EE_MAC + i, mac[i]);
        EEPROM.commit();
        hasData = true;
    }

private:
    void getMac() {
        WiFi.persistent(false);
        WiFi.mode(WIFI_STA);
        wifi_get_macaddr(STATION_IF, mac);
        if (!(mac[0]|mac[1]|mac[2]|mac[3]|mac[4]|mac[5]))
            WiFi.macAddress(mac);
        WiFi.mode(WIFI_OFF);
        WiFi.forceSleepBegin();
        delay(1);
        ws_detach_uart1();
    }
};
static EepromMgr g_ee;

// ============================================================================
// STATUS LED
// ============================================================================
class StatusLed {
public:
    void setState(SysState s) {
        state = s; blinkMs = millis(); blinkOn = true;
        if (g_otaActive) return;
        bool force = (s == SysState::IDENTIFY || s == SysState::BOOT);
        if (!force && g_userLedLock) return;
        switch (s) {
            // ✅ v7.0: RAW/NO_ADDR دیگر اینجا رنگ نمی‌شوند — شناسایی کامل
            // حلقه توسط taskNodeIdent انجام می‌شود (چشمک قرمز/سبز)
            case SysState::BOOT:     paint(128, 0, 128); break;
            case SysState::ONLINE:
                onlineMs = millis();
                if (!g_userLedLock) paint(0, 0, 200);
                break;
            case SysState::IDENTIFY:
                identMs = millis();
                paint(255, 255, 255);
                break;
        }
    }

    void tick() {
        if (g_otaActive) return;
        uint32_t now = millis();
        if (state == SysState::IDENTIFY && (now - identMs >= 5000UL)) {
            setState(g_ee.nodeAddr ? SysState::ONLINE : SysState::NO_ADDR);
            return;
        }
        if (g_userLedLock) return;
        switch (state) {
            case SysState::RAW:
            case SysState::NO_ADDR:
                // ✅ v7.0: شناسایی توسط taskNodeIdent (حلقه کامل) — اینجا ساکت
                break;
            case SysState::ONLINE:
                if (!g_userLedLock && (now - onlineMs >= 3000UL))
                    paint(0, 0, 0);
                break;
            default: break;
        }
    }

private:
    SysState state   = SysState::RAW;
    bool     blinkOn = false;
    uint32_t blinkMs = 0, onlineMs = 0, identMs = 0;

    void paint(uint8_t r, uint8_t g, uint8_t b) {
        if (ledCount3 == 0) return;
        uint16_t idx = ledCount3 - 1;
        ledBuf3[idx].r = r; ledBuf3[idx].g = g; ledBuf3[idx].b = b;
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3,
                          ledBright3 ? ledBright3 : 128);
    }
};
static StatusLed g_statusLed;

// ============================================================================
// RS485 DRIVER
// ============================================================================
class RS485 {
public:
    static void begin() {
        pinMode(PIN_DE_RE, OUTPUT);
        digitalWrite(PIN_DE_RE, LOW);
        Serial.begin(UART_BAUD, SERIAL_8N1);
        Serial.setRxBufferSize(2048);
        delay(20);
        flush_rx();
    }
    static void flush_rx() { while (Serial.available()) (void)Serial.read(); }
    static void txMode()   { digitalWrite(PIN_DE_RE, HIGH); delayMicroseconds(TX_HOLD_US); }
    static void rxMode()   { Serial.flush(); delayMicroseconds(RX_HOLD_US); digitalWrite(PIN_DE_RE, LOW); }
    static void sendFrame(uint8_t addr, uint8_t cmd, uint8_t len, const uint8_t* data);
    static void sendACK(uint8_t forCmd);
    static void sendNACK(uint8_t forCmd, uint8_t err);
};

static uint16_t crc16_ccitt(const uint8_t* data, uint16_t len) {
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static uint16_t crc16_frame(uint8_t addr, uint8_t cmd,
                              uint8_t len, const uint8_t* data) {
    uint8_t  hdr[4] = {FRAME_START, addr, cmd, len};
    uint16_t c      = crc16_ccitt(hdr, 4);
    for (uint16_t i = 0; i < len; i++) {
        c ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++)
            c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
    }
    return c;
}

void RS485::sendFrame(uint8_t addr, uint8_t cmd,
                       uint8_t len, const uint8_t* data) {
    uint16_t crc = crc16_frame(addr, cmd, len, data);
    txMode();
    Serial.write(FRAME_START); Serial.write(addr);
    Serial.write(cmd);         Serial.write(len);
    if (len && data) Serial.write(data, len);
    Serial.write((uint8_t)(crc & 0xFF));
    Serial.write((uint8_t)(crc >> 8));
    rxMode();
    g_lastNoiseMs = millis();   // ✅ v7.14: پنجره آرام‌شدن تاچ بعد از TX
}

void RS485::sendACK(uint8_t forCmd) {
    uint8_t d[2] = {forCmd, g_ee.nodeAddr};
    sendFrame(ADDR_MASTER, CMD::ACK, 2, d);
}

void RS485::sendNACK(uint8_t forCmd, uint8_t err) {
    uint8_t d[3] = {forCmd, err, g_ee.nodeAddr};
    sendFrame(ADDR_MASTER, CMD::NACK, 3, d);
}

// ============================================================================
// BROADCAST-SAFE REPLIES
// ============================================================================
static uint8_t g_curFrameAddr = ADDR_MASTER;

static inline bool frameIsBroadcast() {
    return (g_curFrameAddr == ADDR_BROADCAST);
}

static inline void replyACK(uint8_t forCmd) {
    if (!frameIsBroadcast()) RS485::sendACK(forCmd);
}

static inline void replyNACK(uint8_t forCmd, uint8_t err) {
    if (!frameIsBroadcast()) RS485::sendNACK(forCmd, err);
}

static inline void replyFrame(uint8_t cmd, uint8_t len, const uint8_t* data) {
    if (!frameIsBroadcast()) RS485::sendFrame(ADDR_MASTER, cmd, len, data);
}

// ============================================================================
// LED HELPERS
// ============================================================================
static void led_setAll(Pixel* buf, uint8_t count,
                        uint8_t r, uint8_t g, uint8_t b) {
    for (uint8_t i = 0; i < count; i++) {
        buf[i].r = r; buf[i].g = g; buf[i].b = b;
    }
}

static void led_setPixel(Pixel* buf, uint8_t idx, uint8_t count,
                          uint8_t r, uint8_t g, uint8_t b) {
    if (idx < count) { buf[idx].r = r; buf[idx].g = g; buf[idx].b = b; }
}

static void led_clear(uint8_t ch) {
    if (ch == 1 || ch == 0xFF) {
        if (!g_stageLocked) {
            g_fadeCh1.active = false;
            memset(ledBuf1, 0, sizeof(ledBuf1));
            ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        }
    }
    if (ch == 2 || ch == 0xFF) {
        g_fadeCh2.active = false;
        memset(ledBuf2, 0, sizeof(ledBuf2));
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    }
    if (ch == 3 || ch == 0xFF) {
        memset(ledBuf3, 0, sizeof(ledBuf3));
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    }
}

static void led_show(uint8_t ch) {
    if      (ch == 1)    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    else if (ch == 2)    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    else if (ch == 3)    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    else if (ch == 0xFF) {
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    }
}

// ============================================================================
// RUNTIME GEOMETRY
// ============================================================================
static inline uint8_t activeDisplays() {
    uint8_t d1 = ledCount1 / SEGS_PER_DIGIT;
    uint8_t d2 = ledCount2 / SEGS_PER_DIGIT;
    uint8_t d  = (d1 < d2) ? d1 : d2;
    if (d > MAX_DISPLAYS) d = MAX_DISPLAYS;
    return d;
}

static inline uint8_t activeKeys() {
    uint8_t k = ledCount3 / PIXELS_PER_KEY;
    if (k > MAX_KEYS) k = MAX_KEYS;
    return k;
}

static inline bool keyIndexValid(uint8_t keyNum) {
    return (keyNum < activeKeys());
}
static inline bool displayIndexValid(uint8_t d) {
    return (d < activeDisplays());
}

// ✅ v6.0 FIX: تابع واحد برای محاسبه تعداد پیکسل هر کلید
// قبلاً setKeyPixels و game_clearKeyPixels از روش‌های متفاوت استفاده می‌کردند
static inline uint8_t keyPixelCount() {
    uint8_t ppk = (ledCount3 >= MAX_KEYS) ? (uint8_t)(ledCount3 / MAX_KEYS) : ledCount3;
    if (ppk == 0) ppk = 1;
    return ppk;
}

// ============================================================================
// SEVEN-SEGMENT DISPLAY ENGINE
// ============================================================================
static void displayDigitOnSegment(uint8_t displayNum, uint8_t segment,
                                   uint8_t digit, uint32_t color) {
    if (digit > 9 || !displayIndexValid(displayNum) || segment > 1) return;
    uint8_t pattern = sevenSegmentDigits[digit];
    uint8_t cr, cg, cb;
    paletteRGB(color, cr, cg, cb);
    Pixel* targetBuf   = (segment == 0) ? ledBuf1 : ledBuf2;
    uint8_t targetCount = (segment == 0) ? ledCount1 : ledCount2;
    for (uint8_t i = 0; i < SEGS_PER_DIGIT; i++) {
        uint16_t pixelIndex = displayNum * SEGS_PER_DIGIT + i;
        if (pattern & (1 << i)) {
            led_setPixel(targetBuf, pixelIndex, targetCount, cr, cg, cb);
        } else {
            led_setPixel(targetBuf, pixelIndex, targetCount, 0, 0, 0);
        }
    }
}

static void displayTwoDigitNumber(uint8_t displayNum, uint8_t number, uint32_t color) {
    if (number > 99 || !displayIndexValid(displayNum)) return;
    displayDigitOnSegment(displayNum, 0, number / 10, color);
    displayDigitOnSegment(displayNum, 1, number % 10, color);
}

// ============================================================================
// KEY LED FUNCTIONS
// ============================================================================
// ✅ v6.0 FIX: حالا از keyPixelCount() استفاده می‌کند
static void setKeyPixels(uint8_t keyNum, uint32_t color) {
    if (!keyIndexValid(keyNum)) return;
    uint8_t cr, cg, cb;
    paletteRGB(color, cr, cg, cb);
    uint8_t ppk = keyPixelCount();
    uint8_t base = (uint8_t)(keyNum * ppk);
    for (uint8_t i = 0; i < ppk; i++) {
        led_setPixel(ledBuf3, base + i, ledCount3, cr, cg, cb);
    }
}

// ✅ v6.11 سناریو ۱: تنظیم پیکسل‌های حلقه کلید با RGB خام (بدون پالت)
static void setKeyPixelsRgb(uint8_t keyNum, uint8_t r, uint8_t g, uint8_t b) {
    if (!keyIndexValid(keyNum)) return;
    uint8_t ppk  = keyPixelCount();
    uint8_t base = (uint8_t)(keyNum * ppk);
    for (uint8_t i = 0; i < ppk; i++)
        led_setPixel(ledBuf3, base + i, ledCount3, r, g, b);
}

// ============================================================================
// TIMER BAR ENGINE (v5.6)
// ============================================================================
static inline uint32_t tmr_color() { return g_tmrColor; }   // ✅ v7.0

static void tmr_clearBar() {
    uint8_t n = (ledCountTimer > ledCount1) ? ledCountTimer : ledCount1;
    if (n > MAX_BUFFER_CAP) n = MAX_BUFFER_CAP;
    for (uint8_t i = 0; i < n; i++) {
        ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, n, ledBright1);
    g_tmrLastLit = -1;
}

// tmr_drawBar — حذف شد v6.2: رندر نوار الان مستقیماً در taskTimerBar انجام می‌شود

// ══════════════════════════════════════════════════════════════════════════
// ✅ v7.17 POLL-PATCH — حالت جاروب (Polling) سرویس
// ──────────────────────────────────────────────────────────────────────────
// سرویس 4.4.20 هر 50ms همه نودهای بریج را با POLL_STATE (0x08) جاروب می‌کند.
// نود بین دو جاروب ساکت می‌ماند و «آخرین وضعیت» خود را latch می‌کند؛ پاسخ
// جاروب همین snapshot است (پایین). اگر > POLL_FALLBACK_MS جاروب نیامد
// (سرویس کرش / کابل قطع / سرویس قدیمی بدون polling)، پوش یدکی از همان
// مسیر قدیمی خودانگیخته اجرا می‌شود → سازگاری کامل با سرویس‌های قدیمی.
// ══════════════════════════════════════════════════════════════════════════
#define POLL_FALLBACK_MS 1000UL          // D3: آستانه غیبت جاروب → پوش یدکی
static uint32_t g_lastPollMs = 0;        // آخرین POLL_STATE دریافتی
static inline bool pollLinkUp() { return (millis() - g_lastPollMs) < POLL_FALLBACK_MS; }

// ── snapshot وضعیت (D1: تاچ+موشن+ADC+بازی · D2: snapshot+seq) ──
static uint8_t  g_pTouchMask   = 0;      // ماسک لحظه‌ای پدها (بیت=ch)
static uint8_t  g_pTouchSeq    = 0;      // +۱ به ازای هر رویداد لمس (لبه)
static uint8_t  g_pMotionFlags = 0;      // bit0/bit1: تریگر سنسور ۱/۲
static uint8_t  g_pMotionSeq   = 0;      // +۱ به ازای هر گزارش موشن
static uint8_t  g_pGameSeq     = 0;      // +۱ به ازای هر رویداد بازی
static uint8_t  g_pGameKey = 0, g_pGameEvt = 0, g_pGameVal = 0, g_pGameEvtSeq = 0;
static bool     g_pDirty = false;        // داده جدید از جاروب قبل (flags bit0)

// ✅ v7.17 POLL: رویداد بازی — latch همیشه · ارسال فقط در حالت پوش یدکی
static void gameEventEmit(const uint8_t p[5]) {
    g_pGameKey = p[1]; g_pGameEvt = p[2]; g_pGameVal = p[3]; g_pGameEvtSeq = p[4];
    g_pGameSeq++;
    g_pDirty = true;
    if (pollLinkUp()) return;            // جاروب فعال → فقط latch (سرویس از seq می‌فهمد)
    RS485::sendFrame(ADDR_MASTER, CMD::GAME_EVENT, 5, p);
}

static void tmr_sendEvent(uint8_t evt, uint8_t secValue) {
    uint8_t p[5] = { g_ee.nodeAddr, g_tmrKey, evt, secValue, g_tmrSeq++ };
    gameEventEmit(p);   // ✅ v7.17 POLL
}

static void tmr_finish(bool clearVisuals) {
    if (clearVisuals) {
        tmr_clearBar();
        for (uint8_t i = 0; i < ledCount3; i++)
            led_setPixel(ledBuf3, i, ledCount3, 0, 0, 0);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    }
    g_tmrActive   = false;
    g_tmrKeyArmed = false;
    g_tmrRemainMs = 0;
    g_tmrTotalMs  = 0;
    g_tmrLastLit  = -1;
    g_tmrPhase    = TmrPhase::IDLE;
    g_ch1Mode     = Ch1Mode::SEVENSEG;
    g_lastCommandMs = millis();
}

// ✅ v6.2: شروع تایمر — نوار و کلید خاموش شروع می‌شوند و تدریجی روشن می‌شوند
static bool tmr_start(uint8_t keyNum, uint8_t r, uint8_t g, uint8_t b,
                      uint8_t seconds) {
    if (!keyIndexValid(keyNum))            return false;
    if (seconds < 1 || seconds > GAME_MAX_SECONDS) return false;

    if (g_gameState != GameState::IDLE) {
        game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
    }

    // ✅ v6.3: لغو key display فعال
    g_keyDisplayActive = false;

    g_tmrKey        = keyNum;
    g_tmrColor     = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;   // ✅ v7.0
    g_tmrTotalSec   = seconds;
    g_tmrTotalMs    = (uint32_t)seconds * 1000UL;
    g_tmrRemainMs   = g_tmrTotalMs;
    g_tmrActive     = true;
    g_tmrKeyArmed   = false;   // ✅ v7.0: حلقه فقط با دستور 0x64 فعال می‌شود

    // ✅ v6.2: شروع از لحظه 0 — همه خاموش، fade in در taskTimerBar
    g_tmrStartMs    = millis();
    g_tmrPhase      = TmrPhase::RUNNING;

    g_ch1Mode     = Ch1Mode::TIMERBAR;
    g_userLedLock = true;

    // پاک‌سازی اولیه (همه خاموش)
    // ✅ v6.3 FIX (BUG-8): دو شاخه if/else یکسان ادغام شدند (کد تکراری)
    tmr_clearBar();
    for (uint8_t i = 0; i < ledCount3; i++)
        led_setPixel(ledBuf3, i, ledCount3, 0, 0, 0);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    return true;
}

// ✅ v6.2: فلاش سفید → reverse نرم → fade out کلید
static bool tmr_onKeyPress(uint8_t keyNum) {
    if (!g_tmrActive || !g_tmrKeyArmed) return false;
    if (keyNum != g_tmrKey)             return false;
    if (g_tmrPhase != TmrPhase::RUNNING) return false;

    uint8_t remainSec = (uint8_t)((g_tmrRemainMs + 999UL) / 1000UL);
    tmr_sendEvent(GEVT::KEY_HIT, remainSec);

    // ✅ v6.2: ذخیره تعداد پیکسل‌های روشن فعلی
    uint8_t n = (ledCountTimer < MAX_BUFFER_CAP) ? ledCountTimer : MAX_BUFFER_CAP;
    uint32_t elapsed = millis() - g_tmrStartMs;
    uint32_t perPx = g_tmrTotalMs / (n > 0 ? n : 1);
    g_tmrRevLitPx = 0;
    for (uint8_t i = 0; i < n; i++) {
        if (elapsed >= (uint32_t)i * perPx) g_tmrRevLitPx++;
    }

    // شروع فاز HIT_FLASH (فلاش سفید 100ms)
    g_tmrHitMs = millis();
    g_tmrPhase = TmrPhase::HIT_FLASH;
    return true;
}

// ✅ v6.2: ماشین حالت کامل — fade in → running → hit_flash → reverse → done
static void taskTimerBar() {
    if (g_otaActive || !g_tmrActive) return;
    uint32_t now = millis();

    // ✅ v6.3 CRITICAL FIX: Throttle رندر — حداکثر ~60 فریم بر ثانیه
    // بدون این throttle، taskTimerBar در هر loop iteration (~1ms) صدا زده می‌شد
    // و ws_show_strip هر بار 60 LED را بازنویسی می‌کرد → ~1000 بار/ثانیه
    // → جریان لحظه‌ای وحشتناک → سوختن پیکسل‌ها
    const uint32_t tmrThrottleMs = (ledCountTimer > 64) ? 32 : 16;  // ✅ v6.7.1
    if (now - g_tmrLastRenderMs < tmrThrottleMs) return;
    g_tmrLastRenderMs = now;

    uint8_t n = (ledCountTimer < MAX_BUFFER_CAP) ? ledCountTimer : MAX_BUFFER_CAP;
    if (n == 0) n = 1;
    uint8_t flush = (ledCountTimer > ledCount1) ? ledCountTimer : ledCount1;
    if (flush > MAX_BUFFER_CAP) flush = MAX_BUFFER_CAP;

    uint32_t col = tmr_color();
    uint8_t colR = (col >> 16) & 0xFF;
    uint8_t colG = (col >> 8)  & 0xFF;
    uint8_t colB =  col        & 0xFF;

    switch (g_tmrPhase) {

        // ── RUNNING: نوار تدریجی پر می‌شود + کلید fade in ──
        case TmrPhase::RUNNING: {
            uint32_t elapsed = now - g_tmrStartMs;

            // Timeout check
            if (elapsed >= g_tmrTotalMs) {
                g_tmrRemainMs = 0;
                g_tmrFadeMs = now;
                g_tmrPhase = TmrPhase::TIMEOUT_FADE;
                tmr_sendEvent(GEVT::TIMEOUT, 0);
                break;
            }

            g_tmrRemainMs = g_tmrTotalMs - elapsed;

            // ── رندر نوار: هر پیکسل fade in با ease-in curve ──
            uint32_t perPx = g_tmrTotalMs / n;
            if (perPx == 0) perPx = 1;

            for (uint8_t i = 0; i < n; i++) {
                uint8_t mi = (uint8_t)(n - 1 - i);   // ✅ v7.1: آینه — پر شدن از آخرین پیکسل (19) به اولین (0)
                uint32_t pxStart = (uint32_t)i * perPx;
                if (elapsed < pxStart) {
                    // هنوز نوبت این پیکسل نرسیده
                    ledBuf1[mi].r = 0; ledBuf1[mi].g = 0; ledBuf1[mi].b = 0;
                } else {
                    uint32_t pxEla = elapsed - pxStart;
                    uint16_t brt;
                    if (pxEla < TMR_PIXEL_FADEIN_MS) {
                        // ✅ v6.2: Quadratic ease-in: f(t) = t² → شروع نرم، پایان سریع
                        uint32_t tNorm = (pxEla * 256) / TMR_PIXEL_FADEIN_MS;  // 0..256
                        uint32_t tEased = (tNorm * tNorm) / 256;                // 0..256
                        brt = (uint16_t)((uint32_t)tEased * GAME_TIMER_DIM / 256);
                    } else {
                        // کاملاً روشن (نصف روشنایی — مثل ATmega)
                        brt = GAME_TIMER_DIM;
                    }
                    ledBuf1[mi].r = (uint8_t)((uint16_t)colR * brt / 255);
                    ledBuf1[mi].g = (uint8_t)((uint16_t)colG * brt / 255);
                    ledBuf1[mi].b = (uint8_t)((uint16_t)colB * brt / 255);
                }
            }
            for (uint8_t i = n; i < flush; i++) {
                ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, flush, ledBright1);

            // ── رندر پیکسل‌های کلید: fade in با ease-in طی TMR_KEY_FADEIN_MS ──
            if (g_tmrKeyArmed) {
                uint16_t keyBrt;
                if (elapsed < TMR_KEY_FADEIN_MS) {
                    // ✅ v6.2: Quadratic ease-in
                    uint32_t tNorm = (elapsed * 256) / TMR_KEY_FADEIN_MS;
                    uint32_t tEased = (tNorm * tNorm) / 256;
                    keyBrt = (uint16_t)((uint32_t)tEased * 255 / 256);
                } else {
                    keyBrt = 255;
                }
                uint32_t rcol = g_tmrRingColor;   // ✅ v7.0: رنگ حلقه از 0x64
                uint8_t kr = (uint8_t)(((rcol >> 16) & 0xFF) * keyBrt / 255);
                uint8_t kg = (uint8_t)(((rcol >>  8) & 0xFF) * keyBrt / 255);
                uint8_t kb = (uint8_t)(( rcol        & 0xFF) * keyBrt / 255);
                for (uint8_t i = 0; i < ledCount3; i++)
                    led_setPixel(ledBuf3, i, ledCount3, kr, kg, kb);
                ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            }
            break;
        }

        // ── HIT_FLASH: فلاش سفید روی کلید + فلاش رنگی روی نوار ──
        case TmrPhase::HIT_FLASH: {
            uint32_t elapsed = now - g_tmrHitMs;

            // منحنی فلاش: بالا → پایین (ease-out)
            uint16_t flashBrt;
            if (elapsed < TMR_HIT_FLASH_MS / 3) {
                // 1/3 اول: روشن شدن سریع
                flashBrt = (uint16_t)((uint32_t)elapsed * 255 / (TMR_HIT_FLASH_MS / 3));
            } else {
                // 2/3 بقیه: محو شدن
                uint32_t fadeEla = elapsed - TMR_HIT_FLASH_MS / 3;
                uint32_t fadeDur = TMR_HIT_FLASH_MS - TMR_HIT_FLASH_MS / 3;
                flashBrt = (uint16_t)(255 - (uint32_t)fadeEla * 255 / fadeDur);
            }
            // ✅ v6.4 FIX (W-6): چک مرده «flashBrt > 255» حذف شد — نتیجه هیچ‌وقت >255 نمی‌شود

            // ── کلیدها: فلاش سفید ──
            for (uint8_t i = 0; i < ledCount3; i++)
                led_setPixel(ledBuf3, i, ledCount3,
                             (uint8_t)flashBrt, (uint8_t)flashBrt, (uint8_t)flashBrt);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);

            // ✅ v6.10: فیدبک CH2 — فلاش سفید هم‌زمان با فلاش حلقه (سناریو ۳)
            for (uint8_t i = 0; i < ledCount2; i++)
                led_setPixel(ledBuf2, i, ledCount2,
                             (uint8_t)flashBrt, (uint8_t)flashBrt, (uint8_t)flashBrt);
            ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);

            // ── نوار تایمر: فلاش رنگی (رنگ تایمر با brightness بالا) ──
            // فقط پیکسل‌هایی که قبلاً روشن بودند فلاش می‌زنند
            uint16_t barFlash = (uint16_t)((uint32_t)flashBrt * 200 / 255);
            for (uint8_t i = 0; i < n; i++) {
                uint8_t mi = (uint8_t)(n - 1 - i);   // ✅ v7.1: آینه — پر شدن از آخرین پیکسل (19) به اولین (0)
                if (i < g_tmrRevLitPx) {
                    ledBuf1[mi].r = (uint8_t)((uint16_t)colR * barFlash / 255);
                    ledBuf1[mi].g = (uint8_t)((uint16_t)colG * barFlash / 255);
                    ledBuf1[mi].b = (uint8_t)((uint16_t)colB * barFlash / 255);
                } else {
                    ledBuf1[mi].r = 0; ledBuf1[mi].g = 0; ledBuf1[mi].b = 0;
                }
            }
            for (uint8_t i = n; i < flush; i++) {
                ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, flush, ledBright1);

            if (elapsed >= TMR_HIT_FLASH_MS) {
                g_tmrRevMs = now;
                g_tmrPhase = TmrPhase::REVERSE;
            }
            break;
        }

        // ── REVERSE: نوار از آخرین پیکسل روشن‌شده به سمت اول خاموش + کلید fade out ──
        case TmrPhase::REVERSE: {
            uint32_t elapsed = now - g_tmrRevMs;

            // ── نوار: از پیکسل (g_tmrRevLitPx-1) به سمت پیکسل 0 خاموش شود ──
            // فقط پیکسل‌های 0 تا (g_tmrRevLitPx-1) روشن بودند
            bool barDone = true;
            for (uint8_t i = 0; i < n; i++) {
                uint8_t mi = (uint8_t)(n - 1 - i);   // ✅ v7.1: آینه — پر شدن از آخرین پیکسل (19) به اولین (0)
                if (i >= g_tmrRevLitPx) {
                    // این پیکسل هرگز روشن نبوده → خاموش
                    ledBuf1[mi].r = 0; ledBuf1[mi].g = 0; ledBuf1[mi].b = 0;
                    continue;
                }

                // ترتیب خاموشی: از آخر (g_tmrRevLitPx-1) به اول (0)
                // پیکسل i → ترتیب خاموشی = (g_tmrRevLitPx - 1) - i
                uint16_t revIdx = (g_tmrRevLitPx - 1) - i;
                uint32_t offTime = (uint32_t)revIdx * TMR_REVERSE_PER_PX_MS;

                if (elapsed < offTime) {
                    // هنوز روشن
                    ledBuf1[mi].r = (uint8_t)((uint16_t)colR * GAME_TIMER_DIM / 255);
                    ledBuf1[mi].g = (uint8_t)((uint16_t)colG * GAME_TIMER_DIM / 255);
                    ledBuf1[mi].b = (uint8_t)((uint16_t)colB * GAME_TIMER_DIM / 255);
                    barDone = false;
                } else {
                    uint32_t fadeEla = elapsed - offTime;
                    if (fadeEla < TMR_PIXEL_FADEOUT_MS) {
                        uint16_t brt = (uint16_t)(GAME_TIMER_DIM -
                            (uint32_t)fadeEla * GAME_TIMER_DIM / TMR_PIXEL_FADEOUT_MS);
                        ledBuf1[mi].r = (uint8_t)((uint16_t)colR * brt / 255);
                        ledBuf1[mi].g = (uint8_t)((uint16_t)colG * brt / 255);
                        ledBuf1[mi].b = (uint8_t)((uint16_t)colB * brt / 255);
                        barDone = false;
                    } else {
                        ledBuf1[mi].r = 0; ledBuf1[mi].g = 0; ledBuf1[mi].b = 0;
                    }
                }
            }
            for (uint8_t i = n; i < flush; i++) {
                ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, flush, ledBright1);

            // ── کلید: fade out همزمان ──
            uint16_t keyFade;
            if (elapsed < TMR_KEY_FADEOUT_MS) {
                keyFade = (uint16_t)(255 - (uint32_t)elapsed * 255 / TMR_KEY_FADEOUT_MS);
            } else {
                keyFade = 0;
            }
            uint8_t kr = (uint8_t)((uint16_t)colR * keyFade / 255);
            uint8_t kg = (uint8_t)((uint16_t)colG * keyFade / 255);
            uint8_t kb = (uint8_t)((uint16_t)colB * keyFade / 255);
            for (uint8_t i = 0; i < ledCount3; i++)
                led_setPixel(ledBuf3, i, ledCount3, kr, kg, kb);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);

            // پایان reverse
            if (barDone && elapsed >= TMR_KEY_FADEOUT_MS) {
                tmr_finish(false);
                tmr_sendEvent(GEVT::SUCCESS_END, 0);
            }
            break;
        }

        // ── TIMEOUT_FADE: همه fade out ──
        case TmrPhase::TIMEOUT_FADE: {
            uint32_t elapsed = now - g_tmrFadeMs;

            if (elapsed >= TMR_TIMEOUT_FADE_MS) {
                tmr_finish(true);
                break;
            }

            uint16_t fade = (uint16_t)(GAME_TIMER_DIM -
                (uint32_t)elapsed * GAME_TIMER_DIM / TMR_TIMEOUT_FADE_MS);

            // نوار fade out
            for (uint8_t i = 0; i < n; i++) {
                uint8_t mi = (uint8_t)(n - 1 - i);   // ✅ v7.1: آینه — پر شدن از آخرین پیکسل (19) به اولین (0)
                ledBuf1[mi].r = (uint8_t)((uint16_t)colR * fade / 255);
                ledBuf1[mi].g = (uint8_t)((uint16_t)colG * fade / 255);
                ledBuf1[mi].b = (uint8_t)((uint16_t)colB * fade / 255);
            }
            for (uint8_t i = n; i < flush; i++) {
                ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, flush, ledBright1);

            // کلید fade out
            uint16_t keyFade = (uint16_t)(255 -
                (uint32_t)elapsed * 255 / TMR_TIMEOUT_FADE_MS);
            uint8_t kr = (uint8_t)((uint16_t)colR * keyFade / 255);
            uint8_t kg = (uint8_t)((uint16_t)colG * keyFade / 255);
            uint8_t kb = (uint8_t)((uint16_t)colB * keyFade / 255);
            for (uint8_t i = 0; i < ledCount3; i++)
                led_setPixel(ledBuf3, i, ledCount3, kr, kg, kb);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            break;
        }

        default:
            break;
    }
}

// ============================================================================
// DROP GAME ENGINE — بازی قطره | opcode 0x72 (legacy 0x5A)
// هم‌گام با منطق مرجع ATmega64 (stream_strip / update_strip)
// ============================================================================
// مدل حرکت (مطابق ATmega64):
//   • قطره = بدنه‌ی DropLen پیکسل یکدست + دنباله‌ی TrailLen پیکسل با ۴ سطح
//     روشنایی گسسته: کامل، ¼، ۱/۱۶، ۱/۶۴ (تابع dim_quarter در ATmega)
//   • FORWARD: نوک قطره از پیکسل 0 تا 99 در نیمه‌ی اولِ زمان دستور
//   • لمس فقط در پنجره‌ی فعال (پیش‌فرض 75٪) معتبر است — سطح‌محور مثل ATmega:
//       کلید «نگه‌داشته‌شده» + pos ≥ actPx → DROP_HIT → RETURN از همان نقطه
//       (حلقه کلید = آبی)؛ زمان برگشت = half × pos / 99
//   • بدون لمس تا پیکسل 99 → DROP_TIMEOUT (معادل Bx0) → END_DRAIN → RETURN
//     (حلقه کلید = قرمز)
//   • END_DRAIN: قطره با همان سرعتِ رفت از انتهای نوار خارج می‌شود (pos 100→120)
//   • RETURN: 99→0 در نیمه‌ی دوم (بدنه حالا جلوی pos و دنباله پشت آن)
//   • START_DRAIN: قطره از ابتدای نوار خارج می‌شود (pos 0→−20) → پایان → DROP_END
//   • SOFT_ZONE: ۱۵ پیکسل اولِ رفت، رنگ قطره fade-in می‌شود
//   • دور کلید: در پنجره فعال = رنگ بازی + «نقطه‌ی چرخان» (سرعت چرخش با پیشرفت
//     زیاد می‌شود)؛ در دریِن/برگشت = آبی (لمس‌شده) یا قرمز (لمس‌نشده)
//
enum class DropPhase : uint8_t {
    IDLE        = 0,
    FORWARD     = 1,   // رفت 0→99
    END_DRAIN   = 2,   // خروج قطره از انتهای نوار (100→100+DRAIN)
    RETURN      = 3,   // برگشت 99→0
    START_DRAIN = 4    // خروج قطره از ابتدای نوار (0→−DRAIN)
};

static bool      g_dropActive        = false;
static DropPhase g_dropPhase         = DropPhase::IDLE;
static uint8_t   g_dropKey           = 0;
static uint32_t  g_dropColor = 0x0000FF;   // ✅ v7.0: RGB
static uint8_t   g_dropDrops         = 1;
static uint8_t   g_dropTrail         = DROP_DEF_TRAIL;
static uint8_t   g_dropLen           = DROP_DEF_LEN;
static uint8_t   g_dropActPct        = DROP_DEF_ACTPCT;
static bool      g_dropRing          = true;
static bool      g_dropMissFb        = true;
static uint16_t  g_dropBarLen        = 0;    // = DROP_BAR_PIXELS (100)
static uint32_t  g_dropStartMs       = 0;    // شروع FORWARD
static uint32_t  g_dropHalfMs        = 0;    // نصفِ زمان دستور (نیم‌زمان رفت)
static uint32_t  g_dropDrainStartMs  = 0;
static uint32_t  g_dropDrainTotalMs  = 0;
static uint32_t  g_dropRetStartMs    = 0;
static uint16_t  g_dropRetStartPos   = 0;
static uint32_t  g_dropRetTotalMs    = 0;
static bool      g_dropPressedInFwd  = false;
static bool      g_dropB0Sent        = false;
static bool      g_dropActiveSent    = false;
static float     g_dropTailAngle     = 0.0f;
static uint32_t  g_dropLastRenderMs  = 0;
static uint32_t  g_dropMissUntilMs   = 0;
static uint8_t   g_dropSeq           = 0;

static inline uint32_t drop_color() { return g_dropColor; }   // ✅ v7.0

static void drop_sendEvent(uint8_t evt, uint8_t val) {
    uint8_t p[5] = { g_ee.nodeAddr, g_dropKey, evt, val, g_dropSeq++ };
    gameEventEmit(p);   // ✅ v7.17 POLL
}

// موقعیت نوک قطره در FORWARD برای زمان t (ms از شروع)
static int16_t drop_fwdPos(uint32_t t) {
    int16_t lastPx = (int16_t)(g_dropBarLen - 1);
    int16_t pos = (int16_t)((t * (uint32_t)lastPx) / (g_dropHalfMs ? g_dropHalfMs : 1));
    return (pos > lastPx) ? lastPx : pos;
}

// درصد پیمایش (0..100) — cpos بین 0..99
static uint8_t drop_progressPct(int16_t pos) {
    if (g_dropBarLen < 2) return 0;
    int16_t lastPx = (int16_t)(g_dropBarLen - 1);
    int16_t cpos = pos < 0 ? 0 : (pos > lastPx ? lastPx : pos);
    return (uint8_t)(((uint32_t)cpos * 100UL) / (uint32_t)lastPx);
}

static inline int16_t drop_actPx() {
    return (int16_t)((uint32_t)g_dropBarLen * g_dropActPct / 100UL);
}

// رندر یک قطره (بدنه + دنباله) با جهت مشخص — pos می‌تواند بیرون نوار باشد
// (کلیپ خودکار). مطابق stream_strip در ATmega64:
//   رفت:     بدنه [pos-Len+1 .. pos] ، دنباله [pos-Len-Trail .. pos-Len]
//   برگشت:  بدنه [pos .. pos+Len-1] ، دنباله [pos+Len .. pos+Len+Trail-1]
static void drop_renderOne(int16_t pos, bool retOrient, uint8_t fade) {
    uint32_t col = drop_color();
    uint8_t cr = (uint8_t)(((col >> 16) & 0xFF) * fade / 255);
    uint8_t cg = (uint8_t)(((col >>  8) & 0xFF) * fade / 255);
    uint8_t cb = (uint8_t)(( col        & 0xFF) * fade / 255);
    uint8_t t2r = (uint8_t)(cr >> 2), t2g = (uint8_t)(cg >> 2), t2b = (uint8_t)(cb >> 2);
    uint8_t t3r = (uint8_t)(t2r >> 2), t3g = (uint8_t)(t2g >> 2), t3b = (uint8_t)(t2b >> 2);
    uint8_t t4r = (uint8_t)(t3r >> 2), t4g = (uint8_t)(t3g >> 2), t4b = (uint8_t)(t3b >> 2);

    int16_t ds, de, ts, te;
    if (!retOrient) {
        de = pos;              ds = (int16_t)(de - (int16_t)g_dropLen + 1);
        te = (int16_t)(ds - 1); ts = (int16_t)(te - (int16_t)g_dropTrail + 1);
    } else {
        ds = pos;              de = (int16_t)(ds + (int16_t)g_dropLen - 1);
        ts = (int16_t)(de + 1); te = (int16_t)(ts + (int16_t)g_dropTrail - 1);
    }

    for (uint8_t i = 0; i < g_dropBarLen; i++) {
        // ✅ v7.1: آینه — قطره از آخرین پیکسل (99) وارد می‌شود و به اولین (0) پیمایش می‌کند
        int16_t ii = (int16_t)(g_dropBarLen - 1 - i);
        // ✅ v7.14: max-merge — قطره‌های تزئینی قطره اصلی را پاک نمی‌کنند
        if (ii >= ds && ii <= de) {
            if (ledBuf1[i].r < cr) ledBuf1[i].r = cr;
            if (ledBuf1[i].g < cg) ledBuf1[i].g = cg;
            if (ledBuf1[i].b < cb) ledBuf1[i].b = cb;
        } else if (ii >= ts && ii <= te) {
            int16_t d = retOrient ? (ii - ts) : (te - ii);
            uint8_t vr, vg, vb;
            if      (d == 0) { vr = cr;  vg = cg;  vb = cb;  }
            else if (d == 1) { vr = t2r; vg = t2g; vb = t2b; }
            else if (d == 2) { vr = t3r; vg = t3g; vb = t3b; }
            else             { vr = t4r; vg = t4g; vb = t4b; }
            if (ledBuf1[i].r < vr) ledBuf1[i].r = vr;
            if (ledBuf1[i].g < vg) ledBuf1[i].g = vg;
            if (ledBuf1[i].b < vb) ledBuf1[i].b = vb;
        }
    }
}

// رندر کل نوار: پاک‌سازی + قطره اصلی + قطره‌های تزئینی (d≥1 با اختلاف فاز یکنواخت)
static void drop_renderFrame(int16_t pos, bool retOrient, uint8_t fade, uint32_t now) {
    for (uint8_t i = 0; i < g_dropBarLen; i++) {
        ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
    }
    drop_renderOne(pos, retOrient, fade);

    if (g_dropDrops > 1) {
        // قطره‌های اضافی صرفاً تزئینی‌اند (مرجع ATmega تک‌قطره‌ای است) —
        // با موج مثلثی روی کل چرخه رفت+برگشت توزیع می‌شوند
        uint32_t cycle = g_dropHalfMs * 2;
        if (cycle == 0) cycle = 1;
        for (uint8_t d = 1; d < g_dropDrops; d++) {
            uint32_t off = (uint32_t)d * cycle / g_dropDrops;
            uint32_t ph  = (now - g_dropStartMs + off) % cycle;
            int16_t  p; bool rO;
            if (ph < g_dropHalfMs) {
                p  = (int16_t)(ph * (uint32_t)(g_dropBarLen - 1) / g_dropHalfMs);
                rO = false;
            } else {
                uint32_t rem = cycle - ph;
                p  = (int16_t)(rem * (uint32_t)(g_dropBarLen - 1) / g_dropHalfMs);
                rO = true;
            }
            drop_renderOne(p, rO, 255);
        }
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, (uint8_t)g_dropBarLen, ledBright1);
}

static void drop_renderRingRaw(uint8_t r, uint8_t g, uint8_t b) {
    for (uint8_t i = 0; i < ledCount3; i++)
        led_setPixel(ledBuf3, i, ledCount3, r, g, b);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

// حلقه دور کلید — مطابق stream_strip در ATmega64:
//   FORWARD:  pos ≥ actPx → رنگ بازی + نقطه چرخان (tail_angle)
//   دریِن/برگشت: آبی (لمس‌شده) / قرمز (لمس‌نشده) + چرخش ادامه دارد
static void drop_renderRingFrame(int16_t pos, uint32_t now) {
    if (now < g_dropMissUntilMs) {           // فلاش MISS اولویت دارد
        drop_renderRingRaw(255, 0, 0);
        return;
    }
    if (!g_dropRing) { drop_renderRingRaw(0, 0, 0); return; }

    int16_t lastPx = (int16_t)(g_dropBarLen - 1);
    int16_t cpos = pos < 0 ? 0 : (pos > lastPx ? lastPx : pos);
    uint8_t progress = drop_progressPct(cpos);
    int16_t actPx = drop_actPx();

    bool     active = false;
    uint8_t  br = 0, bg = 0, bb = 0;
    if (g_dropPhase == DropPhase::FORWARD) {
        if (cpos >= actPx) {
            uint32_t col = drop_color();
            br = (col >> 16) & 0xFF; bg = (col >> 8) & 0xFF; bb = col & 0xFF;
            active = true;
        }
    } else {
        active = true;
        if (g_dropPressedInFwd) { bg = 255; }        // ✅ v7.0 تسلا: سبز 0x00FF00
        else                    { br = 255; }        // قرمز 0xFF0000
    }

    // نقطه چرخان — سرعت با پیشرفت زیاد می‌شود (drop_speed × 0.2 در ATmega)
    if (cpos >= actPx) {
        g_dropTailAngle += (0.2f + (progress / 100.0f)) * 0.2f;
        if (g_dropTailAngle >= 4.0f) g_dropTailAngle -= 4.0f;
    } else {
        g_dropTailAngle = 0.0f;
    }
    uint8_t rot = (uint8_t)g_dropTailAngle;

    if (!active) { drop_renderRingRaw(0, 0, 0); return; }

    uint8_t ppk = keyPixelCount();
    for (uint8_t i = 0; i < ppk && i < ledCount3; i++) {
        uint8_t d = (i > rot) ? (i - rot) : (rot - i);
        if      (d == 0) led_setPixel(ledBuf3, i, ledCount3, br, bg, bb);
        else if (d == 1) led_setPixel(ledBuf3, i, ledCount3, (uint8_t)(br>>2), (uint8_t)(bg>>2), (uint8_t)(bb>>2));
        else             led_setPixel(ledBuf3, i, ledCount3, (uint8_t)(br>>4), (uint8_t)(bg>>4), (uint8_t)(bb>>4));
    }
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void drop_finish(bool clearVisuals) {
    if (clearVisuals) {
        memset(ledBuf1, 0, (size_t)g_dropBarLen * 3);
        ws_show_strip(PIN_LED_CH1, ledBuf1, (uint8_t)g_dropBarLen, ledBright1);
        drop_renderRingRaw(0, 0, 0);
        for (uint8_t i = 0; i < ledCount2; i++)          // ✅ v6.10: CH2 هم خاموش
            led_setPixel(ledBuf2, i, ledCount2, 0, 0, 0);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    }
    g_dropCh2FlashDur = 0;
    g_dropActive      = false;
    g_dropPhase       = DropPhase::IDLE;
    g_dropMissUntilMs = 0;
    if (g_activeEffect == EffectType::GAME && g_gameState == GameState::IDLE) {
        g_activeEffect = EffectType::NONE;
        g_userLedLock  = false;
    }
    g_ch1Mode       = Ch1Mode::SEVENSEG;
    g_lastCommandMs = millis();
}

static void drop_cancel(bool silent) {
    if (!g_dropActive) {
        if (!silent) drop_sendEvent(GEVT::DROP_CANCELED, 0);
        return;
    }
    drop_finish(true);
    if (!silent) drop_sendEvent(GEVT::DROP_CANCELED, 0);
}

// ✅ v6.4.1: شروع بازی قطره — اعتبارسنجی در هندلر DROP_ARM تکرار می‌شود
static bool drop_start(uint8_t keyNum, uint8_t r, uint8_t g, uint8_t b,
                       uint8_t seconds, uint8_t dropCount, uint8_t trailLen,
                       uint8_t dropLen, uint8_t actPct, bool ringEnable, bool missFb) {
    if (!keyIndexValid(keyNum))         return false;
    if (seconds  < 1 || seconds  > GAME_MAX_SECONDS) return false;
    if (dropCount < 1 || dropCount > DROP_MAX_DROPS) return false;
    if (trailLen  < 1 || trailLen  > DROP_MAX_TRAIL) return false;
    if (dropLen   < 1 || dropLen   > DROP_MAX_LEN)   return false;
    if (actPct    < 1 || actPct    > 100)  return false;

    g_dropKey      = keyNum;
    g_dropColor    = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;   // ✅ v7.0
    g_dropDrops    = dropCount;
    g_dropTrail    = trailLen;
    g_dropLen      = dropLen;
    g_dropActPct   = actPct;
    g_dropRing     = ringEnable;
    g_dropMissFb   = missFb;
    g_dropBarLen   = DROP_BAR_PIXELS;
    g_dropHalfMs   = (uint32_t)seconds * 500UL;   // نصف dur

    g_dropStartMs       = millis();
    g_dropLastRenderMs  = 0;
    g_dropPressedInFwd  = false;
    g_dropB0Sent        = false;
    g_dropActiveSent    = false;
    g_dropTailAngle     = 0.0f;
    g_dropMissUntilMs   = 0;
    g_dropPhase         = DropPhase::FORWARD;
    g_dropActive        = true;

    g_keyDisplayActive = false;
    g_ch1Mode      = Ch1Mode::TIMERBAR;   // CH1 = نوار
    g_userLedLock  = true;
    g_activeEffect = EffectType::GAME;    // موتور افکت رندر نمی‌کند؛ تاچ مجاز است

    memset(ledBuf1, 0, sizeof(ledBuf1));
    ws_show_strip(PIN_LED_CH1, ledBuf1, (uint8_t)g_dropBarLen, ledBright1);
    drop_renderRingRaw(0, 0, 0);
    return true;
}

// ✅ HIT: لمس در پنجره فعال → برگشت فوری از همان نقطه (مطابق ATmega)
static void drop_doHit(int16_t pos, uint32_t now) {
    g_dropPressedInFwd = true;
    drop_sendEvent(GEVT::DROP_HIT, drop_progressPct(pos));
    // ✅ v6.10: فیدبک CH2 — فلاش سفید 200ms (سناریو ۴ HIT)
    g_dropCh2FlashMs  = now; g_dropCh2FlashDur = 200;
    g_dropCh2R = 255; g_dropCh2G = 255; g_dropCh2B = 255;
    g_dropRetStartMs  = now;
    g_dropRetStartPos = (uint16_t)pos;
    uint32_t rt = (uint32_t)g_dropHalfMs * (uint32_t)pos / (uint32_t)(g_dropBarLen - 1);
    g_dropRetTotalMs = rt ? rt : 1;
    g_dropPhase = DropPhase::RETURN;
}

// لمس (لبه) در بازی قطره — true یعنی مصرف شد (رویداد لمس عمومی ارسال نمی‌شود)
static bool drop_onKeyPress(uint8_t keyNum) {
    if (!g_dropActive || keyNum != g_dropKey) return false;
    if (g_dropPhase == DropPhase::FORWARD) {
        uint32_t now = millis();
        int16_t  pos = drop_fwdPos(now - g_dropStartMs);
        if (pos >= drop_actPx()) {
            drop_doHit(pos, now);                     // ✅ لمس در پنجره فعال
        } else if (g_dropMissFb) {
            // ❌ فیدبک لمس خارج از پنجره (اختیاری — ATmega نادیده می‌گرفت)
            drop_sendEvent(GEVT::DROP_MISS, drop_progressPct(pos));
            g_dropMissUntilMs = now + DROP_MISS_MS;
            // ✅ v6.10: فلاش قرمز CH2 (180ms)
            g_dropCh2FlashMs  = now; g_dropCh2FlashDur = DROP_MISS_MS;
            g_dropCh2R = 255; g_dropCh2G = 0; g_dropCh2B = 0;
        }
    }
    return true;
}

static void taskDrop() {
    if (g_otaActive || !g_dropActive) return;
    uint32_t now = millis();
    // ✅ v6.7.1 FIX: throttle تطبیقی — نوارهای بزرگ (>64px) با ~30fps رندر می‌شوند
    // تا زمان قفل‌بودن IRQ برای بیت‌بنگ (~3ms در هر فریم 100px) نصف شود؛
    // در سرعت‌های فعلی قطره/تایمر تفاوت بصری محسوس نیست
    const uint32_t dropThrottleMs = (g_dropBarLen > 64) ? 32 : 16;
    if (now - g_dropLastRenderMs < dropThrottleMs) return;
    g_dropLastRenderMs = now;

    const int16_t lastPx = (int16_t)(g_dropBarLen - 1);
    const uint16_t drainLen = (uint16_t)(g_dropLen + g_dropTrail);   // = DRAIN_LEN

    // ✅ v6.10: فلاش CH2 — فیدبک HIT (سفید) / MISS (قرمز) سناریو ۴
    if (g_dropCh2FlashDur) {
        uint32_t fe = now - g_dropCh2FlashMs;
        if (fe >= g_dropCh2FlashDur) {
            g_dropCh2FlashDur = 0;
        } else {
            uint8_t k = (uint8_t)(255 - fe * 255 / g_dropCh2FlashDur);
            for (uint8_t i = 0; i < ledCount2; i++)
                led_setPixel(ledBuf2, i, ledCount2,
                             (uint8_t)((uint16_t)g_dropCh2R * k / 255),
                             (uint8_t)((uint16_t)g_dropCh2G * k / 255),
                             (uint8_t)((uint16_t)g_dropCh2B * k / 255));
            ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        }
    }

    switch (g_dropPhase) {

        // ── FORWARD: رفت 0→99 + پنجره فعال + SOFT_ZONE ──
        case DropPhase::FORWARD: {
            uint32_t t   = now - g_dropStartMs;
            int16_t  pos = drop_fwdPos(t);

            // رویداد باز شدن پنجره فعال (یک‌بار)
            if (pos >= drop_actPx() && !g_dropActiveSent) {
                g_dropActiveSent = true;
                drop_sendEvent(GEVT::DROP_ACTIVE, g_dropActPct);
            }

            // لمس سطح‌محور (مطابق ATmega — چک لمس قبل از چک پایان می‌آید):
            // کلید نگه‌داشته + پنجره فعال → HIT (حتی در خود پیکسل 99)
            if (pos >= drop_actPx() &&
                (touchHeldMask() & (uint8_t)(1u << g_dropKey))) {
                drop_doHit(pos, now);
                return;   // فریم بعد در RETURN رندر می‌شود
            }

            if (pos >= lastPx) {
                // نوک قطره به انتهای نوار رسید بدون لمس (معادل Bx0 در ATmega)
                if (!g_dropB0Sent) {
                    drop_sendEvent(GEVT::DROP_TIMEOUT, 100);
                    g_dropB0Sent = true;
                }
                g_dropPhase        = DropPhase::END_DRAIN;
                g_dropDrainStartMs = now;
                uint32_t dm = (uint32_t)drainLen * g_dropHalfMs / (uint32_t)lastPx;
                g_dropDrainTotalMs = dm ? dm : 1;
            }

            // SOFT_ZONE: fade-in در ۱۵ پیکسل اول رفت
            uint8_t fade = 255;
            if (pos >= 0 && pos < (int16_t)DROP_SOFT_ZONE)
                fade = (uint8_t)((uint32_t)pos * 255UL / DROP_SOFT_ZONE);

            drop_renderFrame(pos, false, fade, now);
            drop_renderRingFrame(pos, now);
            break;
        }

        // ── END_DRAIN: قطره از انتهای نوار خارج می‌شود (pos 100→120) ──
        case DropPhase::END_DRAIN: {
            uint32_t de = now - g_dropDrainStartMs;
            uint32_t dt = g_dropDrainTotalMs;
            int16_t  pos;
            if (de >= dt) {
                // دریِن تمام شد → برگشت از پیکسل 99 در نیم‌زمان
                g_dropPhase       = DropPhase::RETURN;
                g_dropRetStartMs  = now;
                g_dropRetStartPos = (uint16_t)lastPx;
                g_dropRetTotalMs  = g_dropHalfMs;
                pos = lastPx;
            } else {
                pos = (int16_t)((int32_t)g_dropBarLen +
                                (int16_t)((de * drainLen) / dt));
            }
            drop_renderFrame(pos, false, 255, now);
            drop_renderRingFrame(pos, now);
            break;
        }

        // ── RETURN: برگشت 99→0 (بدنه جلوی pos، دنباله پشت آن) ──
        case DropPhase::RETURN: {
            uint32_t re = now - g_dropRetStartMs;
            uint32_t rt = g_dropRetTotalMs;
            if (re >= rt) {
                drop_sendEvent(GEVT::DROP_END, g_dropPressedInFwd ? 1 : 0);
                drop_finish(true);
                break;
            }
            int16_t pos = (int16_t)((uint32_t)g_dropRetStartPos * (rt - re) / rt);
            if (pos == 0) {
                // قطره به خانه رسید → خروج از ابتدای نوار
                g_dropPhase        = DropPhase::START_DRAIN;
                g_dropDrainStartMs = now;
                uint32_t dm = (uint32_t)drainLen * rt /
                              (g_dropRetStartPos ? g_dropRetStartPos : 1);
                g_dropDrainTotalMs = dm ? dm : 1;
            }
            drop_renderFrame(pos, true, 255, now);
            drop_renderRingFrame(pos, now);
            break;
        }

        // ── START_DRAIN: قطره از ابتدای نوار خارج می‌شود (pos 0→−20) ──
        case DropPhase::START_DRAIN: {
            uint32_t de = now - g_dropDrainStartMs;
            uint32_t dt = g_dropDrainTotalMs;
            if (de >= dt) {
                drop_sendEvent(GEVT::DROP_END, g_dropPressedInFwd ? 1 : 0);
                drop_finish(true);
                break;
            }
            int16_t pos = (int16_t)(-((int32_t)((de * drainLen) / dt)));
            drop_renderFrame(pos, true, 255, now);
            drop_renderRingFrame(pos, now);
            break;
        }

        default: break;
    }
}

// ============================================================================
// GRID MODES ENGINE (✅ v6.5) — هم‌گام با ATmega64
// SSS→GRID_SELECT(0x61) | NNN→TESLA_START(0x70) | LLL→GRID_RAIN(0x62)
// MMM→GRID_STOP(0x63) | EEE→GRID_ERROR(0x60) — بلوک‌بندی v6.7
// ============================================================================
// معادل‌سازی معماری: ATmega یک برد با ۸ ریسه/۸ کلید است؛ در RBUS هر نود
// یک ریسه 100px (CH1) + یک حلقه کلید (CH3) دارد. انتخاب تصادفی N کلید
// (SSS n) و شمارش «ok» از آنِ master است؛ نودِ انتخاب‌شده فقط GRID_SELECT
// می‌گیرد و با فشرده‌شدن کلید، رویداد GRID_KEY_DONE می‌فرستد.
//
enum class GridMode : uint8_t {
    NONE       = 0,
    SELECT     = 1,   // حلقه سفید پالس می‌زند تا لمس کلید (≡ start_mode)
    LIGHTNING  = 2,   // رعد سفید روی نوار (≡ NNN)
    RAIN       = 3,   // باران (≡ LLL)
    ERROR      = 4    // چشمک قرمز خطا (≡ EEE با ERROR_EFFECT=5)
};

static GridMode g_gridMode         = GridMode::NONE;
static uint8_t  g_gridSeq          = 0;
static uint32_t g_gridPhaseMs      = 0;      // شروع حالت جاری
static uint32_t g_gridLastRenderMs = 0;
static uint16_t g_gridLightSpeed   = 1000;   // سرعت جاروی رعد (ثابت در هر سیکل)
static uint8_t  g_gridRainDrops    = GRID_RAIN_DROPS_DEF;
static uint8_t  g_gridRainTail     = GRID_RAIN_TAIL_DEF;
static float    g_gridRainPos[GRID_RAIN_DROPS_MAX];
static float    g_gridRainSpeed[GRID_RAIN_DROPS_MAX];
static uint32_t g_gridRainLastMs   = 0;

static void grid_sendEvent(uint8_t evt, uint8_t val) {
    uint8_t p[5] = { g_ee.nodeAddr, 0, evt, val, g_gridSeq++ };
    gameEventEmit(p);   // ✅ v7.17 POLL
}

static inline bool gridEffectActive() {
    return (g_activeEffect == EffectType::GRID_SELECT ||
            g_activeEffect == EffectType::GRID_LIGHTNING ||
            g_activeEffect == EffectType::GRID_RAIN ||
            g_activeEffect == EffectType::GRID_ERROR);
}

static void grid_finish(bool clearVisuals) {
    if (clearVisuals) {
        memset(ledBuf1, 0, (size_t)DROP_BAR_PIXELS * 3);
        ws_show_strip(PIN_LED_CH1, ledBuf1, DROP_BAR_PIXELS, ledBright1);
        drop_renderRingRaw(0, 0, 0);   // حلقه CH3 خاموش
    }
    g_gridMode = GridMode::NONE;
    if (gridEffectActive()) {
        g_activeEffect = EffectType::NONE;
        g_userLedLock  = false;
    }
    g_lastCommandMs = millis();
}

static void grid_cancel(bool silent) {
    (void)silent;
    if (g_gridMode == GridMode::NONE) return;
    grid_finish(true);
}

static void grid_start(GridMode mode, uint8_t rainDrops, uint8_t rainTail) {
    game_cancel(true);
    drop_cancel(true);
    if (g_tmrActive) tmr_finish(true);
    keyRingRgbCancel();          // ✅ v7.13: SELECT مالک CH3 می‌شود — حالت اکشن آزاد
    g_keyDisplayActive = false;  // ✅ v7.13: kd هم مه‌آلود نماند

    g_gridMode        = mode;
    g_gridPhaseMs     = millis();
    g_gridLastRenderMs = 0;

    switch (mode) {
        case GridMode::SELECT:
            g_activeEffect = EffectType::GRID_SELECT;
            break;
        case GridMode::LIGHTNING:
            g_activeEffect = EffectType::GRID_LIGHTNING;
            g_gridLightSpeed = (uint16_t)(500UL + rng32() % 1001UL);
            break;
        case GridMode::RAIN: {
            g_activeEffect = EffectType::GRID_RAIN;
            g_gridRainDrops = rainDrops;
            g_gridRainTail  = rainTail;
            for (uint8_t d = 0; d < rainDrops; d++) {
                g_gridRainPos[d]   = (float)(rng32() % DROP_BAR_PIXELS);
                g_gridRainSpeed[d] = GRID_RAIN_SPD_MIN +
                                     (float)(rng32() % 301UL) / 10.0f;
            }
            g_gridRainLastMs = millis();
            break;
        }
        case GridMode::ERROR:
            g_activeEffect = EffectType::GRID_ERROR;
            break;
        default: break;
    }
    g_userLedLock = true;
    memset(ledBuf1, 0, sizeof(ledBuf1));
    ws_show_strip(PIN_LED_CH1, ledBuf1, DROP_BAR_PIXELS, ledBright1);
    drop_renderRingRaw(0, 0, 0);
}

// لمس در حالت SELECT: کلید فشرده شد → رویداد + پایان حالت
// (≡ حذف بیت از active_keys_mask در handle_start_mode)
static bool grid_onSelectPress(uint8_t keyNum) {
    if (g_gridMode != GridMode::SELECT) return false;
    if (!keyIndexValid(keyNum))         return false;
    drop_renderRingRaw(0, 0, 0);
    grid_sendEvent(GEVT::GRID_KEY_DONE, keyNum);
    grid_finish(false);
    return true;
}

static void taskGrid() {
    if (g_otaActive || g_gridMode == GridMode::NONE) return;
    uint32_t now = millis();
    const uint32_t gridThrottleMs = (DROP_BAR_PIXELS > 64) ? 32 : 16;  // ✅ v6.7.1
    if (now - g_gridLastRenderMs < gridThrottleMs) return;
    g_gridLastRenderMs = now;

    const uint8_t barLen = DROP_BAR_PIXELS;

    switch (g_gridMode) {

        // ── SELECT: حلقه سفید پالس نرم (handle_start_mode در ATmega) ──
        case GridMode::SELECT: {
            uint16_t phase = (uint16_t)(now % GRID_SEL_PERIOD_MS);
            uint8_t  br = (phase < GRID_SEL_PERIOD_MS / 2)
                ? (uint8_t)(60u + (uint32_t)phase * 195u / (GRID_SEL_PERIOD_MS / 2))
                : (uint8_t)(255u - (uint32_t)(phase - GRID_SEL_PERIOD_MS / 2) * 195u /
                            (GRID_SEL_PERIOD_MS / 2));
            drop_renderRingRaw(br, br, br);
            break;
        }

        // ── LIGHTNING: رعد سفید جارویی (lightning_effect در ATmega) ──
        case GridMode::LIGHTNING: {
            uint32_t elapsed = now - g_gridPhaseMs;
            if (elapsed > GRID_LIGHT_RESET_MS) {
                g_gridPhaseMs    = now;
                elapsed          = 0;
                g_gridLightSpeed = (uint16_t)(500UL + rng32() % 1001UL);
            }
            // peak = 255-(1500-speed)*105/1000 → بازه 150..255 (مطابق ATmega)
            uint8_t  peak = (uint8_t)(255u - ((1500u - g_gridLightSpeed) * 105u) / 1000u);
            uint16_t pos  = (uint16_t)((elapsed * barLen) / g_gridLightSpeed);
            for (uint8_t i = 0; i < barLen; i++) {
                uint16_t dist = (i > pos) ? (i - pos) : (pos - i);
                uint8_t  br   = 0;
                if (dist < GRID_LIGHT_FADE)
                    br = (uint8_t)(peak - (uint16_t)dist * peak / GRID_LIGHT_FADE);
                ledBuf1[i].r = br; ledBuf1[i].g = br; ledBuf1[i].b = br;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, barLen, ledBright1);
            break;
        }

        // ── RAIN: باران با دنباله کوتاه (rain_effect در ATmega) ──
        case GridMode::RAIN: {
            if (now - g_gridRainLastMs >= GRID_RAIN_STEP_MS) {
                float dt = (now - g_gridRainLastMs) / 1000.0f;
                g_gridRainLastMs = now;
                for (uint8_t d = 0; d < g_gridRainDrops; d++) {
                    g_gridRainPos[d] += g_gridRainSpeed[d] * dt;
                    if (g_gridRainPos[d] >= (float)barLen) {
                        g_gridRainPos[d]   = 0.0f;   // چرخه مجدد از بالا
                        g_gridRainSpeed[d] = GRID_RAIN_SPD_MIN +
                                             (float)(rng32() % 301UL) / 10.0f;
                    }
                }
            }
            for (uint8_t i = 0; i < barLen; i++) {
                ledBuf1[i].r = 0; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            for (uint8_t d = 0; d < g_gridRainDrops; d++) {
                int16_t ipos  = (int16_t)g_gridRainPos[d];
                int16_t start = (int16_t)(ipos - g_gridRainTail);
                for (int16_t i = start; i <= ipos; i++) {
                    if (i < 0 || i >= (int16_t)barLen) continue;
                    uint8_t dist = (uint8_t)(ipos - i);
                    uint8_t br   = (uint8_t)(255 - dist * 255 / g_gridRainTail);
                    // رنگ 0xFEFFFE مطابق ATmega (سبز کامل + r/b تقریبا کامل)
                    uint8_t w = (uint8_t)((uint16_t)0xFE * br / 255);
                    if (br > ledBuf1[i].g) ledBuf1[i].g = br;
                    if (w > ledBuf1[i].r) ledBuf1[i].r = w;
                    if (w > ledBuf1[i].b) ledBuf1[i].b = w;
                }
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, barLen, ledBright1);
            break;
        }

        // ── ERROR: چشمک قرمز کل نوار + حلقه (ERROR_EFFECT=5 در ATmega) ──
        case GridMode::ERROR: {
            uint8_t on = ((now / GRID_ERR_BLINK_MS) & 1) ? 255 : 0;
            for (uint8_t i = 0; i < barLen; i++) {
                ledBuf1[i].r = on; ledBuf1[i].g = 0; ledBuf1[i].b = 0;
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, barLen, ledBright1);
            drop_renderRingRaw(on, 0, 0);
            break;
        }

        default: break;
    }
}

// ============================================================================
// EXIT IDLE MODE
// ============================================================================
// ============================================================================
// ✅ اکشن (0x42): موتور حلقه کلید RGB — لمس → فید‌اوت 200ms → فیداین 200ms → برگشت رنگ
// ============================================================================
static void keyRingRgbCancel() {
    g_keyRingRgbActive = false;
    g_keyRingRgbPhase  = 0;
}

static void keyRingRgb_onKeyPress(uint8_t keyNum) {
    if (!g_keyRingRgbActive || g_keyRingRgbPhase != 0) return;
    if (keyNum != g_keyRingRgbKey) return;
    g_keyRingRgbPhase  = 1;          // فید‌اوت 200ms شروع (v7.2)
    g_keyRingRgbFadeMs = millis();
}

static void taskKeyRingRgb() {
    if (g_otaActive || !g_keyRingRgbActive || g_keyRingRgbPhase == 0) return;
    uint32_t t = millis() - g_keyRingRgbFadeMs;
    uint16_t k = 255;               // 255=رنگ کامل، 0=خاموش
    if (g_keyRingRgbPhase == 1) {   // فید‌اوت
        if (t >= ACTION_FADE_MS) { g_keyRingRgbPhase = 2; g_keyRingRgbFadeMs = millis(); t = 0; }
        else k = (uint16_t)(255 - t * 255 / ACTION_FADE_MS);
    }
    if (g_keyRingRgbPhase == 2) {   // فید‌این — بازگشت به رنگ خودش ✅
        if (t >= ACTION_FADE_MS) { g_keyRingRgbPhase = 0; k = 255; }
        else k = (uint16_t)(t * 255 / ACTION_FADE_MS);
    }
    setKeyPixelsRgb(g_keyRingRgbKey,
                    (uint8_t)((uint16_t)g_keyRingRgbR * k / 255),
                    (uint8_t)((uint16_t)g_keyRingRgbG * k / 255),
                    (uint8_t)((uint16_t)g_keyRingRgbB * k / 255));
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

// ✅ v7.0: شناسایی نود — بی‌آدرس=چشمک قرمز آهسته | آدرس‌گیری=۲ فلاش سبز
static void taskNodeIdent() {
    if (g_otaActive || g_userLedLock) return;   // در حین بازی/محتوا مزاحم نمی‌شویم
    static uint32_t s_identMs = 0;              // ✅ v7.14: throttle 50ms —
    if (millis() - s_identMs < 50) return;      // قبلاً هر loop یک بیت‌بنگ CH3 = نویز دائمی
    s_identMs = millis();
    uint32_t now = millis();
    uint32_t col = 0;
    if (now < g_identGreenUntilMs) {
        col = ((now / 175) & 1) ? 0x00FF00 : 0x000000;   // تأیید آدرس: سبز ×۲
    } else if (g_ee.nodeAddr == 0) {
        col = ((now / 500) & 1) ? 0xFF0000 : 0x000000;   // بی‌آدرس: قرمز آهسته
    } else return;
    setKeyPixels(0, col);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

// ✅ v7.14: پوش تأخیری کانال‌های منحرف‌شده — بعد از پایان رندر، یک‌جا
static void taskLedFlush() {
    if (g_otaActive || g_inLedShow || !g_ledDirtyCh) return;
    uint8_t d = g_ledDirtyCh; g_ledDirtyCh = 0;
    if (d & 1) ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    if (d & 2) ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    if (d & 4) ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void exitIdleMode() {
    if (g_activeEffect == EffectType::IDLE ||
        g_activeEffect == EffectType::SELF_TEST) {
        g_commandProcessing = true;
        effect_stop();
        led_clear(0xFF);
        g_commandProcessing = false;
    }
    g_lastCommandMs = millis();
}

// ============================================================================
// TOUCH FEEDBACK
// ============================================================================
static void __attribute__((unused)) startTouchFeedback(uint8_t keyNum) {
    if (!keyIndexValid(keyNum)) return;
    g_touchFeedbackActive = true;
    g_touchFeedbackStartMs = millis();
    g_touchFeedbackKey = keyNum;

    setKeyPixels(keyNum, 0x00FF00);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);

    displayTwoDigitNumber(keyNum, 0, 0xFFFFFF);
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

static void taskTouchFeedback() {
    if (!g_touchFeedbackActive) return;
    if (millis() - g_touchFeedbackStartMs >= 100) {
        setKeyPixels(g_touchFeedbackKey, 0xFFFFFF);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
        g_touchFeedbackActive = false;
    }
}

// ============================================================================
// GAME ENGINE
// ============================================================================
static inline uint8_t game_displayIndex(uint8_t keyNum) {
    uint8_t nd = activeDisplays();
    if (nd == 0) return 0;
    return (keyNum < nd) ? keyNum : 0;
}

static inline uint32_t game_scaleColor(uint32_t color, uint8_t scale) {
    uint32_t r = ((color >> 16) & 0xFF) * scale / 255;
    uint32_t g = ((color >> 8)  & 0xFF) * scale / 255;
    uint32_t b = ( color        & 0xFF) * scale / 255;
    return (r << 16) | (g << 8) | b;
}

static inline uint32_t game_color() { return g_gameColor; }   // ✅ v7.0

static void game_sendEvent(uint8_t evt, uint8_t secValue) {
    uint8_t p[5] = { g_ee.nodeAddr, g_gameKey, evt, secValue, g_gameSeq++ };
    gameEventEmit(p);   // ✅ v7.17 POLL
}

// ✅ v6.0 FIX: از keyPixelCount() استفاده کن
static void game_clearKeyPixels() {
    uint8_t ppk = keyPixelCount();
    for (uint8_t i = 0; i < ppk; i++)
        led_setPixel(ledBuf3, g_gameKey * ppk + i, ledCount3, 0, 0, 0);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void game_clearDisplay() {
    uint8_t d = game_displayIndex(g_gameKey);
    for (uint8_t i = 0; i < SEGS_PER_DIGIT; i++) {
        uint16_t idx = d * SEGS_PER_DIGIT + i;
        led_setPixel(ledBuf1, idx, ledCount1, 0, 0, 0);
        led_setPixel(ledBuf2, idx, ledCount2, 0, 0, 0);
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    g_gameLastShownSec = -1;
}

static void game_showSeconds(uint16_t sec, uint32_t color, bool force) {
    if (sec > 99) sec = 99;
    if (!force && (int16_t)sec == g_gameLastShownSec) return;
    g_gameLastShownSec = (int16_t)sec;
    displayTwoDigitNumber(game_displayIndex(g_gameKey), (uint8_t)sec, color);
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

static void game_updateDrip(uint32_t now) {
    if (now - g_gameLastDripMs < GAME_DRIP_STEP_MS) return;
    g_gameLastDripMs = now;

    uint32_t col = game_color();
    uint8_t  br  = (col >> 16) & 0xFF;
    uint8_t  bg  = (col >> 8)  & 0xFF;
    uint8_t  bb  =  col        & 0xFF;
    uint8_t  ppk = keyPixelCount();  // ✅ v6.0 FIX
    uint16_t base = g_gameKey * ppk;

    for (uint8_t p = 0; p < ppk; p++) {
        int16_t dist = (int16_t)p - (int16_t)g_gameDripPos;
        if (dist < 0) dist = -dist;
        int16_t intensity = 255 - (dist * 160);
        if (intensity < 0) intensity = 0;
        led_setPixel(ledBuf3, base + p, ledCount3,
                     (uint8_t)((br * intensity) / 255),
                     (uint8_t)((bg * intensity) / 255),
                     (uint8_t)((bb * intensity) / 255));
    }
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    g_gameDripPos = (uint8_t)((g_gameDripPos + 1) % ppk);
}

static void game_finish(bool clearVisuals) {
    if (clearVisuals) {
        game_clearDisplay();
        game_clearKeyPixels();
    }
    g_gameState        = GameState::IDLE;
    g_gameRemainMs     = 0;
    g_gameTotalMs      = 0;
    g_gameTotalSec     = 0;
    g_gameLastShownSec = -1;
    g_gameDripPos      = 0;
    g_gameEngaged      = false;
    if (g_activeEffect == EffectType::GAME) {
        g_activeEffect = EffectType::NONE;
        g_userLedLock  = false;
    }
    g_lastCommandMs = millis();
}

// ✅ v6.0 FIX: tmr_finish + g_ch1Mode=SEVENSEG اضافه شد
static bool game_arm(uint8_t keyNum, uint8_t r, uint8_t g, uint8_t b, uint8_t seconds) {
    if (!keyIndexValid(keyNum)) return false;
    if (activeDisplays() == 0)  return false;
    if (seconds < 1 || seconds > GAME_MAX_SECONDS) return false;

    // ✅ v6.0 FIX: اگر نوار تایمر فعال است، اول آن را ببند
    if (g_tmrActive) {
        tmr_finish(true);
    }
    drop_cancel(true); grid_cancel(true);   // ✅ v6.4: بازی قطره هم لغو شود
    // ✅ v6.3: لغو key display فعال
    g_keyDisplayActive = false;
    // ✅ v6.0 FIX: پین CH1 را به حالت سون‌سگمنت برگردان
    g_ch1Mode = Ch1Mode::SEVENSEG;

    g_gameKey        = keyNum;
    g_gameColor      = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;   // ✅ v7.0
    g_gameTotalSec   = seconds;
    g_gameTotalMs    = (uint32_t)seconds * 1000UL;
    g_gameRemainMs   = g_gameTotalMs;
    g_gameLastTickMs = millis();
    g_gamePhaseMs    = g_gameLastTickMs;
    g_gameDripPos    = 0;
    g_gameLastDripMs = g_gameLastTickMs;
    g_gameEngaged    = true;
    g_gameState      = GameState::ARMED;

    g_activeEffect = EffectType::GAME;
    g_userLedLock  = true;

    game_clearKeyPixels();
    game_clearDisplay();
    game_showSeconds(seconds, game_scaleColor(game_color(), GAME_TIMER_DIM), true);
    return true;
}

static bool game_start(uint8_t keyNum) {
    if (!keyIndexValid(keyNum)) return false;
    if (g_gameState != GameState::ARMED || g_gameKey != keyNum) return false;
    if (g_gameRemainMs == 0) return false;

    g_gameState      = GameState::ACTIVE;
    g_gameDripPos    = 0;
    g_gameLastDripMs = millis();
    g_activeEffect   = EffectType::GAME;
    g_userLedLock    = true;

    setKeyPixels(keyNum, game_color());
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    return true;
}

// ✅ v6.0 FIX: keyNum نامعتبر → NACK به جای نگاشت بی‌صدا به 0
static void game_fail(uint8_t keyNum) {
    // ✅ v6.4 FIX (W-1): رویداد دوبل حذف شد — قبلاً در حالت REVERSE ابتدا
    // SUCCESS_END و بلافاصله بعد FAIL_END ارسال می‌شد (پیام متناقض برای master).
    // الان: فقط یک FAIL_END در پایان فاز FAIL ارسال می‌شود (در taskGame).
    g_gameKey      = keyNum;
    g_gameEngaged  = true;
    g_gameState    = GameState::FAIL;
    g_gamePhaseMs  = millis();
    g_gameRemainMs = 0;
    g_activeEffect = EffectType::GAME;
    g_userLedLock  = true;

    setKeyPixels(keyNum, 0xFF0000);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    game_clearDisplay();
}

static void game_cancel(bool silent) {
    if (g_gameState == GameState::IDLE) {
        if (!silent) game_sendEvent(GEVT::CANCELED, 0);
        return;
    }
    game_finish(true);
    if (!silent) game_sendEvent(GEVT::CANCELED, 0);
}

static bool game_onKeyPress(uint8_t keyNum) {
    if (!g_gameEngaged) return false;
    if (!keyIndexValid(keyNum)) return false;

    if (g_gameState == GameState::ACTIVE && keyNum == g_gameKey) {
        uint8_t remainSec = (uint8_t)((g_gameRemainMs + 999UL) / 1000UL);
        game_sendEvent(GEVT::KEY_HIT, remainSec);

        g_gameState   = GameState::HIT_BLANK;
        g_gamePhaseMs = millis();
        g_gameRemainMs = 0;
        game_clearKeyPixels();
        game_clearDisplay();
        return true;
    }

    if (g_gameState == GameState::ARMED || g_gameState == GameState::ACTIVE) {
        game_sendEvent(GEVT::WRONG_KEY, 0);
        return true;
    }
    return false;
}

// ✅ v6.3: throttle متغیر
static uint32_t g_gameLastRenderMs = 0;

static void taskGame() {
    if (g_otaActive) return;
    if (g_gameState == GameState::IDLE) return;

    uint32_t now = millis();

    // ✅ v6.3 CRITICAL FIX: Throttle — جلوگیری از سوختن پیکسل
    // شمارش تایمر همیشه اجرا می‌شود، فقط رندر throttle می‌شود
    // (dt برای شمارش باید دقیق باشد)

    if (g_gameState == GameState::ARMED || g_gameState == GameState::ACTIVE) {
        uint32_t dt = now - g_gameLastTickMs;
        if (dt) {
            g_gameLastTickMs = now;
            if (g_gameRemainMs > dt) g_gameRemainMs -= dt;
            else                     g_gameRemainMs = 0;
        }
        if (g_gameRemainMs == 0) {
            game_sendEvent(GEVT::TIMEOUT, 0);
            game_finish(true);
            return;
        }
    }

    // ✅ v6.3: Throttle رندر — حداکثر ~60fps
    bool renderOk = (g_activeEffect == EffectType::GAME) &&
                    (now - g_gameLastRenderMs >= 16);
    if (renderOk) g_gameLastRenderMs = now;

    switch (g_gameState) {
        case GameState::ARMED: {
            if (renderOk) {
                uint16_t sec = (uint16_t)((g_gameRemainMs + 999UL) / 1000UL);
                game_showSeconds(sec, game_scaleColor(game_color(), GAME_TIMER_DIM), false);
            }
            break;
        }
        case GameState::ACTIVE: {
            if (renderOk) {
                uint16_t sec = (uint16_t)((g_gameRemainMs + 999UL) / 1000UL);
                game_showSeconds(sec, game_scaleColor(game_color(), GAME_TIMER_DIM), false);
                game_updateDrip(now);
            }
            break;
        }
        case GameState::HIT_BLANK: {
            if (now - g_gamePhaseMs >= GAME_RESET_DISPLAY_MS) {
                g_gameState   = GameState::HIT_FLASH;
                g_gamePhaseMs = now;
                if (renderOk) {
                    uint32_t dim = ((uint32_t)GAME_FLASH_LEVEL << 16) |
                                   ((uint32_t)GAME_FLASH_LEVEL << 8)  |
                                    (uint32_t)GAME_FLASH_LEVEL;
                    game_showSeconds(88, dim, true);
                }
            }
            break;
        }
        case GameState::HIT_FLASH: {
            if (now - g_gamePhaseMs >= GAME_FLASH_MS) {
                g_gameState   = GameState::REVERSE;
                g_gamePhaseMs = now;
                g_gameLastShownSec = -1;
            }
            break;
        }
        case GameState::REVERSE: {
            uint32_t elapsed = now - g_gamePhaseMs;
            if (elapsed >= GAME_REVERSE_MS) {
                game_sendEvent(GEVT::SUCCESS_END, 0);
                game_finish(true);
                break;
            }
            if (renderOk) {
                uint32_t remain = GAME_REVERSE_MS - elapsed;
                uint16_t sec = (uint16_t)((uint32_t)g_gameTotalSec * remain / GAME_REVERSE_MS);
                if (sec > 99) sec = 99;
                game_showSeconds(sec, game_scaleColor(game_color(), GAME_TIMER_DIM), false);
            }
            break;
        }
        case GameState::FAIL: {
            if (now - g_gamePhaseMs >= GAME_FAIL_MS) {
                game_sendEvent(GEVT::FAIL_END, 0);
                game_finish(true);
            }
            break;
        }
        default: break;
    }
}

// ============================================================================
// FADE ENGINE
// ============================================================================
static void startChannelFade(uint8_t chNum) {
    if (chNum == 1 && !g_fadeCh1.active) {
        memcpy(g_fadeCh1.origin, ledBuf1, sizeof(ledBuf1));
        g_fadeCh1.step = 0; g_fadeCh1.returning = false;
        g_fadeCh1.active = true; g_fadeCh1.lastTick = millis();
    } else if (chNum == 2 && !g_fadeCh2.active) {
        memcpy(g_fadeCh2.origin, ledBuf2, sizeof(ledBuf2));
        g_fadeCh2.step = 0; g_fadeCh2.returning = false;
        g_fadeCh2.active = true; g_fadeCh2.lastTick = millis();
    }
}

static void updateChannelFades(uint32_t now) {
    if (g_fadeCh1.active && (now - g_fadeCh1.lastTick >= FADEOUT_STEP_MS)) {
        g_fadeCh1.lastTick = now;
        if (!g_fadeCh1.returning) {
            g_fadeCh1.step++;
            if (g_fadeCh1.step >= FADEOUT_STEPS) {
                memset(ledBuf1, 0, sizeof(ledBuf1));
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
                g_fadeCh1.step = 0; g_fadeCh1.returning = true;
            } else {
                uint8_t f = (uint8_t)(255 - (uint32_t)255 * g_fadeCh1.step / FADEOUT_STEPS);
                for (uint8_t i = 0; i < ledCount1; i++) {
                    ledBuf1[i].r = (uint16_t)g_fadeCh1.origin[i].r * f / 255;
                    ledBuf1[i].g = (uint16_t)g_fadeCh1.origin[i].g * f / 255;
                    ledBuf1[i].b = (uint16_t)g_fadeCh1.origin[i].b * f / 255;
                }
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
            }
        } else {
            g_fadeCh1.step++;
            if (g_fadeCh1.step >= FADEIN_STEPS) {
                memcpy(ledBuf1, g_fadeCh1.origin, sizeof(ledBuf1));
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
                g_fadeCh1.active = false; g_fadeCh1.returning = false;
            } else {
                uint8_t f = (uint8_t)((uint32_t)255 * g_fadeCh1.step / FADEIN_STEPS);
                for (uint8_t i = 0; i < ledCount1; i++) {
                    ledBuf1[i].r = (uint16_t)g_fadeCh1.origin[i].r * f / 255;
                    ledBuf1[i].g = (uint16_t)g_fadeCh1.origin[i].g * f / 255;
                    ledBuf1[i].b = (uint16_t)g_fadeCh1.origin[i].b * f / 255;
                }
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
            }
        }
    }
    if (g_fadeCh2.active && (now - g_fadeCh2.lastTick >= FADEOUT_STEP_MS)) {
        g_fadeCh2.lastTick = now;
        if (!g_fadeCh2.returning) {
            g_fadeCh2.step++;
            if (g_fadeCh2.step >= FADEOUT_STEPS) {
                memset(ledBuf2, 0, sizeof(ledBuf2));
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
                g_fadeCh2.step = 0; g_fadeCh2.returning = true;
            } else {
                uint8_t f = (uint8_t)(255 - (uint32_t)255 * g_fadeCh2.step / FADEOUT_STEPS);
                for (uint8_t i = 0; i < ledCount2; i++) {
                    ledBuf2[i].r = (uint16_t)g_fadeCh2.origin[i].r * f / 255;
                    ledBuf2[i].g = (uint16_t)g_fadeCh2.origin[i].g * f / 255;
                    ledBuf2[i].b = (uint16_t)g_fadeCh2.origin[i].b * f / 255;
                }
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            }
        } else {
            g_fadeCh2.step++;
            if (g_fadeCh2.step >= FADEIN_STEPS) {
                memcpy(ledBuf2, g_fadeCh2.origin, sizeof(ledBuf2));
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
                g_fadeCh2.active = false; g_fadeCh2.returning = false;
            } else {
                uint8_t f = (uint8_t)((uint32_t)255 * g_fadeCh2.step / FADEIN_STEPS);
                for (uint8_t i = 0; i < ledCount2; i++) {
                    ledBuf2[i].r = (uint16_t)g_fadeCh2.origin[i].r * f / 255;
                    ledBuf2[i].g = (uint16_t)g_fadeCh2.origin[i].g * f / 255;
                    ledBuf2[i].b = (uint16_t)g_fadeCh2.origin[i].b * f / 255;
                }
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            }
        }
    }
}

// ============================================================================
// EFFECT HELPERS
// ============================================================================
static void effect_stop() {
    g_activeEffect = EffectType::NONE;
    g_userLedLock  = false;
}

static void startSpinEffect() {
    g_effectStartMs  = millis();
    g_lastFxRenderMs = 0;
    g_userLedLock    = true;
    g_fadeCh1.active = false;
    g_fadeCh2.active = false;
    g_activeEffect   = EffectType::SPIN;
}

static void startBootSpin() {
    startSpinEffect();
    g_bootSpinActive  = true;
    g_bootSpinStartMs = millis();
}

static void taskBootSpin() {
    if (!g_bootSpinActive) return;
    if (millis() - g_bootSpinStartMs >= BOOT_SPIN_MS) {
        g_bootSpinActive = false;
        if (g_activeEffect == EffectType::SPIN) {
            effect_stop();
            led_clear(0xFF);
        }
        return;
    }
    if (g_activeEffect != EffectType::SPIN) {
        g_bootSpinActive = false;
    }
}

static void rain_init() {
    for (uint8_t i = 0; i < MAX_BUFFER_CAP; i++) {
        g_rain1[i].pos = -1; g_rain2[i].pos = -1;
        g_rainTail1[i] = 0;  g_rainTail2[i] = 0;
    }
    g_rainNextSpawn = millis();
}

static void matrix_init() {
    memset(g_matrixBrt1, 0, sizeof(g_matrixBrt1));
    memset(g_matrixBrt2, 0, sizeof(g_matrixBrt2));
    g_matrixHead1 = -1; g_matrixHead2 = -1;
    g_matrixNextDrop = millis();
}

static void lightning_init() {
    uint32_t rnd = ESP.getChipId() ^ millis();
    g_lightFlashTotal = LIGHTNING_MIN_FLASH +
                        (uint8_t)(rnd % (LIGHTNING_MAX_FLASH - LIGHTNING_MIN_FLASH + 1));
    g_lightFlashDone = 0; g_lightOn = false;
    g_lightNextEvent = millis() + LIGHTNING_GAP_MS;
}

// ✅ v7.8: رویداد لمس هنگام ترافیک OTA روی باس صف می‌شود (نه حذف — قانون «لمس
// از دست نرود») و بعد از سکوت ۲ ثانیه flush می‌شود — ریشه‌ی تصادم با چانک OTA
static uint32_t g_lastOtaBusMs = 0;
#define TOUCH_OTA_QUIET_MS 2000UL
struct TouchDeferred { uint8_t addr, ch, evt; uint32_t ts; uint8_t longFmt; };
static TouchDeferred g_touchQ[8];
static uint8_t       g_touchQHead = 0, g_touchQTail = 0;
static uint8_t       g_touchQCount = 0;   // ✅ v7.12 QC-PATCH (SW-002، ادغام مجدد v7.17): شمارنده صریح —
                                          // قبلاً head==tail در حالت پر «خالی» تشخیص داده می‌شد و رویداد ۸م کل صف را می‌پاکید
static inline bool touchBusBusy() {
    return (g_lastOtaBusMs != 0 && (millis() - g_lastOtaBusMs < TOUCH_OTA_QUIET_MS));
}
static void touchEmit(uint8_t addr, uint8_t ch, uint8_t evt,
                      uint32_t now, uint8_t longFmt = 0, bool latch = true) {
    // ✅ v7.17 POLL-PATCH: latch وضعیت — همیشه، صرف‌نظر از مسیر ارسال
    // (latch=false فقط برای بازپخش رویداد صف‌شده در taskTouchFlush — قبلاً لچ شده)
    if (latch) {
        g_pTouchSeq++;                                // هر رویداد لمس = یک لبه
        if (evt == TOUCH_PRESSED)  g_pTouchMask |=  (uint8_t)(1u << (ch & 7));
        if (evt == TOUCH_RELEASED) g_pTouchMask &= ~(uint8_t)(1u << (ch & 7));
        g_pDirty = true;
    }
    // ✅ v7.17 POLL-PATCH: جاروب فعال یا باس مشغول → فقط صف (پوش یدکی/after-OTA)
    if (pollLinkUp() || touchBusBusy()) {
        if (g_touchQCount >= 8) {   // ✅ v7.12 QC-PATCH (SW-002): صف پر → فقط «قدیمی‌ترین» قربانی می‌شود (نه همه)
            g_touchQHead = (uint8_t)((g_touchQHead + 1) % 8);
            g_touchQCount--;
        }
        TouchDeferred* d = &g_touchQ[g_touchQTail];
        d->addr = addr; d->ch = ch; d->evt = evt; d->ts = now; d->longFmt = longFmt;
        g_touchQTail = (uint8_t)((g_touchQTail + 1) % 8);
        g_touchQCount++;
        return;
    }
    if (longFmt) {
        uint8_t p[4] = {addr, evt, g_touchSeqNum++, ch};
        RS485::sendFrame(ADDR_MASTER, CMD::TOUCH_EVENT, 4, p);
    } else {
        uint8_t p[7] = {addr, evt, g_touchSeqNum++, ch,
                        (uint8_t)(now >> 16), (uint8_t)(now >> 8), (uint8_t)now};
        RS485::sendFrame(ADDR_MASTER, CMD::TOUCH_EVENT, 7, p);
    }
}
static void taskTouchFlush() {
    // ✅ v7.17 POLL-PATCH: pollLinkUp → سرویس زنده است و وضعیت را از snapshot می‌خواند؛
    // صف فقط «بافر پوش یدکی» است (سرویس قدیمی / سرویس غایب) → drain ممنوع
    if (g_otaActive || g_touchQCount == 0 || touchBusBusy() || pollLinkUp()) return;
    while (g_touchQCount > 0) {   // ✅ v7.12 QC-PATCH (SW-002): شمارنده صریح
        TouchDeferred* d = &g_touchQ[g_touchQHead];
        touchEmit(d->addr, d->ch, d->evt, d->ts, d->longFmt, /*latch=*/false);   // باس ساکت → مستقیم می‌رود
        g_touchQHead = (uint8_t)((g_touchQHead + 1) % 8);
        g_touchQCount--;
    }
}
// ✅ v7.17 POLL-PATCH: با رسیدن جاروب، صف لمس خالی می‌شود — سرویس همه‌چیز را
// از diff snapshot (ماسک+seq) دریافته؛ رویدادهای صفی تکراری‌اند. رویدادهای
// «بعد از» این جاروب دوباره صف می‌شوند تا اگر سرویس غیب کرد، پوش یدکی برشان دارد.
static void pollTouchQClear() {
    g_touchQHead = g_touchQTail = 0;
    g_touchQCount = 0;
}

// ============================================================================
// TOUCH MANAGER
// ============================================================================
struct SoftwareTouchCh {
    uint8_t  pin;
    uint8_t  id;
    uint8_t  state;
    uint8_t  accumulator;
    uint32_t lastDebounceMs;
};

class TouchMgr {
public:
    SoftwareTouchCh ch[TOUCH_CHANNELS] = {
        {PIN_TOUCH_CH0, 0, TOUCH_RELEASED, 0, 0},
#if TOUCH_CHANNELS > 1
        {PIN_TOUCH_CH1, 1, TOUCH_RELEASED, 0, 0},
#endif
    };

    void begin() {
        pinMode(PIN_TOUCH_CH0, INPUT_PULLUP);
        // PIN_TOUCH_CH1 فقط اگر TOUCH_CHANNELS > 1 باشد
        if (TOUCH_CHANNELS > 1) pinMode(PIN_TOUCH_CH1, INPUT_PULLUP);
    }

    void tick(uint8_t nodeAddr) {
        if (g_otaActive) return;
        // ✅ v7.14: پنجره آرام‌شدن — بعد از نوشتن LED / TX رادیو، نمونه‌برداری
        // تاچ ممنوع (جهش جریان = LOW فانتوم روی سنسور خازنی)
        if (millis() - g_lastNoiseMs < TOUCH_SETTLE_MS) return;
        uint32_t now = millis();
        if (now - lastScanMs < 10) return;
        lastScanMs = now;
        static uint8_t sP[2] = {0, 0}, sR[2] = {0, 0};   // ✅ v7.14: debounce متقارن

        // ✅ v7.6 حالت پا: منبع تاچ = BS814A-2 (I2C) — کلید۱=چپ، کلید۴=راست
        if (g_footMode) {
            if (now - g_bootMs < TOUCH_BOOT_GRACE_MS) return;
            // ✅ v7.8: debounce متقارن (الگوی مرجع BS814A کاربر) — پرس ۵ نمونه
            // لمس + رهاشدن ۵ نمونه رها (قبلاً یک نمونه نویز = RELEASE → فلیکر لاگ)
            uint8_t keys = bs814a_read_keys();
            bool rawL = keys & BS814A_KEY4_BIT;   // K4=پد چپ
            bool rawR = keys & BS814A_KEY1_BIT;   // K1=پد راست
            static uint8_t pL = 0, rL = 0, pR = 0, rR = 0;
            static bool    stL = false, stR = false;
            pL = rawL ? (uint8_t)(pL < 5 ? pL + 1 : 5) : 0;
            rL = rawL ? 0 : (uint8_t)(rL < 5 ? rL + 1 : 5);
            pR = rawR ? (uint8_t)(pR < 5 ? pR + 1 : 5) : 0;
            rR = rawR ? 0 : (uint8_t)(rR < 5 ? rR + 1 : 5);
            if (pL >= 5 && !stL) {
                stL = true; rL = 0;
                foot_onKeyPress(0);
                triggerFootEvent(nodeAddr, 0, TOUCH_PRESSED, now);
            } else if (rL >= 5 && stL) {
                stL = false; pL = 0;
                triggerFootEvent(nodeAddr, 0, TOUCH_RELEASED, now);
            }
            if (pR >= 5 && !stR) {
                stR = true; rR = 0;
                foot_onKeyPress(1);
                triggerFootEvent(nodeAddr, 1, TOUCH_PRESSED, now);
            } else if (rR >= 5 && stR) {
                stR = false; pR = 0;
                triggerFootEvent(nodeAddr, 1, TOUCH_RELEASED, now);
            }
            return;
        }

        bool touchAllowed = (g_activeEffect == EffectType::NONE ||
                             g_activeEffect == EffectType::SPIN ||
                             g_activeEffect == EffectType::PULSE ||
                             g_activeEffect == EffectType::IDLE ||
                             g_activeEffect == EffectType::GAME ||
                             g_activeEffect == EffectType::MATRIX_PRO ||
                             g_activeEffect == EffectType::EQUALIZER ||
                             g_activeEffect == EffectType::GRID_SELECT);  // ✅ v6.5
        if (!touchAllowed) {
            for (int i = 0; i < TOUCH_CHANNELS; i++) {
                ch[i].accumulator = 0; ch[i].state = TOUCH_RELEASED; sP[i] = 0; sR[i] = 0;
            }
            return;
        }

        bool bootGrace = (now - g_bootMs < TOUCH_BOOT_GRACE_MS);
        for (int i = 0; i < TOUCH_CHANNELS; i++) {
            uint8_t sample = digitalRead(ch[i].pin);
            if (bootGrace) {
                ch[i].accumulator = 0; ch[i].state = TOUCH_RELEASED; sP[i] = 0; sR[i] = 0;
                continue;
            }
            // ✅ v7.14: متقارن — پرس ۵×لمس متوالی، رها ۵×رها (نویز فانتوم حذف)
            sP[i] = (sample == LOW) ? (uint8_t)(sP[i] < 5 ? sP[i] + 1 : 5) : 0;
            sR[i] = (sample == LOW) ? 0 : (uint8_t)(sR[i] < 5 ? sR[i] + 1 : 5);

            if (sP[i] >= 5 && ch[i].state == TOUCH_RELEASED) {
                ch[i].state = TOUCH_PRESSED; ch[i].lastDebounceMs = now; sR[i] = 0;
                exitIdleMode();

                // ✅ v7.0 هاید: لمس → «00» + حلقه «سفید» تا دستور جدید (مشخصه کاربر)
                if (g_keyDisplayActive && (uint8_t)i == g_keyDisplayNum) {
                    g_keyDisplayActive = false;
                    // ✅ v7.3 (مشخصه کاربر): «00» هم مثل حلقه «سفید» نمایش داده شود
                    displayTwoDigitNumber(g_keyDisplayNum, 0, 0xFFFFFF);
                    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
                    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
                    setKeyPixels(g_keyDisplayNum, 0xFFFFFF);
                    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
                    triggerEvent(nodeAddr, i, TOUCH_PRESSED, now);
                    continue;
                }

                bool gameConsumed = tmr_onKeyPress((uint8_t)i);
                if (!gameConsumed) gameConsumed = game_onKeyPress((uint8_t)i);
                if (!gameConsumed) gameConsumed = drop_onKeyPress((uint8_t)i);
                if (!gameConsumed) gameConsumed = grid_onSelectPress((uint8_t)i);
                keyRingRgb_onKeyPress((uint8_t)i);
                // ✅ v7.0 RULE FIX: رویداد لمس «همیشه» ارسال می‌شود — حتی وقتی
                // وایبرون/تسلا آن را مصرف کرده (قانون: لمس نباید از دست برود)
                (void)gameConsumed;
                triggerEvent(nodeAddr, i, TOUCH_PRESSED, now);
            } else if (sR[i] >= 5 && ch[i].state == TOUCH_PRESSED) {
                ch[i].state = TOUCH_RELEASED; ch[i].lastDebounceMs = now; sP[i] = 0;
                if (!g_gameEngaged && !g_tmrActive && !g_dropActive &&
                    g_gridMode == GridMode::NONE)
                    triggerEvent(nodeAddr, i, TOUCH_RELEASED, now);
            }
            if (ch[i].state == TOUCH_PRESSED &&
                (now - ch[i].lastDebounceMs >= TOUCH_LONG_PRESS_MS)) {
                ch[i].lastDebounceMs = now;
                triggerLongEvent(nodeAddr, i);
            }
        }
    }

    uint8_t mask() const {
        uint8_t m = 0;
        for (int i = 0; i < TOUCH_CHANNELS; i++)
            if (ch[i].state == TOUCH_PRESSED) m |= (1 << i);
        return m;
    }

private:
    uint32_t lastScanMs = 0;
    void triggerEvent(uint8_t nodeAddr, uint8_t chIdx, uint8_t evtType, uint32_t now) {
        touchEmit(nodeAddr, ch[chIdx].id, evtType, now);   // ✅ v7.8: صف هنگام OTA
    }
    void triggerEvent_old(uint8_t nodeAddr, uint8_t chIdx, uint8_t evtType, uint32_t now) {
        uint8_t p[7] = {nodeAddr, evtType, g_touchSeqNum++, ch[chIdx].id,
                        (uint8_t)(now >> 16), (uint8_t)(now >> 8), (uint8_t)now};
        RS485::sendFrame(ADDR_MASTER, CMD::TOUCH_EVENT, 7, p);
    }
    void triggerLongEvent(uint8_t nodeAddr, uint8_t chIdx) {
        touchEmit(nodeAddr, ch[chIdx].id, TOUCH_LONG, millis(), 1);   // ✅ v7.8
    }
    // ✅ v7.6: رویداد لمس پا — side=0 چپ / 1 راست (BS814A کلید۱/۴)
    void triggerFootEvent(uint8_t nodeAddr, uint8_t side, uint8_t evtType, uint32_t now) {
        touchEmit(nodeAddr, side, evtType, now);   // ✅ v7.8: صف هنگام OTA
    }
};
static TouchMgr g_touch;

// ✅ v6.4.1: ماسک لحظه‌ای کلیدهای نگه‌داشته‌شده — برای HIT سطح‌محور بازی قطره
// (معادل خواندن مستقیم PINA در ATmega64)
static uint8_t touchHeldMask() { return g_touch.mask(); }

// ============================================================================
// ADC MANAGER
// ============================================================================
class AdcMgr {
public:
    bool autoMode = false;
    uint16_t last = 0;
    void tick(uint8_t nodeAddr) {
        if (g_otaActive || !autoMode) return;
        uint32_t now = millis();
        if (now - ms < ADC_POLL_MS) return;
        ms = now;
        uint16_t v = analogRead(PIN_ADC);
        int32_t d = (int32_t)v - (int32_t)last;
        if (d < 0) d = -d;
        if (d >= ADC_THRESHOLD) {
            last = v;
            // ✅ v7.17 POLL-PATCH: جاروب فعال → خودفرستی ADC ممنوع؛ مقدار از
            // snapshot پاسخ POLL_STATE خوانده می‌شود (g_adc.read زنده)
            if (pollLinkUp()) return;
            uint8_t p[4] = {nodeAddr, 0, (uint8_t)(v >> 8), (uint8_t)v};
            RS485::sendFrame(ADDR_MASTER, CMD::ADC_EVENT, 4, p);
        }
    }
    uint16_t read() { last = analogRead(PIN_ADC); return last; }
private:
    uint32_t ms = 0;
};
static AdcMgr g_adc;

// ============================================================================
// OTA MANAGER
// ============================================================================
class OtaMgr {
public:
    enum class St : uint8_t { IDLE, WAIT_START, RECEIVING };
    St state = St::IDLE;
    bool fwStarted = false, magicOk = false;
    uint32_t lastActMs = 0;
    uint16_t expectPkt = 0;
    uint32_t written = 0, totalSize = 0, maxSpace = 0;

    bool active() const { return state != St::IDLE; }

    void enter() {
        g_otaActive = true; state = St::WAIT_START;
        fwStarted = false; magicOk = false;
        expectPkt = 0; written = 0; totalSize = 0;
        maxSpace = (ESP.getFreeSketchSpace() - 0x1000) & 0xFFFFF000;
        if (maxSpace < 64UL * 1024UL) maxSpace = 0;
        lastActMs = millis(); RS485::flush_rx(); ESP.wdtFeed();
    }

    void abortLocal() {
        if (fwStarted) Update.end(false);
        fwStarted = false; magicOk = false;
        state = St::IDLE; g_otaActive = false;
        ws_boot_safe_before_restart();
    }

    void kick() { lastActMs = millis(); }

    bool timedOut() const {
        if (!active()) return false;
        uint32_t lim = (state == St::WAIT_START) ? OTA_IDLE_MS : OTA_PKT_MS;
        return (millis() - lastActMs) >= lim;
    }

    void handleStart(const uint8_t* data, uint8_t dLen) {
        kick();
        if (dLen < 4) { RS485::sendNACK(CMD::UPDATE_START, 0x01); return; }
        uint32_t sz = (uint32_t)data[0] | ((uint32_t)data[1]<<8) |
                      ((uint32_t)data[2]<<16) | ((uint32_t)data[3]<<24);
        if (sz < 1024UL || maxSpace == 0 || sz > maxSpace) {
            uint8_t nk = 0x01;
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &nk); return;
        }
        if (fwStarted) { Update.end(false); fwStarted = false; }
        if (!Update.begin(sz, U_FLASH)) {
            uint8_t nk = 0x01;
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &nk); return;
        }
        fwStarted = true; magicOk = false;
        totalSize = sz; written = 0; expectPkt = 0;
        state = St::RECEIVING;
        uint8_t ok = 0x00;
        RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &ok);
    }

    void handleData(const uint8_t* data, uint8_t dLen) {
        kick();
        if (!fwStarted || dLen < 5) { RS485::sendNACK(CMD::UPDATE_DATA, 0x02); return; }
        uint16_t pkt = (uint16_t)data[0] | ((uint16_t)data[1] << 8);
        uint8_t blkLen = (uint8_t)(dLen - 4);
        const uint8_t* blk = &data[2];
        uint16_t crcRx = (uint16_t)data[dLen-2] | ((uint16_t)data[dLen-1] << 8);
        uint16_t crcCalc = crc16_ccitt(blk, blkLen);
        auto ack3 = [&](uint8_t st) {
            uint8_t r[3] = {(uint8_t)(pkt & 0xFF), (uint8_t)(pkt >> 8), st};
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 3, r);
        };
        if (crcCalc != crcRx)             { ack3(0x03); return; }
        if (pkt + 1 == expectPkt)         { ack3(0x00); return; }
        if (pkt != expectPkt)             { ack3(0x02); return; }
        if (written + blkLen > totalSize) { ack3(0x01); return; }
        if (pkt == 0) {
            if (blkLen < 1 || blk[0] != 0xE9) {
                ack3(0x05); Update.end(false);
                fwStarted = false; state = St::WAIT_START; return;
            }
            magicOk = true;
        }
        size_t w = Update.write(const_cast<uint8_t*>(blk), blkLen);
        ESP.wdtFeed(); yield();
        if (w != blkLen) { ack3(0x02); return; }
        written += blkLen; expectPkt++;
        ack3(0x00);
    }

    void handleEnd() {
        kick();
        if (!fwStarted) { RS485::sendNACK(CMD::UPDATE_END, 0x02); return; }
        if (!magicOk || written != totalSize) {
            uint8_t nk = 0x04;
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &nk);
            abortLocal(); return;
        }
        if (Update.end(true)) {
            uint8_t ok = 0x00;
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &ok);
            ws_boot_safe_before_restart();
            delay(250); ESP.restart();
        } else {
            uint8_t nk = 0x01;
            RS485::sendFrame(ADDR_MASTER, CMD::UPDATE_ACK, 1, &nk);
            abortLocal();
        }
    }
};
static OtaMgr g_ota;

// ============================================================================
// EFFECTS ENGINE
// ============================================================================
static inline void fx_paintPixel(Pixel* buf, uint8_t idx, uint8_t brt) {
    if (idx >= MAX_BUFFER_CAP) return;
    buf[idx].r = (uint16_t)g_effectColorR * brt / 255;
    buf[idx].g = (uint16_t)g_effectColorG * brt / 255;
    buf[idx].b = (uint16_t)g_effectColorB * brt / 255;
}

static void fx_pulse(uint32_t now) {
    if (now - g_lastFxRenderMs < 20) return;
    g_lastFxRenderMs = now;
    uint32_t elapsed = now - g_effectStartMs;
    uint32_t phase = elapsed % PULSE_CYCLE_MS;
    uint32_t half = PULSE_CYCLE_MS / 2;
    uint8_t brt = (phase < half) ?
        (uint8_t)((phase * 255UL) / half) :
        (uint8_t)(((PULSE_CYCLE_MS - phase) * 255UL) / half);
    for (uint8_t i = 0; i < ledCount1; i++) fx_paintPixel(ledBuf1, i, brt);
    for (uint8_t i = 0; i < ledCount2; i++) fx_paintPixel(ledBuf2, i, brt);
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

static void fx_spin(uint32_t now) {
    if (now - g_lastFxRenderMs < SPIN_STEP_MS) return;
    g_lastFxRenderMs = now;
    uint8_t maxLen = (ledCount1 > ledCount2) ? ledCount1 : ledCount2;
    if (ledCount3 > maxLen) maxLen = ledCount3;
    if (maxLen == 0) maxLen = 1;
    uint32_t steps = (now - g_effectStartMs) / SPIN_STEP_MS;
    uint8_t spinPos = steps % maxLen;
    for (uint8_t i = 0; i < ledCount1; i++) {
        uint8_t pos1 = spinPos % ledCount1;
        uint8_t dist = (pos1 >= i) ? (pos1 - i) : (ledCount1 - i + pos1);
        uint8_t brt  = (dist == 0) ? 255 : (dist == 1) ? 140 : (dist == 2) ? 60 : (dist == 3) ? 20 : 0;
        fx_paintPixel(ledBuf1, i, brt);
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        uint8_t pos2 = spinPos % ledCount2;
        uint8_t dist = (pos2 >= i) ? (pos2 - i) : (ledCount2 - i + pos2);
        uint8_t brt  = (dist == 0) ? 255 : (dist == 1) ? 140 : (dist == 2) ? 60 : (dist == 3) ? 20 : 0;
        fx_paintPixel(ledBuf2, i, brt);
    }
    for (uint8_t i = 0; i < ledCount3; i++) {
        uint8_t pos3 = spinPos % ledCount3;
        uint8_t dist = (pos3 >= i) ? (pos3 - i) : (ledCount3 - i + pos3);
        uint8_t brt  = (dist == 0) ? 255 : (dist == 1) ? 140 : (dist == 2) ? 60 : (dist == 3) ? 20 : 0;
        fx_paintPixel(ledBuf3, i, brt);
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void fx_rainbow(uint32_t now) {
    if (now - g_lastFxRenderMs < RAINBOW_STEP_MS) return;
    g_lastFxRenderMs = now;
    uint8_t phase = (uint8_t)(((now - g_effectStartMs) / RAINBOW_STEP_MS) * 4);
    for (uint8_t i = 0; i < ledCount1; i++) {
        uint8_t step = (ledCount1 > 0) ? (uint8_t)(256 / ledCount1) : 16;
        uint8_t pos = phase + (i * step);
        uint8_t r, g, b;
        if      (pos < 85)  { r = pos * 3;          g = 255 - pos * 3;    b = 0; }
        else if (pos < 170) { r = 255 - (pos-85)*3; g = 0;                b = (pos-85)*3; }
        else                { r = 0;                 g = (pos-170)*3;      b = 255 - (pos-170)*3; }
        led_setPixel(ledBuf1, i, ledCount1, r, g, b);
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        uint8_t step = (ledCount2 > 0) ? (uint8_t)(256 / ledCount2) : 16;
        uint8_t pos = phase + (i * step);
        uint8_t r, g, b;
        if      (pos < 85)  { r = pos * 3;          g = 255 - pos * 3;    b = 0; }
        else if (pos < 170) { r = 255 - (pos-85)*3; g = 0;                b = (pos-85)*3; }
        else                { r = 0;                 g = (pos-170)*3;      b = 255 - (pos-170)*3; }
        led_setPixel(ledBuf2, i, ledCount2, r, g, b);
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

static void fx_error(uint32_t now) {
    if (!g_errorBackedUp) {
        memcpy(ledBuf1Backup, ledBuf1, sizeof(ledBuf1));
        memcpy(ledBuf2Backup, ledBuf2, sizeof(ledBuf2));
        memcpy(ledBuf3Backup, ledBuf3, sizeof(ledBuf3));
        g_errorBackedUp = true;
        led_setAll(ledBuf1, ledCount1, 255, 0, 0);
        led_setAll(ledBuf2, ledCount2, 255, 0, 0);
        led_setAll(ledBuf3, ledCount3, 255, 0, 0);
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    }
    if (now - g_effectStartMs >= ERROR_DURATION_MS) {
        memcpy(ledBuf1, ledBuf1Backup, sizeof(ledBuf1));
        memcpy(ledBuf2, ledBuf2Backup, sizeof(ledBuf2));
        memcpy(ledBuf3, ledBuf3Backup, sizeof(ledBuf3));
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
        g_errorBackedUp = false;
        effect_stop();
    }
}

static void fx_matrix(uint32_t now) {
    if (now - g_lastFxRenderMs < MATRIX_STEP_MS) return;
    g_lastFxRenderMs = now;
    for (uint8_t i = 0; i < ledCount1; i++) {
        if (g_matrixBrt1[i] > MATRIX_TAIL_FADE) g_matrixBrt1[i] -= MATRIX_TAIL_FADE;
        else g_matrixBrt1[i] = 0;
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        if (g_matrixBrt2[i] > MATRIX_TAIL_FADE) g_matrixBrt2[i] -= MATRIX_TAIL_FADE;
        else g_matrixBrt2[i] = 0;
    }
    if (g_matrixHead1 >= 0) {
        g_matrixBrt1[g_matrixHead1] = 255;
        g_matrixHead1++;
        if (g_matrixHead1 >= (int8_t)ledCount1) {
            g_matrixHead1 = -1;
            g_matrixNextDrop = now + 80 + (ESP.getChipId() & 0x3F);
        }
    } else if (now >= g_matrixNextDrop) { g_matrixHead1 = 0; }
    if (g_matrixHead2 >= 0) {
        g_matrixBrt2[g_matrixHead2] = 255;
        g_matrixHead2++;
        if (g_matrixHead2 >= (int8_t)ledCount2) {
            g_matrixHead2 = -1;
            g_matrixNextDrop = now + 60 + (ESP.getChipId() & 0x1F);
        }
    } else if (now >= g_matrixNextDrop + 30) { g_matrixHead2 = 0; }
    for (uint8_t i = 0; i < ledCount1; i++) fx_paintPixel(ledBuf1, i, g_matrixBrt1[i]);
    for (uint8_t i = 0; i < ledCount2; i++) fx_paintPixel(ledBuf2, i, g_matrixBrt2[i]);
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

static void fx_lightning(uint32_t now) {
    if (now < g_lightNextEvent) return;
    if (g_lightFlashDone >= g_lightFlashTotal) {
        memset(ledBuf1, 0, sizeof(ledBuf1));
        memset(ledBuf2, 0, sizeof(ledBuf2));
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        g_lightOn = false;
        uint32_t rnd = ESP.getChipId() ^ now;
        g_lightFlashTotal = LIGHTNING_MIN_FLASH +
                            (uint8_t)(rnd % (LIGHTNING_MAX_FLASH - LIGHTNING_MIN_FLASH + 1));
        g_lightFlashDone = 0;
        g_lightNextEvent = now + LIGHTNING_PAUSE_MS;
        return;
    }
    if (!g_lightOn) {
        led_setAll(ledBuf1, ledCount1, 255, 255, 255);
        led_setAll(ledBuf2, ledCount2, 255, 255, 255);
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        g_lightOn = true; g_lightNextEvent = now + LIGHTNING_FLASH_MS;
    } else {
        memset(ledBuf1, 0, sizeof(ledBuf1));
        memset(ledBuf2, 0, sizeof(ledBuf2));
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
        g_lightOn = false; g_lightFlashDone++;
        g_lightNextEvent = now + LIGHTNING_GAP_MS + (uint32_t)(now & 0x1F);
    }
}

static void fx_rain(uint32_t now) {
    bool updated = false;
    if (now >= g_rainNextSpawn) {
        for (uint8_t i = 0; i < ledCount1; i++) {
            if (g_rain1[i].pos < 0) {
                g_rain1[i].pos = 0; g_rain1[i].brt = 255;
                uint32_t rnd = ESP.getChipId() ^ now ^ i;
                g_rain1[i].speed = RAIN_SPEED_MIN + (uint16_t)(rnd % (RAIN_SPEED_MAX - RAIN_SPEED_MIN));
                g_rain1[i].nextMs = now + g_rain1[i].speed;
                break;
            }
        }
        for (uint8_t i = 0; i < ledCount2; i++) {
            if (g_rain2[i].pos < 0) {
                g_rain2[i].pos = 0; g_rain2[i].brt = 255;
                uint32_t rnd = (ESP.getChipId() >> 4) ^ now ^ (i + 7);
                g_rain2[i].speed = RAIN_SPEED_MIN + (uint16_t)(rnd % (RAIN_SPEED_MAX - RAIN_SPEED_MIN));
                g_rain2[i].nextMs = now + g_rain2[i].speed;
                break;
            }
        }
        g_rainNextSpawn = now + 60 + ((now ^ ESP.getChipId()) & 0x3F);
    }
    for (uint8_t i = 0; i < ledCount1; i++) {
        if (g_rain1[i].pos < 0 || now < g_rain1[i].nextMs) continue;
        g_rain1[i].nextMs = now + g_rain1[i].speed;
        g_rain1[i].pos++;
        if (g_rain1[i].pos >= (int8_t)ledCount1) g_rain1[i].pos = -1;
        updated = true;
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        if (g_rain2[i].pos < 0 || now < g_rain2[i].nextMs) continue;
        g_rain2[i].nextMs = now + g_rain2[i].speed;
        g_rain2[i].pos++;
        if (g_rain2[i].pos >= (int8_t)ledCount2) g_rain2[i].pos = -1;
        updated = true;
    }
    if (!updated) return;
    for (uint8_t i = 0; i < ledCount1; i++) {
        if (g_rainTail1[i] > RAIN_TAIL_FADE) g_rainTail1[i] -= RAIN_TAIL_FADE;
        else g_rainTail1[i] = 0;
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        if (g_rainTail2[i] > RAIN_TAIL_FADE) g_rainTail2[i] -= RAIN_TAIL_FADE;
        else g_rainTail2[i] = 0;
    }
    for (uint8_t i = 0; i < ledCount1; i++)
        if (g_rain1[i].pos >= 0 && g_rain1[i].pos < (int8_t)ledCount1)
            g_rainTail1[g_rain1[i].pos] = 255;
    for (uint8_t i = 0; i < ledCount2; i++)
        if (g_rain2[i].pos >= 0 && g_rain2[i].pos < (int8_t)ledCount2)
            g_rainTail2[g_rain2[i].pos] = 255;
    for (uint8_t i = 0; i < ledCount1; i++) {
        ledBuf1[i].r = 0;
        ledBuf1[i].g = (uint16_t)40 * g_rainTail1[i] / 255;
        ledBuf1[i].b = g_rainTail1[i];
    }
    for (uint8_t i = 0; i < ledCount2; i++) {
        ledBuf2[i].r = 0;
        ledBuf2[i].g = (uint16_t)40 * g_rainTail2[i] / 255;
        ledBuf2[i].b = g_rainTail2[i];
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

// ============================================================================
// IDLE EFFECTS
// ============================================================================
static void fx_idle_keyFade(uint32_t now) {
    if (g_commandProcessing) return;
    if (now - g_lastIdleKeyFadeMs < IDLE_KEY_FADE_RENDER_MS) return;
    g_lastIdleKeyFadeMs = now;
    uint32_t t    = now % IDLE_KEY_FADE_PERIOD;
    uint32_t half = IDLE_KEY_FADE_PERIOD / 2;
    uint16_t brt  = (t < half)
        ? (uint16_t)(t * 255UL / half)
        : (uint16_t)((IDLE_KEY_FADE_PERIOD - t) * 255UL / half);
    uint8_t r = 0;
    uint8_t g = (uint8_t)((0x50UL * brt) / 255);
    uint8_t b = (uint8_t)((0xC8UL * brt) / 255);
    for (uint16_t i = 0; i < ledCount3; i++)
        led_setPixel(ledBuf3, i, ledCount3, r, g, b);
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void fx_idle_displayDrop(uint32_t now) {
    if (g_commandProcessing) return;
    if (now - g_lastIdleDropMs < IDLE_DROP_INTERVAL) return;
    g_lastIdleDropMs = now;
    uint8_t activeSeg = g_idleDropFrame % SEGS_PER_DIGIT;
    uint8_t cr = 0x00, cg = 0x32, cb = 0x64;
    uint8_t nd = activeDisplays();
    for (uint8_t disp = 0; disp < nd; disp++) {
        for (uint8_t i = 0; i < SEGS_PER_DIGIT; i++) {
            uint16_t idx = disp * SEGS_PER_DIGIT + i;
            if (i == activeSeg) {
                led_setPixel(ledBuf1, idx, ledCount1, cr, cg, cb);
                led_setPixel(ledBuf2, idx, ledCount2, cr, cg, cb);
            } else {
                led_setPixel(ledBuf1, idx, ledCount1, 0, 0, 0);
                led_setPixel(ledBuf2, idx, ledCount2, 0, 0, 0);
            }
        }
    }
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    g_idleDropFrame++;
}

// ============================================================================
// TEST MODE
// ============================================================================
static void fx_test_sevenSegment(uint32_t now) {
    if (now - g_lastTestTime < TEST_INTERVAL_MS) return;
    g_lastTestTime = now;
    g_testNumber = (g_testNumber + 1) % 100;
    uint8_t nd = activeDisplays();
    for (uint8_t disp = 0; disp < nd; disp++)
        displayTwoDigitNumber(disp, g_testNumber, 0xFFFFFF);
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

// ============================================================================
// WAIT-MODE EFFECTS (Matrix Pro / Equalizer)
// ============================================================================
static inline uint16_t vstrip_len() {
    return (uint16_t)ledCount1 + (uint16_t)ledCount2;
}

static inline void vstrip_set(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
    if (idx < ledCount1) {
        ledBuf1[idx].r = r; ledBuf1[idx].g = g; ledBuf1[idx].b = b;
    } else {
        uint16_t j = idx - ledCount1;
        if (j < ledCount2) { ledBuf2[j].r = r; ledBuf2[j].g = g; ledBuf2[j].b = b; }
    }
}

static void vstrip_show() {
    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
}

const uint8_t matrixProColors[4][3] = {
    {0,   180, 0  },
    {0,   230, 0  },
    {50,  255, 50 },
    {200, 255, 200}
};

static void matrixPro_init() {
    uint16_t vlen = vstrip_len();
    if (vlen == 0) vlen = 1;
    for (uint8_t d = 0; d < MP_DROPS; d++) {
        g_mpDrop[d].pos_q8   = -((int32_t)rng8(5, 25) << 8);
        g_mpDrop[d].speed_q8 = ((int32_t)rng8(MP_MIN_SPEED, MP_MAX_SPEED) << 8) / 8;
        g_mpDrop[d].flicker  = 255;
        g_mpDrop[d].variant  = rng8(0, 2);
    }
    g_mpInit   = true;
    g_mpFrame  = 0;
    g_mpLastMs = millis();
}

static void fx_matrix_pro(uint32_t now) {
    if (!g_mpInit) matrixPro_init();
    if (now - g_mpLastMs < MP_STEP_MS) return;
    g_mpLastMs = now;
    g_mpFrame++;

    const uint16_t vlen = vstrip_len();
    if (vlen == 0) return;

    for (uint16_t i = 0; i < vlen; i++) vstrip_set(i, 0, 0, 0);

    for (uint8_t d = 0; d < MP_DROPS; d++) {
        MpDrop* drop = &g_mpDrop[d];
        drop->pos_q8 += drop->speed_q8;
        if (rng8(0, 99) < 2) drop->flicker = rng8(80, 255);
        else                 drop->flicker = 255;
        if ((drop->pos_q8 >> 8) > (int32_t)vlen + 10) {
            drop->pos_q8   = -((int32_t)rng8(3, 12) << 8);
            drop->speed_q8 = ((int32_t)rng8(MP_MIN_SPEED, MP_MAX_SPEED) << 8) / 8;
            drop->variant  = rng8(0, 2);
        }
        int32_t head = drop->pos_q8 >> 8;
        for (int8_t t = 0; t < MP_TRAIL; t++) {
            int32_t p = head - t;
            if (p < 0 || p >= (int32_t)vlen) continue;
            uint16_t intensity = (t * 32 >= 235) ? 20 : (uint16_t)(255 - t * 32);
            if (intensity < 20) intensity = 20;
            intensity = (uint16_t)((intensity * drop->flicker) / 255);
            uint8_t colIdx = (t == 0) ? 3 : (t < 3 ? 2 : drop->variant);
            const uint8_t* col = matrixProColors[colIdx];
            vstrip_set((uint16_t)p,
                       (uint8_t)((col[0] * intensity) / 255),
                       (uint8_t)((col[1] * intensity) / 255),
                       (uint8_t)((col[2] * intensity) / 255));
        }
    }
    vstrip_show();

    uint8_t mode = (uint8_t)(g_mpFrame % 30);
    if (mode < 20) {
        for (uint8_t i = 0; i < ledCount3; i++) {
            if (rng8(0, 99) < 15) led_setPixel(ledBuf3, i, ledCount3, 0, 200, 0);
            else                  led_setPixel(ledBuf3, i, ledCount3, 0, 20,  0);
        }
    } else if (mode < 25) {
        uint8_t on = (uint8_t)((g_mpFrame) % (ledCount3 ? ledCount3 : 1));
        for (uint8_t i = 0; i < ledCount3; i++) {
            if (i == on) led_setPixel(ledBuf3, i, ledCount3, 50, 255, 50);
            else         led_setPixel(ledBuf3, i, ledCount3, 0,  40,  0);
        }
    } else {
        for (uint8_t i = 0; i < ledCount3; i++)
            led_setPixel(ledBuf3, i, ledCount3, 0, 10, 0);
    }
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
}

static void equalizer_init() {
    g_eqHeight    = 0;
    g_eqDir       = 1;
    g_eqPhase     = 0;
    g_eqDripPos   = 0;
    g_eqDripColor = 1;
    g_eqLastMs    = millis();
}

static void fx_equalizer(uint32_t now) {
    if (now - g_eqLastMs < EQ_STEP_MS) return;
    g_eqLastMs = now;

    const uint16_t vlen = vstrip_len();
    if (vlen == 0) return;

    g_eqPhase++;
    uint8_t colorIndex = (uint8_t)(1 + ((g_eqPhase / 10) % 6));
    uint32_t col = colorPalette[colorIndex];
    uint8_t br = (col >> 16) & 0xFF;
    uint8_t bg = (col >> 8)  & 0xFF;
    uint8_t bb =  col        & 0xFF;

    if (g_eqDir) {
        if (g_eqHeight < vlen) g_eqHeight++;
        else { g_eqHeight = (uint8_t)vlen; g_eqDir = 0; }
    } else {
        if (g_eqHeight > 0) g_eqHeight--;
        else {
            g_eqDir = 1;
            if ((g_eqPhase % 7) == 0) g_eqHeight = rng8(0, (uint8_t)(vlen - 1));
        }
    }

    for (uint16_t i = 0; i < vlen; i++) {
        if (i < g_eqHeight) {
            uint16_t intensity = 128 + (uint16_t)((uint32_t)i * 127 / vlen);
            if (intensity > 255) intensity = 255;
            vstrip_set(i,
                       (uint8_t)((br * intensity) / 255),
                       (uint8_t)((bg * intensity) / 255),
                       (uint8_t)((bb * intensity) / 255));
        } else {
            vstrip_set(i, 0, 0, 0);
        }
    }
    vstrip_show();

    if ((g_eqPhase % 20) == 0) g_eqDripColor = (uint8_t)(1 + (g_eqDripColor % 6));
    uint32_t dcol = colorPalette[g_eqDripColor];
    uint8_t dr = (dcol >> 16) & 0xFF;
    uint8_t dg = (dcol >> 8)  & 0xFF;
    uint8_t db =  dcol        & 0xFF;

    for (uint8_t p = 0; p < ledCount3; p++) {
        int16_t dist = (int16_t)p - (int16_t)g_eqDripPos;
        if (dist < 0) dist = -dist;
        int16_t intensity = 255 - (dist * 85);
        if (intensity < 30) intensity = 30;
        led_setPixel(ledBuf3, p, ledCount3,
                     (uint8_t)((dr * intensity) / 255),
                     (uint8_t)((dg * intensity) / 255),
                     (uint8_t)((db * intensity) / 255));
    }
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    g_eqDripPos = (uint8_t)((g_eqDripPos + 1) % (ledCount3 ? ledCount3 : 1));
}

// ============================================================================
// SELF-TEST ENGINE
// ============================================================================
#define SELFTEST_STEP_MS  350UL

static uint8_t  g_selftestMode   = 0x07;
static uint8_t  g_selftestPhase  = 0;
static uint8_t  g_selftestStep   = 0;
static uint32_t g_selftestNextMs = 0;

static bool selftestPhaseEnabled(uint8_t phase) {
    return (g_selftestMode & (1 << phase)) != 0;
}

static void selftest_nextPhase() {
    for (uint8_t i = 0; i < 3; i++) {
        g_selftestPhase = (uint8_t)((g_selftestPhase + 1) % 4);   // ✅ v7.4: فاز 3 = اکوی تاچ
        if (selftestPhaseEnabled(g_selftestPhase)) return;
    }
}

static void selftest_fillChannel(uint8_t ch, uint8_t r, uint8_t g, uint8_t b) {
    if (ch == 1) {
        led_setAll(ledBuf1, ledCount1, r, g, b);
        ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
    } else if (ch == 2) {
        led_setAll(ledBuf2, ledCount2, r, g, b);
        ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
    } else if (ch == 3) {
        led_setAll(ledBuf3, ledCount3, r, g, b);
        ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
    }
}

static void startSelfTest(uint8_t mode) {
    g_selftestMode = mode & 0x0F;   // ✅ v7.4: بیت۳ = فاز تشخیصی اکوی تاچ
    if (g_selftestMode == 0) g_selftestMode = 0x07;
    g_selftestPhase  = 0;
    g_selftestStep   = 0;
    g_selftestNextMs = millis();
    if (!selftestPhaseEnabled(g_selftestPhase)) selftest_nextPhase();
    led_clear(0xFF);
    g_activeEffect = EffectType::SELF_TEST;
    g_userLedLock  = true;
}

static void fx_selftest(uint32_t now) {
    if (now < g_selftestNextMs) return;
    g_selftestNextMs = now + SELFTEST_STEP_MS;

    if (!selftestPhaseEnabled(g_selftestPhase)) {
        selftest_nextPhase();
        g_selftestStep = 0;
        led_clear(0xFF);
        return;
    }

    switch (g_selftestPhase) {
        case 0: {
            if (g_selftestStep == 0) {
                led_setAll(ledBuf1, ledCount1, 255, 255, 255);
                led_setAll(ledBuf2, ledCount2, 255, 255, 255);
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            } else if (g_selftestStep >= 1 && g_selftestStep <= 10) {
                uint8_t digit = (uint8_t)(g_selftestStep - 1);
                uint8_t nd = activeDisplays();
                for (uint8_t disp = 0; disp < nd; disp++)
                    displayTwoDigitNumber(disp, (uint8_t)(digit * 11), 0xFFFFFF);
                ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
                ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            }
            if (g_selftestStep >= 11) {
                selftest_nextPhase(); g_selftestStep = 0; led_clear(0xFF);
            } else { g_selftestStep++; }
            break;
        }
        case 1: {
            led_clear(3);
            switch (g_selftestStep) {
                case 0: setKeyPixels(0, 0xFFFFFF); break;          // 4 پیکسل سفید
                case 1: setKeyPixels(0, 0xFF0000); break;          // 4 پیکسل قرمز
                case 2: setKeyPixels(0, 0x00FF00); break;          // 4 پیکسل سبز
                case 3: setKeyPixels(0, 0x0000FF); break;          // 4 پیکسل آبی
                default: break;
            }
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            if (g_selftestStep >= 4) {
                selftest_nextPhase(); g_selftestStep = 0; led_clear(0xFF);
            } else { g_selftestStep++; }
            break;
        }
        // ✅ v7.4 فاز ۳: اکوی تاچ — تشخیص سخت‌افزار بدون سرویس
        // حلقه: سبز=لمس / آبی کم‌نور=رها | تا دستور جدید می‌ماند
        case 3: {
            bool pressed = (digitalRead(PIN_TOUCH_CH0) == LOW);
            for (uint8_t i = 0; i < ledCount3; i++)
                led_setPixel(ledBuf3, i, ledCount3,
                             0, pressed ? 255 : 8, pressed ? 0 : 60);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            for (uint8_t i = 0; i < ledCount1; i++)
                led_setPixel(ledBuf1, i, ledCount1, 0, 0, 0);
            for (uint8_t i = 0; i < ledCount2; i++)
                led_setPixel(ledBuf2, i, ledCount2, 0, 0, 0);
            ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
            ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            break;
        }
        case 2: {
            led_clear(0xFF);
            if (g_selftestStep <= 8) {
                uint8_t ch = (uint8_t)(g_selftestStep / 3 + 1);
                uint8_t c  = (uint8_t)(g_selftestStep % 3);
                uint8_t r = (c == 0) ? 255 : 0;
                uint8_t g = (c == 1) ? 255 : 0;
                uint8_t b = (c == 2) ? 255 : 0;
                selftest_fillChannel(ch, r, g, b);
            } else if (g_selftestStep == 9) {
                selftest_fillChannel(3, 255, 255, 255);
            }
            if (g_selftestStep >= 10) {
                selftest_nextPhase(); g_selftestStep = 0; led_clear(0xFF);
            } else { g_selftestStep++; }
            break;
        }
        default: break;
    }
}

// ============================================================================
// MOTION SENSOR
// ============================================================================
static void checkMotionSensor() {
    if (g_activeMotionSensor == 1)
        g_motionDetected = (digitalRead(PIN_MOTION_1) == HIGH);
    else if (g_activeMotionSensor == 2)
        g_motionDetected = (digitalRead(PIN_MOTION_2) == HIGH);
}

static void taskMotionMonitor() {
    if (g_activeMotionSensor == 0) return;
    uint32_t now = millis();
    if (now - g_motionMonitorStartMs >= MOTION_MONITOR_TIME) {
        // ✅ v7.17 POLL-PATCH: latch پایان مانیتور (پاک بیت سنسور)
        g_pMotionFlags &= (uint8_t)~(1u << ((g_activeMotionSensor - 1) & 7));
        g_pMotionSeq++;
        g_pDirty = true;
        if (!pollLinkUp()) {   // پوش یدکی فقط وقتی سرویس جاروب نمی‌کند
            uint8_t p[3] = {g_ee.nodeAddr, g_activeMotionSensor, 0x00};
            RS485::sendFrame(ADDR_MASTER, CMD::MOTION_EVENT, 3, p);
        }
        g_activeMotionSensor = 0;
        g_motionLastReported = false;
        return;
    }
    if (now - g_lastMotionCheckMs >= MOTION_CHECK_INTERVAL) {
        g_lastMotionCheckMs = now;
        checkMotionSensor();
        if (g_motionDetected && !g_motionLastReported) {
            // ✅ v7.17 POLL-PATCH: latch تریگر موشن (ست بیت سنسور)
            g_pMotionFlags |= (uint8_t)(1u << ((g_activeMotionSensor - 1) & 7));
            g_pMotionSeq++;
            g_pDirty = true;
            if (!pollLinkUp()) {
                uint8_t p[3] = {g_ee.nodeAddr, g_activeMotionSensor, 0x01};
                RS485::sendFrame(ADDR_MASTER, CMD::MOTION_EVENT, 3, p);
            }
        }
        g_motionLastReported = g_motionDetected;
    }
}

// ============================================================================
// EFFECTS ENGINE DISPATCHER
// ============================================================================
#define RENDER_MIN_INTERVAL_MS  15UL
static uint32_t g_lastRenderMs = 0;

static void taskEffectsEngine() {
    if (g_otaActive) return;
    uint32_t now = millis();
    updateChannelFades(now);
    if (now - g_lastRenderMs < RENDER_MIN_INTERVAL_MS) return;
    g_lastRenderMs = now;
    if (g_activeEffect == EffectType::NONE) return;
    switch (g_activeEffect) {
        case EffectType::RAINBOW:   fx_rainbow(now);   break;
        case EffectType::SPIN:      fx_spin(now);      break;
        case EffectType::PULSE:     fx_pulse(now);     break;
        case EffectType::ERROR:     fx_error(now);     break;
        case EffectType::MATRIX:    fx_matrix(now);    break;
        case EffectType::LIGHTNING: fx_lightning(now); break;
        case EffectType::RAIN:      fx_rain(now);      break;
        case EffectType::IDLE:
            fx_idle_keyFade(now);
            fx_idle_displayDrop(now);
            break;
        case EffectType::TEST:
            fx_test_sevenSegment(now);
            break;
        case EffectType::MATRIX_PRO:
            fx_matrix_pro(now);
            break;
        case EffectType::EQUALIZER:
            fx_equalizer(now);
            break;
        case EffectType::SELF_TEST:
            fx_selftest(now);
            break;
        case EffectType::GAME:
            break;
        // ✅ v6.5: رندر حالت‌های گرید در taskGrid انجام می‌شود
        case EffectType::GRID_SELECT:
        case EffectType::GRID_LIGHTNING:
        case EffectType::GRID_RAIN:
        case EffectType::GRID_ERROR:
            break;
        default: break;
    }
}

// ============================================================================
// FRAME RX DISPATCHER
// ============================================================================
class FrameRx {
public:
    void tick() {
        uint32_t now = millis();
        if (len > 0 && (now - lastMs) > PKT_TIMEOUT_MS) { len = 0; need = 0; }
        while (Serial.available()) {
            uint8_t c = (uint8_t)Serial.read();
            lastMs = millis();
            if (len == 0) {
                if (c != FRAME_START) continue;
                buf[0] = c; len = 1; need = 0; continue;
            }
            if (len < RX_BUF_SIZE) buf[len++] = c;
            else { len = 0; need = 0; continue; }
            if (len == 4) {
                need = (uint16_t)4 + buf[3] + 2;
                if (need > RX_BUF_SIZE) { len = 0; need = 0; continue; }
            }
            if (need && len >= need) { dispatch(need); len = 0; need = 0; }
        }
    }
private:
    uint8_t  buf[RX_BUF_SIZE];
    uint16_t len = 0, need = 0;
    uint32_t lastMs = 0;
    void dispatch(uint16_t flen);
    void dispatchBody(uint16_t flen);
};
static FrameRx g_rx;

// ============================================================================
// comm_pump()
// ============================================================================
static volatile bool g_inPump = false;

static void comm_pump() {
    if (g_inDispatch) return;
    if (g_otaActive)  return;
    if (g_inPump) return;
    g_inPump = true;
    g_rx.tick();
    g_inPump = false;
}

void FrameRx::dispatch(uint16_t flen) {
    if (g_inDispatch) return;
    g_inDispatch = true;
    dispatchBody(flen);
    g_inDispatch = false;
}

// ══════════════════════════════════════════════════════════════════════════
// ✅ v7.17 POLL-PATCH: ساخت snapshot پاسخ POLL_STATE (0x08) — 18 بایت
// ──────────────────────────────────────────────────────────────────────────
// [0]=nodeAddr · [1]=flags(bit0:داده جدید · bit1:OTA فعال) · [2]=touchMask
// [3]=touchSeq · [4]=motionFlags · [5]=motionSeq · [6..7]=adc(BE)
// [8]=gameSeq · [9]=gameKey · [10]=gameEvt · [11]=gameVal · [12]=gameEvtSeq
// [13..17]=رزرو (صفر) — seq ها monotonic با wrap مجاز (سرویس تفاضل پیمانه‌ای می‌گیرد)
// ══════════════════════════════════════════════════════════════════════════
static void buildPollSnapshot(uint8_t s[18]) {
    uint16_t adc = g_adc.read();
    s[0]  = g_ee.nodeAddr;
    s[1]  = (uint8_t)((g_pDirty ? 0x01 : 0x00) |
                      (g_ota.active() ? 0x02 : 0x00));
    s[2]  = g_pTouchMask;
    s[3]  = g_pTouchSeq;
    s[4]  = g_pMotionFlags;
    s[5]  = g_pMotionSeq;
    s[6]  = (uint8_t)(adc >> 8);
    s[7]  = (uint8_t)(adc & 0xFF);
    s[8]  = g_pGameSeq;
    s[9]  = g_pGameKey;
    s[10] = g_pGameEvt;
    s[11] = g_pGameVal;
    s[12] = g_pGameEvtSeq;
    s[13] = 0; s[14] = 0; s[15] = 0; s[16] = 0; s[17] = 0;   // رزرو
}

// ============================================================================
// DISPATCH BODY  ✅ v6.0 — با تمام اصلاحات
// ============================================================================
void FrameRx::dispatchBody(uint16_t flen) {
    if (flen < 6 || buf[0] != FRAME_START) return;
    uint8_t  fAddr = buf[1];
    uint8_t  cmd   = buf[2];
    uint8_t  dLen  = buf[3];
    uint8_t* data  = &buf[4];
    if ((uint16_t)(4 + dLen + 2) != flen) return;

    uint16_t rxCrc = (uint16_t)buf[4+dLen] | ((uint16_t)buf[5+dLen] << 8);
    if (rxCrc != crc16_frame(fAddr, cmd, dLen, data)) {
        bool forMe = (fAddr == ADDR_BROADCAST) ||
                     (g_ee.nodeAddr != 0 && fAddr == g_ee.nodeAddr);
        // ✅ v6.3 FIX (BUG-6): روی فریم broadcast خراب پاسخی فرستاده نمی‌شود —
        // قبلاً همه نودها همزمان NACK می‌فرستادند → تصادم روی باس RS485
        if (forMe && fAddr != ADDR_BROADCAST) RS485::sendNACK(cmd, ERR_INVALID_PARAM);
        return;
    }

    // ✅ v7.8: هر نودِ روی باس ترافیک OTA می‌بیند (رسانه مشترک) — تاچ‌های خود را
    // صف می‌کند تا با چانک/ACK تصادم نکنند (ریشه‌ی بخشی از تایم‌اوت‌های OTA)
    if (cmd == CMD::UPDATE_START || cmd == CMD::UPDATE_DATA || cmd == CMD::UPDATE_END)
        g_lastOtaBusMs = millis();
    g_lastMasterRxMs = millis();
    if (g_linkLost) {
        g_linkLost = false;
        g_statusLed.setState(g_ee.nodeAddr ? SysState::ONLINE : SysState::NO_ADDR);
    }

    bool forMe = (fAddr == ADDR_BROADCAST) ||
                 (g_ee.nodeAddr != 0 && fAddr == g_ee.nodeAddr);
    if (!forMe) {
        if (cmd == CMD::SET_ADDRESS || cmd == CMD::CLEAR_ADDRESS ||
            cmd == CMD::DISCOVERY_REQ) forMe = true;
        else return;
    }

    g_curFrameAddr = fAddr;

    // OTA mode
    if (g_ota.active()) {
        g_ota.kick();
        switch (cmd) {
            case CMD::UPDATE_START: g_ota.handleStart(data, dLen); break;
            case CMD::UPDATE_DATA:  g_ota.handleData(data, dLen);  break;
            case CMD::UPDATE_END:   g_ota.handleEnd();             break;
            case CMD::ABORT_BOOT:
                // ✅ v7.12 QC-PATCH (SW-005، ادغام مجدد v7.17): replyACK → در broadcast ساکت
                // (پیشگیری تصادم هنگام batch-OTA هم‌زمان چند نود) — قبلاً sendACK مستقیم بود
                g_ota.abortLocal(); replyACK(cmd);
                g_statusLed.setState(g_ee.nodeAddr ? SysState::ONLINE : SysState::NO_ADDR);
                break;
            case CMD::ENTER_BOOT: { uint8_t ok=0x01; replyFrame(CMD::BOOT_ACK, 1, &ok); break; }  // ✅ v7.12 QC-PATCH (SW-005)
            // ✅ v6.4 FIX (W-4): PING در حالت OTA پاسخ می‌گیرد (health-check در طول آپدیت)
            case CMD::PING: { uint8_t ok=0x01; replyFrame(CMD::PING, 1, &ok); break; }
            // ✅ v7.17 POLL-PATCH: جاروب حتی در حالت OTA جواب می‌گیرد (flags bit1=1)
            // → سرویس می‌تواند بدون قطع چرخه، وضعیت OTA نود را ببیند
            case CMD::POLL_STATE: {
                g_lastPollMs = millis();
                uint8_t s[18];
                buildPollSnapshot(s);
                g_pDirty = false;
                pollTouchQClear();
                replyFrame(CMD::POLL_STATE, 18, s);
                break;
            }
            case CMD::GET_STATUS: {
                uint16_t adc = g_adc.read();
                uint8_t s[16];
                s[0]=g_ee.nodeAddr; memcpy(&s[1],g_ee.mac,6);
                s[7]=FW_MAJOR; s[8]=FW_MINOR; s[9]=g_ee.hasData?1:0;
                s[10]=g_ota.active()?1:0; s[11]=g_touch.mask();
                s[12]=(uint8_t)(adc>>8); s[13]=(uint8_t)(adc&0xFF);
                s[14]=g_bootCount; s[15]=g_resetReason;
                replyFrame(CMD::GET_STATUS, 16, s);   // ✅ v7.12 QC-PATCH (SW-005): broadcast-safe
                break;
            }
            default: break;
        }
        return;
    }

    exitIdleMode();
    // ✅ v7.5 CRITICAL FIX: keyRingRgbCancel از اینجا حذف شد — poll دوره‌ای سرویس
    // (GET_STATUS) حالت اکشن را می‌کشت و فید لمس هرگز اجرا نمی‌شد. لغو اکشن
    // فقط با «دستورهای تغییردهنده نمایش» انجام می‌شود (خوشه‌های پایین).

    switch (cmd) {

        case CMD::PING: {
            uint8_t ok = 0x01;
            replyFrame(CMD::PING, 1, &ok);
            break;
        }

        case CMD::RESET: {
            replyACK(cmd);
            ws_boot_safe_before_restart();
            delay(10);
            ESP.restart();
            break;
        }

        case CMD::IDENTIFY: {
            g_statusLed.setState(SysState::IDENTIFY);
            replyACK(cmd);
            break;
        }

        case CMD::DISCOVERY_REQ: {
            uint32_t cid = ESP.getChipId();
            g_discReplyAtMs    = millis() + 5 + (cid % 250) * 2;
            g_discReplyPending = true;
            break;
        }

        case CMD::SET_ADDRESS: {
            // ✅ v6.3 FIX (BUG-7): NACK ها broadcast-safe شدند —
            // این دستور معمولاً با آدرس broadcast ارسال می‌شود و پاسخ همزمان
            // چند نود باعث تصادم باس می‌شد (کامند خراب → سکوت، نه NACK)
            if (dLen < 7) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            if (memcmp(data, g_ee.mac, 6) != 0) return;
            uint8_t na = data[6];
            if (na == 0x00 || na == ADDR_BROADCAST) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            g_ee.nodeAddr = na; g_ee.save();
            g_addrAckValue   = na;
            g_addrAckAtMs    = millis() + 1 + (ESP.getChipId() & 0x07);
            g_addrAckPending = true;
            g_statusLed.setState(SysState::ONLINE);
            g_identGreenUntilMs = millis() + 700;   // ✅ v7.0: ۲ فلاش سبز «شناسایی شد»
            break;
        }

        case CMD::CLEAR_ADDRESS: {
            if (dLen >= 6) {
                if (memcmp(data, g_ee.mac, 6) != 0) return;
            } else if (fAddr == ADDR_BROADCAST) {
                return;
            } else if (g_ee.nodeAddr == 0 || fAddr != g_ee.nodeAddr) {
                return;
            }
            g_ee.clearAddress();
            uint8_t ack[7]; memcpy(ack, g_ee.mac, 6); ack[6] = 0;
            RS485::sendFrame(ADDR_MASTER, CMD::ADDRESS_ACK, 7, ack);
            g_userLedLock = false;
            g_statusLed.setState(SysState::NO_ADDR);
            break;
        }

        case CMD::SET_PIXEL_COUNT: {
            if (dLen < 2) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t c1 = data[0], c2 = data[1];
            uint8_t c3 = (dLen >= 3) ? data[2] : PIXELS_CH3;
            if (c1 == 0 || c1 > MAX_BUFFER_CAP ||
                c2 == 0 || c2 > MAX_BUFFER_CAP ||
                c3 == 0 || c3 > MAX_BUFFER_CAP) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            ledCount1 = c1; ledCount2 = c2; ledCount3 = c3;
            g_ee.save();
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            effect_stop(); led_clear(0xFF);
            uint8_t ackPayload[3] = {ledCount1, ledCount2, ledCount3};
            replyFrame(CMD::ACK, 3, ackPayload);
            break;
        }

        case CMD::ENTER_BOOT: {
            if (dLen < 4) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            if (memcmp(data, OTA_PASSWORD, 4) != 0) {
                replyNACK(cmd, ERR_AUTH_FAILED); return;
            }
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            digitalWrite(PIN_LED_CH1, LOW);
            ws_boot_safe_before_restart();
            g_ota.enter();
            uint8_t ok = 0x01;
            replyFrame(CMD::BOOT_ACK, 1, &ok);
            break;
        }

        case CMD::POLL_STATE: {   // ✅ v7.17 POLL-PATCH — جاروب 50ms سرویس
            // توجه: هر فریم مستر از مسیر exitIdleMode() بالا رد شده — رفتار
            // polls قبلی GET_STATUS حفظ می‌شود (v7.5: بدون لغو افکت/اکشن)
            g_lastPollMs = millis();     // تازه‌سازی watchdog پوش یدکی
            uint8_t s[18];
            buildPollSnapshot(s);
            g_pDirty = false;
            pollTouchQClear();           // سرویس وضعیت را از diff خواند — صف تکراری است
            replyFrame(CMD::POLL_STATE, 18, s);
            break;
        }
        case CMD::GET_STATUS: {
            uint16_t adc = g_adc.read();
            uint8_t s[16];
            s[0]=g_ee.nodeAddr; memcpy(&s[1],g_ee.mac,6);
            s[7]=FW_MAJOR; s[8]=FW_MINOR; s[9]=g_ee.hasData?1:0;
            s[10]=g_ota.active()?1:0; s[11]=g_touch.mask();
            s[12]=(uint8_t)(adc>>8); s[13]=(uint8_t)(adc&0xFF);
            s[14]=g_bootCount;
            s[15]=g_resetReason;
            replyFrame(CMD::GET_STATUS, 16, s);
            break;
        }

        case CMD::SET_COLOR: {
            g_keyDisplayActive = false;   // ✅ v7.12: پاک‌سازی حالت هاید — رفع تداخل (لمس بعدی «00» ناخواسته)
            if (dLen < 5) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t ch=data[0], idx=data[1], r=data[2], g=data[3], b=data[4];
            if (ch != 1 && ch != 2 && ch != 3 && ch != 0xFF) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            if ((ch==1||ch==0xFF) && g_stageLocked && !r && !g && !b) {
                replyNACK(cmd, ERR_STAGE_LOCKED); return;
            }
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_fadeCh1.active = false; g_fadeCh2.active = false;
            if (ch==1||ch==0xFF) {
                if (idx==0xFF) led_setAll(ledBuf1,ledCount1,r,g,b);
                else           led_setPixel(ledBuf1,idx,ledCount1,r,g,b);
            }
            if (ch==2||ch==0xFF) {
                if (idx==0xFF) led_setAll(ledBuf2,ledCount2,r,g,b);
                else           led_setPixel(ledBuf2,idx,ledCount2,r,g,b);
            }
            if (ch==3||ch==0xFF) {
                if (idx==0xFF) led_setAll(ledBuf3,ledCount3,r,g,b);
                else           led_setPixel(ledBuf3,idx,ledCount3,r,g,b);
            }
            g_userLedLock=true; g_activeEffect=EffectType::NONE;
            led_show(ch); replyACK(cmd);
            break;
        }

        case CMD::SET_CHANNEL_COLOR: {
            g_keyDisplayActive = false;   // ✅ v7.12: پاک‌سازی حالت هاید — رفع تداخل (لمس بعدی «00» ناخواسته)
            if (dLen < 4) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t ch=data[0], r=data[1], g=data[2], b=data[3];
            // ✅ v6.3 FIX (BUG-1): بایپس STAGE_LOCK با ch=0xFF بسته شد
            // قبلاً فقط ch==1 چک می‌شد → ch=0xFF با RGB=0 می‌توانست CH1 قفل‌شده را پاک کند
            if ((ch==1 || ch==0xFF) && g_stageLocked && !r && !g && !b) {
                replyNACK(cmd, ERR_STAGE_LOCKED); return;
            }
            // ✅ v6.3 FIX (BUG-3): اعتبارسنجی شماره کانال (قبلاً ch نامعتبر ACK بی‌عمل می‌گرفت)
            if (ch!=1 && ch!=2 && ch!=3 && ch!=0xFF) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            // ✅ v6.3: فقط CH1/CH2/ALL بازی و تایمر را لغو می‌کنند
            // CH3 (دور کلید) مستقل است و تایمر/بازی را متوقف نمی‌کند
            if (ch != 3) {
                game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
                if (g_tmrActive) tmr_finish(true);
                g_fadeCh1.active = false; g_fadeCh2.active = false;
            } else if (g_activeEffect != EffectType::NONE &&
                       g_activeEffect != EffectType::GAME) {
                // ✅ v6.4 FIX (W-2): نوشتن CH3 هنگام افکت غیربازی → افکت متوقف
                // شود وگرنه فریم بعد افکت، رنگ جدید دور کلید را بازنویسی می‌کرد
                g_activeEffect = EffectType::NONE;
            }
            if (ch==1||ch==0xFF) led_setAll(ledBuf1,ledCount1,r,g,b);
            if (ch==2||ch==0xFF) led_setAll(ledBuf2,ledCount2,r,g,b);
            if (ch==3||ch==0xFF) led_setAll(ledBuf3,ledCount3,r,g,b);
            g_userLedLock=true;
            if (ch != 3) g_activeEffect=EffectType::NONE;
            led_show(ch); replyACK(cmd);
            break;
        }

        case CMD::SET_PIXEL_COLOR: {
            if (dLen < 5) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t ch=data[0], idx=data[1], r=data[2], g=data[3], b=data[4];
            // ✅ v6.3 FIX (BUG-4): اعتبارسنجی کانال — قبلاً ch نامعتبر (مثل 0x07)
            // ACK تاییدی می‌گرفت بدون اینکه هیچ پیکسلی تنظیم شود
            if (ch!=1 && ch!=2 && ch!=3) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            // ✅ v6.3: فقط CH1/CH2 بازی و تایمر را لغو می‌کنند
            if (ch != 3) {
                game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
                if (g_tmrActive) tmr_finish(true);
                // ✅ v6.3 FIX (BUG-5): افکت فعال برای CH1/CH2 متوقف شود
                // (ناسازگاری با SET_COLOR — در غیر این صورت افکت بلافاصله پیکسل را بازنویسی می‌کرد)
                g_activeEffect = EffectType::NONE;
            } else if (g_activeEffect != EffectType::NONE &&
                       g_activeEffect != EffectType::GAME) {
                // ✅ v6.4 FIX (W-2): مانند SET_CHANNEL_COLOR
                g_activeEffect = EffectType::NONE;
            }
            if (ch==1) {
                if (g_stageLocked && !r && !g && !b) {
                    replyNACK(cmd, ERR_STAGE_LOCKED); return;
                }
                g_fadeCh1.active = false;
                led_setPixel(ledBuf1,idx,ledCount1,r,g,b);
            } else if (ch==2) {
                g_fadeCh2.active = false;
                led_setPixel(ledBuf2,idx,ledCount2,r,g,b);
            } else if (ch==3) {
                led_setPixel(ledBuf3,idx,ledCount3,r,g,b);
            }
            g_userLedLock=true; led_show(ch); replyACK(cmd);
            break;
        }

        case CMD::CLEAR_ALL: {
            g_keyDisplayActive = false;   // ✅ v7.12: پاک‌سازی حالت هاید — رفع تداخل (لمس بعدی «00» ناخواسته)
            uint8_t ch = (dLen > 0) ? data[0] : 0xFF;
            if (ch==1 && g_stageLocked) {
                replyNACK(cmd, ERR_STAGE_LOCKED); return;
            }
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_userLedLock=true; g_activeEffect=EffectType::NONE;
            led_clear(ch); replyACK(cmd);
            break;
        }

        case CMD::SET_BRIGHTNESS: {
            if (dLen < 2) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t ch=data[0], brt=data[1];
            if (ch!=1 && ch!=2 && ch!=3 && ch!=0xFF) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            bool bChanged = false;   // ✅ v6.7.1: commit فقط در صورت تغییر واقعی (ضدفرسایش)
            if (ch==1||ch==0xFF) { bChanged |= (g_ee.bright1 != brt); ledBright1=brt; g_ee.bright1=brt; }
            if (ch==2||ch==0xFF) { bChanged |= (g_ee.bright2 != brt); ledBright2=brt; g_ee.bright2=brt; }
            if (ch==3||ch==0xFF) { bChanged |= (g_ee.bright3 != brt); ledBright3=brt; g_ee.bright3=brt; }
            if (bChanged) g_ee.save();
            g_userLedLock=true;
            led_show(ch); replyACK(cmd);
            break;
        }

        case CMD::PIXEL_EFFECT: {
            g_keyDisplayActive = false;   // ✅ v7.12: پاک‌سازی حالت هاید — رفع تداخل (لمس بعدی «00» ناخواسته)
            if (dLen < 1) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t effId = data[0];
            if (dLen >= 4) {
                g_effectColorR = data[1]; g_effectColorG = data[2]; g_effectColorB = data[3];
            } else {
                g_effectColorR = 255; g_effectColorG = 255; g_effectColorB = 255;
            }
            g_effectStartMs = millis(); g_lastFxRenderMs = 0;
            g_userLedLock = true;
            g_fadeCh1.active = false; g_fadeCh2.active = false;
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            switch (effId) {
                case 0x00: g_activeEffect = EffectType::NONE; g_userLedLock = false;
                           led_clear(0xFF); break;  // ✅ v6.3: خاموشی LEDها هنگام Stop
                case 0x01: g_activeEffect = EffectType::RAINBOW;   break;
                case 0x02: g_activeEffect = EffectType::SPIN;      break;
                case 0x03: g_activeEffect = EffectType::PULSE;     break;
                case 0x04: g_activeEffect = EffectType::ERROR;     break;
                case 0x05: startChannelFade(1); startChannelFade(2);
                           g_activeEffect = EffectType::NONE; break;
                case 0x06: matrix_init();    g_activeEffect = EffectType::MATRIX;    break;
                case 0x07: lightning_init(); g_activeEffect = EffectType::LIGHTNING; break;
                case 0x08: rain_init();      g_activeEffect = EffectType::RAIN;      break;
                case 0x09:
                    g_activeEffect = EffectType::IDLE;
                    g_idleDropFrame = 0; g_lastIdleDropMs = millis();
                    led_clear(0xFF);
                    break;
                case 0x0A:
                    g_activeEffect = EffectType::TEST;
                    g_testNumber = 0; g_lastTestTime = millis();
                    break;
                case 0x0B:
                    g_rngState ^= (ESP.getChipId() ^ millis());
                    g_mpInit = false;
                    matrixPro_init();
                    led_clear(0xFF);
                    g_activeEffect = EffectType::MATRIX_PRO;
                    break;
                case 0x0C:
                    g_rngState ^= (ESP.getChipId() ^ millis());
                    equalizer_init();
                    led_clear(0xFF);
                    g_activeEffect = EffectType::EQUALIZER;
                    break;
                default:
                    g_activeEffect = EffectType::NONE; g_userLedLock = false;
                    break;
            }
            replyACK(cmd);
            break;
        }

        case CMD::STAGE_LOCK:   { g_stageLocked=true;  replyACK(cmd); break; }
        case CMD::STAGE_UNLOCK: { g_stageLocked=false; replyACK(cmd); break; }

        // ====================================================================
        // ✅ v6.11 سناریو ۱: KEY_RING_RGB (0x5A) — حلقه کلید با RGB خام
        // payload: [key][r][g][b] — سرویس رنگ را از پالت/آدرس حساب می‌کند و
        // RGB می‌فرستد؛ میکرو عیناً اجرا می‌کند (یکسان‌سازی فرمت اینجاست)
        // فیدبک لمس: fade-out 250ms با همان رنگ → خاموش (قانون سناریو ۱)
        // گزارش درستی: ACK/NACK طبق قانون ۱
        // ====================================================================
        case CMD::ACTION_RING: {
            // ✅ v7.6 حالت پا: key=0 → پای چپ (CH1) | key=1 → پای راست (CH2)
            if (g_footMode) {
                if (dLen < 4) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
                // ✅ v7.8: key=0xFF → هر دو پا همزمان با همان RGB (درخواست کاربر)
                if (data[0] == 0xFF) {
                    for (uint8_t sd = 0; sd < 2; sd++) {
                        g_footActive[sd] = true;
                        g_footPhase[sd]  = 0;
                        g_footR[sd] = data[1]; g_footG[sd] = data[2]; g_footB[sd] = data[3];
                        // ✅ v7.11: چپ(sd=0)→CH2 · راست(sd=1)→CH1
                        Pixel* buf = sd ? ledBuf1 : ledBuf2;
                        for (uint8_t i = 0; i < FOOT_PIXELS; i++) {
                            buf[i].r = data[1]; buf[i].g = data[2]; buf[i].b = data[3];
                        }
                        ws_show_strip(sd ? PIN_LED_CH1 : PIN_LED_CH2, buf, FOOT_PIXELS,
                                      sd ? ledBright1 : ledBright2);
                    }
                    replyACK(cmd);
                    break;
                }
                // ✅ v7.11: چپ(0)→CH2 · راست(1)→CH1
                uint8_t side = data[0];
                if (side > 1) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
                g_footActive[side] = true;
                g_footPhase[side]  = 0;
                g_footR[side] = data[1]; g_footG[side] = data[2]; g_footB[side] = data[3];
                Pixel* buf = side ? ledBuf1 : ledBuf2;
                for (uint8_t i = 0; i < FOOT_PIXELS; i++) {
                    buf[i].r = data[1]; buf[i].g = data[2]; buf[i].b = data[3];
                }
                ws_show_strip(side ? PIN_LED_CH1 : PIN_LED_CH2, buf, FOOT_PIXELS,
                              side ? ledBright1 : ledBright2);
                replyACK(cmd);
                break;
            }
            if (dLen < 4) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum = data[0];
            uint8_t r = data[1], g = data[2], b = data[3];
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            exitIdleMode();
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_keyDisplayActive = false;
            g_keyRingRgbActive = true;
            g_keyRingRgbPhase  = 0;
            g_keyRingRgbKey    = keyNum;
            g_keyRingRgbR = r; g_keyRingRgbG = g; g_keyRingRgbB = b;
            setKeyPixelsRgb(keyNum, r, g, b);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            g_userLedLock   = true;
            g_activeEffect  = EffectType::NONE;
            replyACK(cmd);
            break;
        }

        case CMD::ACTION_RING_OFF: {
            // ✅ v7.11 (تایید کاربر): خاموشی «کامل» نود در زمینه اکشن —
            // پاها (16+16) + دور استیج (33) + حلقه کلید (4). payload بی‌اثر.
            // صراحت اپراتور > STAGE_LOCK (مستند در CHANGELOG).
            if (dLen < 1) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_keyDisplayActive = false;
            g_footActive[0] = g_footActive[1] = false;
            g_footPhase[0]  = g_footPhase[1]  = 0;
            memset(ledBuf1, 0, sizeof(ledBuf1));
            memset(ledBuf2, 0, sizeof(ledBuf2));
            memset(ledBuf3, 0, sizeof(ledBuf3));
            ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
            ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            replyACK(cmd);
            break;
        }

        // ====================================================================
        // ✅ v7.6 اکشن — حالت پا (نود ۱۱/۱۲): [enable] ماندگار در EEPROM
        // فعال‌سازی: نودِ پا تاچ GPIO5/GPIO4 را به I2C (BS814A-2) واگذار می‌کند
        // ====================================================================
        // ====================================================================
        // ✅ v7.10 اکشن — 0x45: رنگ ثابت دور استیج (نود ۱۱) [R G B]
        // رنگ اختصاصی بازیکن برای مشخص‌کردن جایگاه — ثابت می‌ماند تا دستور جدید؛
        // 00 00 00 = خاموشی. بدون رفتار لمسی (تأیید کاربر).
        // ====================================================================
        case CMD::ACTION_STAGE_RGB: {
            if (dLen < 3) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_keyDisplayActive = false;
            uint8_t n = ledCount1 < STAGE_PIXELS ? ledCount1 : (uint8_t)STAGE_PIXELS;
            for (uint8_t i = 0; i < n; i++) {
                ledBuf1[i].r = data[0]; ledBuf1[i].g = data[1]; ledBuf1[i].b = data[2];
            }
            ws_show_strip(PIN_LED_CH1, ledBuf1, n, ledBright1);
            g_userLedLock  = true;
            g_activeEffect = EffectType::NONE;
            replyACK(cmd);
            break;
        }

        case CMD::ACTION_FOOT_MODE: {
            game_cancel(true); drop_cancel(true); grid_cancel(true);   // ✅ v7.12: تغییر نقش نود = پاک‌سازی کامل
            if (g_tmrActive) tmr_finish(true);
            g_keyDisplayActive = false;
            if (dLen < 1) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t en = data[0] ? 1 : 0;
            g_footMode = (en == 1);
            EEPROM.write(EE_FOOTMODE, en);
            EEPROM.commit();
            // پاک‌سازی امن هنگام تغییر حالت
            g_footActive[0] = g_footActive[1] = false;
            g_footPhase[0]  = g_footPhase[1]  = 0;
            keyRingRgbCancel();
            if (g_footMode) bs814a_init();   // ✅ v7.7
            // ✅ v7.12 QC-PATCH (SW-001، ادغام مجدد v7.17): غیرفعال‌سازی حالت پا باید GPIO5 را از
            // OUTPUT (کلاک BS814A) به INPUT_PULLUP (تاچ عادی) برگرداند — قبلاً پین در OUTPUT
            // می‌ماند و تاچ نود تا ریست بعدی مرده بود
            else g_touch.begin();
            replyACK(cmd);
            break;
        }

        // ====================================================================
        // SET_KEY_DISPLAY (0x50)
        // ====================================================================
        case CMD::SET_KEY_DISPLAY: {
            // ✅ v7.0 هاید: [key][r][g][b][tens][ones] — RGB یکپارچه
            if (dLen < 6) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum = data[0];
            uint8_t tens   = data[4];
            uint8_t ones   = data[5];
            if (!keyIndexValid(keyNum) || tens > 9 || ones > 9) {
                replyNACK(cmd, ERR_INVALID_PARAM); return;
            }
            uint32_t selectedColor = ((uint32_t)data[1] << 16) |
                                     ((uint32_t)data[2] <<  8) |
                                      (uint32_t)data[3];
            exitIdleMode();
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            g_ch1Mode = Ch1Mode::SEVENSEG;
            setKeyPixels(keyNum, selectedColor);
            displayTwoDigitNumber(keyNum, tens * 10 + ones, selectedColor);
            ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, ledBright3);
            ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, ledBright1);
            ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, ledBright2);
            g_userLedLock = true; g_activeEffect = EffectType::NONE;
            g_keyDisplayActive = true;
            g_keyDisplayNum    = keyNum;
            g_keyDisplayColor  = selectedColor;
            replyACK(cmd);
            break;
        }

        case CMD::MOTION_MONITOR_1: {
            g_activeMotionSensor = 1;
            g_motionMonitorStartMs = millis();
            g_lastMotionCheckMs = millis();
            g_motionDetected = false;
            g_motionLastReported = false;
            pinMode(PIN_MOTION_1, INPUT);
            replyACK(cmd);
            break;
        }

        case CMD::MOTION_MONITOR_2: {
            g_activeMotionSensor = 2;
            g_motionMonitorStartMs = millis();
            g_lastMotionCheckMs = millis();
            g_motionDetected = false;
            g_motionLastReported = false;
            pinMode(PIN_MOTION_2, INPUT);
            replyACK(cmd);
            break;
        }

        // ====================================================================
        // ✅ v6.0 FIX: GAME_ARM (0x51) — exitIdleMode اضافه شد
        // ====================================================================
        case CMD::GAME_ARM: {
            grid_cancel(true); keyRingRgbCancel();   // ✅ v7.12: گرید/حلقه روی نمایش هاید می‌ریختند
            // ✅ v7.0 هاید: [key][r][g][b][sec]
            if (dLen < 5) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum  = data[0];
            uint8_t seconds = data[4];
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (seconds < 1 || seconds > GAME_MAX_SECONDS) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            exitIdleMode();
            if (!game_arm(keyNum, data[1], data[2], data[3], seconds)) {
                replyNACK(cmd, ERR_INVALID_PARAM); return;
            }
            replyACK(cmd);
            if (!frameIsBroadcast()) game_sendEvent(GEVT::ARMED, seconds);
            break;
        }

        // ====================================================================
        // GAME_START (0x52)
        // ====================================================================
        case CMD::GAME_START: {
            uint8_t keyNum = (dLen > 0) ? data[0] : 0;
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (!game_start(keyNum)) {
                replyNACK(cmd, ERR_NOT_ARMED); return;
            }
            replyACK(cmd);
            if (!frameIsBroadcast())
                game_sendEvent(GEVT::STARTED,
                               (uint8_t)((g_gameRemainMs + 999UL) / 1000UL));
            break;
        }

        // ====================================================================
        // ✅ v6.0 FIX: GAME_FAIL (0x53) — validation قبل از game_fail
        // ====================================================================
        case CMD::GAME_FAIL: {
            uint8_t keyNum = (dLen > 0) ? data[0] : 0;
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            game_fail(keyNum);
            replyACK(cmd);
            break;
        }

        case CMD::GAME_CANCEL: {
            game_cancel(frameIsBroadcast());
            drop_cancel(frameIsBroadcast());   // ✅ v6.4: لغو بازی قطره هم‌زمان
            grid_cancel(frameIsBroadcast());   // ✅ v6.5: لغو حالت گرید هم‌زمان
            g_gameEngaged = false;
            led_clear(0xFF);
            replyACK(cmd);
            break;
        }

        case CMD::GAME_WAIT: {
            if (g_tmrActive) tmr_finish(true);   // ✅ v7.12: تایمرِ در حال اجرا زیر ماتریکس می‌ماند
            grid_cancel(true);                    // ✅ v7.12
            g_keyDisplayActive = false;           // ✅ v7.12
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            g_rngState ^= (ESP.getChipId() ^ millis());
            g_mpInit = false;
            matrixPro_init();
            led_clear(0xFF);
            g_effectStartMs  = millis();
            g_lastFxRenderMs = 0;
            g_userLedLock    = true;
            g_activeEffect   = EffectType::MATRIX_PRO;
            replyACK(cmd);
            break;
        }

        case CMD::GAME_STATUS: {
            uint8_t s[7];
            s[0] = g_ee.nodeAddr;
            s[1] = (uint8_t)g_gameState;
            s[2] = g_gameKey;
            s[3] = (uint8_t)((g_gameColor >> 16) & 0xFF);   // ✅ v7.0: بایت R رنگ
            s[4] = (uint8_t)(g_gameTotalSec > 99 ? 99 : g_gameTotalSec);
            s[5] = (uint8_t)((g_gameRemainMs + 999UL) / 1000UL);
            s[6] = g_gameEngaged ? 1 : 0;
            replyFrame(CMD::GAME_STATUS, 7, s);
            break;
        }

        // ====================================================================
        // ✅ v6.0 FIX: TIMER_KEY_START (0x65) — exitIdleMode اضافه شد
        // ====================================================================
        case CMD::TIMER_KEY_START: {
            g_keyDisplayActive = false;   // ✅ v7.12: پاک‌سازی حالت هاید — رفع تداخل (لمس بعدی «00» ناخواسته)
            // ✅ v7.0 وایبرون مرحله ۱: [key][r][g][b][sec] — تایمر بدون حلقه
            if (dLen < 5) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum  = data[0];
            uint8_t r       = data[1], g = data[2], b = data[3];
            uint8_t seconds = data[4];
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (seconds < 1 || seconds > GAME_MAX_SECONDS) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            if (g_stageLocked) { replyNACK(cmd, ERR_STAGE_LOCKED); return; }
            exitIdleMode();
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            g_activeEffect = EffectType::NONE;
            if (!tmr_start(keyNum, r, g, b, seconds)) {
                replyNACK(cmd, ERR_INVALID_PARAM); return;
            }
            replyACK(cmd);
            if (!frameIsBroadcast()) tmr_sendEvent(GEVT::STARTED, seconds);
            break;
        }

        case CMD::VIBRON_RING: {
            // ✅ v7.0 وایبرون مرحله ۲: [key][r][g][b] — حلقه روشن تا لمس
            // (MISS-3: بدون تایمر فعال → NACK 0x04 NOT_ARMED)
            if (dLen < 4) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum = data[0];
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (!g_tmrActive || keyNum != g_tmrKey || g_tmrPhase != TmrPhase::RUNNING) {
                replyNACK(cmd, ERR_NOT_ARMED); return;
            }
            g_tmrRingColor = ((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | data[3];
            g_tmrKeyArmed  = true;
            replyACK(cmd);
            break;
        }

        case CMD::TIMER_STOP: {
            if (g_tmrActive) {
                tmr_finish(true);
                if (!frameIsBroadcast()) tmr_sendEvent(GEVT::CANCELED, 0);
            } else {
                g_ch1Mode = Ch1Mode::SEVENSEG;
            }
            replyACK(cmd);
            break;
        }

        // ====================================================================
        // ✅ v6.7: DROP_ARM (0x72 / legacy 0x5A) — بازی قطره (هم‌گام با ATmega64)
        // payload: [0]keyNum [1]colorCode(1..6) [2]seconds(1..99)
        //          [3]dropCount(1..8) [4]trailLen(1..16) [5]actPct(1..100)
        //          [6]flags: bit0=ringEnable  bit1=missFeedback
        //          [7]dropLen(1..32, اختیاری — پیش‌فرض 13 مثل ATmega64)
        // ====================================================================
        case CMD::DROP_ARM: {
            // ✅ v7.0 تسلا: [key][r][g][b][sec][drops][trail][act%][flags][len؟]
            if (dLen < 9) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t keyNum   = data[0];
            uint8_t seconds  = data[4];
            uint8_t dropCnt  = data[5];
            uint8_t trailLen = data[6];
            uint8_t actPct   = data[7];
            uint8_t flags    = data[8];
            uint8_t dropLen  = (dLen >= 10) ? data[9] : DROP_DEF_LEN;
            if (!keyIndexValid(keyNum)) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (seconds   < 1 || seconds   > GAME_MAX_SECONDS) { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (dropCnt   < 1 || dropCnt   > DROP_MAX_DROPS)   { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (trailLen  < 1 || trailLen  > DROP_MAX_TRAIL)   { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (actPct    < 1 || actPct    > 100)  { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (dropLen   < 1 || dropLen   > DROP_MAX_LEN)     { replyNACK(cmd, ERR_OUT_OF_BOUNDS); return; }
            if (flags > 3)                         { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            exitIdleMode();
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            if (!drop_start(keyNum, data[1], data[2], data[3], seconds, dropCnt,
                            trailLen, dropLen, actPct,
                            (flags & 0x01) != 0, (flags & 0x02) != 0)) {
                replyNACK(cmd, ERR_INVALID_PARAM); return;
            }
            replyACK(cmd);
            if (!frameIsBroadcast()) drop_sendEvent(GEVT::DROP_ARMED, seconds);
            break;
        }

        // ====================================================================
        // ✅ v6.7: DROP_STATUS (0x73 / legacy 0x5B) — وضعیت بازی قطره
        // reply: [0]addr [1]phase [2]key [3]colorIdx [4]drops [5]trail
        //        [6]actPct [7]progress% [8]dropLen [9]hit(0/1)
        // ====================================================================
        case CMD::DROP_STATUS: {
            uint8_t prog = 0;
            if (g_dropActive && g_dropPhase == DropPhase::FORWARD)
                prog = drop_progressPct(drop_fwdPos(millis() - g_dropStartMs));
            uint8_t s[10];
            s[0] = g_ee.nodeAddr;
            s[1] = (uint8_t)g_dropPhase;
            s[2] = g_dropKey;
            s[3] = (uint8_t)((g_dropColor >> 16) & 0xFF);   // ✅ v7.0: بایت R رنگ
            s[4] = g_dropDrops;
            s[5] = g_dropTrail;
            s[6] = g_dropActPct;
            s[7] = prog;
            s[8] = g_dropLen;
            s[9] = g_dropPressedInFwd ? 1 : 0;
            replyFrame(CMD::DROP_STATUS, 10, s);
            break;
        }

        // ====================================================================
        // ✅ v6.5 NEW: حالت‌های گرید — هم‌ارز ATmega64
        // ====================================================================
        case CMD::GRID_SELECT: {
            if (g_stageLocked) { replyNACK(cmd, ERR_STAGE_LOCKED); return; }
            exitIdleMode();
            grid_start(GridMode::SELECT, 0, 0);
            replyACK(cmd);
            break;
        }

        case CMD::TESLA_START: {
            if (g_stageLocked) { replyNACK(cmd, ERR_STAGE_LOCKED); return; }
            exitIdleMode();
            grid_start(GridMode::LIGHTNING, 0, 0);
            replyACK(cmd);
            break;
        }

        case CMD::GRID_RAIN: {
            if (g_stageLocked) { replyNACK(cmd, ERR_STAGE_LOCKED); return; }
            uint8_t drops = (dLen >= 1) ? data[0] : GRID_RAIN_DROPS_DEF;
            uint8_t tail  = (dLen >= 2) ? data[1] : GRID_RAIN_TAIL_DEF;
            if (drops < 1 || drops > GRID_RAIN_DROPS_MAX ||
                tail  < 1 || tail  > GRID_RAIN_TAIL_MAX) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            exitIdleMode();
            grid_start(GridMode::RAIN, drops, tail);
            replyACK(cmd);
            break;
        }

        case CMD::GRID_ERROR: {
            if (g_stageLocked) { replyNACK(cmd, ERR_STAGE_LOCKED); return; }
            exitIdleMode();
            grid_start(GridMode::ERROR, 0, 0);
            replyACK(cmd);
            break;
        }

        case CMD::GRID_STOP: {
            grid_cancel(frameIsBroadcast());
            replyACK(cmd);
            break;
        }

        case CMD::TESLA_STOP: {          // ✅ v6.7 — توقف تسلا (هم‌ارز GRID_STOP)
            grid_cancel(frameIsBroadcast());
            replyACK(cmd);
            break;
        }

        case CMD::DROP_CANCEL: {         // ✅ v6.7 — لغو صریح بازی قطره
            drop_cancel(frameIsBroadcast());
            replyACK(cmd);
            break;
        }

        case CMD::SET_TIMER_COUNT: {
            if (dLen < 1) { replyNACK(cmd, ERR_INVALID_PARAM); return; }
            uint8_t c = data[0];
            if (c == 0 || c > MAX_BUFFER_CAP) {
                replyNACK(cmd, ERR_OUT_OF_BOUNDS); return;
            }
            if (g_tmrActive) tmr_finish(true);
            drop_cancel(true); grid_cancel(true);   // ✅ v6.4: طول نوار تغییر می‌کند — بازی قطره لغو شود
            ledCountTimer = c;
            g_ee.save();
            uint8_t ackPayload[1] = { ledCountTimer };
            replyFrame(CMD::ACK, 1, ackPayload);
            break;
        }

        case CMD::SELF_TEST: {
            uint8_t mode = (dLen > 0) ? data[0] : 0x07;
            game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
            if (g_tmrActive) tmr_finish(true);
            if (mode == 0x00) {
                effect_stop();
                led_clear(0xFF);
            } else {
                startSelfTest(mode);
            }
            replyACK(cmd);
            break;
        }

        default:
            if (fAddr != ADDR_BROADCAST) RS485::sendNACK(cmd, 0xFF);
            break;
    }
}

// ============================================================================
// BACKGROUND TASKS
// ============================================================================
static void taskAutoDiscovery() {
    if (g_otaActive || g_ee.nodeAddr != 0) return;
    // ✅ v6.8 FIX (ریشه‌ی مکث‌های OTA): اگر باس اخیراً فعال بوده، پخش کشف را
    // به تعویق بینداز. هر فریم معتبر روی باس (حتی برای نود دیگر) مقدار
    // g_lastMasterRxMs را تازه می‌کند — پس وسط یک OTA، نودِ بی‌آدرس دیگر
    // DISCOVERY_RES پخش نمی‌کرد که با chunk/ACK تصادم کند → CRC fail →
    // مکث ۳ ثانیه‌ای سرویس. باس ساکت = کشف ادامه می‌یابد (بیشینه ۵s تأخیر).
    if (g_lastMasterRxMs != 0 && (millis() - g_lastMasterRxMs < 4000UL)) return;
    if (millis() - g_discMs < DISC_INTERVAL_MS) return;
    g_discMs = millis();
    uint8_t res[11]; memcpy(res, g_ee.mac, 6);
    res[6]=0; res[7]=g_ee.hasData?1:0;
    res[8]=0x01; res[9]=FW_MAJOR; res[10]=FW_MINOR;
    RS485::sendFrame(ADDR_BROADCAST, CMD::DISCOVERY_RES, 11, res);
}

static void taskDeferredReplies() {
    if (g_otaActive) return;
    uint32_t now = millis();

    if (g_discReplyPending && (int32_t)(now - g_discReplyAtMs) >= 0) {
        g_discReplyPending = false;
        uint8_t res[11];
        memcpy(res, g_ee.mac, 6);
        res[6]=g_ee.nodeAddr; res[7]=g_ee.hasData?1:0;
        res[8]=0x01; res[9]=FW_MAJOR; res[10]=FW_MINOR;
        RS485::sendFrame(ADDR_BROADCAST, CMD::DISCOVERY_RES, 11, res);
    }

    if (g_addrAckPending && (int32_t)(now - g_addrAckAtMs) >= 0) {
        g_addrAckPending = false;
        uint8_t ack[7];
        memcpy(ack, g_ee.mac, 6);
        ack[6] = g_addrAckValue;
        RS485::sendFrame(ADDR_MASTER, CMD::ADDRESS_ACK, 7, ack);
    }
}

static void taskHeartbeat() {
#if !HEARTBEAT_ENABLE
    return;
#else
    if (g_otaActive)        return;
    if (g_ee.nodeAddr == 0) return;
    // ✅ v7.10: نودهای خواهر روی باس مشترک هنگام OTAِ نود دیگر ساکت می‌شوند —
    // هارت‌بیتِ ۲ ثانیه‌ای وسط چانک می‌نشست → CRC fail → تایم‌اوت‌های ۵تایی سرویس
    if (touchBusBusy())     return;

    uint32_t now   = millis();
    uint32_t cycle = now / HEARTBEAT_PERIOD_MS;
    if (cycle == g_lastHbCycle) return;

    uint32_t slot     = (uint32_t)(g_ee.nodeAddr % HEARTBEAT_MAX_NODES);
    uint32_t offset   = slot * HEARTBEAT_SLOT_MS;
    uint32_t slotOpen = cycle * HEARTBEAT_PERIOD_MS + offset;

    if ((int32_t)(now - slotOpen) < 0) return;
    if (now - slotOpen > HEARTBEAT_SLOT_MS) { g_lastHbCycle = cycle; return; }

    g_lastHbCycle = cycle;

    uint8_t p[7];
    p[0] = g_ee.nodeAddr;
    p[1] = g_linkLost ? 0x00 : 0x01;
    p[2] = FW_MAJOR;
    p[3] = FW_MINOR;
    p[4] = g_touch.mask();
    p[5] = (uint8_t)g_gameState;
    p[6] = g_hbSeq++;
    RS485::sendFrame(ADDR_MASTER, CMD::HEARTBEAT, 7, p);
#endif
}

static void taskLinkSupervision() {
    if (g_otaActive) return;
    if (g_ee.nodeAddr == 0) return;
    if (g_lastMasterRxMs == 0) return;

    if (!g_linkLost && (millis() - g_lastMasterRxMs >= LINK_LOST_MS)) {
        g_linkLost = true;
        game_cancel(true); drop_cancel(true); grid_cancel(true); keyRingRgbCancel();
        if (g_tmrActive) tmr_finish(true);
        g_userLedLock = false;
        led_clear(0xFF);
        g_statusLed.setState(SysState::NO_ADDR);
    }
}

static void taskOtaTimeout() {
    if (!g_ota.timedOut()) return;
    g_ota.abortLocal();
    uint8_t err = 0xFE;
    RS485::sendFrame(ADDR_MASTER, CMD::BOOT_ERROR, 1, &err);
    g_statusLed.setState(g_ee.nodeAddr ? SysState::ONLINE : SysState::NO_ADDR);
}

// ============================================================================
// SETUP & LOOP
// ============================================================================
void setup() {
    pinMode(PIN_DE_RE,   OUTPUT); digitalWrite(PIN_DE_RE,   LOW);
    ws_pins_init();
    pinMode(PIN_MOTION_1, INPUT);
    pinMode(PIN_MOTION_2, INPUT);

    memset(ledBuf1, 0, sizeof(ledBuf1));
    memset(ledBuf2, 0, sizeof(ledBuf2));
    memset(ledBuf3, 0, sizeof(ledBuf3));
    memset(g_matrixBrt1, 0, sizeof(g_matrixBrt1));
    memset(g_matrixBrt2, 0, sizeof(g_matrixBrt2));
    memset(g_rainTail1, 0, sizeof(g_rainTail1));
    memset(g_rainTail2, 0, sizeof(g_rainTail2));
    for (uint8_t i = 0; i < MAX_BUFFER_CAP; i++) {
        g_rain1[i].pos = -1; g_rain2[i].pos = -1;
    }

    {
        uint32_t rtcMagic = 0, rtcCount = 0;
        ESP.rtcUserMemoryRead(0, &rtcMagic, sizeof(rtcMagic));
        ESP.rtcUserMemoryRead(1, &rtcCount, sizeof(rtcCount));
        if (rtcMagic != 0x52425553UL) { rtcMagic = 0x52425553UL; rtcCount = 0; }
        rtcCount++;
        ESP.rtcUserMemoryWrite(0, &rtcMagic, sizeof(rtcMagic));
        ESP.rtcUserMemoryWrite(1, &rtcCount, sizeof(rtcCount));
        g_bootCount = (uint8_t)(rtcCount & 0xFF);
        const rst_info* ri = system_get_rst_info();
        g_resetReason = ri ? (uint8_t)ri->reason : 0xFF;
    }

    RS485::begin();
    g_ee.load();
    ledBright1 = g_ee.bright1;
    ledBright2 = g_ee.bright2;
    ledBright3 = g_ee.bright3;
    g_touch.begin();
    if (g_footMode) bs814a_init();   // ✅ v7.7: از بوت

    g_rngState ^= ESP.getChipId() ^ micros() ^
                  ((uint32_t)g_ee.mac[4] << 8) ^ g_ee.mac[5];
    if (g_rngState == 0) g_rngState = 0xA5A5F00DUL;

    ws_show_strip(PIN_LED_CH1, ledBuf1, ledCount1, 255);
    g_rx.tick();
    ws_show_strip(PIN_LED_CH2, ledBuf2, ledCount2, 255);
    g_rx.tick();
    ws_show_strip(PIN_LED_CH3, ledBuf3, ledCount3, 255);
    g_rx.tick();

    if      (!g_ee.hasData)      g_statusLed.setState(SysState::RAW);
    else if (g_ee.nodeAddr == 0) g_statusLed.setState(SysState::NO_ADDR);
    else                         g_statusLed.setState(SysState::ONLINE);

    startBootSpin();

    g_adc.last = analogRead(PIN_ADC);
    ESP.wdtEnable(WDTO_8S);
    // ✅ v7.12 QC-PATCH (SW-006، ادغام مجدد v7.17): فاز تصادفی per-node (0..2.5s) —
    // قبلاً همه نودهای بی‌آدرس با تغذیه مشترک هم‌فاز می‌شدند و DISCOVERY_RES ها تصادم می‌کردند
    if (g_ee.nodeAddr == 0)
        g_discMs = millis() - DISC_INTERVAL_MS + (ESP.getChipId() % 2500);
    g_bootMs = millis();
}

static void taskAutoIdle() {
#if REQUIRE_SERVICE_COMMAND
    return;
#else
    if (g_otaActive) return;
    if (g_activeEffect != EffectType::NONE) return;
    if (g_gameState != GameState::IDLE) return;
    if (g_activeMotionSensor != 0) return;
    if (g_touchFeedbackActive) return;
    if (g_ee.nodeAddr == 0) return;

    if (g_lastCommandMs == 0) {
        g_lastCommandMs = millis();
        return;
    }

    if (millis() - g_lastCommandMs >= AUTO_IDLE_TIMEOUT_MS) {
        g_activeEffect = EffectType::IDLE;
        g_userLedLock = true;
        g_idleDropFrame = 0;
        g_lastIdleDropMs = millis();
        g_lastCommandMs = millis();
    }
#endif
}

void loop() {
    ESP.wdtFeed();
    g_rx.tick();
    taskDeferredReplies();
    taskOtaTimeout();
    if (!g_otaActive) {
        g_touch.tick(g_ee.nodeAddr);
        g_adc.tick(g_ee.nodeAddr);
        taskAutoDiscovery();
        taskBootSpin();
        g_statusLed.tick();
        taskEffectsEngine();
        taskGame();
        taskTimerBar();
        taskDrop();        // ✅ v6.4: بازی قطره
        taskGrid();        // ✅ v6.5: حالت‌های گرید
        taskKeyRingRgb();       // اکشن — فید 200ms بازگشتی
        taskFoot();             // نود پا — فید هر پای مستقل
        taskTouchFlush();       // تخلیه صف تاچ پس از سکوت OTA
        taskLedFlush();         // ✅ v7.14: پوش کانال‌های منحرف‌شده
        taskNodeIdent();        // ✅ v7.0: شناسایی نود (قرمز/سبز)
        taskMotionMonitor();
        taskTouchFeedback();
        taskAutoIdle();
        taskHeartbeat();
        taskLinkSupervision();
    }
    yield();
}
