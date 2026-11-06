#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无窗口启动器
============

双击「监控屏.lnk」就是跑这个文件（用 pythonw.exe，所以完全不弹黑窗口）。
它负责：

  1. 温度服务：8085 没在跑 → 用管理员权限拉起 lhm_supervisor.py（隐藏 LHM 窗口）
  2. 画面服务：8770 没在跑 → 后台 detached 拉起 server.py
  3. 等第一帧数据真的出来，再开浏览器（不会先闪一下 NO SIGNAL）
  4. 任何一步失败 → 弹消息框 + 写日志，而不是在无窗口环境里静默死掉

命令行：
    pythonw launch.py              # 启动（默认）
    pythonw launch.py --stop       # 停止：画面服务 + 温度服务一起关
    pythonw launch.py --lhm-only   # 只重启温度服务
    pythonw launch.py --demo       # 假数据（看 UI 用）
    pythonw launch.py --no-browser # 只起服务，不开浏览器
    pythonw launch.py --keep-lhm   # 配合 --stop：只关画面服务
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8770

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
FLAGS = DETACHED_PROCESS | CREATE_NO_WINDOW

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


# ----------------------------------------------------------------------------

# ShellExecuteW 返回的是 HINSTANCE。ctypes 默认把它当 32 位 int，
# 大值会被截断成负数、被误判成失败，所以这里显式声明签名。
_shell32 = ctypes.windll.shell32
_shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
]
_shell32.ShellExecuteW.restype = ctypes.c_void_p


def runas(file: str, params: str | None, cwd: str | None, show: int) -> int:
    """以管理员权限启动，返回 ShellExecuteW 的码（>32 表示成功，1223 = 用户取消）。"""
    try:
        rc = _shell32.ShellExecuteW(None, "runas", file, params, cwd, show)
    except Exception as exc:                # noqa: BLE001
        raise RuntimeError(f"ShellExecuteW failed: {exc}") from exc
    return int(rc or 0)


def server_up(port: int, timeout: float = 0.6) -> bool:
    """服务进程在不在（HTTP 能应答就算在）"""
    try:
        url = f"http://127.0.0.1:{port}/api/stats"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:                       # noqa: BLE001
        return False


def data_ready(port: int, timeout: float = 0.6) -> bool:
    """第一帧数据出来没有（避免刚打开那一下闪 NO SIGNAL）"""
    try:
        url = f"http://127.0.0.1:{port}/api/stats"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return bool(json.load(r).get("alive"))
    except Exception:                       # noqa: BLE001
        return False


def msgbox(text: str, flags: int = 0x40) -> None:
    """0x40 = 信息图标, 0x10 = 错误图标"""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "SYSTEM MONITOR", flags)
    except Exception:                       # noqa: BLE001
        pass


def pythonw_path() -> str:
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-4] + "w.exe"
        if os.path.isfile(cand):
            return cand
    return exe


# ----------------------------------------------------------------------------
# 顺带把 LibreHardwareMonitor 拉起来（CPU 温度靠它）
# ----------------------------------------------------------------------------

LHM_EXE = os.environ.get("LHM_EXE") or r"C:\Tools\LibreHardwareMonitor\LibreHardwareMonitor.exe"
SUPERVISOR = os.path.join(HERE, "lhm_supervisor.py")
LHM_STOP_FLAG = os.path.join(HERE, "lhm.stop")


def lhm_serving(timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen("http://localhost:8085/data.json", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:                       # noqa: BLE001
        return False


def lhm_process_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq LibreHardwareMonitor.exe", "/NH"],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW)
        return "LibreHardwareMonitor.exe" in (out.stdout or "")
    except Exception:                       # noqa: BLE001
        return False


def ensure_lhm() -> str:
    """没在跑就把「守护进程 + LHM」拉起来。返回状态字符串，方便写日志。

    LHM 的 manifest 是 requireAdministrator，普通权限既杀不掉它、也藏不掉它的窗口；
    所以这里启动的是 lhm_supervisor.py（同样提权），由它负责：
      - 用 SW_HIDE 拉起 LHM 并把窗口按下去（同权限才藏得掉）
      - 收到 lhm.stop 旗标就杀掉 LHM
    Windows 会弹一次 UAC，点「是」就好。
    """
    if lhm_serving():
        return "already"
    if not os.path.isfile(LHM_EXE):
        return "missing:" + LHM_EXE

    # 说明：即使已经有 LHM 进程在跑，只要 8085 没起来，也要再拉一份 ——
    # 因为那个进程可能是早先随手开的、web server 没开。
    had_process = lhm_process_running()
    try:
        if os.path.isfile(SUPERVISOR):
            # nCmdShow=0 (SW_HIDE)：守护进程自己也不要有窗口
            rc = runas(pythonw_path(), f'"{SUPERVISOR}"', HERE, 0)
        else:
            rc = runas(LHM_EXE, None, os.path.dirname(LHM_EXE), 6)
    except RuntimeError as exc:
        return f"error:{exc}"
    # ShellExecuteW 成功时返回值 > 32；<= 32 都是失败码（1223 = 用户点了“否”）
    if rc <= 32:
        return "cancelled" if rc == 1223 else f"shell-error:{rc}"
    return "supervisor" + ("(had-process)" if had_process else "")


def stop_lhm(wait: float = 8.0) -> str:
    """让守护进程把 LHM 关掉。没有守护进程时退化成 taskkill（多半没权限）。"""
    try:
        with open(LHM_STOP_FLAG, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        return f"flag-error:{exc}"

    # 循环里第一件事就是检查 8085，所以 LHM 本来就没在跑时会立刻返回，不用等满 wait
    deadline = time.time() + wait
    while time.time() < deadline:
        if not lhm_serving(0.3):
            break
        time.sleep(0.25)

    still = lhm_serving(0.3)
    if still:
        # 兜底：没守护进程在跑的时候，只能直接 taskkill（提权的 LHM 会失败，正常）
        try:
            subprocess.run(["taskkill", "/F", "/IM", "LibreHardwareMonitor.exe"],
                           creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=10)
        except Exception:                   # noqa: BLE001
            pass
        time.sleep(0.5)
        still = lhm_serving(0.3)

    if not still:
        try:
            os.remove(LHM_STOP_FLAG)
        except OSError:
            pass
    return "stopped" if not still else "still-running"


def log(msg: str) -> None:
    try:
        with open(os.path.join(HERE, "launch.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def open_browser(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    for path in EDGE_PATHS + CHROME_PATHS:
        if os.path.isfile(path):
            try:
                subprocess.Popen(
                    [path, "--new-window", url,
                     "--window-size=1920,1080", "--window-position=0,0"],
                    creationflags=FLAGS, close_fds=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:                       # noqa: BLE001
        msgbox(f"请手动打开浏览器访问：\n{url}")


def start_server(port: int, extra: list[str]) -> bool:
    server = os.path.join(HERE, "server.py")
    # 把子进程的 stdout/stderr 直接接到 server.log，
    # 这样 pythonw 下也能看到服务自己打印的东西（排错时很关键）
    try:
        logf = open(os.path.join(HERE, "server.log"), "ab", buffering=0)
    except OSError:
        logf = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [pythonw_path(), server, "--port", str(port), "--no-open"] + extra,
            cwd=HERE,
            creationflags=FLAGS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
        )
        # 存 PID，停止时直接 taskkill /PID（比扫全进程的 PowerShell 快得多）
        try:
            with open(os.path.join(HERE, "server.pid"), "w", encoding="utf-8") as fh:
                fh.write(str(proc.pid))
        except OSError:
            pass
        return True
    except Exception as exc:                # noqa: BLE001
        log(f"start_server failed: {exc}")
        return False
    finally:
        if logf is not subprocess.DEVNULL:
            logf.close()


def stop_server(port: int = DEFAULT_PORT) -> int:
    pid_file = os.path.join(HERE, "server.pid")
    pid = 0
    try:
        with open(pid_file, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        pid = 0

    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           creationflags=CREATE_NO_WINDOW,
                           capture_output=True, timeout=10)
        except Exception:                   # noqa: BLE001
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass

    if not server_up(port, 0.3):
        return 0                            # 已经没了，不用再扫

    # 兜底：PID 文件缺失/过期时，按命令行扫一遍
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
        " | Where-Object { $_.CommandLine -like '*server.py*' }"
        " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=CREATE_NO_WINDOW,
                       capture_output=True, timeout=20)
    except Exception:                       # noqa: BLE001
        return 1
    return 0


# ----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    port = DEFAULT_PORT
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    if "--stop" in argv:
        running = server_up(port)
        keep_lhm = "--keep-lhm" in argv

        # 停画面服务和停温度服务互不依赖，并行做，总耗时取两者最大值
        results: dict = {}

        def _do_stop_server():
            results["server"] = stop_server(port)

        th_srv = threading.Thread(target=_do_stop_server, daemon=True)
        th_srv.start()

        if not keep_lhm:
            th_lhm = threading.Thread(target=lambda: results.update(lhm=stop_lhm()),
                                      daemon=True)
            th_lhm.start()

        th_srv.join(timeout=20)
        if not keep_lhm:
            th_lhm.join(timeout=20)

        lhm_state = "kept" if keep_lhm else results.get("lhm", "unknown")
        log(f"stop: server={running} lhm={lhm_state}")

        if not running:
            log("stop: 本来就没在跑")
        elif lhm_state == "stopped":
            log("stop: 画面 + 温度 都已关闭")
        elif lhm_state == "kept":
            log("stop: 画面已关闭，温度保留（--keep-lhm）")
        else:
            # 只有没关干净才打扰用户
            msgbox("画面服务已关闭，但温度服务没关掉。\n\n"
                   "可能是它不属于当前守护进程（比如手动开的 LHM），\n"
                   "需要的话用任务管理器（管理员）结束 LibreHardwareMonitor。",
                   flags=0x30)
        return 0

    # 只重启温度服务（守护进程），不动画面服务
    if "--lhm-only" in argv:
        state = ensure_lhm()
        log(f"lhm-only -> {state}")
        if state != "already" and not state.startswith("supervisor"):
            msgbox("温度服务没能启动：\n\n" + state
                   + "\n\n细节看 lhm_supervisor.log / launch.log", flags=0x10)
        return 0

    # 温度服务放到后台线程里启动：
    # ShellExecuteW(runas) 会一直阻塞到用户点完 UAC，绝不能拿它挡住画面服务。
    lhm_thread = None
    if "--no-lhm" not in argv and "--demo" not in argv:
        def _start_lhm():
            log(f"lhm -> {ensure_lhm()}")

        lhm_thread = threading.Thread(target=_start_lhm, name="lhm-start", daemon=False)
        lhm_thread.start()

    if server_up(port):
        if "--no-browser" not in argv:      # 已经开着，就别再起一份
            open_browser(port)
        return 0

    extra = []
    if "--demo" in argv:
        extra.append("--demo")

    if not start_server(port, extra):
        msgbox("画面服务没能启动。\n\n详细信息见：\n"
               + os.path.join(HERE, "launch.log"), flags=0x10)
        return 1

    # 等第一帧数据出来再开浏览器，避免刚打开时闪一下 NO SIGNAL
    deadline = time.time() + 12.0
    while time.time() < deadline:
        time.sleep(0.25)
        if data_ready(port, 0.3):
            if "--no-browser" not in argv:
                open_browser(port)
            return 0

    if server_up(port, 0.5):
        # 服务在，但一直没数据：还是把页面打开，至少能看到 NO SIGNAL 提示
        if "--no-browser" not in argv:
            open_browser(port)
        return 0

    msgbox("服务没能启动。\n\n"
           "请打开日志看看：\n"
           f"{os.path.join(HERE, 'server.log')}\n\n"
           "常见原因：8770 端口被别的程序占用，\n"
           "或安全软件拦截了 python 监听本地端口。",
           flags=0x10)
    return 1


if __name__ == "__main__":
    # pythonw 下没有任何输出，异常会直接静默消失 —— 这里兜住并弹窗
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:                   # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(HERE, "launch.log"), "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "FATAL\n" + tb + "\n")
        except OSError:
            pass
        try:
            msgbox("启动器出错了：\n\n" + tb.strip().splitlines()[-1]
                   + "\n\n完整信息见 launch.log", flags=0x10)
        except Exception:                   # noqa: BLE001
            pass
        sys.exit(1)
