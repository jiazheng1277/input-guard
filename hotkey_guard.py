# -*- coding: utf-8 -*-
"""
hotkey_guard.py — 常驻后台，按全局热键立即上锁（无窗口、无倒计时）。

用法：
    pythonw hotkey_guard.py                                  # 默认 Ctrl+Alt+L 上锁，Ctrl+Alt+Q 退出
    pythonw hotkey_guard.py --hotkey ctrl+shift+f12 --quit-hotkey none
    python  hotkey_guard.py --dry-run                        # 测试热键：命中只写日志，不真正上锁
    python  hotkey_guard.py --check ctrl+alt+l               # 只检查热键写法是否有效

热键写法：修饰键（ctrl / alt / shift / win）+ 一个主键（a-z、0-9、f1-f24、
pause、space、home、end、insert、delete、pageup、pagedown），用 + 连接，大小写不限。
"""
import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

if os.name != "nt":
    sys.exit("这个脚本只能在 Windows 上运行。")

for _s in (sys.stdout, sys.stderr):
    # 管道 / 无控制台时默认编码是 cp1252，打印中文会直接崩
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MODS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
NAMED_KEYS = {"pause": 0x13, "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23,
              "home": 0x24, "insert": 0x2D, "delete": 0x2E}
WM_HOTKEY = 0x0312
CREATE_NO_WINDOW = 0x08000000
KEYEVENTF_KEYUP = 0x0002
MODIFIER_VKS = (0xA2, 0xA3, 0xA4, 0xA5, 0xA0, 0xA1, 0x5B, 0x5C)  # 左右 Ctrl / Alt / Shift / Win

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]

LOCK_ID = 1
QUIT_ID = 2
HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(HOME, "input_guard_log.txt")
ERR_PATH = os.path.join(HOME, "input_guard_err.txt")

_guard_proc = None
_watch_vks = MODIFIER_VKS


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


def parse_hotkey(text):
    """'ctrl+alt+l' -> (mods, vk)。写法不对抛 ValueError。"""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("热键为空")
    mods, vk = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
            continue
        if vk is not None:
            raise ValueError("只能有一个主键：%s" % text)
        if len(p) == 1 and p.isascii() and p.isalnum():
            vk = ord(p.upper())
        elif p[0] == "f" and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            vk = 0x6F + int(p[1:])
        elif p in NAMED_KEYS:
            vk = NAMED_KEYS[p]
        else:
            raise ValueError("不认识的键：%s" % p)
    if vk is None:
        raise ValueError("缺少主键：%s" % text)
    if mods == 0 and not (0x70 <= vk <= 0x87 or vk == 0x13):
        raise ValueError("普通键必须配修饰键（ctrl/alt/shift/win），否则打字时会误触：%s" % text)
    return mods, vk


def keys_down():
    return [vk for vk in _watch_vks if user32.GetAsyncKeyState(vk) & 0x8000]


def wait_keys_released(timeout=3.0):
    """等热键都松开再上锁，否则松开动作被锁吞掉，系统以为 Ctrl 一直按着。"""
    end = time.time() + timeout
    while keys_down() and time.time() < end:
        time.sleep(0.02)


def release_stuck_keys():
    """解锁后若系统仍认为某个键按着，补发一个（注入的）松开事件。"""
    stuck = keys_down()
    for vk in stuck:
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    if stuck:
        log("解锁后补发松开：%s" % ", ".join("0x%02X" % vk for vk in stuck))


def _watch_unlock(proc):
    proc.wait()
    time.sleep(0.1)
    release_stuck_keys()


def find_python():
    """把 pythonw.exe 换成 python.exe：input_guard.py 需要有效的 stdout。"""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def launch_guard(script_path, dry_run=False):
    global _guard_proc
    if dry_run:
        log("[dry-run] 热键命中，本应上锁（未真正执行）")
        return
    if _guard_proc is not None and _guard_proc.poll() is None:
        log("已处于上锁中，忽略重复触发")
        return
    wait_keys_released()
    with open(ERR_PATH, "a", encoding="utf-8") as err:
        err.write("\n=== %s launch ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        err.flush()
        _guard_proc = subprocess.Popen(
            [find_python(), script_path, "--delay", "0"],
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=err,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(script_path),
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
    threading.Thread(target=_watch_unlock, args=(_guard_proc,), daemon=True).start()
    log("热键触发，已启动上锁（pid=%d）" % _guard_proc.pid)


def main():
    global _watch_vks
    ap = argparse.ArgumentParser(description="全局热键上锁守护")
    ap.add_argument("--hotkey", default="ctrl+alt+l", help="上锁热键（默认 ctrl+alt+l）")
    ap.add_argument("--quit-hotkey", default="ctrl+alt+q", help="退出守护的热键，none 表示不设")
    ap.add_argument("--dry-run", action="store_true", help="只测试热键，不真正上锁")
    ap.add_argument("--check", metavar="HOTKEY", help="只检查热键写法，有效返回 0")
    args = ap.parse_args()

    if args.check:
        try:
            parse_hotkey(args.check)
        except ValueError as e:
            print(e)
            return 1
        print("ok")
        return 0

    here = os.path.dirname(os.path.abspath(__file__))
    guard = os.path.join(here, "input_guard.py")
    if not os.path.exists(guard):
        log("找不到 input_guard.py：%s" % guard)
        return 1

    try:
        lock_mods, lock_vk = parse_hotkey(args.hotkey)
        quit_key = None if args.quit_hotkey.lower() == "none" else parse_hotkey(args.quit_hotkey)
    except ValueError as e:
        log("热键配置错误：%s" % e)
        return 1
    _watch_vks = MODIFIER_VKS + (lock_vk,)

    if not user32.RegisterHotKey(None, LOCK_ID, lock_mods | MOD_NOREPEAT, lock_vk):
        log("热键 %s 注册失败（可能被其它程序占用），错误码 %d" % (args.hotkey, ctypes.get_last_error()))
        return 1
    if quit_key and not user32.RegisterHotKey(None, QUIT_ID, quit_key[0] | MOD_NOREPEAT, quit_key[1]):
        log("热键 %s 注册失败，错误码 %d" % (args.quit_hotkey, ctypes.get_last_error()))

    log("热键守护已启动：%s 上锁%s%s" % (
        args.hotkey, "，%s 退出" % args.quit_hotkey if quit_key else "",
        "（dry-run 模式）" if args.dry_run else ""))

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            if msg.wParam == LOCK_ID:
                launch_guard(guard, args.dry_run)
            elif msg.wParam == QUIT_ID:
                log("收到退出热键，守护结束")
                break

    user32.UnregisterHotKey(None, LOCK_ID)
    user32.UnregisterHotKey(None, QUIT_ID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
