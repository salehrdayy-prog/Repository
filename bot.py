# -*- coding: utf-8 -*-
"""
ربات گزارش وضعیت هوا و آسمان شهرهای ایران
منبع فهرست شهرها: sajaddp/list-of-cities-in-Iran (GPL-3.0)
مختصات هر شهر از Open-Meteo Geocoding دریافت و ذخیره می‌شود.
"""

import os
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ==================================================
# تنظیمات اصلی
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))

CONFIG_FILE = DATA_DIR / "cities.json"
CITIES_FILE = DATA_DIR / "cities_data.json"
PROVINCES_FILE = DATA_DIR / "provinces_data.json"
GEO_CACHE_FILE = DATA_DIR / "geocoding_cache.json"

DEFAULT_DATA_CONFIG = {
    "cities_url": (
        "https://raw.githubusercontent.com/"
        "sajaddp/list-of-cities-in-Iran/"
        "main/dist/json/cities-filtered.json"
    ),
    "provinces_url": (
        "https://raw.githubusercontent.com/"
        "sajaddp/list-of-cities-in-Iran/"
        "main/dist/json/provinces.json"
    ),
    "dataset": "cities-filtered",
    "expected_city_count": 1481
}

GEOCODING_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"
)

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
)

PAGE_SIZE = 24
REQUEST_TIMEOUT = 25

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "IranWeatherTelegramBot/1.0"
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    )
)

log = logging.getLogger("iran-weather-bot")


# ==================================================
# نام انگلیسی استان‌ها
# ==================================================

PROVINCE_EN = {
    "آذربایجان شرقی": "East Azerbaijan",
    "آذربایجان غربی": "West Azerbaijan",
    "اردبیل": "Ardabil",
    "اصفهان": "Isfahan",
    "البرز": "Alborz",
    "ایلام": "Ilam",
    "بوشهر": "Bushehr",
    "تهران": "Tehran",
    "چهارمحال و بختیاری": "Chaharmahal and Bakhtiari",
    "خراسان جنوبی": "South Khorasan",
    "خراسان رضوی": "Razavi Khorasan",
    "خراسان شمالی": "North Khorasan",
    "خوزستان": "Khuzestan",
    "زنجان": "Zanjan",
    "سمنان": "Semnan",
    "سیستان و بلوچستان": "Sistan and Baluchestan",
    "فارس": "Fars",
    "قزوین": "Qazvin",
    "قم": "Qom",
    "کردستان": "Kurdistan",
    "کرمان": "Kerman",
    "کرمانشاه": "Kermanshah",
    "کهگیلویه و بویراحمد": "Kohgiluyeh and Boyer-Ahmad",
    "گلستان": "Golestan",
    "گیلان": "Gilan",
    "لرستان": "Lorestan",
    "مازندران": "Mazandaran",
    "مرکزی": "Markazi",
    "هرمزگان": "Hormozgan",
    "همدان": "Hamadan",
    "یزد": "Yazd",
}


# ==================================================
# کدهای وضعیت هوا
# ==================================================

WEATHER_CODES = {
    0: "آسمان صاف",
    1: "عمدتاً صاف",
    2: "نیمه‌ابری",
    3: "ابری",
    45: "مه‌آلود",
    48: "مه یخ‌زننده",
    51: "نم‌نم باران خفیف",
    53: "نم‌نم باران",
    55: "نم‌نم باران شدید",
    56: "باران یخ‌زننده خفیف",
    57: "باران یخ‌زننده شدید",
    61: "باران خفیف",
    63: "باران",
    65: "باران شدید",
    66: "باران یخ‌زننده خفیف",
    67: "باران یخ‌زننده شدید",
    71: "برف خفیف",
    73: "برف",
    75: "برف شدید",
    77: "دانه‌های برف",
    80: "رگبار باران خفیف",
    81: "رگبار باران",
    82: "رگبار باران شدید",
    85: "رگبار برف خفیف",
    86: "رگبار برف شدید",
    95: "رعدوبرق",
    96: "رعدوبرق همراه تگرگ خفیف",
    99: "رعدوبرق همراه تگرگ شدید",
}


# ==================================================
# توابع عمومی
# ==================================================

def normalize_text(value):

    value = str(value or "").strip()

    value = value.replace("ي", "ی")
    value = value.replace("ك", "ک")
    value = value.replace("ۀ", "ه")
    value = value.replace("ة", "ه")

    value = re.sub(r"\s+", " ", value)

    return value


def norm_key(value):

    value = normalize_text(value)

    value = value.replace("‌", " ")
    value = value.replace("-", " ")

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip().casefold()


def get_list(payload):

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):

        for key in (
            "data",
            "items",
            "results",
            "provinces",
            "cities"
        ):

            if isinstance(payload.get(key), list):
                return payload[key]

    raise ValueError(
        "ساختار فایل JSON قابل‌شناسایی نیست."
    )


# ==================================================
# دانلود و ذخیره اطلاعات
# ==================================================

def download_json(url, local_path):

    if local_path.exists():

        try:

            with local_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                cached = json.load(file)

            if isinstance(cached, (list, dict)):
                return cached

        except Exception:

            log.warning(
                "فایل cache خراب است؛ دوباره دانلود می‌شود: %s",
                local_path
            )

    response = SESSION.get(
        url,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    payload = response.json()

    local_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = local_path.with_suffix(
        local_path.suffix + ".tmp"
    )

    with temp.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False
        )

    temp.replace(local_path)

    return payload


# ==================================================
# بارگذاری استان‌ها و شهرها
# ==================================================

def load_datasets():

    if not CONFIG_FILE.exists():

        CONFIG_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with CONFIG_FILE.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                DEFAULT_DATA_CONFIG,
                file,
                ensure_ascii=False,
                indent=2
            )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8"
    ) as file:

        data_config = json.load(file)

    cities_url = (
        data_config.get("cities_url")
        or DEFAULT_DATA_CONFIG["cities_url"]
    )

    provinces_url = (
        data_config.get("provinces_url")
        or DEFAULT_DATA_CONFIG["provinces_url"]
    )

    cities_raw = download_json(
        cities_url,
        CITIES_FILE
    )

    provinces_raw = download_json(
        provinces_url,
        PROVINCES_FILE
    )

    cities = get_list(cities_raw)
    provinces = get_list(provinces_raw)

    province_names = {}

    for province in provinces:

        if not isinstance(province, dict):
            continue

        province_id = province.get(
            "id",
            province.get("province_id")
        )

        province_name = (
            province.get("name_fa")
            or province.get("name")
            or province.get("title")
            or province.get("province_name")
            or province.get("name_fa_IR")
        )

        if province_id is not None and province_name:

            province_names[str(province_id)] = (
                normalize_text(province_name)
            )

    clean_cities = []

    for city in cities:

        if not isinstance(city, dict):
            continue

        city_name = (
            city.get("name_fa")
            or city.get("name")
            or city.get("city")
        )

        province_id = city.get("province_id")

        if city_name and province_id is not None:

            province_name = province_names.get(
                str(province_id)
            )

            if not province_name:

                province_name = (
                    city.get("province_name")
                    or city.get("province")
                )

            if province_name:

                clean_cities.append({

                    "id": city.get("id"),

                    "name": normalize_text(
                        city_name
                    ),

                    "province_id": str(
                        province_id
                    ),

                    "province": normalize_text(
                        province_name
                    )

                })

    if not clean_cities:

        raise RuntimeError(
            "از فایل شهرها هیچ رکورد معتبری خوانده نشد؛ "
            "ساختار منبع تغییر کرده است."
        )

    return clean_cities, province_names


# ==================================================
# حافظه مختصات جغرافیایی
# ==================================================

def load_geo_cache():

    try:

        with GEO_CACHE_FILE.open(
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

            return (
                data
                if isinstance(data, dict)
                else {}
            )

    except Exception:

        return {}


def save_geo_cache(cache):

    try:

        temp = GEO_CACHE_FILE.with_suffix(".tmp")

        with temp.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2
            )

        temp.replace(GEO_CACHE_FILE)

    except Exception:

        log.exception(
            "ذخیره cache مختصات ناموفق بود."
        )


# بارگذاری اطلاعات هنگام شروع ربات

CITIES, PROVINCE_NAMES = load_datasets()

GEO_CACHE = load_geo_cache()

PROVINCES = sorted(
    {
        city["province"]
        for city in CITIES
    },
    key=norm_key
)


# ==================================================
# تبدیل نام استان
# ==================================================

def province_english(persian_name):

    key = norm_key(persian_name)

    for fa, en in PROVINCE_EN.items():

        if norm_key(fa) == key:
            return en

    return persian_name


# ==================================================
# دریافت مختصات شهر
# ==================================================

def get_city_coordinates(
    city_name,
    province_name
):

    cache_key = (
        norm_key(city_name)
        + "|"
        + norm_key(province_name)
    )

    if cache_key in GEO_CACHE:

        item = GEO_CACHE[cache_key]

        return (
            float(item["latitude"]),
            float(item["longitude"]),
            item.get("timezone", "Asia/Tehran")
        )

    province_en = province_english(
        province_name
    )

    attempts = [

        {
            "name": city_name,
            "count": 100,
            "language": "fa",
            "format": "json",
            "countryCode": "IR"
        },

        {
            "name": f"{city_name}, {province_en}",
            "count": 100,
            "language": "en",
            "format": "json",
            "countryCode": "IR"
        },

        {
            "name": f"{city_name}, {province_name}",
            "count": 100,
            "language": "fa",
            "format": "json",
            "countryCode": "IR"
        }

    ]

    all_results = []

    for params in attempts:

        try:

            response = SESSION.get(
                GEOCODING_URL,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            results = (
                response.json().get("results")
                or []
            )

            all_results.extend(results)

        except Exception as exc:

            log.warning(
                "Geocoding request failed for %s: %s",
                city_name,
                exc
            )

    province_aliases = {

        norm_key(province_name),

        norm_key(province_en)

    }

    if province_name == "آذربایجان شرقی":

        province_aliases.add(
            norm_key("East Azarbaijan")
        )

    if province_name == "آذربایجان غربی":

        province_aliases.add(
            norm_key("West Azarbaijan")
        )

    if province_name == "سیستان و بلوچستان":

        province_aliases.add(
            norm_key("Sistan and Baluchestan")
        )

    candidates = []

    seen = set()

    for item in all_results:

        if str(
            item.get("country_code", "")
        ).upper() != "IR":

            continue

        latitude = item.get("latitude")
        longitude = item.get("longitude")

        if latitude is None or longitude is None:
            continue

        admin1 = item.get("admin1") or ""

        if (
            admin1
            and norm_key(admin1) not in province_aliases
        ):

            continue

        signature = (
            round(float(latitude), 4),
            round(float(longitude), 4)
        )

        if signature not in seen:

            seen.add(signature)
            candidates.append(item)

    if not candidates:

        raise LookupError(
            f"مختصات شهر «{city_name}» "
            f"در استان «{province_name}» پیدا نشد."
        )

    if len(candidates) > 1:

        exact = [

            item
            for item in candidates

            if norm_key(
                item.get("admin1", "")
            ) in province_aliases

        ]

        if len(exact) == 1:

            candidates = exact

        elif len(exact) > 1:

            candidates = exact

        else:

            raise LookupError(
                f"نتیجه جغرافیایی شهر «{city_name}» "
                "مبهم است؛ مختصات اشتباه انتخاب نشد."
            )

    chosen = candidates[0]

    result = {

        "latitude": float(
            chosen["latitude"]
        ),

        "longitude": float(
            chosen["longitude"]
        ),

        "timezone": (
            chosen.get("timezone")
            or "Asia/Tehran"
        )

    }

    GEO_CACHE[cache_key] = result

    save_geo_cache(GEO_CACHE)

    return (
        result["latitude"],
        result["longitude"],
        result["timezone"]
    )


# ==================================================
# وضعیت هوا
# ==================================================

def weather_description(code):

    try:

        return WEATHER_CODES.get(
            int(code),
            "وضعیت نامشخص"
        )

    except (TypeError, ValueError):

        return "وضعیت نامشخص"


# ==================================================
# ارزیابی وضعیت رصد آسمان
# ==================================================

def sky_rating(
    cloud,
    rain,
    wind
):

    cloud = (
        cloud
        if cloud is not None
        else 100
    )

    rain = (
        rain
        if rain is not None
        else 0
    )

    wind = (
        wind
        if wind is not None
        else 0
    )

    if rain >= 40 or wind >= 35:

        return "🔴 نامناسب"

    if cloud >= 80 or rain >= 20 or wind >= 25:

        return "🟠 نسبتاً نامناسب"

    if (
        cloud <= 35
        and rain < 10
        and wind < 20
    ):

        return "🟢 مناسب"

    return "🟡 متوسط"


def format_num(
    value,
    digits=1
):

    if value is None:
        return "نامشخص"

    return f"{float(value):.{digits}f}"


# ==================================================
# دریافت پیش‌بینی هواشناسی
# ==================================================

def get_forecast(
    city,
    province,
    day_offset
):

    latitude, longitude, timezone = (
        get_city_coordinates(
            city,
            province
        )
    )

    params = {

        "latitude": latitude,

        "longitude": longitude,

        "hourly": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "cloud_cover,"
            "precipitation_probability,"
            "wind_speed_10m,"
            "weather_code"
        ),

        "forecast_days": 7,

        "timezone": timezone

    }

    response = SESSION.get(
        WEATHER_URL,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    hourly = data.get("hourly", {})

    times = hourly.get("time", [])

    if not times:

        raise RuntimeError(
            "داده ساعتی هواشناسی دریافت نشد."
        )

    target_date = (
        datetime.now(
            ZoneInfo(timezone)
        ).date()
        + timedelta(days=day_offset)
    )

    exact_index = None

    nearest = []

    for index, stamp in enumerate(times):

        try:

            dt = datetime.fromisoformat(stamp)

        except ValueError:

            continue

        if dt.date() == target_date:

            if dt.hour == 21:

                exact_index = index

                break

            if 18 <= dt.hour <= 23:

                nearest.append(
                    (
                        abs(dt.hour - 21),
                        index
                    )
                )

    if exact_index is None:

        if not nearest:

            raise RuntimeError(
                "برای تاریخ انتخاب‌شده پیش‌بینی در دسترس نیست."
            )

        exact_index = sorted(nearest)[0][1]

    def val(key):

        array = hourly.get(key, [])

        return (
            array[exact_index]
            if exact_index < len(array)
            else None
        )

    return {

        "time": times[exact_index],

        "temperature": val(
            "temperature_2m"
        ),

        "humidity": val(
            "relative_humidity_2m"
        ),

        "cloud": val(
            "cloud_cover"
        ),

        "rain": val(
            "precipitation_probability"
        ),

        "wind": val(
            "wind_speed_10m"
        ),

        "code": val(
            "weather_code"
        ),

        "timezone": timezone,

        "latitude": latitude,

        "longitude": longitude

    }


# ==================================================
# کیبوردهای تلگرام
# ==================================================

def keyboard(
    rows,
    resize=True
):

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=resize
    )


def province_keyboard():

    rows = []

    for i in range(
        0,
        len(PROVINCES),
        2
    ):

        rows.append(
            [
                KeyboardButton(x)
                for x in PROVINCES[i:i + 2]
            ]
        )

    rows.append([
        KeyboardButton("🔄 شروع مجدد")
    ])

    return keyboard(rows)


def city_keyboard(
    context,
    page=0
):

    cities = context.user_data.get(
        "province_cities",
        []
    )

    start = page * PAGE_SIZE

    page_items = cities[
        start:start + PAGE_SIZE
    ]

    rows = []

    for i in range(
        0,
        len(page_items),
        2
    ):

        rows.append(
            [
                KeyboardButton(city["name"])
                for city in page_items[i:i + 2]
            ]
        )

    nav = []

    if page > 0:

        nav.append(
            KeyboardButton("⬅️ صفحه قبل")
        )

    if start + PAGE_SIZE < len(cities):

        nav.append(
            KeyboardButton("صفحه بعد ➡️")
        )

    if nav:
        rows.append(nav)

    rows.append([
        KeyboardButton("🏠 انتخاب استان")
    ])

    rows.append([
        KeyboardButton("🔄 شروع مجدد")
    ])

    return keyboard(rows)


def time_keyboard():

    return keyboard([

        [
            KeyboardButton("امشب"),
            KeyboardButton("فرداشب")
        ],

        [
            KeyboardButton("۲ شب بعد"),
            KeyboardButton("۳ شب بعد")
        ],

        [
            KeyboardButton("🏠 انتخاب استان")
        ],

        [
            KeyboardButton("🔄 شروع مجدد")
        ]

    ])


# ==================================================
# شروع ربات
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(

        "🌤 به ربات گزارش وضعیت هوا و آسمان شهرهای ایران خوش آمدید.\n\n"

        "ابتدا استان موردنظرتان را انتخاب کنید:",

        reply_markup=province_keyboard()

    )


# ==================================================
# مدیریت پیام‌های کاربر
# ==================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.message
        or not update.message.text
    ):

        return

    text = normalize_text(
        update.message.text
    )

    # شروع مجدد

    if text in (
        "/start",
        "🔄 شروع مجدد"
    ):

        await start(
            update,
            context
        )

        return

    # بازگشت به انتخاب استان

    if text == "🏠 انتخاب استان":

        context.user_data.clear()

        await update.message.reply_text(

            "استان را انتخاب کنید:",

            reply_markup=province_keyboard()

        )

        return

    # صفحه قبل

    if text == "⬅️ صفحه قبل":

        page = max(
            0,
            int(
                context.user_data.get(
                    "city_page",
                    0
                )
            ) - 1
        )

        context.user_data["city_page"] = page

        await update.message.reply_text(

            "شهر موردنظر را انتخاب کنید:",

            reply_markup=city_keyboard(
                context,
                page
            )

        )

        return

    # صفحه بعد

    if text == "صفحه بعد ➡️":

        page = (
            int(
                context.user_data.get(
                    "city_page",
                    0
                )
            ) + 1
        )

        context.user_data["city_page"] = page

        await update.message.reply_text(

            "شهر موردنظر را انتخاب کنید:",

            reply_markup=city_keyboard(
                context,
                page
            )

        )

        return

    # انتخاب استان

    if text in PROVINCES:

        province_cities = sorted(

            [
                city
                for city in CITIES

                if norm_key(
                    city["province"]
                ) == norm_key(text)
            ],

            key=lambda x: norm_key(
                x["name"]
            )

        )

        if not province_cities:

            await update.message.reply_text(
                "برای این استان شهری در فهرست پیدا نشد."
            )

            return

        context.user_data.clear()

        context.user_data["province"] = text

        context.user_data["province_cities"] = (
            province_cities
        )

        context.user_data["city_page"] = 0

        await update.message.reply_text(

            f"استان «{text}» انتخاب شد.\n"

            f"تعداد شهرهای فهرست: {len(province_cities)}\n"

            "حالا شهر را انتخاب کنید:",

            reply_markup=city_keyboard(
                context,
                0
            )

        )

        return

    # انتخاب شهر

    province = context.user_data.get(
        "province"
    )

    if (
        province
        and "province_cities" in context.user_data
    ):

        selected = next(

            (
                city
                for city in context.user_data[
                    "province_cities"
                ]

                if norm_key(
                    city["name"]
                ) == norm_key(text)
            ),

            None

        )

        if selected:

            context.user_data["city"] = (
                selected["name"]
            )

            await update.message.reply_text(

                f"شهر «{selected['name']}» "
                f"از استان «{province}» انتخاب شد.\n"

                "زمان پیش‌بینی را انتخاب کنید:",

                reply_markup=time_keyboard()

            )

            return

    # انتخاب زمان

    if text in (
        "امشب",
        "فرداشب",
        "۲ شب بعد",
        "۳ شب بعد"
    ):

        city = context.user_data.get(
            "city"
        )

        province = context.user_data.get(
            "province"
        )

        if not city or not province:

            await update.message.reply_text(
                "ابتدا استان و شهر را انتخاب کنید. /start"
            )

            return

        offset = {

            "امشب": 0,

            "فرداشب": 1,

            "۲ شب بعد": 2,

            "۳ شب بعد": 3

        }[text]

        status = await update.message.reply_text(
            "⏳ در حال دریافت پیش‌بینی هوا..."
        )

        try:

            result = get_forecast(
                city,
                province,
                offset
            )

            local_dt = datetime.fromisoformat(
                result["time"]
            )

            rating = sky_rating(

                result["cloud"],

                result["rain"],

                result["wind"]

            )

            date_text = local_dt.strftime(
                "%Y-%m-%d"
            )

            report = (

                "━━━━━━━━━━━━━━━━━━\n"

                "🌌 گزارش وضعیت آسمان\n"

                "━━━━━━━━━━━━━━━━━━\n\n"

                f"📍 استان: {province}\n"

                f"🏙 شهر: {city}\n"

                f"📅 تاریخ: {date_text}\n"

                f"🕘 ساعت: {local_dt.strftime('%H:%M')} "
                "(به وقت محلی)\n\n"

                f"🌥 وضعیت هوا: "
                f"{weather_description(result['code'])}\n"

                f"🌡 دما: "
                f"{format_num(result['temperature'])} °C\n"

                f"💧 رطوبت: "
                f"{format_num(result['humidity'], 0)}٪\n"

                f"☁️ پوشش ابر: "
                f"{format_num(result['cloud'], 0)}٪\n"

                f"🌧 احتمال بارش: "
                f"{format_num(result['rain'], 0)}٪\n"

                f"💨 سرعت باد: "
                f"{format_num(result['wind'])} km/h\n\n"

                f"🔭 ارزیابی رصد آسمان: {rating}\n\n"

                "ℹ️ این گزارش پیش‌بینی مدل هواشناسی است "
                "و ممکن است تغییر کند."

            )

            await status.edit_text(
                report
            )

        except LookupError as exc:

            await status.edit_text(

                "⚠️ برای این شهر مختصات مطمئن پیدا نشد.\n"

                f"{exc}\n\n"

                "برای جلوگیری از گزارش اشتباه، "
                "مختصات شهر دیگری انتخاب نشد."

            )

        except requests.RequestException:

            log.exception(
                "Network error"
            )

            await status.edit_text(
                "❌ ارتباط با سرویس هواشناسی برقرار نشد. "
                "کمی بعد دوباره تلاش کنید."
            )

        except Exception:

            log.exception(
                "Forecast error"
            )

            await status.edit_text(
                "❌ هنگام ساخت گزارش خطایی رخ داد. "
                "جزئیات در گزارش اجرای ربات ثبت شد."
            )

        return

    # پیام نامعتبر

    await update.message.reply_text(

        "لطفاً یکی از گزینه‌های کیبورد را انتخاب کنید "
        "یا /start را بزنید."

    )


# ==================================================
# اجرای ربات
# ==================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "متغیر محیطی BOT_TOKEN تنظیم نشده است."
        )

    log.info(
        "Loaded %d cities across %d provinces.",
        len(CITIES),
        len(PROVINCES)
    )

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
