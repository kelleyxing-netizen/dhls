"""员工问答可复用核心：配置、本地检索、历史拼装及真实大模型流。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from stage1_rag import (CHUNK_SIZE, DEFAULT_EMBEDDING_MODEL, LLMConfig, LocalEmbedder,
                        SearchHit, build_prompt, ensure_example_document, hybrid_search,
                        load_or_build_index, redact_for_llm, resolve_llm_config,
                        split_document, tokenize_chinese, validate_answer_sources)


class ChatError(Exception):
    """统一业务错误，只携带允许发给前端的中文说明，不包含供应商原始响应。"""
    def __init__(self, code: str, message: str, status: int = 500, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


@dataclass
class Settings:
    """配置仅来自环境或显式注入；密钥不写入SQLite、网页及日志。"""
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent / ".chat_data")
    rag_dir: Path = field(default_factory=lambda: Path(__file__).parent / ".stage1_rag")
    document: Path | None = None
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    offline: bool = False
    demo_mode: bool = True
    timeout: float = 60.0
    generation_timeout: float = 180.0
    heartbeat_seconds: float = 10.0
    llm_config: LLMConfig | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        result = cls(
            data_dir=Path(os.getenv("CHAT_DATA_DIR", str(Path(__file__).parent / ".chat_data"))),
            rag_dir=Path(os.getenv("RAG_WORK_DIR", str(Path(__file__).parent / ".stage1_rag"))),
            document=Path(os.environ["RAG_DOCUMENT"]) if os.getenv("RAG_DOCUMENT") else None,
            embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            offline=os.getenv("EMBEDDING_OFFLINE", "0") == "1",
            demo_mode=os.getenv("CHAT_DEMO_MODE", "1") == "1",
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
            generation_timeout=float(os.getenv("CHAT_GENERATION_TIMEOUT", "180")),
        )
        return result

    def validate(self) -> None:
        for value in (self.timeout, self.generation_timeout, self.heartbeat_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("超时和心跳参数必须是有限正数。")
        self.data_dir, self.rag_dir = self.data_dir.resolve(), self.rag_dir.resolve()

    def model_config(self) -> LLMConfig:
        if self.llm_config is not None:
            return self.llm_config
        try:
            return resolve_llm_config(argparse.Namespace(
                provider=os.getenv("LLM_PROVIDER", "deepseek"), base_url=None,
                model=None, api_key_env=None, timeout=self.timeout,
            ))
        except ValueError as error:
            raise ChatError("MODEL_NOT_CONFIGURED", "模型配置不正确，请联系管理员。", 503) from error
        except Exception as error:
            raise ChatError("MODEL_NOT_CONFIGURED", "问答模型尚未配置，请联系管理员。", 503) from error


class KnowledgeBase:
    """直接复用阶段1的500字切片、本地Embedding、FAISS、BM25和RRF。"""
    def __init__(self, settings: Settings):
        from rank_bm25 import BM25Okapi

        document = ensure_example_document(settings.rag_dir, settings.document)
        text = document.read_text(encoding="utf-8-sig")
        self.document_version = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.chunks = [c for c in split_document(text, document.name) if c.text.strip()]
        corpus = [tokenize_chinese(c.text) for c in self.chunks]
        if not any(corpus):
            raise ValueError("制度没有可检索内容。")
        self.bm25 = BM25Okapi(corpus)
        self.embedder = LocalEmbedder(settings.embedding_model, settings.rag_dir / "models", settings.offline)
        self.index = load_or_build_index(self.chunks, self.embedder, settings.rag_dir, {
            "source": str(document), "source_sha256": self.document_version,
            "chunk_size": CHUNK_SIZE, "embedding_model": settings.embedding_model,
        })
        self._lock = threading.Lock()

    def search(self, question: str) -> list[SearchHit]:
        # asyncio取消不能强行终止CPU线程；锁在工作线程内部，保证旧线程结束前不会
        # 有另一个请求同时调用同一本地推理实例。
        with self._lock:
            return hybrid_search(question, self.chunks, self.bm25, self.index, self.embedder)


def build_chat_messages(question: str, hits: list[SearchHit], history: list[dict]) -> list[dict]:
    """最近6个完整问答对按原顺序加入；历史不是本次制度证据，全部内容统一脱敏。"""
    base = build_prompt(question, hits)
    base[0]["content"] += "历史对话仅用于理解上下文，当前sources才是本次引用依据；历史片段编号不沿用。"
    previous = []
    for message in history:
        content = re.sub(r"\[S\d+\]", "", message["content"])
        previous.append({"role": message["role"], "content": redact_for_llm(content)})
    return [base[0], *previous, base[1]]


def answer_sources(answer: str, hits: list[SearchHit], version: str = "") -> list[dict]:
    """先校验，再从真实检索结果取实际引用；原文和版本快照随回答持久保存。"""
    try:
        validate_answer_sources(answer, hits)
    except Exception as error:
        raise ChatError("CITATION_INVALID", "回答来源校验未通过，请重新提问。", 502, True) from error
    cited = set(re.findall(r"\[(S\d+)\]", answer))
    return [{
        "source_id": hit.chunk.chunk_id, "filename": Path(hit.chunk.source).name,
        "chunk_id": hit.chunk.chunk_id, "start_line": hit.chunk.start_line,
        "end_line": hit.chunk.end_line, "excerpt": hit.chunk.text,
        "document_version": version or hashlib.sha256(hit.chunk.text.encode()).hexdigest(),
    } for hit in hits if hit.chunk.chunk_id in cited]


async def stream_llm(messages: list[dict], config: LLMConfig,
                     client: httpx.AsyncClient) -> AsyncIterator[str]:
    """独立的真实流式调用：解析上游SSE，只转发正文，不泄露推理过程或错误原文。

    已发出正文后不自动重试，以免把两个不同回答拼到一起。正常结束必须同时看到
    finish_reason=stop 和 [DONE]；截断、工具调用、坏JSON均不当作成功。
    """
    payload: dict[str, Any] = {"model": config.model, "messages": messages,
                              "temperature": 0.1, "max_tokens": 1200, "stream": True}
    if config.provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    finished, received_done = False, False
    try:
        async with client.stream("POST", config.base_url + "/chat/completions", json=payload,
                                 headers=headers, timeout=config.timeout) as response:
            if response.status_code == 429:
                raise ChatError("LLM_RATE_LIMITED", "问答服务繁忙，请稍后重试。", 429, True)
            if response.status_code != 200:
                raise ChatError("LLM_UPSTREAM_ERROR", "模型服务暂时异常，请稍后重试。", 502, True)
            if "text/event-stream" not in response.headers.get("content-type", "").lower():
                raise ChatError("LLM_PROTOCOL_ERROR", "模型服务未返回可用的流式回答。", 502, True)
            # aiter_lines负责UTF-8拆包；按SSE空行分帧，支持注释与多行data。
            lines: list[str] = []
            async for line in response.aiter_lines():
                if line:
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip(" "))
                    continue
                if not lines:
                    continue
                raw, lines = "\n".join(lines), []
                if raw == "[DONE]":
                    received_done = True
                    break
                try:
                    item = json.loads(raw)
                    if not isinstance(item, dict) or item.get("error"):
                        raise ValueError("无效模型事件")
                    choices = item.get("choices")
                    if choices == [] and "usage" in item:
                        continue
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        raise ValueError("缺少choices")
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    if not isinstance(delta, dict) or delta.get("tool_calls"):
                        raise ValueError("不是正文回答")
                    text = delta.get("content")
                    if text is not None:
                        if not isinstance(text, str) or finished:
                            raise ValueError("正文格式或顺序无效")
                        if text:
                            yield text
                    reason = choice.get("finish_reason")
                    if reason is not None:
                        if reason != "stop":
                            raise ChatError("LLM_INCOMPLETE", "模型回答未完整生成，请重新提问。", 502, True)
                        finished = True
                except (ValueError, TypeError, KeyError) as error:
                    raise ChatError("LLM_PROTOCOL_ERROR", "模型流式响应格式异常，请稍后重试。", 502, True) from error
            if not received_done or not finished:
                raise ChatError("LLM_INCOMPLETE", "模型连接提前结束，回答未完成。", 502, True)
    except httpx.TimeoutException as error:
        raise ChatError("LLM_TIMEOUT", "回答生成超时，请稍后重试。", 504, True) from error
    except (httpx.RequestError, httpx.InvalidURL) as error:
        raise ChatError("LLM_UPSTREAM_ERROR", "模型连接失败，请稍后重试。", 502, True) from error
