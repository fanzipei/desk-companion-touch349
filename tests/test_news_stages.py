import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pc.core import Repository
from pc.news_model_config import stage_config, uses_cloud, model_signature
from pc.news_recommender import NewsRecommender, translated_title_ok
from tests.test_news_learning import article, feedback, NOW
from tests.test_briefs import BRIEF


class NewsStageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Repository(self.temp.name)
        self.repo.configure(news_personalized=True,news_limit=2,llm_base_url='https://example.org/v1',llm_model='strong-api',
            news_ollama_model='local-model',news_recommendation_provider='configured',news_translation_provider='ollama',
            news_brief_provider='ollama',news_learning_provider='configured')
        self.r=NewsRecommender(self.repo)

    def test_api_ranks_and_learns_local_translates_and_summarizes(self):
        for seq in range(1,21):self.r.preferences.record(feedback(seq,'opened',1000+seq),'test-device')
        calls=[]
        def complete(config,system,content):
            data=json.loads(content);calls.append((config['news_model_provider'],data))
            if 'feedback' in data:
                self.assertEqual(config['news_model_provider'],'configured')
                return '{"prefer":["国际政策"],"avoid":[],"uncertain":[]}'
            if 'exploration_ids' in data:
                self.assertEqual(config['news_model_provider'],'configured')
                self.assertIn('不翻译',system)
                self.assertTrue(all('body' not in row for row in data['headlines']))
                return json.dumps({'selected':[{'id':row['id'],'category':'politics'} for row in data['headlines']]})
            self.assertEqual(config['news_model_provider'],'ollama')
            self.assertNotIn('skill',data)
            return json.dumps({'translations':[{'id':row['id'],'title_zh':'本地翻译新闻标题'} for row in data['headlines']]})
        self.r.ai.complete=Mock(side_effect=complete)
        self.r.ai.complete_text=Mock(return_value=BRIEF)
        self.r.collector.collect=Mock(return_value=([article(1),article(2)],[]))
        with patch('pc.news_exploration.time.time',return_value=NOW),patch('pc.summarizer.article_text',return_value='正文事实。'*400):
            report=self.r.run_cycle()
        self.assertEqual([provider for provider,_ in calls],['configured','configured','ollama'])
        self.assertEqual(self.r.ai.complete_text.call_count,2)
        self.assertTrue(all(call.args[0]['news_model_provider']=='ollama' for call in self.r.ai.complete_text.call_args_list))
        self.assertTrue(report['skill_updated']);self.assertEqual(report['published'],2)
        self.assertEqual(report['stage_models']['brief']['provider'],'ollama')
        self.assertTrue(all(item['title_zh']=='本地翻译新闻标题' for item in self.repo.snapshot()['news']))

    def test_local_translation_failure_never_falls_back_to_api_or_loses_queue(self):
        self.repo.publish_recommendations([article(1)],[article(1)],NOW)
        self.r.collector.collect=Mock(return_value=([article(2)],[]))
        calls=[]
        def complete(config,system,content):
            calls.append(config['news_model_provider'])
            if calls==['configured']:return json.dumps({'selected':[{'id':article(2)['id'],'category':'politics'}]})
            raise ValueError('local service offline')
        self.r.ai.complete=Mock(side_effect=complete)
        self.r.ai.complete_text=Mock()
        before=self.repo.news_packet()
        with patch('pc.news_exploration.time.time',return_value=NOW),self.assertRaises(ValueError):self.r.run_cycle()
        self.assertEqual(calls,['configured','ollama'])
        self.r.ai.complete_text.assert_not_called()
        self.assertEqual(self.repo.news_packet(),before)
        status=json.loads((Path(self.temp.name)/'news-recommendation-status.json').read_text(encoding='utf8'))
        self.assertEqual(status['stage'],'translating')

    def test_each_stage_can_be_switched_and_brief_cache_tracks_its_model(self):
        config=self.repo.snapshot()['config']
        self.assertTrue(uses_cloud(config))
        local=model_signature(config)
        self.repo.configure(news_brief_provider='configured')
        self.assertNotEqual(local,model_signature(self.repo.snapshot()['config']))
        self.repo.configure(news_recommendation_provider='local_openai',news_local_model='unsloth-test',
                            news_learning_provider='local_openai',news_brief_provider='ollama')
        config=self.repo.snapshot()['config']
        self.assertFalse(uses_cloud(config))
        self.assertEqual(stage_config(config,'recommendation')['news_model_provider'],'local_openai')
        with self.assertRaises(ValueError):self.repo.configure(news_translation_provider='unknown')

    def test_translation_batches_use_short_ids_and_preserve_ranking(self):
        stories=[{**article(n),'recommendation_lane':'interest'} for n in range(1,15)]
        def complete(config,system,content):
            rows=json.loads(content)['headlines']
            return json.dumps({'translations':[{'id':row['id'],'title_zh':'中文标题'+str(row['id'])} for row in rows[::-1]]})
        self.r.ai.complete=Mock(side_effect=complete)
        result=self.r.translate(stories,stage_config(self.repo.snapshot()['config'],'translation'))
        self.assertEqual([i['id'] for i in result],[i['id'] for i in stories])
        self.assertEqual(self.r.ai.complete.call_count,3)
        for call in self.r.ai.complete.call_args_list:
            rows=json.loads(call.args[2])['headlines']
            self.assertEqual([row['id'] for row in rows],list(range(1,len(rows)+1)))

    def test_default_translation_uses_default_service_even_with_local_recommender(self):
        self.repo.configure(news_model_provider='configured',news_recommendation_provider='ollama',news_translation_provider='default')
        calls=[]
        def complete(config,system,content):
            calls.append(config['news_model_provider'])
            data=json.loads(content)
            if 'exploration_ids' in data:return '{"selected":[{"id":1,"category":"politics"}]}'
            return '{"translations":[{"id":1,"title_zh":"测试翻译标题"}]}'
        self.r.ai.complete=Mock(side_effect=complete)
        with patch('pc.news_exploration.time.time',return_value=NOW):result=self.r.select([article(1)],self.repo.snapshot()['config'])
        self.assertEqual(calls,['ollama','configured'])
        self.assertEqual(result[0]['id'],article(1)['id'])

    def test_local_title_alias_is_normalized_but_untranslated_text_is_rejected(self):
        self.r.ai.complete=Mock(return_value='{"translations":[{"id":1,"title":"本地中文标题"}]}')
        config=stage_config(self.repo.snapshot()['config'],'translation')
        self.assertEqual(self.r.translate([article(1)],config)[0]['title_zh'],'本地中文标题')
        self.r.ai.complete.return_value='{"translations":[{"id":1,"title":"Untranslated English title"}]}'
        with self.assertRaises(ValueError):self.r.translate([article(1)],config)

    def test_translation_checks_foreign_company_names_and_english_fragments(self):
        source='Binance tries to recover $6.3 billion as rate hike looming'
        self.assertFalse(translated_title_ok(source,'Coinbase 试图追回63亿美元'))
        self.assertFalse(translated_title_ok(source,'币安试图追回63亿美元，加息 looming'))
        self.assertTrue(translated_title_ok(source,'Binance 试图追回63亿美元'))
        self.assertTrue(translated_title_ok(source,'币安试图追回63亿美元'))


if __name__=='__main__':unittest.main()
