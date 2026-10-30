/* 排期与变更助手：SSE 执行过程、可恢复预览和员工确认。
 * 【可复用核心】前端只提交确认意愿；服务端仍须复查校验凭据、版本、权限和幂等。
 * 工具参数、模型回复与草稿内容均按纯文本渲染，禁止作为 HTML 或脚本执行。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "agent-main", "agent-form", "agent-question", "agent-submit", "agent-new", "agent-logout",
    "agent-input-hint", "agent-status", "agent-status-dot", "agent-rounds", "agent-refresh",
    "agent-notice", "agent-notice-text", "agent-notice-action", "agent-log-empty", "agent-logs",
    "agent-answer-panel", "agent-answer", "agent-preview-panel", "agent-preview-badge",
    "agent-preview-empty", "agent-preview-content", "agent-preview-note", "agent-preview-stream",
    "agent-preview-fields", "agent-preview-details", "agent-preview-json", "agent-expiry",
    "agent-approve", "agent-reject", "agent-confirm-actions", "agent-draft-result", "agent-draft-label",
    "agent-run-label", "agent-live-status",
  ].map((id) => [id, $(id)]));
  const USER_ID = document.querySelector('meta[name="current-user-id"]')?.content || "";
  const STORAGE_KEY = `venue-agent:last-run:${USER_ID}`;
  const AUTH_REVISION_KEY = "venue-policy-assistant:auth-revision";
  const TOOL_LABELS = {
    search_policy: "检索制度", get_booking_context: "读取原排期", check_availability: "核对资源与冲突",
    validate_request: "校验申请条件", create_draft: "创建申请单草稿", get_workflow_status: "核对任务状态",
  };
  const STATE_LABELS = {
    completed: "本次处理已完成", awaiting_confirmation: "已暂停，等待你确认草稿", round_limit: "已达到 5 轮上限",
    rejected: "已取消本次草稿创建", failed: "本次处理未完成", interrupted: "连接已中断，请核对任务状态",
    running: "助手正在处理", created: "任务已接收", pending: "任务正在准备", queued: "任务已接收，等待处理",
  };
  const state = {
    ready: false, busy: false, authExpired: false, csrf: "", runId: null,
    round: 0, maxRounds: 5, status: "idle", terminal: false, pending: null,
    previewText: "", answer: "", requestAttempt: null, controller: null, operation: 0,
    logViews: new Map(), noticeAction: null, draft: null,
  };

  class RequestError extends Error {
    constructor(message, code = "REQUEST_FAILED", status = 0) {
      super(message); this.name = "RequestError"; this.code = code; this.status = status;
    }
  }
  function textNode(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function uuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const h = Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  function plain(value) {
    if (value == null) return "";
    return typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }
  function showNotice(message, action = null) {
    ui["agent-notice-text"].textContent = message;
    state.noticeAction = action;
    ui["agent-notice-action"].hidden = !action;
    ui["agent-notice"].hidden = false;
  }
  function hideNotice() { ui["agent-notice"].hidden = true; state.noticeAction = null; }
  function setStatus(message, status = state.status) {
    state.status = status;
    ui["agent-status"].textContent = message;
    ui["agent-status-dot"].dataset.state = state.busy ? "working" : status;
    ui["agent-live-status"].textContent = message;
  }
  function rememberRun(id) {
    // 仅保存当前账号的任务编号；不持久保存问题、草稿、日志、CSRF 或认证凭据。
    try { if (id) localStorage.setItem(STORAGE_KEY, id); else localStorage.removeItem(STORAGE_KEY); }
    catch { /* 禁用存储时仍可在当前页面完成确认。 */ }
  }
  function rememberedRun() {
    try {
      const id = localStorage.getItem(STORAGE_KEY);
      return /^[a-zA-Z0-9_-]{1,100}$/.test(id || "") ? id : null;
    } catch { return null; }
  }
  function clearPrivateView() {
    state.operation += 1;
    state.controller?.abort(); state.controller = null;
    state.pending = null; state.answer = ""; state.previewText = ""; state.runId = null; state.csrf = "";
    ui["agent-main"].replaceChildren();
    document.querySelector(".account")?.replaceChildren();
  }
  function expiredLogin() {
    if (state.authExpired) return;
    state.authExpired = true;
    clearPrivateView();
    window.location.replace("/login");
  }

  /* 【可复用核心】统一 JSON/HTTP 错误处理；401 先清除旧账号画面再跳登录。 */
  async function fetchResponse(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin", cache: "no-store", ...options,
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase()) ? { "X-CSRF-Token": state.csrf } : {}),
          ...options.headers },
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new RequestError("连接服务失败，请检查网络后重试。", "NETWORK_ERROR");
    }
    if (response.status === 401) { expiredLogin(); throw new RequestError("登录已失效，请重新登录。", "UNAUTHORIZED", 401); }
    if (!response.ok) {
      let result;
      try { result = await response.json(); } catch { /* 不把反向代理返回的原始 HTML 显示给员工。 */ }
      throw new RequestError(result?.message || "请求未完成，请稍后重试。", result?.code, response.status);
    }
    return response;
  }
  async function jsonRequest(path, options = {}) {
    const response = await fetchResponse(path, options);
    let result;
    try { result = await response.json(); }
    catch { throw new RequestError("服务返回内容无法识别，请刷新状态。", "INVALID_RESPONSE"); }
    if (!result || result.code !== "OK") throw new RequestError(result?.message || "任务读取失败。", result?.code);
    return result.data;
  }

  function expiryTime(value) {
    if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
    return Date.parse(value);
  }
  function pendingValid() {
    const pending = state.pending;
    const expires = expiryTime(pending?.expires_at);
    // 内容可见不等于可确认：必须有服务端的完整确认事件或 GET 返回的持久记录。
    return pending?.trusted === true && pending.id && pending.preview_hash && pending.preview
      && Number.isFinite(expires) && expires > Date.now() && state.status === "awaiting_confirmation";
  }
  function updateControls() {
    if (state.authExpired) return;
    const locked = !state.ready || state.busy;
    ui["agent-question"].disabled = locked || Boolean(state.runId);
    ui["agent-submit"].disabled = locked || Boolean(state.runId) || !ui["agent-question"].value.trim();
    ui["agent-submit"].textContent = state.busy ? "正在处理…" : "开始处理";
    ui["agent-new"].disabled = locked || !state.runId;
    ui["agent-refresh"].hidden = !state.runId;
    ui["agent-refresh"].disabled = locked;
    ui["agent-approve"].disabled = locked || !pendingValid();
    ui["agent-reject"].disabled = locked || !pendingValid();
    ui["agent-rounds"].textContent = `${state.round} / ${state.maxRounds} 轮`;
    ui["agent-input-hint"].textContent = !state.ready ? "正在确认登录状态…" : state.busy ? "处理期间请等待结果" : state.runId ? "开始新任务可提交补充后的需求" : "最多 2000 字；每个任务最多决策 5 轮";
    ui["agent-main"].setAttribute("aria-busy", String(state.busy));
    ui["agent-status-dot"].dataset.state = state.busy ? "working" : state.status;
  }

  function setRun(id) {
    if (!id || typeof id !== "string") throw new RequestError("任务编号缺失，请重新核对状态。", "INVALID_RESPONSE");
    if (state.runId && state.runId !== id) throw new RequestError("返回的任务编号不一致，请刷新页面。", "INVALID_RESPONSE");
    state.runId = id; rememberRun(id);
    ui["agent-run-label"].hidden = false;
    ui["agent-run-label"].textContent = `任务编号：${id}`;
  }
  function setRounds(round, maximum) {
    if (Number.isInteger(round) && round >= 0) state.round = Math.min(round, 5);
    if (Number.isInteger(maximum) && maximum > 0) state.maxRounds = Math.min(maximum, 5);
    updateControls();
  }

  /* 执行日志以业务名称展示；参数和服务端摘要按需展开，绝不展示模型私有推理。 */
  function addLog(entry, complete = false) {
    const tool = String(entry.tool_name || entry.name || "");
    const round = entry.round ?? entry.round_no ?? entry.round_number ?? state.round;
    const key = String(entry.id || `${round}:${entry.event_type || "tool"}:${tool}:${entry.tool_call_id || ""}`);
    let view = state.logViews.get(key);
    if (!view) {
      const row = textNode("li");
      const heading = textNode("div", "log-heading");
      const title = textNode("strong", "", entry.event_type === "decision" ? "确定下一步" : TOOL_LABELS[tool] || (tool === "__model__" || !tool ? "整理下一步" : tool));
      const meta = textNode("span", "log-meta");
      const summary = textNode("p", "log-summary");
      const detail = textNode("details", "log-detail");
      detail.append(textNode("summary", "", "查看调用信息"));
      const body = textNode("pre"); detail.append(body); detail.hidden = true;
      heading.append(title, meta); row.append(heading, summary, detail);
      ui["agent-logs"].append(row);
      view = { row, meta, summary, detail, body }; state.logViews.set(key, view);
    }
    const elapsed = entry.elapsed_ms ?? entry.duration_ms;
    const milliseconds = Number.isFinite(Number(elapsed)) ? ` · ${Math.round(Number(elapsed))} ms` : "";
    view.meta.textContent = `${round ? `第 ${round} 轮` : "处理记录"}${milliseconds}`;
    const resultCode = entry.code || entry.error_code;
    view.row.dataset.status = complete ? (resultCode && resultCode !== "OK" ? "error" : "done") : "running";
    view.summary.textContent = plain(entry.summary ?? entry.result_summary ?? entry.message ?? (complete ? "已完成" : "正在执行…")).slice(0, 2400);
    const parameters = entry.parameters ?? entry.arguments ?? entry.arguments_json ?? entry.parameters_json;
    if (parameters != null) { view.detail.hidden = false; view.body.textContent = plain(parameters).slice(0, 12000); }
    ui["agent-log-empty"].hidden = true;
  }
  function showAnswer(text, replace = false) {
    state.answer = replace ? text : state.answer + text;
    ui["agent-answer-panel"].hidden = !state.answer;
    ui["agent-answer"].textContent = state.answer;
  }

  function resetPreview() {
    state.pending = null; state.previewText = ""; state.draft = null;
    ui["agent-preview-empty"].hidden = false; ui["agent-preview-content"].hidden = true;
    ui["agent-preview-stream"].textContent = ""; ui["agent-preview-stream"].hidden = false;
    ui["agent-preview-fields"].replaceChildren(); ui["agent-preview-fields"].hidden = true;
    ui["agent-preview-details"].hidden = true; ui["agent-preview-json"].textContent = "";
    ui["agent-expiry"].hidden = true; ui["agent-draft-result"].hidden = true;
    ui["agent-confirm-actions"].hidden = false;
    ui["agent-preview-badge"].textContent = "等待校验"; ui["agent-preview-badge"].dataset.ready = "false";
    updateControls();
  }
  function showPartialPreview(text) {
    ui["agent-preview-empty"].hidden = true; ui["agent-preview-content"].hidden = false;
    state.previewText += text;
    ui["agent-preview-stream"].textContent = state.previewText;
    ui["agent-preview-stream"].scrollTop = ui["agent-preview-stream"].scrollHeight;
    ui["agent-preview-badge"].textContent = "正在接收";
    ui["agent-preview-note"].textContent = "正在接收草稿内容，完整接收后才能确认。";
  }
  function formatTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return plain(value) || "未提供";
    return `${date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}（北京时间）`;
  }
  function showPending(pending) {
    if (!pending || !pending.id || !pending.preview_hash || !pending.preview || typeof pending.preview !== "object") {
      throw new RequestError("草稿确认信息不完整，请核对任务状态。", "INVALID_PREVIEW");
    }
    state.pending = { ...pending, trusted: true };
    ui["agent-preview-empty"].hidden = true; ui["agent-preview-content"].hidden = false;
    ui["agent-draft-result"].hidden = true; ui["agent-confirm-actions"].hidden = false;
    ui["agent-preview-badge"].textContent = "等待你确认"; ui["agent-preview-badge"].dataset.ready = "true";
    ui["agent-preview-note"].textContent = "申请单尚未创建。请核对以下内容，再决定是否创建草稿。";
    ui["agent-preview-stream"].hidden = true;
    const candidate = pending.preview.candidate || pending.preview;
    const rows = [
      ["申请类型", { new_booking: "新排期申请", booking_change: "排期变更申请" }[candidate.request_type] || candidate.request_type],
      ["场馆", candidate.venue_name || candidate.venue_id], ["资源", candidate.resource_name || candidate.resource_id],
      ["开始时间", candidate.start_at ? formatTime(candidate.start_at) : null], ["结束时间", candidate.end_at ? formatTime(candidate.end_at) : null],
      ["人数", candidate.attendees], ["用途", candidate.purpose], ["原排期", candidate.original_booking_id], ["变更原因", candidate.change_reason],
    ].filter(([, value]) => value !== undefined && value !== null && value !== "");
    const fields = ui["agent-preview-fields"]; fields.replaceChildren();
    for (const [label, value] of rows) fields.append(textNode("dt", "", label), textNode("dd", "", plain(value)));
    fields.hidden = !rows.length;
    ui["agent-preview-details"].hidden = false;
    ui["agent-preview-details"].open = !rows.length;
    ui["agent-preview-json"].textContent = plain(pending.preview);
    ui["agent-expiry"].hidden = false;
    updateExpiry(); updateControls();
  }
  function updateExpiry() {
    if (!state.pending || state.authExpired) return;
    const stamp = expiryTime(state.pending.expires_at);
    const valid = Number.isFinite(stamp) && stamp > Date.now();
    ui["agent-expiry"].textContent = valid ? `请在 ${formatTime(stamp)} 前确认；创建时会再次核对权限和版本。` : "本次确认已过期。请开始新任务，重新查询和校验。";
    if (!valid) { ui["agent-preview-badge"].textContent = "确认已过期"; ui["agent-preview-badge"].dataset.ready = "false"; }
    updateControls();
  }
  function showDraft(draft) {
    state.draft = draft; state.pending = null;
    ui["agent-preview-empty"].hidden = true; ui["agent-preview-content"].hidden = true;
    ui["agent-draft-result"].hidden = false;
    ui["agent-preview-badge"].textContent = "已创建"; ui["agent-preview-badge"].dataset.ready = "false";
    ui["agent-draft-label"].textContent = `草稿编号：${draft.id || draft.draft_id || draft.identifier || "已保存，请核对任务记录"}`;
    updateControls();
  }

  /* 【可复用核心】SSE 逐行解析，兼容跨网络包的汉字、CRLF、空行帧和心跳注释。 */
  function sseParser(onEvent) {
    let buffer = "", event = "", data = [], first = true;
    function line(value) {
      if (first) { value = value.replace(/^\uFEFF/, ""); first = false; }
      if (!value) {
        if (data.length) {
          let result;
          try { result = JSON.parse(data.join("\n")); }
          catch { throw new RequestError("任务流格式异常，请核对任务状态。", "INVALID_STREAM"); }
          const name = event || "message"; event = ""; data = []; onEvent(name, result);
        } else event = "";
        return;
      }
      if (value.startsWith(":")) return;
      const index = value.indexOf(":");
      const field = index < 0 ? value : value.slice(0, index);
      let content = index < 0 ? "" : value.slice(index + 1);
      if (content.startsWith(" ")) content = content.slice(1);
      if (field === "event") event = content; else if (field === "data") data.push(content);
    }
    function consume(eof) {
      while (buffer.length) {
        const index = buffer.search(/[\r\n]/); if (index < 0) break;
        if (buffer[index] === "\r" && index === buffer.length - 1 && !eof) break;
        const width = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
        const value = buffer.slice(0, index); buffer = buffer.slice(index + width); line(value);
      }
      if (buffer.length > 262144) throw new RequestError("任务流数据过长，请核对任务状态。", "INVALID_STREAM");
    }
    return { push(text) { buffer += text; consume(false); }, finish() { consume(true); buffer = ""; event = ""; data = []; } };
  }
  function onStreamEvent(name, result) {
    if (state.terminal || state.authExpired) return;
    if (!result || typeof result !== "object") throw new RequestError("任务流内容不完整。", "INVALID_STREAM");
    if (name === "error" || result.code !== "OK") {
      state.terminal = true;
      throw new RequestError(result.message || "本次处理未完成，请核对状态。", result.code);
    }
    const data = result.data || {};
    if (name === "meta") { setRun(data.run_id); setRounds(data.round_count, data.max_rounds); }
    else if (name === "status") {
      setRounds(data.round, data.max_rounds);
      setStatus(result.message || ({ deciding: "正在整理下一步…", executing: "正在执行查询与校验…" }[data.stage] || "助手正在处理…"), "running");
    } else if (name === "tool_start") { setRounds(data.round); addLog(data); }
    else if (name === "tool_result") { setRounds(data.round); addLog(data, true); }
    else if (name === "delta") {
      if (typeof data.text !== "string") throw new RequestError("助手回复片段格式异常。", "INVALID_STREAM");
      showAnswer(data.text);
    } else if (name === "preview_start") {
      resetPreview(); showPartialPreview("");
    } else if (name === "preview_delta") {
      if (typeof data.text !== "string") throw new RequestError("草稿片段格式异常。", "INVALID_STREAM");
      showPartialPreview(data.text);
    } else if (name === "confirmation_required") {
      showPending({ id: data.confirmation_id, preview_hash: data.preview_hash, expires_at: data.expires_at, preview: data.preview });
      setStatus("校验已通过，等待你确认草稿", "awaiting_confirmation");
    } else if (name === "done") {
      if (!Object.hasOwn(STATE_LABELS, data.status)) throw new RequestError("任务终态无法识别，请核对状态。", "INVALID_STREAM");
      state.terminal = true; setStatus(STATE_LABELS[data.status], data.status);
      if (data.draft) showDraft(data.draft);
      if (data.status !== "awaiting_confirmation") state.pending = null;
      if (data.status === "rejected") {
        ui["agent-preview-badge"].textContent = "已取消"; ui["agent-preview-badge"].dataset.ready = "false";
        ui["agent-preview-note"].textContent = "你已选择暂不创建。本次操作没有创建申请单草稿。";
        ui["agent-confirm-actions"].hidden = true;
      }
    }
  }
  async function readStream(response, operation) {
    if (!response.body || !(response.headers.get("content-type") || "").includes("text/event-stream")) {
      throw new RequestError("服务未返回任务流，请核对任务状态。", "INVALID_STREAM");
    }
    const reader = response.body.getReader(), decoder = new TextDecoder("utf-8");
    const parser = sseParser(onStreamEvent);
    let ended = false;
    try {
      while (operation === state.operation && !state.terminal && !state.authExpired) {
        const { value, done } = await reader.read();
        if (done) { parser.push(decoder.decode()); parser.finish(); ended = true; break; }
        parser.push(decoder.decode(value, { stream: true }));
      }
    } finally {
      if (!ended) { try { await reader.cancel(); } catch { /* 已断开的连接无需再次处理。 */ } }
      reader.releaseLock();
    }
    if (!state.terminal && operation === state.operation && !state.authExpired) {
      throw new RequestError("连接提前结束，正在核对任务状态。", "STREAM_INTERRUPTED");
    }
  }

  /* 恢复只发 GET。断线、刷新或确认请求超时，都不能被当作重新建单的授权。 */
  async function refreshRun({ quiet = false } = {}) {
    if (!state.runId || state.authExpired) return false;
    const operation = state.operation;
    try {
      const result = await jsonRequest(`/agent/runs/${encodeURIComponent(state.runId)}`);
      if (operation !== state.operation || state.authExpired) return false;
      if (!result || typeof result !== "object") throw new RequestError("任务状态不完整，请稍后重试。", "INVALID_RESPONSE");
      setRun(result.id || result.run_id); setRounds(result.round_count, result.max_rounds);
      if (Array.isArray(result.logs)) {
        state.logViews.clear(); ui["agent-logs"].replaceChildren(); ui["agent-log-empty"].hidden = false;
        for (const entry of result.logs) addLog(entry, entry.status !== "running");
      }
      if (typeof result.question === "string") ui["agent-question"].value = result.question;
      if (typeof result.answer === "string") showAnswer(result.answer, true);
      setStatus(STATE_LABELS[result.status] || "任务状态已读取", result.status);
      if (result.draft) showDraft(result.draft);
      else if (result.status === "awaiting_confirmation" && result.pending_confirmation) showPending(result.pending_confirmation);
      else {
        state.pending = null;
        if (result.status === "rejected") {
          ui["agent-preview-badge"].textContent = "已取消";
          ui["agent-preview-note"].textContent = "你已选择暂不创建。本次操作没有创建申请单草稿。";
          ui["agent-confirm-actions"].hidden = true;
        }
      }
      updateControls(); return true;
    } catch (error) {
      if (!state.authExpired && operation === state.operation) {
        if (error.status === 404) { rememberRun(null); showNotice(error.message); }
        else if (!quiet) showNotice(error.message, () => refreshRun());
      }
      throw error;
    }
  }
  async function consumeRequest(path, body, headers = {}) {
    const operation = ++state.operation;
    state.busy = true; state.terminal = false; state.pending = null;
    state.controller = new AbortController(); hideNotice(); updateControls();
    setStatus("任务正在处理…", "running");
    try {
      const response = await fetchResponse(path, { method: "POST", body: JSON.stringify(body),
        headers: { Accept: "text/event-stream", ...headers }, signal: state.controller.signal });
      await readStream(response, operation);
      // 终态之后读取一次持久状态，取得草稿编号/完整日志，并确认可恢复的中断记录。
      if (operation === state.operation && state.runId && !state.authExpired) await refreshRun({ quiet: true });
    } catch (error) {
      if (operation !== state.operation || state.authExpired) return;
      state.pending = null;
      if (state.runId) {
        try {
          const recovered = await refreshRun({ quiet: true });
          if (recovered) {
            showNotice(error.message === "连接服务失败，请检查网络后重试。" ? "连接曾中断，已核对任务的保存状态。" : error.message, () => refreshRun());
          }
        } catch {
          setStatus("暂时无法核对任务结果，请刷新状态后再操作。", "interrupted");
          showNotice("连接中断且暂时无法核对结果。请核对任务状态，勿重复创建。", () => refreshRun());
        }
      } else {
        setStatus("尚未取得任务编号", "interrupted");
        showNotice(`${error.message} 再次发送相同内容将复用原请求编号。`);
      }
    } finally {
      if (operation === state.operation && !state.authExpired) { state.busy = false; state.controller = null; updateControls(); }
    }
  }

  async function startRun(event) {
    event.preventDefault();
    if (!state.ready || state.busy || state.runId || state.authExpired) return;
    const question = ui["agent-question"].value.trim();
    if (!question || question.length > 2000) { showNotice("请填写2000字以内的具体需求。"); return; }
    // 未收到 meta 的网络不确定请求保留同一幂等键；显式修改内容才视为新请求。
    if (!state.requestAttempt || state.requestAttempt.question !== question) state.requestAttempt = { question, key: uuid() };
    resetPreview(); state.answer = ""; ui["agent-answer"].textContent = ""; ui["agent-answer-panel"].hidden = true;
    state.logViews.clear(); ui["agent-logs"].replaceChildren(); ui["agent-log-empty"].hidden = false;
    await consumeRequest("/agent/runs", { question }, { "Idempotency-Key": state.requestAttempt.key });
  }
  async function confirmDraft(approved) {
    if (state.busy || !pendingValid() || !state.runId || state.authExpired) return;
    const pending = state.pending;
    // 请求绑定员工实际看到的完整内容摘要，模型不能替员工生成这里的确认动作。
    await consumeRequest(`/agent/runs/${encodeURIComponent(state.runId)}/confirm`, {
      confirmation_id: pending.id, preview_hash: pending.preview_hash, approved,
    });
  }
  function newTask() {
    if (state.busy || !state.ready) return;
    state.operation += 1; state.runId = null; state.round = 0; state.status = "idle";
    state.requestAttempt = null; state.answer = ""; state.logViews.clear(); rememberRun(null);
    ui["agent-logs"].replaceChildren(); ui["agent-log-empty"].hidden = false;
    ui["agent-answer"].textContent = ""; ui["agent-answer-panel"].hidden = true;
    ui["agent-run-label"].hidden = true; ui["agent-question"].value = "";
    resetPreview(); hideNotice(); setStatus("等待你的需求", "idle"); updateControls(); ui["agent-question"].focus();
  }
  async function logout() {
    if (state.authExpired) return;
    ui["agent-logout"].disabled = true;
    try {
      await jsonRequest("/auth/logout", { method: "POST" });
      try { localStorage.removeItem(STORAGE_KEY); localStorage.setItem(AUTH_REVISION_KEY, uuid()); } catch { /* 禁用存储不阻止服务端撤销登录。 */ }
      expiredLogin();
    } catch (error) {
      if (!state.authExpired) { showNotice(error.message); ui["agent-logout"].disabled = false; }
    }
  }
  async function initialize() {
    try {
      const me = await jsonRequest("/auth/me");
      if (state.authExpired) return;
      if (!me?.id || me.id !== USER_ID) { clearPrivateView(); window.location.reload(); return; }
      if (!me.csrf_token) throw new RequestError("登录校验信息不完整，请重新登录。", "INVALID_AUTH");
      state.csrf = me.csrf_token; state.ready = true;
      const id = rememberedRun();
      if (id) { setRun(id); await refreshRun(); }
    } catch (error) {
      if (!state.authExpired) showNotice(error.message, state.runId ? () => refreshRun() : initialize);
    } finally { updateControls(); }
  }

  ui["agent-form"].addEventListener("submit", startRun);
  ui["agent-question"].addEventListener("input", updateControls);
  ui["agent-new"].addEventListener("click", newTask);
  ui["agent-approve"].addEventListener("click", () => confirmDraft(true));
  ui["agent-reject"].addEventListener("click", () => confirmDraft(false));
  ui["agent-refresh"].addEventListener("click", async () => { try { await refreshRun(); } catch { /* 中文错误已显示。 */ } });
  ui["agent-notice-action"].addEventListener("click", async () => {
    const action = state.noticeAction; hideNotice(); if (action) { try { await action(); } catch { /* action已提供中文错误。 */ } }
  });
  ui["agent-logout"].addEventListener("click", logout);
  window.addEventListener("storage", (event) => {
    if (event.key === AUTH_REVISION_KEY) { state.authExpired = true; clearPrivateView(); window.location.reload(); }
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { state.authExpired = true; clearPrivateView(); window.location.reload(); }
  });
  window.addEventListener("beforeunload", () => state.controller?.abort());
  window.setInterval(updateExpiry, 1000);
  initialize();
})();
