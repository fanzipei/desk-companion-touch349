# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Small native Tk components; no web view, extra runtime or theme dependency."""
import ctypes
import tkinter as tk
from tkinter import ttk

BG = "#1e1e1e"
SIDEBAR = "#181818"
SURFACE = "#252526"
INPUT = "#2b2b2b"
BORDER = "#383838"
TEXT = "#e4e4e7"
MUTED = "#9b9bA2"
SUBTLE = "#71717a"
ACCENT = "#b8c9e8"
SELECTED = "#303036"
SUCCESS = "#82b29a"
WARNING = "#ddb784"
FONT = "Microsoft YaHei UI"


def install_theme(root, px):
    style = ttk.Style(root)
    style.theme_use("clam")
    common = dict(font=(FONT, 10), foreground=TEXT)
    style.configure("DC.TFrame", background=BG)
    style.configure("DC.TLabel", **common, background=BG)
    style.configure("DC.TButton", **common, padding=(px(13), px(7)), borderwidth=1,
                    relief="flat", bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    background=SURFACE, focuscolor=ACCENT)
    style.map("DC.TButton", background=[("pressed", "#3b3b40"), ("active", "#343438")],
              foreground=[("disabled", SUBTLE)], bordercolor=[("focus", ACCENT)])
    style.configure("Primary.DC.TButton", background="#dedee3", foreground="#202024", bordercolor="#dedee3")
    style.map("Primary.DC.TButton", background=[("disabled", "#55555c"), ("pressed", "#bcbcc4"), ("active", "#f2f2f5")],
              foreground=[("disabled", "#9999a1"), ("!disabled", "#202024")])
    for kind in ("TEntry", "TCombobox", "TSpinbox"):
        name = "DC."+kind
        style.configure(name, **common, fieldbackground=INPUT, background=INPUT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT, arrowcolor=MUTED,
                        selectbackground="#3c4b63", selectforeground=TEXT, padding=px(7), borderwidth=1)
        style.map(name, fieldbackground=[("disabled", SURFACE), ("readonly", INPUT)],
                  foreground=[("disabled", SUBTLE), ("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT), ("active", "#53535b")],
                  arrowcolor=[("disabled", SUBTLE), ("!disabled", MUTED)])
    style.configure("DC.TCheckbutton", **common, background=BG, padding=(0, px(4)), indicatorbackground=INPUT,
                    indicatorforeground=TEXT, indicatorsize=px(16), focuscolor=ACCENT)
    style.map("DC.TCheckbutton", background=[("active", BG)],
              foreground=[("disabled", SUBTLE), ("active", TEXT)],
              indicatorbackground=[("selected", "#697b98"), ("!selected", INPUT)])
    style.configure("DC.Vertical.TScrollbar", background="#424248", troughcolor=BG,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, arrowcolor=MUTED, arrowsize=px(9), width=px(10))
    style.map("DC.Vertical.TScrollbar", background=[("active", "#62626a")])
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", "#414754")
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))


def dark_titlebar(window):
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), 4)
    except (OSError, AttributeError):
        pass


def label(parent, text="", *, size=10, color=TEXT, bg=BG, bold=False, **kwargs):
    return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                    bg=bg, fg=color, anchor="w", justify="left", **kwargs)


def button(parent, text, command, primary=False, **kwargs):
    return ttk.Button(parent, text=text, command=command,
                      style="Primary.DC.TButton" if primary else "DC.TButton", **kwargs)


def icon(canvas, kind, color=MUTED):
    canvas.delete("all")
    def line(*points): canvas.create_line(*points, fill=color, width=1.4)
    def box(*points): canvas.create_rectangle(*points, outline=color, width=1.2)
    if kind == "overview":
        for x,y in ((3,3),(12,3),(3,12),(12,12)): box(x,y,x+5,y+5)
    elif kind == "screen":
        box(2,4,19,14); line(10,14,10,18); line(6,18,14,18)
    elif kind == "news":
        box(3,3,18,18); line(6,7,15,7); line(6,11,15,11); line(6,15,12,15)
    elif kind == "api":
        line(7,4,2,10,7,16); line(14,4,19,10,14,16); line(12,2,9,18)
    elif kind == "tools":
        line(4,3,17,16); line(4,16,16,4); canvas.create_oval(2,1,8,7,outline=color,width=1.2)
    else:
        for y,x in ((5,8),(11,14),(17,6)):
            line(2,y,19,y); canvas.create_oval(x-2,y-2,x+2,y+2,fill=canvas.cget("bg"),outline=color,width=1.2)


class ScrollPage:
    def __init__(self, parent, px):
        self.frame = tk.Frame(parent, bg=BG)
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview, style="DC.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG, padx=px(30), pady=px(23))
        self.item = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.item, width=event.width))

    def scroll(self, delta):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(delta/120) if abs(delta)>=120 else (-1 if delta>0 else 1), "units")
