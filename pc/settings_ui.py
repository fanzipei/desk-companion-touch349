# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""An on-demand native control window over the existing tray application's state."""
import os
import tkinter as tk
from tkinter import ttk

from .core import MAX_NOTES, MAX_NEWS
from .display import FONT_SIZES
from .news_network import PROXY_MODES
from .news_recommender import news_status_text
from .news_feeds import FEEDS
from . import ui
from .news_model_config import DEFAULTS as NEWS_MODEL_DEFAULTS, PROVIDERS as NEWS_MODEL_PROVIDERS, STAGES as NEWS_MODEL_STAGES, STAGE_PROVIDERS, STAGE_KEYS, is_local, settings_signature, validate as validate_news_model
from .news_model import NewsModelClient
from .secrets import ApiKeyStore

PAGES = (
    ("overview", "概览", "设备、内容和后台状态，一眼可见。"),
    ("general", "常规", "设置日常快捷键、便签容量和连接方式。"),
    ("screen", "小屏显示", "调整阅读外观，保存后同步到小屏。"),
    ("news", "新闻推荐", "先筛选标题，再抓取正文生成中文简讯。"),
    ("tools", "快捷工具", "截图识别与文字输入，随手调用。"),
    ("api", "模型服务", "使用你配置的模型处理新闻、截图。"),
)


class SettingsWindow:
    def __init__(self, app):
        self.app, self.px = app, app.px
        self.config = app.repository.snapshot()["config"]
        self.pages, self.nav, self.fields = {}, {}, {}
        self.selected = "overview"
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.title("桌面小伴侣")
        self.window.configure(bg=ui.BG)
        app.settings_window = self.window
        app.settings = {}
        app.settings_status = tk.StringVar(value="")
        app.news_refresh_status = tk.StringVar(value=news_status_text(app.repository.directory))
        app.news_status_checked_at = 0
        self._build_shell()
        self._overview()
        self._general()
        self._screen()
        self._news()
        self._tools()
        self._api()
        self.show_page("overview")
        self.refresh()
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Control-s>", lambda event: app.save_settings())
        self.window.bind("<MouseWheel>", self._scroll, add="+")
        self.window.update_idletasks()
        width = min(self.px(980), self.window.winfo_screenwidth()-self.px(40))
        height = min(self.px(680), self.window.winfo_screenheight()-self.px(90))
        self.window.minsize(min(self.px(820),width), min(self.px(550),height))
        self.window.geometry(f"{width}x{height}+{(self.window.winfo_screenwidth()-width)//2}+{max(0,(self.window.winfo_screenheight()-height)//2-self.px(20))}")
        ui.dark_titlebar(self.window)
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def _build_shell(self):
        px = self.px
        status = tk.Frame(self.window, bg=ui.SIDEBAR, height=px(29), highlightbackground=ui.BORDER, highlightthickness=1)
        status.pack(side="bottom", fill="x")
        self.status_connection = ui.label(status, bg=ui.SIDEBAR, color=ui.MUTED, size=8)
        self.status_connection.pack(side="left", padx=px(14), pady=px(5))
        ui.label(status,"关闭窗口后，后台继续运行",bg=ui.SIDEBAR,color=ui.SUBTLE,size=8).pack(side="right",padx=px(14))
        sidebar = tk.Frame(self.window, bg=ui.SIDEBAR, width=px(184))
        sidebar.pack(side="left", fill="y");sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar,bg=ui.SIDEBAR)
        brand.pack(fill="x",padx=px(20),pady=(px(25),px(27)))
        ui.label(brand,"桌面小伴侣",size=12,bold=True,bg=ui.SIDEBAR).pack(anchor="w")
        ui.label(brand,"DESK COMPANION",size=8,color=ui.SUBTLE,bg=ui.SIDEBAR).pack(anchor="w",pady=(px(5),0))
        ui.label(sidebar,"工作区",size=8,color=ui.SUBTLE,bg=ui.SIDEBAR).pack(anchor="w",padx=px(20),pady=(0,px(8)))
        for key,title,_ in PAGES:
            row = tk.Frame(sidebar,bg=ui.SIDEBAR,height=px(40),cursor="hand2")
            row.pack(fill="x",padx=px(8),pady=px(2));row.pack_propagate(False)
            indicator = tk.Frame(row,bg=ui.SIDEBAR,width=px(2));indicator.pack(side="left",fill="y")
            glyph = tk.Canvas(row,width=22,height=22,highlightthickness=0,bg=ui.SIDEBAR)
            glyph.pack(side="left",padx=(px(12),px(9)))
            ui.icon(glyph,key)
            text=ui.label(row,title,bg=ui.SIDEBAR,color=ui.MUTED)
            text.pack(side="left",fill="y")
            target=lambda event=None,value=key:self.show_page(value)
            for widget in (row,indicator,glyph,text):widget.bind("<Button-1>",target)
            row.configure(takefocus=True)
            row.bind("<Return>",target);row.bind("<space>",target)
            row.bind("<FocusIn>",lambda event,w=row:w.configure(highlightbackground=ui.ACCENT,highlightthickness=1))
            row.bind("<FocusOut>",lambda event,w=row:w.configure(highlightthickness=0))
            self.nav[key]=(row,indicator,glyph,text)
        foot=tk.Frame(sidebar,bg=ui.SIDEBAR)
        foot.pack(side="bottom",fill="x",padx=px(17),pady=px(18))
        ui.button(foot,"＋  新建便签",app_command(self.app,"show_input")).pack(fill="x")
        self.compose_shortcut=ui.label(foot,self.config["compose_hotkey"],bg=ui.SIDEBAR,color=ui.SUBTLE,size=8)
        self.compose_shortcut.pack(pady=(px(8),0))
        tk.Frame(self.window,bg=ui.BORDER,width=1).pack(side="left",fill="y")
        workspace=tk.Frame(self.window,bg=ui.BG)
        workspace.pack(side="left",fill="both",expand=True)
        top=tk.Frame(workspace,bg=ui.BG,height=px(44))
        top.pack(fill="x");top.pack_propagate(False)
        self.breadcrumb=ui.label(top,"工作区  /  概览",color=ui.MUTED,size=9)
        self.breadcrumb.pack(side="left",padx=px(30))
        self.connection=ui.label(top,color=ui.SUCCESS,size=9)
        self.connection.pack(side="right",padx=px(26))
        tk.Frame(workspace,bg=ui.BORDER,height=1).pack(fill="x")
        actions=tk.Frame(workspace,bg=ui.BG)
        actions.pack(side="bottom",fill="x")
        tk.Frame(actions,bg=ui.BORDER,height=1).pack(fill="x")
        row=tk.Frame(actions,bg=ui.BG,padx=px(26),pady=px(15));row.pack(fill="x")
        ui.label(row,textvariable=self.app.settings_status,color=ui.WARNING,size=9,wraplength=px(340)).pack(side="left",fill="x",expand=True)
        ui.button(row,"关闭",self.window.destroy).pack(side="right",padx=(px(8),0))
        self.app.settings_save=ui.button(row,"保存更改",self.app.save_settings,primary=True)
        self.app.settings_save.pack(side="right")
        self.host=tk.Frame(workspace,bg=ui.BG);self.host.pack(fill="both",expand=True)
        for key,title,description in PAGES:
            page=ui.ScrollPage(self.host,px)
            ui.label(page.inner,title,size=21).pack(anchor="w")
            ui.label(page.inner,description,color=ui.MUTED,wraplength=px(560)).pack(anchor="w",pady=(px(7),px(25)))
            self.pages[key]=page

    def show_page(self, key):
        if key not in self.pages:return
        for page in self.pages.values():page.frame.pack_forget()
        self.pages[key].frame.pack(fill="both",expand=True)
        self.selected=key
        title=next(title for value,title,_ in PAGES if value==key)
        self.breadcrumb.configure(text=f"工作区  /  {title}")
        for value,(row,indicator,glyph,text) in self.nav.items():
            active=value==key;bg=ui.SELECTED if active else ui.SIDEBAR
            row.configure(bg=bg);indicator.configure(bg=ui.ACCENT if active else bg)
            glyph.configure(bg=bg);ui.icon(glyph,value,ui.TEXT if active else ui.MUTED)
            text.configure(bg=bg,fg=ui.TEXT if active else ui.MUTED)

    def _scroll(self,event):
        if isinstance(event.widget,(tk.Text,ttk.Combobox,ttk.Spinbox)):return
        self.pages[self.selected].scroll(event.delta)

    def section(self,key,title,description=None):
        parent=self.pages[key].inner
        ui.label(parent,title,size=11,bold=True).pack(anchor="w",pady=(self.px(5),self.px(10)))
        if description:
            ui.label(parent,description,color=ui.MUTED,size=9,wraplength=self.px(565)).pack(anchor="w",pady=(0,self.px(15)))
        result=tk.Frame(parent,bg=ui.BG)
        result.pack(fill="x",pady=(0,self.px(22)))
        return result

    def field(self,parent,key,title,hint="",values=None,maximum=None,secret=False):
        row=tk.Frame(parent,bg=ui.BG);row.pack(fill="x",pady=self.px(8))
        row.columnconfigure(0,weight=1)
        copy=tk.Frame(row,bg=ui.BG);copy.grid(row=0,column=0,sticky="nw",padx=(0,self.px(16)))
        ui.label(copy,title).pack(anchor="w")
        if hint:ui.label(copy,hint,color=ui.MUTED,size=8,wraplength=self.px(235)).pack(anchor="w",pady=(self.px(4),0))
        value=self.config.get(key,"")
        if key=="screen_theme":value="黑底白字" if value=="dark" else "白底黑字"
        if key=="news_proxy_mode":value=PROXY_MODES.get(value,PROXY_MODES["auto"])
        if key=="news_model_provider":value=NEWS_MODEL_PROVIDERS.get(value,NEWS_MODEL_PROVIDERS["configured"])
        if key in STAGE_KEYS:value=STAGE_PROVIDERS.get(value,STAGE_PROVIDERS['default'])
        variable=tk.StringVar(value=str(value));self.app.settings[key]=variable
        if values is not None:
            entry=ttk.Combobox(row,textvariable=variable,values=values,state="readonly",width=27,style="DC.TCombobox")
        elif maximum is not None:
            entry=ttk.Spinbox(row,textvariable=variable,from_=0 if key=="screen_line_spacing" else 1,to=maximum,width=27,style="DC.TSpinbox")
        else:
            entry=ttk.Entry(row,textvariable=variable,width=29,show="●" if secret else "",style="DC.TEntry")
        entry.grid(row=0,column=1,sticky="ne")
        self.fields[key]=entry
        return entry

    def check(self,parent,key,title,hint=""):
        variable=tk.BooleanVar(value=self.config.get(key,False));self.app.settings[key]=variable
        ttk.Checkbutton(parent,text=title,variable=variable,style="DC.TCheckbutton").pack(anchor="w",pady=(0,self.px(6)))
        if hint:ui.label(parent,hint,color=ui.MUTED,size=9,wraplength=self.px(560)).pack(anchor="w",padx=(self.px(25),0),pady=(0,self.px(13)))

    def _overview(self):
        px=self.px;parent=self.pages["overview"].inner
        cards=tk.Frame(parent,bg=ui.BG);cards.pack(fill="x",pady=(0,px(25)))
        self.metrics=[]
        for i,title in enumerate(("设备连接","便签容量","新闻队列")):
            cards.columnconfigure(i,weight=1,uniform="metric")
            card=tk.Frame(cards,bg=ui.SURFACE,highlightbackground=ui.BORDER,highlightthickness=1,padx=px(17),pady=px(16))
            card.grid(row=0,column=i,sticky="nsew",padx=(0,px(10) if i<2 else 0))
            ui.label(card,title,bg=ui.SURFACE,color=ui.MUTED,size=9).pack(anchor="w")
            value=ui.label(card,"—",size=19,bg=ui.SURFACE);value.pack(anchor="w",pady=(px(10),0))
            self.metrics.append(value)
        section=self.section("overview","新闻动态")
        self.overview_news=ui.label(section,textvariable=self.app.news_refresh_status,color=ui.MUTED,wraplength=px(570))
        self.overview_news.pack(anchor="w",pady=(0,px(13)))
        actions=tk.Frame(section,bg=ui.BG);actions.pack(anchor="w")
        ui.button(actions,"立即更新",self.app.bridge.refresh_news).pack(side="left")
        ui.button(actions,"推荐设置",lambda:self.show_page("news")).pack(side="left",padx=px(8))
        section=self.section("overview","最近的便签")
        self.recent_notes=tk.Frame(section,bg=ui.BG);self.recent_notes.pack(fill="x")
        self.notes_signature=None
        section=self.section("overview","快捷操作")
        for text,action in (("输入便签","show_input"),("截图识别","start_screenshot")):
            ui.button(section,text,app_command(self.app,action)).pack(side="left",padx=(0,px(8)))

    def _general(self):
        section=self.section("general","快捷键")
        self.field(section,"capture_hotkey","划选发送","选中文字后，静默发送到小屏。")
        self.field(section,"compose_hotkey","输入便签","随时打开托盘附近的输入框。")
        section=self.section("general","内容与连接")
        self.field(section,"note_limit","便签上限","满额时替换最早未置顶的便签。",maximum=MAX_NOTES)
        self.field(section,"device_address","蓝牙地址","留空自动查找；指定地址可固定设备。")
        ui.label(section,"Enter 发送便签，Shift+Enter 换行，Esc 收起输入框。",color=ui.MUTED,size=9,
                 wraplength=self.px(560)).pack(anchor="w",pady=(self.px(13),0))

    def _screen(self):
        section=self.section("screen","阅读外观")
        self.field(section,"screen_theme","显示主题",values=("白底黑字","黑底白字"))
        self.field(section,"screen_font_size","字体大小","单位为像素。",values=FONT_SIZES)
        self.field(section,"screen_line_spacing","额外行距","便签与新闻使用同一套显示设置。",maximum=16)
        self.check(section,"screen_show_hints","显示屏幕底部的操作提示")
        section=self.section("screen","阅读预览","外观示意；保存后同步到小屏。")
        self.preview=tk.Canvas(section,height=self.px(140),bg=ui.SURFACE,highlightthickness=1,highlightbackground=ui.BORDER)
        self.preview.pack(fill="x")
        self.preview.bind("<Configure>",lambda event:self._preview())
        for key in ("screen_theme","screen_font_size","screen_line_spacing"):
            self.app.settings[key].trace_add("write",lambda *args:self._preview())

    def _preview(self):
        try:
            dark=self.app.settings["screen_theme"].get()=="黑底白字"
            size=max(10,int(self.app.settings["screen_font_size"].get())//2)
            spacing=int(self.app.settings["screen_line_spacing"].get())
        except (ValueError,KeyError):return
        bg="#161b22" if dark else "#f3f5f7";fg="#e4e9f0" if dark else "#222831"
        self.preview.configure(bg=bg);self.preview.delete("all")
        self.preview.create_text(self.px(20),self.px(20),anchor="nw",text="便签  /  阅读预览",fill="#8b95a4" if dark else "#687382",font=(ui.FONT,9))
        for index,line in enumerate(("把重要的事留在眼前。","让桌面保持专注与从容。")):
            self.preview.create_text(self.px(20),self.px(52+index*(size*1.7+spacing/2)),anchor="nw",text=line,fill=fg,
                font=(ui.FONT,size),width=max(10,self.preview.winfo_width()-self.px(40)))
        self.preview.create_text(self.preview.winfo_width()-self.px(18),self.px(20),anchor="ne",
            text=f"{self.app.settings['screen_font_size'].get()} px · 行距 {spacing}",fill="#8b95a4" if dark else "#687382",font=(ui.FONT,8))

    def _news(self):
        px=self.px
        section=self.section("news","流程分工","推荐与偏好学习可使用更强的 API；翻译和正文简讯可使用本地模型。RSS 与网页正文由 PC 直接抓取。")
        for stage,title in NEWS_MODEL_STAGES.items():
            self.field(section,f'news_{stage}_provider',title,values=tuple(STAGE_PROVIDERS.values()))
        ui.button(section,"本地处理 + API 推荐",self.mixed_model_preset).pack(anchor='w',pady=(px(8),0))
        ui.label(section,"本地服务失败时保留旧队列，不自动切换商业 API。\n偏好学习仍累计约 20 条主动反馈才运行。",color=ui.MUTED,size=9,wraplength=px(560)).pack(anchor='w',pady=(px(10),0))
        section=self.section("news","模型连接与测试","配置下面所选服务。流程中选择“沿用默认新闻模型”的步骤也会使用它；现有 API 在“模型服务”页配置。")
        self.field(section,"news_model_provider","默认新闻模型",values=tuple(NEWS_MODEL_PROVIDERS.values()))
        self.news_model_panels={}
        for provider,url_key,model_key,hint in (
            ("ollama","news_ollama_url","news_ollama_model","Ollama 服务地址，通常为 http://127.0.0.1:11434"),
            ("strata","news_strata_url","news_strata_model","Strata 推理 API，例如 http://127.0.0.1:8080/v1；先在 Strata 中加载模型。"),
            ("local_openai","news_local_url","news_local_model","填写服务实际提供的兼容 API 地址，例如 http://127.0.0.1:8001/v1")):
            panel=tk.Frame(section,bg=ui.BG);self.news_model_panels[provider]=panel
            self.field(panel,url_key,"本地服务地址",hint)
            model=self.field(panel,model_key,"模型名称","可手动填写，或点击下方“读取模型列表”。",values=())
            model.configure(state="normal")
            if provider=="ollama":
                self.field(panel,"news_ollama_context","上下文长度","仅 Ollama 生效；增大会增加内存 / 显存占用。",maximum=131072)
            elif provider=="local_openai":
                self.field(panel,"news_local_api_key","本地服务密钥","可选，留空保持已保存密钥；与原 API 密钥分开保存。",secret=True)
        self.news_local_options=tk.Frame(section,bg=ui.BG)
        self.field(self.news_local_options,"news_model_timeout","等待时间（秒）","本地模型首次加载可能较慢，范围 30–1800 秒。",maximum=1800)
        self.news_model_actions=tk.Frame(section,bg=ui.BG)
        self.news_models_button=ui.button(self.news_model_actions,"读取模型列表",lambda:self.news_model_request(True))
        self.news_models_button.pack(side="left")
        self.news_test_button=ui.button(self.news_model_actions,"测试所选服务",lambda:self.news_model_request(False))
        self.news_test_button.pack(side="left",padx=px(8))
        self.news_model_status=tk.StringVar(value="上方可按流程分工。读取列表与测试使用当前默认新闻模型。")
        self.news_model_status_label=ui.label(section,textvariable=self.news_model_status,color=ui.MUTED,size=9,wraplength=px(565))
        self.news_model_busy=False
        def provider_changed(*args):
            provider={value:key for key,value in NEWS_MODEL_PROVIDERS.items()}[self.app.settings["news_model_provider"].get()]
            for panel in self.news_model_panels.values():panel.pack_forget()
            self.news_local_options.pack_forget();self.news_model_actions.pack_forget();self.news_model_status_label.pack_forget()
            used={provider}
            for key in STAGE_KEYS:
                value={label:name for name,label in STAGE_PROVIDERS.items()}[self.app.settings[key].get()]
                used.add(provider if value=='default' else value)
            for local in ('ollama','strata','local_openai'):
                if local in used:self.news_model_panels[local].pack(fill="x")
            if used-{'configured'}:
                self.news_local_options.pack(fill="x")
            self.news_model_actions.pack(anchor="w",pady=(px(12),px(8)))
            self.news_model_status_label.pack(anchor="w")
            self.news_models_button.configure(state="disabled" if provider=="configured" or self.news_model_busy else "normal")
        self.app.settings["news_model_provider"].trace_add("write",provider_changed)
        for key in STAGE_KEYS:self.app.settings[key].trace_add('write',provider_changed)
        self.update_news_model_provider=provider_changed
        provider_changed()
        section=self.section("news","采集状态")
        ui.label(section,textvariable=self.app.news_refresh_status,color=ui.MUTED,wraplength=px(570)).pack(anchor="w",pady=(0,px(13)))
        actions=tk.Frame(section,bg=ui.BG);actions.pack(anchor="w")
        ui.button(actions,"立即收集筛选",self.app.bridge.refresh_news).pack(side="left")
        ui.button(actions,"查看 / 编辑偏好",lambda:os.startfile(str(self.app.bridge.recommender.preferences.path))).pack(side="left",padx=px(8))
        section=self.section("news","个性化推荐")
        self.check(section,"news_personalized","启用个性化 RSS 推荐",f"{len(FEEDS)} 个订阅源，每 15 分钟收集一次。入选后才读取正文并生成中文简讯。")
        self.field(section,"news_limit","新闻队列容量","默认 30 条。满额先淘汰已读且未置顶的旧新闻，再淘汰未读；置顶固定保留。",maximum=MAX_NEWS)
        ui.label(section,"少量成功或整轮失败都不会清空旧队列。\n小屏累计新消息数是历史到达数，不是当前队列长度。",color=ui.MUTED,size=9,wraplength=px(560)).pack(anchor="w",pady=(0,px(10)))
        self.field(section,"news_exploration_percent","探索比例（%）","预留给较少展示的话题，默认 30%，范围 10–60%。",maximum=60)
        self.field(section,"news_learning_threshold","偏好学习门槛","累计到指定数量的主动反馈后，学习一轮。",maximum=200)
        ui.label(section,"置顶 > PC 打开 > 滑过；确认删除为负反馈。\n普通滑动只作辅助，不计入主动反馈门槛。",color=ui.MUTED,size=9,wraplength=px(560)).pack(anchor="w",pady=(px(10),0))
        ui.label(section,"探索新闻标记“探索”，穿插在已有兴趣新闻之间。\n主题与来源会轮换，一两条删除不会封闭整个领域。",color=ui.MUTED,size=9,wraplength=px(560)).pack(anchor="w",pady=(px(10),0))
        section=self.section("news","网络连接","代理只用于新闻抓取；模型 API 连接使用系统网络设置。")
        self.field(section,"news_proxy_mode","新闻代理",values=tuple(PROXY_MODES.values()))
        self.app.news_proxy_entry=self.field(section,"news_proxy_url","代理地址","例如 http://127.0.0.1:7897")
        def proxy_changed(*args):
            self.app.news_proxy_entry.configure(state="normal" if self.app.settings["news_proxy_mode"].get()==PROXY_MODES["custom"] else "disabled")
        self.app.settings["news_proxy_mode"].trace_add("write",proxy_changed);proxy_changed()
        section=self.section("news","兼容模式","关闭个性化推荐后，使用原有的 Hacker News / GDELT。")
        self.field(section,"gdelt_query","GDELT 查询","仅在兼容模式下使用。")

    def mixed_model_preset(self):
        provider={value:key for key,value in NEWS_MODEL_PROVIDERS.items()}[self.app.settings['news_model_provider'].get()]
        local=provider if provider!='configured' else 'ollama'
        for stage in NEWS_MODEL_STAGES:
            chosen='configured' if stage in ('recommendation','learning') else local
            self.app.settings[f'news_{stage}_provider'].set(STAGE_PROVIDERS[chosen])
        self.app.settings['news_model_provider'].set(NEWS_MODEL_PROVIDERS[local])

    def _tools(self):
        section=self.section('tools','手机微信便签','与桌面日历共享 ClawBot：说“帮我记下…”存便签，提醒或日期时间交给日历；不明确时在微信追问。两个后台需运行。')
        self.wechat_status=tk.StringVar(value='未连接微信。')
        ui.label(section,textvariable=self.wechat_status,color=ui.MUTED,size=9,wraplength=self.px(560)).pack(anchor='w')
        actions=tk.Frame(section,bg=ui.BG);actions.pack(anchor='w',pady=self.px(12))
        ui.button(actions,'共享日历连接',self.app.use_calendar_wechat).pack(side='left')
        ui.button(actions,'暂停 / 继续',lambda:self.app.wechat.pause()).pack(side='left',padx=self.px(7))
        ui.button(actions,'停止此接入',lambda:self.app.wechat.disconnect()).pack(side='left')
        self.wechat_direct_button=ui.button(section,'独立扫码接入',self.app.show_wechat_login)
        self.wechat_direct_button.pack(anchor='w',pady=(0,self.px(8)))
        ui.button(section,'查看未导入文本',self.app.show_wechat_rejected).pack(anchor='w')
        ui.label(section,'仍可用“便签：…”或“日历：…”指定去向，无需重新扫码。\n便签判定不调用大模型；微信追问前不会写入任何应用。',color=ui.MUTED,size=9,wraplength=self.px(560)).pack(anchor='w',pady=(self.px(10),0))
        section=self.section("tools","截图识别","框选一块区域：纯文本完整转录，聊天或流程整理为便签。")
        self.field(section,"screenshot_hotkey","截图快捷键")
        self.check(section,"screenshot_enabled","启用截图识别","需要先到“模型服务”验证图片能力；框选图片会发送到所配置的 API。")
        ui.button(section,"前往模型服务",lambda:self.show_page("api")).pack(anchor="w")

    def _api(self):
        section=self.section("api","连接配置","用于截图及默认新闻模型；新闻也可在“新闻推荐”页单独选择本地模型。")
        self.field(section,"llm_base_url","API 基础地址")
        self.field(section,"llm_model","模型名称")
        self.field(section,"llm_api_key","API 密钥","密钥已保存在本机，留空保持原密钥。" if self.app.api_keys.exists() else "密钥将使用 Windows 本机加密保存。",secret=True)
        self.app.verify_button=ui.button(section,"验证文字与图片能力",self.app.verify_api)
        self.app.verify_button.pack(anchor="w",pady=(self.px(14),0))
        section=self.section("api","中文新闻简讯")
        self.check(section,"llm_enabled","为兼容模式启用中文简讯","仅控制原 HN / GDELT 新闻；使用“新闻推荐”页选择的新闻模型。")
        ui.label(section,"默认新闻模型会向此 API 发送标题、所选正文和学习反馈；\n选择本地新闻模型后改发到本地服务。个人便签不参与新闻偏好学习。",color=ui.MUTED,size=9,wraplength=self.px(560)).pack(anchor="w",pady=(self.px(10),0))

    def current_news_model(self):
        from types import SimpleNamespace
        config=self.app.repository.snapshot()["config"]
        for key in (*NEWS_MODEL_DEFAULTS,"llm_base_url","llm_model"):
            config[key]=self.app.settings[key].get().strip()
        config["news_model_provider"]={value:key for key,value in NEWS_MODEL_PROVIDERS.items()}[config["news_model_provider"]]
        for key in STAGE_KEYS:config[key]={label:name for name,label in STAGE_PROVIDERS.items()}[config[key]]
        for key in ("news_model_timeout","news_ollama_context"):config[key]=int(config[key])
        cloud_key=self.app.settings["llm_api_key"].get().strip()
        local_key=self.app.settings["news_local_api_key"].get().strip()
        keys=SimpleNamespace(load=lambda:cloud_key) if cloud_key else self.app.api_keys
        local_keys=SimpleNamespace(load=lambda:local_key) if local_key else ApiKeyStore(self.app.repository.directory,"news-local-api-key.dpapi")
        return config,NewsModelClient(keys,self.app.repository.directory,local_keys=local_keys)

    def news_model_request(self, models):
        if self.news_model_busy:return
        try:
            config,client=self.current_news_model();validate_news_model(config,require_model=not models)
            signature=settings_signature(config)
            self.news_model_busy=True
            self.news_models_button.configure(state="disabled");self.news_test_button.configure(state="disabled")
            self.news_model_status.set("正在读取本地模型列表…" if models else "正在测试新闻模型；不发送真实新闻或阅读记录…")
            self.app.background_ai("news_models" if models else "news_model_verified",
                lambda:(signature,client.models(config) if models else client.verify(config)))
        except Exception as exc:self.news_model_status.set(str(exc))

    def news_model_result(self, kind, value):
        self.news_model_busy=False;self.news_test_button.configure(state="normal");self.update_news_model_provider()
        if kind.endswith("_error"):
            self.news_model_status.set(str(value));return
        signature,result=value
        try:config,_=self.current_news_model()
        except ValueError:return
        if signature!=settings_signature(config):
            self.news_model_status.set("配置已修改，请重新读取或测试。");return
        if kind=="news_models":
            from .news_model_config import model_key
            key=model_key(config)
            self.fields[key].configure(values=result)
            if result and not self.app.settings[key].get().strip():self.app.settings[key].set(result[0])
            self.news_model_status.set(f"读取到 {len(result)} 个模型。选择模型后测试并保存。" if result else "服务已连接，暂未发现模型；请先在本地服务中加载模型。")
        else:self.news_model_status.set(result)

    def refresh(self):
        snapshot=self.app.repository.snapshot();config=snapshot["config"]
        connected=bool(self.app.connected)
        self.compose_shortcut.configure(text=config["compose_hotkey"])
        self.connection.configure(text="●  设备已连接" if connected else "○  等待设备连接",fg=ui.SUCCESS if connected else ui.MUTED)
        self.status_connection.configure(text=("蓝牙已连接" if connected else "蓝牙等待连接")+f"   ·   待发送 {len(snapshot['outbox'])} 条")
        for label,value in zip(self.metrics,("已连接" if connected else "等待连接",f"{len(snapshot['notes'])} / {config['note_limit']}",f"{len(snapshot['news'])} / {config['news_limit']}")):
            label.configure(text=value)
        self.app.news_refresh_status.set(news_status_text(self.app.repository.directory,snapshot))
        if getattr(self.app,'wechat',None) is not None:
            self.wechat_status.set(self.app.wechat.status())
            self.wechat_direct_button.configure(state='disabled' if self.app.wechat.state.get('source')=='calendar' else 'normal')
        notes=snapshot["notes"]
        signature=tuple((n["id"],n["text"],n.get("pinned")) for n in notes)
        if signature!=self.notes_signature:
            self.notes_signature=signature
            for child in self.recent_notes.winfo_children():child.destroy()
            for note in notes[-3:][::-1]:
                row=tk.Frame(self.recent_notes,bg=ui.BG);row.pack(fill="x",pady=self.px(6))
                ui.button(row,"查看",lambda n=dict(note):self.app.show_note(n)).pack(side="right",padx=(self.px(12),0))
                text=("●  " if note.get("pinned") else "")+" ".join(note["text"].split())
                ui.label(row,text[:54]+("…" if len(text)>54 else ""),color=ui.MUTED,wraplength=self.px(460)).pack(side="left",anchor="w")
            if not notes:ui.label(self.recent_notes,"还没有便签。输入一条，把它送到桌边。",color=ui.MUTED).pack(anchor="w")


def app_command(app,name):
    return lambda:getattr(app,name)()
