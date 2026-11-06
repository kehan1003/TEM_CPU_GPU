#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控屏本地服务
==============

两件事：
  1. 每 0.5 秒采一次 CPU / 内存 / GPU（采集逻辑在 collect.py，零依赖）；
  2. 把 index.html 和 /api/stats 一起用 HTTP 发出去。

浏览器打开 http://127.0.0.1:8770/ ，把窗口拖到小屏幕，按 F 或 F11 全屏。

用法:
    python server.py                # 真实数据
    python server.py --demo         # 假数据（纯看 UI）
    python server.py --port 8770    # 换端口
    python server.py --hz 2         # 采集频率
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# pythonw.exe 下 sys.stdout / sys.stderr 是 None，print() 会抛异常把服务弄挂。
# 用无窗口启动器跑的时候，把日志重定向到 server.log。
if sys.stdout is None or sys.stderr is None:
    try:
        _log = open(os.path.join(HERE, "server.log"), "a", buffering=1,
                    encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = _log
        if sys.stderr is None:
            sys.stderr = _log
    except OSError:
        pass

STATE = {"data": None, "ts": 0.0, "error": None, "sources": ""}
LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# 采集线程
# ----------------------------------------------------------------------------

def publish(data: dict) -> None:
    data["ts"] = time.time()
    with LOCK:
        STATE["data"] = data
        STATE["ts"] = data["ts"]
        STATE["error"] = None


def demo_loop(hz: float) -> None:
    """没有硬件 / 只想看 UI 时用：造几条像样的曲线。"""
    t0 = time.time()
    seq = 0
    period = 1.0 / max(0.2, hz)
    while True:
        t = time.time() - t0
        seq += 1
        publish({
            "seq": seq,
            "cpu_load": int(45 + 30 * math.sin(t / 3.0)),
            "cpu_temp_c10": int((62 + 6 * math.sin(t / 7.0)) * 10),
            "cpu_clk": 3600 + int(200 * math.sin(t / 5.0)),
            "mem_load": int(58 + 8 * math.sin(t / 11.0)),
            "mem_used": int(19000 + 2000 * math.sin(t / 11.0)),
            "mem_total": 32768,
            "gpu_load": int(70 + 25 * math.sin(t / 2.5)),
            "gpu_temp_c10": int((70 + 5 * math.sin(t / 6.0)) * 10),
            "gpu_used": int(6000 + 1500 * math.sin(t / 9.0)),
            "gpu_total": 16384,
            "gpu_power": 110 + 25 * math.sin(t / 4.0),
        })
        time.sleep(period)


def real_loop(hz: float) -> None:
    from collect import Collector

    c = Collector()
    time.sleep(0.4)                     # 让 CPU 占用率有个采样间隔
    # 先采一帧再报告数据源：LHM 是否可用要等第一次读取才知道，
    # 否则启动横幅会永远写着 no-cpu-temp。
    try:
        publish(c.sample())
    except Exception as exc:            # noqa: BLE001
        with LOCK:
            STATE["error"] = repr(exc)
        print(f"[warn] 首帧采样失败: {exc}", file=sys.stderr)
    with LOCK:
        STATE["sources"] = c.describe_sources()
    print(f"[ok] 数据源: {c.describe_sources()}", flush=True)

    period = 1.0 / max(0.2, hz)
    while True:
        started = time.time()
        try:
            publish(c.sample())
            with LOCK:
                STATE["sources"] = c.describe_sources()
        except Exception as exc:        # noqa: BLE001
            with LOCK:
                STATE["error"] = repr(exc)
            print(f"[warn] 采样失败: {exc}", file=sys.stderr)
        time.sleep(max(0.0, period - (time.time() - started)))


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def do_GET(self):                   # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.rstrip("/").endswith("api/stats"):
            self.send_stats()
            return
        if path in ("/", ""):
            self.path = "/index.html"
        super().do_GET()

    def send_stats(self):
        with LOCK:
            data = STATE["data"]
            ts = STATE["ts"]
            error = STATE["error"]
            sources = STATE["sources"]

        if data is None:
            body = json.dumps({"alive": False, "sources": sources,
                               "error": error or "还没有数据"}).encode()
        else:
            payload = dict(data)
            payload["alive"] = (time.time() - ts) < 5.0
            payload["sources"] = sources
            if error:
                payload["error"] = error
            body = json.dumps(payload).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if self.path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # 静音访问日志
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="监控屏本地服务")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--hz", type=float, default=2.0, help="采集频率，默认 2Hz")
    ap.add_argument("--demo", action="store_true", help="用假数据看 UI")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    target = demo_loop if args.demo else real_loop
    th = threading.Thread(target=target, args=(args.hz,), daemon=True)
    th.start()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"[x] 端口 {args.port} 起不来（{exc}）。换个端口: --port 8781", file=sys.stderr)
        return 3

    url = f"http://127.0.0.1:{args.port}/"
    print(f"[ok] {url}")
    if args.no_open:
        print("     （--no-open：不自动开浏览器，手动打开上面的地址）")
    else:
        print("     浏览器会自动打开；没弹出来就手动打开上面的地址。")
    print("     拖到小屏幕 → 按 F 或 F11 全屏 → Esc 退出。Ctrl+C 结束服务。")

    if not args.no_open:
        def _open_browser():
            time.sleep(1.0)
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:               # noqa: BLE001
                pass
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bye]")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
