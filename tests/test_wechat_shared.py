import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import contextmanager

from pc.core import Repository
from pc.secrets import _transform
from pc.wechat import WechatNotes
from pc.wechat_shared import CalendarInbox
from tests.test_wechat import MemoryStore, message


@contextmanager
def database(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()


class SharedWechatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'calendar.sqlite3'
        with database(self.path) as db:
            db.executescript("CREATE TABLE wechat_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE wechat_jobs(key TEXT PRIMARY KEY,account TEXT,payload BLOB,reply TEXT,delivered INTEGER DEFAULT 0,route TEXT NOT NULL);")
            db.executemany('INSERT INTO wechat_meta VALUES (?,?)',[('router/version','1'),('router/account','bot'),('router/owner','my-wechat')])
        self.repo=Repository(self.root/'companion')
        self.events=queue.Queue();self.client=Mock();self.store=MemoryStore()
        self.service=WechatNotes(self.repo,self.events,Mock(),self.store,self.client)
        self.service.use_calendar(self.path)

    def add(self,key='one',text='带电脑',owner='my-wechat',account='bot',route='notes'):
        raw={'version':1,'owner':owner,'message':message(key,'便签：'+text,owner),'note_text':text}
        with database(self.path) as db:
            db.execute('INSERT INTO wechat_jobs(key,account,payload,route) VALUES (?,?,?,?)',
                       (key,account,_transform(json.dumps(raw,ensure_ascii=False).encode(),True),route))

    def flag(self,key='one'):
        with database(self.path) as db:return db.execute('SELECT reply,delivered FROM wechat_jobs WHERE key=?',(key,)).fetchone()

    def step(self):self.service.shared_step(dict(self.service.state),self.service.generation)

    def test_only_explicit_notes_import_and_existing_calendar_jobs_are_untouched(self):
        self.add();self.add('calendar-job',route='calendar')
        self.step()
        self.assertEqual(self.repo.next_operation()['text'],'带电脑')
        self.assertEqual(self.flag(),('',1))
        self.assertEqual(self.flag('calendar-job'),(None,0))
        self.assertNotIn('bot_token',self.service.state)
        self.client.updates.assert_not_called();self.client.qr.assert_not_called()

    def test_process_crash_between_outbox_save_and_source_ack_is_safe(self):
        self.add()
        with patch.object(CalendarInbox,'acknowledge',side_effect=OSError('disk busy')):
            with self.assertRaises(OSError):self.step()
        self.assertEqual(len(self.repo.snapshot()['outbox']),1)
        self.assertEqual(self.flag(),(None,0))
        self.step()
        self.assertEqual(len(self.repo.snapshot()['outbox']),1)
        self.assertEqual(self.flag(),('',1))

    def test_offline_backpressure_keeps_shared_note_durable(self):
        self.repo.configure(note_limit=1);self.repo.enqueue_note('先前内容');self.add()
        with self.assertRaises(ValueError):self.step()
        self.assertEqual(self.flag(),(None,0))
        self.repo.acknowledge(self.repo.next_operation()['qid']);self.step()
        self.assertEqual(self.repo.next_operation()['text'],'带电脑')

    def test_different_accounts_and_tampered_owner_cannot_import(self):
        self.add(account='other-bot')
        self.assertIsNone(CalendarInbox(self.path).head())
        self.add('bad-owner',owner='outsider')
        with self.assertRaises(ValueError):self.step()
        self.assertIsNone(self.repo.next_operation())

    def test_pause_and_disconnect_do_not_change_calendar_or_consume_notes(self):
        self.add();generation=self.service.generation;state=dict(self.service.state)
        self.service.pause();self.service.shared_step(state,generation)
        self.assertEqual(self.flag(),(None,0))
        self.service.disconnect()
        self.assertEqual(CalendarInbox(self.path).inspect()['router/account'],'bot')
        self.assertEqual(self.flag(),(None,0))

    def test_shared_background_never_polls_wechat_server(self):
        self.add();self.service.start()
        try:
            self.assertEqual(self.events.get(timeout=3)[0],'wechat_note')
            self.client.updates.assert_not_called();self.client.qr.assert_not_called()
        finally:self.service.stop();self.service.thread.join(3)

    def test_missing_or_old_calendar_database_is_not_created_or_modified(self):
        missing=self.root/'missing.sqlite3'
        with self.assertRaises(ValueError):CalendarInbox(missing)
        self.assertFalse(missing.exists())
        old=self.root/'old.sqlite3'
        with database(old) as db:db.execute('CREATE TABLE wechat_jobs(key TEXT)')
        with self.assertRaises(ValueError):CalendarInbox(old).inspect()


if __name__=='__main__':unittest.main()
