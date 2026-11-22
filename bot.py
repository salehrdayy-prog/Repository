# -*- coding: utf-8 -*-
"""
ربات گزارش وضعیت هوا و آسمان شهرهای ایران
منبع فهرست شهرها: sajaddp/list-of-cities-in-Iran (GPL-3.0)
مختصات هر شهر در زمان نیاز از Open-Meteo Geocoding دریافت و cache می‌شود.
"""
import os
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# -------------------- تنظیمات --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "."))
CONFIG_FILE = DATA_DIR / "cities.json"
CITIES_FILE = DATA_DIR / "cities_data.json"
PROVINCES_FILE = DATA_DIR / "provinces_data.json"
GEO_CACHE_FILE = DATA_DIR / "geocoding_cache.json"

DEFAULT_DATA_CONFIG = {
    "cities_url": "https://raw.githubusercontent.com/sajaddp/list-of-cities-in-Iran/main/dist/json/cities-filtered.json",
    "provinces_url": "https://raw.githubusercontent.com/sajaddp/list-of-cities-in-Iran/main/dist/json/provinces.json",
    "dataset": "cities-filtered",
    "expected_city_count": 1481
}

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

PAGE_SIZE = 24
REQUEST_TIMEOUT = 25

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "IranWeatherTelegramBot/1.0"
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

log = logging.getLogger("iran-weather-bot")

# نام انگلیسی استان‌ها برای کمک به رفع ابهام در Geocoding
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

# -------------------- ابزارهای عمومی --------------------

def normalize_text(value):
    value = str(value or "").strip()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("ۀ", "ه").replace("ة", "ه")
    value = re.sub(r"\s+", " ", value)
    return value


def norm_key(value):
    value = normalize_text(value)
    value = value.replace("‌", " ").replace("-", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def get_list(payload):
    """با چند ساختار رایج JSON سازگار است."""
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "items", "results", "provinces", "cities"):
            if isinstance(payload.get(key), list):
                return payload[key]

    raise ValueError("ساختار فایل JSON قابل‌شناسایی نیست.")


def download_json(url, local_path):
    if local_path.exists():
        try:
            with local_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)

            if isinstance(cached, (list, dict)):
                return cached

        except Exception:
            log.warning(
                "فایل cache خراب است؛ دوباره دانلود می‌شود: %s",
                local_path
            )

    response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = response.json()

    local_path.parent.mkdir(parents=True, exist_ok=True)

    temp = local_path.with_suffix(local_path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    temp.replace(local_path)

    return payload


def load_datasets():
    # cities.json فایل تنظیمات منبع داده است؛ خود فهرست کامل شهرها
    # در اولین اجرا از منبع اصلی دریافت و در cities_data.json cache می‌شود.

    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(
                DEFAULT_DATA_CONFIG,
                f,
                ensure_ascii=False,
                indent=2
            )

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data_config = json.load(f)

    cities_url = (
        data_config.get("cities_url")
        or DEFAULT_DATA_CONFIG["cities_url"]
    )

    provinces_url = (
        data_config.get("provinces_url")
        or DEFAULT_DATA_CONFIG["provinces_url"]
    )

    cities_raw = download_json(cities_url, CITIES_FILE)
    provinces_raw = download_json(provinces_url, PROVINCES_FILE)

    cities = get_list(cities_raw)
    provinces = get_list(provinces_raw)

    # شناسه استان -> نام استان
    province_names = {}

    for p in provinces:
        if not isinstance(p, dict):
            continue

        pid = p.get("id", p.get("province_id"))

        pname = (
            p.get("name_fa")
            or p.get("name")
            or p.get("title")
            or p.get("province_name")
            or p.get("name_fa_IR")
        )

        if pid is not None and pname:
            province_names[str(pid)] = normalize_text(pname)

    # اگر فایل استان نام‌های انگلیسی/فارسی متفاوت داشته باشد،
    # نگاشت فارسی را ترجیح می‌دهیم.
    clean_cities = []

    for c in cities:
        if not isinstance(c, dict):
            continue

        name = (
            c.get("name_fa")
            or c.get("name")
            or c.get("city")
        )

        pid = c.get("province_id")

        if name and pid is not None:
            pname = province_names.get(str(pid))

            if not pname:
                # در بعضی نسخه‌ها نام استان مستقیماً در رکورد شهر قرار دارد.
                pname = (
                    c.get("province_name")
                    or c.get("province")
                )

            if pname:
                clean_cities.append({
                    "id": c.get("id"),
                    "name": normalize_text(name),
                    "province_id": str(pid),
                    "province": normalize_text(pname),
                })

    if not clean_cities:
        raise RuntimeError(
            "از فایل شهرها هیچ رکورد معتبری خوانده نشد؛ "
            "ساختار منبع تغییر کرده است."
        )

    return clean_cities, province_names


def load_geo_cache():
    try:
        with GEO_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

            return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def save_geo_cache(cache):
    try:
        temp = GEO_CACHE_FILE.with_suffix(".tmp")

        with temp.open("w", encoding="utf-8") as f:
            json.dump(
                cache,
                f,
                ensure_ascii=False,
                indent=2
            )

        temp.replace(GEO_CACHE_FILE)

    except Exception:
        log.exception("ذخیره cache مختصات ناموفق بود.")


CITIES, PROVINCE_NAMES = load_datasets()
GEO_CACHE = load_geo_cache()

# استان‌ها از خود داده‌ها استخراج می‌شوند؛ ترتیب الفبایی فارسی
PROVINCES = sorted(
    {c["province"] for c in CITIES},
    key=norm_key
)


def province_english(persian_name):
    key = norm_key(persian_name)

    for fa, en in PROVINCE_EN.items():
        if norm_key(fa) == key:
            return en

    return persian_name


# -------------------- پیدا کردن مختصات شهر --------------------

def get_city_coordinates(city_name, province_name):
    """
    مختصات را برای همه شهرها به‌صورت پویا پیدا می‌کند.
    اولویت: Open-Meteo با فیلتر استان، سپس Nominatim.
    نتیجه فقط وقتی ذخیره می‌شود که تطبیق شهر/استان قابل‌قبول باشد.
    """

    cache_key = (
        norm_key(city_name)
        + "|"
        + norm_key(province_name)
    )

    cached = GEO_CACHE.get(cache_key)

    if cached:
        try:
            return (
                float(cached["latitude"]),
                float(cached["longitude"]),
                cached.get("timezone") or "Asia/Tehran",
            )

        except (KeyError, TypeError, ValueError):
            GEO_CACHE.pop(cache_key, None)

    province_en = province_english(province_name)

    aliases = {
        norm_key(province_name),
        norm_key(province_en),
        norm_key(province_en + " Province"),
    }

    province_aliases = {
        "آذربایجان شرقی": [
            "East Azarbaijan",
            "East Azerbaijan",
            "East Azerbaijan Province"
        ],
        "آذربایجان غربی": [
            "West Azarbaijan",
            "West Azerbaijan",
            "West Azerbaijan Province"
        ],
        "سیستان و بلوچستان": [
            "Sistan and Baluchestan",
            "Sistan and Baluchestan Province"
        ],
        "همدان": [
            "Hamedan",
            "Hamedan Province",
            "Hamadan",
            "Hamadan Province"
        ],
        "چهارمحال و بختیاری": [
            "Chaharmahal Bakhtiari",
            "Chaharmahal and Bakhtiari Province"
        ],
        "کهگیلویه و بویراحمد": [
            "Kohgiluyeh Boyer Ahmad",
            "Kohgiluyeh and Boyer-Ahmad Province"
        ],
    }

    for fa_name, values in province_aliases.items():
        if norm_key(fa_name) == norm_key(province_name):
            aliases.update(norm_key(v) for v in values)

    def clean_admin(value):
        value = norm_key(value)
        value = value.replace(" province", "")
        value = value.replace(" استان", "")
        return value.strip()

    normalized_aliases = {
        clean_admin(x)
        for x in aliases
    }

    def province_matches(value):
        if not value:
            return False

        return clean_admin(value) in normalized_aliases

    def store(lat, lon, timezone="Asia/Tehran"):
        result = {
            "latitude": float(lat),
            "longitude": float(lon),
            "timezone": timezone or "Asia/Tehran",
        }

        GEO_CACHE[cache_key] = result
        save_geo_cache(GEO_CACHE)

        return (
            result["latitude"],
            result["longitude"],
            result["timezone"]
        )

    wanted_city = norm_key(city_name)

    def city_name_matches(candidate_name):
        candidate = norm_key(candidate_name)

        if not candidate:
            return False

        # API از تطبیق پیشوندی استفاده می‌کند؛ تطبیق دقیق در اولویت است.
        return (
            candidate == wanted_city
            or candidate.startswith(wanted_city + " ")
            or wanted_city.startswith(candidate + " ")
        )

    # درخواست‌های دارای نام استان، برای جلوگیری از انتخاب شهر هم‌نام در استان دیگر.
    qualified_attempts = [
        (f"{city_name}, {province_en}", "en"),
        (f"{city_name}, {province_name}", "fa"),
    ]

    for query, language in qualified_attempts:
        try:
            response = SESSION.get(
                GEOCODING_URL,
                params={
                    "name": query,
                    "count": 100,
                    "language": language,
                    "format": "json",
                    "countryCode": "IR",
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            results = response.json().get("results") or []

            matches = []

            for item in results:
                if str(item.get("country_code", "")).upper() != "IR":
                    continue

                if (
                    item.get("latitude") is None
                    or item.get("longitude") is None
                ):
                    continue

                admin1 = item.get("admin1") or ""

                # چون استان در خود query قید شده، نبود admin1 قابل قبول است؛
                # اما اگر admin1 وجود دارد، باید با استان انتخابی سازگار باشد.
                if admin1 and not province_matches(admin1):
                    continue

                if not city_name_matches(item.get("name", "")):
                    continue

                feature = str(item.get("feature_code") or "")
                population = int(item.get("population") or 0)

                score = 100

                if admin1 and province_matches(admin1):
                    score += 50

                if feature.startswith("PPL"):
                    score += 30

                if feature in {
                    "PPLC",
                    "PPLA",
                    "PPLA2",
                    "PPLA3",
                    "PPLA4"
                }:
                    score += 20

                score += min(population // 10000, 20)

                matches.append((score, population, item))

            if matches:
                matches.sort(
                    key=lambda row: (row[0], row[1]),
                    reverse=True
                )

                best_score = matches[0][0]

                best = [
                    row[2]
                    for row in matches
                    if row[0] == best_score
                ]

                # اگر چند نتیجه با امتیاز برابر مختصات متفاوت داشته باشند،
                # به‌جای انتخاب تصادفی، سراغ روش جایگزین می‌رویم.
                unique = {}

                for item in best:
                    key = (
                        round(float(item["latitude"]), 5),
                        round(float(item["longitude"]), 5)
                    )

                    unique[key] = item

                if len(unique) == 1:
                    item = next(iter(unique.values()))

                    return store(
                        item["latitude"],
                        item["longitude"],
                        item.get("timezone") or "Asia/Tehran",
                    )

        except requests.RequestException as exc:
            log.warning(
                "Open-Meteo geocoding failed for %s / %s: %s",
                city_name,
                province_name,
                exc
            )

        except (ValueError, TypeError, KeyError) as exc:
            log.warning(
                "Invalid Open-Meteo geocoding response for %s: %s",
                city_name,
                exc
            )

    # جست‌وجوی بدون qualifier فقط وقتی نتیجه استان را صریحاً تأیید کند.
    for language in ("fa", "en"):
        try:
            response = SESSION.get(
                GEOCODING_URL,
                params={
                    "name": city_name,
                    "count": 100,
                    "language": language,
                    "format": "json",
                    "countryCode": "IR",
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            matches = []

            for item in response.json().get("results") or []:
                if str(item.get("country_code", "")).upper() != "IR":
                    continue

                if not province_matches(item.get("admin1", "")):
                    continue

                if not city_name_matches(item.get("name", "")):
                    continue

                if (
                    item.get("latitude") is None
                    or item.get("longitude") is None
                ):
                    continue

                feature = str(item.get("feature_code") or "")
                population = int(item.get("population") or 0)

                score = (
                    (30 if feature.startswith("PPL") else 0)
                    + min(population // 10000, 20)
                )

                matches.append((score, population, item))

            if matches:
                matches.sort(
                    key=lambda row: (row[0], row[1]),
                    reverse=True
                )

                top_score = matches[0][0]

                best = [
                    row[2]
                    for row in matches
                    if row[0] == top_score
                ]

                unique = {
                    (
                        round(float(x["latitude"]), 5),
                        round(float(x["longitude"]), 5)
                    ): x
                    for x in best
                }

                if len(unique) == 1:
                    item = next(iter(unique.values()))

                    return store(
                        item["latitude"],
                        item["longitude"],
                        item.get("timezone") or "Asia/Tehran",
                    )

        except requests.RequestException as exc:
            log.warning(
                "Open-Meteo broad search failed for %s: %s",
                city_name,
                exc
            )

    # جایگزین: OpenStreetMap. نتیجه باید نام شهر و استان را تأیید کند.
    nominatim_queries = [
        f"{city_name}, {province_name}, Iran",
        f"{city_name}, {province_en}, Iran",
    ]

    for query in nominatim_queries:
        try:
            time.sleep(1.1)

            response = SESSION.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "namedetails": 1,
                    "limit": 10,
                    "countrycodes": "ir",
                },
                headers={
                    "User-Agent": (
                        "IranWeatherTelegramBot/1.0 "
                        "(geocoding; contact: bot-admin)"
                    )
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            valid = []

            for item in response.json():
                address = item.get("address") or {}

                country = norm_key(
                    address.get("country", "")
                )

                if country not in {
                    norm_key("Iran"),
                    norm_key("ایران")
                }:
                    continue

                state = (
                    address.get("state")
                    or address.get("province")
                    or ""
                )

                if state and not province_matches(state):
                    continue

                namedetails = item.get("namedetails") or {}

                possible_names = [
                    item.get("name", ""),
                    namedetails.get("name:fa", ""),
                    namedetails.get("name", ""),
                    address.get("city", ""),
                    address.get("town", ""),
                    address.get("village", ""),
                    address.get("municipality", ""),
                ]

                if not any(
                    city_name_matches(name)
                    for name in possible_names
                ):
                    continue

                if (
                    item.get("lat") is not None
                    and item.get("lon") is not None
                ):
                    valid.append(item)

            unique = {}

            for item in valid:
                key = (
                    round(float(item["lat"]), 5),
                    round(float(item["lon"]), 5)
                )

                unique[key] = item

            if len(unique) == 1:
                item = next(iter(unique.values()))

                return store(
                    item["lat"],
                    item["lon"],
                    "Asia/Tehran"
                )

        except requests.RequestException as exc:
            log.warning(
                "Nominatim failed for %s / %s: %s",
                city_name,
                province_name,
                exc
            )

    raise LookupError(
        f"مختصات شهر «{city_name}» در استان «{province_name}» پیدا نشد. "
        "این شهر در فهرست وجود دارد، اما سرویس نقشه مختصات قابل‌تأیید برنگرداند."
    )


# -------------------- اطلاعات وضعیت هوا --------------------

def weather_description(code):
    try:
        return WEATHER_CODES.get(
            int(code),
            "وضعیت نامشخص"
        )

    except (TypeError, ValueError):
        return "وضعیت نامشخص"


def sky_rating(cloud, rain, wind):
    cloud = cloud if cloud is not None else 100
    rain = rain if rain is not None else 0
    wind = wind if wind is not None else 0

    if rain >= 40 or wind >= 35:
        return "🔴 نامناسب"

    if cloud >= 80 or rain >= 20 or wind >= 25:
        return "🟠 نسبتاً نامناسب"

    if cloud <= 35 and rain < 10 and wind < 20:
        return "🟢 مناسب"

    return "🟡 متوسط"


def format_num(value, digits=1):
    if value is None:
        return "نامشخص"

    return f"{float(value):.{digits}f}"


def get_forecast(city, province, day_offset):
    lat, lon, timezone = get_city_coordinates(
        city,
        province
    )

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "temperature_2m,relative_humidity_2m,cloud_cover,"
            "precipitation_probability,wind_speed_10m,weather_code"
        ),
        "forecast_days": 7,
        "timezone": timezone,
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

    # ساعت هدف ۲۱:۰۰ به وقت محلی؛ اگر موجود نبود نزدیک‌ترین ساعت شب انتخاب می‌شود.
    target_date = (
        datetime.now(ZoneInfo(timezone)).date()
        + timedelta(days=day_offset)
    )

    exact_index = None
    nearest = []

    for i, stamp in enumerate(times):
        try:
            dt = datetime.fromisoformat(stamp)

        except ValueError:
            continue

        if dt.date() == target_date:
            if dt.hour == 21:
                exact_index = i
                break

            if 18 <= dt.hour <= 23:
                nearest.append(
                    (abs(dt.hour - 21), i)
                )

    if exact_index is None:
        if not nearest:
            raise RuntimeError(
                "برای تاریخ انتخاب‌شده پیش‌بینی در دسترس نیست."
            )

        exact_index = sorted(nearest)[0][1]

    def val(key):
        arr = hourly.get(key, [])

        return (
            arr[exact_index]
            if exact_index < len(arr)
            else None
        )

    return {
        "time": times[exact_index],
        "temperature": val("temperature_2m"),
        "humidity": val("relative_humidity_2m"),
        "cloud": val("cloud_cover"),
        "rain": val("precipitation_probability"),
        "wind": val("wind_speed_10m"),
        "code": val("weather_code"),
        "timezone": timezone,
        "latitude": lat,
        "longitude": lon,
    }


# -------------------- رابط کاربری تلگرام --------------------

def keyboard(rows, resize=True):
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=resize
    )


def province_keyboard():
    rows = []

    for i in range(0, len(PROVINCES), 2):
        rows.append([
            KeyboardButton(x)
            for x in PROVINCES[i:i+2]
        ])

    rows.append([
        KeyboardButton("🔄 شروع مجدد")
    ])

    return keyboard(rows)


def city_keyboard(context, page=0):
    cities = context.user_data.get(
        "province_cities",
        []
    )

    start = page * PAGE_SIZE

    page_items = cities[
        start:start + PAGE_SIZE
    ]

    rows = []

    for i in range(0, len(page_items), 2):
        rows.append([
            KeyboardButton(x["name"])
            for x in page_items[i:i+2]
        ])

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
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🌤 به ربات گزارش وضعیت هوا و آسمان شهرهای ایران خوش آمدید.\n\n"
        "ابتدا استان موردنظرتان را انتخاب کنید:",
        reply_markup=province_keyboard()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = normalize_text(
        update.message.text
    )

    if text in (
        "/start",
        "🔄 شروع مجدد"
    ):
        await start(update, context)
        return

    if text in ("🏠 انتخاب استان",):
        context.user_data.clear()

        await update.message.reply_text(
            "استان را انتخاب کنید:",
            reply_markup=province_keyboard()
        )

        return

    if text == "⬅️ صفحه قبل":
        page = max(
            0,
            int(context.user_data.get("city_page", 0)) - 1
        )

        context.user_data["city_page"] = page

        await update.message.reply_text(
            "شهر موردنظر را انتخاب کنید:",
            reply_markup=city_keyboard(context, page)
        )

        return

    if text == "صفحه بعد ➡️":
        page = (
            int(context.user_data.get("city_page", 0))
            + 1
        )

        context.user_data["city_page"] = page

        await update.message.reply_text(
            "شهر موردنظر را انتخاب کنید:",
            reply_markup=city_keyboard(context, page)
        )

        return

    if text in PROVINCES:
        province_cities = sorted(
            [
                c for c in CITIES
                if norm_key(c["province"]) == norm_key(text)
            ],
            key=lambda x: norm_key(x["name"])
        )

        if not province_cities:
            await update.message.reply_text(
                "برای این استان شهری در فهرست پیدا نشد."
            )
            return

        context.user_data.clear()

        context.user_data["province"] = text
        context.user_data["province_cities"] = province_cities
        context.user_data["city_page"] = 0

        await update.message.reply_text(
            f"استان «{text}» انتخاب شد.\n"
            f"تعداد شهرهای فهرست: {len(province_cities)}\n"
            "حالا شهر را انتخاب کنید:",
            reply_markup=city_keyboard(context, 0)
        )

        return

    province = context.user_data.get("province")

    if province and "province_cities" in context.user_data:
        selected = next(
            (
                c for c in context.user_data["province_cities"]
                if norm_key(c["name"]) == norm_key(text)
            ),
            None
        )

        if selected:
            context.user_data["city"] = selected["name"]

            await update.message.reply_text(
                f"شهر «{selected['name']}» از استان «{province}» انتخاب شد.\n"
                "زمان پیش‌بینی را انتخاب کنید:",
                reply_markup=time_keyboard()
            )

            return

    if text in (
        "امشب",
        "فرداشب",
        "۲ شب بعد",
        "۳ شب بعد"
    ):
        city = context.user_data.get("city")
        province = context.user_data.get("province")

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

            date_text = local_dt.strftime("%Y-%m-%d")

            report = (
                "━━━━━━━━━━━━━━━━━━\n"
                "🌌 گزارش وضعیت آسمان\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"📍 استان: {province}\n"
                f"🏙 شهر: {city}\n"
                f"📅 تاریخ: {date_text}\n"
                f"🕘 ساعت: {local_dt.strftime('%H:%M')} (به وقت محلی)\n\n"
                f"🌥 وضعیت هوا: {weather_description(result['code'])}\n"
                f"🌡 دما: {format_num(result['temperature'])} °C\n"
                f"💧 رطوبت: {format_num(result['humidity'], 0)}٪\n"
                f"☁️ پوشش ابر: {format_num(result['cloud'], 0)}٪\n"
                f"🌧 احتمال بارش: {format_num(result['rain'], 0)}٪\n"
                f"💨 سرعت باد: {format_num(result['wind'])} km/h\n\n"
                f"🔭 ارزیابی رصد آسمان: {rating}\n\n"
                "ℹ️ این گزارش پیش‌بینی مدل هواشناسی است و ممکن است تغییر کند."
            )

            await status.edit_text(report)

        except LookupError as exc:
            await status.edit_text(
                "⚠️ برای این شهر مختصات مطمئن پیدا نشد.\n"
                f"{exc}\n\n"
                "برای جلوگیری از گزارش اشتباه، مختصات شهر دیگری انتخاب نشد."
            )

        except requests.RequestException:
            log.exception("Network error")

            await status.edit_text(
                "❌ ارتباط با سرویس هواشناسی برقرار نشد. "
                "کمی بعد دوباره تلاش کنید."
            )

        except Exception:
            log.exception("Forecast error")

            await status.edit_text(
                "❌ هنگام ساخت گزارش خطایی رخ داد. "
                "جزئیات در گزارش اجرای ربات ثبت شد."
            )

        return

    await update.message.reply_text(
        "لطفاً یکی از گزینه‌های کیبورد را انتخاب کنید یا /start را بزنید."
    )


# -------------------- اجرای ربات --------------------

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
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
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
