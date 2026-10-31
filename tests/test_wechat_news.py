import tempfile
import re
import unittest
from unittest.mock import patch

from pc.core import Repository
from pc.wechat_news import news_request, format_news
from tests.test_news_queue import brief


class WechatNewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = Repository(self.temp.name)
        self.repo.configure(news_personalized=True, news_limit=4)

    def push(self, numbers):
        items = [brief(i) for i in numbers]
        self.repo.publish_recommendations(items, items, 100)

    def ids(self):
        return [item['id'] for item in self.repo.snapshot()['news']]

    def test_most_recent_read_evicted_before_oldest_unread(self):
        self.push([1,2,3,4]); self.repo.mark_news_read([brief(1)['id']])
        self.push([5])
        self.assertEqual(self.ids(), [brief(i)['id'] for i in (5,2,3,4)])

    def test_oldest_read_first_then_oldest_unread_and_pins_survive(self):
        self.push([1,2,3,4]); self.repo.mark_news_read([brief(i)['id'] for i in (1,3,4)])
        self.repo.apply_event({'event':'news_pinned','id':brief(4)['id'],'pinned':True})
        self.push([5]); self.assertNotIn(brief(3)['id'], self.ids())
        self.push([6]); self.assertNotIn(brief(1)['id'], self.ids())
        self.push([7]); self.assertNotIn(brief(2)['id'], self.ids())
        self.assertEqual(self.ids()[0],brief(4)['id'])

    def test_capacity_change_preserves_unread_and_does_not_reorder(self):
        self.push([1,2,3,4]); self.repo.mark_news_read([brief(1)['id'],brief(3)['id']])
        self.repo.configure(news_limit=2)
        self.assertEqual(self.ids(),[brief(2)['id'],brief(4)['id']])
        self.repo.configure(news_limit=4)
        self.assertEqual(len(self.ids()),2)

    def test_read_is_durable_additive_and_save_failure_rolls_back(self):
        self.push([1]); identifier=brief(1)['id']
        before=self.repo.snapshot()
        with patch.object(self.repo,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.repo.mark_news_read([identifier])
        self.assertEqual(self.repo.snapshot(),before)
        self.assertTrue(self.repo.mark_news_read([identifier]))
        self.assertFalse(self.repo.mark_news_read([identifier]))
        self.repo.apply_state({'notes':[], 'news_read_ids':[]})
        self.assertEqual(Repository(self.temp.name).snapshot()['news_read_ids'],[identifier])

    def test_query_intents_and_recording_are_distinct(self):
        for text in ('最近推送的未读的新闻发给我','发我最近5条未读新闻','看看新闻','所有未读新闻发我',
                     '未读信息发送给我','把未读的消息发给我'):
            self.assertTrue(news_request(text)['unread_only'],text)
        self.assertEqual(news_request('发我最近5条未读新闻')['limit'],5)
        self.assertFalse(news_request('看看全部新闻')['unread_only'])
        for text in ('帮我记下这条新闻观点','提醒我明天看新闻','便签：看看最新新闻','新闻自由很重要'):
            self.assertIsNone(news_request(text),text)

    def test_brief_split_marks_only_final_part_and_preserves_all_content(self):
        story={**brief(1),'summary':'新闻正文。'*900}
        parts=format_news([story])
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(part['text'])<=1800 for part in parts))
        self.assertTrue(all(not part['ids'] for part in parts[:-1]))
        self.assertEqual(parts[-1]['ids'],[story['id']])
        self.assertIn(story['url'],parts[-1]['text'])
        restored=''.join(re.sub(r'^【本条续 \d+/\d+】\n','',part['text']) for part in parts)
        self.assertIn(story['summary'],restored)

    def test_empty_or_headlines_only_does_not_fetch_anything(self):
        self.assertEqual(format_news([])[0]['ids'],[])
        parts=format_news([brief(1)],headlines_only=True)
        self.assertNotIn(brief(1)['summary'],parts[0]['text'])
        self.assertIn(brief(1)['title_zh'],parts[0]['text'])
