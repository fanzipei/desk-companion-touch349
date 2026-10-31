# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""A lightweight tray companion with on-demand native controls and popups."""
import argparse
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

import pystray
from PIL import Image, ImageDraw

from .bridge import Bridge
from .core import Repository, MAX_NOTES, MAX_NEWS
from .secrets import ApiKeyStore
from .summarizer import api_endpoint
from .display import display_packet, FONT_SIZES
from .ai import AiClient
from .capture import RegionCapture
from .news_network import PROXY_MODES,validate_proxy
from .news_recommender import news_status_text
from .news_model_config import PROVIDERS as NEWS_MODEL_PROVIDERS, STAGE_PROVIDERS, STAGE_KEYS, uses_cloud, validate_routes as validate_news_model
from .settings_ui import SettingsWindow
from .wechat import WechatNotes
from . import ui
from .windows import Hotkeys, parse_hotkey, voice_typing, place_by_tray, ime_composing

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOG = logging.getLogger(__name__)


def icon_image(connected=False):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((9, 8, 55, 53), 10, fill="#edf3ee", outline="#9caaa4", width=2)
    draw.line((20, 23, 44, 23), fill="#637b70", width=3)
    draw.line((20, 32, 41, 32), fill="#637b70", width=3)
    draw.ellipse((43, 42, 60, 59), fill="#5bb990" if connected else "#9aa39f",
                 outline="#202824", width=2)
    return image


class App:
    def __init__(self, initial_action=None):
        self.repository = Repository(DATA)
        self.api_keys = ApiKeyStore(DATA)
        self.news_api_keys = ApiKeyStore(DATA,"news-local-api-key.dpapi")
        self.ai=AiClient(self.api_keys,DATA)
        self.region_capture=None
        self.events = queue.Queue()
        self.bridge = Bridge(self.repository, self.events)
        self.wechat = WechatNotes(self.repository,self.events,self.bridge.wake)
        self.connected = False
        self.closed = False
        self.last_command = time.time_ns()
        self.pending_settings = None
        self.input_window = None
        self.settings_window = None
        self.note_window = None
        self.error = ""
        self.voice_guard = 0
        self.root = tk.Tk()
        self.root.withdraw()  # Never map a main window, including the first frame.
        self.root.report_callback_exception = lambda kind, value, trace: LOG.error(
            "UI callback failed", exc_info=(kind, value, trace))
        self.root.title("桌面小伴侣 · Touch349")
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        ui.install_theme(self.root, self.px)
        self.hotkeys = Hotkeys(self.repository.snapshot()["config"], self.events)
        self.tray = pystray.Icon("DeskCompanionTouch349", icon_image(), "桌面小伴侣 · Touch349", pystray.Menu(
            pystray.MenuItem("设置…", lambda: self.events.put(("settings", None))),
            pystray.MenuItem("手机微信…", lambda: self.events.put(("wechat_login", None))),
            pystray.MenuItem("退出", lambda: self.events.put(("quit", None))),
        ))
        self.tray.run_detached()
        self.hotkeys.start()
        self.bridge.start()
        self.wechat.start()
        self.update_tray()
        self.root.after(80, self.poll)
        if initial_action:
            self.root.after(200, {'settings':self.show_settings,'wechat_login':self.show_wechat_login}.get(initial_action,self.show_input))

    def px(self, value):
        return max(1, int(value*self.scale))

    def update_tray(self):
        snapshot = self.repository.snapshot()
        state = "已连接" if self.connected else "等待设备"
        pending = len(snapshot["outbox"])
        detail = f"待发送 {pending} 条" if pending else f"便签 {len(snapshot['notes'])}/{snapshot['config']['note_limit']}"
        title = f"桌面小伴侣 · {state} · {detail}"
        if self.error:
            title += " · " + self.error.replace("\n", " ")
        self.tray.title = title[:120]

    def record_error(self, value, *, input_feedback=False):
        self.error = str(value)
        LOG.warning("%s", value)
        self.update_tray()
        if input_feedback and self.input_window and self.input_window.winfo_viewable():
            self.input_hint.configure(text=self.error[:48], fg="#e3a4a1")

    def build_input(self):
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("桌面小伴侣输入")
        window.overrideredirect(True)
        window.attributes("-toolwindow", True)
        window.attributes("-topmost", True)
        window.configure(bg="#383838")
        outer = tk.Frame(window, bg="#1e1e1e")
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        self.input_window = window
        self.text = tk.Text(outer, height=2, wrap="word", undo=True, borderwidth=0,
                            highlightthickness=0, relief="flat", font=("Microsoft YaHei UI", 12),
                            bg="#1e1e1e", fg="#e4e4e7", insertbackground="#d4d4d8",
                            selectbackground="#3c4b63", padx=self.px(15), pady=self.px(12))
        self.text.pack(fill="both", expand=True)
        self.placeholder = tk.Label(self.text, text="记点什么…", bg="#1e1e1e", fg="#9b9ba2",
                                    font=("Microsoft YaHei UI", 12), cursor="xterm")
        self.placeholder.place(x=self.px(15), y=self.px(12))
        self.placeholder.bind("<Button-1>", lambda event: self.text.focus_set())
        self.text.bind("<<Modified>>", self.input_modified)
        footer = tk.Frame(outer, bg="#1e1e1e")
        footer.pack(fill="x", padx=self.px(15), pady=(0, self.px(10)))
        voice = tk.Label(footer, text="语音", font=("Microsoft YaHei UI", 9), cursor="hand2",
                         bg="#1e1e1e", fg="#b8c9e8")
        voice.pack(side="left")
        voice.bind("<Button-1>", lambda event: self.voice())
        self.input_hint = tk.Label(footer, text="Enter 发送 · Esc 收起", font=("Microsoft YaHei UI", 8),
                                   bg="#1e1e1e", fg="#9b9ba2", anchor="e")
        self.input_hint.pack(side="right")
        self.text.bind("<Return>", self.send_input)
        self.text.bind("<Shift-Return>", lambda event: self.insert_newline())
        self.text.bind("<Escape>", lambda event: self.hide_input())
        window.bind("<Escape>", lambda event: self.hide_input())
        window.bind("<FocusOut>", lambda event: self.root.after(200, self.dismiss_unfocused))
        window.protocol("WM_DELETE_WINDOW", self.hide_input)

    def input_modified(self, event=None):
        if self.text.edit_modified():
            if self.text.get("1.0", "end-1c"):
                self.placeholder.place_forget()
            else:
                self.placeholder.place(x=self.px(15), y=self.px(12))
            self.text.edit_modified(False)
            self.input_hint.configure(text="Enter 发送 · Esc 收起", fg="#9b9ba2")

    def show_input(self):
        if self.input_window is None or not self.input_window.winfo_exists():
            self.build_input()
        self.input_hint.configure(text="Enter 发送 · Esc 收起", fg="#9b9ba2")
        place_by_tray(self.input_window, self.tray, self.px(360), self.px(112), self.px(10))
        self.text.focus_force()

    def hide_input(self):
        if self.input_window:
            self.input_window.withdraw()
        return "break"

    def dismiss_unfocused(self):
        if not self.input_window or not self.input_window.winfo_viewable():
            return
        if time.monotonic() < self.voice_guard:
            self.root.after(500, self.dismiss_unfocused)
            return
        try:
            focus = self.root.focus_displayof()
            if focus is None or focus.winfo_toplevel() != self.input_window:
                self.hide_input()
        except tk.TclError:
            self.hide_input()

    def insert_newline(self):
        self.text.insert("insert", "\n")
        return "break"

    def voice(self):
        self.voice_guard = time.monotonic()+5
        self.text.focus_force()
        def start():
            try:
                voice_typing()
            except Exception as exc:
                self.record_error(exc, input_feedback=True)
        self.root.after(160, start)

    def enqueue(self, value):
        self.repository.enqueue_note(value)
        self.error = ""
        self.bridge.wake()
        self.update_tray()

    def show_wechat_login(self):
        if self.wechat.state.get('source')=='calendar':
            self.show_settings('tools');return
        current=getattr(self,'wechat_login_window',None)
        if current is not None and current.window.winfo_exists():
            current.window.lift();return
        from .wechat_ui import WechatLoginWindow
        self.wechat_login_window=WechatLoginWindow(self)

    def use_calendar_wechat(self):
        try:
            self.wechat.use_calendar()
            window=getattr(self,'wechat_login_window',None)
            if window is not None and window.window.winfo_exists():window.window.destroy()
            if getattr(self,'settings_ui',None):self.settings_ui.refresh()
        except Exception as exc:self.record_error(str(exc))

    def show_wechat_rejected(self):
        items=self.wechat.rejected()
        if not items:
            self.record_error('没有未导入的微信文本。');return
        text='\n\n'.join(item['reason']+'\n'+item['text'] for item in items)
        self.show_note({'id':'wechat-rejected','text':text})

    def send_input(self, event=None):
        # Enter while a Chinese IME is composing commits characters, never a note.
        if ime_composing():
            return None
        value = self.text.get("1.0", "end-1c")
        if not value.strip():
            return self.hide_input()
        try:
            self.enqueue(value)
        except Exception as exc:
            self.record_error(exc, input_feedback=True)
            return "break"
        self.text.delete("1.0", "end")
        self.hide_input()
        return "break"

    def show_settings(self, page=None):
        self.hide_input()
        if self.settings_window and self.settings_window.winfo_exists():
            if page:
                self.settings_ui.show_page(page)
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        self.settings_ui = SettingsWindow(self)
        if page:
            self.settings_ui.show_page(page)

    def save_settings(self):
        if self.pending_settings is not None:
            return
        settings = {key: variable.get().strip() if isinstance(variable.get(), str) else variable.get()
                    for key, variable in self.settings.items()}
        try:
            settings["note_limit"] = int(settings["note_limit"])
            settings["news_limit"] = int(settings["news_limit"])
            settings["news_learning_threshold"] = int(settings["news_learning_threshold"])
            settings["news_exploration_percent"] = int(settings["news_exploration_percent"])
            settings["news_model_provider"]={value:key for key,value in NEWS_MODEL_PROVIDERS.items()}[settings["news_model_provider"]]
            for key in STAGE_KEYS:
                if key in settings:settings[key]={label:name for name,label in STAGE_PROVIDERS.items()}.get(settings[key],settings[key])
            for field in ("news_model_timeout","news_ollama_context"):settings[field]=int(settings[field])
            settings["screen_theme"] = {"白底黑字": "light", "黑底白字": "dark"}[settings["screen_theme"]]
            settings["screen_font_size"] = int(settings["screen_font_size"])
            settings["screen_line_spacing"] = int(settings["screen_line_spacing"])
            settings["news_proxy_mode"]={label:mode for mode,label in PROXY_MODES.items()}[settings["news_proxy_mode"]]
            validate_proxy(settings)
            display_packet(settings)
            api_key = settings.pop("llm_api_key")
            news_api_key = settings.pop("news_local_api_key", "")
            validate_news_model(settings,require_model=bool(settings["llm_enabled"] or settings.get("news_personalized")))
            if not 1 <= settings["news_limit"] <= MAX_NEWS:
                raise ValueError(f"新闻队列容量应为 1–{MAX_NEWS}。")
            if (settings["llm_enabled"] or settings.get("news_personalized")) and uses_cloud(settings):
                api_endpoint(settings["llm_base_url"])
                if not settings["llm_model"] or not (api_key or self.api_keys.exists()):
                    raise ValueError("请填写模型名称和 API 密钥。")
            shortcuts=[settings[key] for key in ("capture_hotkey","compose_hotkey")]
            if settings["screenshot_enabled"]:
                shortcuts.append(settings["screenshot_hotkey"])
                client,candidate=self.settings_ai()
                if not client.verified(candidate,"vision"):
                    raise ValueError("请先点击“大模型 → 验证 API 与图片能力”。")
            if len({parse_hotkey(key) for key in shortcuts})!=len(shortcuts):
                raise ValueError("快捷键必须互不相同。")
            if not 1 <= settings["note_limit"] <= MAX_NOTES:
                raise ValueError("便签上限应为 1–20。")
            snapshot = self.repository.snapshot()
            minimum = max(len(snapshot["notes"]), sum(op["op"] == "add" for op in snapshot["outbox"]))
            if settings["note_limit"] < minimum:
                raise ValueError(f"已有 {minimum} 条便签或待发送内容，请先删除后再降低上限。")
            if settings["news_limit"] < len(snapshot["news_pins"]):
                raise ValueError("请先取消部分新闻的置顶，再降低新闻上限。")
            if not settings["gdelt_query"] or len(settings["gdelt_query"]) > 512:
                raise ValueError("GDELT 查询应为 1–512 个字符。")
            if settings["device_address"]:
                import re
                if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", settings["device_address"]):
                    raise ValueError("蓝牙地址格式应为 AA:BB:CC:DD:EE:FF；留空可自动发现设备。")
            self.pending_settings = settings
            self.pending_api_key = api_key
            self.pending_news_api_key = news_api_key
            self.settings_save.configure(state="disabled")
            self.settings_status.set("正在保存…")
            self.hotkeys.reload(settings)
        except Exception as exc:
            self.settings_status.set(str(exc))

    def settings_done(self, error=None):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_save.configure(state="normal")
            self.settings_status.set(str(error or ""))
            if error is None:
                self.settings["llm_api_key"].set("")
                if "news_local_api_key" in self.settings:self.settings["news_local_api_key"].set("")
                self.settings_status.set("设置已保存")

    def settings_ai(self):
        config=self.repository.snapshot()["config"]
        for field in ("llm_base_url","llm_model"):
            config[field]=self.settings[field].get().strip()
        key=self.settings["llm_api_key"].get().strip()
        if key:
            from types import SimpleNamespace
            return AiClient(SimpleNamespace(load=lambda:key),DATA),config
        return self.ai,config

    def background_ai(self,kind,work):
        def run():
            try:
                self.events.put((kind,work()))
            except Exception as exc:
                self.events.put((kind+"_error",str(exc)))
        threading.Thread(target=run,daemon=True).start()

    def verify_api(self):
        try:
            client,config=self.settings_ai()
            self.verify_button.configure(state="disabled")
            self.settings_status.set("正在验证文字接口和合成测试图…")
            self.background_ai("api_verified",lambda:client.verify(config))
        except Exception as exc:
            self.settings_status.set(str(exc))

    def start_screenshot(self):
        config=self.repository.snapshot()["config"]
        if not config["screenshot_enabled"] or not self.ai.verified(config,"vision"):
            self.show_settings("api")
            self.settings_status.set("请先在模型服务页验证图片能力，并启用截图识别。")
            return
        if self.region_capture:
            return
        self.hide_input()
        try:
            self.region_capture=RegionCapture(self.root,
                lambda image:self.background_ai("screenshot_note",lambda:self.ai.screenshot_note(config,image)),
                lambda:setattr(self,"region_capture",None))
        except Exception as exc:
            self.record_error(exc)


    def show_note(self, note):
        self.hide_input()
        if self.note_window and self.note_window.winfo_exists():
            self.note_window.destroy()
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("桌面小伴侣便签")
        window.overrideredirect(True)
        window.attributes("-toolwindow", True)
        window.configure(bg="#1e1e1e")
        self.note_window = window
        header = tk.Frame(window, bg="#1e1e1e")
        header.pack(fill="x", padx=self.px(16), pady=(self.px(12), 0))
        tk.Label(header, text="置顶便签" if note.get("pinned") else "便签", bg="#1e1e1e", fg="#b8c9e8",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        close = tk.Label(header, text="×", bg="#1e1e1e", fg="#b8c9e8", cursor="hand2",
                         font=("Microsoft YaHei UI", 14))
        close.pack(side="right")
        close.bind("<Button-1>", lambda event: window.destroy())
        body = tk.Text(window, wrap="word", borderwidth=0, highlightthickness=0, font=("Microsoft YaHei UI", 12),
                       bg="#1e1e1e", fg="#e4e4e7", selectbackground="#3c4b63", padx=self.px(16), pady=self.px(12))
        body.pack(fill="both", expand=True)
        body.insert("1.0", note["text"])
        body.configure(state="disabled")
        window.bind("<Escape>", lambda event: window.destroy())
        place_by_tray(window, self.tray, self.px(440), self.px(320), self.px(10))
        body.focus_force()
        LOG.info("Note window shown id=%s",note.get("id"))


    def poll(self):
        if getattr(self,"settings_window",None) and self.settings_window.winfo_exists() and time.monotonic() >= getattr(self,"news_status_checked_at",0):
            if getattr(self,"settings_ui",None):
                self.settings_ui.refresh()
            else:
                self.news_refresh_status.set(news_status_text(self.repository.directory))
            self.news_status_checked_at = time.monotonic()+2
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "quit":
                    self.quit()
                    return
                if kind == "compose":
                    self.show_input()
                elif kind == "settings":
                    self.show_settings()
                elif kind=='wechat_login':
                    self.show_wechat_login()
                elif kind in ('wechat_qr','wechat_code_required','wechat_connected'):
                    LOG.info('WeChat login progress: %s',kind)
                    window=getattr(self,'wechat_login_window',None)
                    if window is not None and window.window.winfo_exists():window.update(kind,value)
                elif kind=='wechat_note':
                    self.update_tray()
                    LOG.info('WeChat text queued as a note')
                elif kind=="screenshot":
                    self.start_screenshot()
                elif kind=="screenshot_note":
                    try:
                        self.enqueue(value)
                    except Exception as exc:
                        self.record_error(exc)
                elif kind=="api_verified":
                    if self.settings_window and self.settings_window.winfo_exists():
                        self.verify_button.configure(state="normal")
                        client,config=self.settings_ai()
                        if value["signature"]==client.signature(config):
                            self.settings["screenshot_enabled"].set(True)
                            self.settings_status.set("文字与图片验证通过。保存后启用截图快捷键。")
                        else:
                            self.settings_status.set("配置已变化，请重新验证。")
                elif kind=="api_verified_error":
                    if self.settings_window and self.settings_window.winfo_exists():
                        self.verify_button.configure(state="normal")
                        self.settings_status.set(value)
                elif kind == "capture":
                    try:
                        self.enqueue(value)  # Never opens a window or toast.
                    except Exception as exc:
                        self.record_error(exc)
                elif kind in ("capture_error", "error", "status", "brief_error","screenshot_note_error"):
                    self.record_error(value)
                elif kind == "connection":
                    changed = self.connected != bool(value[0])
                    self.connected = bool(value[0])
                    if self.connected:
                        self.error = ""
                    if changed:
                        self.tray.icon = icon_image(self.connected)
                    self.update_tray()
                elif kind in ("delivered", "changed", "settings_synced", "brief_ready"):
                    self.error = ""
                    self.update_tray()
                elif kind == "open_url":
                    webbrowser.open(value)
                elif kind == "open_note":
                    self.show_note(value)
                elif kind == "news_status":
                    LOG.info("%s", value)
                elif kind == "news_updated":
                    LOG.info("%s", value)
                elif kind in ("news_models","news_model_verified","news_models_error","news_model_verified_error"):
                    if getattr(self,"settings_ui",None) and self.settings_window and self.settings_window.winfo_exists():
                        self.settings_ui.news_model_result(kind,value)
                elif kind == "hotkeys_ready" and self.pending_settings is not None:
                    settings, self.pending_settings = self.pending_settings, None
                    try:
                        if self.pending_api_key:
                            self.api_keys.save(self.pending_api_key)
                            self.bridge.refresh_news()
                        if getattr(self,"pending_news_api_key",""):
                            self.news_api_keys.save(self.pending_news_api_key)
                            self.bridge.refresh_news()
                        self.pending_api_key = ""
                        self.pending_news_api_key = ""
                        self.repository.configure(**settings)
                        self.bridge.settings_changed()
                        LOG.info("Settings saved")
                        self.settings_done()
                        self.update_tray()
                    except Exception as exc:
                        self.hotkeys.reload(self.repository.snapshot()["config"])
                        self.settings_done(exc)
                elif kind == "hotkeys_error":
                    self.pending_settings = None
                    self.pending_api_key = ""
                    self.settings_done(value)
                    self.record_error(value)
        except queue.Empty:
            pass
        request = DATA / "control.request"
        try:
            stamp = request.stat().st_mtime_ns
            if stamp > self.last_command:
                self.last_command = stamp
                action = json.loads(request.read_text(encoding="utf-8")).get("action")
                LOG.info("Tray action: %s", action)
                if action == "settings":
                    self.show_settings()
                elif action == "compose":
                    self.show_input()
                elif action == "wechat_login":
                    self.show_wechat_login()
                elif action == "quit":
                    self.quit()
                    return
        except (OSError, ValueError):
            pass
        if not self.closed:
            self.root.after(80, self.poll)

    def quit(self):
        self.closed = True
        self.hotkeys.stop()
        self.bridge.stop()
        self.wechat.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--settings", action="store_true", help="Open settings explicitly.")
    parser.add_argument("--input", action="store_true", help="Open the tray input explicitly.")
    parser.add_argument("--wechat", action="store_true", help="Connect phone WeChat with a QR code.")
    args = parser.parse_args()
    action = "wechat_login" if args.wechat else "settings" if args.settings else "compose" if args.input else None
    DATA.mkdir(exist_ok=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, "Local\\DeskCompanionTouch349_82F2D7A6")
    if ctypes.get_last_error() == 183:
        if action:
            (DATA / "control.request").write_text(json.dumps({"action": action}), encoding="utf-8")
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        return  # A normal second launch is silent as well.
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "logs/pc-app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        try:
            from .windows import user32
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        App(action).run()
    except Exception:
        LOG.exception("Application failed")  # No surprise error dialog.
    finally:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
