/* ============================================================
   Lexify · AI 咨询入口（ai-chat.js + ai-chat.css）
   ------------------------------------------------------------
   右下角常驻一个「咨询」入口，点开是对话面板。后端 /api/ai/*
   已经把项目文件夹整理成知识库，这里只管展示：

     1. 入口默认隐藏，先问 /api/ai/status，服务端配了 Key 才出现。
     2. 流式输出走 SSE（fetch + ReadableStream），首字即出，不等整段。
     3. 气泡只认 **加粗** / `代码` / 短列表 / 链接，其余一律转义，
        不让模型输出成为 XSS 入口。
     4. 类名统一 ai- 前缀（站点共用一张全局样式表，裸类名会互相踩）。
   ============================================================ */
(function () {
  "use strict";
  if (window.__LEXIFY_AI__) return;
  window.__LEXIFY_AI__ = true;

  var SUGGESTIONS = [
    "你们有哪些看板？",
    "数据多久更新一次？",
    "可以导出成表格吗？",
    "怎么本地跑起来？"
  ];

  var state = {
    open: false,
    busy: false,
    history: [],      // [{role, content}]，只发最近 8 轮
    mounted: false
  };

  /* ---------------- 极简 Markdown ----------------
     只处理模型最常用的四种内联语法；先整体转义再插标签，顺序不能反。 */
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  function toHtml(text) {
    var lines = String(text).split("\n");
    var out = [], list = null, buf = [];
    function flushP() {
      if (buf.length) { out.push("<p>" + inline(buf.join(" ")) + "</p>"); buf = []; }
    }
    function flushL() {
      if (list) { out.push("<" + list.type + ">" + list.items.join("") + "</" + list.type + ">"); list = null; }
    }
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i], m;
      if ((m = ln.match(/^\s*[-*]\s+(.*)$/))) {
        flushP();
        if (!list || list.type !== "ul") { flushL(); list = { type: "ul", items: [] }; }
        list.items.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = ln.match(/^\s*\d+[.)]\s+(.*)$/))) {
        flushP();
        if (!list || list.type !== "ol") { flushL(); list = { type: "ol", items: [] }; }
        list.items.push("<li>" + inline(m[1]) + "</li>");
      } else if (!ln.trim()) {
        flushP(); flushL();
      } else {
        flushL();
        buf.push(ln.trim());
      }
    }
    flushP(); flushL();
    return out.join("");
  }

  /* ---------------- DOM ---------------- */
  var ICON_CHAT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M20 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 3.5V6a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 6z"/>' +
    '<path d="M8.5 8.5h7M8.5 12h4.5"/></svg>';

  var root, panel, log, input, sendBtn, statusEl;

  function build() {
    root = document.createElement("div");
    root.className = "ai-root";
    root.innerHTML =
      '<button type="button" class="ai-fab" id="aiFab" aria-label="AI 咨询" aria-expanded="false" hidden>' +
        '<span class="ai-fab-ico">' + ICON_CHAT + "</span>" +
        '<span class="ai-fab-txt">咨询</span>' +
      "</button>" +

      '<section class="ai-panel" id="aiPanel" role="dialog" aria-modal="false" ' +
        'aria-labelledby="aiTitle" hidden>' +
        '<header class="ai-head">' +
          '<div class="ai-head-main">' +
            '<span class="ai-avatar" aria-hidden="true">律</span>' +
            "<div>" +
              '<h2 id="aiTitle" class="ai-title">Lexify 咨询</h2>' +
              '<p class="ai-sub" id="aiStatus"><i class="ai-dot"></i>在线 · 随时为你解答</p>' +
            "</div>" +
          "</div>" +
          '<button type="button" class="ai-x" id="aiClose" aria-label="收起">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
            'stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
          "</button>" +
        "</header>" +
        '<div class="ai-log" id="aiLog" role="log" aria-live="polite"></div>' +
        '<form class="ai-compose" id="aiForm" autocomplete="off">' +
          '<textarea id="aiInput" class="ai-input" rows="1" maxlength="2000" ' +
            'placeholder="问问看板、数据、用法……" aria-label="你的问题"></textarea>' +
          '<button type="submit" class="ai-send" id="aiSend" aria-label="发送">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
            'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M12 5l7 7-7 7"/></svg>' +
          "</button>" +
        "</form>" +
        '<p class="ai-foot">回答仅供了解平台参考。</p>' +
      "</section>";
    document.body.appendChild(root);

    panel = document.getElementById("aiPanel");
    log = document.getElementById("aiLog");
    input = document.getElementById("aiInput");
    sendBtn = document.getElementById("aiSend");
    statusEl = document.getElementById("aiStatus");

    document.getElementById("aiFab").addEventListener("click", toggle);
    document.getElementById("aiClose").addEventListener("click", function () { setOpen(false); });
    document.getElementById("aiForm").addEventListener("submit", function (e) {
      e.preventDefault();
      ask(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        ask(input.value);
      }
    });
    input.addEventListener("input", autoGrow);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && state.open) setOpen(false);
    });
    state.mounted = true;
    greet();
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 132) + "px";
  }

  function setOpen(v) {
    state.open = v;
    var fab = document.getElementById("aiFab");
    panel.hidden = !v;
    fab.setAttribute("aria-expanded", v ? "true" : "false");
    fab.classList.toggle("ai-fab--on", v);
    if (v) setTimeout(function () { input.focus(); }, 30);
  }

  function toggle() { setOpen(!state.open); }

  /* ---------------- 气泡 ---------------- */
  function bubble(role, html) {
    var wrap = document.createElement("div");
    wrap.className = "ai-msg ai-msg--" + role;
    if (role === "ai") {
      var av = document.createElement("span");
      av.className = "ai-msg-av";
      av.textContent = "律";
      wrap.appendChild(av);
    }
    var body = document.createElement("div");
    body.className = "ai-msg-body";
    body.innerHTML = html;
    wrap.appendChild(body);
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
    return body;
  }

  function greet() {
    var box = bubble("ai", "");
    box.innerHTML =
      "<p>Hi，我是 <strong>Lexify 法律数据洞察平台</strong>智能助手 <strong>乐菲</strong> ~<br>" +
      "欢迎随时提问</p>" +
      '<div class="ai-chips">' +
      SUGGESTIONS.map(function (s) {
        return '<button type="button" class="ai-chip" data-q="' + esc(s) + '">' + esc(s) + "</button>";
      }).join("") +
      "</div>";
    var chips = box.querySelectorAll(".ai-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].addEventListener("click", function () { ask(this.getAttribute("data-q")); });
    }
  }

  function typing() {
    var body = bubble("ai", '<span class="ai-typing"><i></i><i></i><i></i></span>');
    return body;
  }

  function setBusy(v) {
    state.busy = v;
    sendBtn.disabled = v;
    input.disabled = v;
    panel.classList.toggle("ai-panel--busy", v);
  }

  function setStatus(text, tone) {
    statusEl.className = "ai-sub" + (tone ? " ai-sub--" + tone : "");
    statusEl.innerHTML = '<i class="ai-dot"></i>' + esc(text);
  }

  /* ---------------- 发送 ---------------- */
  function ask(text) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    if (!state.open) setOpen(true);
    input.value = "";
    autoGrow();

    bubble("user", "<p>" + inline(text) + "</p>");
    state.history.push({ role: "user", content: text });
    setBusy(true);
    setStatus("正在为你解答…", "busy");

    var body = typing();
    var acc = "";

    function fail(msg) {
      body.innerHTML = '<p class="ai-err">' + esc(msg) + "</p>";
      setStatus("暂时没答上来，可以重试", "err");
      setBusy(false);
      input.focus();
    }

    fetch("api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify({
        message: text,
        history: state.history.slice(-8).slice(0, -1),
        stream: true
      })
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.error || ("请求失败（" + res.status + "）"));
        });
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buffer = "";

      function handle(block) {
        var ev = "message", data = "";
        block.split("\n").forEach(function (line) {
          if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
        });
        if (!data) return;
        var obj;
        try { obj = JSON.parse(data); } catch (e) { return; }
        if (ev === "delta" && obj.t) {
          if (!acc) body.innerHTML = "";
          acc += obj.t;
          body.innerHTML = toHtml(acc);
          log.scrollTop = log.scrollHeight;
        } else if (ev === "error") {
          throw new Error(obj.error || "模型返回失败");
        }
      }

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) {
            if (buffer.trim()) handle(buffer);
            return;
          }
          buffer += decoder.decode(r.value, { stream: true });
          var idx;
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            var block = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            handle(block);
          }
          return pump();
        });
      }
      return pump();
    }).then(function () {
      if (!acc) {
        body.innerHTML = '<p class="ai-err">这次没有返回内容，换个问法试试。</p>';
        setStatus("暂时没答上来，可以重试", "err");
      } else {
        state.history.push({ role: "assistant", content: acc });
        setStatus("在线 · 随时为你解答");
      }
      setBusy(false);
      input.focus();
    }).catch(function (e) {
      fail(e && e.message ? e.message : "网络异常，请稍后再试。");
    });
  }

  /* ---------------- 启动 ----------------
     站点可能以 file:// 打开（README 明确支持），此时没有后端，
     直接不显示入口，而不是弹一个永远连不上的对话框。 */
  function boot() {
    if (location.protocol === "file:") return;
    fetch("api/ai/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : { enabled: false }; })
      .then(function (st) {
        if (!st.enabled) return;
        build();
        document.getElementById("aiFab").hidden = false;
      })
      .catch(function () { /* 后端没起：静默隐藏 */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
