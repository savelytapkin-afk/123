"""
Parser Client — работа с API vvsproject.xyz
==========================================

API документация:
- Endpoint: GET http://vvsproject.xyz/ads/{platform}
- Auth: api-key header
- Параметр email: true → получить только продавцов с валидным Gmail
- Rate limit: 1 запрос в 5 секунд
- Ответ: словарь с ID ключами {"1": {...}, "2": {...}}
"""

import re
import time
import requests
import json
from typing import List, Dict, Optional, Tuple


class ParserClient:
    """Клиент для получения email-ов с API парсера vvsproject.xyz"""
    
    API_URL = "http://vvsproject.xyz/ads"
    EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    MAX_RETRIES = 3
    TIMEOUT = 15
    
    def __init__(self, api_key: str):
        """
        Args:
            api_key: API ключ для авторизации
        """
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ValueError("API key cannot be empty")
        
        self.session = requests.Session()
        self.session.headers.update({
            "api-key": self.api_key,
            "User-Agent": "Gmail-Sender-Bot/1.0"
        })
    
    def fetch_emails(self, platform: str, filters: Optional[Dict] = None) -> tuple[List[str], str]:
        """
        Получить email-ы с API парсера
        
        Args:
            platform: платформа (vinted, 2dehands и т.д.)
            filters: доп. фильтры {country: "DE", price: "1..100", ...}
        
        Returns:
            (список уникальных валидных email-ов, сообщение статуса)
        
        Raises:
            ValueError: если API ключ неверный или платформа не найдена
            RuntimeError: если сетевая ошибка после всех retry
        """
        
        if not platform or not platform.strip():
            raise ValueError("Platform cannot be empty")
        
        platform = platform.strip().lower()
        
        # Подготовка параметров запроса (копируем чтобы не мутировать оригинал)
        params = dict(filters) if filters else {}
        params["limit"] = params.get("limit", 100)  # Максимум результатов
        params["email"] = True  # ← ГЛАВНОЕ: только продавцы с email!
        
        endpoint = f"{self.API_URL}/{platform}"
        
        print(f"\n📨 Отправляем запрос на {platform} с параметрами: {params}")
        
        # Retry logic
        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    endpoint,
                    params=params,
                    timeout=self.TIMEOUT
                )
                
                # Обработка HTTP ошибок
                if response.status_code == 401:
                    raise ValueError("❌ API ошибка 401: неверный api-key")
                elif response.status_code == 403:
                    raise ValueError("❌ API ошибка 403: доступ запрещен")
                elif response.status_code == 404:
                    raise ValueError(f"❌ API ошибка 404: платформа '{platform}' не найдена")
                elif response.status_code == 422:
                    raise ValueError("❌ API ошибка 422: api-key не передан в headers")
                elif response.status_code == 429:
                    # Rate limit — вернуть пустой список, не ломать
                    return [], f"⏸ Rate limit: превышен лимит 1 запрос/5 сек (попытка {attempt}/{self.MAX_RETRIES})"
                elif response.status_code == 402:
                    raise ValueError("❌ API ошибка 402: нет подписки")
                elif response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
                
                # Парсинг JSON
                try:
                    data = response.json()
                except requests.exceptions.JSONDecodeError:
                    raise RuntimeError(f"Invalid JSON response: {response.text[:200]}")
                
                # DEBUG: Выводим структуру ответа
                print(f"\n=== DEBUG API RESPONSE ===")
                print(f"Response type: {type(data)}")
                print(f"Total items: {len(data) if isinstance(data, dict) else 'N/A'}")
                print(f"Response keys: {list(data.keys())[:10] if isinstance(data, dict) else 'N/A'}")
                
                if isinstance(data, dict) and data:
                    first_key = list(data.keys())[0]
                    print(f"\nFirst item ({first_key}):")
                    print(json.dumps(data[first_key], ensure_ascii=False, indent=2))
                
                print(f"=== END DEBUG ===\n")
                
                # Извлечение email-ов
                emails = self._extract_emails(data)
                
                status = f"✅ Получено {len(emails)} email-ов"
                if not emails:
                    status = "⚠️ Ответ получен, но email-ов не найдено"
                
                return emails, status
                
            except requests.exceptions.Timeout:
                last_error = f"Timeout (попытка {attempt}/{self.MAX_RETRIES})"
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {str(e)[:50]} (попытка {attempt}/{self.MAX_RETRIES})"
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)
            except (ValueError, RuntimeError) as e:
                # Критические ошибки — не retry
                raise
        
        # Если все retry исчерпаны
        raise RuntimeError(f"❌ Не удалось подключиться после {self.MAX_RETRIES} попыток. Последняя ошибка: {last_error}")
    
    def fetch_with_metadata(self, platform: str,
                             filters: Optional[Dict] = None
                             ) -> Tuple[List[dict], str]:
        """
        Получить данные из парсера вместе с метаданными товара.

        Returns:
            (records, status)
            records: List[dict] — [{email, product_name, price, photo,
                                      seller_name, address, ad_url, service_code}]
            status:  str — строка статуса

        Raises:
            ValueError, RuntimeError — аналогично fetch_emails()
        """
        if not platform or not platform.strip():
            raise ValueError("Platform cannot be empty")

        platform = platform.strip().lower()
        params = dict(filters) if filters else {}
        params["limit"] = params.get("limit", 100)
        params["email"] = True

        endpoint = f"{self.API_URL}/{platform}"

        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    endpoint, params=params, timeout=self.TIMEOUT
                )
                if response.status_code == 401:
                    raise ValueError("❌ API ошибка 401: неверный api-key")
                elif response.status_code == 403:
                    raise ValueError("❌ API ошибка 403: доступ запрещен")
                elif response.status_code == 404:
                    raise ValueError(f"❌ API ошибка 404: платформа '{platform}' не найдена")
                elif response.status_code == 429:
                    return [], f"⏸ Rate limit (attempt {attempt}/{self.MAX_RETRIES})"
                elif response.status_code == 402:
                    raise ValueError("❌ API ошибка 402: нет подписки")
                elif response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")

                try:
                    data = response.json()
                except Exception:
                    raise RuntimeError("Invalid JSON response")

                # Извлечь полные записи с метаданными
                records = self._extract_with_metadata(data, platform)
                status = f"✅ Получено {len(records)} записей"
                if not records:
                    status = "⚠️ Ответ получен, но email-ов не найдено"
                return records, status

            except requests.exceptions.Timeout:
                last_error = f"Timeout ({attempt}/{self.MAX_RETRIES})"
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error ({attempt}/{self.MAX_RETRIES})"
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)
            except (ValueError, RuntimeError):
                raise

        raise RuntimeError(
            f"❌ Не удалось подключиться. Ошибка: {last_error}"
        )

    def _extract_emails(self, api_response: Dict) -> List[str]:
        """Извлечь список email-ов (обратная совместимость)"""
        records = self._extract_with_metadata(api_response)
        return [r["email"] for r in records]

    def _extract_with_metadata(self, api_response: Dict,
                                platform: str = "") -> List[dict]:
        """
        Извлечь полные записи с метаданными товара.

        Returns:
            [{email, product_name, price, photo, seller_name, address,
              ad_url, service_code}]
        """
        results = []
        seen_emails = set()

        if not isinstance(api_response, dict):
            return []

        for item_id, item_data in api_response.items():
            if not isinstance(item_data, dict):
                continue

            # ── Email ──
            email = None
            for field in ["email", "contact_email", "seller_email", "mail", "почта", "e-mail"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip() and self._is_valid_email(val.strip()):
                    email = val.strip().lower()
                    break

            if not email:
                for field in ["seller", "title", "description", "seller_url", "ad_url",
                               "chat_url", "contact", "phone"]:
                    text = str(item_data.get(field, ""))
                    found = re.findall(self.EMAIL_REGEX, text)
                    for fe in found:
                        if self._is_valid_email(fe):
                            email = fe.lower()
                            break
                    if email:
                        break

            if not email or email in seen_emails:
                continue
            seen_emails.add(email)

            # ── Название товара ──
            product_name = ""
            for field in ["title", "name", "product_name", "ad_title",
                          "item_title", "subject"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip():
                    product_name = val.strip()
                    break

            # ── Цена ──
            price = ""
            for field in ["price", "price_amount", "cost", "amount"]:
                val = item_data.get(field, "")
                if val not in ("", None):
                    price_str = str(val).strip()
                    # Добавить валюту если есть
                    currency = item_data.get("price_currency", "") or ""
                    price = f"{price_str} {currency}".strip() if currency else price_str
                    break

            # ── Фото ──
            photo = ""
            for field in ["photo", "image", "photo_url", "image_url",
                          "thumbnail", "picture"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip():
                    photo = val.strip()
                    break
                elif isinstance(val, list) and val:
                    photo = str(val[0]).strip()
                    break

            # ── Имя продавца ──
            seller_name = ""
            for field in ["seller_name", "seller", "username", "user",
                          "owner", "author"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip():
                    seller_name = val.strip()
                    break
                elif isinstance(val, dict):
                    seller_name = (val.get("name") or val.get("username") or "").strip()
                    if seller_name:
                        break

            # ── Адрес / локация ──
            address = "-"
            for field in ["location", "address", "city", "region",
                          "country", "place"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip():
                    address = val.strip()
                    break

            # ── Ссылка на объявление ──
            ad_url = ""
            for field in ["ad_url", "url", "link", "chat_url",
                          "seller_url", "item_url"]:
                val = item_data.get(field, "")
                if isinstance(val, str) and val.strip() and val.startswith("http"):
                    ad_url = val.strip()
                    break

            results.append({
                "email":        email,
                "product_name": product_name,
                "price":        price,
                "photo":        photo,
                "seller_name":  seller_name,
                "address":      address,
                "ad_url":       ad_url,
                "platform":     platform,
            })

        return results

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """Простая валидация email"""
        if not isinstance(email, str):
            return False
        
        email = email.strip()
        
        # Базовые проверки
        if len(email) < 5 or len(email) > 254:
            return False
        
        if not re.match(ParserClient.EMAIL_REGEX, email):
            return False
        
        # Исключить очевидные спам email-ы
        spam_keywords = ["noreply", "no-reply", "donotreply", "test", "fake", "example", "placeholder"]
        if any(keyword in email.lower() for keyword in spam_keywords):
            return False
        
        return True
    
    def __del__(self):
        """Закрыть сессию при удалении объекта"""
        if hasattr(self, 'session'):
            self.session.close()


# Тестирование (можешь запустить: python parser_client.py)
if __name__ == "__main__":
    # ⚠️ Замени на свой API ключ для тестирования
    API_KEY = "your_api_key_here"
    
    try:
        client = ParserClient(API_KEY)
        emails, status = client.fetch_emails("vinted", filters={"country": "DE", "limit": 5})
        print(f"Status: {status}")
        print(f"Emails: {emails}")
    except Exception as e:
        print(f"Error: {e}")
