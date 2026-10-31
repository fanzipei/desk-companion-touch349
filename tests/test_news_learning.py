import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from pc.core import Repository
from pc.news_feeds import FEEDS, FeedCollector, RSS_ID_BASE, parse_feed
from pc.news_preferences import NewsPreferences, WEIGHTS
from pc.news_recommender import NewsRecommender
from pc.news_schedule import CollectionSchedule
from pc.summarizer import Summarizer, config_signature
from tests.test_briefs import BRIEF
from tests.test_mixed_news import hn

NOW=1793106000


def article(number):
    return {"id":RSS_ID_BASE+number,"source":"rss","feed":"bbc-world","publisher":"BBC",
            "category":"politics","title":f"World event {number}","url":f"https://example.org/news/{number}",
            "domain":"example.org","time":NOW,"score":0,"comments":0}


def feedback(seq,kind="browsed",number=1):
    return {"seq":seq,"kind":kind,"id":RSS_ID_BASE+number,"title":f"国际事件{number}","domain":"example.org","time":NOW}


class FeedTests(unittest.TestCase):
    def test_rss_and_atom_are_bounded_dated_and_have_cross_feed_stable_ids(self):
        raw=b'<rss><channel><item><title>A &amp; B</title><link>https://example.org/a?utm_source=rss</link><pubDate>Tue, 27 Oct 2026 12:00:00 GMT</pubDate></item><item><title>old</title><link>https://example.org/old</link><pubDate>Tue, 20 Oct 2026 12:00:00 GMT</pubDate></item><item><title>bad</title><link>file:///private</link></item></channel></rss>'
        first=parse_feed(raw,FEEDS[0],NOW)
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]["title"],"A & B")
        atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Other title</title><link href="https://example.org/a"/><updated>2026-10-27T12:00:00Z</updated></entry></feed>'
        second=parse_feed(atom,FEEDS[1],NOW)
        self.assertEqual(first[0]["id"],second[0]["id"])
        self.assertEqual(int(float(first[0]["id"])),first[0]["id"])

    def test_entities_and_oversized_or_failed_responses_cannot_replace_cache(self):
        with self.assertRaises(ValueError):parse_feed(b'<!DOCTYPE rss [<!ENTITY x "x">]><rss/>',FEEDS[0],NOW)
        with tempfile.TemporaryDirectory() as directory:
            collector=FeedCollector(directory)
            collector.cache[FEEDS[0][0]]={"items":[article(1)],"etag":"v1"}
            with patch('pc.news_feeds.build_opener') as opener:
                opener.return_value.open.side_effect=HTTPError(FEEDS[0][3],304,'not modified',{},None)
                items,error=collector.fetch_one(FEEDS[0],{"news_proxy_mode":"direct"},NOW)
                self.assertEqual(items,[article(1)]);self.assertIsNone(error)
                opener.return_value.open.side_effect=TimeoutError()
                items,error=collector.fetch_one(FEEDS[0],{"news_proxy_mode":"direct"},NOW)
                self.assertEqual(items,[article(1)]);self.assertEqual(error,'TimeoutError')

    def test_all_sources_collected_and_duplicate_urls_or_stale_cache_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            collector=FeedCollector(directory)
            with patch.object(collector,'fetch_one',return_value=([article(1),{**article(2),'time':NOW-3*86400}],None)) as fetch:
                items,status=collector.collect({},NOW)
            self.assertEqual(fetch.call_count,len(FEEDS));self.assertEqual(len(status),len(FEEDS))
            self.assertEqual([item['id'] for item in items],[article(1)['id']])


class PreferenceTests(unittest.TestCase):
    def test_disk_failure_does_not_acknowledge_unsaved_feedback_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            with patch('pc.news_preferences.atomic_json',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):prefs.record(feedback(1),'board')
            self.assertEqual(prefs.state['events'],[])
            self.assertTrue(prefs.record(feedback(1),'board'))

    def test_reconnect_and_live_duplicate_feedback_is_counted_once_and_weights_are_ordered(self):
        self.assertGreater(WEIGHTS['pinned'],WEIGHTS['opened'])
        self.assertGreater(WEIGHTS['opened'],WEIGHTS['browsed'])
        self.assertLess(WEIGHTS['deleted'],0)
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            self.assertTrue(prefs.record(feedback(2,'pinned'),'board'))
            self.assertTrue(prefs.record(feedback(1,'opened'),'board'))
            self.assertFalse(NewsPreferences(directory).record(feedback(2,'pinned'),'board'))
            self.assertEqual(len(NewsPreferences(directory).state['events']),2)
            with self.assertRaises(ValueError):prefs.record(feedback(3,'unpin'),'board')

    def test_learning_preserves_manual_skill_and_keeps_feedback_arriving_during_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            prefs.path.write_text(prefs.skill().replace('## 本人明确偏好','另外关注国际能源政策。\n\n## 本人明确偏好'),encoding='utf8')
            prefs.record(feedback(1,'pinned'),'board')
            ai=Mock()
            def complete(*args):
                prefs.record(feedback(2,'opened',2),'board')
                return json.dumps({'prefer':['国际能源政策'],'avoid':[],'uncertain':['单条反馈仍需观察']})
            ai.complete.side_effect=complete
            self.assertTrue(prefs.evolve(ai,{'news_learning_threshold':1}))
            self.assertIn('另外关注国际能源政策。',prefs.skill())
            self.assertEqual(prefs.state['cursor'],1)
            self.assertEqual(prefs.state['events'][-1]['index'],2)
            self.assertEqual(len(list((prefs.directory/'history').glob('*.md'))),1)
            ai.complete.return_value='invalid';ai.complete.side_effect=None
            old=prefs.skill()
            with self.assertRaises(ValueError):prefs.evolve(ai,{'news_learning_threshold':1})
            self.assertEqual(prefs.skill(),old);self.assertEqual(prefs.state['cursor'],1)

    def test_repeated_swipes_do_not_multiply_evidence_in_learning_window(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            for seq in range(1,8):prefs.record(feedback(seq),'board')
            prefs.record(feedback(8,'pinned'),'board')
            ai=Mock();ai.complete.return_value='{"prefer":[],"avoid":[],"uncertain":[]}'
            prefs.evolve(ai,{'news_learning_threshold':1})
            evidence=json.loads(ai.complete.call_args.args[2])['feedback']
            self.assertEqual(len(evidence),2)

    def test_large_backlog_is_processed_in_order_without_skipping_early_strong_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            for seq in range(1,165):prefs.record(feedback(seq,'pinned' if seq==1 else 'opened',seq),'board')
            ai=Mock();ai.complete.return_value='{"prefer":[],"avoid":[],"uncertain":[]}'
            prefs.evolve(ai,{})
            evidence=json.loads(ai.complete.call_args.args[2])['feedback']
            self.assertEqual(evidence[0]['kind'],'pinned');self.assertEqual(prefs.state['cursor'],160)
            self.assertFalse(prefs.evolve(ai,{})) # Four left do not trigger another learning call.
            for seq in range(165,181):prefs.record(feedback(seq,'opened',seq),'board')
            prefs.evolve(ai,{})
            self.assertEqual(prefs.state['cursor'],180)

    def test_twenty_active_decisions_required_and_browsing_does_not_count(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            for seq in range(1,20):prefs.record(feedback(seq,'opened',seq),'board')
            for seq in range(20,170):prefs.record(feedback(seq,'browsed',seq),'board')
            ai=Mock();ai.complete.return_value='{"prefer":[],"avoid":[],"uncertain":[]}'
            self.assertEqual(prefs.learning_count(),19)
            self.assertFalse(prefs.evolve(ai,{}));ai.complete.assert_not_called()
            prefs.record(feedback(170,'pinned',20),'board')
            self.assertEqual(prefs.learning_count(),20)
            self.assertTrue(prefs.evolve(ai,{}))
            self.assertEqual(prefs.learning_count(),0)
            ai.complete.assert_called_once()

    def test_repeated_open_of_same_article_cannot_fill_learning_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=NewsPreferences(directory)
            for seq in range(1,25):prefs.record(feedback(seq,'opened'),'board')
            self.assertEqual(prefs.learning_count(),1)
            ai=Mock();self.assertFalse(prefs.evolve(ai,{}));ai.complete.assert_not_called()


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        clock=patch('pc.news_exploration.time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.repo=Repository(self.temp.name)
        self.repo.apply_state({'notes':[{'id':'personal','text':'保留便签','pinned':True}]})
        self.repo.configure(news_personalized=True,llm_base_url='https://example.org/v1',llm_model='test',news_limit=2)
        self.recommender=NewsRecommender(self.repo)

    def selection(self,number=1):
        return json.dumps({'selected':[{'id':RSS_ID_BASE+number,'title_zh':'国际政策出现新变化','category':'politics'}]})

    def test_filter_precedes_selected_body_fetch_and_cached_brief_is_reused(self):
        steps=[]
        self.recommender.collector.collect=Mock(return_value=([article(1),article(2)],[]))
        self.recommender.ai.complete=Mock(side_effect=lambda *args:steps.append('filter') or self.selection())
        self.recommender.summarizer.summarize=Mock(side_effect=lambda *args,**kwargs:steps.append('body') or (BRIEF,'article'))
        result=self.recommender.run_cycle()
        self.assertEqual(steps,['filter','body']);self.assertEqual(result['published'],1)
        call=self.recommender.summarizer.summarize.call_args
        self.assertEqual(call.args[0]['id'],article(1)['id']);self.assertTrue(call.kwargs['require_article'])
        packet=self.repo.news_packet()['items'][0]
        self.assertIn('国际政策出现新变化',packet['title']);self.assertEqual(packet['summary'],BRIEF)
        self.recommender.collector.collect.return_value=([article(1)],[])
        self.recommender.run_cycle()
        self.assertEqual(steps,['filter','body'])  # Delivered titles do not consume another model call.
        self.assertEqual(self.repo.snapshot()['notes'][0]['text'],'保留便签')

    def test_invalid_model_or_article_failure_preserves_published_news(self):
        story={**article(1),'title_zh':'现有新闻','summary':BRIEF,'summary_basis':'article'}
        self.repo.publish_recommendations([story],[story],NOW)
        original=self.repo.news_packet()
        self.recommender.collector.collect=Mock(return_value=([article(2)],[]))
        self.recommender.ai.complete=Mock(return_value=self.selection(99))
        with self.assertRaises(ValueError):self.recommender.run_cycle()
        self.assertEqual(self.repo.news_packet(),original)
        self.recommender.ai.complete.return_value=self.selection(2)
        self.recommender.summarizer.summarize=Mock(side_effect=ValueError('no article'))
        with self.assertRaises(ValueError):self.recommender.run_cycle()
        self.assertEqual(self.repo.news_packet(),original)

    def test_deleted_items_stay_deleted_and_pins_survive_model_ranking(self):
        self.repo.update_news([hn(1)],NOW)
        self.repo.publish_recommendations([article(1),article(2)],[article(1),article(2)],NOW)
        self.repo.apply_event({'event':'news_pinned','id':article(1)['id'],'pinned':True})
        self.repo.apply_event({'event':'news_deleted','id':article(2)['id']})
        self.repo.publish_recommendations([article(2),article(3)],[article(2),article(3)],NOW+1)
        self.assertEqual([s['id'] for s in self.repo.snapshot()['news']],[article(1)['id'],article(3)['id']])
        self.assertNotIn(1,[s['id'] for s in self.repo.snapshot()['news']])

    def test_strict_article_mode_never_calls_model_on_title_only_fallback(self):
        keys=Mock();keys.load.return_value='test-key'
        with patch('pc.summarizer.article_text',side_effect=ValueError()),patch('pc.summarizer.build_opener') as api:
            with self.assertRaises(ValueError):Summarizer(keys).summarize(article(1),self.repo.snapshot()['config'],require_article=True)
            api.assert_not_called()


class CollectionScheduleTests(unittest.TestCase):
    def test_all_sources_share_900_second_slots_without_accumulating_request_duration(self):
        schedule=CollectionSchedule(100)
        self.assertEqual(schedule.delay(100),0)
        schedule.started(100);self.assertEqual(schedule.next_due,1000)
        self.assertEqual(schedule.delay(700),300)
        schedule.started(1005);self.assertEqual(schedule.next_due,1900)
        schedule.started(2801);self.assertEqual(schedule.next_due,3700)


if __name__=='__main__':unittest.main()
