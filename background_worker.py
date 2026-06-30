"""
Background Worker — многопоточная система для получения и отправки email-ов
=============================================================================

Компоненты:
- EmailQueue: thread-safe очередь — свежие email-ы первыми, старые вытесняются
- FetcherThread: получает email-ы каждые 5 сек, заменяет очередь полностью
- ProfileSenderThread: один независимый поток на каждый профиль Dolphin
- BackgroundWorker: оркестратор, браузеры НЕ закрываются при остановке
"""

import time
import threading
import re
import random
from typing import List, Callable, Optional, Set, Dict
from dataclasses import dataclass
from datetime import datetime
from parser_client import ParserClient


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные типы
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SendResult:
    email: str
    profile_id: str
    success: bool
    error: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# EmailQueue
# ─────────────────────────────────────────────────────────────────────────────

class EmailQueue:
    """
    Thread-safe очередь email-ов — свежие первыми.
    Новые email-ы вставляются в начало очереди и вытесняют самые старые.
    SenderThread всегда работает с наиболее актуальными адресами.
    Поддерживает лимит по возрасту: email-ы старше N минут автоматически удаляются.
    """

    MAX_SIZE = 100  # default, используется только как значение по умолчанию

    def __init__(self, max_size: int = 100, sent_emails: set = None):
        self.max_size = max_size
        self.lock = threading.Lock()
        self.queue: List[str] = []
        self.seen: Set[str] = set()
        self.sent_emails: Set[str] = sent_emails if sent_emails is not None else set()
        self._timestamps: Dict[str, float] = {}   # email → time.time() при добавлении
        self.skipped_count = 0
        self.evicted_count = 0

    def add(self, email: str) -> bool:
        """
        Добавить email в начало очереди (свежие — первыми).
        Если очередь полна — вытесняет самый старый email с конца.

        Returns:
            True если добавлен, False если дубль/уже отправлен
        """
        email = email.strip().lower()

        with self.lock:
            if email in self.sent_emails:
                self.skipped_count += 1
                return False
            if email in self.seen:
                return False
            if len(self.queue) >= self.max_size and self.queue:
                evicted = self.queue.pop()
                self.seen.discard(evicted)
                self._timestamps.pop(evicted, None)
                self.evicted_count += 1
            self.queue.insert(0, email)
            self.seen.add(email)
            self._timestamps[email] = time.time()
            return True

    def add_batch(self, emails: List[str]) -> int:
        """
        Добавить пачку email-ов. Обрабатываем в обратном порядке:
        первый email API-ответа окажется на позиции 0 (самый свежий).

        Returns:
            кол-во добавленных email-ов
        """
        added = 0
        # reversed: последний вставляется первым, значит email[0] == queue[0]
        for email in reversed(emails):
            if self.add(email):
                added += 1
        return added

    def replace_batch(self, emails: List[str]):
        """
        Заменить всю очередь свежей пачкой email-ов.
        """
        with self.lock:
            evicted = len(self.queue)
            for old_email in self.queue:
                self.seen.discard(old_email)
                self._timestamps.pop(old_email, None)
            self.queue.clear()
            self.evicted_count += evicted

            now = time.time()
            added = 0
            for raw_email in emails:
                email = raw_email.strip().lower()
                if email in self.sent_emails:
                    self.skipped_count += 1
                    continue
                if email in self.seen:
                    continue
                self.queue.append(email)
                self.seen.add(email)
                self._timestamps[email] = now
                added += 1

        return added, evicted

    def get_batch(self, count: int) -> List[str]:
        """Взять N email-ов из начала очереди"""
        with self.lock:
            batch = self.queue[:count]
            self.queue = self.queue[count:]
            return batch

    def get_next(self) -> Optional[str]:
        """Взять один email"""
        with self.lock:
            if self.queue:
                email = self.queue.pop(0)
                self._timestamps.pop(email, None)
                return email
            return None

    def size(self) -> int:
        """Текущий размер очереди"""
        with self.lock:
            return len(self.queue)

    def is_full(self) -> bool:
        """Очередь переполнена?"""
        with self.lock:
            return len(self.queue) >= self.max_size

    def get_all(self) -> List[str]:
        """Получить все email-ы (для UI)"""
        with self.lock:
            return self.queue.copy()

    def clear(self):
        """Очистить очередь"""
        with self.lock:
            self.queue.clear()
            self.seen.clear()
            self._timestamps.clear()

    def purge_old(self, max_age_minutes: int) -> int:
        """
        Удалить из очереди email-ы старше max_age_minutes минут.
        0 = отключено.

        Returns:
            Количество удалённых email-ов.
        """
        if max_age_minutes <= 0:
            return 0
        cutoff = time.time() - max_age_minutes * 60
        with self.lock:
            old_emails = [
                e for e in self.queue
                if self._timestamps.get(e, cutoff + 1) <= cutoff
            ]
            for e in old_emails:
                self.queue.remove(e)
                self.seen.discard(e)
                self._timestamps.pop(e, None)
                self.evicted_count += 1
        return len(old_emails)

    def mark_sent(self, email: str):
        """Отметить email как отправленный и сохранить в файл"""
        email = email.lower().strip()
        with self.lock:
            self.sent_emails.add(email)
        try:
            with open("sent_emails.txt", "a", encoding="utf-8") as f:
                f.write(email + "\n")
        except Exception:
            pass

    def __len__(self):
        return self.size()


# ─────────────────────────────────────────────────────────────────────────────
# FetcherThread
# ─────────────────────────────────────────────────────────────────────────────

class FetcherThread(threading.Thread):
    """Фоновый поток для получения email-ов с API парсера (поддержка нескольких площадок)"""

    FETCH_INTERVAL = 5  # секунд между запросами

    def __init__(self,
                 parser_client: ParserClient,
                 email_queue: EmailQueue,
                 platform: str = "",
                 platforms: List[str] = None,
                 service_codes_map: Dict[str, str] = None,
                 on_update: Callable = None,
                 on_metadata_update: Callable = None,
                 filters: Optional[Dict] = None,
                 max_age_minutes: int = 0):
        """
        Args:
            parser_client:      инстанс ParserClient
            email_queue:        инстанс EmailQueue
            platform:           одна платформа (обратная совместимость)
            platforms:          список платформ для round-robin
            service_codes_map:  {platform: service_code} — маппинг для тегирования
            on_update:          callback для лога (msg)
            on_metadata_update: callback(records: List[dict]) — полные данные товара
            filters:            фильтры из UI {country, category, price, ...}
            max_age_minutes:    максимальный возраст email-а в очереди (мин). 0 = отключено.
        """
        super().__init__(daemon=True)
        self.parser_client      = parser_client
        self.email_queue        = email_queue
        # Поддержка и одной платформы, и списка
        if platforms:
            self.platforms = list(platforms)
        elif platform:
            self.platforms = [platform]
        else:
            self.platforms = ["vinted"]
        self._platform_index    = 0
        self.service_codes_map  = service_codes_map or {}
        self.on_update          = on_update or (lambda x: None)
        self.on_metadata_update = on_metadata_update
        self.filters            = filters or {}
        self.max_age_minutes    = max_age_minutes
        self.stop_event         = threading.Event()

    def _next_platform(self) -> str:
        """Получить следующую платформу (round-robin)"""
        platform = self.platforms[self._platform_index % len(self.platforms)]
        self._platform_index += 1
        return platform

    def run(self):
        """
        Основной цикл fetcher-а.
        Чередует площадки round-robin: vinted → subito → vinted → ...
        """
        while not self.stop_event.is_set():
            try:
                # ── Очистка устаревших email-ов ──
                if self.max_age_minutes > 0:
                    purged = self.email_queue.purge_old(self.max_age_minutes)
                    if purged > 0:
                        self.on_update(
                            f"⏰ Удалено {purged} устаревших email-ов "
                            f"(старше {self.max_age_minutes} мин) │ Очередь: {self.email_queue.size()}"
                        )

                # Выбираем следующую площадку
                current_platform = self._next_platform()

                queue_size = self.email_queue.size()
                self.on_update(f"🔄 Запрос к парсеру ({current_platform})... | В очереди: {queue_size}")
                try:
                    records, status = self.parser_client.fetch_with_metadata(
                        current_platform, filters=dict(self.filters)
                    )

                    if records:
                        # Добавляем service_code в каждую запись
                        for r in records:
                            if not r.get("service_code"):
                                # Используем маппинг если есть, иначе конструируем
                                if current_platform in self.service_codes_map:
                                    r["service_code"] = self.service_codes_map[current_platform]
                                else:
                                    country = self.filters.get("country", "").lower()
                                    r["service_code"] = f"{current_platform}_{country}" if country else current_platform

                        emails = [r["email"] for r in records]
                        added = self.email_queue.add_batch(emails)

                        if self.on_metadata_update:
                            self.on_metadata_update(records)

                        self.on_update(
                            f"✅ [{current_platform}] {status} | "
                            f"Новых добавлено: {added}/{len(records)} | "
                            f"В очереди: {self.email_queue.size()}"
                        )
                    else:
                        self.on_update(f"⚠️ [{current_platform}] {status}")

                except ValueError as e:
                    self.on_update(f"❌ [{current_platform}] {str(e)}")
                except RuntimeError as e:
                    self.on_update(f"❌ [{current_platform}] Ошибка подключения: {str(e)}")

                time.sleep(self.FETCH_INTERVAL)

            except Exception as e:
                self.on_update(f"❌ FetcherThread ошибка: {str(e)}")
                time.sleep(1)

    def stop(self):
        """Остановить поток"""
        self.stop_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# ProfileSenderThread
# ─────────────────────────────────────────────────────────────────────────────

class ProfileSenderThread(threading.Thread):
    """
    Независимый поток отправки для одного профиля Dolphin.

    - Не ждёт других профилей — каждый работает самостоятельно
    - При ошибке send_func сам открывает новую чистую вкладку Gmail
    - При серии ошибок делает паузу и продолжает
    - При остановке НЕ закрывает браузер — профиль остаётся открытым
    - Поддерживает pause/resume для отправки ответов
    """

    MAX_CONSECUTIVE_FAILURES = 5  # пауза 15с после стольких ошибок подряд

    def __init__(self, profile_id: str, email_queue: EmailQueue, send_func: Callable,
                 subject: str, body: str, interval_sec: int, on_update: Callable,
                 on_send_result: Callable = None):
        super().__init__(daemon=True)
        self.profile_id          = profile_id
        self.email_queue         = email_queue
        self.send_func           = send_func
        self.subject             = subject
        self.body                = body
        self.interval_sec        = interval_sec
        self.on_update           = on_update
        self.on_send_result      = on_send_result or (lambda r: None)
        self.stop_event          = threading.Event()
        self.pause_event         = threading.Event()   # Запрос на паузу
        self.paused_event        = threading.Event()   # Подтверждение: поток встал
        self.sent_count          = 0
        self.failed_count        = 0
        self._consecutive_failures = 0

    def run(self):
        self.on_update(f"▶ Профиль #{self.profile_id}: начинаю рассылку")

        while not self.stop_event.is_set():

            # ── Точка паузы: если запрошена — останавливаемся и ждём ──
            if self.pause_event.is_set():
                self.on_update(f"⏸ #{self.profile_id}: приостановлен (отправка ответа)")
                self.paused_event.set()  # Подтверждаем: driver свободен
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.1)
                self.paused_event.clear()
                if self.stop_event.is_set():
                    break
                self.on_update(f"▶ #{self.profile_id}: возобновлена рассылка")

            # Проверяем stop раньше чем брать email
            if self.stop_event.is_set():
                break

            # Берём свежий email из очереди
            email = self.email_queue.get_next()
            if not email:
                # Очередь пуста — ждём, но чанками (чтобы stop/pause сработали)
                for _ in range(5):
                    if self.stop_event.is_set() or self.pause_event.is_set():
                        break
                    time.sleep(0.1)
                continue

            # Попытка отправки
            self._try_send(email)

            # Пауза между отправками (разбита на чанки — stop/pause сработают быстро)
            if not self.stop_event.is_set():
                pause = random.uniform(self.interval_sec * 0.7, self.interval_sec * 1.3)
                self.on_update(f"⏳ #{self.profile_id}: пауза {pause:.1f}с")
                deadline = time.time() + pause
                while time.time() < deadline and not self.stop_event.is_set() and not self.pause_event.is_set():
                    time.sleep(0.3)

        self.on_update(f"⏹ Профиль #{self.profile_id}: остановлен")


    def _try_send(self, email: str):
        """
        Отправка одного письма.
        send_func при первой ошибке сам открывает новую чистую вкладку и повторяет.
        """
        try:
            result = self.send_func(email, self.profile_id, self.subject, self.body)
            success = result.get("success", False)
            error = result.get("error", "")

            if success:
                self.email_queue.mark_sent(email)
                self.sent_count += 1
                self._consecutive_failures = 0
                self.on_update(f"✅ #{self.profile_id} → {email}")
            else:
                self.failed_count += 1
                self._consecutive_failures += 1
                self.on_update(f"⚠️ #{self.profile_id}: ошибка — {error}")
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self.on_update(
                        f"⏸ #{self.profile_id}: {self._consecutive_failures} ошибок подряд, "
                        f"пауза 15с..."
                    )
                    deadline = time.time() + 15
                    while time.time() < deadline and not self.stop_event.is_set():
                        time.sleep(0.2)
                    self._consecutive_failures = 0

            self.on_send_result(SendResult(
                email=email,
                profile_id=self.profile_id,
                success=success,
                error=error
            ))

        except Exception as e:
            error = str(e)[:120]
            self.failed_count += 1
            self._consecutive_failures += 1
            self.on_update(f"⚠️ #{self.profile_id}: исключение — {error}")
            self.on_send_result(SendResult(
                email=email,
                profile_id=self.profile_id,
                success=False,
                error=error
            ))
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self.on_update(
                        f"⏸ #{self.profile_id}: {self._consecutive_failures} ошибок подряд, "
                        f"пауза 15с..."
                    )
                    # Пауза чанками — чтобы stop сработал быстро
                    deadline = time.time() + 15
                    while time.time() < deadline and not self.stop_event.is_set():
                        time.sleep(0.2)
                    self._consecutive_failures = 0

    def pause(self, timeout: float = 30) -> bool:
        """
        Приостановить поток и дождаться подтверждения.
        Возвращает True если поток подтвердил паузу, False по таймауту.
        """
        self.pause_event.set()
        return self.paused_event.wait(timeout=timeout)

    def resume(self):
        """Снять паузу — поток продолжит рассылку."""
        self.pause_event.clear()

    def stop(self):
        """Остановить поток (браузер НЕ закрывается)"""
        self.stop_event.set()
        self.pause_event.clear()  # снять паузу чтобы поток мог выйти


# ─────────────────────────────────────────────────────────────────────────────
# BackgroundWorker
# ─────────────────────────────────────────────────────────────────────────────

class BackgroundWorker:
    """
    Оркестратор: FetcherThread (один) + ProfileSenderThread (один на профиль).

    Каждый профиль работает независимо — не ждёт других.
    Браузеры НЕ закрываются при остановке.
    """



    def __init__(self):
        self.email_queue    = EmailQueue()
        self.fetcher_thread: Optional[FetcherThread]          = None
        self.profile_threads: List[ProfileSenderThread]       = []
        self._threads_lock  = threading.Lock()
        self._metadata_lock = threading.Lock()
        self._email_metadata: Dict[str, dict] = {}  # email → данные товара
        self.running = False
        self.stats = {
            "fetched":    0,
            "sent":       0,
            "failed":     0,
            "skipped":    0,
            "start_time": None
        }

    def get_email_metadata(self, email: str) -> dict:
        """Thread-safe получение метаданных для email (используется в send_func)"""
        with self._metadata_lock:
            return dict(self._email_metadata.get(email.lower().strip(), {}))

    def _update_metadata(self, records: List[dict]):
        """FetcherThread вызывает этот callback с полными записями товара"""
        with self._metadata_lock:
            for r in records:
                email = r.get("email", "").lower().strip()
                if email:
                    self._email_metadata[email] = r

    def _trigger_reply_check(self):
        """Запросить проверку ответов — метод временно заглушен (проверка ответов перерабатывается)"""
        pass

    def start(self, parser_client: ParserClient, platform: str = "", profiles: List[str] = None,
              send_func: Callable = None, subject: str = "", body: str = "",
              interval_sec: int = 3, on_update: Callable = None,
              on_stats_update: Callable = None, on_profile_broken: Callable = None,
              sent_emails: set = None,
              max_age_minutes: int = 0,
              filters: Optional[Dict] = None,
              on_sent: Callable = None,
              platforms: List[str] = None,
              service_codes_map: Dict[str, str] = None):
        """
        Запустить FetcherThread + по одному ProfileSenderThread на каждый профиль.
        on_sent(email, profile_id, metadata) — вызывается после каждой успешной отправки.
        platforms — список площадок для чередования (round-robin).
        service_codes_map — {platform: service_code} для тегирования email-ов.
        """
        if self.running:
            if on_update:
                on_update("⚠️ Worker уже запущен")
            return

        self.running = True
        self.stats["start_time"] = datetime.now()
        self.stats["sent"] = 0
        self.stats["failed"] = 0
        self.stats["skipped"] = 0

        # Динамическая очередь (без жёсткого лимита)
        self.email_queue = EmailQueue(max_size=10000, sent_emails=sent_emails)

        def update_wrapper(msg):
            if on_update:
                on_update(msg)

        def send_result_wrapper(result: SendResult):
            if result.success:
                self.stats["sent"] += 1
                # Сохраняем метаданные товара при успешной отправке
                if on_sent:
                    meta = self.get_email_metadata(result.email)
                    try:
                        on_sent(result.email, result.profile_id, meta)
                    except Exception:
                        pass
            else:
                self.stats["failed"] += 1
            if on_stats_update:
                on_stats_update(self.stats)

        # Определяем список площадок
        platform_list = platforms or ([platform] if platform else ["vinted"])

        # FetcherThread — получает свежие email и метаданные товара каждые 5 сек
        self.fetcher_thread = FetcherThread(
            parser_client=parser_client,
            email_queue=self.email_queue,
            platforms=platform_list,
            service_codes_map=service_codes_map or {},
            on_update=update_wrapper,
            on_metadata_update=self._update_metadata,
            filters=filters,
            max_age_minutes=max_age_minutes
        )
        self.fetcher_thread.start()

        # ProfileSenderThread — один на каждый профиль, независимый
        with self._threads_lock:
            self.profile_threads = []
            for profile_id in profiles:
                t = ProfileSenderThread(
                    profile_id=profile_id,
                    email_queue=self.email_queue,
                    send_func=send_func,
                    subject=subject,
                    body=body,
                    interval_sec=interval_sec,
                    on_update=update_wrapper,
                    on_send_result=send_result_wrapper,
                )
                t.start()
                self.profile_threads.append(t)

        if on_update:
            on_update(f"✅ Worker запущен! {len(profiles)} профилей работают независимо")


    def stop(self, on_update: Callable = None):
        """
        Остановить все потоки.
        Браузеры Dolphin НЕ закрываются — профили остаются открытыми.
        """
        if not self.running:
            return

        if on_update:
            on_update("⏹ Остановка рассылки...")

        if self.fetcher_thread:
            self.fetcher_thread.stop()
            self.fetcher_thread.join(timeout=5)

        with self._threads_lock:
            threads_to_stop = list(self.profile_threads)

        # Отправляем сигнал всем потокам одновременно
        for t in threads_to_stop:
            t.stop()

        # Ждём завершения всех потоков параллельно
        # timeout=8 — достаточно чтобы завершить текущую отправку
        join_threads = []
        for t in threads_to_stop:
            jt = threading.Thread(target=t.join, kwargs={"timeout": 8}, daemon=True)
            jt.start()
            join_threads.append(jt)
        for jt in join_threads:
            jt.join()  # Ждём все join-ы одновременно

        with self._threads_lock:
            self.profile_threads = []

        self.running = False

        if on_update:
            on_update("✅ Рассылка остановлена. Браузеры открыты.")

    def get_profile_thread(self, profile_id: str) -> Optional[ProfileSenderThread]:
        """Найти ProfileSenderThread по profile_id."""
        with self._threads_lock:
            for t in self.profile_threads:
                if t.profile_id == str(profile_id):
                    return t
        return None

    def pause_profile(self, profile_id: str, timeout: float = 30) -> bool:
        """
        Приостановить рассылку для конкретного профиля.
        Ждёт завершения текущей отправки.
        Возвращает True если профиль приостановлен.
        """
        thread = self.get_profile_thread(profile_id)
        if not thread or not thread.is_alive():
            return True  # поток не запущен — driver свободен
        return thread.pause(timeout=timeout)

    def resume_profile(self, profile_id: str):
        """Снять паузу с профиля — рассылка продолжится."""
        thread = self.get_profile_thread(profile_id)
        if thread:
            thread.resume()

    def get_queue_snapshot(self) -> List[str]:
        return self.email_queue.get_all()

    def get_stats(self) -> dict:
        stats = self.stats.copy()
        stats["skipped"] = self.email_queue.skipped_count
        if stats["start_time"]:
            elapsed = (datetime.now() - stats["start_time"]).total_seconds()
            stats["elapsed_time"] = self._format_time(elapsed)
        return stats

    def get_broken_profiles(self) -> set:
        """Профили не помечаются как нерабочие — все всегда активны."""
        return set()

    @staticmethod
    def _format_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
