# -*- coding: utf-8 -*-
"""
input_guard.py — 挡住实体键盘和鼠标，放行程序注入的输入 (Injected Input)。

Windows 本身不锁屏，所以截屏 + 自动点击的脚本、远程控制照常工作；
坐在电脑前的人按键盘、动鼠标全部无效，直到盲打出解锁密码。

用法（建议用「以管理员身份运行」的终端）：
    python input_guard.py --set-password     设定解锁密码（只能用小写字母和数字）
    python input_guard.py --test 30          试锁 30 秒后自动解开（第一次必须先跑这个）
    python input_guard.py                    正式上锁，盲打密码解锁

逃生口：Ctrl+Alt+Del 任何程序都拦不了，从那里可以登出或重启。
日志：~/input_guard_log.txt（上锁/解锁时间、期间被挡掉多少次实体输入）
"""
import argparse
import ctypes
import getpass
import hashlib
import json
import os
import sys
import time
from ctypes import wintypes

if os.name != "nt":
    sys.exit("这个脚本只能在 Windows 上运行。")

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".input_guard.json")
LOG_PATH = os.path.join(os.path.expanduser("~"), "input_guard_log.txt")

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_TIMER = 0x0113
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
REHOOK_EVERY_MS = 30000  # 定期重挂钩子：Windows 会悄悄摘掉超时的钩子

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
kernel32.SetThreadExecutionState.restype = wintypes.DWORD


for _s in (sys.stdout, sys.stderr):
    # 后台无控制台时默认编码是 cp1252，打印中文会直接崩
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:  # pythonw 下没有 stdout
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def hash_pw(pw, salt):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def set_password():
    print("密码只能用小写字母 a-z 和数字 0-9，至少 6 位。解锁时是盲打，没有输入框。")
    pw = getpass.getpass("新密码：").strip().lower()
    if len(pw) < 6 or not all(c.isascii() and c.isalnum() for c in pw):
        sys.exit("密码不符合要求，没有保存。")
    if getpass.getpass("再输一次：").strip().lower() != pw:
        sys.exit("两次不一致，没有保存。")
    salt = os.urandom(8).hex()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"salt": salt, "hash": hash_pw(pw, salt), "length": len(pw)}, f)
    print("已保存到 %s（只存哈希，不存明文）。" % CONFIG_PATH)


def vk_to_char(vk):
    if 0x30 <= vk <= 0x39:          # 主键盘数字
        return chr(vk)
    if 0x60 <= vk <= 0x69:          # 小键盘数字
        return chr(vk - 0x30)
    if 0x41 <= vk <= 0x5A:          # 字母，一律当小写
        return chr(vk + 32)
    return None


class Guard:
    def __init__(self, cfg):
        self.cfg = cfg
        self.buf = ""
        self.blocked = 0
        self.first_blocked = None
        self.last_blocked = None
        self.unlocked = False
        self.kb_hook = None
        self.ms_hook = None
        # 回调必须留着引用，否则会被垃圾回收导致崩溃
        self._kb_proc = HOOKPROC(self._on_key)
        self._ms_proc = HOOKPROC(self._on_mouse)

    def _count(self):
        now = time.strftime("%H:%M:%S")
        self.blocked += 1
        if self.first_blocked is None:
            self.first_blocked = now
        self.last_blocked = now

    def _on_key(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and not self.unlocked:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (info.flags & LLKHF_INJECTED):
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self._count()
                        ch = vk_to_char(info.vkCode)
                        if ch:
                            n = self.cfg["length"]
                            self.buf = (self.buf + ch)[-n:]
                            if len(self.buf) == n and hash_pw(self.buf, self.cfg["salt"]) == self.cfg["hash"]:
                                self.unlocked = True
                                user32.PostQuitMessage(0)
                    return 1  # 吞掉实体按键
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and not self.unlocked:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (info.flags & LLMHF_INJECTED):
                    if w_param != 0x0200:  # 鼠标移动太频繁，不计数，只计点击和滚轮
                        self._count()
                    return 1  # 吞掉实体鼠标事件（含移动）
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def install(self):
        hmod = kernel32.GetModuleHandleW(None)
        for mod in (hmod, None):
            self.kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, mod, 0)
            self.ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, mod, 0)
            if self.kb_hook and self.ms_hook:
                return True
            self.remove()
        return False

    def remove(self):
        for h in (self.kb_hook, self.ms_hook):
            if h:
                user32.UnhookWindowsHookEx(h)
        self.kb_hook = self.ms_hook = None

    def run(self, test_seconds):
        if not self.install():
            log("失败：钩子挂不上（错误码 %d），没有上锁。" % ctypes.get_last_error())
            return 1
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # 上锁期间不让电脑睡眠
        rehook_id = user32.SetTimer(None, 0, REHOOK_EVERY_MS, None)
        test_id = user32.SetTimer(None, 0, test_seconds * 1000, None) if test_seconds else 0
        log("已上锁%s。实体键盘鼠标无效，盲打密码解锁。"
            % ("（试锁 %d 秒后自动解开）" % test_seconds if test_seconds else ""))
        msg = wintypes.MSG()
        reason = "密码解锁"
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                if test_id and msg.wParam == test_id:
                    reason = "试锁时间到，自动解开"
                    break
                if msg.wParam == rehook_id:
                    self.remove()
                    if not self.install():
                        log("警告：重挂钩子失败，保护已失效。")
                        reason = "钩子失效"
                        break
        self.unlocked = True
        self.remove()
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        if self.blocked:
            log("已解锁（%s）。期间挡掉 %d 次实体按键/点击，第一次 %s，最后一次 %s。"
                % (reason, self.blocked, self.first_blocked, self.last_blocked))
        else:
            log("已解锁（%s）。期间没有人碰过键盘鼠标。" % reason)
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass
        return 0


def main():
    ap = argparse.ArgumentParser(description="挡住实体键鼠，放行注入输入")
    ap.add_argument("--set-password", action="store_true", help="设定解锁密码")
    ap.add_argument("--test", type=int, default=0, metavar="秒", help="试锁 N 秒后自动解开")
    ap.add_argument("--delay", type=int, default=5, metavar="秒", help="上锁前倒数秒数（默认 5）")
    args = ap.parse_args()

    if args.set_password:
        set_password()
        return 0
    if not os.path.exists(CONFIG_PATH):
        sys.exit("还没设密码。先跑：python input_guard.py --set-password")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    if not is_admin:
        print("注意：现在不是管理员身份。当前台是管理员权限的窗口（例如任务管理器）时，"
              "实体输入拦不住。要堵这个洞，用「以管理员身份运行」的终端启动。")

    for i in range(max(args.delay, 0), 0, -1):
        print("%d 秒后上锁，现在松开键盘鼠标…" % i, flush=True)
        time.sleep(1)
    return Guard(cfg).run(args.test)


if __name__ == "__main__":
    sys.exit(main())
