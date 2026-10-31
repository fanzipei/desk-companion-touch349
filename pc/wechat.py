# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Text-only Weixin iLink intake; no desktop-WeChat automation or AI execution."""
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from .core import MAX_NOTE_BYTES
from .secrets import ApiKeyStore

BASE = 'https://ilinkai.weixin.qq.com'
VERSION = '2.4.10'


def weixin_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or
        not parsed.hostname.endswith('.weixin.qq.com') or parsed.username or parsed.password or
        parsed.port not in (None,443) or parsed.query or parsed.fragment or parsed.path not in ('','/')):
        raise ValueError('微信返回了无效的服务地址。')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('微信接口重定向未通过校验，请重新扫码。')


class ILinkClient:
    def request(self, base, endpoint, payload=None, token='', timeout=40):
        headers={'iLink-App-Id':'bot','iLink-App-ClientVersion':str((2<<16)|(4<<8)|10)}
        if payload is not None:
            headers.update({'Content-Type':'application/json','AuthorizationType':'ilink_bot_token',
                'X-WECHAT-UIN':base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
        if token:
            headers['Authorization']='Bearer '+token
            payload={**payload,'base_info':{'channel_version':VERSION,'bot_agent':'DeskCompanion/1.0 (Windows)'}}
        data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf8')
        request=Request(weixin_url(base)+'/ilink/bot/'+endpoint,data=data,headers=headers)
        try:
            with build_opener(NoRedirect()).open(request,timeout=timeout) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('微信返回内容过大。')
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('微信返回格式无效。')
            if value.get('ret')==-14 or value.get('errcode')==-14:
                raise PermissionError('微信登录已失效，请重新扫码。')
            if value.get('ret',0) or value.get('errcode',0):
                raise ValueError('微信接口暂不可用（返回码 '+str(value.get('errcode') or value.get('ret'))+'）。')
            return value
        except HTTPError as exc:
            code=exc.code;exc.close()
            raise ValueError(f'微信服务返回 HTTP {code}。') from None
        except URLError:
            raise ConnectionError('无法连接微信服务，请检查网络后重试。') from None
        except json.JSONDecodeError:
            raise ValueError('微信服务没有返回有效 JSON。') from None

    def qr(self, tokens):
        return self.request(BASE,'get_bot_qrcode?bot_type=3',{'local_token_list':tokens},timeout=15)

    def qr_status(self, base, qrcode, code=''):
        args={'qrcode':qrcode}
        if code:args['verify_code']=code
        return self.request(base,'get_qrcode_status?'+urlencode(args))

    def updates(self, state):
        return self.request(state['base_url'],'getupdates',{'get_updates_buf':state.get('cursor','')},token=state['bot_token'])


def inbound_text(message, owner):
    if (not isinstance(message,dict) or message.get('from_user_id')!=owner or
        message.get('message_type')!=1 or message.get('message_state',2) not in (0,2) or
        message.get('group_id') or message.get('delete_time_ms')):
        return None
    parts=[item['text_item']['text'] for item in message.get('item_list',[]) if isinstance(item,dict) and
           item.get('type')==1 and isinstance(item.get('text_item'),dict) and isinstance(item['text_item'].get('text'),str)]
    text='\n'.join(parts).strip()
    if not text:return None
    if text.startswith('/便签 '):text=text[4:].strip()
    identifier=message.get('message_id') or message.get('client_id')
    if not identifier:
        if message.get('seq') is None:return None
        identifier='seq:'+str(message['seq'])
    receipt=hashlib.sha256(json.dumps([owner,str(identifier)],ensure_ascii=False).encode()).hexdigest()
    return receipt,text


class WechatNotes:
    def __init__(self, repository, events, wake, store=None, client=None):
        self.repository,self.events,self.wake=repository,events,wake
        self.store=store or ApiKeyStore(repository.directory,'wechat-session.dpapi')
        self.client=client or ILinkClient()
        self.lock=threading.RLock();self.stopping=threading.Event();self.changed=threading.Event()
        self.generation=0;self.login_running=False;self.expired=False;self.verify_code=''
        self.message='未连接微信；扫码后向专用对话发送文字即可新增便签。'
        try:
            self.state=json.loads(self.store.load() or '{}')
            if not isinstance(self.state,dict):raise ValueError()
        except (ValueError,OSError):self.state={};self.message='微信绑定数据暂不可读，请重新扫码。'
        if self.state.get('bot_token'):
            self.message='正在恢复微信连接…' if self.state.get('enabled') else '已暂停微信接收。'
        if self.state.get('source')=='calendar':self.message='正在连接日历共享收件队列…'
        self.thread=threading.Thread(target=self.run,name='wechat-notes',daemon=True)

    def start(self):self.thread.start()

    def status(self):
        with self.lock:
            suffix=f" · {len(self.state.get('rejected',[]))} 条未导入" if self.state.get('rejected') else ''
            return self.message+suffix

    def save(self):self.store.save(json.dumps(self.state,ensure_ascii=False))

    def rejected(self):
        with self.lock:return list(self.state.get('rejected',[]))

    def pause(self):
        with self.lock:
            if not self.state.get('bot_token') and self.state.get('source')!='calendar':
                self.message='请先扫码连接微信。';return
            self.state['enabled']=not self.state.get('enabled',True);self.generation+=1;self.save()
            self.message='已暂停微信接收。' if not self.state['enabled'] else '正在连接微信…'
        self.changed.set()

    def disconnect(self):
        with self.lock:
            shared=self.state.get('source')=='calendar'
            self.generation+=1;self.login_running=False
            self.state={'rejected':self.state.get('rejected',[])};self.save()
            self.message='已停止共享便签接收，日历的微信绑定保留。' if shared else '微信已断开，本机便签保留。'
        self.changed.set()

    def submit_code(self, code):
        if not code.strip() or len(code)>32:raise ValueError('请输入手机授权页显示的验证码。')
        with self.lock:self.verify_code=code.strip()

    def begin_login(self):
        with self.lock:
            if self.state.get('source')=='calendar':
                self.message='已共享日历的微信连接；直接发“便签：内容”，无需重新扫码。';return
            if self.login_running:return
            self.generation+=1;generation=self.generation;self.login_running=True
            self.message='正在获取微信二维码…'
        threading.Thread(target=self.login,args=(generation,),name='wechat-login',daemon=True).start()

    def cancel_login(self):
        with self.lock:
            if self.login_running:
                self.generation+=1;self.login_running=False
                self.message='已取消扫码，原有绑定保留。' if self.state.get('bot_token') else '已取消微信扫码。'
        self.changed.set()

    def active_login(self, generation):
        return not self.stopping.is_set() and generation==self.generation and self.login_running

    def login(self, generation):
        try:
            with self.lock:tokens=[self.state['bot_token']] if self.state.get('bot_token') and not self.expired else []
            qr=self.client.qr(tokens)
            if not isinstance(qr.get('qrcode'),str) or not isinstance(qr.get('qrcode_img_content'),str):
                raise ValueError('微信没有返回可用二维码，请稍后重试。')
            link=qr['qrcode_img_content'];parsed=urlsplit(link)
            if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.weixin.qq.com'):
                raise ValueError('微信二维码链接未通过校验。')
            if not self.active_login(generation):return
            self.events.put(('wechat_qr',link))
            self.message='请用手机微信扫码，在手机上确认连接。'
            until=time.monotonic()+300;base=BASE
            while self.active_login(generation) and time.monotonic()<until:
                with self.lock:code=self.verify_code;self.verify_code=''
                try:reply=self.client.qr_status(base,qr['qrcode'],code)
                except (TimeoutError,ConnectionError):
                    if self.stopping.wait(2):return
                    continue
                if not self.active_login(generation):return
                status=reply.get('status')
                if status=='confirmed':
                    if not all(isinstance(reply.get(key),str) and reply[key] for key in ('bot_token','ilink_user_id','ilink_bot_id')):
                        raise ValueError('微信登录信息缺少账号标识，暂未启用接收，请重新扫码。')
                    with self.lock:
                        self.state={'bot_token':reply['bot_token'],'owner':reply['ilink_user_id'],'bot_id':reply['ilink_bot_id'],
                                    'base_url':weixin_url(reply.get('baseurl') or BASE),'enabled':True,'cursor':'',
                                    'received':0,'rejected':self.state.get('rejected',[])}
                        self.save();self.expired=False;self.message='微信已连接。请向微信 ClawBot 对话发送一条文字便签。'
                    self.events.put(('wechat_connected',None));self.changed.set();return
                if status=='scaned_but_redirect':
                    host=reply.get('redirect_host','');base=weixin_url(host if host.startswith('https://') else 'https://'+host)
                elif status=='need_verifycode':
                    self.message='请在连接窗口输入手机授权页的验证码。';self.events.put(('wechat_code_required',None))
                elif status=='scaned':self.message='已扫码，请在手机上确认连接。'
                elif status in ('expired','verify_code_blocked'):
                    raise ValueError('二维码已过期或验证次数过多，请刷新二维码。')
                elif status=='binded_redirect':
                    if self.state.get('bot_token'):
                        self.message='已绑定此后台。';self.changed.set();return
                    raise ValueError('微信已有连接，可在设置中选择“共享日历连接”，无需解除已有绑定。')
                self.stopping.wait(1)
            if self.active_login(generation):self.message='二维码已过期，请刷新后重试。'
        except Exception as exc:
            if self.active_login(generation):self.message=str(exc) if isinstance(exc,(ValueError,ConnectionError,OSError)) else '微信连接暂未完成，请重试。'
        finally:
            with self.lock:
                if generation==self.generation:self.login_running=False

    def process(self, reply, generation):
        messages=reply.get('msgs',[])
        if not isinstance(messages,list):raise ValueError('微信消息列表格式无效。')
        with self.lock:
            if self.stopping.is_set() or generation!=self.generation or not self.state.get('enabled'):return
            for message in messages:
                item=inbound_text(message,self.state['owner'])
                if item is None:continue
                receipt,text=item
                self.import_text(receipt,text)
            previous_cursor=self.state.get('cursor','')
            cursor=reply.get('get_updates_buf')
            if isinstance(cursor,str) and cursor:self.state['cursor']=cursor
            try:self.save()
            except OSError:
                self.state['cursor']=previous_cursor
                raise
            self.message=f"微信已连接 · 已接收 {self.state.get('received',0)} 条便签。"

    def import_text(self, receipt, text):
        if not text.strip() or len(text.encode('utf8'))>MAX_NOTE_BYTES:
            rejected=self.state.setdefault('rejected',[])
            if not any(item['receipt']==receipt for item in rejected):
                reason='便签内容为空。' if not text.strip() else '超过单条便签 3072 字节，请拆分后重新发送。'
                rejected.append({'receipt':receipt,'text':text,'reason':reason})
                self.state['rejected']=rejected[-50:]
            return
        # The receipt and note operation are persisted together before source acknowledgement.
        note=self.repository.enqueue_note(text,receipt=receipt)
        if note is not None:
            self.state['received']=self.state.get('received',0)+1
            self.events.put(('wechat_note',None));self.wake()

    def use_calendar(self, path=None):
        from .wechat_shared import CalendarInbox
        path=path or self.repository.directory.parent.parent/'桌面日历/data/calendar.sqlite3'
        inbox=CalendarInbox(path);inbox.inspect()
        with self.lock:
            self.generation+=1;self.login_running=False;self.expired=False
            self.state={'source':'calendar','calendar_db':str(inbox.path),'enabled':True,
                        'received':self.state.get('received',0),'rejected':self.state.get('rejected',[])}
            self.save();self.message='已共享日历微信：可以说“帮我记下…”；提醒或日期时间交给日历，不明确时会在微信追问。'
        self.changed.set()

    def shared_step(self, state, generation):
        from .wechat_shared import CalendarInbox
        inbox=CalendarInbox(state['calendar_db']);item=inbox.head()
        with self.lock:
            if self.stopping.is_set() or generation!=self.generation or not self.state.get('enabled'):return
            from .wechat_news import NewsRelay
            if NewsRelay(inbox, self.repository).step():
                self.events.put(('changed', None)); self.wake()
            if item is None:
                self.message=f"微信已就绪 · 已接收 {self.state.get('received',0)} 条便签；可说“未读新闻发给我”。"
                return
            self.import_text(item['receipt'],item['text'])
            self.save()
            inbox.acknowledge(item['key'])
            self.message=f"日历共享接入已连接 · 已接收 {self.state.get('received',0)} 条便签。"

    def run(self):
        while not self.stopping.is_set():
            with self.lock:
                state=dict(self.state);generation=self.generation
                shared=state.get('source')=='calendar'
                enabled=bool((shared or (state.get('bot_token') and state.get('owner'))) and state.get('enabled') and not self.expired and not self.login_running)
            if not enabled:
                self.changed.wait(1);self.changed.clear();continue
            try:
                if shared:
                    self.shared_step(state,generation)
                    self.changed.wait(1);self.changed.clear();continue
                reply=self.client.updates(state)
                self.process(reply,generation)
                if not reply.get('msgs'):self.stopping.wait(.2)
            except PermissionError:
                self.expired=True;self.message='微信登录已失效，请重新扫码。'
            except TimeoutError:continue
            except ValueError as exc:
                self.message='微信便签暂未导入：'+str(exc)
                self.changed.wait(5);self.changed.clear()
            except ConnectionError:
                self.message='微信网络连接中断，正在重连；便签队列保留。'
                self.changed.wait(5);self.changed.clear()
            except OSError:
                self.message='微信接收进度暂未保存，请检查本机磁盘；稍后会重新接收并去重。'
                self.changed.wait(5);self.changed.clear()
            except Exception:
                self.message='微信接收暂不可用，请在设置中重新扫码。'
                self.changed.wait(5);self.changed.clear()

    def stop(self):
        self.stopping.set();self.changed.set()
