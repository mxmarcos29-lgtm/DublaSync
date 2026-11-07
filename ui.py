"""
Interface do DublaSync — visual inspirado no qui (autobrr): barra lateral, fundo neutro
quase preto, cartões arredondados com borda fina, estatísticas no topo, etapas em tabela
com barras finas e etiquetas, barra de status embaixo.

O Tk não tem cantos arredondados nativos, então caixas, botões, campos e itens do menu
são desenhados num Canvas (RoundBox, RoundButton, NavItem).
"""

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import dublasync_core as core
import naming
import torrent as tor

VERSION = "1.7"
EXTS = ("*.mkv *.mp4 *.m4v *.avi *.mov *.wmv *.flv *.webm *.ts *.m2ts *.mts *.mpg *.mpeg "
        "*.vob *.3gp *.ogv *.rmvb *.rm *.divx *.asf *.mka *.mp3 *.aac *.ac3 *.eac3 *.dts "
        "*.flac *.wav *.m4a *.ogg *.opus *.wma *.thd *.mlp")
FILETYPES = [("Vídeo e áudio", EXTS), ("Todos os arquivos", "*.*")]

C = {
    "bg": "#171717", "side": "#141414", "card": "#1c1c1c", "card2": "#232323", "hover": "#262626",
    "line": "#2a2a2a", "line2": "#363636", "text": "#e5e5e5", "dim": "#8f8f8f", "dim2": "#5f5f5f",
    "sel": "#b7c3d3", "sel_text": "#111418", "sel_hover": "#c8d2df", "track": "#333333", "bar": "#d4d4d4",
    "ok": "#22c55e", "ok_bg": "#15291c", "ok_fg": "#4ade80",
    "err": "#ef4444", "err_bg": "#2d1515", "err_fg": "#f87171", "err_hover": "#3a1b1b",
    "warn": "#f5b94a", "badge": "#2b2b2b",
}
R = 10          # raio dos cartões
R_SMALL = 8     # raio de botões, campos e itens do menu
SPIN = "◐◓◑◒"

ICONS_MDL2 = {"home": "", "history": "", "settings": "", "play": "",
              "stop": "", "folder": "", "video": "", "audio": "",
              "sync": "", "file": "", "trash": "", "check": "",
              "drop": "", "logo": "", "share": "", "link": "",
              "refresh": "", "queue": "", "add": "", "up": "", "down": "",
              "pause": ""}
ICONS_TXT = {"home": "⌂", "history": "↺", "settings": "⚙", "play": "▶", "stop": "■", "folder": "▤",
             "video": "▣", "audio": "♪", "sync": "⇄", "file": "▤", "trash": "✕", "check": "✓",
             "drop": "↓", "logo": "♪", "share": "⇪", "link": "⛓", "refresh": "↻",
             "queue": "≡", "add": "+", "up": "↑", "down": "↓", "pause": "‖"}

STATUS = {
    None: ("Aguardando", C["badge"], C["dim"]),
    "run": ("Executando", C["sel"], C["sel_text"]),
    "ok": ("Concluído", C["ok_bg"], C["ok_fg"]),
    "err": ("Erro", C["err_bg"], C["err_fg"]),
    "skip": ("Pulado", C["badge"], C["dim2"]),
}
WEIGHTS = {"ler": 1, "orig": 14, "dub": 14, "sync": 6, "ajuste": 15, "gerar": 40, "conferir": 2,
           "torrent": 6, "limpar": 2}


# ------------------------------------------------------------------ utilidades
def _data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "DublaSync")
    os.makedirs(d, exist_ok=True)
    return d


def _load(name, default):
    try:
        with open(os.path.join(_data_dir(), name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _save(name, data):
    try:
        with open(os.path.join(_data_dir(), name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


def fmt_secs(s):
    s = int(s or 0)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else (f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s")


def fmt_size(b):
    b = float(b or 0)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if b < 1024 or u == "TiB":
            return f"{b:.2f} {u}".replace(".", ",") if u in ("GiB", "TiB") else f"{b:.0f} {u}"
        b /= 1024


def compact_speed(t):
    t = t.replace("dublagem ", "").replace("original ", "")
    return t.replace(" fps →", " →")


def open_folder(path):
    try:
        if os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
            else:
                os.startfile(path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path)))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", os.path.dirname(os.path.abspath(path))])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(os.path.abspath(path))])
    except OSError:
        pass


def rr_points(x0, y0, x1, y1, r):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def rrect(cv, x0, y0, x1, y1, r, **kw):
    return cv.create_polygon(rr_points(x0, y0, x1, y1, r), smooth=True, **kw)


# ---------------------------------------------------------- componentes
class RoundBox(tk.Canvas):
    """Caixa com cantos arredondados e borda fina. Coloque os filhos em .inner.
    fill_height=True: a caixa ocupa a altura que o layout der (use com expand)."""

    def __init__(self, parent, outer=None, fill=None, border=None, radius=R, pad=(16, 12), fill_height=False):
        outer = outer or C["bg"]
        super().__init__(parent, bg=outer, highlightthickness=0, bd=0, height=pad[1] * 2 + 2)
        self.fill_c, self.border_c, self.r, self.pad, self.fh = fill or C["card"], border or C["line"], radius, pad, fill_height
        self.inner = tk.Frame(self, bg=self.fill_c)
        self.win = self.create_window(pad[0], pad[1], window=self.inner, anchor="nw")
        self.bind("<Configure>", self._on_cfg)
        if not fill_height:
            self.inner.bind("<Configure>", self._on_inner)

    def _on_inner(self, _):
        h = self.inner.winfo_reqheight() + 2 * self.pad[1]
        if abs(int(float(self.cget("height"))) - h) > 0:
            self.configure(height=h)

    def _on_cfg(self, e):
        kw = {"width": max(1, e.width - 2 * self.pad[0])}
        if self.fh:
            kw["height"] = max(1, e.height - 2 * self.pad[1])
        self.itemconfigure(self.win, **kw)
        self.redraw()

    def redraw(self):
        self.delete("shape")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        rrect(self, 0, 0, w - 1, h - 1, self.r, fill=self.fill_c, outline=self.border_c, tags="shape")
        self.tag_lower("shape")

    def set_border(self, color):
        self.border_c = color
        self.redraw()


class RoundButton(tk.Canvas):
    KINDS = {"primary": ("sel", "sel_text", "sel_hover", None),
             "ghost": ("card", "text", "hover", "line2"),
             "danger": ("err_bg", "err_fg", "err_hover", None)}

    def __init__(self, parent, app, text, icon=None, kind="ghost", command=None, outer=None):
        super().__init__(parent, bg=outer or C["bg"], highlightthickness=0, bd=0, height=36, cursor="hand2")
        self.app, self.text, self.icon, self.kind, self.command = app, text, icon, kind, command
        self.enabled, self.hover = True, False
        self.bind("<Button-1>", lambda e: self.enabled and self.command and self.command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._size()

    def _size(self):
        f = tkfont.Font(root=self, font=self.app.F(10, "bold"))
        w = f.measure(self.text) + 28 + (22 if self.icon else 0)
        self.configure(width=w)
        self.draw()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = int(self.cget("width")), int(self.cget("height"))
        bg, fg, hov, border = (C[k] if k else None for k in self.KINDS[self.kind])
        fill = hov if (self.hover and self.enabled) else bg
        if not self.enabled:
            fill, border = C["card2"], C["line2"]
        rrect(self, 0, 0, w - 1, h - 1, R_SMALL, fill=fill, outline=border or fill)
        col = fg if self.enabled else C["dim2"]
        x = 14
        if self.icon:
            self.create_text(x, h / 2, text=self.app.ICON.get(self.icon, ""), anchor="w", fill=col,
                             font=self.app.IF(10))
            x += 22
        self.create_text(x, h / 2, text=self.text, anchor="w", fill=col, font=self.app.F(10, "bold"))

    def set_enabled(self, on):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self.draw()

    def set_text(self, t):
        self.text = t
        self._size()


class NavItem(tk.Canvas):
    def __init__(self, parent, app, key, icon, label):
        super().__init__(parent, bg=C["side"], highlightthickness=0, bd=0, height=38, cursor="hand2")
        self.app, self.key, self.icon, self.label = app, key, icon, label
        self.selected = self.hover = False
        self.badge = ""
        self.bind("<Button-1>", lambda e: app.show(key))
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Configure>", lambda e: self.draw())

    def _h(self, on):
        self.hover = on
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if w < 10:
            return
        if self.selected:
            rrect(self, 0, 1, w - 1, h - 2, R_SMALL, fill=C["sel"], outline=C["sel"])
        elif self.hover:
            rrect(self, 0, 1, w - 1, h - 2, R_SMALL, fill=C["hover"], outline=C["hover"])
        fg = C["sel_text"] if self.selected else C["text"]
        self.create_text(16, h / 2, text=self.app.ICON[self.icon], anchor="w",
                         fill=C["sel_text"] if self.selected else C["dim"], font=self.app.IF(11))
        self.create_text(42, h / 2, text=self.label, anchor="w", fill=fg, font=self.app.F(10, "bold"))
        if self.badge:
            f = self.app.F(8, "bold")
            tw = tkfont.Font(root=self, font=f).measure(self.badge)
            bx = w - 12 - tw - 14
            bg = C["sel_text"] if self.selected else C["badge"]
            rrect(self, bx, h / 2 - 9, w - 12, h / 2 + 9, 9, fill=bg, outline=bg)
            self.create_text(bx + 7, h / 2, text=self.badge, anchor="w",
                             fill=C["sel"] if self.selected else C["text"], font=f)


class ScrollPage(tk.Frame):
    """Página com rolagem vertical (roda do mouse)."""

    def __init__(self, parent, padx=26, pady=22):
        super().__init__(parent, bg=C["bg"])
        self.cv = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=C["bg"], padx=padx, pady=pady)
        self.win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self.win, width=e.width))
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.bind("<Enter>", lambda e: self._wheel(True))
        self.bind("<Leave>", lambda e: self._wheel(False))

    def _wheel(self, on):
        if on:
            self.bind_all("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1))
            self.bind_all("<Button-4>", lambda e: self._scroll(-1))
            self.bind_all("<Button-5>", lambda e: self._scroll(1))
        else:
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.unbind_all(ev)

    def _scroll(self, d):
        if self.inner.winfo_reqheight() > self.cv.winfo_height():
            self.cv.yview_scroll(d * 3, "units")


# ------------------------------------------------------------------ aplicativo
class App:
    def __init__(self):
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            self.root = TkinterDnD.Tk()
            self.DND = DND_FILES
        except Exception:  # noqa: BLE001
            self.root = tk.Tk()
            self.DND = None
        r = self.root
        r.title("DublaSync")
        r.configure(bg=C["bg"])
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(1180, sw - 40), min(920, sh - 80)
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        r.minsize(min(1000, w), min(700, h))
        self.compact = h < 880
        r.report_callback_exception = self._callback_error
        fams = set(tkfont.families(r))
        self.family = next((f for f in ("Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans") if f in fams),
                           "TkDefaultFont")
        icon_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.ICON = ICONS_MDL2 if icon_font else ICONS_TXT
        self.icon_family = icon_font or self.family
        self.cfg = {"lang": "Português", "codec": "ac3", "style": "cena", "dub_default": True,
                    "remove_old": True, "folder": "", "torrent": True, "private": False,
                    "trackers": list(tor.DEFAULT_TRACKERS), "source": "", "fast": True}
        self.cfg.update(_load("config.json", {}))
        self.history = _load("historico.json", [])
        self.st = {"dub": None, "orig": None, "oinfo": None, "dinfo": None, "running": False,
                   "result": None, "rep": None, "start": None, "status": {}, "frac": {}, "detail": {},
                   "t0": {}, "t1": {}, "spin": 0, "info": {}, "hover": {}}
        self.name_var = tk.StringVar()
        self.auto_name = ""
        self.page = None
        self.queue = _load("fila.json", [])
        for j in self.queue:                      # trabalho interrompido volta para a espera
            if j.get("status") == "run":
                j.update(status="wait", frac=0.0, detail="")
            j.pop("session", None)
        self.current = None
        self.queue_paused = False
        self.q_sel = None
        self._style()
        self._layout()
        self.show("novo")
        self._save_queue()
        self._queue_buttons()
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        if any(j["status"] == "wait" for j in self.queue):
            self.set_status(f"{sum(1 for j in self.queue if j['status'] == 'wait')} trabalho(s) esperando na fila "
                            f"— abra a Fila e clique em Iniciar", C["warn"])
        threading.Thread(target=self._check_ffmpeg, daemon=True).start()
        threading.Thread(target=self._clean_temp, daemon=True).start()
        self._tick()

    @staticmethod
    def _clean_temp():
        """Remove pastas temporárias de trabalhos interrompidos (programa fechado no meio)."""
        import glob
        import shutil
        import tempfile
        for d in glob.glob(os.path.join(tempfile.gettempdir(), "dublasync_*")):
            try:
                if time.time() - os.path.getmtime(d) > 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def _post(self, fn):
        """Agenda algo na tela a partir de outro thread; ignora se a janela já fechou."""
        if getattr(self, "_closing", False):
            return
        try:
            self.root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def _callback_error(self, exc, val, tb):
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(os.path.join(_data_dir(), "erro.txt"), "a", encoding="utf-8") as f:
                f.write(dt.datetime.now().isoformat() + "\n" + text + "\n")
        except OSError:
            pass
        messagebox.showerror("DublaSync — erro", text[-1200:])

    # --------------------------------------------------------------- base
    def F(self, size, weight="normal"):
        return (self.family, size, weight)

    def IF(self, size):
        return (self.icon_family, size)

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Flat.TCombobox", fieldbackground=C["card2"], background=C["card2"], foreground=C["text"],
                     arrowcolor=C["dim"], bordercolor=C["card2"], lightcolor=C["card2"], darkcolor=C["card2"],
                     padding=4, relief="flat")
        st.map("Flat.TCombobox", fieldbackground=[("readonly", C["card2"])], foreground=[("readonly", C["text"])],
               selectbackground=[("readonly", C["card2"])], selectforeground=[("readonly", C["text"])],
               bordercolor=[("focus", C["card2"])], background=[("active", C["card2"])])
        self.root.option_add("*TCombobox*Listbox.background", C["card2"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["sel"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["sel_text"])
        self.root.option_add("*TCombobox*Listbox.font", self.F(10))

    def card(self, parent, outer=None, pad=(16, 12), fill_height=False):
        return RoundBox(parent, outer=outer or C["bg"], pad=pad, fill_height=fill_height)

    def button(self, parent, text, icon=None, kind="ghost", command=None, outer=None):
        return RoundButton(parent, self, text, icon, kind, command, outer=outer or C["bg"])

    def entry(self, parent, var, outer=None):
        box = RoundBox(parent, outer=outer or C["card"], fill=C["card2"], border=C["line2"], radius=R_SMALL,
                       pad=(10, 4 if self.compact else 6))
        e = tk.Entry(box.inner, textvariable=var, bg=C["card2"], fg=C["text"], relief="flat", bd=0,
                     highlightthickness=0, insertbackground=C["text"], font=self.F(10),
                     disabledbackground=C["card2"], disabledforeground=C["dim"])
        e.pack(fill="x", ipady=2)
        e.bind("<FocusIn>", lambda ev: box.set_border(C["sel"]))
        e.bind("<FocusOut>", lambda ev: box.set_border(C["line2"]))
        return box, e

    def combo(self, parent, var, values, width=40, outer=None):
        box = RoundBox(parent, outer=outer or C["card"], fill=C["card2"], border=C["line2"], radius=R_SMALL,
                       pad=(4, 2))
        cb = ttk.Combobox(box.inner, textvariable=var, values=values, state="readonly", width=width,
                          font=self.F(10), style="Flat.TCombobox")
        cb.pack(fill="x")
        return box, cb

    def font(self, font):
        """Objetos de fonte em cache (criar um por desenho custa caro e trava a sincronização)."""
        cache = self.__dict__.setdefault("_fonts", {})
        if font not in cache:
            cache[font] = tkfont.Font(root=self.root, font=font)
        return cache[font]

    def fit(self, text, font, maxw, end=False):
        key = (text, font, int(maxw), end)
        cache = self.__dict__.setdefault("_fitc", {})
        if key in cache:
            return cache[key]
        if len(cache) > 4000:
            cache.clear()
        cache[key] = self._fit(text, font, maxw, end)
        return cache[key]

    def _fit(self, text, font, maxw, end=False):
        f = self.font(font)
        if f.measure(text) <= maxw:
            return text
        if end:
            while text and f.measure(text + "…") > maxw:
                text = text[:-1]
            return text.rstrip(" ·") + "…"
        a, b = text[: len(text) // 2], text[len(text) // 2:]
        while a and b and f.measure(a + "…" + b) > maxw:
            a, b = a[:-1], b[1:]
        return a + "…" + b

    def toggle(self, parent, key, label, hint="", on_change=None):
        row = tk.Frame(parent, bg=C["card"])
        cv = tk.Canvas(row, width=38, height=22, bg=C["card"], highlightthickness=0, cursor="hand2")
        cv.pack(side="left", anchor="n", pady=2)
        txt = tk.Frame(row, bg=C["card"])
        txt.pack(side="left", padx=(12, 0), fill="x")
        tk.Label(txt, text=label, bg=C["card"], fg=C["text"], font=self.F(10, "bold"), anchor="w").pack(fill="x")
        if hint:
            tk.Label(txt, text=hint, bg=C["card"], fg=C["dim"], font=self.F(9), anchor="w",
                     justify="left", wraplength=700).pack(fill="x")

        def draw():
            cv.delete("all")
            on = self.cfg[key]
            col = C["sel"] if on else C["line2"]
            rrect(cv, 1, 1, 37, 21, 10, fill=col, outline=col)
            x = 27 if on else 11
            cv.create_oval(x - 8, 3, x + 8, 19, fill=C["sel_text"] if on else C["dim"], outline="")

        def flip(_):
            self.cfg[key] = not self.cfg[key]
            _save("config.json", self.cfg)
            draw()
            self.refresh_summary()
            if on_change:
                on_change()

        cv.bind("<Button-1>", flip)
        draw()
        return row

    def header(self, parent, title, sub):
        h = tk.Frame(parent, bg=C["bg"])
        h.pack(fill="x", pady=(0, 10 if self.compact else 16))
        left = tk.Frame(h, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text=title, bg=C["bg"], fg=C["text"], font=self.F(18, "bold"), anchor="w").pack(anchor="w")
        tk.Label(left, text=sub, bg=C["bg"], fg=C["dim"], font=self.F(10), anchor="w").pack(anchor="w", pady=(2, 0))
        right = tk.Frame(h, bg=C["bg"])
        right.pack(side="right", anchor="s")
        return right

    # ------------------------------------------------------------- layout
    def _layout(self):
        r = self.root
        # barra de status (antes do corpo, para não ser engolida)
        bar = tk.Frame(r, bg=C["side"], height=30)
        bar.pack(fill="x", side="bottom")
        tk.Frame(r, bg=C["line"], height=1).pack(fill="x", side="bottom")
        self.sb_dot = tk.Label(bar, text="●", bg=C["side"], fg=C["ok"], font=self.F(9))
        self.sb_dot.pack(side="left", padx=(14, 4), pady=5)
        self.sb_left = tk.Label(bar, text="Pronto", bg=C["side"], fg=C["text"], font=self.F(9))
        self.sb_left.pack(side="left")
        self.sb_right = tk.Label(bar, text="", bg=C["side"], fg=C["dim"], font=self.F(9))
        self.sb_right.pack(side="right", padx=14)
        self.sb_mid = tk.Label(bar, text="", bg=C["side"], fg=C["dim"], font=self.F(9))
        self.sb_mid.pack(side="right", padx=14)

        body = tk.Frame(r, bg=C["bg"])
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=C["side"], width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=C["line"], width=1).pack(side="left", fill="y")
        logo = tk.Frame(side, bg=C["side"])
        logo.pack(fill="x", padx=18, pady=(18, 18))
        tk.Label(logo, text=self.ICON["logo"], bg=C["side"], fg=C["sel"], font=self.IF(15)).pack(side="left")
        tk.Label(logo, text="DublaSync", bg=C["side"], fg=C["text"], font=self.F(14, "bold")).pack(side="left", padx=8)

        self.nav = {}
        for key, icon, label in (("novo", "home", "Novo trabalho"), ("fila", "queue", "Fila"),
                                 ("hist", "history", "Histórico"), ("share", "share", "Compartilhar"),
                                 ("cfg", "settings", "Configurações")):
            n = NavItem(side, self, key, icon, label)
            n.pack(fill="x", padx=10, pady=1)
            self.nav[key] = n

        tk.Frame(side, bg=C["line"], height=1).pack(fill="x", padx=12, pady=(16, 12))
        tk.Label(side, text="FERRAMENTAS", bg=C["side"], fg=C["dim"], font=self.F(8, "bold"),
                 anchor="w").pack(fill="x", padx=18)
        self.tool_rows = {}
        for key, label in (("ffmpeg", "FFmpeg"), ("dnd", "Arrastar e soltar"), ("trash", "Lixeira")):
            row = tk.Frame(side, bg=C["side"])
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=label, bg=C["side"], fg=C["text"], font=self.F(10), anchor="w").pack(
                side="left", padx=(6, 0), pady=6)
            dot = tk.Label(row, text="●", bg=C["side"], fg=C["dim2"], font=self.F(9))
            dot.pack(side="right", padx=6)
            sub = tk.Label(row, text="", bg=C["side"], fg=C["dim"], font=self.F(8))
            sub.pack(side="right")
            self.tool_rows[key] = (dot, sub)
        try:
            import send2trash  # noqa: F401
            self._tool("trash", True, "")
        except ImportError:
            self._tool("trash", False, "pasta _antigos")
        self._tool("dnd", bool(self.DND), "" if self.DND else "rode instalar.bat")
        tk.Label(side, text=f"Versão {VERSION}\n© DublaSync", bg=C["side"], fg=C["dim2"], font=self.F(8),
                 justify="left", anchor="w").pack(side="bottom", fill="x", padx=18, pady=14)
        tk.Frame(side, bg=C["line"], height=1).pack(side="bottom", fill="x")

        main = tk.Frame(body, bg=C["bg"])
        main.pack(side="left", fill="both", expand=True)
        self.pages = {"novo": self._page_novo(main), "fila": self._page_fila(main), "hist": self._page_hist(main),
                      "share": self._page_share(main), "cfg": self._page_cfg(main)}
        self.refresh_summary()

    def _tool(self, key, ok, sub):
        dot, lab = self.tool_rows[key]
        dot.configure(fg=C["ok"] if ok else C["err"] if key == "ffmpeg" else C["warn"])
        lab.configure(text=sub)

    def _check_ffmpeg(self):
        v = core.ffmpeg_version()
        soxr = core.has_soxr() if v else False

        def upd():
            self._tool("ffmpeg", bool(v), f"v{v}" if v else "não encontrado")
            self.sb_mid.configure(text=f"{core.THREADS} threads  ·  FFmpeg {v or '—'}  ·  "
                                       f"resampler {'soxr' if soxr else 'padrão'}")
            if not v:
                self.set_status("FFmpeg não encontrado — rode o instalar.bat", C["err"])
        self._post(upd)

    def show(self, key):
        self.page = key
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav[k].selected = (k == key)
            self.nav[k].draw()
        self.pages[key].pack(fill="both", expand=True)
        if key == "hist":
            self.draw_hist()
        if key == "fila":
            self.draw_queue()
        if key == "novo":
            self.draw_table()
            self.draw_allbar()
        if key == "share":
            self.refresh_share()

    # ---------------------------------------------------------- página: novo
    def _page_novo(self, parent):
        g = 8 if self.compact else 14
        pg = tk.Frame(parent, bg=C["bg"], padx=22 if self.compact else 26, pady=12 if self.compact else 22)
        actions = self.header(pg, "Novo trabalho", "Dublagem + vídeo em alta  →  MKV dual áudio")
        self.actions = actions
        self.btn_go = self.button(actions, "Gerar agora", "play", "primary", self.on_go)
        self.btn_queue = self.button(actions, "Adicionar à fila", "add", "ghost", lambda: self.enqueue())
        self.btn_cancel = self.button(actions, "Cancelar atual", "stop", "danger", self.on_cancel)
        self.btn_folder = self.button(actions, "Abrir pasta", "folder", "ghost",
                                      lambda: self.st["result"] and open_folder(self.st["result"]["out"]))
        self.btn_magnet = self.button(actions, "Copiar magnet", "link", "ghost", self.copy_magnet)

        zr = tk.Frame(pg, bg=C["bg"])
        zr.pack(fill="x")
        zr.columnconfigure(0, weight=1, uniform="z")
        zr.columnconfigure(1, weight=1, uniform="z")
        self.zones = {}
        for col, key, title, hint in ((0, "dub", "ARQUIVO DUBLADO", "Arraste o dublado aqui (ou os dois juntos)"),
                                      (1, "orig", "ORIGINAL EM ALTA QUALIDADE", "Arraste o original aqui")):
            cv = tk.Canvas(zr, height=80 if self.compact else 96, bg=C["bg"], highlightthickness=0, cursor="hand2")
            cv.grid(row=0, column=col, sticky="ew", padx=(0, 7) if col == 0 else (7, 0))
            self.zones[key] = {"cv": cv, "title": title, "hint": hint}
            cv.bind("<Configure>", lambda e, k=key: self.draw_zone(k))
            cv.bind("<Button-1>", lambda e, k=key: self.browse(k))
            cv.bind("<Enter>", lambda e, k=key: self._zone_hover(k, True))
            cv.bind("<Leave>", lambda e, k=key: self._zone_hover(k, False))
            if self.DND:
                cv.drop_target_register(self.DND)
                cv.dnd_bind("<<DropEnter>>", lambda e, k=key: (self._zone_hover(k, True), e.action)[1])
                cv.dnd_bind("<<DropLeave>>", lambda e, k=key: (self._zone_hover(k, False), e.action)[1])
                cv.dnd_bind("<<Drop>>", lambda e, k=key: self.on_drop(e, k))
        if self.DND:
            self.root.drop_target_register(self.DND)
            self.root.dnd_bind("<<Drop>>", lambda e: self.on_drop(e, None))

        sr = tk.Frame(pg, bg=C["bg"])
        sr.pack(fill="x", pady=(g, 0))
        self.stats = {}
        for i, (key, title, icon) in enumerate((("orig", "Original", "video"), ("dub", "Dublagem", "audio"),
                                                ("sync", "Sincronia", "sync"), ("out", "Arquivo novo", "file"))):
            sr.columnconfigure(i, weight=1, uniform="s")
            box = self.card(sr, pad=(16, 8 if self.compact else 12))
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 7, 0 if i == 3 else 7))
            c = box.inner
            top = tk.Frame(c, bg=C["card"])
            top.pack(fill="x")
            tk.Label(top, text=title, bg=C["card"], fg=C["text"], font=self.F(10, "bold")).pack(side="left")
            tk.Label(top, text=self.ICON[icon], bg=C["card"], fg=C["dim"], font=self.IF(11)).pack(side="right")
            val = tk.Label(c, text="—", bg=C["card"], fg=C["text"], font=self.F(14 if self.compact else 17, "bold"),
                           anchor="w")
            val.pack(fill="x", pady=(4 if self.compact else 8, 0))
            sub = tk.Label(c, text="", bg=C["card"], fg=C["dim"], font=self.F(9), anchor="w")
            sub.pack(fill="x")
            self.stats[key] = (val, sub, box)

        ob = self.card(pg, pad=(16, 10 if self.compact else 14))
        ob.pack(fill="x", pady=(g, 0))
        oc = ob.inner
        oc.columnconfigure(1, weight=1)
        tk.Label(oc, text="Nome do arquivo", bg=C["card"], fg=C["dim"], font=self.F(9)).grid(
            row=0, column=0, sticky="w", padx=(0, 12))
        self.name_box, self.name_entry = self.entry(oc, self.name_var)
        self.name_box.grid(row=0, column=1, columnspan=2, sticky="ew")
        tk.Label(oc, text="Pasta", bg=C["card"], fg=C["dim"], font=self.F(9)).grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
        self.folder_lbl = tk.Label(oc, text="mesma pasta do original", bg=C["card"], fg=C["text"],
                                   font=self.F(9), anchor="w")
        self.folder_lbl.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        lk = tk.Label(oc, text="alterar", bg=C["card"], fg=C["sel"], font=self.F(9, "underline"), cursor="hand2")
        lk.grid(row=1, column=2, sticky="e", pady=(8, 0))
        lk.bind("<Button-1>", lambda e: self.change_folder())
        tk.Label(oc, text="Opções", bg=C["card"], fg=C["dim"], font=self.F(9)).grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(6, 0))
        self.summary_lbl = tk.Label(oc, text="", bg=C["card"], fg=C["text"], font=self.F(9), anchor="w")
        self.summary_lbl.grid(row=2, column=1, sticky="ew", pady=(6, 0), padx=(0, 12))
        self.summary_lbl.bind("<Configure>", lambda e: self._fit_summary())
        lk2 = tk.Label(oc, text="configurações", bg=C["card"], fg=C["sel"], font=self.F(9, "underline"),
                       cursor="hand2")
        lk2.grid(row=2, column=2, sticky="e", pady=(6, 0))
        lk2.bind("<Button-1>", lambda e: self.show("cfg"))
        self.folder = {"path": self.cfg.get("folder") or "", "custom": bool(self.cfg.get("folder"))}
        if self.folder["custom"]:
            self.folder_lbl.configure(text=self.folder["path"])

        sb = self.card(pg, pad=(1, 1), fill_height=True)
        sb.pack(fill="both", expand=True, pady=(g, 0))
        sc = sb.inner
        top = tk.Frame(sc, bg=C["card"])
        top.pack(fill="x", padx=16, pady=(8 if self.compact else 12, 6))
        self.steps_title = tk.Label(top, text="Etapas", bg=C["card"], fg=C["text"], font=self.F(11, "bold"))
        self.steps_title.pack(side="left")
        self.total_lbl = tk.Label(top, text="", bg=C["card"], fg=C["dim"], font=self.F(9))
        self.total_lbl.pack(side="right")
        self.allbar = tk.Canvas(sc, height=6, bg=C["card"], highlightthickness=0)
        self.allbar.pack(fill="x", padx=16, pady=(0, 2 if self.compact else 4))
        self.allbar.bind("<Configure>", lambda e: self.draw_allbar())
        self.table = tk.Canvas(sc, bg=C["card"], highlightthickness=0, height=200)
        self.table.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.table.bind("<Configure>", lambda e: self.draw_table())
        self._update_buttons()
        return pg

    def _zone_hover(self, k, on):
        self.st["hover"][k] = on
        self.draw_zone(k)

    def draw_zone(self, key):
        z = self.zones[key]
        cv = z["cv"]
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 20:
            return
        path = self.st[key]
        hover = self.st["hover"].get(key)
        bg = C["hover"] if hover else C["card"]
        border = C["sel"] if hover else C["line2"]
        rrect(cv, 1, 1, w - 2, h - 2, R, fill=bg, outline=border, dash=() if path else (5, 4))
        cv.create_text(18, 20, text=z["title"], anchor="w", fill=C["dim"], font=self.F(8, "bold"))
        if path:
            cv.create_text(w - 18, 20, text="trocar", anchor="e", fill=C["sel"], font=self.F(9))
            y1, y2 = (44, 64) if self.compact else (48, 74)
            cv.create_text(18, y1, text=self.fit(os.path.basename(path), self.F(11, "bold"), w - 40),
                           anchor="w", fill=C["text"], font=self.F(11, "bold"))
            info = self.st["info"].get(key, "lendo informações…")
            cv.create_text(18, y2, text=self.fit(info, self.F(9), w - 40, end=True), anchor="w", fill=C["dim"],
                           font=self.F(9))
        else:
            cv.create_text(w / 2, h / 2 + 6, text=self.ICON["drop"], fill=C["sel"] if hover else C["dim"],
                           font=self.IF(16), anchor="s")
            cv.create_text(w / 2, h / 2 + 12, text=z["hint"] + " · ou clique para procurar",
                           fill=C["dim"], font=self.F(9), anchor="n", width=w - 40, justify="center")

    def on_drop(self, e, key):
        files = [p for p in self.root.tk.splitlist(e.data) if os.path.isfile(p)]
        if key:
            self.st["hover"][key] = False
            self.draw_zone(key)
        if len(files) >= 2:
            self.assign_pair(files[:2])
        elif files:
            self.set_file(key or ("dub" if not self.st["dub"] else "orig"), files[0])
        return e.action

    def assign_pair(self, files):
        def work():
            scored = []
            for f in files:
                try:
                    h = core.video_height(core.probe(f))
                except core.SyncError:
                    h = 0
                scored.append((h, os.path.getsize(f), f))
            scored.sort()
            self._post(lambda: (self.set_file("dub", scored[0][2]), self.set_file("orig", scored[1][2])))
        threading.Thread(target=work, daemon=True).start()

    def browse(self, key):
        p = filedialog.askopenfilename(filetypes=FILETYPES)
        if p:
            self.set_file(key, p)

    def set_file(self, key, path):
        if self.st["result"] and not self.current:
            self.st["result"] = None
            self.name_var.set("")
            self.auto_name = ""
            self._stat("sync", "—", "")
            self._stat("out", "—", "")
        self.name_entry.configure(state="normal")
        self.st[key] = path
        self.st["info"].pop(key, None)
        if key == "orig":
            self.st["oinfo"] = None
            self.name_var.set("")
            self.auto_name = ""
            if not self.folder["custom"]:
                self.folder["path"] = os.path.dirname(os.path.abspath(path))
                self.folder_lbl.configure(text=self.folder["path"])
        if not self.current:
            self.reset_steps()
        self.draw_zone(key)
        self._update_buttons()

        def work():
            try:
                info = core.probe(path)
                txt = core.describe_file(info)
            except Exception as ex:  # noqa: BLE001
                info, txt = None, f"não consegui ler este arquivo ({ex})"[:120]
            self.st["info"][key] = txt
            self._post(lambda: self._file_ready(key, info))
        threading.Thread(target=work, daemon=True).start()

    def _file_ready(self, key, info):
        self.draw_zone(key)
        if not info:
            return
        if key == "orig":
            self.st["oinfo"] = info
            v = naming.video_info(info) or {}
            a = naming._main_audio(info)
            an, ach, atm = naming.audio_tag(a) if a else ("", "", False)
            try:
                d = core.fmt_clock(core.duration_of(info))
            except core.SyncError:
                d = ""
            res = v.get("res") or "Áudio"
            hdr = " " + " ".join(v.get("hdr") or []) if v.get("hdr") else ""
            self._stat("orig", res + hdr, f"{d} · {an}{' Atmos' if atm else ''} {ach} · {v.get('codec', '')}")
            self.refresh_name()
        else:
            self.st["dinfo"] = info
            aud = core.audio_streams(info)
            if aud:
                idx = core.pick_dub_stream(info, core.LANGS[self.cfg["lang"]])
                an, ach, _ = naming.audio_tag(aud[idx])
                try:
                    d = core.fmt_clock(core.duration_of(info))
                except core.SyncError:
                    d = ""
                ext = os.path.splitext(self.st["dub"])[1].lstrip(".").upper()
                self._stat("dub", f"{an} {ach}", f"{d} · {ext} · faixa {idx + 1} de {len(aud)}")
            if self.st["orig"] and self.st["oinfo"]:
                self.refresh_name()

    def _stat(self, key, value, sub):
        val, s, _ = self.stats[key]
        val.configure(text=value)
        s.configure(text=self.fit(sub, self.F(9), max(120, val.winfo_width() or 200), end=True))

    def change_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder["path"] or None)
        if d:
            self.folder.update(path=d, custom=True)
            self.folder_lbl.configure(text=d)

    def refresh_name(self, force=False):
        if not self.st["orig"]:
            return
        cur = self.name_var.get().strip()
        if not force and cur and cur != self.auto_name:
            return
        new = os.path.basename(core.default_output(self.st["orig"], self.st["oinfo"],
                                                   core.LANGS[self.cfg["lang"]], self.cfg["style"]))
        self.auto_name = new
        self.name_var.set(new)

    def refresh_summary(self):
        if not hasattr(self, "summary_lbl"):
            return
        style = "padrão torrent" if self.cfg["style"] == "cena" else "padrão Plex/Radarr"
        parts = [self.cfg["lang"], core.QUALITY[self.cfg["codec"]].split("(")[0].strip(), style,
                 "dublagem padrão" if self.cfg["dub_default"] else "original padrão",
                 (".torrent " + ("privado" if self.cfg["private"] else "público")) if self.cfg["torrent"] else "sem .torrent",
                 "antigos → Lixeira" if self.cfg["remove_old"] else "mantém os antigos"]
        self.summary_full = "  ·  ".join(parts)
        self._fit_summary()
        self.sb_right.configure(text="Retirar antigos: " + ("ligado" if self.cfg["remove_old"] else "desligado"))

    def _fit_summary(self):
        w = self.summary_lbl.winfo_width()
        txt = getattr(self, "summary_full", "")
        self.summary_lbl.configure(text=self.fit(txt, self.F(9), w - 4, end=True) if w > 40 else txt)

    # tabela de etapas
    def reset_steps(self):
        for k in ("status", "frac", "detail", "t0", "t1"):
            self.st[k].clear()
        self.st["start"] = None
        self.total_lbl.configure(text="")
        self.draw_table()
        self.draw_allbar()

    def overall(self):
        tot = sum(WEIGHTS.values())
        done = sum(WEIGHTS[k] * (1.0 if self.st["status"].get(k) in ("ok", "skip") else self.st["frac"].get(k, 0))
                   for k in WEIGHTS)
        return done / tot

    def draw_allbar(self):
        cv = self.allbar
        cv.delete("all")
        w = cv.winfo_width()
        if w < 10:
            return
        f = self.overall()
        self._pill(cv, 0, 0, w, 6, C["track"])
        if f > 0:
            self._pill(cv, 0, 0, max(6, w * f), 6, C["ok"] if f >= 0.999 else C["bar"])

    def _pill(self, cv, x0, y0, x1, y1, color):
        r = (y1 - y0) / 2
        cv.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline=color)
        cv.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline=color)
        if x1 - x0 > 2 * r:
            cv.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline=color)

    def _badge(self, cv, x, y, text, bg, fg):
        f = self.F(8, "bold")
        tw = self.font(f).measure(text)
        bh = getattr(self, "_badge_h", 10) if cv is self.table else 10
        self._pill(cv, x, y - bh, x + tw + 18, y + bh, bg)
        cv.create_text(x + 9, y, text=text, anchor="w", fill=fg, font=f)

    def draw_table(self):
        cv = self.table
        cv.delete("all")
        W = cv.winfo_width()
        if W < 50:
            return
        lw = max(self.font(self.F(10, "bold")).measure(lab) for _, lab in core.STAGES)
        x_prog = 10 + 28 + lw + 28
        x_stat = x_prog + 110 + 58
        x_time = x_stat + 112
        x_det = x_time + 62
        cols = [("Etapa", 10), ("Progresso", x_prog), ("Status", x_stat), ("Tempo", x_time), ("Detalhes", x_det)]
        for name, x in cols:
            cv.create_text(x, 14, text=name, anchor="w", fill=C["dim"], font=self.F(9, "bold"))
        cv.create_line(0, 28, W, 28, fill=C["line"])
        n = len(core.STAGES)
        rh = int(max(17, min(36, (cv.winfo_height() - 30) / n)))
        self._badge_h = min(10, rh / 2 - 1)
        now = time.time()
        for i, (key, label) in enumerate(core.STAGES):
            y0 = 28 + i * rh
            yc = y0 + rh / 2
            stt = self.st["status"].get(key)
            if stt == "run":
                rrect(cv, 2, y0 + 2, W - 2, y0 + rh - 1, 6, fill=C["hover"], outline=C["hover"])
            cv.create_text(cols[0][1], yc, text=f"{i + 1:02d}", anchor="w", fill=C["dim2"], font=self.F(9))
            cv.create_text(cols[0][1] + 28, yc, text=label, anchor="w",
                           fill=C["text"] if stt else C["dim"], font=self.F(10, "bold" if stt == "run" else "normal"))
            frac = 1.0 if stt == "ok" else self.st["frac"].get(key, 0.0)
            bx, bw = cols[1][1], 110
            self._pill(cv, bx, yc - 3, bx + bw, yc + 3, C["track"])
            if frac > 0 and stt != "skip":
                col = C["ok"] if stt == "ok" else C["err"] if stt == "err" else C["bar"]
                self._pill(cv, bx, yc - 3, bx + max(6, bw * frac), yc + 3, col)
            pct = "—" if stt == "skip" else (f"{frac * 100:.0f}%" if frac >= 0.995 or frac == 0 else f"{frac * 100:.1f}%")
            cv.create_text(bx + bw + 10, yc, text=pct, anchor="w", fill=C["dim"], font=self.F(9))
            lab, bg, fg = STATUS.get(stt, STATUS[None])
            if stt == "run":
                lab = SPIN[self.st["spin"]] + " " + lab
            self._badge(cv, cols[2][1], yc, lab, bg, fg)
            t = fmt_secs((self.st["t1"].get(key) or now) - self.st["t0"][key]) if key in self.st["t0"] else "—"
            cv.create_text(cols[3][1], yc, text=t, anchor="w", fill=C["dim"], font=self.F(9))
            det = self.st["detail"].get(key) or ""
            cv.create_text(cols[4][1], yc, text=self.fit(det, self.F(9), W - cols[4][1] - 12, end=True), anchor="w",
                           fill=C["err_fg"] if stt == "err" else C["dim"], font=self.F(9))
            if i < n - 1:
                cv.create_line(8, y0 + rh, W - 8, y0 + rh, fill=C["line"])

    def apply_stage(self, key, stt, detail, frac):
        now = time.time()
        if stt == "run" and key not in self.st["t0"]:
            self.st["t0"][key] = now
        if stt in ("ok", "err", "skip"):
            self.st["t1"][key] = now
            self.st["t0"].setdefault(key, now)
        self.st["status"][key] = stt
        if frac is not None:
            self.st["frac"][key] = frac
        if detail is not None:
            self.st["detail"][key] = detail
        if stt == "run" and now - getattr(self, "_last_draw", 0) < 0.25:
            return                     # no máximo 4 redesenhos por segundo
        self._last_draw = now
        if self.page == "novo" or stt != "run":
            self.draw_table()
            self.draw_allbar()
        self._update_total()
        if self.current:
            self.current["frac"] = self.overall()
            if stt == "run":
                self.current["detail"] = dict(core.STAGES)[key] + (f" — {detail}" if detail else "")
            if self.page == "fila":
                self.draw_queue()
        if stt == "run":
            self.set_status(dict(core.STAGES)[key] + "…", C["sel"])

    def apply_info(self, d):
        if "delay" in d:
            cuts = d["cuts"]
            self._stat("sync", f"{d['delay']:+.2f} s".replace(".", ","),
                       f"{compact_speed(d['speed_text'])} · " +
                       ("sem cortes" if cuts == 0 else f"{cuts} corte" + ("s" if cuts > 1 else "")))
        if "size" in d:
            self._stat("out", fmt_size(d["size"]), f"MKV · dublagem em {d['codec']}")
        if "magnet" in d:
            val, sub, _ = self.stats["out"]
            sub.configure(text=sub.cget("text") + " · .torrent pronto")

    def _update_total(self):
        if not self.st["start"]:
            return
        f = self.overall()
        el = time.time() - self.st["start"]
        txt = f"{f * 100:.0f}%  ·  {fmt_secs(el)} decorridos"
        if self.st["running"] and 0.08 < f < 0.98:
            txt += f"  ·  ~{fmt_secs(el * (1 - f) / f)} restantes"
        self.total_lbl.configure(text=txt)

    def set_status(self, text, color):
        self.sb_left.configure(text=text)
        self.sb_dot.configure(fg=color)

    def _tick(self):
        self.st["spin"] = (self.st["spin"] + 1) % len(SPIN)
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self.st["running"]:
            if self.page == "novo":
                self.draw_table()
            self._update_total()
            if self.page == "fila" and self._ticks % 3 == 0:
                self.draw_queue()
        self.root.after(250, self._tick)

    def _update_buttons(self):
        for b in (self.btn_go, self.btn_queue, self.btn_cancel, self.btn_folder, self.btn_magnet):
            b.pack_forget()
        ready = bool(self.st["dub"] and self.st["orig"])
        if self.current:
            self.btn_cancel.pack(side="right")
            if ready:
                self.btn_queue.set_text("Adicionar à fila")
                self.btn_queue.pack(side="right", padx=(0, 8))
            return
        self.btn_go.pack(side="right")
        self.btn_go.set_enabled(ready)
        if ready:
            self.btn_queue.pack(side="right", padx=(0, 8))
        elif self.st["result"]:
            self.btn_folder.pack(side="right", padx=(0, 8))
            if self.st["result"].get("magnet"):
                self.btn_magnet.pack(side="right", padx=(0, 8))

    def copy_magnet(self, magnet=None):
        m = magnet or (self.st["result"] or {}).get("magnet")
        if not m:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(m)
        self.set_status("Link magnet copiado — cole onde quiser compartilhar", C["ok"])

    # ------------------------------------------------------------------ fila
    def _save_queue(self):
        _save("fila.json", [{k: v for k, v in j.items() if k != "rep"} for j in self.queue])
        n = sum(1 for j in self.queue if j["status"] in ("wait", "run"))
        self.nav["fila"].badge = str(n) if n else ""
        self.nav["fila"].draw()

    def _new_job(self, dub, orig, out, cfg=None):
        return {"id": f"{time.time():.6f}{len(self.queue)}", "dub": dub, "orig": orig, "out": out,
                "cfg": dict(cfg or self.cfg), "status": "wait", "frac": 0.0, "detail": "",
                "elapsed": 0, "added": dt.datetime.now().strftime("%d/%m %H:%M")}

    def _job_from_prep(self):
        dub, orig = self.st["dub"], self.st["orig"]
        if not (dub and orig and os.path.isfile(dub) and os.path.isfile(orig)):
            return None
        if os.path.abspath(dub) == os.path.abspath(orig):
            messagebox.showwarning("DublaSync", "Você escolheu o mesmo arquivo nos dois lados.")
            return None
        nm = self.name_var.get().strip() or os.path.basename(core.default_output(orig))
        if not nm.lower().endswith(".mkv"):
            nm += ".mkv"
        nm = naming.safe(nm[:-4]) + ".mkv"
        out = os.path.join(self.folder["path"] or os.path.dirname(os.path.abspath(orig)), nm)
        if os.path.abspath(out) in (os.path.abspath(dub), os.path.abspath(orig)):
            messagebox.showwarning("DublaSync", "O arquivo novo não pode ter o mesmo nome de um dos arquivos de entrada.")
            return None
        if any(os.path.abspath(j["out"]) == os.path.abspath(out) and j["status"] in ("wait", "run")
               for j in self.queue):
            messagebox.showwarning("DublaSync", "Esse arquivo já está na fila com o mesmo nome de saída.")
            return None
        if os.path.exists(out) and not messagebox.askyesno("DublaSync", f"Já existe:\n{out}\n\nSubstituir?"):
            return None
        if self.cfg["torrent"] and not [t for t in self.cfg["trackers"] if t.strip()]:
            if not messagebox.askyesno("DublaSync", "O .torrent está ligado mas não há nenhum tracker.\n"
                                                    "Gerar mesmo assim (só DHT/magnet)?"):
                return None
        return self._new_job(dub, orig, out)

    def enqueue(self, front=False, start=False):
        job = self._job_from_prep()
        if not job:
            return
        if front:
            pos = next((i for i, j in enumerate(self.queue) if j["status"] == "wait"), len(self.queue))
            self.queue.insert(pos, job)
        else:
            self.queue.append(job)
        self._save_queue()
        waiting = [j for j in self.queue if j["status"] == "wait"]
        self.clear_prep()
        self.set_status(f"Na fila: {os.path.basename(job['out'])}  (posição {waiting.index(job) + 1} "
                        f"de {len(waiting)})", C["sel"])
        if start or self.current:
            self.start_queue()
        self.draw_queue()

    def on_go(self):
        self.enqueue(front=True, start=True)

    def clear_prep(self):
        self.st["dub"] = self.st["orig"] = None
        self.st["oinfo"] = self.st["dinfo"] = None
        self.st["info"].clear()
        self.name_var.set("")
        self.auto_name = ""
        self._stat("orig", "—", "")
        self._stat("dub", "—", "")
        if not self.folder["custom"]:
            self.folder_lbl.configure(text="mesma pasta do original")
        for z in self.zones:
            self.draw_zone(z)
        self._update_buttons()

    def start_queue(self):
        self.queue_paused = False
        self._run_next()
        self.draw_queue()
        self._queue_buttons()

    def pause_queue(self):
        self.queue_paused = True
        self.set_status("A fila vai pausar depois do trabalho atual", C["warn"])
        self._queue_buttons()

    def _run_next(self):
        if self.queue_paused or self.current:
            return
        job = next((j for j in self.queue if j["status"] == "wait"), None)
        if not job:
            self._queue_done()
            return
        missing = [p for p in (job["dub"], job["orig"]) if not os.path.isfile(p)]
        if missing:
            job.update(status="err", detail="arquivo não encontrado: " + os.path.basename(missing[0]))
            self._save_queue()
            self.root.after(100, self._run_next)
            return
        self.current = job
        self._ran_any = True
        job.update(status="run", frac=0.0, detail="começando…", started=time.time())
        self._save_queue()
        self.reset_steps()
        self._stat("sync", "—", "")
        self._stat("out", "—", "")
        self.st.update(running=True, start=time.time())
        waiting = [j for j in self.queue if j["status"] == "wait"]
        done = [j for j in self.queue if j["status"] in ("ok", "err", "cancel") and j.get("session")]
        self.steps_title.configure(text=f"Em andamento: {os.path.basename(job['out'])}"
                                        + (f"   ·   {len(done) + 1} de {len(done) + 1 + len(waiting)}"
                                           if waiting or done else ""))
        rep = core.Reporter(on_stage=lambda *a: self._post(lambda: self.apply_stage(*a)),
                            on_log=lambda m: None,
                            on_info=lambda d: self._post(lambda: self.apply_info(d)))
        self.st["rep"] = rep
        cfg = job["cfg"]
        topts = ({"trackers": cfg.get("trackers", []), "private": cfg.get("private", False),
                  "source": cfg.get("source", "")} if cfg.get("torrent") else None)
        self._update_buttons()
        self._queue_buttons()

        def work():
            err, res = None, None
            try:
                res = core.build_output(job["orig"], job["dub"], job["out"], lang=core.LANGS[cfg["lang"]],
                                        title=f"{cfg['lang']} (Dublado)", codec=cfg["codec"],
                                        dub_default=cfg["dub_default"], remove_old=cfg["remove_old"],
                                        rep=rep, torrent_opts=topts, fast=cfg.get("fast", True))
            except core.SyncError as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            self._post(lambda: self._job_done(job, res, err))

        threading.Thread(target=work, daemon=True).start()

    def on_cancel(self):
        if not self.current:
            return
        waiting = sum(1 for j in self.queue if j["status"] == "wait")
        msg = "Cancelar o trabalho atual? Os arquivos originais não são alterados."
        if waiting:
            msg += f"\n\nA fila ({waiting} na espera) fica pausada; use “Iniciar fila” para continuar."
        if messagebox.askyesno("DublaSync", msg):
            self.queue_paused = True
            self.st["rep"].cancel()

    def _job_done(self, job, res, err):
        self.current = None
        self.st["running"] = False
        for key in list(self.st["status"]):
            if self.st["status"][key] == "run":
                self.apply_stage(key, "err", "cancelado" if err == "Cancelado." else "interrompido", None)
        job["elapsed"] = time.time() - job.get("started", time.time())
        job["session"] = True
        entry = {"when": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "name": os.path.basename(job["out"]),
                 "out": job["out"], "dub": os.path.basename(job["dub"]), "elapsed": job["elapsed"]}
        if err:
            st = "cancel" if err == "Cancelado." else "err"
            job.update(status=st, detail=err.split("\n")[0][:200], error=err[-1500:])
            entry.update(status=st, detail=job["detail"])
            self.set_status(f"{'Cancelado' if st == 'cancel' else 'Erro'}: {os.path.basename(job['out'])}",
                            C["warn"] if st == "cancel" else C["err"])
        else:
            self.st["result"] = res
            job.update(status="ok", frac=1.0, out=res["out"], torrent=res.get("torrent"), magnet=res.get("magnet"),
                       detail=f"{compact_speed(res['speed_text'])} · {res['delay']:+.2f} s".replace(".", ","))
            entry.update(status="ok", size=os.path.getsize(res["out"]) if os.path.exists(res["out"]) else 0,
                         sync=job["detail"], cuts=res["cuts"], torrent=res.get("torrent"), magnet=res.get("magnet"))
            self.total_lbl.configure(text=f"concluído em {fmt_secs(res['elapsed'])}")
            self.set_status(f"Pronto — {os.path.basename(res['out'])}", C["ok"])
            self.draw_allbar()
        self._add_history(entry)
        # se o trabalho retirou arquivos que ainda estavam sendo preparados, limpa a preparação
        for k in ("dub", "orig"):
            if self.st[k] and not os.path.exists(self.st[k]):
                self.clear_prep()
                break
        self._save_queue()
        self._update_buttons()
        self._queue_buttons()
        self.draw_queue()
        if self.queue_paused:
            if any(j["status"] == "wait" for j in self.queue):
                self.set_status("Fila pausada", C["warn"])
        else:
            self.root.after(800, self._run_next)

    def _queue_done(self):
        self._queue_buttons()
        if not getattr(self, "_ran_any", False):
            return
        self._ran_any = False
        sess = [j for j in self.queue if j.get("session")]
        ok = sum(1 for j in sess if j["status"] == "ok")
        bad = sum(1 for j in sess if j["status"] in ("err", "cancel"))
        self.steps_title.configure(text="Etapas")
        self.set_status(f"Fila concluída — {ok} pronto(s)" + (f", {bad} com erro" if bad else ""),
                        C["ok"] if not bad else C["warn"])
        try:
            self.root.bell()
            self.root.title("DublaSync — fila concluída")
            self.root.after(15000, lambda: self.root.title("DublaSync"))
        except tk.TclError:
            pass
        for j in sess:
            j.pop("session", None)
        self._save_queue()
        if self.cfg.get("shutdown"):
            self.cfg["shutdown"] = False
            _save("config.json", self.cfg)
            self._schedule_shutdown()

    def _schedule_shutdown(self):
        if os.name != "nt":
            self.set_status("Desligar ao terminar só funciona no Windows", C["warn"])
            return
        subprocess.Popen(["shutdown", "/s", "/t", "120", "/c", "DublaSync: fila concluída"],
                         creationflags=0x08000000)
        if messagebox.askyesno("DublaSync", "Fila concluída. O computador vai desligar em 2 minutos.\n\n"
                                            "Cancelar o desligamento?"):
            subprocess.Popen(["shutdown", "/a"], creationflags=0x08000000)
            self.set_status("Desligamento cancelado", C["warn"])

    # ------------------------------------------------------------ página: fila
    def _page_fila(self, parent):
        pg = tk.Frame(parent, bg=C["bg"], padx=22 if self.compact else 26, pady=12 if self.compact else 22)
        actions = self.header(pg, "Fila", "Coloque vários filmes e deixe processando — um de cada vez")
        self.q_actions = actions
        self.btn_qstart = self.button(actions, "Iniciar fila", "play", "primary", self.start_queue)
        self.btn_qpause = self.button(actions, "Pausar depois do atual", "pause", "ghost", self.pause_queue)
        self.btn_qmany = self.button(actions, "Adicionar vários", "add", "ghost", self.add_many)

        tb = tk.Frame(pg, bg=C["bg"])
        tb.pack(fill="x", pady=(0, 10))
        for text, icon, cmd in (("Subir", "up", lambda: self.move_job(-1)), ("Descer", "down", lambda: self.move_job(1)),
                                ("Remover", "trash", self.remove_job), ("Limpar concluídos", None, self.clear_done)):
            self.button(tb, text, icon, "ghost", cmd).pack(side="left", padx=(0, 8))
        self.cfg["shutdown"] = False
        tg = self.card(tb, pad=(12, 6))
        tg.pack(side="right")
        tg.inner.configure(bg=C["card"])
        self.toggle(tg.inner, "shutdown", "Desligar o PC ao terminar a fila").pack()

        box = self.card(pg, pad=(6, 6), fill_height=True)
        box.pack(fill="both", expand=True)
        top = tk.Frame(box.inner, bg=C["card"])
        top.pack(fill="x", padx=10, pady=(6, 4))
        self.q_summary = tk.Label(top, text="", bg=C["card"], fg=C["dim"], font=self.F(9), anchor="w")
        self.q_summary.pack(side="left")
        self.q_cv = tk.Canvas(box.inner, bg=C["card"], highlightthickness=0)
        self.q_cv.pack(fill="both", expand=True)
        self.q_cv.bind("<Configure>", lambda e: self.draw_queue())
        self.q_cv.bind("<Button-1>", self._q_click)
        self.q_cv.bind("<Double-Button-1>", self._q_double)
        self.q_cv.bind("<MouseWheel>", lambda e: self._q_scroll(-1 if e.delta > 0 else 1))
        self.q_cv.bind("<Button-4>", lambda e: self._q_scroll(-1))
        self.q_cv.bind("<Button-5>", lambda e: self._q_scroll(1))
        self.q_off = 0
        if self.DND:
            self.q_cv.drop_target_register(self.DND)
            self.q_cv.dnd_bind("<<Drop>>", lambda e: (self.add_many(
                [p for p in self.root.tk.splitlist(e.data) if os.path.isfile(p)]), e.action)[1])
        return pg

    def _queue_buttons(self):
        if not hasattr(self, "btn_qstart"):
            return
        for b in (self.btn_qstart, self.btn_qpause, self.btn_qmany):
            b.pack_forget()
        waiting = any(j["status"] == "wait" for j in self.queue)
        if self.current and not self.queue_paused:
            self.btn_qpause.pack(side="right")
        else:
            self.btn_qstart.set_text("Continuar fila" if self.current or (waiting and any(
                j["status"] != "wait" for j in self.queue)) else "Iniciar fila")
            self.btn_qstart.pack(side="right")
            self.btn_qstart.set_enabled(waiting)
        self.btn_qmany.pack(side="right", padx=(0, 8))

    def _avg_job_time(self):
        times = [j["elapsed"] for j in self.queue if j["status"] == "ok" and j.get("elapsed")]
        times += [h["elapsed"] for h in self.history[:20] if h.get("status") == "ok" and h.get("elapsed")]
        return sum(times) / len(times) if times else None

    def draw_queue(self):
        if not hasattr(self, "q_cv"):
            return
        cv = self.q_cv
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 100:
            return
        n_wait = sum(1 for j in self.queue if j["status"] == "wait")
        n_ok = sum(1 for j in self.queue if j["status"] == "ok")
        n_bad = sum(1 for j in self.queue if j["status"] in ("err", "cancel"))
        avg = self._avg_job_time()
        parts = [f"{n_wait} esperando"]
        if self.current:
            parts.append("1 processando")
        parts.append(f"{n_ok} pronto(s)")
        if n_bad:
            parts.append(f"{n_bad} com erro/cancelado(s)")
        if avg and (n_wait or self.current):
            rest = n_wait * avg + (avg * (1 - self.current.get("frac", 0)) if self.current else 0)
            parts.append(f"~{fmt_secs(rest)} restantes")
        if self.queue_paused and n_wait:
            parts.append("pausada")
        self.q_summary.configure(text="  ·  ".join(parts))

        x_stat, x_prog, x_det, x_time = W - 520, W - 412, W - 262, W - 62
        for name, x in (("#", 12), ("Filme", 40), ("Status", x_stat), ("Progresso", x_prog), ("Detalhes", x_det),
                        ("Tempo", x_time)):
            cv.create_text(x, 12, text=name, anchor="w", fill=C["dim"], font=self.F(9, "bold"))
        cv.create_line(0, 26, W, 26, fill=C["line"])
        if not self.queue:
            cv.create_text(W / 2, H / 2 - 10, text=self.ICON["drop"], fill=C["dim"], font=self.IF(18), anchor="s")
            cv.create_text(W / 2, H / 2, text="A fila está vazia.\nNo Novo trabalho, use “Adicionar à fila” — ou arraste "
                                               "aqui vários arquivos\n(dublados e originais misturados) que eu formo os pares.",
                           fill=C["dim"], font=self.F(10), justify="center", anchor="n")
            return
        labels = {"wait": ("Na fila", C["badge"], C["text"]), "run": ("Processando", C["sel"], C["sel_text"]),
                  "ok": ("Concluído", C["ok_bg"], C["ok_fg"]), "err": ("Erro", C["err_bg"], C["err_fg"]),
                  "cancel": ("Cancelado", C["badge"], C["dim"])}
        rh = 46
        now = time.time()
        pos = 0
        for k, j in enumerate(self.queue):
            if j["status"] == "wait":
                pos += 1
            if k < self.q_off:
                continue
            y0 = 28 + (k - self.q_off) * rh
            if y0 > H:
                break
            yc = y0 + rh / 2
            if j["id"] == self.q_sel:
                rrect(cv, 2, y0 + 2, W - 2, y0 + rh - 2, 8, fill=C["hover"], outline=C["line2"])
            elif j["status"] == "run":
                rrect(cv, 2, y0 + 2, W - 2, y0 + rh - 2, 8, fill=C["card2"], outline=C["card2"])
            cv.create_text(12, yc, text=f"{k + 1:02d}", anchor="w", fill=C["dim2"], font=self.F(9))
            cv.create_text(40, yc - 8, text=self.fit(os.path.basename(j["out"]), self.F(10, "bold"), x_stat - 60),
                           anchor="w", fill=C["text"], font=self.F(10, "bold"))
            cv.create_text(40, yc + 9, text=self.fit(f"dublagem: {os.path.basename(j['dub'])}  ·  adicionado {j.get('added', '')}",
                                                     self.F(8), x_stat - 60), anchor="w", fill=C["dim2"], font=self.F(8))
            lab, bg, fg = labels.get(j["status"], labels["err"])
            if j["status"] == "run":
                lab = SPIN[self.st["spin"]] + " " + lab
            self._badge(cv, x_stat, yc, lab, bg, fg)
            frac = 1.0 if j["status"] == "ok" else j.get("frac", 0.0)
            self._pill(cv, x_prog, yc - 3, x_prog + 100, yc + 3, C["track"])
            if frac > 0:
                col = C["ok"] if j["status"] == "ok" else C["err"] if j["status"] == "err" else C["bar"]
                self._pill(cv, x_prog, yc - 3, x_prog + max(6, 100 * frac), yc + 3, col)
            cv.create_text(x_prog + 108, yc, text=f"{frac * 100:.0f}%", anchor="w", fill=C["dim"], font=self.F(9))
            det = j.get("detail") or (f"{pos}º na espera" if j["status"] == "wait" else "")
            cv.create_text(x_det, yc, text=self.fit(det, self.F(9), x_time - x_det - 12, end=True), anchor="w",
                           fill=C["err_fg"] if j["status"] == "err" else C["dim"], font=self.F(9))
            if j["status"] == "run":
                t = fmt_secs(now - j.get("started", now))
            elif j["status"] in ("ok", "err", "cancel"):
                t = fmt_secs(j.get("elapsed", 0))
            else:
                t = f"~{fmt_secs(avg)}" if avg else "—"
            cv.create_text(x_time, yc, text=t, anchor="w", fill=C["dim"], font=self.F(9))
            cv.create_line(8, y0 + rh, W - 8, y0 + rh, fill=C["line"])

    def _q_index(self, y):
        i = int((y - 28) // 46) + self.q_off
        return i if y > 28 and 0 <= i < len(self.queue) else None

    def _q_click(self, e):
        i = self._q_index(e.y)
        self.q_sel = self.queue[i]["id"] if i is not None else None
        self.draw_queue()

    def _q_double(self, e):
        i = self._q_index(e.y)
        if i is None:
            return
        j = self.queue[i]
        if j["status"] == "ok":
            open_folder(j["out"])
        elif j["status"] in ("err", "cancel"):
            messagebox.showinfo("DublaSync", j.get("error") or j.get("detail") or "Cancelado.")

    def _q_scroll(self, d):
        self.q_off = max(0, min(max(0, len(self.queue) - 1), self.q_off + d))
        self.draw_queue()

    def _sel_index(self):
        return next((i for i, j in enumerate(self.queue) if j["id"] == self.q_sel), None)

    def move_job(self, d):
        i = self._sel_index()
        if i is None:
            return
        k = i + d
        if not (0 <= k < len(self.queue)) or "run" in (self.queue[i]["status"], self.queue[k]["status"]):
            return
        self.queue[i], self.queue[k] = self.queue[k], self.queue[i]
        self._save_queue()
        self.draw_queue()

    def remove_job(self):
        i = self._sel_index()
        if i is None:
            return
        if self.queue[i]["status"] == "run":
            messagebox.showinfo("DublaSync", "Esse está processando agora. Use “Cancelar atual” no Novo trabalho.")
            return
        self.queue.pop(i)
        self.q_sel = None
        self._save_queue()
        self._queue_buttons()
        self.draw_queue()

    def clear_done(self):
        self.queue = [j for j in self.queue if j["status"] in ("wait", "run")]
        self.q_off = 0
        self._save_queue()
        self._queue_buttons()
        self.draw_queue()

    def add_many(self, files=None):
        """Vários arquivos de uma vez: forma os pares (dublado → original) e põe todos na fila."""
        import pairing
        if files is None:
            files = list(filedialog.askopenfilenames(filetypes=FILETYPES))
        files = [f for f in files if os.path.isfile(f)]
        if len(files) < 2:
            if files:
                messagebox.showinfo("DublaSync", "Preciso de pelo menos dois arquivos (o dublado e o original).")
            return
        self.set_status(f"Lendo {len(files)} arquivos para formar os pares…", C["sel"])
        cfg = dict(self.cfg)
        folder = self.folder["path"] if self.folder["custom"] else None

        def work():
            items, infos = [], {}
            for f in files:
                try:
                    info = core.probe(f)
                    h = core.video_height(info)
                    try:
                        d = core.duration_of(info)
                    except core.SyncError:
                        d = 0
                except core.SyncError:
                    info, h, d = None, 0, 0
                infos[f] = info
                if info is not None:
                    items.append(pairing.describe(f, info, h, d))
            pairs, rest = pairing.pair(items)
            rest += [f for f in files if infos.get(f) is None]
            jobs = []
            for dub, orig, _ in pairs:
                out = core.default_output(orig, infos[orig], core.LANGS[cfg["lang"]], cfg["style"])
                if folder:
                    out = os.path.join(folder, os.path.basename(out))
                jobs.append((dub, orig, out))
            self._post(lambda: self._confirm_many(jobs, rest))

        threading.Thread(target=work, daemon=True).start()

    def _confirm_many(self, jobs, rest):
        if not jobs:
            self.set_status("Não consegui formar nenhum par", C["warn"])
            messagebox.showwarning("DublaSync", "Não consegui formar nenhum par com esses arquivos.\n"
                                                "Adicione um de cada vez pelo Novo trabalho.")
            return
        lines = [f"• {os.path.basename(d)}\n      → {os.path.basename(o)}" for d, o, _ in jobs[:12]]
        if len(jobs) > 12:
            lines.append(f"… e mais {len(jobs) - 12}")
        msg = f"Formei {len(jobs)} par(es) (dublado → original):\n\n" + "\n".join(lines)
        if rest:
            msg += "\n\nSem par (ficaram de fora):\n" + "\n".join("• " + os.path.basename(r) for r in rest[:6])
        msg += "\n\nColocar na fila?"
        if not messagebox.askyesno("DublaSync — adicionar vários", msg):
            self.set_status("Nada foi adicionado", C["dim"])
            return
        added = 0
        for dub, orig, out in jobs:
            if any(os.path.abspath(j["out"]) == os.path.abspath(out) and j["status"] in ("wait", "run")
                   for j in self.queue):
                continue
            self.queue.append(self._new_job(dub, orig, out))
            added += 1
        self._save_queue()
        self._queue_buttons()
        self.draw_queue()
        self.set_status(f"{added} filme(s) adicionados à fila", C["ok"])
        if self.current:
            return
        if messagebox.askyesno("DublaSync", f"{added} filme(s) na fila. Começar agora?"):
            self.start_queue()

    def on_close(self):
        if self.current:
            if not messagebox.askyesno("DublaSync", "Um filme está sendo processado. Fechar cancela ele "
                                                    "(volta para a fila e recomeça na próxima vez).\n\nFechar?"):
                return
            self.current["status"] = "wait"
            self.current["frac"] = 0.0
            self.current["detail"] = ""
            self._save_queue()
            self._closing = True
            self.st["rep"].cancel()
            self.root.destroy()
            time.sleep(0.5)          # dá tempo do FFmpeg ser encerrado
            os._exit(0)              # não espera threads de trabalho (evita processo preso)
        self._closing = True
        self.root.destroy()

    # ------------------------------------------------------- página: histórico
    def _add_history(self, entry):
        self.history.insert(0, entry)
        self.history = self.history[:200]
        _save("historico.json", self.history)

    def _page_hist(self, parent):
        pg = tk.Frame(parent, bg=C["bg"], padx=26, pady=22)
        actions = self.header(pg, "Histórico", "Arquivos gerados — dois cliques abrem a pasta")
        self.button(actions, "Limpar histórico", "trash", "ghost", self.clear_hist).pack(side="right")
        box = self.card(pg, pad=(6, 6), fill_height=True)
        box.pack(fill="both", expand=True)
        self.hist_cv = tk.Canvas(box.inner, bg=C["card"], highlightthickness=0)
        self.hist_cv.pack(fill="both", expand=True)
        self.hist_cv.bind("<Configure>", lambda e: self.draw_hist())
        self.hist_cv.bind("<Double-Button-1>", self._hist_click)
        self.hist_cv.bind("<MouseWheel>", lambda e: self._hist_scroll(-1 if e.delta > 0 else 1))
        self.hist_cv.bind("<Button-4>", lambda e: self._hist_scroll(-1))
        self.hist_cv.bind("<Button-5>", lambda e: self._hist_scroll(1))
        self.hist_off = 0
        return pg

    def _hist_scroll(self, d):
        self.hist_off = max(0, min(len(self.history) - 1, self.hist_off + d * 3))
        self.draw_hist()

    def clear_hist(self):
        if self.history and messagebox.askyesno("DublaSync", "Limpar o histórico? (os arquivos não são apagados)"):
            self.history = []
            _save("historico.json", self.history)
            self.draw_hist()

    def _hist_click(self, e):
        i = int((e.y - 34) // 40) + self.hist_off
        if e.y > 34 and 0 <= i < len(self.history):
            open_folder(self.history[i].get("out", ""))

    def draw_hist(self):
        cv = self.hist_cv
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50:
            return
        cols = [("Nome", 12), ("Tamanho", W - 600), ("Status", W - 505), ("Sincronia", W - 400), ("Tempo", W - 200),
                ("Data", W - 135)]
        for name, x in cols:
            cv.create_text(x, 16, text=name, anchor="w", fill=C["dim"], font=self.F(9, "bold"))
        cv.create_line(0, 34, W, 34, fill=C["line"])
        if not self.history:
            cv.create_text(W / 2, H / 2, text="Nenhum trabalho ainda.\nOs arquivos que você gerar aparecem aqui.",
                           fill=C["dim"], font=self.F(10), justify="center")
            return
        labels = {"ok": ("Concluído", C["ok_bg"], C["ok_fg"]), "err": ("Erro", C["err_bg"], C["err_fg"]),
                  "cancel": ("Cancelado", C["badge"], C["dim"])}
        rh = 40
        for j, h in enumerate(self.history[self.hist_off:]):
            y0 = 34 + j * rh
            if y0 > H:
                break
            yc = y0 + rh / 2
            cv.create_text(12, yc - 7, text=self.fit(h.get("name", ""), self.F(10), cols[1][1] - 40), anchor="w",
                           fill=C["text"], font=self.F(10))
            extra = " · com .torrent" if h.get("torrent") else ""
            cv.create_text(12, yc + 9, text=self.fit("dublagem de: " + h.get("dub", "") + extra, self.F(8),
                                                     cols[1][1] - 40), anchor="w", fill=C["dim2"], font=self.F(8))
            cv.create_text(cols[1][1], yc, text=fmt_size(h["size"]) if h.get("size") else "—", anchor="w",
                           fill=C["text"], font=self.F(9))
            lab, bg, fg = labels.get(h.get("status"), labels["err"])
            self._badge(cv, cols[2][1], yc, lab, bg, fg)
            cv.create_text(cols[3][1], yc, text=self.fit(h.get("sync") or h.get("detail", ""), self.F(9), 190),
                           anchor="w", fill=C["dim"], font=self.F(9))
            cv.create_text(cols[4][1], yc, text=fmt_secs(h.get("elapsed", 0)), anchor="w", fill=C["dim"], font=self.F(9))
            cv.create_text(cols[5][1], yc, text=h.get("when", ""), anchor="w", fill=C["dim"], font=self.F(9))
            cv.create_line(8, y0 + rh, W - 8, y0 + rh, fill=C["line"])

    # ---------------------------------------------------- página: compartilhar
    def _section(self, parent, title, sub=""):
        box = self.card(parent, pad=(18, 14))
        box.pack(fill="x", pady=(0, 14))
        c = box.inner
        tk.Label(c, text=title, bg=C["card"], fg=C["text"], font=self.F(11, "bold"), anchor="w").pack(fill="x")
        if sub:
            tk.Label(c, text=sub, bg=C["card"], fg=C["dim"], font=self.F(9), anchor="w", justify="left",
                     wraplength=760).pack(fill="x")
        inner = tk.Frame(c, bg=C["card"])
        inner.pack(fill="x", pady=(10, 0))
        return inner

    def _steps(self, parent, items):
        for i, text in enumerate(items, 1):
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x", pady=3)
            num = tk.Canvas(row, width=22, height=22, bg=C["card"], highlightthickness=0)
            num.pack(side="left", anchor="n")
            num.create_oval(1, 1, 21, 21, fill=C["badge"], outline=C["line2"])
            num.create_text(11, 11, text=str(i), fill=C["text"], font=self.F(8, "bold"))
            tk.Label(row, text=text, bg=C["card"], fg=C["text"], font=self.F(10), anchor="w", justify="left",
                     wraplength=740).pack(side="left", padx=(10, 0), fill="x")

    def _page_share(self, parent):
        sp = ScrollPage(parent)
        pg = sp.inner
        self.header(pg, "Compartilhar", "Como deixar o arquivo novo disponível por torrent")

        last = self._section(pg, "Último .torrent gerado")
        self.share_name = tk.Label(last, text="", bg=C["card"], fg=C["text"], font=self.F(10, "bold"), anchor="w")
        self.share_name.pack(fill="x")
        self.share_mag = tk.Label(last, text="", bg=C["card"], fg=C["dim"], font=self.F(9), anchor="w")
        self.share_mag.pack(fill="x", pady=(2, 8))
        brow = tk.Frame(last, bg=C["card"])
        brow.pack(fill="x")
        self.share_btns = [
            self.button(brow, "Copiar link magnet", "link", "primary", lambda: self.copy_magnet(self._last_tor().get("magnet")),
                        outer=C["card"]),
            self.button(brow, "Abrir pasta do .torrent", "folder", "ghost",
                        lambda: open_folder(self._last_tor().get("torrent") or ""), outer=C["card"])]
        for b in self.share_btns:
            b.pack(side="left", padx=(0, 8))

        s1 = self._section(pg, "1 · Semear no seu qBittorrent (pelo qui)",
                           "Semear = o seu servidor fica entregando o arquivo para quem baixar. "
                           "O qBittorrent precisa enxergar o MKV na pasta onde ele está.")
        self._steps(s1, [
            "Gere o arquivo com “Gerar .torrent” ligado (Configurações). O MKV e o .torrent ficam lado a lado.",
            "No qui, clique em “Adicionar Torrent” e escolha o arquivo .torrent.",
            "Em “Caminho de salvamento”, coloque a pasta onde o MKV já está, do jeito que o SERVIDOR enxerga "
            "(ex.: /HD 8TB/Filmes) — não o caminho do Windows (\\\\servidor\\…).",
            "Deixe “Pular verificação de hash” desmarcado. O qBittorrent confere o arquivo, chega a 100% e "
            "passa para “Semeando”.",
            "Se ele começar a BAIXAR do zero, o caminho está errado: pare o torrent, use “Definir local” "
            "para a pasta certa e mande verificar de novo.",
            "Não renomeie nem edite o MKV depois: qualquer mudança quebra o torrent."])

        s2 = self._section(pg, "2 · Divulgar", "Quem tiver o .torrent ou o link magnet consegue baixar de você.")
        self._steps(s2, [
            "Mande o arquivo .torrent (é pequeno) ou o link magnet — botão “Copiar link magnet” acima ou na tela "
            "do trabalho.",
            "Torrents públicos usam os trackers da lista + DHT, então quem baixar acha o seu servidor mesmo "
            "sem conhecer você.",
            "Mantenha o qBittorrent ligado: o arquivo só fica disponível enquanto alguém estiver semeando."])

        s3 = self._section(pg, "3 · Deixar o servidor alcançável",
                           "Sem isso, só pessoas com conexão aberta conseguem se conectar a você.")
        self._steps(s3, [
            "No qBittorrent: Configurações → Conexão → veja a “Porta para conexões de entrada”.",
            "Ative UPnP/NAT-PMP ali mesmo, ou no roteador faça o redirecionamento dessa porta (TCP e UDP) "
            "para o IP do servidor ZimaOS.",
            "Em Configurações → BitTorrent, deixe DHT, PeX e Descoberta local ligados (para torrents públicos).",
            "Se usar VPN no qBittorrent, a porta precisa ser aberta na VPN (port forwarding do provedor)."])

        s4 = self._section(pg, "Trackers privados",
                           "Sites privados exigem um .torrent marcado como privado e com o SEU link de anúncio.")
        self._steps(s4, [
            "Em Configurações → Torrent: ligue “Torrent privado” e troque a lista de trackers pelo announce "
            "URL do seu perfil no site (ele tem a sua passkey). Preencha “Origem” se o site pedir.",
            "Gere o trabalho e envie o .torrent pela página de Upload do site, seguindo as regras dele "
            "(nome, mediainfo, capturas etc.).",
            "Baixe de volta o .torrent que o site gera e adicione ESSE no qui, com o caminho da pasta do MKV, "
            "sem pular a verificação. Ele vai direto para “Semeando”."])

        note = tk.Label(pg, text="Compartilhe só conteúdo que você tem direito de distribuir e siga as regras "
                                 "de cada tracker.", bg=C["bg"], fg=C["dim2"], font=self.F(9), anchor="w")
        note.pack(fill="x", pady=(0, 8))
        return sp

    def _last_tor(self):
        if self.st["result"] and self.st["result"].get("torrent"):
            r = self.st["result"]
            return {"name": os.path.basename(r["out"]), "torrent": r["torrent"], "magnet": r["magnet"]}
        for h in self.history:
            if h.get("torrent"):
                return {"name": h.get("name"), "torrent": h["torrent"], "magnet": h.get("magnet")}
        return {}

    def refresh_share(self):
        t = self._last_tor()
        if t:
            self.share_name.configure(text=os.path.basename(t["torrent"]))
            self.share_mag.configure(text=self.fit(t.get("magnet") or "", self.F(9), 760, end=True))
            for b in self.share_btns:
                b.set_enabled(True)
        else:
            self.share_name.configure(text="Nenhum ainda")
            self.share_mag.configure(text="Ligue “Gerar .torrent” nas Configurações e gere um trabalho.")
            for b in self.share_btns:
                b.set_enabled(False)

    # --------------------------------------------------- página: configurações
    def _page_cfg(self, parent):
        sp = ScrollPage(parent)
        pg = sp.inner
        self.header(pg, "Configurações", "Padrões usados em todo trabalho novo — salvos automaticamente")

        def combo_row(parent, label, values, current, on_change, width=40):
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, bg=C["card"], fg=C["dim"], font=self.F(10), width=18, anchor="w").pack(side="left")
            var = tk.StringVar(value=current)
            box, cb = self.combo(row, var, values, width)
            box.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: on_change(var.get()))

        s1 = self._section(pg, "Dublagem", "Idioma e formato da faixa dublada que entra no arquivo novo")

        def set_lang(v):
            self.cfg["lang"] = v
            self._cfg_changed(rename=True)

        def set_codec(v):
            self.cfg["codec"] = {val: k for k, val in core.QUALITY.items()}[v]
            self._cfg_changed()

        combo_row(s1, "Idioma", list(core.LANGS), self.cfg["lang"], set_lang, 20)
        combo_row(s1, "Formato", list(core.QUALITY.values()), core.QUALITY[self.cfg["codec"]], set_codec)
        tk.Frame(s1, bg=C["card"], height=8).pack()
        self.toggle(s1, "dub_default", "Dublagem como faixa padrão",
                    "O player abre já na dublagem. O áudio original continua sendo a 1ª faixa.").pack(fill="x")

        s15 = self._section(pg, "Velocidade", "Como o programa analisa os arquivos")
        self.toggle(s15, "fast", "Análise rápida por amostragem",
                    "Lê só ~13% do original (amostras espalhadas pelo filme) e, se perceber um corte de cena "
                    "entre duas amostras, lê aquele trecho inteiro. Se as amostras não bastarem, lê o original "
                    "todo automaticamente. Desligue só se um filme sair fora de sincronia.").pack(fill="x")

        s2 = self._section(pg, "Nome do arquivo", "Como o arquivo novo é nomeado (dá para editar antes de gerar)")

        def set_style(v):
            self.cfg["style"] = {val: k for k, val in naming.STYLES.items()}[v]
            self._cfg_changed(rename=True)

        combo_row(s2, "Padrão", list(naming.STYLES.values()), naming.STYLES[self.cfg["style"]], set_style, 48)

        s3 = self._section(pg, "Torrent", "Um .torrent do arquivo novo, salvo ao lado dele (veja a página Compartilhar)")
        self.toggle(s3, "torrent", "Gerar .torrent junto com o vídeo",
                    "Calcula os pedaços do MKV e cria o .torrent com o mesmo nome.").pack(fill="x", pady=(0, 10))
        self.toggle(s3, "private", "Torrent privado",
                    "Para trackers privados: desliga DHT/PeX e usa só o tracker informado. "
                    "Deixe desligado para compartilhar publicamente.").pack(fill="x", pady=(0, 12))
        tk.Label(s3, text="Trackers (um por linha)", bg=C["card"], fg=C["dim"], font=self.F(9),
                 anchor="w").pack(fill="x")
        tbox = RoundBox(s3, outer=C["card"], fill=C["card2"], border=C["line2"], radius=R_SMALL, pad=(10, 8))
        tbox.pack(fill="x", pady=(4, 8))
        self.trk_text = tk.Text(tbox.inner, height=7, bg=C["card2"], fg=C["text"], relief="flat", bd=0,
                                highlightthickness=0, insertbackground=C["text"], font=self.F(9), wrap="none")
        self.trk_text.pack(fill="x")
        self.trk_text.insert("1.0", "\n".join(self.cfg["trackers"]))
        self.trk_text.bind("<FocusIn>", lambda e: tbox.set_border(C["sel"]))
        self.trk_text.bind("<FocusOut>", lambda e: (tbox.set_border(C["line2"]), self._save_trackers()))
        self.trk_text.bind("<KeyRelease>", lambda e: self._save_trackers())
        brow = tk.Frame(s3, bg=C["card"])
        brow.pack(fill="x")
        self.btn_fetch = self.button(brow, "Usar lista pública atualizada", "refresh", "ghost", self._fetch_trackers,
                                     outer=C["card"])
        self.btn_fetch.pack(side="left")
        self.button(brow, "Restaurar padrão", None, "ghost", self._default_trackers, outer=C["card"]).pack(
            side="left", padx=8)
        self.fetch_lbl = tk.Label(brow, text="", bg=C["card"], fg=C["dim"], font=self.F(9))
        self.fetch_lbl.pack(side="left", padx=6)
        row = tk.Frame(s3, bg=C["card"])
        row.pack(fill="x", pady=(12, 0))
        tk.Label(row, text="Origem (source)", bg=C["card"], fg=C["dim"], font=self.F(10), width=18,
                 anchor="w").pack(side="left")
        self.src_var = tk.StringVar(value=self.cfg["source"])
        sbox, se = self.entry(row, self.src_var)
        sbox.pack(side="left", fill="x", expand=True)
        se.bind("<KeyRelease>", lambda e: (self.cfg.update(source=self.src_var.get().strip()),
                                           _save("config.json", self.cfg)))
        tk.Label(s3, text="Opcional — alguns trackers privados pedem uma tag de origem.", bg=C["card"],
                 fg=C["dim2"], font=self.F(8), anchor="w").pack(fill="x", pady=(4, 0))

        s4 = self._section(pg, "Depois de terminar", "O que fazer com os arquivos usados")
        self.toggle(s4, "remove_old", "Retirar os arquivos antigos",
                    "O dublado e o original vão para a Lixeira, só depois que o arquivo novo passa na "
                    "conferência.").pack(fill="x")
        return sp

    def _save_trackers(self):
        lines = [l.strip() for l in self.trk_text.get("1.0", "end").splitlines() if l.strip()]
        self.cfg["trackers"] = lines
        _save("config.json", self.cfg)

    def _set_trackers(self, lst):
        self.trk_text.delete("1.0", "end")
        self.trk_text.insert("1.0", "\n".join(lst))
        self._save_trackers()

    def _default_trackers(self):
        self._set_trackers(tor.DEFAULT_TRACKERS)
        self.fetch_lbl.configure(text="lista padrão restaurada", fg=C["dim"])

    def _fetch_trackers(self):
        self.btn_fetch.set_enabled(False)
        self.fetch_lbl.configure(text="baixando…", fg=C["dim"])

        def work():
            try:
                lst = tor.fetch_best_trackers()
                msg, ok = f"{len(lst)} trackers atualizados", True
            except Exception as e:  # noqa: BLE001
                lst, msg, ok = None, f"não consegui baixar ({type(e).__name__})", False

            def done():
                self.btn_fetch.set_enabled(True)
                if lst:
                    self._set_trackers(lst)
                self.fetch_lbl.configure(text=msg, fg=C["ok_fg"] if ok else C["err_fg"])
            self._post(done)
        threading.Thread(target=work, daemon=True).start()

    def _cfg_changed(self, rename=False):
        _save("config.json", self.cfg)
        self.refresh_summary()
        if rename:
            self.refresh_name(force=True)

    def run(self):
        self.root.mainloop()


def run_gui():
    App().run()
