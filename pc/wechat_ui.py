# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Small native pairing window for the Weixin text-note connection."""
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk, Image
from . import ui


class WechatLoginWindow:
    def __init__(self, app):
        self.app=app;px=app.px
        self.window=tk.Toplevel(app.root);self.window.title('连接手机微信')
        self.window.configure(bg=ui.BG);self.window.resizable(False,False)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        body=tk.Frame(self.window,bg=ui.BG,padx=px(24),pady=px(24));body.pack(fill='both',expand=True)
        ui.label(body,'微信扫码 → 新增便签',size=16).pack(anchor='w')
        ui.label(body,'用手机微信扫描下面的二维码，并在手机上确认。\n连接后，向微信 ClawBot 对话发送文字即可。',color=ui.MUTED,size=9,wraplength=px(365)).pack(anchor='w',pady=(px(10),px(18)))
        self.qr=tk.Label(body,text='正在获取二维码…',bg='white',fg='#222222',width=34,height=14)
        self.qr.pack()
        self.status=tk.StringVar(value=app.wechat.status())
        ui.label(body,textvariable=self.status,color=ui.MUTED,size=9,wraplength=px(365)).pack(anchor='w',pady=px(14))
        self.code_frame=tk.Frame(body,bg=ui.BG)
        self.code=tk.StringVar()
        ui.label(self.code_frame,'手机授权页验证码',size=9).pack(anchor='w')
        ttk.Entry(self.code_frame,textvariable=self.code,style='DC.TEntry').pack(side='left',fill='x',expand=True)
        ui.button(self.code_frame,'提交',self.submit_code).pack(side='left',padx=(px(8),0))
        row=tk.Frame(body,bg=ui.BG);row.pack(fill='x',pady=(px(14),0))
        ui.button(row,'刷新二维码',self.refresh).pack(side='left')
        ui.button(row,'关闭',self.close).pack(side='right')
        ui.dark_titlebar(self.window)
        self.window.update_idletasks()
        self.window.geometry(f'+{(self.window.winfo_screenwidth()-self.window.winfo_width())//2}+{max(0,(self.window.winfo_screenheight()-self.window.winfo_height())//2)}')
        self.refresh();self.tick()

    def refresh(self):
        self.app.wechat.cancel_login()
        self.qr.configure(image='',text='正在获取二维码…',width=34,height=14)
        self.code_frame.pack_forget();self.app.wechat.begin_login()

    def update(self,kind,value):
        if kind=='wechat_qr':
            import qrcode
            image=qrcode.make(value).get_image().convert('RGB')
            size=self.app.px(290)
            self.image=ImageTk.PhotoImage(image.resize((size,size),Image.Resampling.NEAREST))
            self.qr.configure(image=self.image,text='',width=size,height=size)
        elif kind=='wechat_code_required':self.code_frame.pack(fill='x')
        elif kind=='wechat_connected':
            self.qr.configure(image='',text='微信已连接 ✓',width=34,height=14)
            self.code_frame.pack_forget()

    def submit_code(self):
        try:self.app.wechat.submit_code(self.code.get());self.code.set('')
        except ValueError as exc:self.status.set(str(exc))

    def tick(self):
        if not self.window.winfo_exists():return
        self.status.set(self.app.wechat.status())
        self.window.after(500,self.tick)

    def close(self):
        self.app.wechat.cancel_login();self.window.destroy()
