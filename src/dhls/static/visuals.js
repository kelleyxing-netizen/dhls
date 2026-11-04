/* 本地 SVG 业务图表。只渲染服务端数据，不改写业务、不加载第三方资源。 */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const statuses = { submitted: "待审核", approved: "已通过", returned: "已退回" };
  const trendViews = new WeakMap();
  function node(tag, cls, text) {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  function svgNode(tag, attrs = {}, text) {
    const item = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, String(value));
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  function svg(height, label, width = 720) {
    return svgNode("svg", { viewBox: `0 0 ${width} ${height}`, class: "viz-svg", role: "img", "aria-label": label });
  }
  function time(value) {
    const stamp = new Date(value);
    return Number.isFinite(stamp.getTime()) ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(stamp) : "时间未提供";
  }
  function duration(start, end) {
    const minutes = Math.round((end - start) / 60000);
    return minutes >= 60 ? `${Math.floor(minutes / 60)}小时${minutes % 60 ? `${minutes % 60}分` : ""}` : `${minutes}分钟`;
  }
  function validRange(item) {
    const start = Date.parse(item?.start_at), end = Date.parse(item?.end_at);
    return Number.isFinite(start) && Number.isFinite(end) && end > start ? { ...item, start, end } : null;
  }
  function extent(rows) {
    const start = Math.min(...rows.map(row => row.start)), end = Math.max(...rows.map(row => row.end));
    const pad = Math.max((end - start) * .06, 900000);
    return [start - pad, end + pad];
  }
  function lane(item, range, kind, overlap = null) {
    const scale = value => Math.max(0, Math.min(720, (value - range[0]) / (range[1] - range[0]) * 720));
    const graphic = svg(36, `${item.label}：${time(item.start_at)}至${time(item.end_at)}`);
    graphic.append(svgNode("rect", { x: 0, y: 7, width: 720, height: 22, rx: 3, class: "viz-track" }));
    for (let i = 1; i < 4; i++) graphic.append(svgNode("line", { x1: i * 180, x2: i * 180, y1: 4, y2: 32, class: "viz-gridline" }));
    graphic.append(svgNode("rect", { x: scale(item.start), y: 8, width: scale(item.end) - scale(item.start), height: 20, rx: 3, class: `viz-interval ${kind}` }));
    if (overlap) {
      const start = Math.max(item.start, overlap.start), end = Math.min(item.end, overlap.end);
      if (end > start) graphic.append(svgNode("rect", { x: scale(start), y: 8, width: scale(end) - scale(start), height: 20, rx: 2, class: "viz-overlap" }));
    }
    return graphic;
  }
  function axis(range) {
    const item = node("div", "viz-time-axis");
    item.append(node("span", "", time(range[0])), node("span", "", time(range[1])));
    return item;
  }
  function schedule(container, detail) {
    if (!container) return;
    container.replaceChildren();
    const candidate = validRange(detail.candidate), baseline = validRange(detail.baseline);
    if (!candidate) { container.append(node("p", "viz-note", "暂无可绘制的申请时段，请核对完整申请内容。")); return; }
    candidate.label = baseline ? "申请方案" : "申请时段";
    const rows = baseline ? [{ ...baseline, label: "原排期基线" }, candidate] : [candidate];
    const range = extent(rows);
    const separate = rows.length > 1 && (range[1] - range[0]) / Math.min(...rows.map(row => row.end - row.start)) > 48;
    container.append(node("h4", "viz-title", baseline ? "排期时间对比" : "申请时段"));
    container.append(node("p", "viz-note", separate ? "跨日期调整：各行独立缩放，请按标注的日期和时长比较。时间均为北京时间。" : "各行采用同一时间刻度 · 北京时间"));
    for (const [index, item] of rows.entries()) {
      const row = node("div", "viz-time-row");
      const heading = node("div", "viz-row-heading");
      heading.append(node("strong", "", item.label), node("span", "", `${duration(item.start, item.end)} · ${item.attendees ?? "—"}人`));
      row.append(heading, node("p", "viz-time-label", `${time(item.start_at)} — ${time(item.end_at)}`), node("p", "viz-resource-label", item.resource_id || ""));
      const bounds = separate ? extent([item]) : range;
      row.append(lane(item, bounds, baseline && index === 0 ? "viz-baseline" : "viz-proposed"));
      if (separate) row.append(axis(bounds));
      container.append(row);
    }
    if (!separate) container.append(axis(range));
    if (baseline && (baseline.resource_id !== candidate.resource_id || baseline.attendees !== candidate.attendees)) {
      container.append(node("p", "viz-change-note", [baseline.resource_id !== candidate.resource_id ? `资源：${baseline.resource_id} → ${candidate.resource_id}` : "", baseline.attendees !== candidate.attendees ? `人数：${baseline.attendees} → ${candidate.attendees}人` : ""].filter(Boolean).join("；")));
    }
  }
  function assessment(container, detail) {
    if (!container) return;
    container.replaceChildren();
    const data = detail.assessment || {}, candidate = validRange(detail.candidate);
    const cap = data.capacity, attendees = detail.candidate?.attendees;
    const capacity = node("div", "viz-capacity");
    const heading = node("div", "viz-row-heading");
    heading.append(node("strong", "", "申请人数 / 资源容量"), node("span", "viz-capacity-value", Number.isFinite(cap) ? `${attendees} / ${cap} 人` : "容量待核实"));
    capacity.append(heading);
    if (Number.isFinite(cap) && cap > 0 && Number.isFinite(attendees)) {
      const over = attendees > cap;
      capacity.dataset.over = String(over);
      const graphic = svg(16, `申请${attendees}人，容量${cap}人${over ? `，超出${attendees - cap}人` : `，余量${cap - attendees}人`}`);
      graphic.append(svgNode("rect", { width: 720, height: 12, y: 2, rx: 3, class: "viz-track" }), svgNode("rect", { width: Math.min(1, attendees / cap) * 720, height: 12, y: 2, rx: 3, class: over ? "viz-overlap" : "viz-proposed" }));
      capacity.append(graphic, node("p", "viz-note", over ? `超出容量 ${attendees - cap} 人，需调整申请。` : `申请人数占容量 ${Math.round(attendees / cap * 100)}%，尚余 ${cap - attendees} 人。`));
    } else capacity.append(node("p", "viz-note", "容量信息尚不完整，请结合下方复核问题处理。"));
    container.append(capacity);
    const conflicts = (data.conflicts || []).map(validRange).filter(Boolean);
    if (!candidate || !conflicts.length) return;
    const shown = conflicts.slice(0, 3), range = extent([candidate, ...shown]);
    const block = node("div", "viz-conflict-chart");
    block.append(node("h4", "viz-title", "资源时段冲突"), node("p", "viz-note", "红色为与申请重叠的部分 · 同一时间刻度 · 北京时间"));
    for (const [index, item] of [candidate, ...shown].entries()) {
      item.label = index === 0 ? "申请方案" : `已有占用 ${index}`;
      const heading = node("div", "viz-row-heading");
      heading.append(node("strong", "", item.label), node("span", "", index ? `重叠 ${duration(Math.max(item.start, candidate.start), Math.min(item.end, candidate.end))}` : duration(item.start, item.end)));
      block.append(heading, node("p", "viz-time-label", `${time(item.start_at)} — ${time(item.end_at)}`), lane(item, range, index ? "viz-baseline" : "viz-proposed", index ? candidate : null));
    }
    block.append(axis(range));
    if (conflicts.length > shown.length) block.append(node("p", "viz-note", `另有 ${conflicts.length - shown.length} 条已返回冲突，见下方时段列表。`));
    container.append(block);
  }
  function distribution(container, data, select) {
    container.replaceChildren();
    const total = data.total || 0, counts = data.counts || {};
    const ledger = node("div", "viz-state-ledger");
    for (const [key, label] of Object.entries(statuses)) {
      const button = node("button", "viz-state-button"); button.type = "button"; button.dataset.status = key;
      button.setAttribute("aria-label", `${label}${counts[key] || 0}份，筛选${label}申请`);
      button.append(node("span", "viz-state-name", label), node("strong", "viz-state-count", counts[key] || 0), node("span", "viz-unit", "份"));
      button.addEventListener("click", () => select(key)); ledger.append(button);
    }
    container.append(ledger);
    const graphic = svg(22, total ? `已提交申请共${total}份，各状态数量见上方按钮` : "暂无已提交申请");
    graphic.append(svgNode("rect", { width: 720, height: 16, y: 3, rx: 3, class: "viz-track" }));
    let left = 0;
    for (const key of Object.keys(statuses)) {
      const width = total ? (counts[key] || 0) / total * 720 : 0;
      if (width) graphic.append(svgNode("rect", { x: left, width, height: 16, y: 3, class: `viz-state-${key}` }));
      left += width;
    }
    container.append(graphic, node("p", "viz-note", total ? `共 ${total} 份已提交申请 · 当前状态，不受下方列表筛选影响` : "尚无已提交申请。员工提交后，这里会显示实际数量。"));
  }
  function trend(container, data) {
    const width = Math.max(280, Math.min(720, container.clientWidth || 480));
    let view = trendViews.get(container);
    if (!view) {
      view = { data, width };
      trendViews.set(container, view);
      if ("ResizeObserver" in window) {
        view.observer = new ResizeObserver(() => {
          if (!container.isConnected) { clearTrend(container); return; }
          const next = Math.max(280, Math.min(720, container.clientWidth || 480));
          if (Math.abs(next - view.width) > 2) trend(container, view.data);
        });
        view.observer.observe(container);
      }
    }
    view.data = data; view.width = width;
    container.replaceChildren();
    const rows = data.trend || [];
    if (!rows.length) { container.append(node("p", "viz-note", "暂无趋势数据。")); return; }
    const legend = node("div", "viz-trend-legend");
    legend.append(node("span", "viz-legend-submitted", "提交次数"), node("span", "viz-legend-processed", "处理次数（通过＋退回）"));
    const max = Math.max(1, ...rows.flatMap(row => [row.submitted, row.processed]));
    const ceiling = max <= 4 ? 4 : Math.ceil(max / 4) * 4;
    const graphic = svg(176, `最近${data.days}天提交与处理次数，支持聚焦数据点查看数值，或展开逐日数据表。`, width);
    graphic.setAttribute("role", "group");
    const x = index => 32 + index / Math.max(1, rows.length - 1) * (width - 42), y = count => 140 - count / ceiling * 124;
    for (let i = 0; i <= 4; i++) {
      const count = ceiling * i / 4, height = y(count);
      graphic.append(svgNode("line", { x1: 32, x2: width - 10, y1: height, y2: height, class: "viz-gridline" }), svgNode("text", { x: 22, y: height + 4, "text-anchor": "end", class: "viz-axis-text" }, count));
    }
    for (const key of ["submitted", "processed"]) graphic.append(svgNode("path", { d: rows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row[key])}`).join(" "), class: `viz-trend-${key}`, fill: "none" }));
    const readout = node("p", "viz-trend-readout");
    const describe = row => `${row.date} · 提交 ${row.submitted} 次 · 处理 ${row.processed} 次`;
    readout.textContent = describe(rows[rows.length - 1]); readout.setAttribute("aria-live", "polite");
    rows.forEach((row, index) => {
      if ([0, Math.floor((rows.length - 1) / 2), rows.length - 1].includes(index)) graphic.append(svgNode("text", { x: x(index), y: 165, "text-anchor": index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle", class: "viz-axis-text" }, row.date.slice(5).replace("-", "/")));
      const group = svgNode("g", { tabindex: 0, role: "button", class: "viz-trend-point", "aria-label": describe(row) });
      const hitWidth = Math.min(24, (width - 42) / rows.length);
      group.append(svgNode("title", {}, describe(row)), svgNode("rect", { x: x(index) - hitWidth / 2, y: 2, width: hitWidth, height: 142, fill: "transparent", rx: 3, class: "viz-point-target" }));
      for (const key of ["submitted", "processed"]) group.append(svgNode("circle", { cx: x(index), cy: y(row[key]), r: 3.5, class: `viz-point-${key}` }));
      const show = () => { readout.textContent = describe(row); };
      group.addEventListener("mouseenter", show); group.addEventListener("focus", show); group.addEventListener("click", show);
      group.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); show(); } });
      graphic.append(group);
    });
    container.append(legend, graphic, readout);
    if (!rows.some(row => row.submitted || row.processed)) container.append(node("p", "viz-note", "所选日期范围内暂无提交或处理记录。"));
    const details = node("details", "viz-data-details"), table = node("table", "viz-data-table");
    details.append(node("summary", "", "查看逐日数据"));
    table.append(node("caption", "sr-only", "按北京时间统计的提交和处理次数"));
    const header = node("tr"); for (const label of ["日期", "提交次数", "处理次数"]) { const cell = node("th", "", label); cell.scope = "col"; header.append(cell); }
    const head = node("thead"), body = node("tbody"); head.append(header);
    for (const row of rows) { const tr = node("tr"); tr.append(node("td", "", row.date), node("td", "", row.submitted), node("td", "", row.processed)); body.append(tr); }
    table.append(head, body); details.append(table); container.append(details);
  }
  function clearTrend(container) {
    trendViews.get(container)?.observer?.disconnect();
    trendViews.delete(container);
  }
  function disclosure({ button, body, root, compact = null, key, label }) {
    const media = window.matchMedia("(max-width: 700px)");
    let preference = null;
    try { const saved = localStorage.getItem(key); if (["collapsed", "expanded"].includes(saved)) preference = saved; } catch { /* 无存储时采用当前屏幕默认。 */ }
    function update() {
      const collapsed = preference ? preference === "collapsed" : media.matches;
      if (collapsed && body.contains(document.activeElement)) button.focus();
      body.hidden = collapsed; root.dataset.collapsed = String(collapsed);
      if (compact) compact.hidden = !collapsed;
      button.setAttribute("aria-expanded", String(!collapsed));
      button.textContent = `${collapsed ? "展开" : "收起"}${label}`;
    }
    button.addEventListener("click", () => {
      preference = body.hidden ? "expanded" : "collapsed";
      try { localStorage.setItem(key, preference); } catch { /* 当前页面仍然可切换。 */ }
      update();
    });
    media.addEventListener("change", update); update();
  }
  function motion() {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    let off = false; try { off = localStorage.getItem("venue-ui:motion") === "off"; } catch { /* 偏好不影响业务操作。 */ }
    const buttons = document.querySelectorAll("[data-motion-toggle]");
    function update() {
      document.documentElement.dataset.motion = off || media.matches ? "off" : "on";
      buttons.forEach(button => { button.disabled = media.matches; button.textContent = media.matches ? "已减少动态" : off ? "开启动效" : "关闭动效"; button.setAttribute("aria-pressed", String(off || media.matches)); });
    }
    buttons.forEach(button => button.addEventListener("click", () => { off = !off; try { localStorage.setItem("venue-ui:motion", off ? "off" : "on"); } catch { /* 当前页面仍生效。 */ } update(); }));
    media.addEventListener("change", update); update();
    document.addEventListener("visibilitychange", () => { document.documentElement.dataset.pageHidden = String(document.hidden); });
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(entries => entries.forEach(entry => { entry.target.dataset.visible = String(entry.isIntersecting); }));
      document.querySelectorAll(".process-steps").forEach(item => observer.observe(item));
    }
  }
  window.VenueVisuals = Object.freeze({ schedule, assessment, distribution, trend, clearTrend, disclosure });
  motion();
})();
