#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreHardwareMonitor 守护进程（以管理员身份运行，无窗口）
========================================================

为什么需要它：
  LHM 的 manifest 是 requireAdministrator —— 它永远以管理员权限运行，
  普通权限的程序（包括 launch.py、任务管理器不加"以管理员身份运行"）
  既杀不掉它，也没法把它的窗口藏起来（UIPI 拦截）。
  所以由这个同样提权的守护进程来管它：

  1. 启动前先清掉所有残留的 LHM 进程；
  2. 用 SW_HIDE 拉起 LHM，并把它的窗口真正隐藏（同权限，藏得掉）；
  3. 盯着 lhm.stop 旗标，一出现就杀掉 LHM 然后自己退出；
  4. LHM 自己退出/崩了，守护进程也跟着退出。

由 launch.py 通过 ShellExecuteW(runas) 拉起，全程没有控制台窗口。
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))

LHM_EXE = os.environ.get("LHM_EXE") or r"C:\Tools\LibreHardwareMonitor\LibreHardwareMonitor.exe"
STOP_FLAG = os.path.join(HERE, "lhm.stop")
LOCK_FILE = os.path.join(HERE, "lhm.lock")
LOG_FILE = os.path.join(HERE, "lhm_supervisor.log")

CREATE_NO_WINDOW = 0x08000000
SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ----------------------------------------------------------------------------

def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def hide_windows_of(pid: int) -> int:
    """把指定进程的所有可见顶层窗口隐藏掉（需要同或更高完整性级别）。"""
    hidden = [0]

    def _cb(hwnd, _lparam):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
            hidden[0] += 1
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception as exc:                 # noqa: BLE001
        log(f"枚举窗口失败: {exc}")
    return hidden[0]


def kill_all_lhm() -> None:
    """清掉所有残留的 LHM 实例（守护进程是提权的，杀得掉）。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "LibreHardwareMonitor.exe"],
                       creationflags=CREATE_NO_WINDOW,
                       capture_output=True, timeout=15)
    except Exception as exc:                 # noqa: BLE001
        log(f"taskkill 失败: {exc}")


_lock_fh = None


def acquire_single_instance() -> bool:
    """用「操作系统持有的文件锁」保证只跑一个守护进程。

    比 PID 文件可靠：进程无论怎么死（任务管理器、崩溃、断电），
    Windows 都会自动释放锁，不会留下"看着还活着"的僵尸锁。
    """
    global _lock_fh
    try:
        fh = open(LOCK_FILE, "a+", encoding="utf-8")
    except OSError:
        return True                     # 锁文件都建不了，就别拦着自己了
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return False
    _lock_fh = fh                       # 持有到进程结束
    return True


def wait_port_free(seconds: float = 4.0) -> None:
    """等 8085 释放，避免新旧实例抢端口。"""
    import socket
    deadline = time.time() + seconds
    while time.time() < deadline:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 8085))
            s.close()
            return
        except OSError:
            s.close()
            time.sleep(0.3)
    log("等待 8085 释放超时，照样继续")


# ----------------------------------------------------------------------------

def main() -> int:
    if not acquire_single_instance():
        log("已有守护进程在跑，本次退出")
        return 0

    try:
        os.remove(STOP_FLAG)
    except OSError:
        pass

    if not os.path.isfile(LHM_EXE):
        log(f"找不到 {LHM_EXE}")
        return 2

    log("清理旧实例")
    kill_all_lhm()
    wait_port_free()

    # 用 SW_HIDE 启动。LHM 是 WinForms，多数情况会自己最小化/隐藏；
    # 万一它还是把窗口显示出来了，下面的 hide_windows_of 会把它按下去。
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE

    log("启动 LHM")
    proc = subprocess.Popen([LHM_EXE], cwd=os.path.dirname(LHM_EXE),
                            startupinfo=si, close_fds=True)

    # 窗口可能晚一拍才出现，多试几次（每轮都先看停止旗标，保证 stop 够快）
    for _ in range(8):
        if os.path.exists(STOP_FLAG):
            break
        time.sleep(0.4)
        n = hide_windows_of(proc.pid)
        if n:
            log(f"已隐藏 {n} 个窗口")
            break

    log(f"LHM pid={proc.pid} 运行中")

    while True:
        if os.path.exists(STOP_FLAG):
            log("收到停止旗标")
            try:
                proc.terminate()
            except Exception:                # noqa: BLE001
                pass
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:            # noqa: BLE001
                    pass
            break

        if proc.poll() is not None:
            log("LHM 自己退出了")
            break

        # 万一窗口被系统/用户又显示出来，压回去
        hide_windows_of(proc.pid)
        time.sleep(0.3)

    try:
        os.remove(STOP_FLAG)
    except OSError:
        pass

    log("守护进程退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
