# -*- coding: utf-8 -*-
"""见微数据（jianweidata.com）MCP 客户端 —— 只用标准库，直接跑官方 server.bundle.mjs。

见微给的接入方式是 MCP server：`node server.bundle-1.3.0.mjs`，ApiKey 放环境变量
`JIANWEI_API_KEY`，stdin/stdout 走 JSON-RPC 2.0。官方文档写的是装进 WorkBuddy，
但 MCP 本身和客户端无关 —— 这里自己起子进程、自己发 JSON-RPC，就能在采集脚本里用。

用法：

    with JianweiClient(bundle=..., api_key=...) as cli:
        data = cli.call_json("search_announcements", {
            "scope": "招股书",
            "contentMust": "未缴纳 社会保险",
            "nearMode": 4,
            "sector": 1,
            "pageNum": 1,
        })

返回值是服务端 tool result 里的文本（一般是 JSON 字符串），`call_json()` 会解析它。
"""

import json
import os
import subprocess
import sys
import threading


class JianweiError(RuntimeError):
    """见微接口/协议层的失败。"""


def _resolve_node(node):
    """node 可执行文件：优先显式传，其次 PATH，最后试 DSH 自带 runtime。"""
    if node:
        return node
    for cand in ("node", "node.exe"):
        from shutil import which
        p = which(cand)
        if p:
            return p
    guess = os.path.expandvars(
        r"%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe")
    if os.path.exists(guess):
        return guess
    raise JianweiError("找不到 node（需要 Node.js 18+）")


class JianweiClient(object):
    def __init__(self, bundle, api_key="", node="", timeout=180, log=None):
        self.bundle = bundle
        self.api_key = api_key or os.environ.get("JIANWEI_API_KEY", "")
        self.node = _resolve_node(node)
        self.timeout = timeout
        self.log = log
        self._proc = None
        self._buf = ""
        self._lock = threading.Lock()
        self._pending = {}
        self._next_id = 1
        self._reader = None

    # ── 生命周期 ───────────────────────────────────────────
    def open(self):
        if self._proc:
            return self
        if not os.path.exists(self.bundle):
            raise JianweiError("找不到 MCP bundle：%s" % self.bundle)
        env = dict(os.environ)
        if self.api_key:
            env["JIANWEI_API_KEY"] = self.api_key
        env.setdefault("NODE_NO_WARNINGS", "1")
        self._proc = subprocess.Popen(
            [self.node, self.bundle],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=1, universal_newlines=True, encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if self.log:
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        init = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "lexify-collector", "version": "1.0.0"},
        })
        if "error" in init:
            raise JianweiError("initialize 失败：%s" % json.dumps(init["error"], ensure_ascii=False))
        self._notify("notifications/initialized", {})
        return self

    def close(self):
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    # ── 协议 ───────────────────────────────────────────────
    def _read_loop(self):
        proc = self._proc
        while proc and proc.poll() is None:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            mid = msg.get("id")
            if mid is None:
                continue
            with self._lock:
                slot = self._pending.pop(mid, None)
            if slot:
                slot.append(msg)

    def _drain_stderr(self):
        proc = self._proc
        while proc and proc.poll() is None:
            line = proc.stderr.readline()
            if not line:
                break
            if self.log:
                self.log(line.rstrip())

    def _send(self, method, params):
        if not self._proc:
            raise JianweiError("客户端还没 open()")
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            slot = self._pending[mid] = []
        payload = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        waited = 0.0
        while waited < self.timeout:
            if slot:
                return slot[0]
            threading.Event().wait(0.05)
            waited += 0.05
            if self._proc.poll() is not None:
                raise JianweiError("MCP 进程提前退出（code=%s）" % self._proc.returncode)
        raise JianweiError("%s 超时（%ss）" % (method, self.timeout))

    def _notify(self, method, params):
        self._proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                       ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    # ── 对外 API ──────────────────────────────────────────
    def tools(self):
        res = self._send("tools/list", {})
        if "error" in res:
            raise JianweiError("tools/list 失败：%s" % res["error"])
        return res.get("result", {}).get("tools", [])

    def call(self, name, arguments=None):
        """调一个工具，返回文本内容（服务端一般返回 JSON 字符串）。"""
        self.open()
        res = self._send("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in res:
            raise JianweiError("%s 调用失败：%s" % (name, json.dumps(res["error"], ensure_ascii=False)))
        result = res.get("result") or {}
        texts = [c.get("text", "") for c in (result.get("content") or [])
                 if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(t for t in texts if t)
        if result.get("isError"):
            raise JianweiError("%s 返回错误：%s" % (name, text[:400] or "(空)"))
        return text

    def call_json(self, name, arguments=None):
        text = self.call(name, arguments)
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
            if start >= 0:
                for end in range(len(text), start, -1):
                    try:
                        return json.loads(text[start:end])
                    except ValueError:
                        continue
            raise JianweiError("%s 返回的不是 JSON：%s" % (name, text[:300]))
