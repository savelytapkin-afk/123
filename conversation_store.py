"""
ConversationStore — хранилище диалогов
=======================================

Сохраняет:
- Email получателя и URL треда после отправки
- Текст ответа получателя (если пришёл через Inbox)
- Статус: ответили ли мы уже обратно HTML-ответом
"""

import json
import threading
from datetime import datetime
from typing import List, Dict, Optional


class ConversationStore:
    """Thread-safe хранилище диалогов в conversations.json"""

    FILE = "conversations.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = self._load()

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            with open(self.FILE, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        """Сохранить на диск (вызывать только внутри self._lock)"""
        try:
            with open(self.FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Запись
    # ─────────────────────────────────────────────────────────────────────────

    def save_sent(self, email: str, profile_id: str, subject: str,
                  body: str, thread_url: str,
                  product_name: str = "", price: str = "",
                  photo: str = "", seller_name: str = "",
                  address: str = "-", ad_url: str = "",
                  service_code: str = "", platform: str = ""):
        """
        Сохранить отправленное письмо с URL треда и метаданными товара.
        Вызывается сразу после успешной отправки.
        """
        email = email.lower().strip()
        with self._lock:
            if email in self._data and self._data[email].get("reply_text"):
                return
            self._data[email] = {
                "profile_id":    str(profile_id),
                "subject":       subject,
                "sent_body":     body,
                "sent_at":       datetime.now().isoformat(),
                "thread_url":    thread_url,
                # Метаданные товара
                "product_name":  product_name,
                "price":         price,
                "photo":         photo,
                "seller_name":   seller_name,
                "address":       address or "-",
                "ad_url":        ad_url,
                "service_code":  service_code,
                "platform":      platform,
                # Статус диалога
                "reply_text":    None,
                "reply_time":    None,
                "replied_back":  False,
                "last_checked":  None,
            }
            self._save()

    def save_reply(self, email: str, reply_text: str):
        """Сохранить текст ответа от получателя"""
        email = email.lower().strip()
        with self._lock:
            if email in self._data:
                self._data[email]["reply_text"]   = reply_text
                self._data[email]["reply_time"]   = datetime.now().isoformat()
                self._data[email]["last_checked"] = datetime.now().isoformat()
                self._save()

    def mark_replied_back(self, email: str):
        """Отметить что мы уже отправили HTML-ответ"""
        email = email.lower().strip()
        with self._lock:
            if email in self._data:
                self._data[email]["replied_back"]  = True
                self._data[email]["last_checked"] = datetime.now().isoformat()
                self._save()

    def mark_checked(self, email: str):
        """Обновить время последней проверки (ответа не было)"""
        email = email.lower().strip()
        with self._lock:
            if email in self._data:
                self._data[email]["last_checked"] = datetime.now().isoformat()
                self._save()

    # ─────────────────────────────────────────────────────────────────────────
    # Чтение
    # ─────────────────────────────────────────────────────────────────────────

    def get_unchecked_for_profile(self, profile_id: str) -> List[dict]:
        """
        Вернуть диалоги профиля, которые ещё не ответили обратно.
        (Inbox-режим: наличие thread_url не обязательно)
        """
        profile_id = str(profile_id)
        with self._lock:
            result = []
            for email, data in self._data.items():
                if data.get("profile_id") != profile_id:
                    continue
                if data.get("replied_back"):
                    continue  # уже ответили — пропустить
                result.append({"email": email, **data})
            return result

    def get(self, email: str) -> Optional[dict]:
        """Найти запись по email. Gmail игнорирует точки — a.b@gmail = ab@gmail."""
        email = email.lower().strip()
        with self._lock:
            # Точное совпадение
            if email in self._data:
                return dict(self._data[email])

            # Нормализация Gmail: убираем точки из username
            normalized = self._normalize_gmail(email)
            for stored_email, data in self._data.items():
                if self._normalize_gmail(stored_email) == normalized:
                    return dict(data)

            # Fallback: по username (до @)
            user = email.split("@")[0].replace(".", "")
            for stored_email, data in self._data.items():
                if stored_email.split("@")[0].replace(".", "") == user:
                    return dict(data)
        return None

    @staticmethod
    def _normalize_gmail(email: str) -> str:
        """Нормализовать Gmail: убрать точки из username, lowercase."""
        parts = email.lower().strip().split("@")
        if len(parts) == 2 and parts[1] in ("gmail.com", "googlemail.com"):
            parts[0] = parts[0].replace(".", "")
        return "@".join(parts)

    def get_all(self) -> dict:
        """Получить все диалоги (для UI)"""
        with self._lock:
            return dict(self._data)

    # ─────────────────────────────────────────────────────────────────────────
    # Статистика
    # ─────────────────────────────────────────────────────────────────────────

    def count_total(self) -> int:
        with self._lock:
            return len(self._data)

    def count_with_reply(self) -> int:
        with self._lock:
            return sum(1 for d in self._data.values() if d.get("reply_text"))

    def count_replied_back(self) -> int:
        with self._lock:
            return sum(1 for d in self._data.values() if d.get("replied_back"))
