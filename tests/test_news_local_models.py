import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from pc.core import Repository
from pc.bridge import Bridge
from pc.news_model import NewsModelClient
from pc.news_model_config import DEFAULTS, local_base, validate, model_signature
from pc.news_recommender import NewsRecommender
from pc.secrets import ApiKeyStore
from pc.summarizer import Summarizer
from tests.test_briefs import BRIEF
from tests.test_news_learning import article, feedback, NOW


class LocalServer:
    def __enter__(self):
        self.requests=[];self.response=None
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.command=='POST' else None
                owner.requests.append((self.path,payload,dict(self.headers)))
                if owner.response is not None:result=copy.deepcopy(owner.response)
                elif self.path=='/api/tags':result={'models':[{'name':'test-local:latest'}]}
                elif self.path=='/v1/models':result={'data':[{'id':'test-local'}]}
                else:
                    system=payload['messages'][0]['content']
                    if '私人新闻兴趣' in system:text=json.dumps({'prefer':['国际科技政策'],'avoid':[],'uncertain':[]})
                    elif '新闻标题推荐' in system:
                        text=json.dumps({'selected':[{'id':1,'title_zh':'国际政策出现新变化','category':'politics'}]})
                    elif '接口测试' in system:text='{"ready":true}'
                    else:text=BRIEF
                    result={'message':{'content':text},'done':True} if self.path=='/api/chat' else {'choices':[{'message':{'content':text},'finish_reason':'stop'}]}
                raw=json.dumps(result,ensure_ascii=False).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            do_GET=reply
            do_POST=reply
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def config(self,provider='ollama'):
        return {**DEFAULTS,'news_model_provider':provider,'news_ollama_url':self.url,'news_ollama_model':'test-local:latest',
                'news_local_url':self.url+'/v1','news_local_model':'test-local'}


class LocalConfigurationTests(unittest.TestCase):
    def test_old_configuration_remains_cloud_and_local_profiles_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory);self.assertEqual(repo.snapshot()['config']['news_model_provider'],'configured')
            repo.configure(news_model_provider='ollama',news_ollama_model='my-model')
            self.assertEqual(Repository(directory).snapshot()['config']['news_ollama_model'],'my-model')
            repo.configure(news_model_provider='configured')
            self.assertEqual(repo.snapshot()['config']['news_ollama_model'],'my-model')

    def test_urls_are_normalized_and_invalid_or_public_http_urls_rejected(self):
        for url in ('http://127.0.0.1:11434','http://localhost:11434/api/chat','http://localhost:11434/api'):
            self.assertNotIn('/api',local_base({**DEFAULTS,'news_model_provider':'ollama','news_ollama_url':url}))
        self.assertEqual(local_base({**DEFAULTS,'news_local_url':'http://192.168.1.8:8000/v1/chat/completions'}),'http://192.168.1.8:8000/v1')
        for url in ('http://example.com','http://0.0.0.0:8000','http://u:secret@localhost','http://localhost:wrong','http://localhost?key=secret'):
            with self.subTest(url=url),self.assertRaises(ValueError):local_base({**DEFAULTS,'news_local_url':url})
        with self.assertRaises(ValueError):validate({**DEFAULTS,'news_model_timeout':True})
        with self.assertRaises(ValueError):validate({**DEFAULTS,'news_model_provider':'ollama'},require_model=True)

    def test_cache_and_refresh_distinguish_cloud_local_model_and_context(self):
        cloud={'llm_base_url':'https://example.org/v1','llm_model':'test'}
        local={**DEFAULTS,'news_model_provider':'ollama','news_ollama_model':'test'}
        self.assertNotEqual(model_signature(cloud),model_signature(local))
        self.assertNotEqual(model_signature(local),model_signature({**local,'news_ollama_model':'other'}))
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory);bridge=Bridge(repo,queue.Queue())
            repo.configure(news_model_provider='ollama',news_ollama_model='test');bridge.settings_changed()
            self.assertTrue(bridge.news_refresh.is_set())


class LocalProtocolTests(unittest.TestCase):
    def test_ollama_native_json_text_and_models_without_cloud_key_or_proxy(self):
        with tempfile.TemporaryDirectory() as directory,LocalServer() as server:
            cloud_keys=Mock();cloud_keys.load.side_effect=AssertionError('cloud key must not be read')
            local_keys=Mock();local_keys.load.side_effect=AssertionError('Ollama must not inherit credentials')
            client=NewsModelClient(cloud_keys,directory,local_keys=local_keys);config=server.config()
            self.assertEqual(client.models(config),['test-local:latest'])
            self.assertIn('通过',client.verify(config))
            self.assertEqual(client.complete_text(config,'中文简讯','正文'),BRIEF)
            self.assertEqual([r[0] for r in server.requests],['/api/tags','/api/chat','/api/chat'])
            self.assertEqual(server.requests[1][1]['format'],'json')
            self.assertNotIn('format',server.requests[2][1])
            self.assertFalse(server.requests[1][1]['stream'])
            self.assertEqual(server.requests[1][1]['options']['num_ctx'],16384)
            self.assertTrue(all(not any(k.lower()=='authorization' for k in r[2]) for r in server.requests))

    def test_unsloth_compatible_lists_models_and_uses_only_its_own_optional_key(self):
        with tempfile.TemporaryDirectory() as directory,LocalServer() as server:
            cloud=Mock();cloud.load.side_effect=AssertionError('cloud credential leaked')
            keys=Mock();keys.load.return_value='local-only-token'
            client=NewsModelClient(cloud,directory,local_keys=keys);config=server.config('local_openai')
            self.assertEqual(client.models(config),['test-local'])
            self.assertEqual(client.complete_text(config,'简讯','正文'),BRIEF)
            self.assertEqual(server.requests[1][0],'/v1/chat/completions')
            self.assertEqual(server.requests[1][2]['Authorization'],'Bearer local-only-token')
            self.assertNotIn('format',server.requests[1][1])

    def test_reasoning_is_removed_but_empty_or_truncated_answers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory,LocalServer() as server:
            client=NewsModelClient(Mock(),directory);config=server.config()
            server.response={'message':{'content':'<think>internal reasoning</think>'+BRIEF},'done':True}
            self.assertEqual(client.complete_text(config,'s','u'),BRIEF)
            server.response={'message':{'content':'<think>internal only</think>'},'done':True}
            with self.assertRaises(ValueError):client.complete_text(config,'s','u')
            server.response={'message':{'content':BRIEF},'done_reason':'length'}
            with self.assertRaises(ValueError):client.complete_text(config,'s','u')

    def test_connection_failure_does_not_fall_back_to_cloud(self):
        with tempfile.TemporaryDirectory() as directory,LocalServer() as server:
            client=NewsModelClient(Mock(),directory);config=server.config()
            client.cloud.complete=Mock(side_effect=AssertionError('unexpected paid request'))
            from urllib.error import URLError
            with patch('pc.news_model.build_opener') as opener:
                opener.return_value.open.side_effect=URLError(ConnectionRefusedError())
                with self.assertRaisesRegex(ValueError,'无法连接'):client.complete(config,'s','u')
            client.cloud.complete.assert_not_called()

    def test_real_pipeline_uses_local_model_for_learning_filtering_and_article_brief(self):
        for provider in ('ollama','local_openai'):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as directory,LocalServer() as server:
                repo=Repository(directory);repo.configure(**server.config(provider),news_personalized=True,news_limit=2)
                recommender=NewsRecommender(repo)
                for seq in range(1,21):recommender.preferences.record(feedback(seq,'opened',seq),'local-test')
                recommender.collector.collect=Mock(return_value=([article(1),article(2)],[]))
                with patch('pc.summarizer.article_text',return_value=BRIEF*20) as body,patch('pc.news_exploration.time.time',return_value=NOW):
                    report=recommender.run_cycle()
                    self.assertEqual(body.call_count,1);self.assertEqual(body.call_args.args[0],article(1)['url'])
                self.assertTrue(report['skill_updated']);self.assertEqual(report['published'],1)
                self.assertEqual(report['model_provider'],provider)
                self.assertEqual(repo.news_packet()['items'][0]['id'],article(1)['id'])
                self.assertEqual(repo.news_packet()['items'][0]['summary'],BRIEF)
                filter_payload=json.loads(server.requests[1][1]['messages'][1]['content'])
                self.assertEqual(filter_payload['headlines'][0]['id'],1)
                self.assertNotIn('body',filter_payload['headlines'][0])
                self.assertEqual(recommender.preferences.learning_count(),0)

    def test_legacy_brief_also_accepts_local_provider_without_cloud_settings(self):
        with tempfile.TemporaryDirectory() as directory,LocalServer() as server:
            with patch('pc.summarizer.article_text',return_value=BRIEF*20):
                value,basis=Summarizer(ApiKeyStore(directory)).summarize(article(1),server.config(),require_article=True)
            self.assertEqual((value,basis),(BRIEF,'article'))

    def test_local_length_repair_receives_actual_count_and_previous_draft(self):
        client=Mock();client.complete_text.side_effect=['中'*40,BRIEF]
        config={**DEFAULTS,'news_model_provider':'ollama','news_ollama_model':'test'}
        with patch('pc.summarizer.article_text',return_value=BRIEF*20):
            value,basis=Summarizer(Mock(),client=client).summarize(article(1),config,require_article=True)
        self.assertEqual((value,basis),(BRIEF,'article'))
        repair=json.loads(client.complete_text.call_args.args[2])
        self.assertIn('40',repair['revision_requirement']);self.assertEqual(repair['previous_draft'],'中'*40)


if __name__=='__main__':unittest.main()
