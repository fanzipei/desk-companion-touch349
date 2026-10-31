# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""News-only model settings; independent of screenshot credentials."""
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit

PROVIDERS = {"configured":"现有模型 API", "ollama":"Ollama", "local_openai":"Unsloth / 本地兼容服务"}
STAGES = {"recommendation":"新闻推荐", "translation":"标题翻译", "brief":"正文简讯", "learning":"偏好学习"}
STAGE_PROVIDERS = {"default":"沿用默认新闻模型", **PROVIDERS}
STAGE_KEYS = tuple(f"news_{stage}_provider" for stage in STAGES)
DEFAULTS = {
    **{key:"default" for key in STAGE_KEYS},
    "news_model_provider":"configured",
    "news_ollama_url":"http://127.0.0.1:11434",
    "news_ollama_model":"",
    "news_local_url":"http://127.0.0.1:8001/v1",
    "news_local_model":"",
    "news_model_timeout":300,
    "news_ollama_context":16384,
}
MODEL_KEYS = ("llm_base_url", "llm_model", *DEFAULTS)


def is_local(config):
    return config.get("news_model_provider", "configured") != "configured"


def stage_config(config, stage):
    provider = config.get(f"news_{stage}_provider", "default")
    return dict(config) if provider == 'default' else {**config, "news_model_provider":provider}


def validate_routes(config, require_model=False):
    validate(config)
    for stage in STAGES:
        provider = config.get(f"news_{stage}_provider", "default")
        if provider not in STAGE_PROVIDERS:
            raise ValueError(f"请选择有效的{STAGES[stage]}模型。")
        validate(stage_config(config, stage), require_model=require_model)


def uses_cloud(config):
    stages = STAGES if config.get('news_personalized') else ('brief',)
    return any(not is_local(stage_config(config, stage)) for stage in stages)


def local_base(config):
    provider = config.get("news_model_provider", "configured")
    key = "news_ollama_url" if provider == "ollama" else "news_local_url"
    value = config.get(key, DEFAULTS[key]).strip().rstrip("/")
    p = urlsplit(value)
    try:
        port = p.port
        private = p.hostname == "localhost" or (ipaddress.ip_address(p.hostname).is_private
            and not ipaddress.ip_address(p.hostname).is_unspecified
            and not ipaddress.ip_address(p.hostname).is_multicast)
    except ValueError:
        private = p.hostname == "localhost"
        port = None
        try:p.port
        except ValueError:raise ValueError("本地模型端口无效。") from None
    if not p.hostname or p.username or p.password or p.query or p.fragment or any(c.isspace() for c in value):
        raise ValueError("本地模型地址不能包含账号、密码、查询参数或空格。")
    if p.scheme not in ("http", "https") or (p.scheme == "http" and not private):
        raise ValueError("本地模型请使用 localhost、局域网 IP，或 HTTPS 地址。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("本地模型端口无效。")
    if provider == "ollama":
        for suffix in ("/api/chat", "/api/tags", "/api"):
            if value.endswith(suffix):return value[:-len(suffix)]
        return value
    if value.endswith("/chat/completions"):value=value[:-len("/chat/completions")]
    if value.endswith("/models"):value=value[:-len("/models")]
    return value if urlsplit(value).path else value+"/v1"


def model_name(config):
    provider = config.get("news_model_provider", "configured")
    key = {"configured":"llm_model", "ollama":"news_ollama_model", "local_openai":"news_local_model"}[provider]
    return config.get(key, "").strip()


def validate(config, require_model=False):
    provider = config.get("news_model_provider", "configured")
    if provider not in PROVIDERS:raise ValueError("请选择有效的新闻模型来源。")
    for key, low, high in (("news_model_timeout",30,1800),("news_ollama_context",2048,131072)):
        value=config.get(key,DEFAULTS[key])
        if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
            raise ValueError(f"{'模型等待时间' if key=='news_model_timeout' else 'Ollama 上下文'}应为 {low}–{high} 的整数。")
    if is_local(config):
        local_base(config)
        if require_model and not model_name(config):raise ValueError("请填写或读取本地模型名称。")


def model_signature(config):
    config = stage_config(config, 'brief')
    if not is_local(config):
        # Retain signatures of existing cloud briefs on upgrade.
        value=[config.get("llm_base_url", "").rstrip("/"),config.get("llm_model", ""),"zh-60-120-v2-facts"]
    else:
        value=[config.get("news_model_provider"),local_base(config),model_name(config),
               config.get("news_ollama_context",16384),"zh-60-120-v2-facts"]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:20]


def settings_signature(config):
    return tuple(config.get(key,DEFAULTS.get(key)) for key in MODEL_KEYS)
