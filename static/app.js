/* 场馆制度助手：原生浏览器客户端。模型文本、文件名与摘录一律用 textContent 写入。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "sidebar", "sidebar-backdrop", "open-sidebar", "close-sidebar", "new-session",
    "refresh-sessions", "session-list", "session-list-note", "session-lock-note",
    "current-session-title", "knowledge-status", "knowledge-status-text", "demo-banner",
    "notice", "notice-text", "notice-action", "dismiss-notice", "chat-scroll", "welcome",
    "history-loading", "conversation", "load-earlier", "messages", "reload-history",
    "jump-latest", "chat-form", "question", "composer-hint", "character-count",
    "send-question", "stop-generation", "live-status", "account-avatar", "account-name",
    "account-role", "logout-button", "admin-link",
  ].map((id) => [id, $(id)]));
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobileLayout = window.matchMedia("(max-width: 700px)");
  const STORAGE_KEY = "venue-policy-assistant:last-session";
  const AUTH_REVISION_KEY = "venue-policy-assistant:auth-revision";
  const state = {
    configReady: false, knowledgeReady: false, modelReady: false, maxQuestionLength: 2000,
    sessions: [], sessionId: null, messages: [],
    activeTurn: null, starting: false, creating: false, loadingHistory: false,
    historyError: false, historyVersion: 0, historyController: null,
    hasMore: false, nextBefore: null, loadingEarlier: false, listVersion: 0,
    autoScroll: true, noticeAction: null, messageViews: new Map(), sidebarOpen: false,
    userId: null, csrfToken: "", authExpired: false, loggingOut: false,
  };

  class ApiError extends Error {
    constructor(message, code = "REQUEST_FAILED", retryable = false, requestId = "") {
      super(message);
      this.name = "ApiError";
      this.code = code;
      this.retryable = retryable;
      this.requestId = requestId;
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function announce(text) { ui["live-status"].textContent = text; }
  function isBusy() { return Boolean(state.activeTurn || state.starting || state.creating); }
  function navigationLocked() { return isBusy() || state.loadingHistory || state.loadingEarlier; }
  function canSend() {
    return !state.authExpired && !state.loggingOut && state.configReady && state.knowledgeReady && state.modelReady && !navigationLocked() && !state.historyError;
  }

  function clearPrivateView() {
    // 身份失效或账号切换时先清空画面，再跳转；中断旧请求，避免旧回包重新填入消息。
    state.configReady = false;
    state.csrfToken = "";
    state.historyVersion += 1;
    state.listVersion += 1;
    state.historyController?.abort();
    if (state.activeTurn) {
      state.activeTurn.stopped = true;
      if (state.activeTurn.typingTimer != null) clearTimeout(state.activeTurn.typingTimer);
      state.activeTurn.controller.abort();
      state.activeTurn = null;
    }
    state.sessionId = null;
    state.sessions = [];
    state.messages = [];
    state.messageViews.clear();
    ui.messages.replaceChildren();
    ui["session-list"].replaceChildren();
    ui.conversation.hidden = true;
    ui.welcome.hidden = false;
    ui.question.value = "";
    ui["account-name"].textContent = "请重新登录";
    ui["account-role"].textContent = "身份已失效";
    ui["admin-link"].hidden = true;
    ui["current-session-title"].textContent = "新会话";
    updateControls();
  }

  function redirectToLogin() {
    if (state.authExpired) return;
    state.authExpired = true;
    clearPrivateView();
    window.location.replace("/login?next=%2F");
  }

  function clearRememberedSessions() {
    try {
      const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index));
      for (const key of keys) if (key === STORAGE_KEY || key?.startsWith(`${STORAGE_KEY}:`)) localStorage.removeItem(key);
      localStorage.setItem(AUTH_REVISION_KEY, uuid());
    } catch { /* 存储不可用时仍然正常退出，服务端会撤销登录记录。 */ }
  }

  function showNotice(message, action = null, label = "重试") {
    ui["notice-text"].textContent = message;
    state.noticeAction = action;
    ui["notice-action"].hidden = !action;
    ui["notice-action"].textContent = label;
    ui.notice.hidden = false;
  }
  function hideNotice() { ui.notice.hidden = true; state.noticeAction = null; }

  function uuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (n) => n.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin", cache: "no-store", ...options,
        // 可复用：写请求统一携带内存 CSRF 令牌；JWT 只通过 HttpOnly Cookie 自动发送。
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase()) ? { "X-CSRF-Token": state.csrfToken } : {}), ...options.headers },
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError("连接服务失败，请检查网络后重试。", "NETWORK_ERROR", true);
    }
    if (response.status === 401) {
      redirectToLogin();
      throw new ApiError("登录已失效，请重新登录。", "UNAUTHORIZED");
    }
    let envelope;
    try { envelope = await response.json(); }
    catch { throw new ApiError("服务返回了无法识别的内容，请稍后重试。", "INVALID_RESPONSE", true); }
    if (!response.ok || !envelope || envelope.code !== "OK") {
      throw new ApiError(envelope?.message || "请求未完成，请稍后重试。", envelope?.code, envelope?.data?.retryable === true, envelope?.request_id);
    }
    return envelope.data;
  }

  function saveSession(id) {
    if (!state.userId) return;
    const key = `${STORAGE_KEY}:${state.userId}`;
    try { if (id) localStorage.setItem(key, id); else localStorage.removeItem(key); }
    catch { /* 隐私模式禁用存储时，会话仍可通过左栏恢复。 */ }
  }
  function rememberedSession() {
    if (!state.userId) return null;
    try {
      // 同一浏览器切换员工账号时不恢复其他人的会话编号；服务端仍会校验消息所有权。
      const value = localStorage.getItem(`${STORAGE_KEY}:${state.userId}`);
      return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || "") ? value : null;
    } catch { return null; }
  }
  function formatDate(value, detailed = false) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const today = new Date();
    const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    if (date.toDateString() === today.toDateString()) return detailed ? time : `今天 ${time}`;
    return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", ...(date.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
  }

  function upsertSession(session) {
    const id = session.id || session.session_id;
    if (!id) throw new ApiError("会话信息不完整，请重新创建。", "INVALID_RESPONSE");
    const index = state.sessions.findIndex((item) => item.id === id);
    const previous = index >= 0 ? state.sessions[index] : {};
    const next = { ...previous, ...session, id, title: session.title || previous.title || "新会话" };
    if (index >= 0) state.sessions[index] = next; else state.sessions.push(next);
    state.sessions.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    renderSessions();
  }

  function renderSessions() {
    ui["session-list"].replaceChildren();
    for (const session of state.sessions) {
      const li = element("li");
      const button = element("button", "session-item");
      button.type = "button";
      button.title = session.title || "新会话";
      button.setAttribute("aria-current", String(session.id === state.sessionId));
      button.disabled = navigationLocked();
      button.append(element("span", "session-item-title", session.title || "新会话"), element("span", "session-item-time", formatDate(session.updated_at)));
      button.addEventListener("click", () => { closeSidebar(); loadSession(session.id); });
      li.append(button);
      ui["session-list"].append(li);
    }
    const current = state.sessions.find((item) => item.id === state.sessionId);
    ui["current-session-title"].textContent = current?.title || "新会话";
  }

  async function refreshSessions({ quiet = false } = {}) {
    const version = ++state.listVersion;
    ui["refresh-sessions"].disabled = true;
    if (!quiet) { ui["session-list-note"].hidden = false; ui["session-list-note"].textContent = "正在加载会话…"; }
    try {
      const data = await api("/sessions?limit=30");
      if (version !== state.listVersion) return;
      if (!Array.isArray(data?.items)) throw new ApiError("会话列表格式异常，请重新加载。");
      // 合并本地刚建的会话，避免旧的列表请求覆盖它；按更新时间保留较新记录。
      const merged = new Map(state.sessions.map((item) => [item.id, item]));
      for (const item of data.items) {
        if (!item?.id) continue;
        const local = merged.get(item.id);
        if (!local || String(item.updated_at || "") >= String(local.updated_at || "")) merged.set(item.id, item);
      }
      state.sessions = [...merged.values()].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
      ui["session-list-note"].textContent = state.sessions.length ? "" : "还没有会话，从第一个问题开始。";
      ui["session-list-note"].hidden = state.sessions.length > 0;
      renderSessions();
    } catch (error) {
      if (version !== state.listVersion) return;
      ui["session-list-note"].hidden = false;
      ui["session-list-note"].textContent = "会话列表加载失败，可点击上方刷新按钮重试。";
      if (!quiet) announce(error.message);
    } finally {
      if (version === state.listVersion) ui["refresh-sessions"].disabled = false;
    }
  }

  async function loadConfig() {
    state.configReady = false;
    updateControls();
    try {
      const config = await api("/config");
      if (state.authExpired) return;
      if (!config?.user?.id || !["admin", "employee"].includes(config.user.role) || !config.csrf_token) {
        throw new ApiError("登录信息不完整，请刷新页面或重新登录。", "INVALID_RESPONSE");
      }
      if (state.userId && state.userId !== config.user.id) { window.location.reload(); return; }
      state.userId = config.user.id;
      state.csrfToken = config.csrf_token;
      ui["account-name"].textContent = config.user.display_name || "当前用户";
      ui["account-role"].textContent = config.user.role === "admin" ? "管理员" : "员工";
      ui["account-avatar"].textContent = config.user.role === "admin" ? "管" : "员";
      ui["admin-link"].hidden = config.user.role !== "admin";
      ui["logout-button"].disabled = false;
      state.configReady = true;
      state.knowledgeReady = config?.knowledge_ready === true;
      state.modelReady = config?.model_ready === true;
      const configuredLimit = Number(config?.max_question_length);
      state.maxQuestionLength = Number.isInteger(configuredLimit) && configuredLimit > 0 ? configuredLimit : 2000;
      ui.question.maxLength = state.maxQuestionLength;
      ui["demo-banner"].hidden = config?.knowledge_is_demo !== true;
      ui["knowledge-status"].className = `knowledge-status ${state.knowledgeReady && state.modelReady ? "is-ready" : "is-unavailable"}`;
      ui["knowledge-status-text"].textContent = !state.modelReady ? "模型尚未配置" : state.knowledgeReady ? "制度资料已就绪" : "制度资料未就绪";
      if (!state.knowledgeReady) showNotice("制度资料尚未就绪，请准备好资料后重新检查。", loadConfig, "重新检查");
      else if (!state.modelReady) showNotice("后端尚未配置模型，配置后重新检查即可提问。", loadConfig, "重新检查");
      else if (state.noticeAction === loadConfig) hideNotice();
    } catch (error) {
      ui["knowledge-status"].className = "knowledge-status is-unavailable";
      ui["knowledge-status-text"].textContent = "连接失败";
      showNotice(error.message, loadConfig, "重新连接");
    } finally { resizeQuestion(); }
  }

  function updateControls() {
    const busy = isBusy();
    const locked = navigationLocked();
    ui["new-session"].disabled = locked || !state.configReady || state.loggingOut || state.authExpired;
    ui["logout-button"].disabled = !state.userId || state.loggingOut || state.authExpired;
    ui["reload-history"].disabled = locked || !state.sessionId;
    ui.question.disabled = !canSend();
    ui["send-question"].disabled = !canSend() || !ui.question.value.trim() || ui.question.value.length > state.maxQuestionLength;
    ui["send-question"].hidden = Boolean(state.activeTurn);
    ui["stop-generation"].hidden = !state.activeTurn;
    ui["session-lock-note"].hidden = !busy;
    ui["chat-scroll"].setAttribute("aria-busy", String(state.loadingHistory || busy));
    ui["load-earlier"].disabled = locked;
    ui["knowledge-status"].disabled = locked;
    for (const button of document.querySelectorAll("[data-question]")) button.disabled = !canSend();
    for (const button of ui["session-list"].querySelectorAll("button")) button.disabled = locked;
    for (const button of ui.messages.querySelectorAll("[data-request-action]")) button.disabled = locked;
    let hint = "Enter 发送，Shift + Enter 换行";
    if (!state.configReady) hint = "正在连接制度资料…";
    else if (!state.knowledgeReady) hint = "制度资料就绪后即可提问";
    else if (!state.modelReady) hint = "后端尚未配置模型";
    else if (state.loadingHistory) hint = "正在恢复会话…";
    else if (state.historyError) hint = "请先重新加载当前会话";
    else if (state.starting || state.creating) hint = "正在准备会话…";
    else if (state.activeTurn) hint = "回答生成中，可随时停止";
    ui["composer-hint"].textContent = hint;
  }

  function normalizeSources(items) {
    if (!Array.isArray(items)) return [];
    // 仅显示服务端最终校验后返回的引用；不使用检索状态中的候选片段。
    return items.filter((item) => item && typeof item.filename === "string" && typeof item.excerpt === "string")
      .map((item) => ({ ...item, filename: item.filename, excerpt: item.excerpt }));
  }
  function normalizeMessage(item) {
    return {
      id: item.id || uuid(), turnId: item.turn_id, role: item.role === "user" ? "user" : "assistant",
      content: typeof item.content === "string" ? item.content : "", status: item.status || "completed",
      sources: normalizeSources(item.sources), errorCode: item.error_code, errorMessage: item.error_message,
      seqNo: item.seq_no, createdAt: item.created_at, request: null,
    };
  }

  async function loadSession(id) {
    if (navigationLocked()) return;
    const version = ++state.historyVersion;
    state.historyController?.abort();
    state.historyController = new AbortController();
    state.sessionId = id;
    state.messages = [];
    state.hasMore = false;
    state.nextBefore = null;
    state.historyError = false;
    state.loadingHistory = true;
    state.autoScroll = true;
    if (state.noticeAction !== loadConfig) hideNotice();
    saveSession(id);
    renderSessions();
    renderConversation();
    updateControls();
    try {
      const data = await api(`/sessions/${encodeURIComponent(id)}/messages?limit=50`, { signal: state.historyController.signal });
      if (version !== state.historyVersion || id !== state.sessionId) return;
      if (!Array.isArray(data?.items)) throw new ApiError("消息格式异常，请重新加载当前会话。");
      state.messages = data.items.map(normalizeMessage);
      state.hasMore = data.has_more === true;
      state.nextBefore = data.next_before;
    } catch (error) {
      if (error.name === "AbortError" || version !== state.historyVersion) return;
      state.historyError = true;
      showNotice(error.message, () => loadSession(id), "重新加载会话");
      if (error.code === "SESSION_NOT_FOUND" || error.code === "NOT_FOUND") saveSession(null);
    } finally {
      if (version === state.historyVersion) {
        state.loadingHistory = false;
        renderConversation();
        updateControls();
        scrollToLatest(true);
      }
    }
  }

  async function loadEarlier() {
    if (navigationLocked() || !state.hasMore || state.nextBefore == null) return;
    const sessionId = state.sessionId;
    const version = state.historyVersion;
    const oldHeight = ui["chat-scroll"].scrollHeight;
    const oldTop = ui["chat-scroll"].scrollTop;
    state.loadingEarlier = true;
    ui["load-earlier"].textContent = "正在加载…";
    updateControls();
    try {
      const data = await api(`/sessions/${encodeURIComponent(sessionId)}/messages?limit=50&before=${encodeURIComponent(state.nextBefore)}`);
      if (version !== state.historyVersion || sessionId !== state.sessionId) return;
      if (!Array.isArray(data?.items)) throw new ApiError("消息格式异常，请重试。");
      const existing = new Set(state.messages.map((message) => message.id));
      state.messages = [...data.items.filter((message) => !existing.has(message.id)).map(normalizeMessage), ...state.messages];
      state.hasMore = data.has_more === true;
      state.nextBefore = data.next_before;
      renderConversation();
      ui["chat-scroll"].scrollTop = oldTop + ui["chat-scroll"].scrollHeight - oldHeight;
    } catch (error) { showNotice(error.message, loadEarlier, "重新加载"); }
    finally { state.loadingEarlier = false; ui["load-earlier"].textContent = "查看更早的消息"; updateControls(); }
  }

  async function createSession() {
    if (navigationLocked() || !state.configReady) return;
    state.creating = true;
    hideNotice();
    updateControls();
    try {
      const data = await api("/sessions", { method: "POST", body: JSON.stringify({}) });
      upsertSession(data);
      state.sessionId = data.session_id || data.id;
      state.messages = [];
      state.hasMore = false;
      state.historyError = false;
      state.historyVersion += 1;
      state.autoScroll = true;
      saveSession(state.sessionId);
      closeSidebar();
      renderSessions();
      renderConversation();
      announce("已新建会话，可以开始提问。");
    } catch (error) { showNotice(error.message, createSession, "重新创建"); }
    finally { state.creating = false; updateControls(); if (canSend()) ui.question.focus(); }
  }

  function renderConversation() {
    state.messageViews.clear();
    ui.messages.replaceChildren();
    ui["history-loading"].hidden = !state.loadingHistory;
    ui.welcome.hidden = state.loadingHistory || state.messages.length > 0 || state.historyError;
    ui.conversation.hidden = state.loadingHistory || !state.messages.length;
    ui["load-earlier"].hidden = !state.hasMore;
    for (const message of state.messages) appendMessage(message);
    updateScrollButton();
  }

  function appendMessage(message) {
    const article = element("article", `message message-${message.role}`);
    const heading = element("div", "message-heading");
    const speaker = message.role === "user" ? "你" : "制度助手";
    heading.append(element("span", "message-avatar", message.role === "user" ? "我" : "制"), element("span", "message-speaker", speaker));
    const time = element("time", "message-time", formatDate(message.createdAt, true));
    if (message.createdAt) time.dateTime = message.createdAt;
    heading.append(time);
    const body = element("div", "message-content", message.content);
    const phase = element("div", "message-phase");
    phase.append(element("span", "status-indicator"), element("span", "phase-text"));
    const sourceContainer = element("div");
    const actions = element("div", "message-actions");
    article.append(heading, body, phase, sourceContainer, actions);
    article.setAttribute("aria-label", `${speaker}的消息`);
    ui.messages.append(article);
    state.messageViews.set(message, { article, body, phase, phaseText: phase.lastChild, sourceContainer, actions, sourcesSignature: null });
    updateMessage(message);
  }

  function sourceLabel(source) {
    const start = Number(source.start_line);
    const end = Number(source.end_line);
    const location = [];
    if (Number.isInteger(start) && start > 0) location.push(Number.isInteger(end) && end >= start && end !== start ? `第 ${start}–${end} 行` : `第 ${start} 行`);
    if (source.chunk_id != null) location.push(`片段 ${String(source.chunk_id)}`);
    if (source.document_version) location.push(`版本 ${String(source.document_version)}`);
    return location.join(" · ");
  }
  function renderSources(message, container) {
    container.replaceChildren();
    if (message.sources.length) {
      const section = element("section", "sources");
      section.setAttribute("aria-label", "回答引用依据");
      section.append(element("h3", "sources-title", `引用依据（${message.sources.length}）`));
      message.sources.forEach((source, index) => {
        const details = element("details", "source");
        const summary = element("summary");
        summary.append(element("span", "source-number", `[${source.source_id || index + 1}]`), element("span", "source-filename", source.filename));
        details.append(summary);
        const locator = sourceLabel(source);
        if (locator) details.append(element("span", "source-locator", locator));
        details.append(element("blockquote", "source-excerpt", source.excerpt || "此引用未提供片段正文。"));
        section.append(details);
      });
      container.append(section);
    } else if (message.status === "completed") {
      container.append(element("p", "no-sources", "本次回答未提供可核对的引用，请结合制度原文确认。"));
    }
  }

  function addAction(container, label, callback, requestAction = false) {
    const button = element("button", "message-action", label);
    button.type = "button";
    if (requestAction) { button.dataset.requestAction = "true"; button.disabled = navigationLocked(); }
    button.addEventListener("click", callback);
    container.append(button);
  }

  function updateMessage(message) {
    const view = state.messageViews.get(message);
    if (!view) return;
    view.body.textContent = message.content;
    if (message.role === "user") { view.phase.hidden = true; view.actions.hidden = true; return; }
    const active = state.activeTurn?.message === message;
    let phase = "";
    let settled = false;
    if (message.status === "generating") {
      phase = active ? (message.phase || "正在检索制度…") : "此回答仍在处理中，可核对最新状态。";
      settled = !active;
    } else if (message.status === "pending") { phase = message.errorMessage || "连接已中断，尚未确认回答状态。"; settled = true; }
    else if (message.status === "interrupted") { phase = message.errorMessage || "已停止生成，以上内容为已生成部分。"; settled = true; }
    else if (message.status === "failed") { phase = message.errorMessage || "回答未完成，请重试。"; settled = true; }
    view.phase.hidden = !phase;
    view.phase.className = `message-phase${settled ? " is-settled" : ""}${message.status === "failed" ? " is-error" : ""}`;
    view.phaseText.textContent = phase;
    const signature = JSON.stringify([message.sources, message.status === "completed"]);
    if (signature !== view.sourcesSignature) {
      renderSources(message, view.sourceContainer);
      view.sourcesSignature = signature;
    }
    // 流式更新只替换文本和状态；引用展开状态、复制按钮的焦点均保持稳定。
    const actionSignature = `${message.status}:${Boolean(message.request)}:${Boolean(message.retryable)}:${active}`;
    if (view.actionSignature !== actionSignature) {
      view.actions.replaceChildren();
      if (!active && message.content) addAction(view.actions, "复制回答", async () => {
        try { await navigator.clipboard.writeText(message.content); announce("回答已复制。"); }
        catch { showNotice("复制失败，请选择回答文字后复制。"); }
      });
      if (!active && message.request && message.retryable && ["failed", "interrupted", "pending"].includes(message.status)) {
        addAction(view.actions, "重试此问题", () => retryMessage(message), true);
      }
      if (!active && ["generating", "pending"].includes(message.status)) {
        addAction(view.actions, "核对回答状态", () => loadSession(state.sessionId), true);
      }
      view.actionSignature = actionSignature;
    }
    view.actions.hidden = !view.actions.childElementCount;
    scrollToLatest();
  }

  function nearBottom() {
    const scroller = ui["chat-scroll"];
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100;
  }
  function scrollToLatest(force = false) {
    if (force || state.autoScroll) ui["chat-scroll"].scrollTop = ui["chat-scroll"].scrollHeight;
    updateScrollButton();
  }
  function updateScrollButton() {
    ui["jump-latest"].hidden = !state.messages.length || nearBottom();
  }

  /* SSE 按行解析，而非按网络块拆分；一个汉字、CRLF 和一帧都可能跨多次读取。 */
  function createSSEParser(onEvent) {
    let buffer = "";
    let eventName = "";
    let dataLines = [];
    function line(value) {
      if (value === "") {
        if (dataLines.length) {
          const data = dataLines.join("\n");
          const name = eventName || "message";
          eventName = "";
          dataLines = [];
          let payload;
          try { payload = JSON.parse(data); }
          catch { throw new ApiError("回答数据格式异常，请重试此问题。", "INVALID_STREAM", true); }
          onEvent(name, payload);
        } else eventName = "";
        return;
      }
      if (value.startsWith(":")) return; // 心跳注释不会改变当前事件。
      const colon = value.indexOf(":");
      const field = colon < 0 ? value : value.slice(0, colon);
      let valueText = colon < 0 ? "" : value.slice(colon + 1);
      if (valueText.startsWith(" ")) valueText = valueText.slice(1);
      if (field === "event") eventName = valueText;
      else if (field === "data") dataLines.push(valueText);
    }
    function consume(eof) {
      while (buffer.length) {
        const index = buffer.search(/[\r\n]/);
        if (index < 0) break;
        if (buffer[index] === "\r" && index === buffer.length - 1 && !eof) break;
        const width = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
        const value = buffer.slice(0, index);
        buffer = buffer.slice(index + width);
        line(value);
      }
    }
    return {
      push(text) { buffer += text; consume(false); },
      finish() {
        consume(true);
        // SSE 事件必须以空行结束。EOF 的半帧不提交，调用方会核对历史中的最终状态。
        buffer = ""; dataLines = []; eventName = "";
      },
    };
  }

  function settleTyping(turn) {
    if (!turn.pending.length) {
      const waiters = turn.typingWaiters.splice(0);
      waiters.forEach((resolve) => resolve());
    }
  }
  function scheduleTyping(turn) {
    if (turn.typingTimer != null || turn.stopped) return;
    turn.typingTimer = window.setTimeout(() => {
      turn.typingTimer = null;
      if (state.activeTurn !== turn || turn.stopped) { settleTyping(turn); return; }
      const count = reducedMotion.matches ? turn.pending.length : Math.min(30, 2 + Math.ceil(turn.pending.length / 45));
      turn.message.content += turn.pending.splice(0, count).join("");
      updateMessage(turn.message);
      if (turn.pending.length) scheduleTyping(turn); else settleTyping(turn);
    }, reducedMotion.matches ? 0 : 24);
  }
  function enqueueText(turn, text) {
    turn.fullText += text;
    // Array.from 保留 Unicode 码点，不把 emoji 的代理对拆成闪烁的乱码。
    for (const character of text) turn.pending.push(character);
    if (turn.pending.length) scheduleTyping(turn);
  }
  function flushTyping(turn) {
    if (turn.typingTimer != null) window.clearTimeout(turn.typingTimer);
    turn.typingTimer = null;
    turn.pending.length = 0;
    turn.message.content = turn.fullText;
    updateMessage(turn.message);
    settleTyping(turn);
  }
  function waitForTyping(turn) {
    return turn.pending.length ? new Promise((resolve) => turn.typingWaiters.push(resolve)) : Promise.resolve();
  }

  function handleStreamEvent(turn, name, envelope) {
    if (turn.stopped || state.activeTurn !== turn || turn.terminal) return;
    if (!envelope || typeof envelope !== "object" || !envelope.data || typeof envelope.data !== "object") {
      throw new ApiError("回答数据不完整，请重试此问题。", "INVALID_STREAM", true);
    }
    const data = envelope.data;
    if (name === "error") {
      turn.terminal = "error";
      turn.error = new ApiError(envelope.message || "回答未完成，请重试。", envelope.code, data.retryable === true, envelope.request_id);
      if (data.turn_id) turn.turnId = data.turn_id;
      return;
    }
    if (envelope.code !== "OK") throw new ApiError(envelope.message || "回答未完成，请重试。", envelope.code, data.retryable === true, envelope.request_id);
    if (name === "meta") {
      if (data.session_id !== turn.request.sessionId) throw new ApiError("回答所属会话不一致，请重新加载会话。", "INVALID_STREAM");
      turn.turnId = data.turn_id;
      turn.message.turnId = data.turn_id;
      if (data.assistant_message_id) turn.message.id = data.assistant_message_id;
      if (data.user_message_id) turn.user.id = data.user_message_id;
    } else if (name === "status") {
      turn.message.phase = data.stage === "retrieving" ? "正在检索制度…" : "正在整理回答…";
      updateMessage(turn.message);
      announce(turn.message.phase);
    } else if (name === "delta") {
      if (typeof data.text !== "string") throw new ApiError("回答片段格式异常，请重试。", "INVALID_STREAM", true);
      if (data.text) {
        turn.message.phase = "正在生成回答…";
        enqueueText(turn, data.text);
      }
    } else if (name === "sources") {
      if (!Array.isArray(data.items)) throw new ApiError("引用数据格式异常，请重试。", "INVALID_STREAM", true);
      turn.message.sources = normalizeSources(data.items);
      updateMessage(turn.message);
    } else if (name === "done") {
      if (data.status !== "completed") throw new ApiError("回答未返回完整状态，请重新核对。", "INVALID_STREAM", true);
      turn.terminal = "done";
    }
  }

  async function readStream(response, turn) {
    if (!response.body) throw new ApiError("浏览器未收到回答流，请重试。", "EMPTY_STREAM", true);
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const parser = createSSEParser((name, envelope) => handleStreamEvent(turn, name, envelope));
    let ended = false;
    try {
      while (!turn.stopped && !turn.terminal) {
        const { value, done } = await reader.read();
        if (done) { parser.push(decoder.decode()); parser.finish(); ended = true; break; }
        parser.push(decoder.decode(value, { stream: true }));
      }
    } finally {
      // 收到终态就停止读取；异常和停止也释放连接，后台可据此标记中断。
      if (!ended) { try { await reader.cancel(); } catch { /* 已关闭的网络流无需再次处理。 */ } }
      reader.releaseLock();
    }
    if (!turn.terminal && !turn.stopped) throw new ApiError("连接提前结束，正在核对回答状态。", "STREAM_DISCONNECTED", true);
  }

  function findRecoveredTurn(items, turn) {
    let assistant = items.find((item) => item.role === "assistant" && (item.id === turn.message.id || (turn.turnId && item.turn_id === turn.turnId)));
    if (assistant) return assistant;
    // meta 也可能在断网时丢失。只有恰好一个新增的同题用户消息时，才通过 turn_id 关联。
    const candidates = items.filter((item) => item.role === "user" && item.content === turn.request.question && !turn.knownIds.has(item.id) && Number(item.seq_no) > turn.beforeSeq);
    if (candidates.length === 1 && candidates[0].turn_id) {
      assistant = items.find((item) => item.role === "assistant" && item.turn_id === candidates[0].turn_id);
      if (assistant) { turn.user.id = candidates[0].id; turn.user.seqNo = candidates[0].seq_no; }
    }
    return assistant;
  }

  function abortableDelay(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(new DOMException("已停止", "AbortError")); return; }
      const onAbort = () => { clearTimeout(timer); reject(new DOMException("已停止", "AbortError")); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, ms);
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  async function reconcileTurn(turn) {
    turn.message.phase = "连接中断，正在核对回答…";
    updateMessage(turn.message);
    announce(turn.message.phase);
    let recovered = null;
    // 只重试只读查询。绝不自动重发 POST，以免网络抖动造成重复提问。
    for (let attempt = 0; attempt < 4; attempt += 1) {
      if (turn.stopped) return false;
      if (attempt) await abortableDelay(700 * attempt, turn.controller.signal);
      const data = await api(`/sessions/${encodeURIComponent(turn.request.sessionId)}/messages?limit=50`, { signal: turn.controller.signal });
      if (!Array.isArray(data?.items)) throw new ApiError("无法核对回答状态，请重新加载会话。", "INVALID_RESPONSE", true);
      recovered = findRecoveredTurn(data.items, turn);
      if (recovered && recovered.status !== "generating") break;
    }
    if (!recovered) return false;
    const canonical = normalizeMessage(recovered);
    turn.message.id = canonical.id;
    turn.message.turnId = canonical.turnId;
    turn.message.seqNo = canonical.seqNo;
    turn.message.content = canonical.content;
    turn.message.sources = canonical.sources;
    turn.fullText = canonical.content;
    turn.message.status = canonical.status;
    turn.message.errorCode = canonical.errorCode;
    if (canonical.status === "completed") {
      turn.message.errorMessage = "";
      turn.message.retryable = false;
      announce("已核对，回答已完成。");
    } else if (canonical.status === "generating") {
      turn.message.status = "pending";
      turn.message.errorMessage = "连接已中断，后台仍在处理。可核对回答状态查看结果。";
      turn.message.retryable = false;
      announce(turn.message.errorMessage);
    } else {
      turn.message.errorMessage = canonical.errorMessage || (canonical.status === "interrupted" ? "连接中断，回答已停止。" : "回答未完成，请重试此问题。");
      turn.message.retryable = true;
      announce(turn.message.errorMessage);
    }
    return true;
  }

  async function executeTurn(user, message, request) {
    const turn = {
      user, message, request, controller: new AbortController(), stopped: false,
      terminal: null, error: null, fullText: "", pending: [], typingTimer: null,
      typingWaiters: [], turnId: message.turnId,
      knownIds: new Set(state.messages.filter((item) => item !== user && item !== message).map((item) => item.id)),
      beforeSeq: Math.max(0, ...state.messages.filter((item) => item !== user && item !== message).map((item) => Number(item.seqNo) || 0)),
    };
    state.activeTurn = turn;
    message.content = "";
    message.sources = [];
    message.status = "generating";
    message.phase = "正在检索制度…";
    message.errorMessage = "";
    message.retryable = false;
    state.autoScroll = true;
    updateMessage(message);
    updateControls();
    ui["stop-generation"].focus({ preventScroll: true });
    announce("正在检索制度…");
    let requestAccepted = false;
    try {
      const response = await fetch("/chat", {
        method: "POST", credentials: "same-origin", cache: "no-store", signal: turn.controller.signal,
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", "Idempotency-Key": request.key, "X-CSRF-Token": state.csrfToken },
        body: JSON.stringify({ session_id: request.sessionId, question: request.question }),
      });
      if (response.status === 401) {
        redirectToLogin();
        throw new ApiError("登录已失效，请重新登录。", "UNAUTHORIZED");
      }
      if (!response.ok) {
        let envelope;
        try { envelope = await response.json(); } catch { /* 非 JSON 的代理错误使用统一提示。 */ }
        throw new ApiError(envelope?.message || `服务暂时无法回答（${response.status}），请稍后重试。`, envelope?.code || "HTTP_ERROR", envelope?.data?.retryable === true || response.status >= 500, envelope?.request_id);
      }
      requestAccepted = true;
      if (!(response.headers.get("content-type") || "").toLowerCase().includes("text/event-stream")) throw new ApiError("服务未返回流式回答，正在核对会话。", "INVALID_STREAM", true);
      await readStream(response, turn);
      // done 表示服务端结束；队列排空之后才把界面标记完成，避免最后一段被截断。
      await waitForTyping(turn);
      if (turn.stopped) return;
      if (turn.terminal === "error") throw turn.error;
      if (turn.terminal !== "done") throw new ApiError("连接提前结束，请核对会话。", "STREAM_DISCONNECTED", true);
      message.status = "completed";
      message.retryable = false;
      announce("回答已完成，可查看下方引用依据。");
    } catch (error) {
      flushTyping(turn);
      if (turn.stopped || error.name === "AbortError") {
        if (turn.terminal === "done") message.status = "completed";
        else { message.status = "interrupted"; message.retryable = true; message.errorMessage = "已停止生成，以上内容为已生成部分。"; }
      } else if (!turn.terminal && (requestAccepted || !(error instanceof ApiError))) {
        // 未见终态时不能直接声称失败：后台可能已经保存完整回答。
        try {
          const recovered = await reconcileTurn(turn);
          if (!recovered && !turn.stopped) {
            message.status = "pending";
            message.errorMessage = "连接已中断，尚未确认回答状态。可核对会话，或重试同一请求。";
            message.retryable = true;
            announce(message.errorMessage);
          }
        } catch (recoveryError) {
          if (!turn.stopped) {
            message.status = "pending";
            message.errorMessage = "连接中断且暂时无法核对结果。可重新加载会话，或重试同一请求。";
            message.retryable = true;
            announce(message.errorMessage);
          }
        }
      } else {
        message.status = error.code === "REQUEST_IN_PROGRESS" ? "pending" : "failed";
        message.errorMessage = error.message || "回答未完成，请重试此问题。";
        message.errorCode = error.code;
        message.retryable = error.retryable === true && error.code !== "REQUEST_IN_PROGRESS" && error.code !== "IDEMPOTENCY_CONFLICT";
        announce(message.errorMessage);
      }
    } finally {
      if (turn.typingTimer != null) clearTimeout(turn.typingTimer);
      if (state.activeTurn === turn) {
        state.activeTurn = null;
        updateMessage(message);
        updateControls();
        const session = state.sessions.find((item) => item.id === request.sessionId);
        if (session) upsertSession({ ...session, updated_at: new Date().toISOString() });
        refreshSessions({ quiet: true });
        if (canSend() && document.activeElement === ui["stop-generation"]) ui.question.focus();
      }
    }
  }

  async function sendQuestion() {
    const question = ui.question.value.trim();
    if (!question || !canSend()) return;
    if (question.length > state.maxQuestionLength) {
      showNotice(`问题最多可输入 ${state.maxQuestionLength} 字，请缩短后发送。`);
      return;
    }
    state.starting = true;
    hideNotice();
    updateControls();
    try {
      if (!state.sessionId) {
        const data = await api("/sessions", { method: "POST", body: JSON.stringify({ title: question.slice(0, 32) }) });
        upsertSession(data);
        state.sessionId = data.session_id || data.id;
        saveSession(state.sessionId);
        renderSessions();
      }
      const createdAt = new Date().toISOString();
      const request = { sessionId: state.sessionId, question, key: uuid() };
      const user = { id: uuid(), role: "user", content: question, status: "completed", sources: [], createdAt };
      const message = { id: uuid(), role: "assistant", content: "", status: "generating", sources: [], createdAt, request };
      state.messages.push(user, message);
      ui.question.value = "";
      resizeQuestion();
      state.autoScroll = true;
      ui.welcome.hidden = true;
      ui.conversation.hidden = false;
      appendMessage(user);
      appendMessage(message);
      state.starting = false;
      await executeTurn(user, message, request);
    } catch (error) { showNotice(error.message || "会话创建失败，请重新发送问题。"); }
    finally { state.starting = false; updateControls(); }
  }

  async function retryMessage(message) {
    if (!canSend() || !message.request || message.request.sessionId !== state.sessionId) return;
    const index = state.messages.indexOf(message);
    const user = state.messages[index - 1];
    if (!user || user.role !== "user") return;
    hideNotice();
    // 显式重试保留原问题、会话及幂等键；完成的请求会重放，绝不新建第二轮。
    await executeTurn(user, message, message.request);
  }

  function stopGeneration() {
    const turn = state.activeTurn;
    if (!turn) return;
    if (turn.terminal) {
      // 服务端已给出终态时，仅跳过剩余动画；done 和 error 的真实状态都不能覆盖。
      flushTyping(turn);
      return;
    }
    turn.stopped = true;
    turn.controller.abort();
    flushTyping(turn);
    turn.message.status = "interrupted";
    turn.message.errorMessage = "已停止生成，以上内容为已生成部分。";
    turn.message.retryable = true;
    updateMessage(turn.message);
    announce("已停止生成。");
  }

  async function logout() {
    if (state.loggingOut || state.authExpired) return;
    state.loggingOut = true;
    stopGeneration();
    updateControls();
    try {
      await api("/auth/logout", { method: "POST" });
      clearRememberedSessions();
      clearPrivateView();
      window.location.replace("/login");
    } catch (error) {
      if (!state.authExpired) showNotice(error.message || "退出失败，请稍后重试。");
    } finally { state.loggingOut = false; updateControls(); }
  }

  function resizeQuestion() {
    ui.question.style.height = "auto";
    ui.question.style.height = `${Math.min(ui.question.scrollHeight, mobileLayout.matches ? 130 : 160)}px`;
    ui["character-count"].hidden = ui.question.value.length < state.maxQuestionLength * 0.8;
    ui["character-count"].textContent = `${ui.question.value.length} / ${state.maxQuestionLength}`;
    updateControls();
  }

  function openSidebar() {
    state.sidebarOpen = true;
    ui.sidebar.classList.add("is-open");
    ui["sidebar-backdrop"].hidden = false;
    ui["open-sidebar"].setAttribute("aria-expanded", "true");
    // 手机导航是模态抽屉：主区不可聚焦，Tab 限制在抽屉内部。
    if (mobileLayout.matches) { $("main").inert = true; ui["close-sidebar"].focus(); }
  }
  function closeSidebar() {
    const wasOpen = state.sidebarOpen;
    state.sidebarOpen = false;
    ui.sidebar.classList.remove("is-open");
    ui["sidebar-backdrop"].hidden = true;
    ui["open-sidebar"].setAttribute("aria-expanded", "false");
    $("main").inert = false;
    if (wasOpen && mobileLayout.matches) ui["open-sidebar"].focus();
  }

  ui["chat-form"].addEventListener("submit", (event) => { event.preventDefault(); sendQuestion(); });
  ui.question.addEventListener("input", resizeQuestion);
  ui.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      sendQuestion();
    }
  });
  for (const button of document.querySelectorAll("[data-question]")) {
    button.addEventListener("click", () => { ui.question.value = button.dataset.question; resizeQuestion(); ui.question.focus(); });
  }
  ui["new-session"].addEventListener("click", createSession);
  ui["refresh-sessions"].addEventListener("click", () => refreshSessions());
  ui["reload-history"].addEventListener("click", () => loadSession(state.sessionId));
  ui["knowledge-status"].addEventListener("click", loadConfig);
  ui["load-earlier"].addEventListener("click", loadEarlier);
  ui["stop-generation"].addEventListener("click", stopGeneration);
  ui["logout-button"].addEventListener("click", logout);
  ui["dismiss-notice"].addEventListener("click", hideNotice);
  ui["notice-action"].addEventListener("click", () => { const action = state.noticeAction; hideNotice(); if (action) action(); });
  ui["open-sidebar"].addEventListener("click", openSidebar);
  ui["close-sidebar"].addEventListener("click", closeSidebar);
  ui["sidebar-backdrop"].addEventListener("click", closeSidebar);
  ui["jump-latest"].addEventListener("click", () => { state.autoScroll = true; scrollToLatest(true); });
  ui["chat-scroll"].addEventListener("scroll", () => { state.autoScroll = nearBottom(); updateScrollButton(); }, { passive: true });
  document.addEventListener("keydown", (event) => {
    if (!state.sidebarOpen || !mobileLayout.matches) return;
    if (event.key === "Escape") { event.preventDefault(); closeSidebar(); }
    if (event.key === "Tab") {
      const focusable = [...ui.sidebar.querySelectorAll("button:not(:disabled),a[href]")].filter((node) => node.getClientRects().length);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  mobileLayout.addEventListener("change", () => { closeSidebar(); resizeQuestion(); });
  window.addEventListener("beforeunload", () => state.activeTurn?.controller.abort());
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    state.authExpired = true;
    clearPrivateView();
    window.location.reload();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== AUTH_REVISION_KEY) return;
    // 其他标签页登录或退出会改变同源 Cookie，立即清除当前账号画面并重新取身份。
    state.authExpired = true;
    clearPrivateView();
    window.location.reload();
  });

  async function initialize() {
    // 先取得真实身份及 CSRF，再加载该账号的会话，避免跨账号恢复旧本地记录。
    await loadConfig();
    if (!state.configReady || state.authExpired) return;
    await refreshSessions();
    const previous = rememberedSession();
    if (previous && !navigationLocked()) await loadSession(previous);
    updateControls();
  }
  initialize();
})();
