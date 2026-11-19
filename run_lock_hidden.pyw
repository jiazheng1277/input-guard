# -*- coding: utf-8 -*-
"""run_lock_hidden.pyw — 无窗口、无倒计时，立即上锁一次（供计划任务 "InputGuard Lock" 调用）。
input_guard.py 需要有效的 stdout，pythonw 下没有，所以这里用 python.exe + CREATE_NO_WINDOW 启动它。
等它结束再退出，这样计划任务的 IgnoreNew 能防止重复上锁。"""
import os
import subprocess
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
exe = sys.executable
if exe.lower().endswith("pythonw.exe"):
    exe = exe[: -len("pythonw.exe")] + "python.exe"

with open(os.path.join(os.path.expanduser("~"), "input_guard_err.txt"), "a", encoding="utf-8") as err:
    err.write("\n=== %s launch (desktop) ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    err.flush()
    subprocess.call(
        [exe, os.path.join(here, "input_guard.py"), "--delay", "0"],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        stdin=subprocess.DEVNULL,
        stdout=err,
        stderr=subprocess.STDOUT,
        cwd=here,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
