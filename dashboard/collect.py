#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据采集（零安装版）
====================

有 psutil 就用 psutil（最省事、最准）；
没有 psutil 也能跑 —— 直接 ctypes 调 Win32 API 拿 CPU / 内存：

    CPU 占用率 : GetSystemTimes() 两次采样做差（和任务管理器同源）
    内存       : GlobalMemoryStatusEx()
    CPU 频率   : 注册表 HKLM\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0\\~MHz（基频）

GPU 一律走 nvidia-smi（NVIDIA 驱动自带，不用装东西）；
CPU 温度只能靠 LibreHardwareMonitor 的 Web 服务，没开就返回 None（屏幕显示 --）。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import threading
import urllib.request

IS_WIN = sys.platform == "win32"

LHM_URL = "http://localhost:8085/data.json"
TEMP_INVALID = None


# ----------------------------------------------------------------------------
# Win32 兜底实现
# ----------------------------------------------------------------------------

if IS_WIN:
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    _kernel32 = ctypes.windll.kernel32

    def _ft(v):
        return (v.dwHighDateTime << 32) + v.dwLowDateTime

    def _cpu_times():
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not _kernel32.GetSystemTimes(ctypes.byref(idle),
                                       ctypes.byref(kern),
                                       ctypes.byref(user)):
            return None
        i, k, u = _ft(idle), _ft(kern), _ft(user)
        total = k + u                       # kernel 时间里已经包含 idle
        return total - i, total             # (busy, total)

    def _mem():
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        mb = 1024 * 1024
        return {
            "load": int(st.dwMemoryLoad),
            "total_mb": int(st.ullTotalPhys // mb),
            "used_mb": int((st.ullTotalPhys - st.ullAvailPhys) // mb),
        }

    def _base_mhz():
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            value, _ = winreg.QueryValueEx(key, "~MHz")
            return int(value)
        except Exception:                   # noqa: BLE001
            return 0
else:
    def _cpu_times():
        return None

    def _mem():
        return None

    def _base_mhz():
        return 0


# ----------------------------------------------------------------------------
# GPU：nvidia-smi
# ----------------------------------------------------------------------------

class GpuSource:
    QUERY = ("utilization.gpu,temperature.gpu,memory.used,memory.total,"
             "power.draw,clocks.current.graphics")

    def __init__(self):
        self.exe = self._find()
        self._warned = False

    @staticmethod
    def _find():
        for cand in ("nvidia-smi",
                     r"C:\Windows\System32\nvidia-smi.exe",
                     r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            try:
                p = subprocess.run([cand, "-L"], capture_output=True,
                                   text=True, timeout=4,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if p.returncode == 0 and "GPU" in p.stdout:
                    return cand
            except (OSError, subprocess.SubprocessError):
                continue
        return None

    def read(self):
        out = {}
        if not self.exe:
            return out
        try:
            p = subprocess.run(
                [self.exe, f"--query-gpu={self.QUERY}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            if not self._warned:
                print(f"[warn] nvidia-smi 调用失败: {exc}", file=sys.stderr)
                self._warned = True
            return out
        if p.returncode != 0 or not p.stdout.strip():
            return out

        parts = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]

        def num(i):
            try:
                return float(parts[i])
            except (ValueError, IndexError):
                return None

        if (v := num(0)) is not None:
            out["load"] = v
        if (v := num(1)) is not None:
            out["temp"] = v
        if (v := num(2)) is not None:
            out["used_mb"] = v
        if (v := num(3)) is not None:
            out["total_mb"] = v
        if (v := num(4)) is not None:
            out["power"] = v
        return out


# ----------------------------------------------------------------------------
# CPU 温度：LibreHardwareMonitor
# ----------------------------------------------------------------------------

class LhmSource:
    def __init__(self, url=LHM_URL):
        self.url = url
        self.ok = False
        self._warned = False

    def _walk(self, node, out):
        text = (node.get("Text") or "").strip()
        value = node.get("Value")
        sid = node.get("SensorId") or ""
        if text and value:
            out.append((text, value, sid))
        for ch in node.get("Children") or []:
            self._walk(ch, out)

    @staticmethod
    def _num(v):
        try:
            return float(str(v).split()[0])
        except (ValueError, IndexError):
            return None

    def cpu_temp(self):
        try:
            with urllib.request.urlopen(self.url, timeout=1.0) as r:
                tree = json.load(r)
            self.ok = True
        except Exception as exc:            # noqa: BLE001
            self.ok = False
            if not self._warned:
                print(f"[warn] 读不到 LibreHardwareMonitor（{type(exc).__name__}），"
                      f"CPU 温度将显示 --。打开它的 Options → Remote Web Server → Run 即可。",
                      file=sys.stderr)
                self._warned = True
            return None

        sensors = []
        self._walk(tree, sensors)

        cands = [s for s in sensors if "/cpu/" in s[2].lower()
                 or ("temperature" in s[2].lower() and "cpu" in s[2].lower())
                 or s[0].lower().startswith(("cpu package", "core average", "core max"))]
        for prefer in ("cpu package", "core average", "core max", "cpu core"):
            for s in cands:
                if prefer in s[0].lower():
                    v = self._num(s[1])
                    if v is not None:
                        return v
        for s in cands:
            v = self._num(s[1])
            if v is not None:
                return v
        return None


# ----------------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------------

class Collector:
    """采集器。

    慢接口（nvidia-smi、LHM 的 HTTP）放到一个后台线程里按慢节奏刷新，
    sample() 只读缓存 —— 否则 nvidia-smi 在笔记本上要 2 秒（唤醒独显），
    会把整个 2Hz 刷新拖垮。
    """

    def __init__(self, slow_interval: float = 1.0):
        self.psutil = None
        try:
            import psutil as _ps
            self.psutil = _ps
        except ImportError:
            self.psutil = None

        self.gpu = GpuSource()
        self.lhm = LhmSource()
        self.seq = 0
        self._prev = None
        self._base_mhz = _base_mhz()

        # 慢接口的缓存
        self._slow_lock = threading.Lock()
        self._gpu_cache: dict = {}
        self._temp_cache = None
        self._slow_interval = max(0.2, slow_interval)
        self._stop = threading.Event()
        self._slow_thread = threading.Thread(target=self._slow_loop,
                                             name="collect-slow", daemon=True)
        self._slow_thread.start()

        # 建立基线（第一次差值需要两次采样）
        if self.psutil is not None:
            self.psutil.cpu_percent(interval=None)
        else:
            self._prev = _cpu_times()

    def _slow_loop(self):
        while not self._stop.is_set():
            try:
                gpu = self.gpu.read()
                temp = self.lhm.cpu_temp()
            except Exception as exc:        # noqa: BLE001
                print(f"[warn] 慢指标读取失败: {exc}", file=sys.stderr)
                gpu, temp = {}, None
            with self._slow_lock:
                self._gpu_cache = gpu or {}
                self._temp_cache = temp
            self._stop.wait(self._slow_interval)

    def close(self):
        self._stop.set()

    # ---- 各项指标 ----

    def cpu_load(self):
        if self.psutil is not None:
            return float(self.psutil.cpu_percent(interval=None))
        now = _cpu_times()
        if now is None:
            return 0.0
        if self._prev is None:
            self._prev = now
            return 0.0
        busy0, total0 = self._prev
        busy1, total1 = now
        self._prev = now
        dt = total1 - total0
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (busy1 - busy0) / dt))

    def memory(self):
        if self.psutil is not None:
            m = self.psutil.virtual_memory()
            mb = 1024 * 1024
            return {"load": int(round(m.percent)),
                    "total_mb": int(m.total // mb),
                    "used_mb": int(m.used // mb)}
        m = _mem()
        return m or {"load": 0, "total_mb": 0, "used_mb": 0}

    def cpu_clk(self):
        if self.psutil is not None:
            try:
                f = self.psutil.cpu_freq()
                if f is not None and f.current:
                    return int(round(f.current))
            except Exception:               # noqa: BLE001
                pass
        return self._base_mhz

    # ---- 采样 ----

    def sample(self):
        self.seq += 1
        mem = self.memory()
        with self._slow_lock:
            gpu = dict(self._gpu_cache)
            temp = self._temp_cache

        def pct(v):
            if v is None:
                return 0
            return int(max(0, min(100, round(v))))

        gpu_total = gpu.get("total_mb") or 0
        gpu_used = gpu.get("used_mb") or 0

        return {
            "seq": self.seq,
            "cpu_load": pct(self.cpu_load()),
            "cpu_temp_c10": int(round((temp or 0) * 10)) if temp else -32768,
            "cpu_clk": self.cpu_clk(),
            "mem_load": pct(mem["load"]),
            "mem_used": int(mem["used_mb"]),
            "mem_total": int(mem["total_mb"]),
            "gpu_load": pct(gpu.get("load")),
            "gpu_temp_c10": int(round(gpu["temp"] * 10)) if gpu.get("temp") is not None else -32768,
            "gpu_used": int(gpu_used),
            "gpu_total": int(gpu_total),
            "gpu_power": float(gpu.get("power") or 0.0),
        }

    def describe_sources(self):
        bits = ["psutil" if self.psutil else "ctypes(Win32)"]
        bits.append("nvidia-smi" if self.gpu.exe else "no-gpu")
        bits.append("LHM" if self.lhm.ok else "no-cpu-temp")
        return " + ".join(bits)
