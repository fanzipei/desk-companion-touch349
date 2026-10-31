# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Consume explicitly routed notes from the calendar's durable local inbox."""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from .secrets import _transform


class CalendarInbox:
    def __init__(self, path):
        self.path=Path(path).resolve()
        if not self.path.is_file():raise ValueError('未找到日历共享队列，请先启动桌面日历。')

    @contextmanager
    def connect(self):
        # mode=rw must never create an empty file when the path is wrong.
        db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=1)
        try:
            with db:yield db
        finally:db.close()

    def inspect(self):
        with self.connect() as db:
            columns={row[1] for row in db.execute('PRAGMA table_info(wechat_jobs)')}
            if 'route' not in columns:raise ValueError('日历尚未启用微信分发，请更新并重新启动日历。')
            meta=dict(db.execute("SELECT key,value FROM wechat_meta WHERE key IN ('router/version','router/account','router/owner')"))
        if meta.get('router/version')!='1':raise ValueError('日历微信分发版本暂不兼容。')
        return meta

    def head(self):
        from .wechat import inbound_text
        meta=self.inspect()
        account=meta.get('router/account');owner=meta.get('router/owner')
        if not account or not owner:return None
        with self.connect() as db:
            row=db.execute("SELECT key,payload FROM wechat_jobs WHERE account=? AND route='notes' AND reply IS NULL ORDER BY rowid LIMIT 1",(account,)).fetchone()
        if row is None:return None
        value=json.loads(_transform(row[1],False))
        if not isinstance(value,dict) or value.get('version')!=1 or value.get('owner')!=owner:
            raise ValueError('共享便签账号或格式校验失败，未导入。')
        verified=inbound_text(value.get('message'),owner)
        if verified is None or not isinstance(value.get('note_text'),str):
            raise ValueError('共享消息未通过私聊文本校验，未导入。')
        return {'key':row[0],'receipt':verified[0],'text':value['note_text']}

    def acknowledge(self, key):
        with self.connect() as db:
            db.execute("UPDATE wechat_jobs SET reply='',delivered=1 WHERE key=? AND route='notes' AND reply IS NULL",(key,))
