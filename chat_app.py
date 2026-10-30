"""员工聊天与管理后台入口：FastAPI＋SQLite＋JWT，默认仅在本机运行。

启动：python chat_app.py
依赖：python -m pip install -r requirements.txt
模型密钥从环境变量读取，沿用阶段1的DEEPSEEK_API_KEY/LLM_*配置。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from chat_core import ChatError, KnowledgeBase, Settings, answer_sources, build_chat_messages, stream_llm
from chat_store import Store
from chat_auth import AuthService, COOKIE_NAME, current_user, public_user, require_admin

logger = logging.getLogger("venue_chat")
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def envelope(code: str = "OK", message: str = "成功", data: Any = None, request_id: str = "") -> dict:
    """JSON和SSE使用同一信封，所有错误均有稳定错误码和中文说明。"""
    return {"code": code, "message": message, "request_id": request_id, "data": data}


def sse(event: str, request_id: str, data: Any, message: str = "成功", code: str = "OK") -> str:
    # JSON序列化会转义正文换行，保证一帧一个data行；帧间以空行分隔。
    return f"event: {event}\ndata: {json.dumps(envelope(code, message, data, request_id), ensure_ascii=False)}\n\n"


class ErrorBoundary:
    """纯ASGI中间件，不缓冲响应体，避免破坏流式传输及断线取消。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [*message.get("headers", []),
                    (b"x-request-id", request_id.encode()), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"), (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        except Exception as error:
            # 不把原始异常、请求体、认证头或供应商响应打印到日志。
            logger.error("请求失败 request_id=%s type=%s", request_id, type(error).__name__)
            if not started:
                response = JSONResponse(envelope("INTERNAL_ERROR", "服务暂时异常，请稍后重试。",
                                                  {"retryable": True}, request_id), status_code=500)
                await response(scope, receive, safe_send)


class InstanceLock:
    """SQLite原型限定单个服务进程，防止另一实例的启动恢复误伤正在生成的消息。

    使用操作系统文件锁，进程崩溃会自动释放，不依赖永久残留的PID文件。
    """
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("该数据目录已有聊天服务运行，请关闭重复实例；本原型仅支持单进程。") from None

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class ManagedStreamingResponse(StreamingResponse):
    """主动监听断线并取消上游；即使生成器还没开始，也会清理已分配的问答状态。"""
    def __init__(self, iterator, cleanup):
        super().__init__(iterator, media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        async def disconnected():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        sending = asyncio.create_task(self.stream_response(send))
        watching = asyncio.create_task(disconnected())
        try:
            finished, _ = await asyncio.wait({sending, watching}, return_when=asyncio.FIRST_COMPLETED)
            if sending in finished:
                await sending
        except (OSError, ConnectionError):
            pass
        finally:
            # 先终止仍在等待检索/模型的任务，再保存可见残文；完成消息不会被中断覆盖。
            for task in (sending, watching):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sending, watching, return_exceptions=True)
            with suppress(Exception):
                await self.body_iterator.aclose()
            cleanup_task = asyncio.create_task(self.cleanup())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task


async def heartbeat_stream(iterator, interval: float):
    """单个生产任务持续消费生成器，队列负责背压，等待期间向客户端发心跳。

    不能每次anext都创建新任务：生成器中的超时/HTTP上下文必须始终由同一任务
    进入和退出，否则总超时会绑定到已经结束的任务。队列有上限，防止慢客户端耗内存。
    """
    queue = asyncio.Queue(maxsize=32)

    async def produce():
        try:
            async for frame in iterator:
                await queue.put(frame)
        finally:
            await iterator.aclose()

    producer = asyncio.create_task(produce())
    pending = None
    try:
        while True:
            if not queue.empty():
                yield queue.get_nowait()
                continue
            if producer.done():
                producer.result()
                break
            pending = asyncio.create_task(queue.get())
            completed, _ = await asyncio.wait({pending, producer}, timeout=interval,
                                               return_when=asyncio.FIRST_COMPLETED)
            if pending in completed or pending.done():
                frame = pending.result()
                pending = None
                yield frame
            else:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                pending = None
                if producer.done():
                    continue
                yield ": ping\n\n"
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await iterator.aclose()


class LoginInput(BaseModel):
    """登录只接受JSON，限制字段及长度；响应不返回密码和JWT正文。"""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        return value.strip() or None if value is not None else None


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question", mode="before")
    @classmethod
    def clean_question(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


def create_app(settings: Settings | None = None, retriever=None, llm_stream=None, agent_decide=None) -> FastAPI:
    """依赖可注入，用同一真实API流程做无网络确定性测试，而非另写一套演示后端。"""
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.data_dir / "chat.sqlite3")
    instance_lock = InstanceLock(settings.data_dir / "server.lock")

    @asynccontextmanager
    async def lifespan(app):
        instance_lock.acquire()
        try:
            await store.initialize()
            app.state.auth = AuthService(store, settings)
            app.state.retriever, app.state.document_version = retriever, ""
            if retriever is None:
                try:
                    kb = await asyncio.to_thread(KnowledgeBase, settings)
                    app.state.retriever = kb.search
                    app.state.document_version = kb.document_version
                    app.state.knowledge_is_demo = kb.is_sample_policy
                except Exception as error:
                    logger.error("制度索引初始化失败 type=%s", type(error).__name__)
            async with httpx.AsyncClient(follow_redirects=False) as client:
                app.state.llm_client = client
                # 【可复用核心：Agent生命周期】业务表与断点均本地持久化，不启动CLI子进程。
                from agent_business import AgentBusiness
                from agent_loop import AgentHarness
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                from agent_state import AgentStateStore
                async with AsyncExitStack() as stack:
                    checkpoint = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "agent-checkpoints.sqlite3")))
                    await checkpoint.setup()
                    await AgentStateStore(store).initialize()
                    business = AgentBusiness(store, app.state.retriever, app.state.document_version, app.state.knowledge_is_demo)
                    await business.initialize()
                    app.state.agent = AgentHarness(store, business, settings, client, checkpoint, agent_decide)
                    yield
        finally:
            instance_lock.release()

    app = FastAPI(title="场馆制度问答与管理后台", version="0.9.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.settings, app.state.store = settings, store
    app.state.retriever, app.state.document_version = retriever, ""
    app.state.knowledge_is_demo = False
    app.state.llm_stream = llm_stream or stream_llm
    app.add_middleware(ErrorBoundary)

    @app.exception_handler(ChatError)
    async def chat_error(request, error):
        # 页面导航跳转登录；API始终保留HTTP错误码与统一中文JSON信封。
        if error.status == 401 and request.method == "GET" and (
            request.url.path in {"/", "/agent", "/admin", "/admin/knowledge", "/admin/prompts", "/admin/conversations"}
        ):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse(envelope(error.code, error.message, {"retryable": error.retryable},
                                      request.state.request_id), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(envelope("INVALID_REQUEST", "请求参数不正确，请检查必填项、格式和长度。",
                                      {"retryable": False}, request.state.request_id), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        messages = {404: ("NOT_FOUND", "请求的接口或资源不存在。"),
                    405: ("METHOD_NOT_ALLOWED", "该接口不支持此请求方式。")}
        code, message = messages.get(error.status_code, ("HTTP_ERROR", "请求未能完成，请检查后重试。"))
        return JSONResponse(envelope(code, message, {"retryable": False}, request.state.request_id),
                            status_code=error.status_code, headers=error.headers)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, error):
        return JSONResponse(envelope("STORAGE_ERROR", "消息保存或读取失败，请稍后重试。",
                                      {"retryable": True}, request.state.request_id), status_code=500)

    @app.get("/")
    async def page(user=Depends(current_user)):
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/login")
    async def login_page(request: Request):
        # 已登录的访问者直接进入其工作入口，不在模板中暴露JWT。
        try:
            user = await app.state.auth.authenticate(request)
        except ChatError:
            return templates.TemplateResponse(request=request, name="login.html", context={})
        return RedirectResponse("/admin/knowledge" if user["role"] == "admin" else "/", status_code=303)

    @app.post("/auth/login")
    async def login(body: LoginInput, request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ChatError("INVALID_REQUEST", "登录请求须使用JSON格式。", 415)
        token, user = await app.state.auth.login(request, body.username, body.password)
        response = JSONResponse(envelope(message="登录成功", data=user, request_id=request.state.request_id))
        response.set_cookie(COOKIE_NAME, token, max_age=settings.auth_ttl_minutes * 60,
                            httponly=True, secure=settings.auth_cookie_secure, samesite="strict", path="/")
        return response

    @app.get("/auth/me")
    async def me(request: Request, user=Depends(current_user)):
        return envelope(data={**public_user(user), "csrf_token": user["csrf_token"]}, request_id=request.state.request_id)

    @app.post("/auth/logout")
    async def logout(request: Request, user=Depends(current_user)):
        await app.state.auth.logout(user)
        response = JSONResponse(envelope(message="已退出登录", request_id=request.state.request_id))
        response.delete_cookie(COOKIE_NAME, path="/", secure=settings.auth_cookie_secure,
                               httponly=True, samesite="strict")
        return response

    @app.get("/admin")
    async def admin_root(user=Depends(require_admin)):
        return RedirectResponse("/admin/knowledge", status_code=303)

    # 【可复用核心：后台路由保护】本批只提供入口骨架，后续批次接入真实管理操作。
    @app.get("/admin/knowledge")
    @app.get("/admin/prompts")
    @app.get("/admin/conversations")
    async def admin_page(request: Request, user=Depends(require_admin)):
        return templates.TemplateResponse(request=request, name="admin.html", context={
            "user": public_user(user), "csrf_token": user["csrf_token"],
            "active_page": request.url.path.rsplit("/", 1)[-1]})

    @app.get("/admin/api/status")
    async def admin_status(request: Request, user=Depends(require_admin)):
        # 仅用于确认当前管理入口权限；没有暴露其他员工数据或未实现的虚构统计。
        return envelope(data={"role": user["role"], "department_id": user["department_id"],
                              "venue_id": user["venue_id"], "implemented_batch": 1},
                        request_id=request.state.request_id)

    @app.get("/config")
    async def config(request: Request, user=Depends(current_user)):
        try:
            settings.model_config()
            model_ready = True
        except ChatError:
            model_ready = False
        return envelope(data={"demo_mode": False, "knowledge_is_demo": app.state.knowledge_is_demo,
            "knowledge_ready": app.state.retriever is not None,
            "model_ready": model_ready, "history_rounds": 6, "max_question_length": 2000,
            "csrf_token": user.get("csrf_token", ""), "user": public_user(user)}, request_id=request.state.request_id)

    @app.post("/sessions", status_code=201)
    async def new_session(body: SessionInput, request: Request, user=Depends(current_user)):
        return envelope(message="会话已创建", data=await store.create_session(user, body.title),
                        request_id=request.state.request_id)

    @app.get("/sessions")
    async def sessions(request: Request, limit: int = Query(default=30, ge=1, le=100), user=Depends(current_user)):
        return envelope(data=await store.list_sessions(user["id"], limit), request_id=request.state.request_id)

    @app.get("/sessions/{session_id}/messages")
    async def messages(session_id: uuid.UUID, request: Request,
                       limit: int = Query(default=50, ge=1, le=100),
                       before: int | None = Query(default=None, ge=1), user=Depends(current_user)):
        return envelope(data=await store.list_messages(user["id"], str(session_id), limit, before),
                        request_id=request.state.request_id)

    @app.post("/chat")
    async def chat(body: ChatInput, request: Request, user=Depends(current_user)):
        # 请求参数、身份、知识库和本地模型配置先检查，失败时仍可返回HTTP+JSON。
        try:
            client_key = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError:
            raise ChatError("INVALID_REQUEST", "缺少有效的请求编号，请重新发送。", 422) from None
        if app.state.retriever is None:
            raise ChatError("KNOWLEDGE_NOT_READY", "制度知识库尚未就绪，请联系管理员后重试。", 503, True)
        model_config = settings.model_config()
        session_id, request_id = str(body.session_id), request.state.request_id
        turn = await store.prepare_turn(user["id"], session_id, body.question, client_key)
        progress = {"content": ""}

        async def cleanup():
            if not turn["replayed"]:
                try:
                    await store.finish_turn(turn["turn_id"], user["id"], "interrupted", progress["content"],
                                            error_code="STREAM_INTERRUPTED", error_message="连接中断，回答未完成。")
                except Exception as error:
                    logger.error("中断状态保存失败 request_id=%s type=%s", request_id, type(error).__name__)

        async def generate():
            yield sse("meta", request_id, {**{k: turn[k] for k in (
                "turn_id", "user_message_id", "assistant_message_id", "replayed")}, "session_id": session_id}, "请求已接收")
            if turn["replayed"]:
                yield sse("delta", request_id, {"text": turn["content"]}, "正在恢复已有回答…")
                yield sse("sources", request_id, {"items": turn["sources"]}, "引用来源已恢复")
                yield sse("done", request_id, {"status": "completed"}, "回答已保存")
                return
            try:
                async with asyncio.timeout(settings.generation_timeout):
                    yield sse("status", request_id, {"stage": "retrieving"}, "正在检索制度…")
                    history = await store.history(user["id"], session_id, 6)
                    try:
                        search = app.state.retriever
                        # CPU检索移出事件循环；异步注入服务则直接await。
                        if inspect.iscoroutinefunction(search):
                            hits = await search(body.question)
                        else:
                            hits = await asyncio.to_thread(search, body.question)
                            if inspect.isawaitable(hits):
                                hits = await hits
                    except Exception as error:
                        raise ChatError("RETRIEVAL_ERROR", "制度检索失败，请稍后重试。", 503, True) from error
                    if not hits:
                        raise ChatError("RETRIEVAL_EMPTY", "未找到可用的制度片段，请补充问题后重试。", 503, True)
                    model_messages = build_chat_messages(body.question, hits, history)
                    yield sse("status", request_id, {"stage": "generating"}, "正在生成回答…")
                    checkpoint_time = time.monotonic()
                    upstream = app.state.llm_stream(model_messages, model_config, app.state.llm_client)
                    try:
                        async for text in upstream:
                            if not isinstance(text, str):
                                raise ChatError("LLM_PROTOCOL_ERROR", "模型正文格式异常，请稍后重试。", 502, True)
                            progress["content"] += text
                            if len(progress["content"]) > 24000:
                                raise ChatError("LLM_INCOMPLETE", "模型回答过长，请缩小问题范围后重试。", 502, True)
                            yield sse("delta", request_id, {"text": text}, "正在生成回答…")
                            if time.monotonic() - checkpoint_time >= 1:
                                await store.checkpoint(turn["turn_id"], user["id"], progress["content"])
                                checkpoint_time = time.monotonic()
                    finally:
                        await upstream.aclose()
                    if not progress["content"].strip():
                        raise ChatError("LLM_EMPTY", "模型未返回可用回答，请稍后重试。", 502, True)
                    sources = answer_sources(progress["content"], hits, app.state.document_version)
                    saved = await store.finish_turn(turn["turn_id"], user["id"], "completed", progress["content"], sources)
                    if not saved:
                        raise ChatError("STREAM_INTERRUPTED", "本次回答已中断，请刷新查看记录。", 409, True)
                    # 只有引用验证及数据库提交均成功，前端才收到sources和done。
                    yield sse("sources", request_id, {"items": sources}, "引用来源已校验")
                    yield sse("done", request_id, {"status": "completed"}, "回答已保存")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if isinstance(error, ChatError):
                    failure = error
                elif isinstance(error, TimeoutError):
                    failure = ChatError("LLM_TIMEOUT", "回答生成超时，请稍后重试。", 504, True)
                elif isinstance(error, sqlite3.Error):
                    failure = ChatError("STORAGE_ERROR", "消息保存失败，请稍后重试。", 500, True)
                else:
                    failure = ChatError("INTERNAL_ERROR", "回答生成失败，请稍后重试。", 500, True)
                    logger.error("生成失败 request_id=%s type=%s", request_id, type(error).__name__)
                try:
                    await store.finish_turn(turn["turn_id"], user["id"], "failed", progress["content"],
                                            error_code=failure.code, error_message=failure.message)
                except Exception:
                    failure = ChatError("STORAGE_ERROR", "消息保存失败，请稍后重试。", 500, True)
                yield sse("error", request_id, {"retryable": failure.retryable, "turn_id": turn["turn_id"],
                                                "partial": bool(progress["content"])}, failure.message, failure.code)

        return ManagedStreamingResponse(heartbeat_stream(generate(), settings.heartbeat_seconds), cleanup)

    # 静态资源同源提供，不引入CDN或跨域模型密钥；404仍由中文错误处理器接管。
    from agent_routes import install_agent_routes
    install_agent_routes(app, templates, ManagedStreamingResponse, heartbeat_stream, sse)
    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    # 本轮仍固定本机与单worker；企业私有云入口应另配HTTPS/受信代理与部署密钥。
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CHAT_PORT", "8000")), workers=1,
                proxy_headers=False)
