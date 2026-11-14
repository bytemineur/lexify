# -*- coding: utf-8 -*-
"""跨平台底座：控制台编码、子进程环境、解释器定位。

只管三件事，别往里堆业务。

为什么需要它
------------
Windows / macOS / Linux 三个平台上，Python 脚本最容易在这三处翻车：

1. **控制台编码**。Windows 控制台默认代码页是 GBK（简中）或 CP437（英文版），
   直接 `print("✓ 完成")` 会抛 `UnicodeEncodeError` 把进程打死；
   重定向到文件时更糟——用的是 locale 编码，日志里全是问号。
2. **子进程输出编码**。`subprocess` 走管道时，子进程按 locale 编码写，
   父进程按 UTF-8 读，中文全变乱码。必须给子进程灌 `PYTHONIOENCODING=utf-8`。
3. **解释器路径**。Linux/macOS 上是 `python3`，Windows 上是 `python`/`py`，
   直接用字符串 "python" 调子进程，在 Debian 上会 `FileNotFoundError`。
   `sys.executable` 是唯一可靠来源。

用法
----
    from compat import setup_console, child_env, python_exe

    setup_console()                                   # 进程启动第一件事
    subprocess.run([python_exe(), "x.py"], env=child_env())
"""
import os
import sys

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"


def setup_console():
    """把 stdout/stderr 钉到 UTF-8，且永不因编码问题崩。

    顺序有讲究：先改 Windows 控制台代码页（否则写出去的 UTF-8 字节会被按 GBK 显示成乱码），
    再 reconfigure 流对象本身。reconfigure 在 3.7+ 都有，包一层 try 是为了兼容被替换过的流。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleOutputCP(65001)
            k.SetConsoleCP(65001)
        except Exception:
            pass

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:                       # pythonw.exe 下 stdout 是 None
            continue
        try:
            # line_buffering 必须开：输出重定向到管道/日志文件时默认是块缓冲，
            # 启动信息要攒满 8KB 才落盘，`journalctl -f` 看着像卡死了。
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


def child_env(base=None):
    """给子进程用的环境变量：强制 UTF-8 + 不缓冲，避免乱码和「看不到进度」。"""
    env = dict(os.environ) if base is None else dict(base)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # 有些 Windows 机器装了多个解释器，PATH 里第一个不一定是当前这个；
    # 采样子进程（crawl.py / cli.py）用同一套依赖，指向同一个解释器最稳。
    exe = python_exe()
    d = os.path.dirname(exe)
    if d and d not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
    return env


def python_exe():
    """当前解释器的绝对路径；冻结环境等极端情况下退回平台惯用名。"""
    exe = sys.executable or ""
    if exe and os.path.exists(exe):
        return exe
    return "python" if IS_WINDOWS else "python3"


def describe():
    """启动时打一行，排错时一眼看出跑在哪。"""
    return "Python %s on %s" % (sys.version.split()[0], sys.platform)
