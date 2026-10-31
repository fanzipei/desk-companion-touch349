import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pc.core import Repository
from pc.news_recommender import NewsRecommender, news_status_text
from pc.protocol import Protocol
from tests.test_news_learning import article, NOW
from tests.test_briefs import BRIEF


def brief(number):
    return {**article(number), 'summary':BRIEF, 'summary_basis':'article',
            'title_zh':f'国际事件{number}', 'recommendation_topic':'geopolitics', 'recommendation_lane':'interest'}


class NewsQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(self.temp.name)
        self.repo.configure(news_personalized=True)

    def push(self, numbers):
        stories = [brief(n) for n in numbers]
        return self.repo.publish_recommendations(stories, stories, NOW)

    def ids(self):
        return [item['id'] for item in self.repo.snapshot()['news']]

    def test_partial_batches_accumulate_and_do_not_replace_whole_queue(self):
        self.push(range(1,21))
        result = self.push([21])
        self.assertEqual(len(self.ids()),21)
        self.assertEqual(self.ids()[0],article(21)['id'])
        self.assertEqual((result['added'],result['evicted']),(1,0))
        self.repo.publish_recommendations([],[],NOW+900)
        self.assertEqual(len(self.ids()),21)
        self.assertEqual(Repository(self.temp.name).snapshot()['news'],self.repo.snapshot()['news'])

    def test_only_pins_survive_fifo_eviction_even_if_opened_or_browsed(self):
        self.push(range(1,31))
        self.repo.apply_event({'event':'news_pinned','id':article(30)['id'],'pinned':True})
        self.repo.apply_event({'event':'open_news','id':article(29)['id']})
        self.repo.apply_event({'event':'news_browsed','id':article(28)['id']})
        for number in range(31,91):
            self.push([number])
            self.assertEqual(len(self.ids()),30)
            self.assertEqual(len(set(self.ids())),30)
        self.assertEqual(self.ids()[0],article(30)['id'])
        self.assertNotIn(article(29)['id'],self.ids())
        self.assertNotIn(article(28)['id'],self.ids())
        self.assertEqual(set(self.ids()[1:]),{article(n)['id'] for n in range(62,91)})

    def test_repeated_article_does_not_move_forward_or_resurrect_after_eviction(self):
        self.repo.configure(news_limit=3)
        self.push([1,2,3]);self.push([2])
        self.assertEqual(self.ids(),[article(n)['id'] for n in (1,2,3)])
        self.push([4])
        expected=self.ids()
        self.assertNotIn(article(3)['id'],expected)
        result=self.push([3,2])
        self.assertEqual(result['added'],0)
        self.assertEqual(self.ids(),expected)
        alias={**brief(99),'url':article(3)['url']+'?utm_source=another'}
        self.repo.publish_recommendations([alias],[alias],NOW+1)
        self.assertEqual(self.ids(),expected)

    def test_delete_and_capacity_changes_never_refill_from_archive(self):
        self.push(range(1,41))
        self.assertEqual(len(self.ids()),30)
        self.repo.configure(news_limit=5)
        keep=self.ids()
        self.repo.configure(news_limit=30)
        self.assertEqual(self.ids(),keep)
        self.repo.apply_event({'event':'news_deleted','id':keep[0]})
        self.assertEqual(len(self.ids()),4)
        self.push([50]);self.assertEqual(len(self.ids()),5)
        self.assertNotIn(keep[0],self.ids())
        self.assertEqual(len(Repository(self.temp.name).snapshot()['news']),5)

    def test_lower_limit_rejects_loss_of_pins_and_can_expand_to_fifty(self):
        self.push(range(1,4))
        for n in range(1,4):self.repo.apply_event({'event':'news_pinned','id':article(n)['id'],'pinned':True})
        with self.assertRaises(ValueError):self.repo.configure(news_limit=2)
        self.repo.configure(news_limit=50);self.push(range(4,70))
        self.assertEqual(len(self.ids()),50)
        self.assertTrue({article(n)['id'] for n in range(1,4)}<=set(self.ids()))
        packet=self.repo.news_packet();self.assertEqual(packet['limit'],50)
        self.assertEqual(len(packet['items']),50)

    def test_title_cache_does_not_count_as_published_but_evicted_ledger_survives(self):
        self.repo.configure(news_limit=1)
        self.repo.publish_recommendations([brief(1)],[article(n) for n in range(1,10)],NOW)
        self.push([2])
        r=NewsRecommender(self.repo)
        with patch('pc.news_exploration.time.time',return_value=NOW):
            plan=r.exploration.plan([article(1),article(3)],self.repo.snapshot()['config'],self.repo.snapshot(),[])
        self.assertEqual([item['id'] for item in plan['pool']],[article(3)['id']])

    def test_upgrade_recovers_valid_briefs_once_and_preserves_other_data(self):
        root=Path(self.temp.name)
        notes=[{'id':'personal','text':'保留便签','pinned':True}]
        saved={'notes':notes,'news':[brief(40)],'news_history':[brief(n) for n in range(1,41)]+[article(99)],
               'news_feeds':{'recommended':[brief(40)]},'news_pins':{str(article(1)['id']):123},
               'config':{'news_personalized':True,'news_limit':20,'llm_model':'keep-model'}}
        (root/'state.json').write_text(json.dumps(saved),encoding='utf8')
        (root/'reminders.json').write_text('old backup',encoding='utf8')
        with patch('pc.core.time.time',return_value=NOW):repo=Repository(root)
        state=repo.snapshot()
        self.assertEqual(state['config']['news_limit'],30)
        self.assertEqual(len(state['news']),30)
        self.assertEqual(state['news'][0]['id'],article(1)['id'])
        self.assertNotIn(article(99)['id'],[n['id'] for n in state['news']])
        self.assertEqual(state['notes'],notes)
        self.assertEqual(state['config']['llm_model'],'keep-model')
        self.assertEqual((root/'reminders.json').read_text(),'old backup')
        repo.configure(news_limit=4)
        self.assertEqual(len(Repository(root).snapshot()['news']),4)

    def test_no_new_titles_is_successful_quiet_round_without_model_or_body_calls(self):
        self.push([1])
        r=NewsRecommender(self.repo)
        r.collector.collect=Mock(return_value=([article(1)],[]))
        r.ai.complete=Mock();r.summarizer.summarize=Mock()
        r._status(last_success_at=NOW,published=1)
        before=self.repo.news_packet()
        with patch('pc.news_exploration.time.time',return_value=NOW):report=r.run_cycle()
        r.ai.complete.assert_not_called();r.summarizer.summarize.assert_not_called()
        self.assertEqual(report['published'],0)
        self.assertEqual(report['queue_size'],1)
        self.assertEqual(report['last_success_at'],NOW)
        self.assertEqual(self.repo.news_packet(),before)
        self.assertIn('队列 1/30',news_status_text(Path(self.temp.name)))

    def test_body_failures_preserve_queue_and_count_only_actual_additions(self):
        self.push(range(1,21))
        r=NewsRecommender(self.repo)
        r.collector.collect=Mock(return_value=([article(21),article(22)],[]))
        r.select=Mock(return_value=[brief(21),brief(22)])
        r.summarizer.summarize=Mock(side_effect=[ValueError('body unavailable'),(BRIEF,'article')])
        report=r.run_cycle()
        self.assertEqual((report['published'],report['queue_size'],report['briefs_deferred']),(1,21,1))
        self.assertEqual(set(self.ids())-{article(n)['id'] for n in range(1,21)},{article(22)['id']})

    def test_thirty_items_use_complete_transactional_frames(self):
        self.push(range(1,31))
        protocol=object.__new__(Protocol)
        protocol.request=AsyncMock(return_value={'ok':True})
        asyncio.run(protocol.sync_news(self.repo.news_packet()))
        requests=[call.args[0] for call in protocol.request.call_args_list]
        self.assertEqual((requests[0]['count'],requests[0]['limit']),(30,30))
        self.assertEqual([r['offset'] for r in requests[1:-1]],[0,5,10,15,20,25])
        self.assertEqual(requests[-1]['op'],'news_commit')


if __name__=='__main__':unittest.main()
