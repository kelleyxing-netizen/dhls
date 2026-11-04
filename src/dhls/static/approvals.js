/* 员工草稿与管理员审批共用的只读渲染和显式写入交互。权限与版本由服务端复核。 */
(() => {
  "use strict";
  const statusNames = { draft: "未提交", submitted: "待审核", approved: "已通过", returned: "已退回" };
  const typeNames = { new_booking: "新建排期", booking_change: "排期变更" };
  const actionNames = { submit: "提交审核", approve: "通过审批", return: "退回申请" };
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const dateFields = new Set(["start_at", "end_at"]);

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function date(value) {
    if (!value) return "未记录";
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(parsed);
  }

  function valueText(value, field = "") {
    if (value === null || value === undefined || value === "") return "未提供";
    if (dateFields.has(field)) return `${date(value)}（北京时间）`;
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  function start(mode) {
    const root = document.getElementById("review-workspace");
    if (!root || root.dataset.started === "true" || root.dataset.mode !== mode) return;
    root.dataset.started = "true";
    const admin = mode === "admin";
    const base = admin ? "/admin/api/approvals" : "/api/drafts";
    const ids = ["notice", "notice-text", "notice-refresh", "filter", "scope", "refresh", "list", "count", "detail-panel", "empty", "detail", "detail-heading", "draft-id", "detail-status", "outcome", "fields", "changes-section", "baseline-note", "changes", "assessment-status", "assessment-summary", "issues", "conflicts", "decision-section", "decision-help", "comment", "acknowledge", "approve", "return", "submit-section", "submit-help", "submit", "events", "confirmation", "confirmation-title", "confirmation-copy", "cancel", "confirm", "live", "schedule-viz", "assessment-viz", "analytics", "analytics-scope", "analytics-error", "analytics-footnote", "distribution", "trend", "trend-days"];
    const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(`review-${id}`)]));
    for (const id of ["analytics-toggle", "analytics-body", "analytics-compact"]) ui[id] = document.getElementById(`review-${id}`);
    if (admin) window.VenueVisuals.disclosure({ button: ui["analytics-toggle"], body: ui["analytics-body"], root: ui.analytics, compact: ui["analytics-compact"], key: "venue-ui:approval-overview", label: "图表" });
    const initialId = new URLSearchParams(window.location.search).get("draft");
    const state = { csrf: document.querySelector('meta[name="csrf-token"]')?.content || "", items: [], selected: uuidPattern.test(initialId || "") ? initialId : null, detail: null, request: 0, summaryRequest: 0, busy: false, loadingList: false, decision: null, user: null };

    function announce(text) { ui.live.textContent = text; }

    function notice(text, { refresh = false, success = false } = {}) {
      ui["notice-text"].textContent = text;
      ui.notice.hidden = !text;
      ui.notice.dataset.kind = success ? "success" : "error";
      ui["notice-refresh"].hidden = !refresh;
      if (text) announce(text);
    }

    async function api(path, options = {}) {
      let response;
      try {
        response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options,
          headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json", "X-CSRF-Token": state.csrf } : {}), ...options.headers } });
      } catch { throw Object.assign(new Error("连接服务失败，请检查网络并刷新当前内容。"), { status: 0 }); }
      let envelope;
      try { envelope = await response.json(); }
      catch { throw Object.assign(new Error("服务未返回有效数据，请稍后刷新重试。"), { status: response.status }); }
      if (!response.ok || envelope?.code !== "OK") {
        if (response.status === 401) window.location.assign("/login");
        throw Object.assign(new Error(envelope?.message || "请求未完成，请刷新后重试。"), { status: response.status, code: envelope?.code });
      }
      return envelope.data;
    }

    function setStatus(node, status) {
      node.textContent = statusNames[status] || status;
      node.dataset.status = status;
    }

    function setBusy(value) {
      state.busy = value;
      root.setAttribute("aria-busy", String(value));
      ui.refresh.disabled = value;
      ui.filter.disabled = value;
      ui.confirm.disabled = value;
      ui.cancel.disabled = value;
      if (ui.comment) ui.comment.disabled = value || !(state.detail?.actions?.can_approve || state.detail?.actions?.can_return);
      if (ui.acknowledge) ui.acknowledge.disabled = value || !state.detail?.actions?.can_approve;
      for (const button of ui.list.querySelectorAll("button")) button.disabled = value;
      if (admin) for (const button of ui.distribution.querySelectorAll("button")) button.disabled = value || state.loadingList;
      updateButtons();
    }

    function updateButtons() {
      const actions = state.detail?.actions || {};
      const blocked = state.busy || Boolean(state.decision);
      if (ui.submit) ui.submit.disabled = blocked || !actions.can_submit;
      if (ui.approve) ui.approve.disabled = blocked || !actions.can_approve || !ui.acknowledge.checked;
      if (ui.return) ui.return.disabled = blocked || !actions.can_return;
    }

    function resetConfirmation() {
      state.decision = null;
      ui.confirmation.hidden = true;
      if (ui.acknowledge) ui.acknowledge.checked = false;
      updateButtons();
    }

    function renderList() {
      ui.list.replaceChildren();
      ui.list.setAttribute("aria-busy", "false");
      ui.count.textContent = `${state.items.length} 份 · 最近50份`;
      if (!state.items.length) {
        const empty = element("div", "review-list-empty");
        empty.append(element("strong", "", admin ? "当前没有符合条件的申请" : "当前没有符合条件的草稿"),
          element("span", "", admin ? "员工提交后会出现在待审核列表中。也可以切换状态查看已处理申请。" : "先在排期与变更助手中创建草稿，再到这里核对并提交审核。"));
        if (!admin) { const link = element("a", "", "准备新草稿"); link.href = "/agent"; empty.append(link); }
        ui.list.append(empty);
        return;
      }
      for (const item of state.items) {
        const candidate = item.candidate || {};
        const button = element("button", "review-list-item");
        button.type = "button";
        button.disabled = state.busy;
        button.setAttribute("aria-current", String(item.id === state.selected));
        const heading = element("span", "review-item-heading");
        const title = `${typeNames[item.request_type || candidate.request_type] || "排期申请"} · ${candidate.resource_id || candidate.venue_id || "待核对资源"}`;
        const badge = element("span", "review-status");
        setStatus(badge, item.status);
        heading.append(element("strong", "", title), badge);
        button.append(heading, element("span", "review-item-time", `${date(candidate.start_at)} 至 ${date(candidate.end_at)}（北京时间）`));
        const owner = item.owner?.display_name || "申请人";
        button.append(element("span", "review-item-meta", admin ? `${owner} · ${item.owner?.department_id || "未记录部门"}` : `版本 ${item.version} · 创建于 ${date(item.created_at)}`));
        button.setAttribute("aria-label", `${title}，${statusNames[item.status] || item.status}，${owner}`);
        button.addEventListener("click", () => { if (!state.busy) loadDetail(item.id, { focus: true }); });
        ui.list.append(button);
      }
    }

    function clearDetail(message = "选择一份申请", copy = "申请内容与审核记录会显示在这里。") {
      state.detail = null;
      resetConfirmation();
      ui.detail.hidden = true;
      ui.empty.hidden = false;
      ui.empty.querySelector("h2").textContent = message;
      ui.empty.querySelector("p").textContent = copy;
      ui["detail-panel"].setAttribute("aria-busy", "false");
    }

    function field(label, value, fieldName = "") {
      ui.fields.append(element("dt", "", label), element("dd", "", valueText(value, fieldName)));
    }

    function renderDetail(detail, { comment = "" } = {}) {
      state.detail = detail;
      state.selected = detail.id;
      resetConfirmation();
      ui.empty.hidden = true;
      ui.detail.hidden = false;
      ui["detail-panel"].setAttribute("aria-busy", "false");
      const candidate = detail.candidate || {};
      ui["detail-heading"].textContent = `${typeNames[detail.request_type || candidate.request_type] || "排期申请"}详情`;
      ui["draft-id"].textContent = `草稿 ${detail.id} · 版本 ${detail.version}`;
      setStatus(ui["detail-status"], detail.status);
      const outcomes = {
        draft: "草稿尚未提交，管理员还不会在待审核列表中看到它。",
        submitted: "本版本已提交审核。审批结果将保存在下方记录中。",
        approved: "本版本审批已通过。审核结果已保存，资源占用与原排期保持原有状态。",
        returned: `申请已退回。${detail.latest_comment ? `退回意见：${detail.latest_comment}` : "请查看下方审批记录中的退回意见。"}`,
      };
      ui.outcome.textContent = outcomes[detail.status] || "";
      ui.outcome.hidden = !ui.outcome.textContent;
      ui.fields.replaceChildren();
      field("申请人", `${detail.owner?.display_name || "未记录"}（${detail.owner?.department_id || "未记录部门"}）`);
      field("申请类型", typeNames[candidate.request_type] || candidate.request_type);
      field("场馆", candidate.venue_id); field("资源", candidate.resource_id);
      field("开始时间", candidate.start_at, "start_at"); field("结束时间", candidate.end_at, "end_at");
      field("人数", candidate.attendees === undefined ? null : `${candidate.attendees} 人`); field("用途", candidate.purpose);
      if (candidate.request_type === "booking_change") {
        field("原排期编号", candidate.original_booking_id); field("申请基线版本", candidate.expected_booking_version); field("变更原因", candidate.change_reason);
      }
      field("创建时间", `${date(detail.created_at)}（北京时间）`);
      if (detail.submitted_at) field("提交时间", `${date(detail.submitted_at)}（北京时间）`);
      window.VenueVisuals.schedule(ui["schedule-viz"], detail);
      window.VenueVisuals.assessment(ui["assessment-viz"], detail);
      renderChanges(detail); renderAssessment(detail); renderEvents(detail.events || []);
      const actions = detail.actions || {};
      if (admin) {
        ui["decision-section"].hidden = detail.status !== "submitted";
        ui.comment.value = comment;
        ui.comment.disabled = !(actions.can_approve || actions.can_return);
        ui.acknowledge.disabled = !actions.can_approve;
        ui["decision-help"].textContent = actions.can_approve
          ? "核对以上内容后选择通过，或写明原因后退回。审核会再次检查最新权限与资源情况。"
          : actions.can_return ? "当前复核未通过，暂不能批准。可根据问题写明原因后退回。"
            : "当前账号不能处理这份申请。本人提交的申请需要其他有场馆审核权限的管理员处理。";
      } else {
        ui["submit-section"].hidden = !["draft", "returned"].includes(detail.status);
        ui["submit-help"].textContent = detail.status === "returned"
          ? "需要修改内容时，请回到助手准备一份修正草稿。若本版内容无需调整，可在复核条件满足后重新提交。"
          : "核对完整申请后提交。提交时会重新检查你的业务权限、规则和资源冲突。";
      }
      updateButtons(); renderList();
      const address = new URL(window.location.href);
      address.searchParams.set("draft", detail.id);
      window.history.replaceState(null, "", address.pathname + address.search);
    }

    function renderChanges(detail) {
      const candidate = detail.candidate || {};
      ui["changes-section"].hidden = candidate.request_type !== "booking_change";
      ui.changes.replaceChildren();
      if (candidate.request_type !== "booking_change") return;
      const baseline = detail.baseline;
      ui["baseline-note"].textContent = baseline
        ? `原排期基线：${baseline.id}，版本 ${baseline.version}，状态 ${baseline.status === "approved" ? "已批准" : baseline.status}。${detail.submitted_at ? "此处保留提交时的基线，是否仍有效由下方当前复核判断。" : ""}时间均按北京时间显示。`
        : "当前无法读取原排期基线，请查看复核问题。";
      for (const change of detail.changes || []) {
        const row = element("tr");
        row.dataset.changed = String(JSON.stringify(change.before) !== JSON.stringify(change.after));
        const label = element("th", "", change.label || change.field); label.scope = "row";
        row.append(label, element("td", "", valueText(change.before, change.field)), element("td", "", valueText(change.after, change.field)));
        ui.changes.append(row);
      }
      if (!ui.changes.children.length) {
        const row = element("tr"); const cell = element("td", "", "暂无可显示的字段差异。"); cell.colSpan = 3; row.append(cell); ui.changes.append(row);
      }
    }

    function renderAssessment(detail) {
      const assessment = detail.assessment || {};
      const passed = assessment.status === "PASS";
      ui["assessment-status"].textContent = passed ? "复核通过" : "需要处理";
      ui["assessment-status"].dataset.status = passed ? "PASS" : "BLOCKED";
      const capacity = typeof assessment.capacity === "number" ? `当前资源容量为 ${assessment.capacity} 人。` : "";
      ui["assessment-summary"].textContent = passed
        ? `当前权限、适用规则和资源冲突复核通过。${capacity}保存审核决定时会再次复核。`
        : `以下条件需要核实或调整。${capacity}`;
      ui.issues.replaceChildren();
      for (const issue of assessment.issues || []) ui.issues.append(element("li", "", typeof issue === "string" ? issue : issue.message || issue.code));
      ui.issues.hidden = !ui.issues.children.length;
      if (!passed && !ui.issues.children.length) { ui.issues.append(element("li", "", "当前复核条件尚未满足，请刷新内容或联系管理员核实。")); ui.issues.hidden = false; }
      ui.conflicts.replaceChildren();
      const conflicts = assessment.conflicts || [];
      ui.conflicts.hidden = !conflicts.length;
      if (conflicts.length) {
        ui.conflicts.append(element("p", "", "已批准占用的冲突时段"));
        const list = element("ul");
        for (const conflict of conflicts) list.append(element("li", "", `${date(conflict.start_at)} 至 ${date(conflict.end_at)}（北京时间）`));
        ui.conflicts.append(list);
      }
    }

    function renderEvents(events) {
      ui.events.replaceChildren();
      if (!events.length) { ui.events.append(element("li", "review-help", "尚无审批记录。提交审核后，处理过程会显示在这里。")); return; }
      for (const event of events) {
        const item = element("li"); const heading = element("div", "review-event-heading");
        const time = element("time", "", `${date(event.created_at)}（北京时间）`);
        if (event.created_at) time.dateTime = event.created_at;
        heading.append(element("strong", "", `${event.actor?.display_name || "操作人"} · ${actionNames[event.action] || event.action}`), time);
        item.append(heading, element("p", "review-event-meta", `${statusNames[event.from_status] || event.from_status} → ${statusNames[event.to_status] || event.to_status} · 版本 ${event.version}`));
        if (event.comment) item.append(element("p", "review-event-comment", event.comment));
        ui.events.append(item);
      }
    }

    async function loadDetail(id, { focus = false, retainComment = false } = {}) {
      if (state.busy || !uuidPattern.test(id || "")) return;
      const comment = retainComment && ui.comment ? ui.comment.value : "";
      const request = ++state.request;
      state.selected = id;
      clearDetail("正在读取详情", "正在核对申请内容、权限与资源状态…");
      ui["detail-panel"].setAttribute("aria-busy", "true");
      renderList();
      try {
        const detail = await api(`${base}/${encodeURIComponent(id)}`);
        if (request !== state.request) return;
        renderDetail(detail, { comment });
        if (focus) { ui["detail-panel"].focus({ preventScroll: true }); if (window.innerWidth <= 900) ui["detail-panel"].scrollIntoView({ block: "start", behavior: "instant" }); }
        announce(`已读取${typeNames[detail.request_type] || "申请"}，${statusNames[detail.status] || detail.status}。`);
      } catch (error) {
        if (request !== state.request) return;
        clearDetail("暂时无法读取这份申请", error.message);
        notice(error.message, { refresh: true });
      }
    }

    async function loadList({ keepDetail = false } = {}) {
      if (state.busy || state.loadingList) return;
      state.loadingList = true;
      ui.refresh.disabled = true;
      ui.filter.disabled = true;
      ui.list.setAttribute("aria-busy", "true");
      syncSummaryFilter();
      if (!state.items.length) ui.list.replaceChildren(element("p", "review-loading", admin ? "正在读取已提交申请…" : "正在读取你的草稿…"));
      try {
        const query = new URLSearchParams({ limit: "50" });
        if (ui.filter.value !== "all") query.set("status", ui.filter.value);
        const data = await api(`${base}?${query}`);
        if (!Array.isArray(data?.items)) throw new Error("申请列表格式异常，请刷新重试。");
        state.items = data.items;
        if (admin) {
          const venues = data.reviewable_venues || [];
          ui.scope.textContent = venues.length ? `可审核场馆：${venues.map(item => item.name && item.name !== item.id ? `${item.name}（${item.id}）` : item.id).join("、")}` : "尚未开通场馆审核权限";
          if (!venues.length) notice("当前管理员尚无场馆审核授权。请联系系统维护人员开通相应场馆的审核权限。");
        }
        renderList();
        if (!keepDetail) {
          const selected = state.selected || state.items[0]?.id;
          if (selected) await loadDetail(selected);
          else clearDetail(admin ? "当前没有待查看的申请" : "先准备一份草稿", admin ? "员工提交后即可在此查看和处理。" : "在排期与变更助手中完成校验并确认创建后，草稿会出现在这里。");
        }
      } catch (error) {
        ui.list.replaceChildren(element("p", "review-list-empty", "列表读取失败，请点击刷新列表。"));
        ui.list.setAttribute("aria-busy", "false");
        notice(error.message, { refresh: true });
      } finally {
        state.loadingList = false;
        ui.refresh.disabled = state.busy;
        ui.filter.disabled = state.busy;
        syncSummaryFilter();
      }
    }

    function syncSummaryFilter() {
      if (!admin) return;
      for (const button of ui.distribution.querySelectorAll("button")) {
        button.disabled = state.busy || state.loadingList;
        button.setAttribute("aria-pressed", String(button.dataset.status === ui.filter.value));
      }
    }

    async function loadSummary() {
      if (!admin) return;
      const request = ++state.summaryRequest;
      ui.analytics.setAttribute("aria-busy", "true");
      ui["analytics-error"].hidden = true;
      try {
        const data = await api(`${base}/summary?days=${encodeURIComponent(ui["trend-days"].value)}`);
        if (request !== state.summaryRequest) return;
        if (!Array.isArray(data?.trend) || !data.counts || !Number.isInteger(data.total)) throw new Error("概览数据格式异常，请刷新重试。");
        window.VenueVisuals.distribution(ui.distribution, data, status => {
          if (state.busy || state.loadingList) return;
          ui.filter.value = status;
          ui.filter.dispatchEvent(new Event("change"));
        });
        window.VenueVisuals.trend(ui.trend, data);
        const venues = data.reviewable_venues || [];
        ui["analytics-compact"].textContent = venues.length ? `待审核 ${data.counts.submitted} 份 · 已通过 ${data.counts.approved} 份 · 已退回 ${data.counts.returned} 份` : "尚未开通场馆审核权限。";
        ui["analytics-scope"].textContent = venues.length ? `统计范围：${venues.map(item => item.name && item.name !== item.id ? `${item.name}（${item.id}）` : item.id).join("、")} · 全部授权申请` : "尚未开通场馆审核权限，当前没有可统计的申请。";
        ui["analytics-footnote"].textContent = `状态按申请份数统计；趋势按操作次数统计，包含重复提交。日期范围 ${data.date_from} 至 ${data.date_to}（北京时间）。更新于 ${date(data.generated_at)}。审批通过不代表正式占档。`;
        syncSummaryFilter();
      } catch (error) {
        if (request !== state.summaryRequest) return;
        ui.distribution.replaceChildren(element("p", "viz-note", "申请状态暂不可用。"));
        window.VenueVisuals.clearTrend(ui.trend);
        ui.trend.replaceChildren(element("p", "viz-note", "趋势暂不可用。"));
        ui["analytics-scope"].textContent = "尚未取得当前统计范围。";
        ui["analytics-compact"].textContent = "统计暂不可用，请展开查看并刷新重试。";
        ui["analytics-error"].textContent = `${error.message} 点击“刷新列表”可同时重试概览。`;
        ui["analytics-error"].hidden = false;
      } finally { if (request === state.summaryRequest) ui.analytics.setAttribute("aria-busy", "false"); }
    }

    function prepareDecision(decision) {
      if (state.busy || !state.detail) return;
      const actions = state.detail.actions || {};
      if (decision === "approve" && (!actions.can_approve || !ui.acknowledge.checked)) return;
      if (decision === "return") {
        if (!actions.can_return) return;
        if (!ui.comment.value.trim()) { notice("请写明退回原因，便于员工调整申请。"); ui.comment.focus(); return; }
      }
      if (decision === "submit" && !actions.can_submit) return;
      state.decision = { decision, id: state.detail.id, version: state.detail.version, reviewHash: state.detail.review_hash, comment: ui.comment?.value.trim() || "" };
      const titles = { approve: "确认通过本版本申请？", return: "确认退回本版本申请？", submit: "确认提交本版本草稿？" };
      const copies = {
        approve: `通过后将保存审核结果和审批记录，不自动占档或修改原排期。${state.decision.comment ? `\n审核意见：${state.decision.comment}` : ""}`,
        return: `申请将退回并保存以下原因：\n${state.decision.comment}`,
        submit: "提交后，本版本草稿进入管理员待审核列表。提交与审批都不会自动占用资源。",
      };
      ui["confirmation-title"].textContent = titles[decision];
      ui["confirmation-copy"].textContent = copies[decision];
      ui.confirm.textContent = { approve: "确认通过审批", return: "确认退回", submit: "确认提交审核" }[decision];
      ui.confirmation.hidden = false;
      updateButtons();
      ui.confirmation.scrollIntoView({ block: "nearest", behavior: "instant" });
      ui.confirm.focus({ preventScroll: true });
    }

    async function saveDecision() {
      const pending = state.decision;
      if (!pending || state.busy) return;
      setBusy(true);
      ui.confirm.textContent = "正在保存…";
      try {
        const submitting = pending.decision === "submit";
        const body = submitting ? { expected_version: pending.version } : {
          expected_version: pending.version, decision: pending.decision, comment: pending.comment,
          ...(pending.decision === "approve" ? { review_hash: pending.reviewHash } : {}),
        };
        const detail = await api(`${base}/${encodeURIComponent(pending.id)}/${submitting ? "submit" : "decision"}`, { method: "POST", body: JSON.stringify(body) });
        renderDetail(detail);
        notice({ submit: "草稿已提交审核。", approve: "审批结果已保存：通过。资源占用和原排期未改变。", return: "申请已退回，退回原因已保存。" }[pending.decision], { success: true });
        setBusy(false);
        await Promise.allSettled([loadList({ keepDetail: true }), loadSummary()]);
        ui["detail-panel"].focus({ preventScroll: true });
      } catch (error) {
        setBusy(false);
        resetConfirmation();
        if (error.status === 409) {
          notice(`${error.message} 内容或状态已变化，正在刷新；请重新核对后操作。`, { refresh: true });
          await loadDetail(pending.id, { retainComment: true });
        } else notice(error.message, { refresh: true });
      } finally { setBusy(false); }
    }

    ui.filter.addEventListener("change", () => { state.selected = null; clearDetail(); notice(""); syncSummaryFilter(); loadList(); });
    ui.refresh.addEventListener("click", () => { notice(""); loadList(); loadSummary(); });
    if (admin) ui["trend-days"].addEventListener("change", loadSummary);
    ui["notice-refresh"].addEventListener("click", () => { notice(""); if (state.selected) loadDetail(state.selected, { retainComment: true }); else loadList(); });
    ui.cancel.addEventListener("click", () => { const previous = state.decision?.decision; resetConfirmation(); (previous === "submit" ? ui.submit : previous === "return" ? ui.return : ui.approve)?.focus(); });
    ui.confirm.addEventListener("click", saveDecision);
    if (ui.submit) ui.submit.addEventListener("click", () => prepareDecision("submit"));
    if (ui.approve) ui.approve.addEventListener("click", () => prepareDecision("approve"));
    if (ui.return) ui.return.addEventListener("click", () => prepareDecision("return"));
    if (ui.acknowledge) ui.acknowledge.addEventListener("change", updateButtons);
    if (ui.comment) ui.comment.addEventListener("input", () => { if (state.decision) resetConfirmation(); });
    window.addEventListener("storage", event => {
      if (event.key === "venue-policy-assistant:auth-revision") { root.replaceChildren(); window.location.reload(); }
    });
    window.addEventListener("pageshow", event => { if (event.persisted) { root.replaceChildren(); window.location.reload(); } });
    (async () => {
      try { state.user = await api("/auth/me"); state.csrf = state.user.csrf_token || state.csrf; await Promise.allSettled([loadList(), loadSummary()]); }
      catch (error) { notice(error.message, { refresh: true }); ui.list.replaceChildren(element("p", "review-list-empty", "身份核验未完成，请重新登录后查看。")); }
    })();
  }

  window.VenueApprovalWorkspace = Object.freeze({ start });
  if (document.getElementById("review-workspace")?.dataset.mode === "admin") start("admin");
})();
