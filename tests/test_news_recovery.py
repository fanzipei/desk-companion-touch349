import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from pc.ai import AiClient
from pc.core import Repository
from pc.news_recommender import NewsRecommender, news_status_text
from pc.summarizer import ApiResponseError


class NewsApiRecoveryTests(unittest.TestCase):
    config = {"llm_model":"test", "llm_base_url":"https://example.org/v1"}

    def client(self, directory):
        keys=Mock(); keys.load.return_value="test-private-key"
        return AiClient(keys,directory,timeout=120,retries=1)

    def response(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({
            "choices":[{"finish_reason":"stop","message":{"content":"READY"}}]}).encode()
        return response

    def test_slow_selection_can_retry_once_without_changing_request_or_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch("pc.ai.build_opener") as opener, patch("pc.ai.time.sleep"):
            opener.return_value.open.side_effect=[TimeoutError(),self.response()]
            self.assertEqual(self.client(directory).complete(self.config,"筛选标题","实际标题"),"READY")
            calls=opener.return_value.open.call_args_list
            self.assertEqual(len(calls),2)
            self.assertTrue(all(call.kwargs["timeout"]==120 for call in calls))
            self.assertEqual(calls[0].args[0].data,calls[1].args[0].data)

    def test_bad_request_is_not_retried_and_provider_reason_redacts_key(self):
        raw=json.dumps({"error":{"message":"invalid model; test-private-key sk-examplesecret"}}).encode()
        with tempfile.TemporaryDirectory() as directory, patch("pc.ai.build_opener") as opener:
            opener.return_value.open.side_effect=HTTPError("https://example.org/v1",400,"bad request",{},io.BytesIO(raw))
            with self.assertRaises(ApiResponseError) as caught:
                self.client(directory).complete(self.config,"system","headlines")
            self.assertEqual(caught.exception.status,400)
            self.assertIn("invalid model",str(caught.exception))
            self.assertNotIn("test-private-key",str(caught.exception))
            self.assertNotIn("sk-examplesecret",str(caught.exception))
            self.assertEqual(opener.return_value.open.call_count,1)

    def test_transient_provider_error_retries_but_stops_after_second_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch("pc.ai.build_opener") as opener, patch("pc.ai.time.sleep"):
            opener.return_value.open.side_effect=[
                HTTPError("https://example.org/v1",503,"unavailable",{},io.BytesIO(b"{}")),TimeoutError()]
            with self.assertRaises(TimeoutError):self.client(directory).complete(self.config,"system","headlines")
            self.assertEqual(opener.return_value.open.call_count,2)

    def test_failed_cycle_records_actual_stage_and_preserves_last_success_and_news(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=Repository(root)
            repo.configure(news_personalized=True,**self.config)
            recommender=NewsRecommender(repo)
            recommender._status(updated=100,last_success_at=100,published=4)
            recommender.collector.collect=Mock(return_value=([{"id":123,"url":"https://example.org/story"}],[]))
            recommender.select=Mock(side_effect=TimeoutError("接口响应超时"))
            previous=repo.snapshot()["news"]
            with self.assertRaises(TimeoutError):recommender.run_cycle()
            report=json.loads((root/"news-recommendation-status.json").read_text(encoding='utf8'))
            self.assertEqual(report["state"],"error");self.assertEqual(report["stage"],"selecting")
            self.assertEqual(report["updated"],100);self.assertEqual(report["published"],4)
            self.assertEqual(repo.snapshot()["news"],previous)
            self.assertIn("筛选标题失败",news_status_text(root))
            self.assertIn("接口响应超时",news_status_text(root))


if __name__=="__main__":unittest.main()
