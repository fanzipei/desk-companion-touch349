# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Read existing curated briefs through the calendar's single WeChat connection."""
import json
import re

from .secrets import _transform
from .core import safe_web_url


def news_request(text):
    """Conservative local query intent; recording/reminding keeps its old route."""
    text = text.strip()
    if not (re.search(r'新闻|简讯|资讯', text) or
            re.search(r'(?:未读|已读|推送)(?:的)?(?:信息|消息)', text)):
        return None
    if re.search(r'便签[：:]|提醒|闹钟|记下|记住|记一下|记一笔|帮我记|记录|写下|存下|存一下|保存|安排|预约|会议|日历', text):
        return None
    if not re.search(r'发给|发我|给我|推送|未读|看看|看一看|看下|看一下|阅读|列出|查看|最近|最新', text):
        return None
    count = re.search(r'(\d{1,3})\s*(?:条|篇|则)', text)
    return {'unread_only': '未读' in text or not bool(re.search(r'(?:包括|含|所有|全部).*已读|(?:全部|所有)(?:的)?新闻|已读(?:的)?新闻', text)),
            'limit': max(1, min(50, int(count[1]))) if count else 50,
            'headlines_only': bool(re.search(r'只.*标题|仅.*标题', text))}


def format_news(items, remaining=0, headlines_only=False):
    """Each article's final part carries its read receipt. Never truncate a brief."""
    if not items:
        return [{'text': '当前没有未读新闻。队列会在下一轮采集后继续更新；已读新闻可用“看看全部新闻”查看。', 'ids': []}]
    parts = []
    for index, item in enumerate(items, 1):
        title = item.get('title_zh') or item['title']
        source = item.get('publisher') or item.get('domain') or '新闻'
        body = '' if headlines_only else item.get('summary', '').strip()
        url = safe_web_url(item.get('url')) or ''
        text = f'【新闻 {index}/{len(items)}】{title}\n来源：{source}'
        if body:
            text += '\n\n' + body
        if url:
            text += '\n\n原文：' + url
        if index == len(items) and remaining:
            text += f'\n\n另有 {remaining} 条未读，可再说“未读新闻发给我”。'
        chunks = [text[n:n+1600] for n in range(0, len(text), 1600)]
        for n, chunk in enumerate(chunks):
            prefix = f'【本条续 {n+1}/{len(chunks)}】\n' if n else ''
            parts.append({'text': prefix + chunk, 'ids': [item['id']] if n == len(chunks)-1 else []})
    return parts


class NewsRelay:
    def __init__(self, inbox, repository):
        self.inbox, self.repository = inbox, repository

    def step(self):
        meta = self.inbox.inspect()
        account, owner = meta.get('router/account'), meta.get('router/owner')
        if not account or not owner:
            return False
        changed = False
        with self.inbox.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wechat_news_parts'").fetchone():
                return False  # Calendar upgrade can happen after the companion starts.
            receipts = db.execute('''SELECT p.key,p.part,p.body FROM wechat_news_parts p
                JOIN wechat_jobs j ON j.key=p.key WHERE j.account=? AND p.sent=1 AND p.applied=0''', (account,)).fetchall()
        for key, part, raw in receipts:
            value = json.loads(_transform(raw, False))
            changed = self.repository.mark_news_read(value['ids']) or changed
            # Only acknowledge after the PC state is durably saved; replay is additive.
            with self.inbox.connect() as db:
                db.execute('UPDATE wechat_news_parts SET applied=1 WHERE key=? AND part=? AND sent=1', (key, part))
        with self.inbox.connect() as db:
            row = db.execute("SELECT key,payload FROM wechat_jobs WHERE account=? AND route='news' AND reply IS NULL ORDER BY rowid LIMIT 1", (account,)).fetchone()
            reserved = db.execute('''SELECT p.body FROM wechat_news_parts p JOIN wechat_jobs j ON j.key=p.key
                WHERE j.account=? AND (j.delivered=0 OR p.sent=1) AND p.applied=0''', (account,)).fetchall()
        if row is None:
            return changed
        from .wechat import inbound_text
        message = json.loads(_transform(row[1], False))
        verified = inbound_text(message, owner)
        options = news_request(verified[1]) if verified else None
        if options is None:
            raise ValueError('微信新闻请求未通过本人私聊文本校验。')
        snapshot = self.repository.snapshot()
        read = set(snapshot['news_read_ids'])
        reserved_ids = {i for (raw,) in reserved for i in json.loads(_transform(raw, False))['ids']}
        available = [item for item in snapshot['news'] if (not options['unread_only'] or item['id'] not in read)
                     and item['id'] not in reserved_ids]
        items = available[:options['limit']]
        parts = format_news(items, len(available)-len(items), options['headlines_only'])
        with self.inbox.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute("SELECT reply FROM wechat_jobs WHERE key=? AND account=? AND route='news'", (row[0], account)).fetchone()
            if current is None or current[0] is not None:
                return changed
            for index, value in enumerate(parts):
                raw = _transform(json.dumps(value, ensure_ascii=False).encode('utf8'), True)
                db.execute('INSERT INTO wechat_news_parts(key,part,body) VALUES (?,?,?)', (row[0], index, raw))
            db.execute("UPDATE wechat_jobs SET reply='news-ready' WHERE key=?", (row[0],))
        return changed
