import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =========================================
# CONFIG
# =========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

CITIES_FILE = os.path.join(os.path.dirname(__file__), "cities.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)

# =========================================
# LOAD PROVINCES & CITIES
# =========================================

try:
    with open(CITIES_FILE, "r", encoding="utf-8") as f:
        PROVINCES = json.load(f)

except (FileNotFoundError, json.JSONDecodeError) as exc:
    raise RuntimeError(f"Cannot load cities.json: {exc}")


# =========================================
# TIME OPTIONS
# =========================================

TIME_OPTIONS = {
    "🌙 امشب": 0,
    "🌆 فردا شب": 1,
    "🌌 ۲ شب بعد": 2,
    "🌠 ۳ شب بعد": 3,
}


# =========================================
# WEATHER CODES
# =========================================

WEATHER_CODES = {
    0: "صاف ☀️",
    1: "عمدتاً صاف 🌤",
    2: "نیمه‌ابری ⛅",
    3: "ابری ☁️",
    45: "مه 🌫",
    48: "مه یخ‌زده 🌫",
    51: "نم‌نم باران 🌦",
    53: "نم‌نم باران 🌦",
    55: "نم‌نم باران شدید 🌧",
    56: "نم‌نم باران یخ‌زده 🌧",
    57: "نم‌نم باران یخ‌زده شدید 🌧",
    61: "باران خفیف 🌧",
    63: "باران 🌧",
    65: "باران شدید 🌧",
    66: "باران یخ‌زده 🌧",
    67: "باران یخ‌زده شدید 🌧",
    71: "برف خفیف 🌨",
    73: "برف 🌨",
    75: "برف شدید ❄️",
    77: "دانه‌های برف ❄️",
    80: "رگبار باران 🌦",
    81: "رگبار 🌧",
    82: "رگبار شدید 🌧",
    85: "رگبار برف 🌨",
    86: "رگبار برف شدید 🌨",
    95: "رعدوبرق ⛈",
    96: "رعدوبرق همراه تگرگ ⛈",
    99: "رعدوبرق و تگرگ شدید ⛈",
}


# =========================================
# KEYBOARDS
# =========================================

def make_keyboard(rows, extra=None):
    buttons = [
        [KeyboardButton(x) for x in row]
        for row in rows
    ]

    if extra:
        buttons.append([KeyboardButton(extra)])

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True
    )


def provinces_keyboard():
    names = list(PROVINCES.keys())

    rows = [
        names[i:i + 3]
        for i in range(0, len(names), 3)
    ]

    return make_keyboard(rows, "❌ لغو")


def cities_keyboard(province):
    cities = PROVINCES.get(province, [])

    rows = [
        cities[i:i + 2]
        for i in range(0, len(cities), 2)
    ]

    return make_keyboard(
        rows,
        "🔙 بازگشت به استان‌ها"
    )


def time_keyboard():
    return make_keyboard([
        ["🌙 امشب", "🌆 فردا شب"],
        ["🌌 ۲ شب بعد", "🌠 ۳ شب بعد"],
        ["🔙 تغییر شهر"],
    ])


def result_keyboard():
    return make_keyboard([
        ["🔄 بررسی زمان دیگر"],
        ["🏙 تغییر شهر", "📍 تغییر استان"],
    ])


# =========================================
# GEOCODING
# =========================================

def get_coordinates(city, province):

    # جستجو فقط با نام شهر انجام می‌شود.
    # سپس نتیجه بر اساس کشور و استان بررسی می‌شود.

    for language in ("fa", "en"):

        try:
            response = requests.get(
                GEOCODING_URL,
                params={
                    "name": city,
                    "count": 20,
                    "language": language,
                    "format": "json"
                },
                timeout=20
            )

            response.raise_for_status()

            results = response.json().get("results", [])

            iran_results = [
                item for item in results
                if item.get("country_code") == "IR"
            ]

            if not iran_results:
                log.warning(
                    "No Iran geocoding results. City=%s Language=%s",
                    city,
                    language
                )
                continue

            # تلاش برای پیدا کردن نتیجه متعلق به استان انتخاب‌شده
            match = next(
                (
                    item for item in iran_results
                    if province in str(item.get("admin1", ""))
                ),
                None
            )

            # اگر استان مطابقت نداشت، نتیجه اول ایران انتخاب می‌شود.
            if match is None:
                match = iran_results[0]

            log.info(
                "Geocoding successful: city=%s province=%s "
                "lat=%s lon=%s",
                match.get("name"),
                match.get("admin1"),
                match.get("latitude"),
                match.get("longitude")
            )

            return (
                match["latitude"],
                match["longitude"]
            )

        except requests.RequestException:
            log.exception(
                "Geocoding HTTP error. City=%s Language=%s",
                city,
                language
            )

        except (ValueError, KeyError, TypeError):
            log.exception(
                "Geocoding data error. City=%s Language=%s",
                city,
                language
            )

    log.error(
        "Could not find coordinates for city=%s province=%s",
        city,
        province
    )

    return None


# =========================================
# WEATHER FETCH
# =========================================

def fetch_weather(city, province, nights_ahead):

    coordinates = get_coordinates(city, province)

    if not coordinates:
        log.error("Coordinates not found for %s", city)
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
                )
            },
            timeout=30
        )

        log.info(
            "Weather API HTTP status: %s",
            response.status_code
        )

        response.raise_for_status()

        data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])

        if not times:
            log.error(
                "Weather API returned no hourly data. Response=%s",
                data
            )
            return None

        timezone_name = data.get("timezone", "UTC")

        local_now = datetime.now(
            ZoneInfo(timezone_name)
        )

        target_date = (
            local_now.date() + timedelta(days=nights_ahead)
        ).isoformat()

        target_hour = 21

        # اگر ساعت ۹ شب گذشته باشد،
        # گزینه امشب به شب آینده منتقل می‌شود.
        if nights_ahead == 0 and local_now.hour >= 21:

            target_date = (
                local_now.date() + timedelta(days=1)
            ).isoformat()

        target_time = (
            f"{target_date}T{target_hour:02d}:00"
        )

        index = next(
            (
                i for i, t in enumerate(times)
                if t == target_time
            ),
            None
        )

        # اگر ساعت ۲۱ موجود نبود، ساعت نزدیک شب انتخاب می‌شود.
        if index is None:

            candidates = [
                i for i, t in enumerate(times)
                if t.startswith(target_date)
                and t[11:13] in {"20", "22", "23"}
            ]

            if not candidates:

                log.error(
                    "No evening forecast found for date=%s",
                    target_date
                )

                return None

            index = min(
                candidates,
                key=lambda i: abs(
                    int(times[i][11:13]) - target_hour
                )
            )

        def get_value(key):

            values = hourly.get(key, [])

            if index < len(values):
                return values[index]

            return None

        weather_result = {
            "datetime": times[index],
            "timezone": timezone_name,
            "temperature": get_value("temperature_2m"),
            "humidity": get_value("relative_humidity_2m"),
            "clouds": get_value("cloud_cover"),
            "rain": get_value("precipitation_probability"),
            "wind": get_value("wind_speed_10m"),
            "code": get_value("weather_code"),
        }

        log.info(
            "Weather successfully fetched for city=%s date=%s",
            city,
            weather_result["datetime"]
        )

        return weather_result

    except requests.HTTPError:

        log.exception(
            "Weather API returned HTTP error for city=%s",
            city
        )

        return None

    except requests.Timeout:

        log.error(
            "Weather API timeout for city=%s",
            city
        )

        return None

    except requests.RequestException:

        log.exception(
            "Weather API connection error for city=%s",
            city
        )

        return None

    except (ValueError, KeyError, TypeError):

        log.exception(
            "Weather API response processing error for city=%s",
            city
        )

        return None


# =========================================
# OBSERVATION RATING
# =========================================

def observation_rating(clouds, rain, wind):

    if clouds is None or rain is None or wind is None:

        return (
            "نامشخص",
            "داده کافی برای تخمین شرایط رصد در دست نیست."
        )

    if rain >= 50 or clouds >= 85 or wind >= 40:

        return (
            "نامناسب 🔴",
            "احتمالاً شرایط رصد چندان مناسب نیست."
        )

    if clouds <= 15 and rain <= 10 and wind <= 20:

        return (
            "خیلی خوب 🌟",
            "پوشش ابر و بارندگی کم است؛ "
            "شرایط می‌تواند برای رصد مناسب باشد."
        )

    if clouds <= 40 and rain <= 20 and wind <= 25:

        return (
            "مناسب 🟢",
            "شرایط نسبتاً خوبی برای رصد وجود دارد."
        )

    if clouds <= 70 and rain <= 30:

        return (
            "متوسط 🟡",
            "شاید بخش‌هایی از آسمان برای رصد قابل استفاده باشد."
        )

    return (
        "ضعیف 🟠",
        "ابر یا شرایط جوی می‌تواند رصد را دشوار کند."
    )


# =========================================
# START COMMAND
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    await update.message.reply_text(
        "🌌 به ربات گزارش وضعیت آسمان خوش آمدید!\n\n"
        "برای شروع، استان خود را انتخاب کنید:",
        reply_markup=provinces_keyboard()
    )


# =========================================
# MESSAGE HANDLER
# =========================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (update.message.text or "").strip()

    province = context.user_data.get("province")
    city = context.user_data.get("city")

    # CANCEL
    if text == "❌ لغو":

        context.user_data.clear()

        await update.message.reply_text(
            "لغو شد. برای شروع دوباره /start را بزنید."
        )

        return

    # PROVINCE SELECTION
    if text in PROVINCES:

        context.user_data["province"] = text
        context.user_data.pop("city", None)

        await update.message.reply_text(
            f"📍 استان {text} انتخاب شد.\n"
            "شهر خود را انتخاب کنید:",
            reply_markup=cities_keyboard(text)
        )

        return

    # BACK TO PROVINCES
    if text == "🔙 بازگشت به استان‌ها":

        context.user_data.pop("province", None)
        context.user_data.pop("city", None)

        await update.message.reply_text(
            "استان خود را انتخاب کنید:",
            reply_markup=provinces_keyboard()
        )

        return

    # CITY SELECTION
    if province and text in PROVINCES.get(province, []):

        context.user_data["city"] = text

        await update.message.reply_text(
            f"🏙 شهر {text} انتخاب شد.\n"
            "برای چه زمانی گزارش را می‌خواهید؟",
            reply_markup=time_keyboard()
        )

        return

    # CHANGE CITY
    if text in ("🔙 تغییر شهر", "🏙 تغییر شهر"):

        if province:

            await update.message.reply_text(
                "شهر خود را انتخاب کنید:",
                reply_markup=cities_keyboard(province)
            )

        else:

            await update.message.reply_text(
                "استان را انتخاب کنید:",
                reply_markup=provinces_keyboard()
            )

        return

    # CHANGE PROVINCE
    if text == "📍 تغییر استان":

        context.user_data.clear()

        await update.message.reply_text(
            "استان خود را انتخاب کنید:",
            reply_markup=provinces_keyboard()
        )

        return

    # CHECK ANOTHER TIME
    if text == "🔄 بررسی زمان دیگر":

        if city:

            await update.message.reply_text(
                "زمان موردنظر را انتخاب کنید:",
                reply_markup=time_keyboard()
            )

        else:

            await update.message.reply_text(
                "اول استان و شهر را انتخاب کنید:",
                reply_markup=provinces_keyboard()
            )

        return

    # TIME SELECTION
    if text in TIME_OPTIONS:

        province = context.user_data.get("province")
        city = context.user_data.get("city")

        if not city or not province:

            await update.message.reply_text(
                "ابتدا استان و شهر را انتخاب کنید.",
                reply_markup=provinces_keyboard()
            )

            return

        await update.message.reply_text(
            "⏳ در حال دریافت پیش‌بینی هوا...\n"
            "لطفاً کمی صبر کنید.",
            reply_markup=time_keyboard()
        )

        weather = fetch_weather(
            city,
            province,
            TIME_OPTIONS[text]
        )

        if not weather:

            await update.message.reply_text(
                "❌ دریافت پیش‌بینی ناموفق شد.\n\n"
                "ممکن است سرویس هواشناسی یا جستجوی شهر "
                "موقتاً در دسترس نباشد.\n\n"
                "چند دقیقه بعد دوباره امتحان کنید.",
                reply_markup=time_keyboard()
            )

            return

        rating, rating_text = observation_rating(
            weather["clouds"],
            weather["rain"],
            weather["wind"]
        )

        dt = datetime.fromisoformat(
            weather["datetime"]
        )

        date_text = dt.strftime("%Y-%m-%d")

        report = (
            "━━━━━━━━━━━━━━━━━━\n"
            "🌌 گزارش وضعیت آسمان\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 استان: {province}\n"
            f"🏙 شهر: {city}\n"
            f"📅 تاریخ: {date_text}\n"
            f"🕘 ساعت پیش‌بینی: {dt.strftime('%H:%M')}\n\n"
            f"🌤 وضعیت: "
            f"{WEATHER_CODES.get(weather['code'], 'وضعیت نامشخص')}\n"
            f"🌡 دما: "
            f"{weather['temperature'] if weather['temperature'] is not None else '—'}°C\n"
            f"💧 رطوبت: "
            f"{weather['humidity'] if weather['humidity'] is not None else '—'}٪\n"
            f"☁️ پوشش ابر: "
            f"{weather['clouds'] if weather['clouds'] is not None else '—'}٪\n"
            f"🌧 احتمال بارندگی: "
            f"{weather['rain'] if weather['rain'] is not None else '—'}٪\n"
            f"💨 سرعت باد: "
            f"{weather['wind'] if weather['wind'] is not None else '—'} km/h\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔭 تخمین شرایط رصد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"⭐ نتیجه: {rating}\n"
            f"{rating_text}\n\n"
            "ℹ️ این فقط تخمین بر اساس پیش‌بینی هواست؛ "
            "تضمین دیدن ستاره‌ها نیست.\n"
            "━━━━━━━━━━━━━━━━━━"
        )

        await update.message.reply_text(
            report,
            reply_markup=result_keyboard()
        )

        return

    # UNKNOWN MESSAGE
    await update.message.reply_text(
        "لطفاً از دکمه‌های کیبورد استفاده کنید "
        "یا /start را بزنید."
    )


# =========================================
# MAIN
# =========================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing. "
            "Set it in GitHub Secrets."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    log.info("Bot started; polling Telegram...")

    app.run_polling()


if __name__ == "__main__":
    main()
