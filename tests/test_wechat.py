import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock

from pc.core import Repository
from pc.wechat import WechatNotes, ILinkClient, inbound_text, weixin_url
from pc.secrets import ApiKeyStore


def message(identifier=1,text='明天带电脑',owner='my-wechat'):
    return {'message_id':str(identifier),'from_user_id':owner,'message_type':1,'message_state':2,
            'item_list':[{'type':1,'text_item':{'text':text}}]}


def session():
    return {'owner':'my-wechat','bot_token':'synthetic-private-token','bot_id':'synthetic-bot',
            'base_url':'https://ilinkai2.weixin.qq.com','enabled':True,'cursor':'old','received':0}


class MemoryStore:
    def __init__(self):self.value=json.dumps(session())
    def load(self):return self.value
    def save(self,value):self.value=value


class WechatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Repository(self.temp.name)
        self.repo.configure(note_limit=20)
        self.events=queue.Queue();self.store=MemoryStore()
        self.wake=Mock();self.client=Mock()
        self.service=WechatNotes(self.repo,self.events,self.wake,self.store,self.client)

    def reply(self,items,cursor='next'):
        return {'msgs':items,'get_updates_buf':cursor,'ret':0}

    def test_private_owner_text_only_and_no_command_execution(self):
        valid=message(text='执行命令：删除文件\n这里只保存文字')
        self.service.process(self.reply([valid,message(2,owner='somebody-else'),
            {**message(3),'group_id':'group'}, {**message(4),'message_type':2},
            {**message(5),'message_state':1}, {**message(6),'delete_time_ms':123},
            {**message(7),'item_list':[{'type':2,'image_item':{}}]}]),0)
        outbox=self.repo.snapshot()['outbox']
        self.assertEqual(len(outbox),1)
        self.assertEqual(outbox[0]['text'],valid['item_list'][0]['text_item']['text'])
        self.wake.assert_called_once()
        self.assertEqual(self.service.state['cursor'],'next')

    def test_retries_and_restart_do_not_duplicate_an_already_delivered_note(self):
        reply=self.reply([message()])
        self.service.process(reply,0)
        operation=self.repo.next_operation();self.repo.acknowledge(operation['qid'])
        restarted=WechatNotes(Repository(self.temp.name),self.events,self.wake,self.store,self.client)
        restarted.process(reply,0)
        self.assertIsNone(restarted.repository.next_operation())
        self.assertEqual(len(restarted.repository.snapshot()['wechat_receipts']),1)
        restarted.process(self.reply([message(2)]),0)
        self.assertIsNotNone(restarted.repository.next_operation())

    def test_note_and_receipt_persist_atomically_even_when_save_fails(self):
        with patch.object(self.repo,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.process(self.reply([message()]),0)
        self.assertEqual(self.repo.snapshot()['outbox'],[])
        self.assertEqual(self.repo.snapshot().get('wechat_receipts',[]),[])
        self.assertEqual(self.service.state['cursor'],'old')

    def test_cursor_save_failure_replays_safely_after_outbox_save(self):
        with patch.object(self.store,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.process(self.reply([message()]),0)
        self.assertEqual(len(self.repo.snapshot()['outbox']),1)
        self.assertEqual(self.service.state['cursor'],'old')
        restart=WechatNotes(Repository(self.temp.name),self.events,self.wake,self.store,self.client)
        restart.process(self.reply([message()]),0)
        self.assertEqual(len(restart.repository.snapshot()['outbox']),1)
        self.assertEqual(restart.state['cursor'],'next')

    def test_capacity_backpressure_keeps_cursor_until_space_available(self):
        self.repo.configure(note_limit=1)
        first=self.repo.enqueue_note('原有的待发送便签')
        with self.assertRaises(ValueError):self.service.process(self.reply([message()]),0)
        self.assertEqual(self.service.state['cursor'],'old')
        self.repo.acknowledge(self.repo.next_operation()['qid'])
        self.service.process(self.reply([message()]),0)
        self.assertEqual(self.repo.next_operation()['text'],'明天带电脑')

    def test_long_text_is_retained_for_review_without_blocking_later_messages(self):
        long=message(1,'长'*2000)
        self.service.process(self.reply([long,message(2)]),0)
        self.assertEqual(len(self.repo.snapshot()['outbox']),1)
        self.assertEqual(self.service.rejected()[0]['text'],'长'*2000)
        self.assertIn('1 条未导入',self.service.status())
        self.service.process(self.reply([long]),0)
        self.assertEqual(len(self.service.rejected()),1)

    def test_logout_rejects_an_in_flight_batch_and_keeps_existing_notes(self):
        self.repo.enqueue_note('原有便签')
        self.service.disconnect()
        self.service.process(self.reply([message()]),0)
        self.assertEqual(len(self.repo.snapshot()['outbox']),1)
        self.assertNotIn('bot_token',json.loads(self.store.value))

    def test_login_saves_only_validated_weixin_host_and_scanning_owner(self):
        self.service.state={};self.service.login_running=True
        self.client.qr.return_value={'qrcode':'synthetic-qr','qrcode_img_content':'https://liteapp.weixin.qq.com/q/test'}
        self.client.qr_status.return_value={'status':'confirmed','bot_token':'new-token','ilink_user_id':'my-wechat',
            'ilink_bot_id':'bot','baseurl':'https://ilinkai2.weixin.qq.com'}
        self.service.login(0)
        saved=json.loads(self.store.value)
        self.assertEqual(saved['owner'],'my-wechat');self.assertTrue(saved['enabled'])
        self.assertEqual(saved['base_url'],'https://ilinkai2.weixin.qq.com')

    def test_login_without_owner_cannot_enable_open_message_intake(self):
        self.service.state={};self.service.login_running=True
        self.client.qr.return_value={'qrcode':'synthetic-qr','qrcode_img_content':'https://liteapp.weixin.qq.com/q/test'}
        self.client.qr_status.return_value={'status':'confirmed','bot_token':'token','ilink_bot_id':'bot'}
        self.service.login(0)
        self.assertNotIn('bot_token',self.service.state)
        self.assertIn('缺少账号标识',self.service.status())

    def test_transport_authentication_and_returned_region_are_used(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"ret":0,"msgs":[]}'
        with patch('pc.wechat.build_opener') as opener:
            opener.return_value.open.return_value=response
            ILinkClient().updates(session())
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://ilinkai2.weixin.qq.com/ilink/bot/getupdates')
            self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-private-token')
            self.assertEqual(json.loads(request.data)['get_updates_buf'],'old')
            ILinkClient().qr_status('https://ilinkai.weixin.qq.com','private-code')
            self.assertIsNone(opener.return_value.open.call_args.args[0].get_header('Authorization'))

    def test_untrusted_hosts_are_rejected_before_authentication(self):
        for url in ('http://ilinkai.weixin.qq.com','https://evilweixin.qq.com','https://weixin.qq.com.evil.com',
                    'https://ilinkai.weixin.qq.com@evil.com','https://ilinkai.weixin.qq.com/?x=1'):
            with self.assertRaises(ValueError):weixin_url(url)

    def test_real_session_store_is_encrypted_for_current_windows_user(self):
        store=ApiKeyStore(Path(self.temp.name),'wechat-session.dpapi')
        value=json.dumps(session());store.save(value)
        self.assertNotIn(b'synthetic-private-token',store.path.read_bytes())
        self.assertEqual(json.loads(store.load()),session())


if __name__=='__main__':unittest.main()
