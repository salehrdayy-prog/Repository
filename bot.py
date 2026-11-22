import os
import json
import logging
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================================================
# تنظیمات
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CITIES_FILE = os.path.join(BASE_DIR, "cities.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(__name__)

# ==================================================
# ۳۱ استان ایران
# ==================================================

ALL_PROVINCES = [
    "آذربایجان شرقی",
    "آذربایجان غربی",
    "اردبیل",
    "اصفهان",
    "البرز",
    "ایلام",
    "بوشهر",
    "تهران",
    "چهارمحال و بختیاری",
    "خراسان جنوبی",
    "خراسان رضوی",
    "خراسان شمالی",
    "خوزستان",
    "زنجان",
    "سمنان",
    "سیستان و بلوچستان",
    "فارس",
    "قزوین",
    "قم",
    "کردستان",
    "کرمان",
    "کرمانشاه",
    "کهگیلویه و بویراحمد",
    "گلستان",
    "گیلان",
    "لرستان",
    "مازندران",
    "مرکزی",
    "هرمزگان",
    "همدان",
    "یزد",
]

# ==================================================
# خواندن شهرهای cities.json
# ==================================================

try:
    with open(CITIES_FILE, "r", encoding="utf-8") as file:
        CITY_DATA = json.load(file)

except (FileNotFoundError, json.JSONDecodeError) as error:
    log.warning("cities.json loading error: %s", error)
    CITY_DATA = {}

# اگر استانی در فایل شهرها نبود، به لیست استان‌ها اضافه می‌شود.
PROVINCES = {
    province: CITY_DATA.get(province, [])
    for province in ALL_PROVINCES
}

# ==================================================
# مختصات جایگزین شهرها
# ==================================================

CITY_COORDINATES = {
    "کبودراهنگ": (35.20924, 48.72341),
    "کبود آهنگ": (35.20924, 48.72341),
    "کبود راهنگ": (35.20924, 48.72341),
    "kabudarahang": (35.20924, 48.72341),
    "kaboodrahang": (35.20924, 48.72341),
}

# ==================================================
# انتخاب زمان
# ==================================================

TIME_OPTIONS = {
    "🌙 امشب": 0,
    "🌆 فردا شب": 1,
    "🌌 ۲ شب بعد": 2,
    "🌠 ۳ شب بعد": 3,
}

# ==================================================
# وضعیت هوا
# ==================================================

WEATHER_CODES = {
    0: "آسمان صاف ☀️",
    1: "عمدتاً صاف 🌤",
    2: "نیمه‌ابری ⛅",
    3: "ابری ☁️",
    45: "مه 🌫",
    48: "مه یخ‌زده 🌫",
    51: "نم‌نم باران 🌦",
    53: "نم‌نم باران 🌦",
    55: "نم‌نم باران شدید 🌧",
    56: "باران یخ‌زده 🌧",
    57: "باران یخ‌زده شدید 🌧",
    61: "باران خفیف 🌧",
    63: "باران 🌧",
    65: "باران شدید 🌧",
    66: "باران یخ‌زده 🌧",
    67: "باران یخ‌زده شدید 🌧",
    71: "برف خفیف 🌨",
    73: "برف 🌨",
    75: "برف شدید ❄️",
    77: "دانه‌های برف ❄️",
    80: "رگبار خفیف 🌦",
    81: "رگبار 🌧",
    82: "رگبار شدید 🌧",
    85: "رگبار برف 🌨",
    86: "رگبار برف شدید 🌨",
    95: "رعدوبرق ⛈",
    96: "رعدوبرق و تگرگ ⛈",
    99: "رعدوبرق و تگرگ شدید ⛈",
}

# ==================================================
# کیبوردها
# ==================================================

def create_keyboard(rows, extra=None):
    keyboard = [
        [KeyboardButton(item) for item in row]
        for row in rows
    ]

    if extra:
        keyboard.append([KeyboardButton(extra)])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


def province_keyboard():
    rows = [
        ALL_PROVINCES[i:i + 3]
        for i in range(0, len(ALL_PROVINCES), 3)
    ]

    return create_keyboard(rows, "❌ لغو")


def city_keyboard(province):
    cities = PROVINCES.get(province, [])

    rows = [
        cities[i:i + 2]
        for i in range(0, len(cities), 2)
    ]

    rows.append(["🔎 شهرم در فهرست نیست"])

    rows.append(["🔙 بازگشت به استان‌ها"])

    return ReplyKeyboardMarkup(
        [[KeyboardButton(city) for city in row] for row in rows],
        resize_keyboard=True
    )


def time_keyboard():
    return create_keyboard([
        ["🌙 امشب", "🌆 فردا شب"],
        ["🌌 ۲ شب بعد", "🌠 ۳ شب بعد"],
        ["🔙 تغییر شهر"],
    ])


def result_keyboard():
    return create_keyboard([
        ["🔄 بررسی زمان دیگر"],
        ["🏙 تغییر شهر", "📍 تغییر استان"],
    ])

# ==================================================
# نرمال‌سازی متن فارسی
# ==================================================

def normalize_text(text):
    text = str(text).strip().lower()

    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("‌", " ")
    text = text.replace("  ", " ")

    return text.strip()

# ==================================================
# پیدا کردن مختصات شهر
# ==================================================

def get_coordinates(city, province):

    city_normalized = normalize_text(city)

    # ۱- مختصات ذخیره‌شده
    if city_normalized in CITY_COORDINATES:
        log.info("Using saved coordinates for %s", city)
        return CITY_COORDINATES[city_normalized]

    # ۲- جستجوی نام شهر همراه استان
    search_queries = [
        f"{city}, {province}",
        f"{city}, Iran",
        city,
    ]

    for query in search_queries:

        for language in ("fa", "en"):

            try:
                response = requests.get(
                    GEOCODING_URL,
                    params={
                        "name": query,
                        "count": 20,
                        "language": language,
                        "format": "json",
                        "countryCode": "IR",
                    },
                    timeout=20,
                )

                response.raise_for_status()

                data = response.json()
                results = data.get("results", [])

                iran_results = [
                    item for item in results
                    if item.get("country_code", "").upper() == "IR"
                ]

                if not iran_results:
                    continue

                normalized_province = normalize_text(province)

                province_matches = [
                    item for item in iran_results
                    if normalized_province in normalize_text(
                        item.get("admin1", "")
                    )
                    or normalize_text(
                        item.get("admin1", "")
                    ) in normalized_province
                ]

                # اگر استان مطابق بود، همان نتیجه انتخاب شود.
                if province_matches:
                    selected = province_matches[0]

                # اگر فقط یک نتیجه ایرانی وجود داشت،
                # می‌توان آن را به‌عنوان نتیجه احتمالی پذیرفت.
                elif len(iran_results) == 1:
                    selected = iran_results[0]

                # اگر چند شهر همنام در استان‌های مختلف باشند،
                # نتیجه اشتباه انتخاب نمی‌کنیم.
                else:
                    log.warning(
                        "Ambiguous geocoding results for %s, %s",
                        city,
                        province
                    )
                    continue

                log.info(
                    "Location found: city=%s province=%s lat=%s lon=%s",
                    selected.get("name"),
                    selected.get("admin1"),
                    selected.get("latitude"),
                    selected.get("longitude"),
                )

                return (
                    selected["latitude"],
                    selected["longitude"],
                )

            except requests.RequestException:
                log.exception("Geocoding request failed: %s", query)

            except (ValueError, KeyError, TypeError):
                log.exception("Geocoding data error: %s", query)

    log.error("Location not found: %s, %s", city, province)

    return None

# ==================================================
# دریافت اطلاعات هوا
# ==================================================

def fetch_weather(city, province, days_ahead):

    coordinates = get_coordinates(city, province)

    if coordinates is None:
        return None

    latitude, longitude = coordinates

    try:
        response = requests.get(
            WEATHER_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "timezone": "auto",
                "forecast_days": 7,
                "hourly": (
                    "temperature_2m,"
                    "relative_humidity_2m,"
                    "cloud_cover,"
                    "precipitation_probability,"
                    "wind_speed_10m,"
                    "weather_code"
                ),
            },
            timeout=30,
        )

        log.info("Weather API status: %s", response.status_code)

        response.raise_for_status()

        data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])

        if not times:
            log.error("Hourly forecast is empty")
            return None

        timezone_name = data.get("timezone", "UTC")
        local_now = datetime.now(ZoneInfo(timezone_name))

        target_date = (
            local_now.date() + timedelta(days=days_ahead)
        ).isoformat()

        target_hour = 21

        # اگر ساعت ۲۱ امشب گذشته باشد، امشب را برای شب بعد
        # در نظر می‌گیریم.
        if days_ahead == 0 and local_now.hour >= target_hour:
            target_date = (
                local_now.date() + timedelta(days=1)
            ).isoformat()

        target_time = f"{target_date}T{target_hour:02d}:00"

        index = next(
            (
                i for i, item in enumerate(times)
                if item == target_time
            ),
            None,
        )

        # اگر ساعت ۲۱ نبود، نزدیک‌ترین ساعت شب را پیدا می‌کند.
        if index is None:

            night_indexes = [
                i for i, item in enumerate(times)
                if item.startswith(target_date)
                and item[11:13] in ("20", "21", "22", "23")
            ]

            if not night_indexes:
                log.error("No night forecast found for %s", target_date)
                return None

            index = min(
                night_indexes,
                key=lambda i: abs(
                    int(times[i][11:13]) - target_hour
                ),
            )

        def value(key):
            values = hourly.get(key, [])
            return values[index] if index < len(values) else None

        return {
            "datetime": times[index],
            "timezone": timezone_name,
            "temperature": value("temperature_2m"),
            "humidity": value("relative_humidity_2m"),
            "clouds": value("cloud_cover"),
            "rain": value("precipitation_probability"),
            "wind": value("wind_speed_10m"),
            "code": value("weather_code"),
        }

    except requests.Timeout:
        log.error("Weather API timed out for %s", city)
        return None

    except requests.HTTPError:
        log.exception("Weather API HTTP error for %s", city)
        return None

    except requests.RequestException:
        log.exception("Weather API connection error for %s", city)
        return None

    except (ValueError, KeyError, TypeError):
        log.exception("Weather data parsing error for %s", city)
        return None

# ==================================================
# ارزیابی شرایط رصد
# ==================================================

def observation_rating(clouds, rain, wind):

    if clouds is None or rain is None or wind is None:
        return (
            "نامشخص ⚪",
            "اطلاعات کافی برای ارزیابی وجود ندارد."
        )

    if rain >= 50 or clouds >= 85 or wind >= 40:
        return (
            "نامناسب 🔴",
            "احتمالاً وضعیت هوا برای رصد مناسب نیست."
        )

    if clouds <= 15 and rain <= 10 and wind <= 20:
        return (
            "خیلی خوب 🌟",
            "آسمان نسبتاً صاف و شرایط جوی مساعد است."
        )

    if clouds <= 40 and rain <= 20 and wind <= 25:
        return (
            "مناسب 🟢",
            "شرایط می‌تواند برای رصد مناسب باشد."
        )

    if clouds <= 70 and rain <= 30:
        return (
            "متوسط 🟡",
            "احتمال وجود ابر در آسمان هست."
        )

    return (
        "ضعیف 🟠",
        "ابر یا شرایط جوی می‌تواند دید آسمان را محدود کند."
    )

# ==================================================
# شروع ربات
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    await update.message.reply_text(
        "🌌 به ربات پیش‌بینی وضعیت آسمان خوش آمدید!\n\n"
        "📍 ابتدا استان خود را انتخاب کنید:",
        reply_markup=province_keyboard(),
    )

# ==================================================
# مدیریت پیام‌ها
# ==================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message

    if not message or not message.text:
        return

    text = message.text.strip()

    province = context.user_data.get("province")
    city = context.user_data.get("city")

    # لغو
    if text == "❌ لغو":
        context.user_data.clear()

        await message.reply_text(
            "لغو شد. برای شروع مجدد /start را بزنید."
        )
        return

    # انتخاب استان
    if text in ALL_PROVINCES:

        context.user_data.clear()
        context.user_data["province"] = text

        await message.reply_text(
            f"📍 استان {text} انتخاب شد.\n\n"
            "🏙 شهر خود را انتخاب کنید.\n"
            "اگر شهر شما در فهرست نیست، دکمه «شهرم در فهرست نیست» را بزنید.",
            reply_markup=city_keyboard(text),
        )
        return

    # بازگشت به استان
    if text == "🔙 بازگشت به استان‌ها":

        context.user_data.clear()

        await message.reply_text(
            "استان خود را انتخاب کنید:",
            reply_markup=province_keyboard(),
        )
        return

    # ورود دستی شهر
    if text == "🔎 شهرم در فهرست نیست":

        if not province:
            await message.reply_text(
                "اول استان خود را انتخاب کنید:",
                reply_markup=province_keyboard(),
            )
            return

        context.user_data["waiting_custom_city"] = True

        await message.reply_text(
            "✍️ نام شهر خود را تایپ کنید.\n\n"
            "مثال: کبودرآهنگ، لواسان، ورامین، بانه و ...",
            reply_markup=create_keyboard([["🔙 بازگشت به شهرها"]]),
        )
        return

    # بازگشت از تایپ دستی
    if text == "🔙 بازگشت به شهرها":

        context.user_data["waiting_custom_city"] = False

        await message.reply_text(
            "شهر خود را انتخاب کنید:",
            reply_markup=city_keyboard(province),
        )
        return

    # دریافت شهر تایپ شده
    if context.user_data.get("waiting_custom_city"):

        if len(text) < 2 or len(text) > 60:
            await message.reply_text(
                "نام شهر معتبر نیست. دوباره نام شهر را وارد کنید."
            )
            return

        context.user_data["city"] = text
        context.user_data["waiting_custom_city"] = False

        await message.reply_text(
            f"🏙 شهر «{text}» ثبت شد.\n\n"
            "برای چه زمانی وضعیت هوا را می‌خواهید؟",
            reply_markup=time_keyboard(),
        )
        return

    # انتخاب شهر از کیبورد
    if province and text in PROVINCES.get(province, []):

        context.user_data["city"] = text

        await message.reply_text(
            f"🏙 شهر {text} انتخاب شد.\n\n"
            "برای چه زمانی گزارش را می‌خواهید؟",
            reply_markup=time_keyboard(),
        )
        return

    # تغییر شهر
    if text in ("🔙 تغییر شهر", "🏙 تغییر شهر"):

        context.user_data.pop("city", None)
        context.user_data["waiting_custom_city"] = False

        if province:
            await message.reply_text(
                "شهر خود را انتخاب کنید:",
                reply_markup=city_keyboard(province),
            )
        else:
            await message.reply_text(
                "استان خود را انتخاب کنید:",
                reply_markup=province_keyboard(),
            )
        return

    # تغییر استان
    if text == "📍 تغییر استان":

        context.user_data.clear()

        await message.reply_text(
            "استان خود را انتخاب کنید:",
            reply_markup=province_keyboard(),
        )
        return

    # انتخاب زمان دیگر
    if text == "🔄 بررسی زمان دیگر":

        if city and province:
            await message.reply_text(
                "زمان موردنظر را انتخاب کنید:",
                reply_markup=time_keyboard(),
            )
        else:
            await message.reply_text(
                "ابتدا استان و شهر را انتخاب کنید:",
                reply_markup=province_keyboard(),
            )
        return

    # درخواست پیش‌بینی
    if text in TIME_OPTIONS:

        province = context.user_data.get("province")
        city = context.user_data.get("city")

        if not province or not city:
            await message.reply_text(
                "ابتدا استان و شهر را انتخاب کنید.",
                reply_markup=province_keyboard(),
            )
            return

        await message.reply_text(
            "⏳ در حال دریافت اطلاعات هواشناسی...\n"
            "لطفاً چند ثانیه صبر کنید.",
            reply_markup=time_keyboard(),
        )

        weather = fetch_weather(
            city,
            province,
            TIME_OPTIONS[text],
        )

        if weather is None:

            await message.reply_text(
                "❌ دریافت اطلاعات هوا ناموفق بود.\n\n"
                "ممکن است سرویس هواشناسی در دسترس نباشد "
                "یا نام شهر پیدا نشده باشد.\n\n"
                "🔄 دوباره تلاش کنید یا نام شهر را به انگلیسی وارد کنید.",
                reply_markup=time_keyboard(),
            )
            return

        rating, rating_text = observation_rating(
            weather["clouds"],
            weather["rain"],
            weather["wind"],
        )

        dt = datetime.fromisoformat(weather["datetime"])

        report = (
            "━━━━━━━━━━━━━━━━━━\n"
            "🌌 گزارش وضعیت آسمان\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 استان: {province}\n"
            f"🏙 شهر: {city}\n"
            f"📅 تاریخ: {dt.strftime('%Y-%m-%d')}\n"
            f"🕘 ساعت پیش‌بینی: {dt.strftime('%H:%M')}\n\n"
            f"🌤 وضعیت: {WEATHER_CODES.get(weather['code'], 'نامشخص')}\n"
            f"🌡 دما: {weather['temperature'] if weather['temperature'] is not None else '—'}°C\n"
            f"💧 رطوبت: {weather['humidity'] if weather['humidity'] is not None else '—'}٪\n"
            f"☁️ پوشش ابر: {weather['clouds'] if weather['clouds'] is not None else '—'}٪\n"
            f"🌧 احتمال بارندگی: {weather['rain'] if weather['rain'] is not None else '—'}٪\n"
            f"💨 سرعت باد: {weather['wind'] if weather['wind'] is not None else '—'} km/h\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔭 ارزیابی شرایط رصد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"⭐ نتیجه: {rating}\n"
            f"{rating_text}\n\n"
            "ℹ️ این گزارش بر اساس پیش‌بینی هواست "
            "و تضمین‌کننده صاف بودن واقعی آسمان نیست.\n"
            "━━━━━━━━━━━━━━━━━━"
        )

        await message.reply_text(
            report,
            reply_markup=result_keyboard(),
        )
        return

    # پیام ناشناخته
    await message.reply_text(
        "از دکمه‌های ربات استفاده کنید، "
        "یا برای شروع /start را بزنید."
    )

# ==================================================
# اجرای برنامه
# ==================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing! "
            "Set BOT_TOKEN in GitHub Secrets."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    log.info("SkyForecastBot is starting...")

    app.run_polling()


if __name__ == "__main__":
    main()
