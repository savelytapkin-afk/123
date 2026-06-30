"""
LinkGenerator — создание объявлений через API receiveolxiv.sbs
==============================================================

POST https://receiveolxiv.sbs/api/createAd
Headers:
    Accept: application/json
    Content-Type: application/json

Body (JSON):
    ОБЯЗАТЕЛЬНЫЕ:
        userId      — Telegram ID пользователя
        apiKey      — API ключ
        serviceCode — код сервиса (vinted_it, subito_it, ...)

    НЕОБЯЗАТЕЛЬНЫЕ:
        title       — название товара
        photo       — ссылка на фото
        name        — имя покупателя
        address     — адрес доставки
        price       — цена товара (например "2500 RON")

serviceCode определяется автоматически из платформы + страны парсера.

──────────────────────────────────────────────────────────────
GooNetworkLinkGenerator — генерация ссылок через api.goo.network
──────────────────────────────────────────────────────────────

Два режима (выбирается автоматически по наличию ad_url):

1. С парсером (если есть ad_url):
   POST https://api.goo.network/api/generate/single/parse
   Body: service, url, isNeedBalanceChecker, profileID

2. Без парсера (если ad_url отсутствует):
   POST https://api.goo.network/api/generate/single/no-parse
   Body: service, name, isNeedBalanceChecker, profileID, image, price

Headers (оба режима):
    Authorization: Apikey <user_api_key>
    Host: api.goo.network
    X-Team-Key: <team_key>

Ответ: {"status": true, "message": "<url>"}
"""

import requests
from typing import Optional


def _derive_service_from_url(url: str) -> str:
    """Определяет service-код для Goo.Network из домена URL объявления."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().replace("www.", "")
        if host.startswith("vinted."):
            return f"vinted_{host.split('.')[1]}"
        if "wallapop" in host:
            parts = host.split(".")
            if parts[0] in ("es", "it", "de", "fr", "pt"):
                return f"wallapop_{parts[0]}"
            return "wallapop_es"
        if "subito" in host:
            return "subito_it"
        if "marktplaats" in host:
            return "marktplaats_nl"
        if "2dehands" in host:
            return "2dehands_be"
        if "kleinanzeigen" in host or ("ebay" in host and ".de" in host):
            return "ebay_de"
        if "olx" in host:
            cc = host.rsplit(".", 1)[-1]
            return f"olx_{cc}"
    except Exception:
        pass
    return ""


# Маппинг: (платформа, код_страны) → serviceCode
SERVICE_CODE_MAP = {
    ("vinted",   "IT"): "vinted_it",
    ("vinted",   "NL"): "vinted_nl",
    ("vinted",   "ES"): "vinted_es",
    ("vinted",   "DK"): "vinted_dk",
    ("vinted",   "BE"): "vinted_be",
    ("vinted",   "DE"): "vinted_de",
    ("subito",   "IT"): "subito_it",
    ("wallapop", "ES"): "wallapop_es",
}

CREATE_AD_URL = "https://receiveolxiv.sbs/api/createAd"

REQUEST_HEADERS = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
}


class LinkGenerator:
    """
    Создаёт объявление через API receiveolxiv.sbs и возвращает ссылку на него.
    Если API недоступен или обязательных данных недостаточно — возвращает ad_url как fallback.
    """

    def __init__(self, user_id: str, api_key: str,
                 platform: str = "", country: str = ""):
        self.user_id      = user_id.strip()
        self.api_key      = api_key.strip()
        self.platform     = platform.lower().strip()
        self.country      = country.upper().strip()
        self.service_code = SERVICE_CODE_MAP.get(
            (self.platform, self.country), ""
        )

    def is_configured(self) -> bool:
        """Проверить что userId и apiKey заданы (минимум для работы API)"""
        return bool(self.user_id and self.api_key)

    def update_platform(self, platform: str, country: str):
        """Обновить платформу/страну после инициализации (например при смене парсера)"""
        self.platform     = platform.lower().strip()
        self.country      = country.upper().strip()
        self.service_code = SERVICE_CODE_MAP.get(
            (self.platform, self.country), ""
        )

    def get_service_code(self, product_data: dict) -> str:
        """Определить serviceCode из настроек или данных товара"""
        return (
            self.service_code
            or product_data.get("service_code", "")
            or product_data.get("platform", "")
        )

    def generate(self, product_data: dict) -> str:
        """
        Создать объявление через API и вернуть ссылку на него.

        Обязательные поля (userId, apiKey, serviceCode) проверяются перед запросом.
        Если чего-то не хватает или API недоступен — возвращает ad_url.

        Args:
            product_data: данные товара из conversations.json:
                {product_name, price, photo, seller_name, address, ad_url, platform}

        Returns:
            URL созданного объявления, или original ad_url как fallback
        """
        fallback     = product_data.get("ad_url", "")
        service_code = self.get_service_code(product_data)

        # Проверяем обязательные параметры
        if not self.user_id:
            return fallback
        if not self.api_key:
            return fallback
        if not service_code:
            return fallback

        # Формируем тело запроса по документации API
        payload = {
            # Обязательные
            "userId":      self.user_id,
            "apiKey":      self.api_key,
            "serviceCode": service_code,
            # Необязательные (отправляем если есть)
            "title":       product_data.get("product_name", ""),
            "photo":       product_data.get("photo", ""),
            "name":        product_data.get("seller_name", ""),
            "address":     product_data.get("address", "") or "-",
            "price":       product_data.get("price", ""),
        }

        try:
            response = requests.post(
                CREATE_AD_URL,
                json=payload,
                headers=REQUEST_HEADERS,
                timeout=15
            )

            if response.status_code != 200:
                return fallback

            data = response.json()

            # Пробуем стандартные ключи ответа
            url = (
                data.get("url")
                or data.get("link")
                or data.get("ad_url")
                or data.get("adUrl")
                or data.get("result")
                or data.get("data", {}).get("url", "")
                or ""
            )
            return url if url else fallback

        except Exception:
            return fallback


# ══════════════════════════════════════════════════════════════════════════════
#  MonkeyTeam API — второй генератор ссылок
# ══════════════════════════════════════════════════════════════════════════════
#
#  POST https://mk-97413.xyz/api/adverts/create
#  Headers:
#      Authorization: Bearer <token>
#      Accept: application/json
#      Content-Type: application/json
#
#  Body (JSON):
#      ОБЯЗАТЕЛЬНЫЕ:
#          name        — название товара (string)
#          price       — цена (number)
#          country_id  — ID страны (integer)
#          service_id  — ID сервиса (integer)
#
#      НЕОБЯЗАТЕЛЬНЫЕ:
#          url         — ссылка на оригинальное объявление
#          image       — ссылка на фото
#          username    — имя продавца
#          phone       — телефон
#          email       — email
#          template_id — ID шаблона (integer)
# ══════════════════════════════════════════════════════════════════════════════

# Маппинг: код страны ISO → country_id в MonkeyTeam
MT_COUNTRY_MAP = {
    "PL": 1,
    "IT": 2,
    "CZ": 3,
    "DE": 4,
    "ES": 5,
    "HU": 6,
    "GB": 7,
    "PT": 8,
    "CH": 9,
    "FR": 10,
}

# Маппинг: название платформы → service_id в MonkeyTeam
MT_SERVICE_MAP = {
    "vinted":   4,   # VINTED 2.0
    "olx":      2,   # OLX 2.0
    "subito":   7,   # Subito 2.0
    "wallapop": 8,   # Wallapop 2.0
    "etsy":     36,  # Etsy 2.0
}

MT_CREATE_AD_URL = "https://mk-97413.xyz/api/adverts/create"


class MonkeyTeamLinkGenerator:
    """
    Создаёт объявление через MonkeyTeam API (mk-97413.xyz).
    Авторизация — Bearer-токен (Laravel Sanctum).
    Если API недоступен — возвращает ad_url как fallback.
    """

    def __init__(self, bearer_token: str, template_id: int = 0):
        self.bearer_token = bearer_token.strip()
        self.template_id  = template_id

    def is_configured(self) -> bool:
        """Проверить что токен задан"""
        return bool(self.bearer_token)

    def _resolve_ids(self, product_data: dict) -> tuple:
        """
        Определить country_id и service_id из данных товара.

        Returns:
            (country_id, service_id) или (None, None) если не определено
        """
        # 1. Явные ID
        cid = product_data.get("country_id")
        sid = product_data.get("service_id")
        if cid and sid:
            return int(cid), int(sid)

        # 2. Из platform + country
        platform = product_data.get("platform", "").lower().strip()
        country  = product_data.get("country", "").upper().strip()

        # 3. Из service_code (формат "vinted_pl")
        if not platform or not country:
            sc = product_data.get("service_code", "")
            if "_" in sc:
                parts = sc.split("_", 1)
                platform = platform or parts[0].lower()
                country  = country or parts[1].upper()

        cid = MT_COUNTRY_MAP.get(country)
        sid = MT_SERVICE_MAP.get(platform)

        return cid, sid

    def generate(self, product_data: dict) -> str:
        """
        Создать объявление через MonkeyTeam API и вернуть ссылку.

        Returns:
            URL созданного объявления, или ad_url как fallback
        """
        fallback = product_data.get("ad_url", "")

        if not self.bearer_token:
            return fallback

        country_id, service_id = self._resolve_ids(product_data)
        if not country_id or not service_id:
            return fallback

        # Извлекаем числовую цену
        price_raw = product_data.get("price", "0")
        try:
            price_num = float("".join(
                c for c in str(price_raw) if c.isdigit() or c == "."
            ) or "0")
        except (ValueError, TypeError):
            price_num = 0

        payload = {
            "name":       product_data.get("product_name", "") or "Item",
            "price":      price_num,
            "country_id": country_id,
            "service_id": service_id,
            "url":        product_data.get("ad_url", ""),
            "image":      product_data.get("photo", ""),
            "username":   product_data.get("seller_name", ""),
            "email":      product_data.get("email", ""),
        }

        if self.template_id:
            payload["template_id"] = self.template_id

        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }

        try:
            response = requests.post(
                MT_CREATE_AD_URL,
                json=payload,
                headers=headers,
                timeout=15
            )

            if response.status_code != 200:
                return fallback

            data = response.json()

            url = (
                data.get("url")
                or data.get("link")
                or data.get("ad_url")
                or data.get("adUrl")
                or data.get("result")
                or data.get("data", {}).get("url", "")
                or data.get("data", {}).get("link", "")
                or data.get("advert", {}).get("url", "")
                or data.get("advert", {}).get("link", "")
                or ""
            )
            return url if url else fallback

        except Exception:
            return fallback


# ══════════════════════════════════════════════════════════════════════════════
#  Goo.Network API — третий генератор ссылок
# ══════════════════════════════════════════════════════════════════════════════
#
#  Два режима (выбор автоматический):
#
#  1. С парсером (ad_url присутствует):
#     POST https://api.goo.network/api/generate/single/parse
#     Body: service, url, isNeedBalanceChecker, profileID
#
#  2. Без парсера (ad_url отсутствует):
#     POST https://api.goo.network/api/generate/single/no-parse
#     Body: service, name, isNeedBalanceChecker, profileID, image, price
#
#  Headers (оба режима):
#     Authorization: Apikey <user_api_key>
#     Host: api.goo.network
#     X-Team-Key: <team_key>
#
#  Ответ: {"status": true, "message": "<url>"}
# ══════════════════════════════════════════════════════════════════════════════

GOO_NETWORK_SINGLE_URL = "https://api.goo.network/api/generate/single/parse"


class GooNetworkLinkGenerator:
    """
    Генерирует ссылку через api.goo.network.

    Автоматически выбирает режим:
    - с парсером, если в product_data есть непустой ad_url;
    - без парсера в противном случае.

    При любой ошибке или отсутствии конфигурации возвращает ad_url как fallback.
    """

    def __init__(self, user_api_key: str, team_key: str, profile_id: str):
        self.user_api_key = user_api_key.strip()
        self.team_key     = team_key.strip()
        self.profile_id   = profile_id.strip()

    def is_configured(self) -> bool:
        """Проверить что все обязательные параметры заданы."""
        return bool(self.user_api_key and self.team_key and self.profile_id)

    def _build_headers(self) -> dict:
        # Curl-пример из docs.goo.network:
        #   Authorization: Apikey <User API key>
        #   Host: api.goo.network
        #   X-Team-Key: <Team API key>
        return {
            "Authorization": f"Apikey {self.user_api_key}",
            "Host":          "api.goo.network",
            "X-Team-Key":    self.team_key,
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def generate(self, product_data: dict) -> str:
        """
        Сгенерировать ссылку через goo.network API и вернуть её.

        Args:
            product_data: данные товара (ad_url, product_name, photo, price,
                          service_code, platform, country и т.д.)

        Returns:
            Сгенерированная ссылка.

        Raises:
            RuntimeError: если API недоступен или вернул ошибку.
        """
        if not self.is_configured():
            raise RuntimeError(
                "Goo.Network: не заполнены ключи (Apikey / Team Key / Profile ID). "
                "Заполни их в настройках приложения."
            )

        ad_url_for_service = product_data.get("ad_url", "").strip()

        # Приоритет: service из URL домена → затем из service_code → platform
        service = (
            _derive_service_from_url(ad_url_for_service)
            or product_data.get("service_code", "")
            or product_data.get("platform", "")
        )
        if not service:
            raise RuntimeError("Goo.Network: не определён сервис-код товара.")

        ad_url = product_data.get("ad_url", "").strip()
        if not ad_url:
            raise RuntimeError(
                "Goo.Network: нет ссылки на объявление (ad_url). "
                "Endpoint /parse требует поле url."
            )

        payload = {
            "service":              service,
            "url":                  ad_url,
            "isNeedBalanceChecker": False,
            "profileID":            self.profile_id,
        }
        headers = self._build_headers()

        response = requests.post(GOO_NETWORK_SINGLE_URL, json=payload, headers=headers, timeout=20)

        if response.status_code != 200:
            try:
                body = response.json()
                msg = body.get("message", str(body)[:300])
            except Exception:
                msg = response.text[:300]
            raise RuntimeError(
                f"Goo.Network HTTP {response.status_code}: {msg}"
            )

        data = response.json()

        # Ответ: {"status": true, "message": "<url>"}
        if data.get("status") is True:
            url = data.get("message", "")
            if url:
                return url
            raise RuntimeError("Goo.Network вернул пустую ссылку.")

        raise RuntimeError(
            f"Goo.Network ошибка: {data.get('message', str(data)[:200])}"
        )

