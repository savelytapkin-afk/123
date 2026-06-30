"""
HTML Template Editor
====================
Редактор HTML-шаблонов ответных писем.

Переменные из парсера:
  {{seller_name}}   — имя продавца
  {{product_name}}  — название товара
  {{price}}         — цена
  {{photo}}         — URL фото товара
  {{address}}       — локация продавца
  {{platform}}      — платформа (vinted, depop...)
  {{email}}         — email покупателя
  {{link}}          — ссылка из Link API (receiveolxiv.sbs)
"""

import json
import os
import re
import tempfile
import webbrowser
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
import customtkinter as ctk


TEMPLATES_FILE = "html_templates.json"

# Переменные с описаниями
VARIABLES = [
    ("{{seller_name}}",  "Имя продавца"),
    ("{{product_name}}", "Название товара"),
    ("{{price}}",        "Цена"),
    ("{{photo}}",        "URL фото"),
    ("{{address}}",      "Локация"),
    ("{{platform}}",     "Платформа"),
    ("{{email}}",        "Email покупателя"),
    ("{{link}}",         "Ссылка (Link API)"),
]

# Демо-данные для предпросмотра
PREVIEW_DATA = {
    "seller_name":  "Maria Rossi",
    "product_name": "Vintage Leather Jacket",
    "price":        "45 EUR",
    "photo":        "https://picsum.photos/200/200?random=42",
    "address":      "Milan, Italy",
    "platform":     "Vinted",
    "email":        "buyer@gmail.com",
    "link":         "https://receiveolxiv.sbs/view/demo-link",
}

DEFAULT_TEMPLATE = """\
<div style="font-family:Arial,sans-serif;background:#f0f2f5;margin:0;padding:24px;">
  <div style="background:#ffffff;border-radius:14px;max-width:520px;margin:0 auto;padding:28px 32px;box-shadow:0 4px 24px rgba(0,0,0,0.10);">

    <div style="font-size:22px;font-weight:bold;color:#1a1a2e;margin-bottom:6px;">Hi {{seller_name}}! 👋</div>
    <div style="color:#666;font-size:14px;margin-bottom:20px;">We found your listing on <b>{{platform}}</b> and are very interested!</div>

    <img src="{{photo}}" alt="{{product_name}}" style="width:100%;border-radius:10px;margin-bottom:18px;object-fit:cover;max-height:240px;display:block;">

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;border-bottom:1px solid #f0f0f0;padding-bottom:8px;">
      <tr>
        <td style="color:#999;font-size:13px;padding:4px 0;">Item</td>
        <td style="font-weight:600;color:#222;font-size:13px;text-align:right;padding:4px 0;">{{product_name}}</td>
      </tr>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;border-bottom:1px solid #f0f0f0;padding-bottom:8px;">
      <tr>
        <td style="color:#999;font-size:13px;padding:4px 0;">Location</td>
        <td style="font-weight:600;color:#222;font-size:13px;text-align:right;padding:4px 0;">{{address}}</td>
      </tr>
    </table>

    <div style="font-size:26px;font-weight:bold;color:#e63946;margin:18px 0 10px;">{{price}}</div>

    <a href="{{link}}" style="display:block;background:linear-gradient(135deg,#6c63ff 0%,#48cae4 100%);color:#ffffff;text-decoration:none;text-align:center;padding:15px 0;border-radius:10px;font-size:16px;font-weight:bold;margin-top:22px;letter-spacing:0.5px;">View Our Offer &rarr;</a>

    <div style="color:#bbb;font-size:11px;text-align:center;margin-top:20px;line-height:1.5;">
      This message was sent automatically.<br>
      Reply to this email to get in touch with us.
    </div>

  </div>
</div>
"""


# ─────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────

def load_templates() -> dict:
    """Загрузить все шаблоны из файла. Ключи — произвольные сервис-коды."""
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_templates(templates: dict):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def render_template(html: str, data: dict = None) -> str:
    """Подставить переменные {{key}} в HTML."""
    result = html
    ctx = data or PREVIEW_DATA
    for key, val in ctx.items():
        result = result.replace("{{" + key + "}}", str(val))
    return result


def get_template(service_code: str, data: dict = None) -> str:
    """
    Получить готовый HTML для сервис-кода с подставленными данными.
    Сначала ищет точный ключ (vinted_it),
    если не нашёл — ищет по платформе (vinted).
    Возвращает "" если шаблон не задан.
    """
    sc = service_code.lower().strip()
    templates = load_templates()

    # Точный ключ (vinted_it)
    html = templates.get(sc, "")
    if html.strip():
        return render_template(html, data)

    # Fallback: по платформе (vinted) — если есть общий шаблон
    platform = sc.split("_")[0]
    html = templates.get(platform, "")
    if html.strip():
        return render_template(html, data)

    return ""


# ─────────────────────────────────────────────────────
# Окно редактора
# ─────────────────────────────────────────────────────

class HtmlTemplateEditor(ctk.CTkToplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.title("HTML-шаблоны ответов")
        self.geometry("1380x820")
        self.resizable(True, True)
        self.configure(fg_color="#0d1117")

        self.templates = load_templates()
        self._current_sc = ""
        self._preview_job = None

        self._build_ui()

        # Загрузить первый доступный шаблон
        saved = self._get_saved_codes()
        if saved:
            self._current_sc = saved[0]
            self._sc_entry_var.set(saved[0])
            self._load_sc(saved[0])
            self._refresh_saved_list()

    def _get_saved_codes(self) -> list:
        """Список сервис-кодов, у которых есть непустой шаблон (отсортирован)."""
        return sorted([sc for sc, html in self.templates.items() if html.strip()])

    # ─────── построение UI ───────────────────────────

    def _build_ui(self):
        # ── Заголовок ──────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#161b22", height=50, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="✉️  HTML-шаблоны ответных писем",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color="#58a6ff").pack(side="left", padx=20, pady=14)

        ctk.CTkButton(hdr, text="💾  Сохранить", width=130, height=32,
                      fg_color="#1f6feb", hover_color="#388bfd",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._save).pack(side="right", padx=14, pady=9)

        ctk.CTkButton(hdr, text="↩ Дефолтный шаблон", width=155, height=32,
                      fg_color="transparent", border_color="#30363d", border_width=1,
                      hover_color="#21262d",
                      font=ctk.CTkFont("Segoe UI", 11),
                      text_color="#8b949e",
                      command=self._insert_default).pack(side="right", padx=(0, 6), pady=9)

        # ── Панель ввода сервис-кода ────────────────
        sc_bar = ctk.CTkFrame(self, fg_color="#161b22", height=50, corner_radius=0)
        sc_bar.pack(fill="x")
        sc_bar.pack_propagate(False)

        ctk.CTkLabel(sc_bar, text="Сервис-код:",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color="#8b949e").pack(side="left", padx=(20, 8), pady=10)

        self._sc_entry_var = ctk.StringVar(value="")
        self._sc_entry = ctk.CTkEntry(
            sc_bar, textvariable=self._sc_entry_var,
            width=220, height=32,
            font=ctk.CTkFont("Consolas", 12),
            fg_color="#0d1117", border_color="#30363d",
            text_color="#c9d1d9",
            placeholder_text="например: vinted_it",
        )
        self._sc_entry.pack(side="left", padx=(0, 8), pady=9)
        self._sc_entry.bind("<Return>", lambda e: self._load_or_create())

        ctk.CTkButton(sc_bar, text="📂 Загрузить", width=110, height=32,
                      fg_color="#238636", hover_color="#2ea043",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._load_or_create).pack(side="left", padx=(0, 8), pady=9)

        ctk.CTkButton(sc_bar, text="🗑 Удалить", width=100, height=32,
                      fg_color="#da3633", hover_color="#f85149",
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      command=self._delete_current).pack(side="left", padx=(0, 8), pady=9)

        # Текущий сервис-код (отображение)
        self._sc_label = ctk.CTkLabel(
            sc_bar, text="",
            font=ctk.CTkFont("Consolas", 11, "bold"),
            text_color="#58a6ff",
        )
        self._sc_label.pack(side="right", padx=20, pady=10)

        # ── Основная область (3 колонки) ──────────────
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0, minsize=200)  # Список сохранённых
        main.columnconfigure(1, weight=5)                # Редактор
        main.columnconfigure(2, weight=5)                # Предпросмотр
        main.rowconfigure(0, weight=1)

        # LEFT: Список сохранённых сервис-кодов
        self._build_saved_list(main)
        # CENTER: редактор
        self._build_editor(main)
        # RIGHT: предпросмотр
        self._build_preview(main)

    def _build_saved_list(self, parent):
        """Панель с сохранёнными сервис-кодами."""
        panel = ctk.CTkFrame(parent, fg_color="#0d1117", corner_radius=0, width=200)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_propagate(False)
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        # Заголовок
        lbl_hdr = ctk.CTkFrame(panel, fg_color="#161b22", height=34, corner_radius=0)
        lbl_hdr.grid(row=0, column=0, sticky="ew")
        lbl_hdr.pack_propagate(False)
        ctk.CTkLabel(lbl_hdr, text="📋 Сохранённые",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color="#8b949e").pack(side="left", padx=12, pady=6)

        # Скроллируемый фрейм для кнопок
        self._saved_scroll = ctk.CTkScrollableFrame(
            panel, fg_color="#0d1117",
            scrollbar_button_color="#21262d",
            scrollbar_button_hover_color="#30363d",
        )
        self._saved_scroll.grid(row=1, column=0, sticky="nsew")
        self._saved_scroll.columnconfigure(0, weight=1)

        self._saved_btns: dict = {}
        self._refresh_saved_list()

    def _refresh_saved_list(self):
        """Перестроить список кнопок сохранённых сервис-кодов."""
        for btn in self._saved_btns.values():
            btn.destroy()
        self._saved_btns.clear()

        for sc in self._get_saved_codes():
            is_active = (sc == self._current_sc)
            btn = ctk.CTkButton(
                self._saved_scroll,
                text=f"● {sc}" if is_active else f"  {sc}",
                height=30,
                anchor="w",
                fg_color="#21262d" if is_active else "transparent",
                hover_color="#30363d",
                border_width=0,
                font=ctk.CTkFont("Consolas", 10, "bold" if is_active else "normal"),
                text_color="#58a6ff" if is_active else "#8b949e",
                command=lambda s=sc: self._select_saved(s),
            )
            btn.pack(fill="x", padx=4, pady=1)
            self._saved_btns[sc] = btn

    def _select_saved(self, sc: str):
        """Клик по сохранённому сервис-коду — загрузить его шаблон."""
        # Сохраняем текущий перед переключением
        if self._current_sc:
            self.templates[self._current_sc] = self._editor.get("1.0", "end-1c")

        self._sc_entry_var.set(sc)
        self._current_sc = sc
        self._load_sc(sc)
        self._refresh_saved_list()

    def _load_or_create(self):
        """Загрузить шаблон по введённому сервис-коду или создать пустой."""
        sc = self._sc_entry_var.get().strip().lower()
        if not sc:
            messagebox.showwarning("Сервис-код", "Введите сервис-код!")
            return

        # Сохраняем текущий перед переключением
        if self._current_sc:
            self.templates[self._current_sc] = self._editor.get("1.0", "end-1c")

        self._current_sc = sc
        self._sc_entry_var.set(sc)

        if sc not in self.templates:
            self.templates[sc] = ""

        self._load_sc(sc)
        self._refresh_saved_list()

    def _delete_current(self):
        """Удалить текущий сервис-код и его шаблон."""
        sc = self._current_sc
        if not sc:
            return
        if not messagebox.askyesno("Удалить?", f"Удалить шаблон «{sc}»?"):
            return

        self.templates.pop(sc, None)
        save_templates(self.templates)

        self._editor.delete("1.0", "end")
        self._current_sc = ""
        self._sc_entry_var.set("")
        self._sc_label.configure(text="")
        self._refresh_saved_list()
        self._refresh_preview()

    # ─────── Редактор (центральная колонка) ───────────

    def _build_editor(self, parent):
        left = ctk.CTkFrame(parent, fg_color="#0d1117", corner_radius=0)
        left.grid(row=0, column=1, sticky="nsew")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        # Кнопки переменных
        var_bar = ctk.CTkFrame(left, fg_color="#161b22", height=34, corner_radius=0)
        var_bar.grid(row=0, column=0, sticky="ew")
        var_bar.pack_propagate(False)

        ctk.CTkLabel(var_bar, text="Вставить:",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#484f58").pack(side="left", padx=(10, 4))

        for var, _ in VARIABLES:
            short = var.replace("{{", "").replace("}}", "")
            ctk.CTkButton(
                var_bar, text=short, height=22,
                width=max(60, len(short) * 7 + 12),
                fg_color="#21262d", hover_color="#30363d",
                border_color="#30363d", border_width=1,
                font=ctk.CTkFont("Consolas", 9),
                text_color="#79c0ff",
                command=lambda v=var: self._insert_var(v),
            ).pack(side="left", padx=2, pady=4)

        # Текстовый редактор HTML
        editor_wrap = tk.Frame(left, bg="#0d1117")
        editor_wrap.grid(row=1, column=0, sticky="nsew")
        editor_wrap.rowconfigure(0, weight=1)
        editor_wrap.columnconfigure(0, weight=1)

        self._editor = tk.Text(
            editor_wrap,
            bg="#0d1117", fg="#c9d1d9",
            insertbackground="#58a6ff",
            selectbackground="#1f6feb", selectforeground="#ffffff",
            font=("Consolas", 11),
            wrap="none", relief="flat",
            padx=12, pady=10,
            undo=True,
        )
        self._editor.grid(row=0, column=0, sticky="nsew")

        sb_y = tk.Scrollbar(editor_wrap, command=self._editor.yview, bg="#21262d")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x = tk.Scrollbar(editor_wrap, command=self._editor.xview,
                             bg="#21262d", orient="horizontal")
        sb_x.grid(row=1, column=0, sticky="ew")
        self._editor.config(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        # Теги подсветки
        self._editor.tag_configure("html_tag",   foreground="#7ee787")
        self._editor.tag_configure("html_attr",  foreground="#79c0ff")
        self._editor.tag_configure("html_str",   foreground="#a5d6ff")
        self._editor.tag_configure("html_var",   foreground="#ffa657",
                                   background="#2d1e00",
                                   font=("Consolas", 11, "bold"))
        self._editor.tag_configure("html_cmt",   foreground="#484f58",
                                   font=("Consolas", 11, "italic"))

        self._editor.bind("<<Modified>>", self._on_edit)

        # Статус-строка
        self._status = ctk.CTkLabel(left, text="", height=22,
                                    font=ctk.CTkFont("Segoe UI", 9),
                                    text_color="#484f58",
                                    fg_color="#161b22")
        self._status.grid(row=2, column=0, sticky="ew")

    # ─────── Предпросмотр (правая колонка) ────────────

    def _build_preview(self, parent):
        right = ctk.CTkFrame(parent, fg_color="#1c2128", corner_radius=0)
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        # Заголовок предпросмотра
        phdr = ctk.CTkFrame(right, fg_color="#21262d", height=36, corner_radius=0)
        phdr.grid(row=0, column=0, sticky="ew")
        phdr.pack_propagate(False)

        ctk.CTkLabel(phdr, text="👁  Предпросмотр (демо-данные)",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#8b949e").pack(side="left", padx=14, pady=8)

        ctk.CTkButton(phdr, text="🌐 В браузере", width=110, height=26,
                      fg_color="#1f6feb", hover_color="#388bfd",
                      font=ctk.CTkFont("Segoe UI", 10, "bold"),
                      command=self._open_in_browser).pack(side="right", padx=10, pady=5)

        # Предпросмотр — raw HTML с подстановленными переменными
        preview_wrap = tk.Frame(right, bg="#ffffff")
        preview_wrap.grid(row=1, column=0, sticky="nsew")
        preview_wrap.rowconfigure(0, weight=1)
        preview_wrap.columnconfigure(0, weight=1)

        self._preview = tk.Text(
            preview_wrap,
            bg="#f6f8fa", fg="#1a1a2e",
            font=("Consolas", 10),
            wrap="word", relief="flat",
            padx=14, pady=10,
            state="disabled",
        )
        self._preview.grid(row=0, column=0, sticky="nsew")

        sb = tk.Scrollbar(preview_wrap, command=self._preview.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._preview.config(yscrollcommand=sb.set)

        # Подсказка
        self._preview_hint = ctk.CTkLabel(
            right, text="  Переменные подставлены демо-значениями. Нажмите «В браузере» для полного рендера.",
            height=22,
            font=ctk.CTkFont("Segoe UI", 8),
            text_color="#484f58",
            fg_color="#161b22",
        )
        self._preview_hint.grid(row=2, column=0, sticky="ew")

    # ─────── логика ─────────────────────────────────

    def _on_edit(self, event=None):
        self._editor.edit_modified(False)
        chars = len(self._editor.get("1.0", "end-1c"))
        lines = int(self._editor.index("end-1c").split(".")[0])
        self._status.configure(text=f"  {lines} строк  ·  {chars} символов")

        # Debounce: обновляем через 400мс
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(400, self._do_refresh)

    def _do_refresh(self):
        self._apply_highlight()
        self._refresh_preview()

    def _apply_highlight(self):
        content = self._editor.get("1.0", "end-1c")
        for tag in ("html_tag", "html_attr", "html_str", "html_var", "html_cmt"):
            self._editor.tag_remove(tag, "1.0", "end")

        patterns = [
            ("html_cmt",  r"<!--.*?-->"),
            ("html_tag",  r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>"),
            ("html_attr", r"\b[a-zA-Z\-]+(?==\")"),
            ("html_str",  r'"[^"]*"'),
            ("html_var",  r"\{\{[a-z_]+\}\}"),
        ]
        for tag, pattern in patterns:
            for m in re.finditer(pattern, content, re.DOTALL):
                s = f"1.0+{m.start()}c"
                e = f"1.0+{m.end()}c"
                self._editor.tag_add(tag, s, e)

    def _refresh_preview(self):
        html = self._editor.get("1.0", "end-1c")
        rendered = render_template(html)
        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", rendered)
        self._preview.config(state="disabled")

    def _insert_var(self, var: str):
        self._editor.insert(tk.INSERT, var)
        self._editor.focus_set()

    def _insert_default(self):
        if self._editor.get("1.0", "end-1c").strip():
            if not messagebox.askyesno(
                "Заменить шаблон?",
                "Текущий шаблон будет заменён дефолтным. Продолжить?"
            ):
                return
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", DEFAULT_TEMPLATE)
        self._on_edit()

    def _load_sc(self, sc: str):
        """Загрузить шаблон для сервис-кода в редактор."""
        self._current_sc = sc
        self._sc_label.configure(text=f"✏️  {sc}")
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", self.templates.get(sc, ""))
        self._on_edit()

    def _save(self):
        """Сохранить текущий шаблон по сервис-коду из поля ввода."""
        sc = self._sc_entry_var.get().strip().lower()
        if not sc:
            messagebox.showwarning("Сервис-код", "Введите сервис-код!")
            return

        self._current_sc = sc
        self.templates[sc] = self._editor.get("1.0", "end-1c")
        try:
            save_templates(self.templates)
            self._sc_label.configure(text=f"✏️  {sc}")
            self._status.configure(
                text=f"  ✅ Сохранено [{sc}]  ({datetime.now().strftime('%H:%M:%S')})"
            )
            self._refresh_saved_list()
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", str(e))

    def _open_in_browser(self):
        html = self._editor.get("1.0", "end-1c")
        rendered = render_template(html)
        sc = self._current_sc or "preview"
        tmp = os.path.join(
            tempfile.gettempdir(),
            f"html_preview_{sc}.html"
        )
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(rendered)
            webbrowser.open(f"file:///{tmp.replace(chr(92), '/')}")
            self._preview_hint.configure(text=f"  Открыт: {tmp}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))


# ── Запуск отдельно (для тестирования) ──────────────
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    root.withdraw()
    ed = HtmlTemplateEditor(root)
    ed.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
