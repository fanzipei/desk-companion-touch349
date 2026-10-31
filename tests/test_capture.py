# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import base64
from datetime import datetime,timedelta
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from pc.ai import AiClient
from pc.capture import RegionCapture
from pc.core import Repository
from pc.summarizer import article_text,facts_only


class CaptureTests(unittest.TestCase):
    def test_only_selected_region_is_encoded(self):
        capture=RegionCapture.__new__(RegionCapture)
        capture.width=capture.height=40
        capture.image=Image.new("RGB",(40,40),"red")
        capture.image.paste("green",(10,10,30,30))
        capture.start=(30,30)
        capture.window=Mock()
        capture.on_image=Mock(); capture.on_close=Mock()
        capture.finish(SimpleNamespace(x=10,y=10))
        selected=capture.on_image.call_args.args[0]
        data=AiClient.image_content(selected,"read")[1]["image_url"]["url"].split(",",1)[1]
        image=Image.open(io.BytesIO(base64.b64decode(data)))
        self.assertEqual(image.size,(20,20))
        self.assertEqual(image.getextrema(),((0,0),(128,128),(0,0)))
        self.assertIsNone(capture.image)

    def test_cancel_and_tiny_region_never_submit(self):
        capture=RegionCapture.__new__(RegionCapture)
        capture.width=capture.height=40; capture.start=(10,10)
        capture.image=Image.new("RGB",(40,40)); capture.window=Mock()
        capture.on_image=Mock(); capture.on_close=Mock()
        capture.finish(SimpleNamespace(x=11,y=11))
        capture.on_image.assert_not_called()
        capture.on_close.assert_called_once()

    def test_vision_validation_tracks_model_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            keys=SimpleNamespace(load=lambda:"key1")
            ai=AiClient(keys,directory)
            config={"llm_base_url":"https://example.com/v1","llm_model":"model1"}
            self.assertFalse(ai.verified(config))
            ai.path.write_text(json.dumps({"signature":ai.signature(config),"text":True,"vision":True}))
            self.assertTrue(ai.verified(config))
            self.assertFalse(ai.verified({**config,"llm_model":"model2"}))
            keys.load=lambda:"key2"
            self.assertFalse(ai.verified(config))
            with patch.object(ai,"complete") as call:
                with self.assertRaises(ValueError):
                    ai.screenshot_note(config,Image.new("RGB",(40,40)))
                call.assert_not_called()




class NewsFactsTests(unittest.TestCase):
    def test_no_fetching_disclaimers(self):
        for text in ("因正文无法获取，以上仅根据标题整理。","由于无法获取新闻正文，只能概述。"):
            self.assertFalse(facts_only(text))
        self.assertTrue(facts_only("研究团队发布了新的机器人控制方法。"))

    def test_article_retries_direct_after_system_proxy_failure(self):
        with patch("pc.summarizer._article_text",side_effect=[OSError(),"article"]) as fetch:
            self.assertEqual(article_text("https://example.com"),"article")
            self.assertEqual([call.args[1] for call in fetch.call_args_list],["system","direct"])
