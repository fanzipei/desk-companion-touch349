# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""News model adapter: existing API, native Ollama, and local OpenAI servers."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

from .ai import AiClient
from .secrets import ApiKeyStore
from .summarizer import NoApiRedirect, api_response_error
from .news_model_config import is_local, local_base, model_name, validate


class NewsModelClient:
    def __init__(self, keys, directory, local_keys=None):
        self.cloud=AiClient(keys,directory,timeout=120,retries=1)
        self.local_keys=local_keys or ApiKeyStore(directory,"news-local-api-key.dpapi")

    def _request(self, config, suffix, payload=None, timeout=None):
        provider=config["news_model_provider"]
        key=self.local_keys.load() if provider=="local_openai" else ""
        headers={"Content-Type":"application/json"}
        if key:headers["Authorization"]="Bearer "+key
        data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
        request=Request(local_base(config)+suffix,data=data,headers=headers)
        try:
            # Local inference never passes through the system/news HTTP proxy.
            with build_opener(NoApiRedirect(),ProxyHandler({})).open(request,
                    timeout=timeout or config.get("news_model_timeout",300)) as response:
                raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError("本地模型返回内容过大。")
            value=json.loads(raw)
            if isinstance(value,dict) and value.get("error"):
                detail=str(value["error"])
                if key:detail=detail.replace(key,"[密钥已隐藏]")
                raise ValueError("本地模型请求失败："+detail[:240])
            return value
        except HTTPError as exc:
            raise api_response_error(exc,key) from None
        except TimeoutError:
            raise ValueError("本地模型响应超时，请增加模型等待时间或选择更小的模型。") from None
        except URLError as exc:
            if isinstance(exc.reason,TimeoutError):raise ValueError("本地模型响应超时，请增加模型等待时间。") from None
            raise ValueError("无法连接本地模型服务，请确认服务已启动、地址和端口正确。") from None
        except json.JSONDecodeError:
            raise ValueError("本地地址未返回模型 JSON，请填写推理 API 地址，勿填写聊天网页地址。") from None

    def complete(self, config, system, content):
        return self._complete(config,system,content,json_mode=True)

    def complete_text(self, config, system, content):
        return self._complete(config,system,content,json_mode=False)

    def _complete(self, config, system, content, json_mode):
        if not is_local(config):return self.cloud.complete(config,system,content)
        validate(config,require_model=True)
        payload={"model":model_name(config),"messages":[{"role":"system","content":system},{"role":"user","content":content}],"stream":False}
        ollama=config["news_model_provider"]=="ollama"
        if ollama:
            payload["options"]={"num_ctx":config.get("news_ollama_context",16384),"num_predict":4096}
            if json_mode:payload["format"]="json"
        result=self._request(config,"/api/chat" if ollama else "/chat/completions",payload)
        try:
            item=result if ollama else result["choices"][0]
            if ollama and item.get("done") is False:raise ValueError("本地模型尚未完成输出，请检查服务的非流式接口。")
            if item.get("done_reason" if ollama else "finish_reason")=="length":
                raise ValueError("本地模型输出被截断，请调整上下文或输出长度。")
            text=item["message"]["content"]
            if not isinstance(text,str):raise TypeError()
            text=re.sub(r"^\s*<think>.*?</think>\s*","",text,flags=re.S).strip()
            if not text:raise ValueError("本地模型未返回正文，可能仍在思考或输出被截断。")
            return text
        except (KeyError,IndexError,TypeError):
            raise ValueError("本地模型返回格式不兼容。") from None

    def models(self, config):
        if not is_local(config):raise ValueError("读取列表仅用于 Ollama 或本地兼容服务。")
        validate(config)
        ollama=config["news_model_provider"]=="ollama"
        result=self._request(config,"/api/tags" if ollama else "/models",timeout=10)
        try:
            rows=result["models" if ollama else "data"]
            if not isinstance(rows,list):raise TypeError()
            names=sorted({row["name" if ollama else "id"] for row in rows})
            if any(not isinstance(name,str) or not name.strip() for name in names):raise TypeError()
            return names
        except (KeyError,TypeError):raise ValueError("服务未返回有效模型列表，请检查 API 地址。") from None

    def verify(self, config):
        # No images or real reading history are needed for this news-only test.
        text=self.complete(config,"这是新闻模型接口测试。严格遵循要求，只输出JSON对象。",'只返回以下JSON，不加其他字段或解释：{"ready":true}')
        from .news_preferences import model_json
        value=model_json(text)
        if not isinstance(value,dict) or set(value)!={"ready"} or value["ready"] is not True:
            raise ValueError("模型未通过新闻 JSON 输出测试，请选择支持中文指令的模型。")
        return "新闻模型连接与 JSON 输出测试通过。"
