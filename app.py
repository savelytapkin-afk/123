"""
Gmail Auto Sender + Parser Integration — GUI версия
====================================================
Запуск:  python app.py
Зависимости: pip install selenium requests customtkinter

Оптимизация:
- Минимальные таймауты (2 сек вместо 60)
- Ускоренные sleep (0.1-0.3 сек вместо 3-6)
- Параллельная отправка на все профили
- Fast-path селекторы
- DEBUG режим с HTML выводом при ошибках
- Умная проверка открыта ли форма письма перед кликом
- Клик на контейнер перед заполнением полей
- Улучшенные селекторы для кнопки "Compose"
"""

import re
import time
import json
import random
import threading
import requests
import customtkinter as ctk
from tkinter import messagebox, filedialog
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from datetime import datetime
from urllib.parse import urlparse
import os

# Импорт наших модулей
from parser_client import ParserClient
from background_worker import BackgroundWorker
from html_template_editor import HtmlTemplateEditor, get_template
try:
    from PIL import Image as PilImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── Тема ──────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

DOLPHIN_API = "http://localhost:3001/v1.0"
DEBUG = True  # ← РЕЖИМ DEBUG
DEBUG_DIR = "debug_logs"

def _is_gmail_tab(url: str) -> bool:
    """Безопасная проверка URL на принадлежность к mail.google.com"""
    try:
        parsed = urlparse(url)
        return parsed.netloc == "mail.google.com" or parsed.netloc.endswith(".mail.google.com")
    except Exception:
        return False

# Создаём папку для дебага если её нет
if DEBUG and not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

# Площадки и их параметры
PLATFORMS = {
    "vinted": "Vinted",
    "2dehands": "2dehands",
    "subito": "Subito",
    "etsy": "Etsy",
    "mercari": "Mercari",
    "depop": "Depop",
    "grailed": "Grailed",
    "carousell": "Carousell",
    "fiverr": "Fiverr",
    "wallapop": "Wallapop",
}

COUNTRIES = ["DE", "US", "GB", "FR", "IT", "ES", "NL", "BE", "AT", "CH", "SE", "NO", "DK", "FI", "PL", "CZ", "AU", "CA", "SG", "MY"]


def _derive_service_from_url(url: str) -> str:
    """
    Определяет service-код для Goo.Network API из URL объявления.
    Примеры: vinted.nl → vinted_nl, es.wallapop.com → wallapop_es
    Fallback: возвращает пустую строку.
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().replace("www.", "")
        # vinted.de, vinted.nl, vinted.it, ...
        if host.startswith("vinted."):
            cc = host.split(".")[1]
            return f"vinted_{cc}"
        # es.wallapop.com, ...
        if "wallapop" in host:
            parts = host.split(".")
            if parts[0] in ("es", "it", "de", "fr", "pt"):
                return f"wallapop_{parts[0]}"
            return "wallapop_es"
        # subito.it
        if "subito" in host:
            return "subito_it"
        # marktplaats.nl
        if "marktplaats" in host:
            return "marktplaats_nl"
        # 2dehands.be
        if "2dehands" in host:
            return "2dehands_be"
        # kleinanzeigen.de / ebay.de
        if "kleinanzeigen" in host or ("ebay" in host and ".de" in host):
            return "ebay_de"
        # olx.pl / olx.ro / ...
        if "olx" in host:
            cc = host.rsplit(".", 1)[-1]
            return f"olx_{cc}"
    except Exception:
        pass
    return ""

def debug_log(message: str, driver=None, email: str = ""):
    """Логирование в DEBUG режиме"""
    if not DEBUG:
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
    safe_email = email.replace("@", "_").replace(".", "_") if email else "unknown"
    
    # Создаём файл лога
    log_file = os.path.join(DEBUG_DIR, f"debug_{safe_email}_{timestamp}.txt")
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")
        f.write("=" * 80 + "\n\n")
        
        if driver:
            try:
                f.write("🔗 CURRENT URL:\n")
                f.write(f"{driver.current_url}\n\n")
                
                f.write("📄 PAGE TITLE:\n")
                f.write(f"{driver.title}\n\n")
                
                f.write("🪟 WINDOW HANDLES:\n")
                f.write(f"Total: {len(driver.window_handles)}\n")
                for i, handle in enumerate(driver.window_handles):
                    f.write(f"  [{i}] {handle}\n")
                f.write("\n")
                
                f.write("📝 PAGE HTML (first 5000 chars):\n")
                f.write("-" * 80 + "\n")
                html = driver.page_source
                f.write(html[:5000])
                f.write("\n...\n\n")
                
                f.write("🔍 SEARCHING FOR KEY ELEMENTS:\n")
                f.write("-" * 80 + "\n")
                
                # Поиск ключевых элементов
                try:
                    recipients = driver.find_element(By.XPATH, '//*[contains(text(), "Recipients")]')
                    f.write("✓ Контейнер 'Recipients' найден (форма открыта)\n")
                except:
                    f.write("✗ Контейнер 'Recipients' НЕ найден (форма закрыта)\n")
                
                # Поиск кнопки Compose
                try:
                    compose = driver.find_element(By.XPATH, '//*[contains(text(), "Compose")]')
                    f.write("✓ Кнопка 'Compose' найдена\n")
                except:
                    f.write("✗ Кнопка 'Compose' НЕ найдена\n")
                
                # Новые селекторы для поля "To"
                try:
                    to_field = driver.find_element(By.XPATH, '//input[@placeholder="To"]')
                    f.write("✓ Поле 'To' найдено (placeholder)\n")
                except:
                    try:
                        to_field = driver.find_element(By.XPATH, '//input[@aria-label="To"]')
                        f.write("✓ Поле 'To' найдено (aria-label)\n")
                    except:
                        f.write("✗ Поле 'To' НЕ найдено\n")
                
                try:
                    subject_field = driver.find_element(By.XPATH, '//input[@placeholder="Subject"]')
                    f.write("✓ Поле 'Subject' найдено (placeholder)\n")
                except:
                    try:
                        subject_field = driver.find_element(By.CSS_SELECTOR, 'input[name="subjectbox"]')
                        f.write("✓ Поле 'Subject' найдено\n")
                    except:
                        f.write("✗ Поле 'Subject' НЕ найдено\n")
                
                try:
                    body_field = driver.find_element(By.CSS_SELECTOR, 'div[role="textbox"]')
                    f.write("✓ Поле 'Body' найдено\n")
                except:
                    f.write("✗ Поле 'Body' НЕ найдено\n")
                
                # Новые селекторы для кнопки Send
                try:
                    send_btn = driver.find_element(By.XPATH, '//div[@role="button"]//span[contains(text(),"Send")]')
                    f.write("✓ Кнопка 'Send' найдена (новый вариант)\n")
                except:
                    try:
                        send_btn = driver.find_element(By.CSS_SELECTOR, 'div[role="button"][data-tooltip*="Send"]')
                        f.write("✓ Кнопка 'Send' найдена (data-tooltip)\n")
                    except:
                        f.write("✗ Кнопка 'Send' НЕ найдена\n")
                
                f.write("\n")
                
                # Скриншот
                f.write("📸 TAKING SCREENSHOT...\n")
                screenshot_path = os.path.join(DEBUG_DIR, f"screenshot_{safe_email}_{timestamp}.png")
                driver.save_screenshot(screenshot_path)
                f.write(f"✓ Сохранён в: {screenshot_path}\n")
                
            except Exception as e:
                f.write(f"⚠️ Ошибка при сборе информации: {e}\n")
    
    print(f"📝 DEBUG лог сохранён: {log_file}")

def spintax(text: str) -> str:
    """Заменяет {вариант1|вариант2|вариант3} на случайный вариант."""
    pattern = re.compile(r'\{([^{}]+)\}')
    while pattern.search(text):
        text = pattern.sub(lambda m: random.choice(m.group(1).split('|')), text)
    return text

def human_type(element, text, min_delay: float = 0.03, max_delay: float = 0.10):
    """
    Хаотичный посимвольный ввод — имитация живого человека.
    
    Поведение:
    - Базовая скорость — [min_delay, max_delay]
    - Барсты (30%): краткие залпы быстрой печати (speed × 0.25)
    - Пауза после пробела: +20–70мс (смена слова)
    - Пауза после пунктуации: +60–200мс (сброс перед новым словом)
    - Редкое «раздумье» (3%): +150–500мс (человек задумался)
    - Редкое «замедление» (8%): удвоенный интервал (как человек замедлил)
    """
    burst_mode = False
    burst_remaining = 0

    for i, char in enumerate(text):
        try:
            element.send_keys(char)
        except Exception as e:
            raise RuntimeError(f"Ошибка посимвольного ввода на позиции {i + 1}: {e}")

        # Базовая задержка
        delay = random.uniform(min_delay, max_delay)

        # Барст-режим: запускаем залп быстрой печати
        if burst_remaining > 0:
            delay *= 0.25  # в залпе — очень быстро
            burst_remaining -= 1
        elif random.random() < 0.30:
            # Запускаем новый залп: 2–4 символа
            burst_remaining = random.randint(2, 4)
            delay *= 0.25

        # Пауза после пробела (смена слова)
        if char == ' ':
            delay += random.uniform(0.02, 0.07)
            burst_remaining = 0  # после пробела залп сбрасывается

        # Пауза после пунктуации
        elif char in '.,!?;:':
            delay += random.uniform(0.06, 0.18)
            burst_remaining = 0

        # Редкое «раздумье» — человек задумался
        elif random.random() < 0.03:
            delay += random.uniform(0.15, 0.50)
            burst_remaining = 0

        # Редкое «замедление» — печатал медленнее
        elif random.random() < 0.08:
            delay *= 2.0

        time.sleep(max(delay, 0.01))  # не меньше 10мс

# ══════════════════════════════════════════════
#  DOLPHIN + SELENIUM
# ══════════════════════════════════════════════

def dolphin_start(profile_id: str, token: str) -> dict:
    url = f"{DOLPHIN_API}/browser_profiles/{profile_id}/start?automation=1"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"Dolphin: не удалось запустить профиль {profile_id}")
    return data["automation"]

def dolphin_stop(profile_id: str, token: str):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/stop",
                     headers=headers, timeout=10)
    except Exception:
        pass

def get_driver(automation: dict) -> webdriver.Remote:
    from selenium.webdriver.chrome.service import Service
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{automation['port']}")
    driver_path = automation.get("webdriver", "")
    if driver_path:
        service = Service(executable_path=driver_path)
        return webdriver.Chrome(service=service, options=opts)
    else:
        return webdriver.Chrome(options=opts)

def find_element_fast(driver, selectors, timeout=2, email: str = ""):
    """Быстрый поиск элемента с минимальным таймаутом и DEBUG логированием."""
    # Быстрый поиск с коротким таймаутом
    wait_fast = WebDriverWait(driver, timeout)
    for by, value in selectors:
        try:
            el = wait_fast.until(EC.element_to_be_clickable((by, value)))
            if DEBUG:
                print(f"✓ Найден элемент: {by}={value}")
            return el
        except:
            if DEBUG:
                print(f"✗ Элемент не найден: {by}={value}")
    
    # Медленный поиск с увеличенным таймаутом
    wait_slow = WebDriverWait(driver, 10)
    for by, value in selectors:
        try:
            el = wait_slow.until(EC.element_to_be_clickable((by, value)))
            if DEBUG:
                print(f"✓ Найден элемент (медленный поиск): {by}={value}")
            return el
        except Exception as e:
            if DEBUG:
                print(f"✗ Элемент не найден (медленный поиск): {by}={value} - {str(e)[:100]}")
    
    # Критическая ошибка - сохраняем DEBUG информацию
    error_msg = f"Элемент не найден из {len(selectors)} вариантов"
    if DEBUG:
        print(f"❌ {error_msg}\n")
        debug_log(error_msg, driver=driver, email=email)
    
    raise RuntimeError(error_msg)

GMAIL_LOAD_WAIT_SECONDS = 3  # время ожидания загрузки Gmail (не уменьшаем — медленные прокси нуждаются в этом)

def open_gmail_inbox_tab(driver, tab_handle: str = None) -> str:
    """
    Переключиться на рабочую вкладку Gmail.
    Если вкладка есть — переключаемся И навигируем на inbox (чтобы не быть в Sent/треде).
    Если вкладки нет — открываем новую.
    Возвращает handle рабочей вкладки.
    """
    if tab_handle and tab_handle in driver.window_handles:
        driver.switch_to.window(tab_handle)
        # ВСЕГДА навигируем на inbox — убеждаемся что не в Sent и не в треде
        current = driver.current_url
        if not current.startswith("https://mail.google.com/mail/u/0/#inbox"):
            driver.get("https://mail.google.com/mail/u/0/#inbox")
            time.sleep(GMAIL_LOAD_WAIT_SECONDS)
        return tab_handle

    # Вкладка не найдена — открываем новую чистую
    before = set(driver.window_handles)
    driver.execute_script("window.open('about:blank', '_blank');")
    after = set(driver.window_handles)
    new_handles = after - before
    new_handle = new_handles.pop() if new_handles else driver.window_handles[-1]
    driver.switch_to.window(new_handle)
    driver.get("https://mail.google.com/mail/u/0/#inbox")
    time.sleep(GMAIL_LOAD_WAIT_SECONDS)
    return new_handle


def _close_stale_compose_forms(driver):
    """
    Закрывает все зависшие формы Compose (если открыты) перед началом нового письма.
    Это предотвращает открытие двойной формы.
    """
    try:
        forms = driver.find_elements(By.CSS_SELECTOR, 'div.nH.if.adB, div.AD')
        if not forms:
            return
        if DEBUG:
            print(f"⚠️ Найдено {len(forms)} открытых форм Compose — закрываю через ESC")
        # Пробуем закрыть через ESC
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        time.sleep(0.4)
        # Если остались — убиваем через JS
        forms2 = driver.find_elements(By.CSS_SELECTOR, 'div.nH.if.adB, div.AD')
        for form in forms2:
            try:
                driver.execute_script("arguments[0].remove();", form)
            except Exception:
                pass
        if forms2 and DEBUG:
            print(f"  → Удалил {len(forms2)} форм через JS")
    except Exception:
        pass


def _wait_for_inbox_compose_button(driver, timeout: int = 15) -> bool:
    """
    Ждёт появления кнопки Compose в Inbox.
    Возвращает True если нашли, False если тайм-аут.
    """
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: any([
                _try_find(d, By.CSS_SELECTOR, 'div.T-I.T-I-KE.L3'),
                _try_find(d, By.XPATH, "//div[@gh='cm']"),
                _try_find(d, By.CSS_SELECTOR, 'div[gh="cm"]'),
            ])
        )
        return True
    except Exception:
        return False


def _try_find(driver, by, value) -> bool:
    try:
        el = driver.find_element(by, value)
        return el.is_displayed()
    except Exception:
        return False

def send_via_gmail(driver, recipient: str, subject: str, body: str,
                   tab_handle: str = None,
                   typing_min: float = 0.03, typing_max: float = 0.10) -> None:
    """
    Отправка письма через Gmail.
    Не заходит в Sent и не открывает View message — остаётся в Inbox после отправки.
    Закрывает зависшие формы перед началом.
    """
    if DEBUG:
        print(f"\n{'='*80}")
        print(f"📧 ОТПРАВЛЯЮ ПИСЬМО")
        print(f"To: {recipient}")
        print(f"Subject: {subject[:50]}")
        print(f"{'='*80}\n")

    try:
        # ── 1. Убедиться что мы на вкладке Inbox ──
        if tab_handle and tab_handle in driver.window_handles:
            driver.switch_to.window(tab_handle)
        else:
            # Фаллбек: ищем Gmail-вкладку
            gmail_found = False
            for handle in driver.window_handles:
                try:
                    driver.switch_to.window(handle)
                    if _is_gmail_tab(driver.current_url):
                        gmail_found = True
                        break
                except Exception:
                    pass
            if not gmail_found:
                driver.execute_script("window.open('https://mail.google.com', '_blank');")
                time.sleep(random.uniform(1.0, 2.0))
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(random.uniform(2.0, 3.0))

        # Гарантируем что мы в Inbox, а не в треде/Sent
        current_url = driver.current_url
        if not current_url.startswith("https://mail.google.com/mail/u/0/#inbox"):
            if DEBUG:
                print(f"⚠️ Не в Inbox (URL={current_url[:60]}), перехожу в Inbox")
            driver.get("https://mail.google.com/mail/u/0/#inbox")
            time.sleep(GMAIL_LOAD_WAIT_SECONDS)

        # ── 2. Закрыть зависшие формы Compose (предотвращает двойную форму) ──
        _close_stale_compose_forms(driver)

        # ── 3. Ждём исчезновения анимации отправки ──
        try:
            WebDriverWait(driver, 4).until(
                EC.invisibility_of_element_located((By.ID, "explosion_clipper_div"))
            )
        except Exception:
            pass

        # ── 4. Нажать Compose ──
        if DEBUG:
            print("🔍 Ищу кнопку 'Compose'...")
        compose = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'div.T-I.T-I-KE.L3'),
            (By.CSS_SELECTOR, 'div[gh="cm"]'),
            (By.XPATH, "//div[@gh='cm']"),
            (By.XPATH, "//div[contains(@class,'T-I-KE')]"),
            (By.CSS_SELECTOR, 'div[role="button"][data-tooltip*="Compose"]'),
        ], timeout=10, email=recipient)
        compose.click()
        if DEBUG:
            print("✓ Кликнул на 'Compose'")
        time.sleep(random.uniform(0.6, 1.2))

        # ── 5. Поле To ──
        try:
            to_container = driver.find_element(By.XPATH, '//div[@aria-label="To"]')
            to_container.click()
            time.sleep(random.uniform(0.2, 0.5))
        except Exception:
            pass

        to_input = find_element_fast(driver, [
            (By.XPATH, '//div[@aria-label="To"]//input'),
            (By.CSS_SELECTOR, 'div[aria-label="To"] input'),
            (By.XPATH, '//div[@aria-label="To"]//textarea'),
            (By.XPATH, '//textarea[@name="to"]'),
        ], timeout=5, email=recipient)

        to_input.click()
        time.sleep(random.uniform(0.2, 0.4))
        try:
            human_type(to_input, recipient, typing_min, typing_max)
        except Exception:
            ActionChains(driver).click(to_input).perform()
            time.sleep(0.3)
            to_input = find_element_fast(driver, [
                (By.XPATH, '//div[@aria-label="To"]//input'),
                (By.CSS_SELECTOR, 'div[aria-label="To"] input'),
            ], timeout=3, email=recipient)
            human_type(to_input, recipient, typing_min, typing_max)
        if DEBUG:
            print(f"✓ Вписал адрес: {recipient}")
        time.sleep(random.uniform(0.3, 0.6))
        to_input.send_keys(Keys.TAB)
        time.sleep(random.uniform(0.2, 0.5))

        # ── 6. Поле Subject ──
        subj_input = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'input[name="subjectbox"]'),
            (By.XPATH, '//input[@name="subjectbox"]'),
            (By.CSS_SELECTOR, 'input[aria-label="Subject"]'),
        ], timeout=5, email=recipient)
        subj_input.click()
        time.sleep(random.uniform(0.2, 0.5))
        human_type(subj_input, subject, typing_min, typing_max)
        if DEBUG:
            print(f"✓ Вписал тему: {subject[:50]}")
        time.sleep(random.uniform(0.2, 0.5))

        # ── 7. Поле Body ──
        body_input = find_element_fast(driver, [
            (By.XPATH, '//div[@aria-label="Message Body"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Message Body"]'),
            (By.CSS_SELECTOR, 'div[role="textbox"][aria-multiline="true"]'),
            (By.CSS_SELECTOR, 'div[role="textbox"]'),
        ], timeout=5, email=recipient)
        body_input.click()
        time.sleep(random.uniform(0.2, 0.5))
        human_type(body_input, body, typing_min, typing_max)
        if DEBUG:
            print(f"✓ Вписал текст")
        time.sleep(random.uniform(0.2, 0.5))

        # Закрыть попап автодополнения
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(0.2)
        except Exception:
            pass

        # ── 8. Кнопка Send ──
        if DEBUG:
            print("🔍 Ищу кнопку 'Send'...")
        send_button = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'div[data-tooltip*="Send"]'),
            (By.CSS_SELECTOR, 'div[aria-label*="Send"][role="button"]'),
            (By.XPATH, '//div[@role="button"][contains(@aria-label,"Send")]'),
            (By.XPATH, '//div[@role="button"]//span[contains(text(),"Send")]'),
        ], timeout=5, email=recipient)

        try:
            send_button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", send_button)
        if DEBUG:
            print("✓ Кликнул 'Send'")

        # ── 9. Ждём уведомления "Message sent" ──
        if DEBUG:
            print("⏳ Жду подтверждения отправки...")
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[contains(text(), "Message sent")]')
            )
        )
        if DEBUG:
            print("✓ 'Письмо отправлено' получено")

        # ── 10. Ждём чтобы форма Compose закрылась и остаёмся в Inbox ──
        # Важно: НЕ кликаем "View message" — это уводит в Sent/тред!
        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, 'div.nH.if.adB')
                )
            )
            if DEBUG:
                print("✓ Форма Compose закрылась")
        except Exception:
            # Если форма не закрылась за 5 сек — принудительно убираем
            _close_stale_compose_forms(driver)

        time.sleep(random.uniform(0.5, 1.0))

        if DEBUG:
            print(f"✅ ПИСЬМО ОТПРАВЛЕНО на {recipient}!\n")

    except Exception as e:
        error_msg = f"ОШИБКА при отправке письма на {recipient}: {str(e)}"
        if DEBUG:
            print(f"❌ {error_msg}\n")
            debug_log(error_msg, driver=driver, email=recipient)
        raise


# ══════════════════════════════════════════════
#  ОТВЕТ В ТРЕД GMAIL (Reply)
# ══════════════════════════════════════════════

def reply_in_gmail_thread(driver, recipient_email: str, html_body: str) -> None:
    """
    Найти тред с recipient_email в Gmail и ответить HTML-ом.

    Алгоритм:
    1. Перейти в Gmail Inbox
    2. Поиск по email (находит и отправленные, и полученные)
    3. Проверить что есть результаты
    4. Открыть первый тред
    5. Нажать Reply
    6. Вставить HTML через JS (innerHTML)
    7. Нажать Send
    8. Вернуться в Inbox
    """
    if DEBUG:
        print(f"\n{'='*80}")
        print(f"📩 ОТВЕТ В ТРЕД")
        print(f"To: {recipient_email}")
        print(f"HTML length: {len(html_body)}")
        print(f"{'='*80}\n")

    try:
        # ── 1. Перейти в Inbox ──
        driver.get("https://mail.google.com/mail/u/0/#inbox")
        time.sleep(GMAIL_LOAD_WAIT_SECONDS)

        # ── 2. Поиск email через search bar ──
        # Используем просто email — найдёт ВСЕ письма с этим адресом
        # (и отправленные нами, и полученные от него)
        search_query = recipient_email
        if DEBUG:
            print(f"🔍 Поиск: {search_query}")

        search_input = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'input[aria-label="Search mail"]'),
            (By.CSS_SELECTOR, 'input[name="q"]'),
            (By.XPATH, '//input[@aria-label="Search mail"]'),
        ], timeout=10, email=recipient_email)

        search_input.click()
        time.sleep(0.3)
        search_input.clear()
        time.sleep(0.2)
        search_input.send_keys(search_query)
        time.sleep(0.3)
        search_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(2.5, 3.5))

        if DEBUG:
            print("✓ Поиск выполнен")

        # ── 3. Проверить что есть результаты ──
        # Сначала проверяем нет ли сообщения "No results"
        try:
            no_results = driver.find_elements(
                By.XPATH,
                '//*[contains(text(),"No results") or contains(text(),"Нет результатов") '
                'or contains(text(),"No messages matched")]'
            )
            if no_results:
                raise RuntimeError(
                    f"Переписка с {recipient_email} не найдена в Gmail. "
                    f"Проверь что письмо было отправлено из этого аккаунта."
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # Элемент "No results" не найден — значит результаты есть

        # ── 4. Открыть первый тред в результатах ──
        # Gmail строки могут иметь разные классы:
        #   tr.zA — стандартная строка (непрочитанная)
        #   tr.yO — прочитанная строка
        #   tr[draggable] — перетаскиваемая строка (все письма)
        thread_row = None
        thread_selectors = [
            (By.CSS_SELECTOR, 'tr.zA'),
            (By.CSS_SELECTOR, 'tr.yO'),
            (By.CSS_SELECTOR, 'tr[draggable="true"]'),
            (By.XPATH, '//tr[.//td[contains(@class,"xY") or contains(@class,"yX")]]'),
            (By.XPATH, '//div[@role="main"]//tr[.//td]'),
        ]
        for by, selector in thread_selectors:
            try:
                thread_row = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if DEBUG:
                    print(f"✓ Строка треда найдена: {selector}")
                break
            except Exception:
                if DEBUG:
                    print(f"✗ Селектор не подошёл: {selector}")
                continue

        if thread_row is None:
            raise RuntimeError(
                f"Тред с {recipient_email} не найден в результатах поиска. "
                f"Возможно письмо было удалено."
            )

        thread_row.click()
        time.sleep(random.uniform(1.0, 1.5))

        if DEBUG:
            print("✓ Тред открыт")

        # ── 5. Нажать Reply ──
        reply_btn = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'div[data-tooltip="Reply"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Reply"]'),
            (By.CSS_SELECTOR, '[data-tooltip="Reply"]'),
            (By.XPATH, '//div[contains(@data-tooltip,"Rispondi")]'),  # Итальянский
            (By.XPATH, '//div[contains(@data-tooltip,"Ответить")]'),  # Русский
            (By.XPATH, '//div[contains(@aria-label,"Reply")]'),
            (By.CSS_SELECTOR, 'span.ams.bkH'),     # иконка Reply
        ], timeout=3, email=recipient_email)

        reply_btn.click()
        time.sleep(random.uniform(0.8, 1.2))

        if DEBUG:
            print("✓ Нажал Reply")

        # ── 6. Найти поле ввода ответа и вставить HTML ──
        body_input = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'div[aria-label="Message Body"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Тело сообщения"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Corpo del messaggio"]'),
            (By.CSS_SELECTOR, 'div[role="textbox"][aria-multiline="true"]'),
            (By.CSS_SELECTOR, 'div[role="textbox"][contenteditable="true"]'),
            (By.CSS_SELECTOR, 'div.editable[contenteditable="true"]'),
            (By.CSS_SELECTOR, 'div[g_editable="true"]'),
        ], timeout=3, email=recipient_email)

        # ═══ Вставка HTML — 6 методов с fallback ═══
        # Метод 0 (CDP): Chrome DevTools Protocol — "Edit as HTML" программно
        # Это ТОЧНЫЙ аналог ручного "Inspect → Edit as HTML" в DevTools
        inserted = False
        try:
            # Получаем nodeId через CDP
            dom_doc = driver.execute_cdp_cmd("DOM.getDocument", {"depth": 0})
            root_node_id = dom_doc["root"]["nodeId"]

            # Находим наш элемент через JS и получаем его backendNodeId
            backend_node_id = driver.execute_script("""
                var el = arguments[0];
                // Уникальный маркер для поиска через CDP
                el.setAttribute('data-selenium-target', 'compose-body');
                return true;
            """, body_input)

            # Ищем элемент через CDP
            search = driver.execute_cdp_cmd("DOM.performSearch", {
                "query": '[data-selenium-target="compose-body"]'
            })
            if search.get("resultCount", 0) > 0:
                results = driver.execute_cdp_cmd("DOM.getSearchResults", {
                    "searchId": search["searchId"],
                    "fromIndex": 0,
                    "toIndex": 1
                })
                node_id = results["nodeIds"][0]

                # Устанавливаем outerHTML — это РОВНО "Edit as HTML"
                # Оборачиваем HTML в div чтобы сохранить contenteditable элемент
                driver.execute_cdp_cmd("DOM.setOuterHTML", {
                    "nodeId": node_id,
                    "outerHTML": f'<div data-selenium-target="compose-body" contenteditable="true" role="textbox" aria-multiline="true" style="min-height:50px;">{html_body}</div>'
                })
                inserted = True
                if DEBUG:
                    print("✓ HTML вставлен через CDP (Edit as HTML)")

            # Убираем маркер
            driver.execute_script("""
                var el = document.querySelector('[data-selenium-target="compose-body"]');
                if (el) el.removeAttribute('data-selenium-target');
            """)
        except Exception as cdp_err:
            if DEBUG:
                print(f"⚠ CDP метод не сработал: {str(cdp_err)[:100]}")

        # Метод 1-4: JavaScript fallbacks
        if not inserted:
            inserted = driver.execute_script("""
                var el = arguments[0];
                var html = arguments[1];
                el.focus();
                el.click();

                var success = false;

                // Метод 1: Trusted Types policy (обход Gmail CSP)
                if (!success) {
                    try {
                        if (typeof trustedTypes !== 'undefined' && trustedTypes.createPolicy) {
                            var policy = trustedTypes.createPolicy('seleniumHTML', {
                                createHTML: function(s) { return s; }
                            });
                            el.innerHTML = policy.createHTML(html);
                            success = el.innerHTML.length > 20;
                        }
                    } catch(e) { /* policy already exists or not supported */ }
                }

                // Метод 2: Прямой innerHTML (если нет Trusted Types)
                if (!success) {
                    try {
                        el.innerHTML = html;
                        success = el.innerHTML.length > 20;
                    } catch(e) {}
                }

                // Метод 3: DOMParser + importNode (обход любых ограничений)
                if (!success) {
                    try {
                        var parser = new DOMParser();
                        var doc = parser.parseFromString(html, 'text/html');
                        while (el.firstChild) el.removeChild(el.firstChild);
                        var nodes = doc.body.childNodes;
                        for (var i = 0; i < nodes.length; i++) {
                            el.appendChild(document.importNode(nodes[i], true));
                        }
                        success = el.innerHTML.length > 20;
                    } catch(e) {}
                }

                // Метод 4: execCommand insertHTML (legacy)
                if (!success) {
                    try {
                        var sel = window.getSelection();
                        var range = document.createRange();
                        range.selectNodeContents(el);
                        sel.removeAllRanges();
                        sel.addRange(range);
                        document.execCommand('insertHTML', false, html);
                        success = el.innerHTML.length > 20;
                    } catch(e) {}
                }

                // Уведомляем Gmail об изменениях
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));

                return success;
            """, body_input, html_body)

        # Метод 5: Clipboard fallback
        if not inserted:
            if DEBUG:
                print("⚠ JS методы не сработали, пробую Ctrl+A/Ctrl+V через clipboard...")
            try:
                import subprocess
                process = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
                process.communicate(html_body.encode('utf-8'))

                body_input.click()
                time.sleep(0.2)
                body_input.send_keys(Keys.CONTROL, 'a')
                time.sleep(0.1)
                body_input.send_keys(Keys.CONTROL, 'v')
                time.sleep(0.3)
                if DEBUG:
                    print("✓ HTML вставлен через clipboard fallback")
            except Exception as clip_err:
                if DEBUG:
                    print(f"✗ Clipboard fallback тоже не сработал: {clip_err}")
                raise RuntimeError(f"Не удалось вставить HTML в поле ответа")

        time.sleep(0.3)

        # Уведомляем Gmail что контент изменился (чтобы кнопка Send активировалась)
        driver.execute_script("""
            var el = arguments[0];
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, key: ' '}));
            el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: ' '}));
        """, body_input)

        if DEBUG:
            print("✓ HTML вставлен в тело ответа")

        # ── 7. Нажать Send ──
        send_button = find_element_fast(driver, [
            (By.CSS_SELECTOR, 'div[data-tooltip*="Send"]'),
            (By.CSS_SELECTOR, 'div[aria-label*="Send"][role="button"]'),
            (By.XPATH, '//div[@role="button"][contains(@aria-label,"Send")]'),
        ], timeout=5, email=recipient_email)

        try:
            send_button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", send_button)

        if DEBUG:
            print("✓ Кликнул Send")

        # ── 8. Ждём подтверждения "Message sent" ──
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[contains(text(), "Message sent")]')
            )
        )
        if DEBUG:
            print("✓ Ответ отправлен!")

        # ── 9. Вернуться в чистый Inbox ──
        time.sleep(random.uniform(0.5, 1.0))
        driver.get("https://mail.google.com/mail/u/0/#inbox")
        time.sleep(GMAIL_LOAD_WAIT_SECONDS)

        if DEBUG:
            print(f"✅ ОТВЕТ ОТПРАВЛЕН на {recipient_email}! Inbox чист.\n")

    except Exception as e:
        error_msg = f"ОШИБКА при ответе на {recipient_email}: {str(e)}"
        if DEBUG:
            print(f"❌ {error_msg}\n")
            debug_log(error_msg, driver=driver, email=recipient_email)
        # Безопасный возврат в Inbox (ВСЕГДА)
        try:
            driver.get("https://mail.google.com/mail/u/0/#inbox")
            time.sleep(GMAIL_LOAD_WAIT_SECONDS)
        except Exception:
            pass
        raise


# ══════════════════════════════════════════════
#  ГЛАВНОЕ ОКНО
# ══════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("blue_magic_bybumpy")
        self.geometry("1300x850")
        self.minsize(1100, 750)
        self.configure(fg_color="#0f1117")

        self.templates: list[dict] = self._load_templates()
        self.profiles: list[str] = self._load_profiles()
        self.running = False
        self.parser_running = False
        self.background_worker = BackgroundWorker()
        self._active_drivers: dict = {}  # {profile_id: driver}
        self._active_tab_handles: dict = {}  # {profile_id: tab_handle}
        self._warmup_thread = None
        self._drivers_lock = threading.Lock()
        self._sent_emails: set = self._load_sent_emails()

        # База данных: сохраняет данные товаров для генерации HTML-ответов
        from conversation_store import ConversationStore
        self._conversation_store = ConversationStore()

        self._build_ui()
        self._load_parser_filters()
        self.after(100, self._update_parser_ui)

    # ── UI ────────────────────────────────────

    def _build_ui(self):
        # ── Левая панель (скроллируемая) ──
        left_container = ctk.CTkFrame(self, width=380, fg_color="#161b22", corner_radius=0)
        left_container.pack(side="left", fill="y", padx=0, pady=0)
        left_container.pack_propagate(False)

        # Скроллируемый фрейм
        left_scroll = ctk.CTkScrollableFrame(left_container, fg_color="#161b22")
        left_scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Лого (картинка + название) ──────────────────────────────
        logo_frame = ctk.CTkFrame(left_scroll, fg_color="transparent")
        logo_frame.pack(fill="x", padx=12, pady=(14, 4))

        # Попытка загрузить картинку
        if _PIL_AVAILABLE:
            try:
                pil_img = PilImage.open(os.path.join(os.path.dirname(__file__), "blue_magic.png"))
                pil_img = pil_img.resize((120, 90), PilImage.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(120, 90))
                ctk.CTkLabel(logo_frame, image=ctk_img, text="").pack(anchor="w", pady=(0, 6))
            except Exception:
                pass  # файл не найден — показываем только текст

        ctk.CTkLabel(logo_frame, text="blue_magic_bybumpy",
                     font=ctk.CTkFont("Segoe UI", 16, "bold"),
                     text_color="#58a6ff").pack(anchor="w")

        ctk.CTkLabel(logo_frame, text="Gmail Sender + Parser Integration",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#484f58").pack(anchor="w", pady=(2, 0))

        ctk.CTkLabel(left_scroll,
                     text="Самое главное в бизнесе — честность,\nточность и трудолюбие.\nСемья, никогда не забывайте — откуда мы.",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#6e7681",
                     justify="left",
                     wraplength=330).pack(padx=16, anchor="w", pady=(6, 4))

        # DEBUG статус
        debug_text = "🔴 DEBUG ON" if DEBUG else "🟢 DEBUG OFF"
        debug_color = "#f85149" if DEBUG else "#3fb950"
        ctk.CTkLabel(left_scroll, text=debug_text,
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color=debug_color).pack(padx=16, anchor="w", pady=(0, 2))

        self._divider(left_scroll)

        # ── API Токен Dolphin ──
        ctk.CTkLabel(left_scroll, text="API ТОКЕН DOLPHIN",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        token_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        token_row.pack(padx=12, fill="x")

        saved_token = self._load_token()
        self.token_entry = ctk.CTkEntry(token_row,
                                        placeholder_text="Токен...",
                                        show="•",
                                        fg_color="#0d1117",
                                        border_color="#30363d",
                                        font=ctk.CTkFont("Consolas", 10),
                                        text_color="#c9d1d9")
        self.token_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if saved_token:
            self.token_entry.insert(0, saved_token)

        self.show_token_btn = ctk.CTkButton(token_row, text="👁", width=30, height=26,
                                            fg_color="#21262d", hover_color="#30363d",
                                            font=ctk.CTkFont("Segoe UI", 11),
                                            command=self._toggle_token_visibility)
        self.show_token_btn.pack(side="left")

        ctk.CTkButton(left_scroll, text="💾 Сохранить", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._save_token).pack(padx=12, fill="x", pady=(3, 6))

        self._divider(left_scroll)

        # ── API Токен Парсера ──
        ctk.CTkLabel(left_scroll, text="API ТОКЕН ПАРСЕРА",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        parser_token_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        parser_token_row.pack(padx=12, fill="x")

        saved_parser_token = self._load_parser_token()
        self.parser_token_entry = ctk.CTkEntry(parser_token_row,
                                               placeholder_text="API ключ...",
                                               show="•",
                                               fg_color="#0d1117",
                                               border_color="#30363d",
                                               font=ctk.CTkFont("Consolas", 10),
                                               text_color="#c9d1d9")
        self.parser_token_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if saved_parser_token:
            self.parser_token_entry.insert(0, saved_parser_token)

        self.show_parser_token_btn = ctk.CTkButton(parser_token_row, text="👁", width=30, height=26,
                                                   fg_color="#21262d", hover_color="#30363d",
                                                   font=ctk.CTkFont("Segoe UI", 11),
                                                   command=self._toggle_parser_token_visibility)
        self.show_parser_token_btn.pack(side="left")

        ctk.CTkButton(left_scroll, text="💾 Сохранить", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._save_parser_token).pack(padx=12, fill="x", pady=(3, 6))

        self._divider(left_scroll)

        # ── ВЫБОР LINK API ──
        ctk.CTkLabel(left_scroll, text="LINK API (ГЕНЕРАЦИЯ ССЫЛОК)",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        # Загружаем текущее значение из конфига
        _link_cfg_active = "receiveolxiv"
        try:
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    _link_cfg_active = json.load(f).get("active_api", "receiveolxiv")
        except Exception:
            pass

        _link_api_display_map = {
            "receiveolxiv": "Receiveolxiv",
            "monkeyteam":   "MonkeyTeam",
            "goo_network":  "Goo.Network",
        }
        self.link_api_var = ctk.StringVar(
            value=_link_api_display_map.get(_link_cfg_active, "Receiveolxiv")
        )
        self.link_api_selector = ctk.CTkOptionMenu(
            left_scroll,
            values=["Receiveolxiv", "MonkeyTeam", "Goo.Network"],
            variable=self.link_api_var,
            fg_color="#0d1117",
            button_color="#21262d",
            button_hover_color="#30363d",
            dropdown_fg_color="#161b22",
            dropdown_hover_color="#21262d",
            font=ctk.CTkFont("Segoe UI", 10),
            dropdown_font=ctk.CTkFont("Segoe UI", 10),
            text_color="#c9d1d9",
            command=self._on_link_api_changed,
        )
        self.link_api_selector.pack(padx=12, fill="x", pady=(0, 2))

        self.link_api_status = ctk.CTkLabel(
            left_scroll, text="",
            font=ctk.CTkFont("Segoe UI", 8),
            text_color="#3fb950",
        )
        self.link_api_status.pack(padx=16, anchor="w", pady=(0, 6))
        self._update_link_api_status()

        # ── Общий конфиг для всех блоков ──
        _link_cfg_all = {}
        try:
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    _link_cfg_all = json.load(f)
        except Exception:
            pass

        # ── Поля настройки Receiveolxiv (скрыты/показаны при выборе) ──
        self.receiveolxiv_frame = ctk.CTkFrame(left_scroll, fg_color="transparent")
        self.receiveolxiv_frame.pack(padx=0, fill="x")

        ctk.CTkLabel(self.receiveolxiv_frame, text="User ID (Telegram ID):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 1))
        self.rx_userid_entry = ctk.CTkEntry(
            self.receiveolxiv_frame, placeholder_text="123456789",
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10), text_color="#c9d1d9")
        self.rx_userid_entry.pack(padx=12, fill="x")
        if _link_cfg_all.get("user_id"):
            self.rx_userid_entry.insert(0, _link_cfg_all["user_id"])

        ctk.CTkLabel(self.receiveolxiv_frame, text="API Key:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(4, 1))
        _rx_key_row = ctk.CTkFrame(self.receiveolxiv_frame, fg_color="transparent")
        _rx_key_row.pack(padx=12, fill="x")
        self.rx_apikey_entry = ctk.CTkEntry(
            _rx_key_row, placeholder_text="API ключ...",
            show="•", fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10), text_color="#c9d1d9")
        self.rx_apikey_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if _link_cfg_all.get("api_key"):
            self.rx_apikey_entry.insert(0, _link_cfg_all["api_key"])
        self.rx_apikey_eye_btn = ctk.CTkButton(
            _rx_key_row, text="👁", width=30, height=26,
            fg_color="#21262d", hover_color="#30363d",
            font=ctk.CTkFont("Segoe UI", 11),
            command=self._toggle_rx_apikey_visibility)
        self.rx_apikey_eye_btn.pack(side="left")

        ctk.CTkButton(self.receiveolxiv_frame, text="💾 Сохранить Receiveolxiv", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#3fb950",
                      command=self._save_receiveolxiv_config).pack(
            padx=12, fill="x", pady=(4, 6))

        if _link_cfg_active != "receiveolxiv":
            self.receiveolxiv_frame.pack_forget()

        # ── Поля настройки Goo.Network (скрыты/показаны при выборе) ──
        _link_cfg_goo = _link_cfg_all

        self.goo_network_frame = ctk.CTkFrame(left_scroll, fg_color="transparent")
        self.goo_network_frame.pack(padx=0, fill="x")

        ctk.CTkLabel(self.goo_network_frame, text="Ключ пользователя (Apikey):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 1))
        _goo_apikey_row = ctk.CTkFrame(self.goo_network_frame, fg_color="transparent")
        _goo_apikey_row.pack(padx=12, fill="x")
        self.goo_apikey_entry = ctk.CTkEntry(
            _goo_apikey_row, placeholder_text="Apikey ...",
            show="•", fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10), text_color="#c9d1d9")
        self.goo_apikey_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if _link_cfg_goo.get("goo_network_api_key"):
            self.goo_apikey_entry.insert(0, _link_cfg_goo["goo_network_api_key"])
        self.goo_apikey_eye_btn = ctk.CTkButton(
            _goo_apikey_row, text="👁", width=30, height=26,
            fg_color="#21262d", hover_color="#30363d",
            font=ctk.CTkFont("Segoe UI", 11),
            command=self._toggle_goo_apikey_visibility)
        self.goo_apikey_eye_btn.pack(side="left")

        ctk.CTkLabel(self.goo_network_frame, text="Ключ команды (X-Team-Key):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(4, 1))
        _goo_teamkey_row = ctk.CTkFrame(self.goo_network_frame, fg_color="transparent")
        _goo_teamkey_row.pack(padx=12, fill="x")
        self.goo_teamkey_entry = ctk.CTkEntry(
            _goo_teamkey_row, placeholder_text="Team Key ...",
            show="•", fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10), text_color="#c9d1d9")
        self.goo_teamkey_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if _link_cfg_goo.get("goo_network_team_key"):
            self.goo_teamkey_entry.insert(0, _link_cfg_goo["goo_network_team_key"])
        self.goo_teamkey_eye_btn = ctk.CTkButton(
            _goo_teamkey_row, text="👁", width=30, height=26,
            fg_color="#21262d", hover_color="#30363d",
            font=ctk.CTkFont("Segoe UI", 11),
            command=self._toggle_goo_teamkey_visibility)
        self.goo_teamkey_eye_btn.pack(side="left")

        ctk.CTkLabel(self.goo_network_frame, text="Profile ID:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(4, 1))
        self.goo_profileid_entry = ctk.CTkEntry(
            self.goo_network_frame, placeholder_text="T3tEktqZuli ...",
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10), text_color="#c9d1d9")
        self.goo_profileid_entry.pack(padx=12, fill="x")
        if _link_cfg_goo.get("goo_network_profile_id"):
            self.goo_profileid_entry.insert(0, _link_cfg_goo["goo_network_profile_id"])

        _goo_btn_row = ctk.CTkFrame(self.goo_network_frame, fg_color="transparent")
        _goo_btn_row.pack(padx=12, fill="x", pady=(4, 2))
        ctk.CTkButton(_goo_btn_row, text="💾 Сохранить", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#a371f7",
                      command=self._save_goo_network_config).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(_goo_btn_row, text="🔌 Тест", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._test_goo_network).pack(side="left")

        # Показываем/скрываем блок в зависимости от выбранного API
        if _link_cfg_active != "goo_network":
            self.goo_network_frame.pack_forget()

        self._divider(left_scroll)

        # ── ФИЛЬТРЫ ПАРСЕРА ──
        ctk.CTkLabel(left_scroll, text="ФИЛЬТРЫ ПАРСЕРА",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#58a6ff").pack(padx=16, anchor="w", pady=(6, 4))

        # Сервис-коды (ручной ввод)
        ctk.CTkLabel(left_scroll, text="Сервис-коды (через запятую):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.service_codes_var = ctk.StringVar(value="vinted_it")
        self.service_codes_entry = ctk.CTkEntry(
            left_scroll,
            textvariable=self.service_codes_var,
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 10),
            text_color="#79c0ff",
            placeholder_text="vinted_it, subito_it"
        )
        self.service_codes_entry.pack(padx=12, fill="x", pady=(0, 2))

        ctk.CTkLabel(left_scroll,
                     text="Формат: площадка_страна. Страна определяется автоматически.\n"
                          "Примеры: vinted_it  subito_it  wallapop_es  2dehands_nl",
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color="#484f58",
                     justify="left").pack(padx=16, anchor="w", pady=(0, 4))

        # Обратная совместимость
        self.platform_var = ctk.StringVar(value="vinted")

        # Страна (автоопределение из сервис-кода, но можно переопределить)
        ctk.CTkLabel(left_scroll, text="Страна (авто из сервис-кода):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.country_var = ctk.StringVar(value="")
        self.country_menu = ctk.CTkOptionMenu(left_scroll,
                                             values=["(авто)"] + COUNTRIES,
                                             variable=self.country_var,
                                             fg_color="#21262d", button_color="#30363d",
                                             font=ctk.CTkFont("Segoe UI", 9))
        self.country_var.set("(авто)")
        self.country_menu.pack(padx=12, fill="x", pady=(0, 4))

        # Категория
        ctk.CTkLabel(left_scroll, text="Категория (или пусто):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.category_entry = ctk.CTkEntry(left_scroll,
                                          placeholder_text="Пример: 1,2,3",
                                          fg_color="#0d1117",
                                          border_color="#30363d",
                                          font=ctk.CTkFont("Segoe UI", 9),
                                          text_color="#c9d1d9")
        self.category_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Цена
        ctk.CTkLabel(left_scroll, text="Цена от:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.price_from_entry = ctk.CTkEntry(left_scroll,
                                             placeholder_text="пример: 10",
                                             fg_color="#0d1117",
                                             border_color="#30363d",
                                             font=ctk.CTkFont("Segoe UI", 9),
                                             text_color="#c9d1d9")
        self.price_from_entry.pack(padx=12, fill="x", pady=(0, 4))

        ctk.CTkLabel(left_scroll, text="Цена до:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.price_to_entry = ctk.CTkEntry(left_scroll,
                                           placeholder_text="пример: 100",
                                           fg_color="#0d1117",
                                           border_color="#30363d",
                                           font=ctk.CTkFont("Segoe UI", 9),
                                           text_color="#c9d1d9")
        self.price_to_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Отзывы
        ctk.CTkLabel(left_scroll, text="Макс. отзывов продавца:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.reviews_entry = ctk.CTkEntry(left_scroll,
                                         placeholder_text="пример: 10 или 5..50",
                                         fg_color="#0d1117",
                                         border_color="#30363d",
                                         font=ctk.CTkFont("Segoe UI", 9),
                                         text_color="#c9d1d9")
        self.reviews_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Объявления продавца (ads)
        ctk.CTkLabel(left_scroll, text="Макс. объявлений продавца:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.ads_entry = ctk.CTkEntry(left_scroll,
                                      placeholder_text="пример: 50 или 5..50",
                                      fg_color="#0d1117",
                                      border_color="#30363d",
                                      font=ctk.CTkFont("Segoe UI", 9),
                                      text_color="#c9d1d9")
        self.ads_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Продажи продавца (sells)
        ctk.CTkLabel(left_scroll, text="Макс. продаж продавца:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.sells_entry = ctk.CTkEntry(left_scroll,
                                        placeholder_text="пример: 100 или 10..100",
                                        fg_color="#0d1117",
                                        border_color="#30363d",
                                        font=ctk.CTkFont("Segoe UI", 9),
                                        text_color="#c9d1d9")
        self.sells_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Покупки продавца (buys)
        ctk.CTkLabel(left_scroll, text="Макс. покупок продавца:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.buys_entry = ctk.CTkEntry(left_scroll,
                                       placeholder_text="пример: 50 или 5..50",
                                       fg_color="#0d1117",
                                       border_color="#30363d",
                                       font=ctk.CTkFont("Segoe UI", 9),
                                       text_color="#c9d1d9")
        self.buys_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Просмотры объявления (views)
        ctk.CTkLabel(left_scroll, text="Макс. просмотров объявления:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.views_entry = ctk.CTkEntry(left_scroll,
                                        placeholder_text="пример: 100",
                                        fg_color="#0d1117",
                                        border_color="#30363d",
                                        font=ctk.CTkFont("Segoe UI", 9),
                                        text_color="#c9d1d9")
        self.views_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Время публикации (publication)
        ctk.CTkLabel(left_scroll, text="Время публикации:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.publication_entry = ctk.CTkEntry(left_scroll,
                                              placeholder_text="пример: 30m, 24h, 5m",
                                              fg_color="#0d1117",
                                              border_color="#30363d",
                                              font=ctk.CTkFont("Segoe UI", 9),
                                              text_color="#c9d1d9")
        self.publication_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Дата регистрации
        ctk.CTkLabel(left_scroll, text="Зарегистрирован после (ДД-ММ-ГГГГ):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.registration_entry = ctk.CTkEntry(left_scroll,
                                              placeholder_text="01-01-2025 или 01-01-2020..01-01-2025",
                                              fg_color="#0d1117",
                                              border_color="#30363d",
                                              font=ctk.CTkFont("Segoe UI", 9),
                                              text_color="#c9d1d9")
        self.registration_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Стоп-слова (blacklist)
        ctk.CTkLabel(left_scroll, text="Стоп-слова (через запятую):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.blacklist_entry = ctk.CTkEntry(left_scroll,
                                            placeholder_text="iphone,samsung,apple",
                                            fg_color="#0d1117",
                                            border_color="#30363d",
                                            font=ctk.CTkFont("Segoe UI", 9),
                                            text_color="#c9d1d9")
        self.blacklist_entry.pack(padx=12, fill="x", pady=(0, 4))

        # Доставка (delivery)
        ctk.CTkLabel(left_scroll, text="Доставка:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.delivery_var = ctk.StringVar(value="(все)")
        self.delivery_menu = ctk.CTkOptionMenu(left_scroll,
                                              values=["все", "только с доставкой", "только самовывоз"],
                                              variable=self.delivery_var,
                                              fg_color="#21262d", button_color="#30363d",
                                              font=ctk.CTkFont("Segoe UI", 9))
        self.delivery_menu.pack(padx=12, fill="x", pady=(0, 4))

        # Телефон (phone)
        ctk.CTkLabel(left_scroll, text="Телефон:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.phone_var = ctk.StringVar(value="все")
        self.phone_menu = ctk.CTkOptionMenu(left_scroll,
                                           values=["все", "с телефоном", "без телефона"],
                                           variable=self.phone_var,
                                           fg_color="#21262d", button_color="#30363d",
                                           font=ctk.CTkFont("Segoe UI", 9))
        self.phone_menu.pack(padx=12, fill="x", pady=(0, 4))

        # Лимит результатов
        ctk.CTkLabel(left_scroll, text="Лимит результатов:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(2, 0))

        self.limit_var = ctk.StringVar(value="100")
        ctk.CTkEntry(left_scroll,
                    textvariable=self.limit_var,
                    fg_color="#0d1117",
                    border_color="#30363d",
                    font=ctk.CTkFont("Segoe UI", 9),
                    text_color="#c9d1d9").pack(padx=12, fill="x", pady=(0, 6))

        ctk.CTkButton(left_scroll, text="💾 Сохранить фильтры", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._save_parser_filters).pack(padx=12, fill="x", pady=(0, 6))

        self._divider(left_scroll)

        # ── Интервал отправки ──
        ctk.CTkLabel(left_scroll, text="ИНТЕРВАЛ ОТПРАВКИ (СЕК)",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        interval_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        interval_row.pack(padx=12, fill="x", pady=(0, 6))
        self.interval_var = ctk.StringVar(value="3")
        ctk.CTkEntry(interval_row, textvariable=self.interval_var, width=50,
                    fg_color="#0d1117", border_color="#30363d",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left")
        ctk.CTkLabel(interval_row, text=" сек",
                    text_color="#8b949e",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(4, 0))

        self._divider(left_scroll)

        # ── Скорость ввода символов ──
        ctk.CTkLabel(left_scroll, text="СКОРОСТЬ ВВОДА СИМВОЛОВ (МС)",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        typing_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        typing_row.pack(padx=12, fill="x", pady=(0, 2))
        ctk.CTkLabel(typing_row, text="от",
                    text_color="#8b949e",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left")
        self.typing_min_var = ctk.StringVar(value="30")
        ctk.CTkEntry(typing_row, textvariable=self.typing_min_var, width=45,
                    fg_color="#0d1117", border_color="#30363d",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(typing_row, text=" до",
                    text_color="#8b949e",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        self.typing_max_var = ctk.StringVar(value="100")
        ctk.CTkEntry(typing_row, textvariable=self.typing_max_var, width=45,
                    fg_color="#0d1117", border_color="#30363d",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(typing_row, text=" мс",
                    text_color="#8b949e",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(left_scroll,
                     text="Хаотичный ввод: залпы, паузы, раздумья — автоматически",
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color="#484f58").pack(padx=16, anchor="w", pady=(0, 6))

        # ── Лимит по возрасту в очереди ──
        ctk.CTkLabel(left_scroll, text="ЛИМИТ ВОЗРАСТА В ОЧЕРЕДИ (МИН)",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        age_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        age_row.pack(padx=12, fill="x", pady=(0, 2))
        self.max_age_var = ctk.StringVar(value="0")
        ctk.CTkEntry(age_row, textvariable=self.max_age_var, width=50,
                    fg_color="#0d1117", border_color="#30363d",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left")
        ctk.CTkLabel(age_row, text=" мин",
                    text_color="#8b949e",
                    font=ctk.CTkFont("Segoe UI", 9)).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(left_scroll, text="0 = отключено. Email старше N мин удаляются из очереди.",
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color="#484f58").pack(padx=16, anchor="w", pady=(0, 6))
        ctk.CTkLabel(left_scroll, text="ПРОФИЛИ DOLPHIN",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        self.profiles_box = ctk.CTkTextbox(left_scroll, height=70,
                                          font=ctk.CTkFont("Consolas", 9),
                                          fg_color="#0d1117", border_color="#30363d",
                                          border_width=1, text_color="#c9d1d9")
        self.profiles_box.pack(padx=12, fill="x")
        self.profiles_box.insert("end", "\n".join(self.profiles))

        btn_row = ctk.CTkFrame(left_scroll, fg_color="transparent")
        btn_row.pack(padx=12, fill="x", pady=(3, 6))
        ctk.CTkButton(btn_row, text="Загрузить", height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 9),
                      command=self._load_profiles_file).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ctk.CTkButton(btn_row, text="Сохранить", height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 9),
                      command=self._save_profiles).pack(side="left", fill="x", expand=True)

        self._divider(left_scroll)

        # ── Шаблоны ──
        ctk.CTkLabel(left_scroll, text="ШАБЛОН ПИСЬМА",
                     font=ctk.CTkFont("Segoe UI", 9, "bold"),
                     text_color="#8b949e").pack(padx=16, anchor="w", pady=(6, 2))

        self.template_var = ctk.StringVar(value=self.templates[0]["name"] if self.templates else "")
        self.template_menu = ctk.CTkOptionMenu(left_scroll,
                                              values=[t["name"] for t in self.templates],
                                              variable=self.template_var,
                                              fg_color="#21262d", button_color="#30363d",
                                              font=ctk.CTkFont("Segoe UI", 9),
                                              command=self._on_template_select)
        self.template_menu.pack(padx=12, fill="x", pady=(0, 3))

        ctk.CTkButton(left_scroll, text="+ Новый шаблон", height=24,
                      fg_color="transparent", hover_color="#21262d",
                      border_color="#30363d", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._new_template).pack(padx=12, fill="x", pady=(0, 6))

        self._divider(left_scroll)

        # ── Кнопки ──
        self.parser_start_btn = ctk.CTkButton(left_scroll, text="▶  Запустить парсер", height=36,
                                             fg_color="#238636", hover_color="#2ea043",
                                             font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                             command=self._start_parser)
        self.parser_start_btn.pack(padx=12, fill="x", pady=(0, 3))

        self.parser_stop_btn = ctk.CTkButton(left_scroll, text="⏹  Остановить", height=36,
                                            fg_color="#b62324", hover_color="#da3633",
                                            font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                            state="disabled",
                                            command=self._stop_parser)
        self.parser_stop_btn.pack(padx=12, fill="x", pady=(0, 3))



        # Кнопка открытия DEBUG папки
        ctk.CTkButton(left_scroll, text="📁 Открыть DEBUG логи", height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 9),
                      text_color="#58a6ff",
                      command=self._open_debug_folder).pack(padx=12, fill="x", pady=(0, 20))

        # ── Правая панель ─────────────────────
        right = ctk.CTkFrame(self, fg_color="#0f1117", corner_radius=0)
        right.pack(side="right", fill="both", expand=True)

        self.tabview = ctk.CTkTabview(right,
                                      fg_color="#161b22",
                                      segmented_button_fg_color="#0d1117",
                                      segmented_button_selected_color="#21262d",
                                      segmented_button_selected_hover_color="#30363d",
                                      text_color="#c9d1d9",
                                      border_color="#30363d",
                                      border_width=1)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        tab_parser = self.tabview.add("🔗  Парсер")
        self._build_parser_tab(tab_parser)

        tab_tpl = self.tabview.add("✏️  Шаблон")
        self._build_template_tab(tab_tpl)

        tab_reply = self.tabview.add("📧  Ответ")
        self._build_reply_tab(tab_reply)

        tab_html_editor = self.tabview.add("✨  HTML Шаблоны")
        self._build_html_editor_tab(tab_html_editor)

        tab_log = self.tabview.add("📋  Лог")
        self._build_log_tab(tab_log)

        if self.templates:
            self._on_template_select(self.templates[0]["name"])

    def _divider(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color="#30363d").pack(fill="x", padx=12, pady=6)

    def _build_parser_tab(self, parent):
        """Вкладка для парсера"""
        # Статус парсера
        status_frame = ctk.CTkFrame(parent, fg_color="#0d1117", corner_radius=8)
        status_frame.pack(fill="x", padx=4, pady=(4, 3))

        ctk.CTkLabel(status_frame, text="СТАТУС ПАРСЕРА",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=10, pady=(6, 1))

        self.parser_status_label = ctk.CTkLabel(status_frame, text="⏹ ОСТАНОВЛЕН",
                                               font=ctk.CTkFont("Segoe UI", 9),
                                               text_color="#8b949e")
        self.parser_status_label.pack(anchor="w", padx=10, pady=(0, 6))

        # Статистика
        stats_frame = ctk.CTkFrame(parent, fg_color="#0d1117", corner_radius=8)
        stats_frame.pack(fill="x", padx=4, pady=3)

        ctk.CTkLabel(stats_frame, text="СТАТИСТИКА",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=10, pady=(6, 3))

        stats_grid = ctk.CTkFrame(stats_frame, fg_color="transparent")
        stats_grid.pack(fill="x", padx=10, pady=(0, 6))

        self.parser_stats_labels = {}
        for i, (key, label) in enumerate([
            ("fetched", "Получено: "),
            ("in_queue", "В очереди: "),
            ("sent", "Отправлено: "),
            ("failed", "Ошибок: "),
            ("broken", "Нерабочих: "),
            ("skipped", "Пропущено дублей: "),
            ("time", "Время: "),
        ]):
            row = i // 2
            col = i % 2
            frame = ctk.CTkFrame(stats_grid, fg_color="transparent")
            frame.grid(row=row, column=col, sticky="w", padx=(0, 16), pady=2)

            ctk.CTkLabel(frame, text=label,
                        font=ctk.CTkFont("Segoe UI", 9),
                        text_color="#8b949e").pack(side="left")

            self.parser_stats_labels[key] = ctk.CTkLabel(frame, text="0",
                                                         font=ctk.CTkFont("Segoe UI", 9, "bold"),
                                                         text_color="#58a6ff")
            self.parser_stats_labels[key].pack(side="left")

        # Очередь email-ов
        ctk.CTkLabel(parent, text="ОЧЕРЕДЬ EMAIL-ОВ",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=4, pady=(6, 2))

        self.parser_queue_box = ctk.CTkTextbox(parent,
                                              font=ctk.CTkFont("Consolas", 9),
                                              fg_color="#0d1117",
                                              border_color="#30363d", border_width=1,
                                              text_color="#c9d1d9",
                                              state="disabled",
                                              height=120)
        self.parser_queue_box.pack(fill="x", padx=4, pady=(0, 3))

        # Лог
        ctk.CTkLabel(parent, text="ЛОГ ОПЕРАЦИЙ",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=4, pady=(3, 2))

        self.parser_log_box = ctk.CTkTextbox(parent,
                                            font=ctk.CTkFont("Consolas", 8),
                                            fg_color="#0d1117",
                                            border_color="#30363d", border_width=1,
                                            text_color="#c9d1d9",
                                            state="disabled")
        self.parser_log_box.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Кнопка очистки
        ctk.CTkButton(parent, text="Очистить лог", height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 9),
                      command=self._clear_parser_log).pack(padx=4, pady=(0, 4))

    def _build_template_tab(self, parent):
        ctk.CTkLabel(parent, text="Тема письма:",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color="#8b949e").pack(anchor="w", padx=4, pady=(8, 2))

        self.subject_entry = ctk.CTkEntry(parent, height=32,
                                          fg_color="#0d1117", border_color="#30363d",
                                          font=ctk.CTkFont("Segoe UI", 11),
                                          text_color="#c9d1d9")
        self.subject_entry.pack(fill="x", padx=4, pady=(0, 10))

        ctk.CTkLabel(parent, text="Текст письма:",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color="#8b949e").pack(anchor="w", padx=4, pady=(0, 2))

        self.body_box = ctk.CTkTextbox(parent,
                                       font=ctk.CTkFont("Consolas", 11),
                                       fg_color="#0d1117",
                                       border_color="#30363d", border_width=1,
                                       text_color="#c9d1d9")
        self.body_box.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(row, text="💾  Сохранить", height=30,
                      fg_color="#1f6feb", hover_color="#388bfd",
                      font=ctk.CTkFont("Segoe UI", 10),
                      command=self._save_current_template).pack(side="left", padx=(0, 3))

        ctk.CTkButton(row, text="🗑  Удалить", height=30,
                      fg_color="#21262d", hover_color="#b62324",
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#f85149",
                      command=self._delete_template).pack(side="left", padx=(0, 3))

        ctk.CTkButton(row, text="👁  Предпросмотр", height=30,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#c9d1d9",
                      command=self._preview_spintax).pack(side="right")

    def _build_log_tab(self, parent):
        self.log_box = ctk.CTkTextbox(parent,
                                      font=ctk.CTkFont("Consolas", 10),
                                      fg_color="#0d1117",
                                      border_color="#30363d", border_width=1,
                                      text_color="#c9d1d9",
                                      state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=4, pady=(8, 4))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(row, text="Очистить лог", height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 9),
                      command=self._clear_log).pack(side="left")

    # ── HTML редактор ─────────────────────────────

    def _open_html_editor(self):
        """Открыть редактор HTML-шаблонов ответных писем."""
        editor = HtmlTemplateEditor(self)
        editor.grab_set()
        editor.focus_force()

    # ── Вкладка «Ответ» ───────────────────────────

    def _build_reply_tab(self, parent):
        """
        Вкладка генерации HTML-ответа:
        1. Пользователь вводит email ответившего
        2. Выбирает платформу
        3. Нажимает «Найти и сгенерировать»
        4. Получает готовый HTML для ответа
        """
        # ── Заголовок ──
        ctk.CTkLabel(parent, text="Генерация HTML-ответа",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color="#58a6ff").pack(anchor="w", padx=6, pady=(10, 2))
        ctk.CTkLabel(parent,
                     text="Введи email из ответа → софт найдёт объявление в парсере, "
                          "сгенерирует ссылку через API и выдаст готовый HTML",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e",
                     wraplength=480, justify="left").pack(anchor="w", padx=6, pady=(0, 8))

        # ── Email + Платформа ──
        input_frame = ctk.CTkFrame(parent, fg_color="#161b22", corner_radius=8)
        input_frame.pack(fill="x", padx=4, pady=(0, 6))

        ctk.CTkLabel(input_frame, text="Email ответившего:",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#8b949e").pack(anchor="w", padx=12, pady=(10, 2))

        self.reply_email_entry = ctk.CTkEntry(
            input_frame, height=34,
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color="#c9d1d9",
            placeholder_text="seller@example.com"
        )
        self.reply_email_entry.pack(fill="x", padx=12, pady=(0, 8))

        # ── Сервис-код (всё в одном) ──
        sc_row = ctk.CTkFrame(input_frame, fg_color="transparent")
        sc_row.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkLabel(sc_row, text="Сервис-код:",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#8b949e", width=100, anchor="w").pack(side="left")

        self.reply_service_code_var = ctk.StringVar(value="")
        sc_entry = ctk.CTkEntry(
            sc_row,
            textvariable=self.reply_service_code_var,
            height=34,
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 12),
            text_color="#79c0ff",
            width=180,
            placeholder_text="vinted_it"
        )
        sc_entry.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(sc_row,
                     text="Необязательно — берётся из базы автоматически\n"
                          "Заполни только если нужно переопределить шаблон",
                     font=ctk.CTkFont("Segoe UI", 8),
                     text_color="#484f58",
                     justify="left").pack(side="left")

        # ── Кнопка: Отправить ответ ──
        ctk.CTkButton(
            input_frame,
            text="📩  Найти и отправить ответ",
            height=36,
            fg_color="#238636", hover_color="#2ea043",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._send_reply_in_thread,
        ).pack(fill="x", padx=12, pady=(0, 4))

        # ── Кнопка: Сгенерировать HTML ──
        ctk.CTkButton(
            input_frame,
            text="🔍  Найти и сгенерировать HTML",
            height=36,
            fg_color="#1f6feb", hover_color="#388bfd",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._generate_reply_html,
        ).pack(fill="x", padx=12, pady=(0, 4))

        # ── Пакетная отправка ответов ─────────────────
        ctk.CTkLabel(input_frame, text="📦 Пакетная отправка (несколько email):",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#d2a8ff").pack(anchor="w", padx=12, pady=(4, 2))

        self.batch_emails_box = ctk.CTkTextbox(
            input_frame, height=60,
            font=ctk.CTkFont("Consolas", 9),
            fg_color="#0d1117",
            border_color="#30363d", border_width=1,
            text_color="#c9d1d9",
        )
        self.batch_emails_box.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkButton(
            input_frame,
            text="📩  Пакетная отправка ответов",
            height=36,
            fg_color="#8957e5", hover_color="#a371f7",
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._batch_send_replies,
        ).pack(fill="x", padx=12, pady=(0, 4))

        self.batch_status_label = ctk.CTkLabel(
            input_frame, text="",
            font=ctk.CTkFont("Segoe UI", 8),
            text_color="#8b949e",
            wraplength=440, justify="left",
        )
        self.batch_status_label.pack(anchor="w", padx=12, pady=(0, 8))

        # ── Статус / данные объявления ──
        self.reply_status_label = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color="#8b949e",
            wraplength=480, justify="left",
        )
        self.reply_status_label.pack(anchor="w", padx=8, pady=(2, 4))

        # Найденные данные
        self.reply_data_frame = ctk.CTkFrame(parent, fg_color="#161b22", corner_radius=8)
        self.reply_data_frame.pack(fill="x", padx=4, pady=(0, 4))
        self.reply_data_labels = {}
        fields = [
            ("seller_name",  "Продавец"),
            ("product_name", "Товар"),
            ("price",        "Цена"),
            ("address",      "Локация"),
            ("platform",     "Платформа"),
            ("link",         "Ссылка (Link API)"),
        ]
        grid = ctk.CTkFrame(self.reply_data_frame, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=8)
        for i, (key, label) in enumerate(fields):
            ctk.CTkLabel(grid, text=f"{label}:",
                         font=ctk.CTkFont("Segoe UI", 9),
                         text_color="#8b949e", width=120, anchor="w").grid(
                row=i, column=0, sticky="w", pady=1)
            lbl = ctk.CTkLabel(grid, text="—",
                               font=ctk.CTkFont("Segoe UI", 9, "bold"),
                               text_color="#c9d1d9", anchor="w", wraplength=320)
            lbl.grid(row=i, column=1, sticky="w", padx=(6, 0), pady=1)
            self.reply_data_labels[key] = lbl

        # ── HTML результат ──
        ctk.CTkLabel(parent, text="Готовый HTML:",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=8, pady=(4, 2))

        self.reply_html_box = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont("Consolas", 9),
            fg_color="#0d1117",
            border_color="#30363d", border_width=1,
            text_color="#c9d1d9",
        )
        self.reply_html_box.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=4, pady=(0, 6))

        ctk.CTkButton(btn_row, text="📋  Копировать HTML", height=30,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#c9d1d9",
                      command=self._copy_reply_html).pack(side="left", padx=(0, 4))

        ctk.CTkButton(btn_row, text="🌐  Открыть в браузере", height=30,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#58a6ff",
                      command=self._open_reply_in_browser).pack(side="left", padx=(0, 4))

        ctk.CTkButton(btn_row, text="✨  Редактор шаблонов", height=30,
                      fg_color="#0d2a0d", hover_color="#1a3d1a",
                      border_color="#3fb950", border_width=1,
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#3fb950",
                      command=self._open_html_editor).pack(side="right")

    def _build_html_editor_tab(self, parent):
        """Встроенная вкладка редактора HTML-шаблонов ответных писем"""
        from html_template_editor import load_templates, save_templates, render_template, PREVIEW_DATA, DEFAULT_TEMPLATE

        # ── Заголовок ──
        ctk.CTkLabel(parent, text="✨ Редактор HTML-шаблонов ответов",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color="#d2a8ff").pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkLabel(parent,
                     text="Сервис-код = ключ шаблона (vinted_it, subito_it...). "
                          "Переменные: {{seller_name}}, {{product_name}}, {{price}}, {{photo}}, {{link}} и др.",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#8b949e", wraplength=700,
                     justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # ── Выбор шаблона ──
        top_row = ctk.CTkFrame(parent, fg_color="transparent")
        top_row.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(top_row, text="Сервис-код:",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#8b949e").pack(side="left", padx=(0, 6))

        self._ht_code_var = ctk.StringVar(value="")
        self._ht_code_entry = ctk.CTkEntry(
            top_row, textvariable=self._ht_code_var,
            width=180, height=30,
            fg_color="#0d1117", border_color="#30363d",
            font=ctk.CTkFont("Consolas", 11),
            text_color="#79c0ff",
            placeholder_text="vinted_it"
        )
        self._ht_code_entry.pack(side="left", padx=(0, 8))

        # Список существующих шаблонов
        templates = load_templates()
        keys = list(templates.keys()) if templates else []

        self._ht_list_var = ctk.StringVar(value="")
        self._ht_list_menu = ctk.CTkOptionMenu(
            top_row, values=keys or ["(нет шаблонов)"],
            variable=self._ht_list_var,
            fg_color="#21262d", button_color="#30363d",
            font=ctk.CTkFont("Segoe UI", 9),
            width=180,
            command=self._ht_load_selected,
        )
        self._ht_list_menu.pack(side="left", padx=(0, 8))

        ctk.CTkButton(top_row, text="🗑 Удалить", height=28,
                      fg_color="#b62324", hover_color="#da3633",
                      font=ctk.CTkFont("Segoe UI", 9),
                      command=self._ht_delete_template).pack(side="right")

        # ── Редактор HTML ──
        self._ht_editor = ctk.CTkTextbox(
            parent, height=300,
            font=ctk.CTkFont("Consolas", 10),
            fg_color="#0d1117",
            border_color="#30363d", border_width=1,
            text_color="#c9d1d9",
        )
        self._ht_editor.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # Вставить дефолтный шаблон если редактор пуст
        if not keys:
            self._ht_editor.insert("1.0", DEFAULT_TEMPLATE)

        # ── Кнопки ──
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkButton(btn_row, text="💾  Сохранить шаблон", height=34,
                      fg_color="#238636", hover_color="#2ea043",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._ht_save_template).pack(side="left", padx=(0, 4))

        ctk.CTkButton(btn_row, text="🌐  Предпросмотр", height=34,
                      fg_color="#1f6feb", hover_color="#388bfd",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._ht_preview).pack(side="left", padx=(0, 4))

        ctk.CTkButton(btn_row, text="📋  Вставить стандартный", height=34,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color="#c9d1d9",
                      command=lambda: (
                          self._ht_editor.delete("1.0", "end"),
                          self._ht_editor.insert("1.0", DEFAULT_TEMPLATE),
                      )).pack(side="left")

        # Статус
        self._ht_status = ctk.CTkLabel(parent, text="",
                                        font=ctk.CTkFont("Segoe UI", 9),
                                        text_color="#8b949e")
        self._ht_status.pack(anchor="w", padx=8, pady=(0, 6))

    def _ht_load_selected(self, name):
        """Загрузить выбранный шаблон в редактор"""
        from html_template_editor import load_templates
        templates = load_templates()
        html = templates.get(name, "")
        self._ht_code_var.set(name)
        self._ht_editor.delete("1.0", "end")
        self._ht_editor.insert("1.0", html)
        self._ht_status.configure(text=f"✅ Загружен: {name}", text_color="#3fb950")

    def _ht_save_template(self):
        """Сохранить текущий шаблон"""
        from html_template_editor import load_templates, save_templates
        code = self._ht_code_var.get().strip().lower()
        if not code:
            self._ht_status.configure(text="❌ Введи сервис-код!", text_color="#f85149")
            return
        html = self._ht_editor.get("1.0", "end-1c").strip()
        if not html:
            self._ht_status.configure(text="❌ HTML пустой!", text_color="#f85149")
            return
        templates = load_templates()
        templates[code] = html
        save_templates(templates)
        # Обновить dropdown
        keys = list(templates.keys())
        self._ht_list_menu.configure(values=keys)
        self._ht_list_var.set(code)
        self._ht_status.configure(text=f"💾 Сохранён: {code}", text_color="#3fb950")

    def _ht_delete_template(self):
        """Удалить выбранный шаблон"""
        from html_template_editor import load_templates, save_templates
        code = self._ht_code_var.get().strip().lower()
        if not code:
            return
        templates = load_templates()
        if code in templates:
            del templates[code]
            save_templates(templates)
            keys = list(templates.keys())
            self._ht_list_menu.configure(values=keys or ["(нет шаблонов)"])
            self._ht_editor.delete("1.0", "end")
            self._ht_code_var.set("")
            self._ht_status.configure(text=f"🗑 Удалён: {code}", text_color="#d29922")

    def _ht_preview(self):
        """Открыть предпросмотр HTML в браузере"""
        from html_template_editor import render_template, PREVIEW_DATA
        import tempfile, webbrowser
        html = self._ht_editor.get("1.0", "end-1c").strip()
        if not html:
            self._ht_status.configure(text="⚠️ Нет HTML", text_color="#d29922")
            return
        rendered = render_template(html, PREVIEW_DATA)
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(rendered)
            webbrowser.open(f"file:///{f.name}")
        self._ht_status.configure(text="🌐 Предпросмотр открыт", text_color="#3fb950")

    def _copy_reply_html(self):
        """Копировать HTML из текстового поля в буфер обмена"""
        html = self.reply_html_box.get("1.0", "end-1c").strip()
        if html:
            self.clipboard_clear()
            self.clipboard_append(html)
            self.reply_status_label.configure(
                text="📋 HTML скопирован в буфер!", text_color="#3fb950"
            )
        else:
            self.reply_status_label.configure(
                text="⚠️ Нет HTML для копирования", text_color="#d29922"
            )

    def _open_reply_in_browser(self):
        """Открыть сгенерированный HTML в браузере для предпросмотра"""
        import tempfile
        import webbrowser
        html = self.reply_html_box.get("1.0", "end-1c").strip()
        if not html:
            self.reply_status_label.configure(
                text="⚠️ Нет HTML для просмотра", text_color="#d29922"
            )
            return
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
                f.write(html)
                webbrowser.open(f"file:///{f.name}")
            self.reply_status_label.configure(
                text="🌐 Открыто в браузере!", text_color="#3fb950"
            )
        except Exception as e:
            self.reply_status_label.configure(
                text=f"❌ {str(e)[:60]}", text_color="#f85149"
            )

    def _open_html_editor(self):
        """Открыть окно редактора HTML-шаблонов"""
        from html_template_editor import HtmlTemplateEditor
        try:
            if hasattr(self, '_editor_window') and self._editor_window.winfo_exists():
                self._editor_window.focus()
                return
        except Exception:
            pass
        self._editor_window = HtmlTemplateEditor(self)

    def _generate_reply_html(self):
        """
        Ищет email в локальной базе (conversations.json) и генерирует HTML.
        Парсер не используется — данные берутся из базы, которая заполняется
        автоматически при каждой успешной отправке рассылки.
        """
        import threading

        email = self.reply_email_entry.get().strip().lower()
        if not email:
            self.reply_status_label.configure(
                text="❌ Введи email!", text_color="#f85149"
            )
            return

        service_code_override = self.reply_service_code_var.get().strip()

        # Очищаем старый результат
        for lbl in self.reply_data_labels.values():
            lbl.configure(text="—", text_color="#c9d1d9")
        self.reply_html_box.delete("1.0", "end")

        # ── Ищем только в локальной базе ──
        record = self._conversation_store.get(email)
        if not record:
            self.reply_status_label.configure(
                text=f"⚠️ '{email}' не найден в базе. Убедись что рассылка на этот email уже была отправлена.",
                text_color="#d29922"
            )
            return

        # Сервис-код: приоритет → поле ввода → база
        service_code = service_code_override or record.get("service_code", "")
        if not service_code:
            self.reply_status_label.configure(
                text="❌ Сервис-код не найден ни в поле ввода, ни в базе!",
                text_color="#f85149"
            )
            return

        parts = service_code.split("_", 1)
        platform = parts[0]

        self.reply_status_label.configure(
            text=f"✅ Найдено в базе! Сервис-код: {service_code}", text_color="#3fb950"
        )

        record["service_code"] = service_code
        record["platform"]     = record.get("platform") or platform

        threading.Thread(
            target=self._finish_generate_html,
            args=(email, service_code, platform, record),
            daemon=True
        ).start()

    def _finish_generate_html(self, email: str, service_code: str, platform: str, found: dict):
        """
        Общий финальный шаг: генерация ссылки + рендер HTML + обновление UI.
        Вызывается и из локальной БД и после поиска в парсере.
        """
        # ── Генерация ссылки через Link API ──
        link      = ""
        link_note = ""
        try:
            link_cfg = {}
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    link_cfg = json.load(f)

            found_with_sc = dict(found)
            found_with_sc["service_code"] = service_code

            active_api = link_cfg.get("active_api", "receiveolxiv")

            if active_api == "monkeyteam":
                from link_generator import MonkeyTeamLinkGenerator
                lg = MonkeyTeamLinkGenerator(
                    bearer_token=link_cfg.get("monkeyteam_token", ""),
                    template_id=link_cfg.get("monkeyteam_template_id", 0),
                )
            elif active_api == "goo_network":
                from link_generator import GooNetworkLinkGenerator
                lg = GooNetworkLinkGenerator(
                    user_api_key=link_cfg.get("goo_network_api_key", ""),
                    team_key=link_cfg.get("goo_network_team_key", ""),
                    profile_id=link_cfg.get("goo_network_profile_id", ""),
                )
            else:
                from link_generator import LinkGenerator
                lg = LinkGenerator(
                    user_id=link_cfg.get("user_id", ""),
                    api_key=link_cfg.get("api_key", ""),
                )

            if lg.is_configured():
                link = lg.generate(found_with_sc)
            else:
                link_note = f"⚠️ Link API ({active_api}): ключи не заполнены — используется оригинальная ссылка"
        except RuntimeError as e:
            link_note = f"⚠️ {e}"
            link = ""
        except Exception as e:
            link_note = f"⚠️ Ошибка Link API: {str(e)[:120]}"
            link = ""

        # ── Рендер HTML ──
        data = {
            "seller_name":  found.get("seller_name", ""),
            "product_name": found.get("product_name", ""),
            "price":        found.get("price", ""),
            "photo":        found.get("photo", ""),
            "address":      found.get("address", ""),
            "platform":     found.get("platform", platform).capitalize(),
            "email":        email,
            "link":         link or found.get("ad_url", ""),
        }
        html = get_template(service_code, data)

        def _update_ui():
            self.reply_data_labels["seller_name"].configure(
                text=data["seller_name"] or "—")
            self.reply_data_labels["product_name"].configure(
                text=data["product_name"] or "—")
            self.reply_data_labels["price"].configure(
                text=data["price"] or "—")
            self.reply_data_labels["address"].configure(
                text=data["address"] or "—")
            self.reply_data_labels["platform"].configure(
                text=data["platform"])
            self.reply_data_labels["link"].configure(
                text=data["link"] or "—",
                text_color="#58a6ff" if data["link"] else "#c9d1d9")

            if html:
                self.reply_html_box.delete("1.0", "end")
                self.reply_html_box.insert("1.0", html)
                if link_note:
                    self.reply_status_label.configure(
                        text=link_note,
                        text_color="#d29922"
                    )
                else:
                    self.reply_status_label.configure(
                        text=f"✅ HTML сгенерирован для {email}",
                        text_color="#3fb950"
                    )
            else:
                self.reply_html_box.delete("1.0", "end")
                self.reply_html_box.insert("1.0", "⚠️ Шаблон для платформы не задан.\n"
                                                  "Открой редактор шаблонов и создай шаблон.")
                self.reply_status_label.configure(
                    text=f"⚠️ Данные найдены, но шаблон для '{platform}' не задан.",
                    text_color="#d29922"
                )

        self.after(0, _update_ui)

    def _copy_reply_html(self):
        """Копировать HTML в буфер обмена."""
        html = self.reply_html_box.get("1.0", "end-1c").strip()
        if not html:
            return
        self.clipboard_clear()
        self.clipboard_append(html)
        self.reply_status_label.configure(
            text="📋 HTML скопирован в буфер обмена!", text_color="#3fb950"
        )

    def _open_reply_in_browser(self):
        """Открыть сгенерированный HTML в браузере."""
        import tempfile, webbrowser
        html = self.reply_html_box.get("1.0", "end-1c").strip()
        if not html:
            return
        tmp = os.path.join(tempfile.gettempdir(), "reply_preview.html")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        webbrowser.open(f"file:///{tmp.replace(chr(92), '/')}")

    # ── Отправка ответа в тред ────────────────

    def _send_reply_in_thread(self):
        """
        Найти профиль по email → приостановить рассылку →
        найти тред в Gmail → ответить HTML → вернуть inbox →
        возобновить рассылку.
        """
        email = self.reply_email_entry.get().strip().lower()
        if not email:
            self.reply_status_label.configure(
                text="❌ Введи email!", text_color="#f85149"
            )
            return

        service_code_override = self.reply_service_code_var.get().strip()

        # ── Ищем запись в базе ──
        record = self._conversation_store.get(email)
        if not record:
            self.reply_status_label.configure(
                text=f"⚠️ '{email}' не найден в базе. Рассылка на этот email ещё не была.",
                text_color="#d29922"
            )
            return

        # Сервис-код: приоритет → поле ввода → база
        service_code = service_code_override or record.get("service_code", "")
        if not service_code:
            self.reply_status_label.configure(
                text="❌ Сервис-код не найден ни в поле ввода, ни в базе!",
                text_color="#f85149"
            )
            return

        profile_id = record.get("profile_id", "")
        if not profile_id:
            self.reply_status_label.configure(
                text="❌ В базе нет profile_id для этого email!", text_color="#f85149"
            )
            return

        # ── Проверяем что driver доступен ──
        with self._drivers_lock:
            driver = self._active_drivers.get(profile_id)
        if driver is None:
            self.reply_status_label.configure(
                text=f"❌ Профиль #{profile_id} не запущен. Запусти рассылку или прогрей профиль.",
                text_color="#f85149"
            )
            return

        parts = service_code.split("_", 1)
        platform = parts[0]
        record["service_code"] = service_code
        record["platform"] = record.get("platform") or platform

        self.reply_status_label.configure(
            text=f"⏳ Генерирую HTML и отправляю через профиль #{profile_id}...",
            text_color="#d29922"
        )

        # Запускаем в фоновом потоке чтобы UI не зависал
        threading.Thread(
            target=self._do_send_reply,
            args=(email, service_code, platform, record, profile_id, driver),
            daemon=True
        ).start()

    def _do_send_reply(self, email: str, service_code: str, platform: str,
                       found: dict, profile_id: str, driver):
        """
        Фоновый поток: генерация HTML + пауза профиля + reply в Gmail + возобновление.
        """
        try:
            # ── 1. Генерация ссылки через Link API ──
            link      = ""
            link_note = ""
            try:
                link_cfg = {}
                if os.path.exists("link_api_config.json"):
                    with open("link_api_config.json", encoding="utf-8") as f:
                        link_cfg = json.load(f)

                found_with_sc = dict(found)
                found_with_sc["service_code"] = service_code

                active_api = link_cfg.get("active_api", "receiveolxiv")

                if active_api == "monkeyteam":
                    from link_generator import MonkeyTeamLinkGenerator
                    lg = MonkeyTeamLinkGenerator(
                        bearer_token=link_cfg.get("monkeyteam_token", ""),
                        template_id=link_cfg.get("monkeyteam_template_id", 0),
                    )
                elif active_api == "goo_network":
                    from link_generator import GooNetworkLinkGenerator
                    lg = GooNetworkLinkGenerator(
                        user_api_key=link_cfg.get("goo_network_api_key", ""),
                        team_key=link_cfg.get("goo_network_team_key", ""),
                        profile_id=link_cfg.get("goo_network_profile_id", ""),
                    )
                else:
                    from link_generator import LinkGenerator
                    lg = LinkGenerator(
                        user_id=link_cfg.get("user_id", ""),
                        api_key=link_cfg.get("api_key", ""),
                    )

                if lg.is_configured():
                    link = lg.generate(found_with_sc)
                else:
                    link_note = f"⚠️ Link API ({active_api}): ключи не заполнены"
            except RuntimeError as e:
                link_note = f"⚠️ {e}"
                link = ""
            except Exception as e:
                link_note = f"⚠️ Ошибка Link API: {str(e)[:120]}"
                link = ""

            if link_note:
                self.after(0, lambda n=link_note: self.reply_status_label.configure(
                    text=n, text_color="#d29922"
                ))

            # ── 2. Рендер HTML шаблона ──
            data = {
                "seller_name":  found.get("seller_name", ""),
                "product_name": found.get("product_name", ""),
                "price":        found.get("price", ""),
                "photo":        found.get("photo", ""),
                "address":      found.get("address", ""),
                "platform":     found.get("platform", platform).capitalize(),
                "email":        email,
                "link":         link or found.get("ad_url", ""),
            }
            html = get_template(service_code, data)

            if not html:
                self.after(0, lambda: self.reply_status_label.configure(
                    text=f"⚠️ Шаблон для '{service_code}' не задан. Открой редактор шаблонов.",
                    text_color="#d29922"
                ))
                return

            # Обновляем UI с данными
            def _show_data():
                self.reply_data_labels["seller_name"].configure(
                    text=data["seller_name"] or "—")
                self.reply_data_labels["product_name"].configure(
                    text=data["product_name"] or "—")
                self.reply_data_labels["price"].configure(
                    text=data["price"] or "—")
                self.reply_data_labels["address"].configure(
                    text=data["address"] or "—")
                self.reply_data_labels["platform"].configure(
                    text=data["platform"])
                self.reply_data_labels["link"].configure(
                    text=data["link"] or "—",
                    text_color="#58a6ff" if data["link"] else "#c9d1d9")
                self.reply_html_box.delete("1.0", "end")
                self.reply_html_box.insert("1.0", html)
                self.reply_status_label.configure(
                    text=f"⏳ HTML готов. Приостанавливаю профиль #{profile_id}...",
                    text_color="#d29922"
                )
            self.after(0, _show_data)

            # ── 3. Приостановить рассылку в этом профиле ──
            self.after(0, lambda: self.reply_status_label.configure(
                text=f"⏸ Жду завершения текущей отправки профиля #{profile_id}...",
                text_color="#d29922"
            ))

            paused = self.background_worker.pause_profile(profile_id, timeout=60)
            if not paused:
                self.after(0, lambda: self.reply_status_label.configure(
                    text=f"❌ Не удалось приостановить профиль #{profile_id} (таймаут 60с)",
                    text_color="#f85149"
                ))
                return

            self.after(0, lambda: self.reply_status_label.configure(
                text=f"📩 Профиль #{profile_id} приостановлен. Отправляю ответ...",
                text_color="#d29922"
            ))

            # ── 4. Отправить ответ через Gmail ──
            try:
                reply_in_gmail_thread(driver, email, html)

                # Успех!
                self._conversation_store.mark_replied_back(email)

                self.after(0, lambda: self.reply_status_label.configure(
                    text=f"✅ Ответ отправлен на {email} через профиль #{profile_id}!",
                    text_color="#3fb950"
                ))
            except Exception as e:
                error = str(e)[:120]
                self.after(0, lambda: self.reply_status_label.configure(
                    text=f"❌ Ошибка при отправке ответа: {error}",
                    text_color="#f85149"
                ))
            finally:
                # ── 5. ВСЕГДА возобновить рассылку ──
                self.background_worker.resume_profile(profile_id)
                self.after(0, lambda: self._parser_log(
                    f"▶ Профиль #{profile_id}: рассылка возобновлена после ответа"
                ))

        except Exception as e:
            error = str(e)[:150]
            self.after(0, lambda: self.reply_status_label.configure(
                text=f"❌ Критическая ошибка: {error}",
                text_color="#f85149"
            ))
            # Безопасное возобновление
            try:
                self.background_worker.resume_profile(profile_id)
            except Exception:
                pass

    # ── Пакетная отправка ответов ────────────────

    def _batch_send_replies(self):
        """
        Парсит email-ы из текстового поля, группирует по профилям,
        последовательно отправляет ответы.
        """
        raw = self.batch_emails_box.get("1.0", "end-1c").strip()
        if not raw:
            self.batch_status_label.configure(
                text="❌ Вставь email-ы (по одному на строку)!", text_color="#f85149"
            )
            return

        service_code_override = self.reply_service_code_var.get().strip()

        # Парсим email-ы (убираем пустые, дубли, пробелы)
        emails = []
        seen = set()
        for line in raw.splitlines():
            e = line.strip().lower()
            if e and "@" in e and e not in seen:
                emails.append(e)
                seen.add(e)

        if not emails:
            self.batch_status_label.configure(
                text="❌ Не найдено ни одного email!", text_color="#f85149"
            )
            return

        self.batch_status_label.configure(
            text=f"⏳ Подготовка: {len(emails)} email-ов...", text_color="#d29922"
        )

        threading.Thread(
            target=self._do_batch_send,
            args=(emails, service_code_override),
            daemon=True
        ).start()

    def _do_batch_send(self, emails: list, service_code_override: str):
        """
        Фоновый поток: группировка по профилям → для каждого профиля:
        одна пауза → все ответы последовательно → одно возобновление.
        """
        from collections import defaultdict

        # service_code_override может быть пустым — тогда берём из базы каждого email

        # ── 1. Найти записи и сгруппировать по профилям ──
        groups = defaultdict(list)  # {profile_id: [(email, record), ...]}
        not_found = []
        no_driver = []

        for email in emails:
            record = self._conversation_store.get(email)
            if not record:
                not_found.append(email)
                continue
            # Определяем service_code для этого email
            sc = service_code_override or record.get("service_code", "")
            if not sc:
                not_found.append(email)
                continue
            pid = record.get("profile_id", "")
            if not pid:
                not_found.append(email)
                continue
            with self._drivers_lock:
                driver = self._active_drivers.get(pid)
            if driver is None:
                no_driver.append(email)
                continue
            groups[pid].append((email, record, sc))

        total = sum(len(v) for v in groups.values())
        skipped_msgs = []
        if not_found:
            skipped_msgs.append(f"не в базе: {', '.join(not_found)}")
        if no_driver:
            skipped_msgs.append(f"профиль не запущен: {', '.join(no_driver)}")

        if total == 0:
            skip_text = "; ".join(skipped_msgs) if skipped_msgs else "нет подходящих"
            self.after(0, lambda: self.batch_status_label.configure(
                text=f"❌ Нечего отправлять ({skip_text})", text_color="#f85149"
            ))
            return

        self.after(0, lambda: self.batch_status_label.configure(
            text=f"⏳ Отправка 0/{total} | Профилей: {len(groups)}"
                 + (f" | Пропущено: {len(not_found) + len(no_driver)}" if skipped_msgs else ""),
            text_color="#d29922"
        ))

        sent = 0
        failed = 0

        # ── 2. Для каждого профиля: пауза → ответы → возобновление ──
        for profile_id, email_records in groups.items():
            with self._drivers_lock:
                driver = self._active_drivers.get(profile_id)
            if driver is None:
                failed += len(email_records)
                continue

            # Приостановить рассылку
            self.after(0, lambda pid=profile_id: self.batch_status_label.configure(
                text=f"⏸ Жду завершения отправки профиля #{pid}...",
                text_color="#d29922"
            ))
            paused = self.background_worker.pause_profile(profile_id, timeout=60)
            if not paused:
                failed += len(email_records)
                self.after(0, lambda pid=profile_id: self._parser_log(
                    f"⚠️ Не удалось приостановить #{pid} для пакетных ответов"
                ))
                continue

            try:
                for email, record, sc in email_records:
                    # Обновляем статус
                    current = sent + failed + 1
                    self.after(0, lambda e=email, c=current, t=total, pid=profile_id:
                        self.batch_status_label.configure(
                            text=f"📩 {c}/{t} | #{pid} → {e}",
                            text_color="#d29922"
                        ))

                    try:
                        parts = sc.split("_", 1)
                        platform = parts[0]

                        # Генерация ссылки
                        link = ""
                        try:
                            link_cfg = {}
                            if os.path.exists("link_api_config.json"):
                                with open("link_api_config.json", encoding="utf-8") as f:
                                    link_cfg = json.load(f)

                            found_with_sc = dict(record)
                            found_with_sc["service_code"] = sc

                            active_api = link_cfg.get("active_api", "receiveolxiv")

                            if active_api == "monkeyteam":
                                from link_generator import MonkeyTeamLinkGenerator
                                lg = MonkeyTeamLinkGenerator(
                                    bearer_token=link_cfg.get("monkeyteam_token", ""),
                                    template_id=link_cfg.get("monkeyteam_template_id", 0),
                                )
                            elif active_api == "goo_network":
                                from link_generator import GooNetworkLinkGenerator
                                lg = GooNetworkLinkGenerator(
                                    user_api_key=link_cfg.get("goo_network_api_key", ""),
                                    team_key=link_cfg.get("goo_network_team_key", ""),
                                    profile_id=link_cfg.get("goo_network_profile_id", ""),
                                )
                            else:
                                from link_generator import LinkGenerator
                                lg = LinkGenerator(
                                    user_id=link_cfg.get("user_id", ""),
                                    api_key=link_cfg.get("api_key", ""),
                                )

                            if lg.is_configured():
                                link = lg.generate(found_with_sc)
                            else:
                                self.after(0, lambda a=active_api: self._parser_log(
                                    f"⚠️ Link API ({a}): ключи не заполнены — используется оригинальная ссылка"
                                ))
                        except RuntimeError as e:
                            link = ""
                            self.after(0, lambda msg=str(e): self._parser_log(f"⚠️ {msg}"))
                        except Exception as e:
                            link = ""
                            self.after(0, lambda msg=str(e)[:120]: self._parser_log(
                                f"⚠️ Ошибка Link API: {msg}"
                            ))

                        # Рендер HTML
                        data = {
                            "seller_name":  record.get("seller_name", ""),
                            "product_name": record.get("product_name", ""),
                            "price":        record.get("price", ""),
                            "photo":        record.get("photo", ""),
                            "address":      record.get("address", ""),
                            "platform":     record.get("platform", platform).capitalize(),
                            "email":        email,
                            "link":         link or record.get("ad_url", ""),
                        }
                        html = get_template(sc, data)

                        if not html:
                            failed += 1
                            self.after(0, lambda e=email, s=sc: self._parser_log(
                                f"⚠️ Шаблон для '{s}' не задан — пропуск {e}"
                            ))
                            continue

                        # Отправка ответа
                        reply_in_gmail_thread(driver, email, html)
                        self._conversation_store.mark_replied_back(email)
                        sent += 1

                        self.after(0, lambda e=email, s=sent, t=total:
                            self.batch_status_label.configure(
                                text=f"✅ {s}/{t} отправлено | Последний: {e}",
                                text_color="#3fb950"
                            ))

                    except Exception as e:
                        failed += 1
                        error = str(e)[:80]
                        self.after(0, lambda em=email, err=error:
                            self._parser_log(f"❌ Ответ на {em}: {err}"))

                    # Пауза между ответами (чтобы Gmail не заподозрил)
                    time.sleep(random.uniform(2.0, 4.0))

            finally:
                # ВСЕГДА возобновить рассылку
                self.background_worker.resume_profile(profile_id)
                self.after(0, lambda pid=profile_id: self._parser_log(
                    f"▶ #{pid}: рассылка возобновлена после пакетных ответов"
                ))

        # ── 3. Финальный статус ──
        skip_count = len(not_found) + len(no_driver)
        skip_details = ""
        if not_found:
            skip_details += f" | Не в базе ({len(not_found)}): {', '.join(not_found[:3])}"
            if len(not_found) > 3:
                skip_details += f"... +{len(not_found)-3}"
        if no_driver:
            skip_details += f" | Профиль не запущен ({len(no_driver)}): {', '.join(no_driver[:3])}"
        skip_info = f" | Пропущено: {skip_count}" if skip_count else ""
        final_text = f"✅ Пакетная отправка завершена: {sent} отправлено, {failed} ошибок{skip_info}"
        final_color = "#3fb950" if failed == 0 else "#d29922"
        self.after(0, lambda: self.batch_status_label.configure(
            text=final_text, text_color=final_color
        ))
        # Логируем детали пропуска
        if skip_details:
            self.after(0, lambda: self._parser_log(f"ℹ️ Пропущены{skip_details}"))

    def _toggle_rx_apikey_visibility(self):
        if self.rx_apikey_entry.cget("show") == "•":
            self.rx_apikey_entry.configure(show="")
            self.rx_apikey_eye_btn.configure(text="🙈")
        else:
            self.rx_apikey_entry.configure(show="•")
            self.rx_apikey_eye_btn.configure(text="👁")

    def _save_receiveolxiv_config(self):
        """Сохранить User ID и API Key для Receiveolxiv в link_api_config.json"""
        try:
            link_cfg = {}
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    link_cfg = json.load(f)
            link_cfg["user_id"] = self.rx_userid_entry.get().strip()
            link_cfg["api_key"] = self.rx_apikey_entry.get().strip()
            with open("link_api_config.json", "w", encoding="utf-8") as f:
                json.dump(link_cfg, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Успех", "✅ Настройки Receiveolxiv сохранены")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}")

    def _toggle_goo_apikey_visibility(self):
        if self.goo_apikey_entry.cget("show") == "•":
            self.goo_apikey_entry.configure(show="")
            self.goo_apikey_eye_btn.configure(text="🙈")
        else:
            self.goo_apikey_entry.configure(show="•")
            self.goo_apikey_eye_btn.configure(text="👁")

    def _toggle_goo_teamkey_visibility(self):
        if self.goo_teamkey_entry.cget("show") == "•":
            self.goo_teamkey_entry.configure(show="")
            self.goo_teamkey_eye_btn.configure(text="🙈")
        else:
            self.goo_teamkey_entry.configure(show="•")
            self.goo_teamkey_eye_btn.configure(text="👁")

    def _save_goo_network_config(self):
        """Сохранить ключи Goo.Network в link_api_config.json"""
        try:
            link_cfg = {}
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    link_cfg = json.load(f)
            link_cfg["goo_network_api_key"]    = self.goo_apikey_entry.get().strip()
            link_cfg["goo_network_team_key"]   = self.goo_teamkey_entry.get().strip()
            link_cfg["goo_network_profile_id"] = self.goo_profileid_entry.get().strip()
            with open("link_api_config.json", "w", encoding="utf-8") as f:
                json.dump(link_cfg, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Успех", "✅ Настройки Goo.Network сохранены")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}")

    def _test_goo_network(self):
        """Тестовый запрос к Goo.Network с реальной ссылкой на объявление."""
        import threading
        import requests as _requests
        from tkinter import simpledialog

        api_key    = self.goo_apikey_entry.get().strip()
        team_key   = self.goo_teamkey_entry.get().strip()
        profile_id = self.goo_profileid_entry.get().strip()

        if not (api_key and team_key and profile_id):
            messagebox.showwarning("Goo.Network", "Заполни все три поля перед тестом.")
            return

        ad_url = simpledialog.askstring(
            "Goo.Network Тест",
            "Вставь ссылку на реальное объявление для теста\n"
            "(например: https://www.vinted.nl/items/12345-название):",
            parent=self
        )
        if not ad_url or not ad_url.strip().startswith("http"):
            messagebox.showwarning("Goo.Network", "Нужна корректная ссылка на объявление.")
            return
        ad_url = ad_url.strip()

        # Определяем service из домена ссылки
        service = _derive_service_from_url(ad_url)

        def _do_test():
            try:
                hdrs = {
                    "Authorization": f"Apikey {api_key}",
                    "Host":          "api-old.goo.network",
                    "X-Team-Key":    team_key,
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                }
                lines = []

                # Вариант 1: no-parse (по данным — предпочтительный)
                payload_np = {
                    "service":              service,
                    "name":                 "Test Item",
                    "isNeedBalanceChecker": False,
                    "profileID":            profile_id,
                    "image":                "",
                    "price":                10.0,
                }
                r1 = _requests.post(
                    "https://api-old.goo.network/api/generate/single/no-parse",
                    json=payload_np, headers=hdrs, timeout=30
                )
                lines.append(f"[no-parse /по данным]\nHTTP {r1.status_code} | {r1.text[:300]}")

                # Вариант 2: parse (по URL объявления)
                payload_p = {
                    "service":              service,
                    "url":                  ad_url,
                    "isNeedBalanceChecker": False,
                    "profileID":            profile_id,
                }
                r2 = _requests.post(
                    "https://api-old.goo.network/api/generate/single/parse",
                    json=payload_p, headers=hdrs, timeout=30
                )
                lines.append(f"[parse /по URL]\nHTTP {r2.status_code} | {r2.text[:300]}")

                result = (
                    f"Сервис: {service}\n"
                    f"URL: {ad_url[:60]}\n\n"
                    + "\n\n".join(lines)
                )
                self.after(0, lambda: messagebox.showinfo("Goo.Network Тест", result))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Goo.Network Тест", str(e)))

        threading.Thread(target=_do_test, daemon=True).start()

    def _toggle_token_visibility(self):
        if self.token_entry.cget("show") == "•":
            self.token_entry.configure(show="")
            self.show_token_btn.configure(text="🙈")
        else:
            self.token_entry.configure(show="•")
            self.show_token_btn.configure(text="👁")

    def _toggle_parser_token_visibility(self):
        if self.parser_token_entry.cget("show") == "•":
            self.parser_token_entry.configure(show="")
            self.show_parser_token_btn.configure(text="🙈")
        else:
            self.parser_token_entry.configure(show="•")
            self.show_parser_token_btn.configure(text="👁")

    def _save_token(self):
        token = self.token_entry.get().strip()
        config = self._load_config()
        config["dolphin_token"] = token
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("Успех", "✅ Токен Dolphin сохранён")

    def _save_parser_token(self):
        token = self.parser_token_entry.get().strip()
        config = self._load_config()
        config["parser_token"] = token
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("Успех", "✅ Токен парсера сохранён")

    def _on_link_api_changed(self, choice: str):
        """Обработчик переключения Link API в выпадающем списке"""
        api_key_map = {
            "MonkeyTeam":  "monkeyteam",
            "Goo.Network": "goo_network",
        }
        api_key = api_key_map.get(choice, "receiveolxiv")
        try:
            link_cfg = {}
            if os.path.exists("link_api_config.json"):
                with open("link_api_config.json", encoding="utf-8") as f:
                    link_cfg = json.load(f)
            link_cfg["active_api"] = api_key
            with open("link_api_config.json", "w", encoding="utf-8") as f:
                json.dump(link_cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # Показываем нужный блок настроек, скрываем остальные
        if choice == "Receiveolxiv":
            self.receiveolxiv_frame.pack(padx=0, fill="x", after=self.link_api_status)
            self.goo_network_frame.pack_forget()
        elif choice == "Goo.Network":
            self.goo_network_frame.pack(padx=0, fill="x", after=self.link_api_status)
            self.receiveolxiv_frame.pack_forget()
        else:
            self.receiveolxiv_frame.pack_forget()
            self.goo_network_frame.pack_forget()
        self._update_link_api_status()

    def _update_link_api_status(self):
        """Обновить статусную строку под выпадающим списком Link API"""
        current = self.link_api_var.get()
        if current == "MonkeyTeam":
            self.link_api_status.configure(
                text="✅ MonkeyTeam API (mk-97413.xyz)",
                text_color="#58a6ff"
            )
        elif current == "Goo.Network":
            self.link_api_status.configure(
                text="✅ Goo.Network API (api.goo.network)",
                text_color="#a371f7"
            )
        else:
            self.link_api_status.configure(
                text="✅ Receiveolxiv API (receiveolxiv.sbs)",
                text_color="#3fb950"
            )


    def _load_token(self) -> str:
        config = self._load_config()
        return config.get("dolphin_token", "")

    def _load_parser_token(self) -> str:
        config = self._load_config()
        return config.get("parser_token", "")

    def _load_config(self) -> dict:
        try:
            with open("config.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_parser_filters(self):
        """Сохранить фильтры парсера в config.json"""
        config = self._load_config()
        config["filters"] = {
            "service_codes": self.service_codes_var.get().strip(),
            "country": self.country_var.get(),
            "category": self.category_entry.get().strip(),
            "price_from": self.price_from_entry.get().strip(),
            "price_to": self.price_to_entry.get().strip(),
            "reviews": self.reviews_entry.get().strip(),
            "ads": self.ads_entry.get().strip(),
            "sells": self.sells_entry.get().strip(),
            "buys": self.buys_entry.get().strip(),
            "views": self.views_entry.get().strip(),
            "publication": self.publication_entry.get().strip(),
            "registration": self.registration_entry.get().strip(),
            "blacklist": self.blacklist_entry.get().strip(),
            "delivery": self.delivery_var.get(),
            "phone": self.phone_var.get(),
            "limit": self.limit_var.get().strip(),
            "interval": self.interval_var.get().strip(),
        }
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("Успех", "✅ Фильтры сохранены")

    def _load_parser_filters(self):
        """Загрузить сохранённые фильтры парсера из config.json в поля UI"""
        config = self._load_config()
        filters = config.get("filters", {})
        if not filters:
            return

        if "service_codes" in filters:
            self.service_codes_var.set(filters["service_codes"])
        elif "platforms" in filters and filters["platforms"]:
            # Миграция со старого формата: platforms + country → service_codes
            old_country = filters.get("country", "").lower()
            codes = [f"{p}_{old_country}" if old_country else p for p in filters["platforms"]]
            self.service_codes_var.set(", ".join(codes))
        elif "platform" in filters:
            old_country = filters.get("country", "").lower()
            p = filters["platform"]
            self.service_codes_var.set(f"{p}_{old_country}" if old_country else p)
        if "country" in filters:
            self.country_var.set(filters["country"])
        if "category" in filters:
            self.category_entry.delete(0, "end")
            self.category_entry.insert(0, filters["category"])
        if "price_from" in filters:
            self.price_from_entry.delete(0, "end")
            self.price_from_entry.insert(0, filters["price_from"])
        if "price_to" in filters:
            self.price_to_entry.delete(0, "end")
            self.price_to_entry.insert(0, filters["price_to"])
        if "reviews" in filters:
            self.reviews_entry.delete(0, "end")
            self.reviews_entry.insert(0, filters["reviews"])
        if "ads" in filters:
            self.ads_entry.delete(0, "end")
            self.ads_entry.insert(0, filters["ads"])
        if "sells" in filters:
            self.sells_entry.delete(0, "end")
            self.sells_entry.insert(0, filters["sells"])
        if "buys" in filters:
            self.buys_entry.delete(0, "end")
            self.buys_entry.insert(0, filters["buys"])
        if "views" in filters:
            self.views_entry.delete(0, "end")
            self.views_entry.insert(0, filters["views"])
        if "publication" in filters:
            self.publication_entry.delete(0, "end")
            self.publication_entry.insert(0, filters["publication"])
        if "registration" in filters:
            self.registration_entry.delete(0, "end")
            self.registration_entry.insert(0, filters["registration"])
        if "blacklist" in filters:
            self.blacklist_entry.delete(0, "end")
            self.blacklist_entry.insert(0, filters["blacklist"])
        if "delivery" in filters:
            self.delivery_var.set(filters["delivery"])
        if "phone" in filters:
            self.phone_var.set(filters["phone"])
        if "limit" in filters:
            self.limit_var.set(filters["limit"])
        if "interval" in filters:
            self.interval_var.set(filters["interval"])

    def _load_sent_emails(self) -> set:
        """Загрузить список уже отправленных email-ов из sent_emails.txt"""
        try:
            with open("sent_emails.txt", encoding="utf-8") as f:
                return {line.strip().lower() for line in f if line.strip()}
        except FileNotFoundError:
            return set()

    def _get_token(self) -> str:
        return self.token_entry.get().strip()

    def _get_parser_token(self) -> str:
        return self.parser_token_entry.get().strip()

    # ── DEBUG ────────────────────────────────

    def _open_debug_folder(self):
        """Открыть папку с DEBUG логами"""
        if not os.path.exists(DEBUG_DIR):
            messagebox.showwarning("Нет логов", "📁 Папка debug_logs ещё не создана")
            return
        
        import subprocess
        import platform
        
        try:
            if platform.system() == "Windows":
                os.startfile(DEBUG_DIR)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", DEBUG_DIR])
            else:
                subprocess.Popen(["xdg-open", DEBUG_DIR])
            self._log(f"📁 Открыта папка: {os.path.abspath(DEBUG_DIR)}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"❌ Не удалось открыть папку: {e}")

    # ── Парсер ────────────────────────────────

    def _parser_log(self, msg: str):
        """Логирование в парсер лог с временем"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        self.parser_log_box.configure(state="normal")
        self.parser_log_box.insert("end", full_msg + "\n")
        self.parser_log_box.see("end")
        self.parser_log_box.configure(state="disabled")

    def _warmup_profiles(self, profiles: list, dolphin_token: str, on_update) -> tuple[dict, dict]:
        """
        Открывает все профили, проверяет Gmail.
        Возвращает tuple: ({profile_id: driver}, {profile_id: tab_handle}) только для готовых профилей.
        """
        profile_load_wait_seconds = 5
        gmail_open_wait_seconds = 3
        compose_cleanup_wait_seconds = 0.5
        drivers = {}
        tab_handles = {}

        opened_drivers = {}
        open_lock = threading.Lock()
        threads = []

        def cleanup_profile(profile_id, driver=None):
            """Log warning only — browsers are never closed forcefully."""
            with self._drivers_lock:
                self._active_drivers.pop(profile_id, None)
            # Браузер НЕ закрываем — профиль остаётся открытым

        def collect_compose_forms(driver, deduplicate=False):
            compose_forms = []
            for sel in ['div.nH.if.adB', 'div.AD']:
                try:
                    compose_forms.extend(driver.find_elements(By.CSS_SELECTOR, sel))
                except Exception:
                    pass

            if not deduplicate:
                return compose_forms

            seen_ids = set()
            unique_forms = []
            for form in compose_forms:
                try:
                    form_element_id = form.id
                    if form_element_id not in seen_ids:
                        seen_ids.add(form_element_id)
                        unique_forms.append(form)
                except Exception:
                    pass
            return unique_forms

        def force_remove_forms(driver, forms):
            for form in forms:
                try:
                    driver.execute_script("arguments[0].remove();", form)
                except Exception:
                    pass

        def open_profile(profile_id):
            driver = None
            try:
                if not self.parser_running:
                    return
                on_update(f"🔄 Открываю профиль #{profile_id}...")
                auto = dolphin_start(profile_id, dolphin_token)
                driver = get_driver(auto)

                with self._drivers_lock:
                    self._active_drivers[profile_id] = driver
                with open_lock:
                    opened_drivers[profile_id] = driver
            except Exception as e:
                on_update(f"❌ Профиль #{profile_id} — ошибка: {str(e)[:100]}")
                cleanup_profile(profile_id, driver)

        # ── Фаза 1: параллельно открыть все профили ──
        for profile_id in profiles:
            if not self.parser_running:
                on_update("⏹ Прогрев прерван пользователем")
                break
            thread = threading.Thread(target=open_profile, args=(profile_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        if not opened_drivers or not self.parser_running:
            return drivers, tab_handles

        # ── Фаза 2: параллельно открыть Gmail и проверить Compose ──
        gmail_threads = []
        gmail_lock = threading.Lock()

        def prepare_profile_gmail(profile_id):
            """\u041eткрыть Gmail и дождаться кнопки Compose — выполняется параллельно."""
            with open_lock:
                driver = opened_drivers.get(profile_id)
            if driver is None:
                return

            try:
                on_update(f"🌐 Открываю Gmail в профиле #{profile_id}...")
                new_handle = open_gmail_inbox_tab(driver, None)

                compose_found = False
                wait_compose = WebDriverWait(driver, 25)  # 25 сек — достаточно для медленных прокси
                for selector in [
                    (By.CSS_SELECTOR, 'div[gh="cm"]'),
                    (By.XPATH, "//div[@gh='cm']"),
                    (By.CSS_SELECTOR, 'div.T-I.T-I-KE.L3'),
                    (By.XPATH, "//div[contains(@class,'T-I-KE')]"),
                ]:
                    try:
                        wait_compose.until(EC.element_to_be_clickable(selector))
                        compose_found = True
                        break
                    except Exception:
                        pass

                if compose_found:
                    remaining_forms = collect_compose_forms(driver)
                    if remaining_forms:
                        on_update(
                            f"⚠️ Профиль #{profile_id}: {len(remaining_forms)} зависших форм — удаляю..."
                        )
                        force_remove_forms(driver, remaining_forms)
                        time.sleep(compose_cleanup_wait_seconds)

                with gmail_lock:
                    if compose_found:
                        on_update(f"✅ Профиль #{profile_id} готов")
                        drivers[profile_id] = driver
                        tab_handles[profile_id] = new_handle
                    else:
                        on_update(f"⚠️ Профиль #{profile_id}: Compose не нашёл — стартуюж через новую вкладку")
                        drivers[profile_id] = driver
                        tab_handles[profile_id] = None

            except Exception as e:
                on_update(f"⚠️ Профиль #{profile_id} — ошибка Gmail: {str(e)[:80]} | Буду пытаться отправить")
                with gmail_lock:
                    drivers[profile_id] = driver
                    tab_handles[profile_id] = None

        for profile_id in list(opened_drivers.keys()):
            if not self.parser_running:
                break
            t = threading.Thread(target=prepare_profile_gmail, args=(profile_id,))
            gmail_threads.append(t)
            t.start()

        for t in gmail_threads:
            t.join()

        return drivers, tab_handles

    def _clear_parser_log(self):
        self.parser_log_box.configure(state="normal")
        self.parser_log_box.delete("1.0", "end")
        self.parser_log_box.configure(state="disabled")

    def _update_parser_ui(self):
        """Обновление UI парсера каждые 100ms"""
        if self.parser_running:
            # Обновление очереди
            queue = self.background_worker.get_queue_snapshot()
            self.parser_queue_box.configure(state="normal")
            self.parser_queue_box.delete("1.0", "end")
            for i, email in enumerate(queue, 1):
                self.parser_queue_box.insert("end", f"{i}. {email}\n")
            self.parser_queue_box.configure(state="disabled")

            # Обновление статистики
            stats = self.background_worker.get_stats()
            queue_size = len(queue)
            self.parser_stats_labels["fetched"].configure(text=str(queue_size + stats.get("sent", 0)))
            self.parser_stats_labels["in_queue"].configure(text=str(queue_size))
            self.parser_stats_labels["sent"].configure(text=str(stats.get("sent", 0)))
            self.parser_stats_labels["failed"].configure(text=str(stats.get("failed", 0)))
            self.parser_stats_labels["skipped"].configure(text=str(stats.get("skipped", 0)))
            self.parser_stats_labels["time"].configure(text=stats.get("elapsed_time", "00:00:00"))

            # Профили — нерабочих больше нет (все открывают новую вкладку вместо отказа)
            self.parser_stats_labels["broken"].configure(text="0", text_color="#3fb950")

        self.after(100, self._update_parser_ui)

    def _get_parser_filters(self) -> dict:
        """Получить фильтры парсера"""
        filters = {}

        # Страна (пропускаем если "(авто)" — будет определена из сервис-кода)
        country = self.country_var.get()
        if country and country != "(авто)":
            filters["country"] = country

        # Категория
        category = self.category_entry.get().strip()
        if category:
            filters["category"] = category

        # Цена — форматы: "10..100" (диапазон), "10.." (от), "100" (до)
        price_from = self.price_from_entry.get().strip()
        price_to   = self.price_to_entry.get().strip()
        if price_from and price_to:
            filters["price"] = f"{price_from}..{price_to}"
        elif price_from:
            filters["price"] = f"{price_from}.."
        elif price_to:
            filters["price"] = price_to

        # Отзывы
        reviews = self.reviews_entry.get().strip()
        if reviews:
            filters["reviews"] = reviews

        # Объявления продавца
        ads = self.ads_entry.get().strip()
        if ads:
            filters["ads"] = ads

        # Продажи продавца
        sells = self.sells_entry.get().strip()
        if sells:
            filters["sells"] = sells

        # Покупки продавца
        buys = self.buys_entry.get().strip()
        if buys:
            filters["buys"] = buys

        # Просмотры объявления
        views = self.views_entry.get().strip()
        if views:
            filters["views"] = views

        # Время публикации
        publication = self.publication_entry.get().strip()
        if publication:
            filters["publication"] = publication

        # Дата регистрации
        registration = self.registration_entry.get().strip()
        if registration:
            filters["registration"] = registration

        # Стоп-слова
        blacklist = self.blacklist_entry.get().strip()
        if blacklist:
            filters["blacklist"] = blacklist

        # Доставка
        delivery = self.delivery_var.get()
        if delivery == "только с доставкой":
            filters["delivery"] = "true"
        elif delivery == "только самовывоз":
            filters["delivery"] = "false"

        # Телефон
        phone = self.phone_var.get()
        if phone == "с телефоном":
            filters["phone"] = "true"
        elif phone == "без телефона":
            filters["phone"] = "false"

        # Лимит
        limit = self.limit_var.get().strip()
        if limit:
            filters["limit"] = limit

        return filters

    def _start_parser(self):
        """Запустить парсер"""
        parser_token = self._get_parser_token()
        dolphin_token = self._get_token()
        
        # Парсим сервис-коды из текстового поля
        raw_codes = self.service_codes_var.get().strip()
        if not raw_codes:
            messagebox.showerror("Ошибка", "❌ Введи хотя бы один сервис-код! Например: vinted_it")
            return

        # Разбираем: "vinted_it, subito_it" → [("vinted", "it"), ("subito", "it")]
        service_codes = []
        for code in raw_codes.replace(" ", "").split(","):
            code = code.strip().lower()
            if code and "_" in code:
                service_codes.append(code)
            elif code:
                # Если нет суффикса — используем как есть (платформа без страны)
                service_codes.append(code)

        if not service_codes:
            messagebox.showerror("Ошибка", "❌ Неверный формат! Используй: vinted_it, subito_it")
            return

        # Извлекаем платформы для API запросов
        selected_platforms = []
        for sc in service_codes:
            parts = sc.split("_", 1)
            platform = parts[0]
            if platform not in selected_platforms:
                selected_platforms.append(platform)

        # Определяем страну: явный выбор или авто из первого сервис-кода
        country_override = self.country_var.get()
        if country_override and country_override != "(авто)":
            country = country_override
        else:
            country = ""
            for sc in service_codes:
                parts = sc.split("_", 1)
                if len(parts) > 1:
                    country = parts[1].upper()
                    break

        # Добавляем страну в фильтры для мультистраночных площадок
        # (если страна не была явно задана пользователем и не попала в filters)
        if country and "country" not in filters:
            filters["country"] = country

        platform = selected_platforms[0]
        platform_names = ", ".join(service_codes)
        
        profiles = self._get_profiles()
        subject = self.subject_entry.get().strip()
        body = self.body_box.get("1.0", "end").strip()

        if not parser_token:
            messagebox.showerror("Ошибка", "❌ Введи API токен парсера!")
            return
        if not dolphin_token:
            messagebox.showerror("Ошибка", "❌ Введи API токен Dolphin!")
            return
        if not profiles:
            messagebox.showerror("Ошибка", "❌ Нет профилей Dolphin!")
            return
        if not subject or not body:
            messagebox.showerror("Ошибка", "❌ Тема и текст письма не могут быть пустыми!")
            return

        try:
            interval = int(self.interval_var.get())
        except ValueError:
            interval = 3

        try:
            typing_min_ms = max(1, int(self.typing_min_var.get()))
        except ValueError:
            typing_min_ms = 30
        try:
            typing_max_ms = max(typing_min_ms, int(self.typing_max_var.get()))
        except ValueError:
            typing_max_ms = 100

        # переводим мс → секунды
        typing_min = typing_min_ms / 1000.0
        typing_max = typing_max_ms / 1000.0

        try:
            max_age_minutes = int(self.max_age_var.get())
            if max_age_minutes < 0:
                max_age_minutes = 0
        except ValueError:
            max_age_minutes = 0

        self.parser_running = True
        self.parser_start_btn.configure(state="disabled")
        self.parser_stop_btn.configure(state="normal")
        self.parser_status_label.configure(text="🟡 ПРОГРЕВ...")
        self.tabview.set("🔗  Парсер")
        self._clear_parser_log()

        # Очищаем список активных драйверов
        with self._drivers_lock:
            self._active_drivers = {}
            self._active_tab_handles = {}

        # Создание клиента парсера
        parser_client = ParserClient(parser_token)
        filters = self._get_parser_filters()

        # Если страна не задана в фильтрах — берём из сервис-кода
        if "country" not in filters and country:
            filters["country"] = country

        def run_warmup_and_start():
            self._parser_log(f"🔥 Прогрев профилей ({len(profiles)} шт.)...")
            drivers, tab_handles = self._warmup_profiles(profiles, dolphin_token, self._parser_log)

            if not self.parser_running:
                # Пользователь нажал Остановить во время прогрева
                return

            if not drivers:
                self._parser_log("❌ Нет готовых профилей! Остановка.")
                self.after(0, self._reset_parser_ui)
                return

            self._parser_log(
                f"✅ Прогрев завершён: {len(drivers)}/{len(profiles)} профилей готовы. "
                f"Запускаю рассылку..."
            )

            # Обновляем список активных драйверов
            with self._drivers_lock:
                self._active_drivers = dict(drivers)
                self._active_tab_handles = dict(tab_handles)

            # Функция отправки — использует постоянный driver из словаря
            def send_func(email: str, profile_id: str, subj: str, body_text: str) -> dict:
                with self._drivers_lock:
                    driver = self._active_drivers.get(profile_id)
                    tab_handle = self._active_tab_handles.get(profile_id)
                if driver is None:
                    return {"success": False, "error": "Драйвер не найден"}

                spun_subject = spintax(subj)
                spun_body    = spintax(body_text)

                def _fresh_tab_and_send() -> bool:
                    """\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0447\u0438\u0441\u0442\u0443\u044e \u0432\u043a\u043b\u0430\u0434\u043a\u0443 Gmail \u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0441 \u043d\u0435\u0451. \u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442 True/False."""
                    try:
                        self._parser_log(f"\u21bb #{profile_id}: \u043e\u0442\u043a\u0440\u044b\u0432\u0430\u044e \u043d\u043e\u0432\u0443\u044e \u0447\u0438\u0441\u0442\u0443\u044e \u0432\u043a\u043b\u0430\u0434\u043a\u0443 Gmail...")
                        new_handle = open_gmail_inbox_tab(driver, None)
                        with self._drivers_lock:
                            self._active_tab_handles[profile_id] = new_handle
                        send_via_gmail(driver, email, spun_subject, spun_body,
                                       tab_handle=new_handle,
                                       typing_min=typing_min, typing_max=typing_max)
                        self._parser_log(f"\u2705 #{profile_id}: \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u0430\u044f \u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0441 \u043d\u043e\u0432\u043e\u0439 \u0432\u043a\u043b\u0430\u0434\u043a\u0438 \u0443\u0434\u0430\u043b\u0430\u0441\u044c")
                        return True
                    except Exception as retry_err:
                        self._parser_log(f"\u274c #{profile_id}: \u043d\u043e\u0432\u0430\u044f \u0432\u043a\u043b\u0430\u0434\u043a\u0430 \u0442\u043e\u0436\u0435 \u043d\u0435 \u043f\u043e\u043c\u043e\u0433\u043b\u0430: {str(retry_err)[:100]}")
                        return False

                try:
                    # Первая попытка — на текущей вкладке
                    current_tab_handle = open_gmail_inbox_tab(driver, tab_handle)
                    if current_tab_handle != tab_handle:
                        with self._drivers_lock:
                            self._active_tab_handles[profile_id] = current_tab_handle
                    send_via_gmail(driver, email, spun_subject, spun_body,
                                   tab_handle=current_tab_handle,
                                   typing_min=typing_min, typing_max=typing_max)
                    return {"success": True}

                except Exception as e:
                    # При ЛЮБОЙ ошибке — сразу чистая вкладка + одна попытка
                    self._parser_log(f"⚠️ #{profile_id}: ошибка — {str(e)[:80]}")
                    ok = _fresh_tab_and_send()
                    return {"success": ok, "error": "" if ok else "ошибка после повторной попытки"}

            def profile_broken_cb(profile_id, error):
                self._parser_log(f"⚠️ Профиль #{profile_id} помечен как нерабочий: {error}")

            # Строим маппинг platform → service_code
            sc_map = {}
            for sc in service_codes:
                parts = sc.split("_", 1)
                sc_map[parts[0]] = sc  # {"vinted": "vinted_it", "subito": "subito_it"}

            # Запускаем BackgroundWorker из главного потока
            self.after(0, lambda: self._launch_worker(
                parser_client, platform, platform_names, list(drivers.keys()),
                send_func, subject, body, interval, filters,
                profile_broken_cb, self._sent_emails, max_age_minutes,
                selected_platforms, sc_map
            ))

        self._warmup_thread = threading.Thread(target=run_warmup_and_start, daemon=True)
        self._warmup_thread.start()

        self._parser_log(f"🚀 Запуск прогрева. Площадки: {platform_names}, Профилей: {len(profiles)}, Фильтры: {filters}")

    def _launch_worker(self, parser_client, platform, platform_name, ready_profiles,
                       send_func, subject, body, interval, filters,
                       on_profile_broken, sent_emails=None, max_age_minutes=0,
                       platforms=None, service_codes_map=None):
        """Запустить BackgroundWorker после завершения прогрева (вызывается из главного потока)"""
        if not self.parser_running:
            return

        self.parser_status_label.configure(text="🟢 РАБОТАЕТ")

        def _on_sent(email, profile_id, meta):
            """Cавести данные товара в локальную базу при успешной отправке"""
            self._conversation_store.save_sent(
                email=email,
                profile_id=str(profile_id),
                subject=subject,
                body=body,
                thread_url=meta.get("ad_url", ""),
                product_name=meta.get("product_name", ""),
                price=meta.get("price", ""),
                photo=meta.get("photo", ""),
                seller_name=meta.get("seller_name", ""),
                address=meta.get("address", "-"),
                ad_url=meta.get("ad_url", ""),
                service_code=meta.get("service_code", ""),
                platform=meta.get("service_code", "").split("_")[0] if meta.get("service_code") else platform,
            )

        self.background_worker.start(
            parser_client=parser_client,
            platform=platform,
            platforms=platforms,
            service_codes_map=service_codes_map or {},
            profiles=ready_profiles,
            send_func=send_func,
            subject=subject,
            body=body,
            interval_sec=interval,
            on_update=self._parser_log,
            on_profile_broken=on_profile_broken,
            sent_emails=sent_emails,
            max_age_minutes=max_age_minutes,
            filters=filters,
            on_sent=_on_sent,
        )

        self._parser_log(
            f"🟢 Рассылка запущена! Площадки: {platform_name}, "
            f"Профилей: {len(ready_profiles)} | Чередование: каждые 5сек"
        )

    def _reset_parser_ui(self):
        """Сбросить UI парсера в остановленное состояние"""
        self.parser_running = False
        self.parser_start_btn.configure(state="normal")
        self.parser_stop_btn.configure(state="disabled")
        self.parser_status_label.configure(text="⏹ ОСТАНОВЛЕН")

    def _stop_parser(self):
        """Остановить парсер — браузеры остаются открытыми"""
        self.parser_running = False
        self.background_worker.stop(on_update=self._parser_log)
        self.parser_start_btn.configure(state="normal")
        self.parser_stop_btn.configure(state="disabled")
        self.parser_status_label.configure(text="⏹ ОСТАНОВЛЕН")
        self._parser_log("✅ Рассылка остановлена. Браузеры остались открытыми — можешь возобновить рассылку.")

    # ── Шаблоны ────────────────────────────────

    def _on_template_select(self, name: str):
        t = next((x for x in self.templates if x["name"] == name), None)
        if not t:
            return
        self.subject_entry.delete(0, "end")
        self.subject_entry.insert(0, t["subject"])
        self.body_box.delete("1.0", "end")
        self.body_box.insert("end", t["body"])

    def _preview_spintax(self):
        subject = spintax(self.subject_entry.get())
        body = spintax(self.body_box.get("1.0", "end").strip())

        win = ctk.CTkToplevel(self)
        win.title("Предпросмотр письма")
        win.geometry("600x400")
        win.configure(fg_color="#0f1117")
        win.grab_set()

        ctk.CTkLabel(win, text="Тема:",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(win, text=subject,
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color="#c9d1d9").pack(anchor="w", padx=12)

        ctk.CTkFrame(win, height=1, fg_color="#30363d").pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(win, text="Текст письма:",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color="#8b949e").pack(anchor="w", padx=12, pady=(0, 2))

        box = ctk.CTkTextbox(win, font=ctk.CTkFont("Consolas", 10),
                             fg_color="#161b22", border_color="#30363d",
                             border_width=1, text_color="#c9d1d9")
        box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        box.insert("end", body)
        box.configure(state="disabled")

        ctk.CTkButton(win, text="Ещё раз (другой вариант)", height=30,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont("Segoe UI", 10),
                      command=lambda: [win.destroy(), self._preview_spintax()]).pack(pady=(0, 10))

    def _save_current_template(self):
        name = self.template_var.get()
        for t in self.templates:
            if t["name"] == name:
                t["subject"] = self.subject_entry.get()
                t["body"] = self.body_box.get("1.0", "end").strip()
                break
        self._save_templates()
        messagebox.showinfo("Успех", f"✅ Шаблон «{name}» сохранён")

    def _new_template(self):
        dialog = ctk.CTkInputDialog(text="Название нового шаблона:", title="Новый шаблон")
        name = dialog.get_input()
        if not name:
            return
        self.templates.append({"name": name, "subject": "", "body": ""})
        self.template_menu.configure(values=[t["name"] for t in self.templates])
        self.template_var.set(name)
        self._on_template_select(name)
        self._save_templates()

    def _delete_template(self):
        name = self.template_var.get()
        self.templates = [t for t in self.templates if t["name"] != name]
        if self.templates:
            self.template_menu.configure(values=[t["name"] for t in self.templates])
            self.template_var.set(self.templates[0]["name"])
            self._on_template_select(self.templates[0]["name"])
        else:
            self.template_menu.configure(values=[])
            self.template_var.set("")
        self._save_templates()

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _get_profiles(self) -> list[str]:
        raw = self.profiles_box.get("1.0", "end")
        return [l.strip() for l in raw.splitlines() if l.strip()]

    def _save_profiles(self):
        profiles = self._get_profiles()
        with open("profiles.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(profiles))
        messagebox.showinfo("Успех", f"✅ Профили сохранены ({len(profiles)} шт.)")

    def _load_profiles(self) -> list[str]:
        try:
            with open("profiles.txt", encoding="utf-8") as f:
                return [l.strip() for l in f if l.strip() and not l.startswith("#")]
        except Exception:
            return []

    def _load_profiles_file(self):
        path = filedialog.askopenfilename(
            title="Выбери файл с профилями",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            ids = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.profiles_box.delete("1.0", "end")
        self.profiles_box.insert("end", "\n".join(ids))
        messagebox.showinfo("Успех", f"✅ Загружено {len(ids)} профилей из файла")

    def _load_templates(self) -> list[dict]:
        try:
            with open("templates.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return [
                {"name": "Деловое предложение",
                 "subject": "Сотрудничество — предложение для вас",
                 "body": "Здравствуйте!\n\nХотел бы предложить вам сотрудничество.\n\nС уважением,\n[Ваше имя]"},
                {"name": "Информационное письмо",
                 "subject": "Важная информация для вас",
                 "body": "Добрый день!\n\nСообщаем вам о важных изменениях.\n\nС уважением,\nКоманда"},
            ]

    def _save_templates(self):
        with open("templates.json", "w", encoding="utf-8") as f:
            json.dump(self.templates, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    app = App()
    app.mainloop()
