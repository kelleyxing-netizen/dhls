/* 登录与后台共用入口。JWT 由 HttpOnly Cookie 管理，JavaScript 只保留 CSRF 令牌。 */
(() => {
  "use strict";
  const notice = document.getElementById("auth-notice");
  const loginForm = document.getElementById("login-form");
  const logoutButton = document.getElementById("logout-button");

  function showNotice(message) {
    if (!notice) return;
    notice.textContent = message;
    notice.hidden = false;
  }

  // 仅保存服务端返回的账号标识；不将密码、JWT 或 CSRF 令牌写入浏览器持久存储。
  function clearRememberedSessions() {
    try {
      const prefix = "venue-policy-assistant:last-session";
      const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index));
      for (const key of keys) if (key === prefix || key?.startsWith(`${prefix}:`)) localStorage.removeItem(key);
      // 通知同一浏览器的其他工作页清除旧账号画面；此值只是变更信号，不含凭据。
      localStorage.setItem("venue-policy-assistant:auth-revision", `${Date.now()}:${Math.random()}`);
    } catch { /* 浏览器禁用存储时不影响登录和退出。 */ }
  }

  // 可复用：服务端始终返回中文错误信封，代理异常也不会把原始 HTML 插入页面。
  async function authRequest(path, options) {
    let response;
    try { response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options }); }
    catch { throw new Error("连接服务失败，请检查网络后重试。"); }
    let envelope;
    try { envelope = await response.json(); }
    catch { throw new Error("服务暂时无法处理请求，请稍后重试。"); }
    if (!response.ok || envelope?.code !== "OK") {
      const error = new Error(envelope?.message || "请求未完成，请稍后重试。");
      error.status = response.status;
      throw error;
    }
    return envelope.data;
  }

  function destination(user) {
    const next = new URLSearchParams(window.location.search).get("next");
    // 跳转目标只允许已知同源页面，拒绝外域 URL、双斜线和任意接口路径。
    if (next === "/") return "/";
    if (user?.role === "admin") {
      if (/^\/admin(?:\/(?:knowledge|prompts|conversations))?$/.test(next || "")) return next;
      return "/admin/knowledge";
    }
    return "/";
  }

  if (loginForm) {
    const username = document.getElementById("username");
    const password = document.getElementById("password");
    const submit = document.getElementById("login-submit");
    // 页面未加载脚本时保持按钮禁用，避免把表单按普通编码发给 JSON 登录接口。
    submit.disabled = false;
    let submitting = false;
    loginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting || !loginForm.reportValidity()) return;
      submitting = true;
      notice.hidden = true;
      submit.disabled = true;
      submit.textContent = "正在登录…";
      loginForm.setAttribute("aria-busy", "true");
      try {
        const data = await authRequest("/auth/login", {
          method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ username: username.value.trim(), password: password.value }),
        });
        password.value = "";
        clearRememberedSessions();
        window.location.replace(destination(data?.user || data));
      } catch (error) {
        password.value = "";
        showNotice(error.message);
        password.focus();
      } finally {
        submitting = false;
        submit.disabled = false;
        submit.textContent = "登录";
        loginForm.removeAttribute("aria-busy");
      }
    });
  }

  if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
      logoutButton.disabled = true;
      try {
        await authRequest("/auth/logout", {
          method: "POST", headers: { Accept: "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        });
        clearRememberedSessions();
        window.location.replace("/login");
      } catch (error) {
        if (error.status === 401) { clearRememberedSessions(); window.location.replace("/login"); return; }
        showNotice(error.message);
        logoutButton.disabled = false;
      }
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== "venue-policy-assistant:auth-revision") return;
      // 同源其他标签页换账号后，撤下旧管理员内容，再由服务端重新判断角色。
      document.querySelector(".admin-main")?.replaceChildren();
      document.querySelector(".admin-account")?.replaceChildren();
      window.location.reload();
    });
  }
  window.addEventListener("pageshow", (event) => {
    // 返回缓存页时重新鉴权，避免浏览器恢复退出前的管理画面。
    if (event.persisted) window.location.reload();
  });
})();
