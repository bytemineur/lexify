/* 访问统计上报：只记「哪个页面被打开、从哪来、停了多久」，不记账号也不记原始 IP。
 * 后台页不统计；尊重 DNT；出错一律静默 —— 统计永远不该影响页面本身。 */
(function () {
  "use strict";
  try {
    var path = location.pathname;
    if (path === "/admin" || path.indexOf("/admin/") === 0) return;
    var dnt = navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack;
    if (dnt === "1" || dnt === "yes") return;
    if (navigator.webdriver) return;
    if (/[?&]notrack=1/.test(location.search)) return;

    function rand(n) {
      var s = "", a = "0123456789abcdefghijklmnopqrstuvwxyz";
      for (var i = 0; i < n; i++) s += a.charAt(Math.floor(Math.random() * a.length));
      return s;
    }

    var sid = "";
    try {
      sid = sessionStorage.getItem("lx.sid") || "";
      if (!sid) { sid = rand(16); sessionStorage.setItem("lx.sid", sid); }
    } catch (e) { sid = rand(16); }

    var vid = sid + "-" + rand(8);
    var t0 = Date.now();
    var sentLeave = false;

    function post(kind, extra) {
      var data = {
        event: kind, sid: sid, vid: vid, path: path,
        query: String(location.search || "").slice(0, 200),
        title: String(document.title || "").slice(0, 120),
        referrer: String(document.referrer || "").slice(0, 300),
        w: window.innerWidth || 0, h: window.innerHeight || 0,
        lang: navigator.language || ""
      };
      for (var k in extra) if (extra.hasOwnProperty(k)) data[k] = extra[k];
      var body = JSON.stringify(data);
      if (kind === "leave" && navigator.sendBeacon) {
        try {
          navigator.sendBeacon("/api/track", new Blob([body], { type: "application/json" }));
          return;
        } catch (e) { /* 落到 fetch */ }
      }
      try {
        fetch("/api/track", {
          method: "POST", keepalive: true, credentials: "omit",
          headers: { "Content-Type": "application/json" }, body: body
        }).catch(function () {});
      } catch (e) { /* 静默 */ }
    }

    post("view");

    function leave() {
      if (sentLeave) return;
      sentLeave = true;
      var d = Date.now() - t0;
      if (d > 0) post("leave", { dwell: d });
    }
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") leave();
    });
  } catch (e) { /* 静默 */ }
})();
