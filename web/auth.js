// 导航栏的登录状态：所有页面都引它。
// 只做两件事：把顶栏那个「登录 / 手机号」按钮刷成当前状态；点「退出」时调登出接口。
// 页面本身要不要拦，是服务端的事（未登录访问看板页会 302 到 login.html）。
(function () {
  var btn = document.getElementById("navLogin");
  if (!btn) return;
  // 登录页自己不需要这个按钮（点了还是回登录页）。
  if (/\/login\.html$/.test(location.pathname)) { btn.style.display = "none"; return; }

  function logout() {
    fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function () {
      location.href = "index.html";
    }).catch(function () {
      location.href = "index.html";
    });
  }

  function paint(state) {
    if (!state || !state.loginRequired) {
      btn.style.display = "none";
      return;
    }
    if (state.loggedIn) {
      btn.textContent = state.phone + " · 退出";
      btn.classList.add("is-user");
      btn.href = "#";
      btn.title = "点击退出登录";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        logout();
      });
    } else {
      btn.textContent = "登录";
      btn.href = "login.html?next=" + encodeURIComponent(location.pathname + location.search);
    }
  }

  fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(paint)
    .catch(function () { btn.style.display = "none"; });
})();
