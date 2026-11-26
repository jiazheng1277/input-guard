# -*- coding: utf-8 -*-
"""
hotkey_guard.py — 常驻后台：全局热键立即上锁（无窗口、无倒计时），可选定时自动上锁。

用法：
    pythonw hotkey_guard.py                                  # 默认 Ctrl+Alt+L 上锁，Ctrl+Alt+Q 退出
    pythonw hotkey_guard.py --hotkey ctrl+shift+f12 --quit-hotkey none
    pythonw hotkey_guard.py --auto-lock-at 21:30 --idle-minutes 15
    python  hotkey_guard.py --dry-run                        # 测试：命中只写日志，不真正上锁
    python  hotkey_guard.py --check ctrl+alt+l               # 只检查热键写法是否有效

定时自动上锁（--auto-lock-at HH:MM）：
    每天到这个时间进入「自动上锁模式」，没锁就立刻锁上。这个模式是单向的：
    不会自己结束，解锁后只要实体键鼠闲置 --idle-minutes 分钟就再锁。
    解锁状态下按 --disarm-hotkey（默认 Ctrl+Alt+U）关掉，当天不再自动锁，第二天到点重新开始。
    闲置只算实体键鼠：自动化脚本 / 远程控制的注入输入不算「有人在用」。

热键写法：修饰键（ctrl / alt / shift / win）+ 一个主键（a-z、0-9、f1-f24、
pause、space、home、end、insert、delete、pageup、pagedown），用 + 连接，大小写不限。
"""
import argparse
import ctypes
import datetime
import json
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
WM_TIMER = 0x0113
CREATE_NO_WINDOW = 0x08000000
KEYEVENTF_KEYUP = 0x0002
MODIFIER_VKS = (0xA2, 0xA3, 0xA4, 0xA5, 0xA0, 0xA1, 0x5B, 0x5C)  # 左右 Ctrl / Alt / Shift / Win
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
SYNCHRONIZE = 0x00100000
LOCKED_MUTEX = "Local\\InputGuardLocked"   # input_guard.py 上锁期间持有
TICK_MS = 15000                            # 定时检查间隔
REHOOK_MS = 60000                          # 定期重挂闲置检测钩子

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
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

LOCK_ID = 1
QUIT_ID = 2
DISARM_ID = 3
HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(HOME, "input_guard_log.txt")
ERR_PATH = os.path.join(HOME, "input_guard_err.txt")
STATE_PATH = os.path.join(HOME, ".input_guard_state.json")

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


def beep():
    try:
        import winsound
        winsound.MessageBeep()
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


def parse_time(text):
    """'21:30' -> (21, 30)；'none' -> None。"""
    if text.lower() == "none":
        return None
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    raise ValueError("时间要写成 HH:MM（例如 21:30），或 none 关闭：%s" % text)


# ---------------- 按键状态 ----------------

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


# ---------------- 闲置检测（只算实体键鼠） ----------------

class PhysicalActivity:
    """独立线程挂低级钩子，只记录最后一次「实体」键鼠输入的时间，从不拦截。"""

    def __init__(self):
        self.last = time.monotonic()
        self._kb = self._ms = None
        self._kb_proc = HOOKPROC(self._on_key)
        self._ms_proc = HOOKPROC(self._on_mouse)

    def touch(self):
        self.last = time.monotonic()

    def idle_seconds(self):
        return time.monotonic() - self.last

    def _on_key(self, n_code, w_param, l_param):
        try:
            if n_code == 0:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (info.flags & LLKHF_INJECTED):
                    self.last = time.monotonic()
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, n_code, w_param, l_param):
        try:
            if n_code == 0:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (info.flags & LLMHF_INJECTED):
                    self.last = time.monotonic()
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _install(self):
        hmod = kernel32.GetModuleHandleW(None)
        self._kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._ms = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, hmod, 0)

    def _remove(self):
        for h in (self._kb, self._ms):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._kb = self._ms = None

    def _run(self):
        self._install()
        if not (self._kb and self._ms):
            log("警告：闲置检测钩子挂不上，闲置自动上锁不可用")
        rehook = user32.SetTimer(None, 0, REHOOK_MS, None)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER and msg.wParam == rehook:
                # Windows 会悄悄摘掉超时的钩子，定期重挂
                self._remove()
                self._install()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()


activity = PhysicalActivity()


# ---------------- 上锁 ----------------

def is_locked():
    if _guard_proc is not None and _guard_proc.poll() is None:
        return True
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, LOCKED_MUTEX)  # 桌面图标等其它途径上的锁
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def find_python():
    """把 pythonw.exe 换成 python.exe：input_guard.py 需要有效的 stdout。"""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def _watch_unlock(proc):
    proc.wait()
    activity.touch()  # 闲置从解锁那一刻开始算
    time.sleep(0.1)
    release_stuck_keys()


def launch_guard(script_path, why, dry_run=False):
    global _guard_proc
    if dry_run:
        log("[dry-run] %s，本应上锁（未真正执行）" % why)
        return
    if is_locked():
        log("已处于上锁中，忽略（%s）" % why)
        return
    wait_keys_released()
    with open(ERR_PATH, "a", encoding="utf-8") as err:
        err.write("\n=== %s launch (%s) ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), why))
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
    log("%s，已启动上锁（pid=%d）" % (why, _guard_proc.pid))


# ---------------- 定时自动上锁 ----------------

class Schedule:
    """到点进入自动上锁模式（单向，直到有人按关闭热键）；模式中闲置 N 分钟就锁。"""

    def __init__(self, at, idle_minutes, state_path):
        self.at = at
        self.idle_secs = idle_minutes * 60
        self.state_path = state_path
        self.state = self._load()
        self.was_locked = False

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
        except OSError as e:
            log("警告：状态文件写不进去：%s" % e)

    @property
    def armed(self):
        return bool(self.state.get("armed"))

    def describe(self):
        return "每天 %02d:%02d 自动上锁，模式中闲置 %g 分钟再锁" % (self.at[0], self.at[1], self.idle_secs / 60)

    def disarm(self):
        if not self.armed:
            log("现在不在自动上锁模式，无需关闭")
            return
        self.state["armed"] = False
        self._save()
        log("已手动关闭自动上锁模式（下次 %02d:%02d 重新开始）" % self.at)
        beep()

    def tick(self, lock):
        now = datetime.datetime.now()
        today = now.date().isoformat()
        start = now.replace(hour=self.at[0], minute=self.at[1], second=0, microsecond=0)
        locked = is_locked()
        if self.was_locked and not locked:
            activity.touch()  # 其它途径解锁的，也从解锁时开始算闲置
        self.was_locked = locked

        if now >= start and self.state.get("last_arm_date") != today:
            self.state.update(armed=True, last_arm_date=today)
            self._save()
            log("到 %02d:%02d，进入自动上锁模式" % self.at)
            if not locked:
                lock("定时到点自动上锁")
            return
        if self.armed and not locked and activity.idle_seconds() >= self.idle_secs:
            lock("自动上锁模式中闲置 %g 分钟，自动上锁" % (self.idle_secs / 60))


# ---------------- 主程序 ----------------

def main():
    global _watch_vks
    ap = argparse.ArgumentParser(description="全局热键上锁守护")
    ap.add_argument("--hotkey", default="ctrl+alt+l", help="上锁热键（默认 ctrl+alt+l）")
    ap.add_argument("--quit-hotkey", default="ctrl+alt+q", help="退出守护的热键，none 表示不设")
    ap.add_argument("--auto-lock-at", default="none", metavar="HH:MM",
                    help="每天几点进入自动上锁模式（默认 none = 不启用）")
    ap.add_argument("--idle-minutes", type=float, default=15, help="自动上锁模式中闲置几分钟再锁（默认 15）")
    ap.add_argument("--disarm-hotkey", default="ctrl+alt+u", help="关闭当天自动上锁模式的热键")
    ap.add_argument("--state-file", default=STATE_PATH, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="只测试，不真正上锁")
    ap.add_argument("--check", metavar="HOTKEY", help="只检查热键写法，有效返回 0")
    ap.add_argument("--check-time", metavar="HH:MM", help="只检查时间写法，有效返回 0")
    args = ap.parse_args()

    if args.check or args.check_time:
        try:
            parse_hotkey(args.check) if args.check else parse_time(args.check_time)
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
        at = parse_time(args.auto_lock_at)
        disarm_key = parse_hotkey(args.disarm_hotkey) if at else None
        if args.idle_minutes <= 0:
            raise ValueError("--idle-minutes 必须大于 0")
    except ValueError as e:
        log("配置错误：%s" % e)
        return 1
    _watch_vks = MODIFIER_VKS + (lock_vk,)

    if not user32.RegisterHotKey(None, LOCK_ID, lock_mods | MOD_NOREPEAT, lock_vk):
        log("热键 %s 注册失败（可能被其它程序占用），错误码 %d" % (args.hotkey, ctypes.get_last_error()))
        return 1
    if quit_key and not user32.RegisterHotKey(None, QUIT_ID, quit_key[0] | MOD_NOREPEAT, quit_key[1]):
        log("热键 %s 注册失败，错误码 %d" % (args.quit_hotkey, ctypes.get_last_error()))
    if disarm_key and not user32.RegisterHotKey(None, DISARM_ID, disarm_key[0] | MOD_NOREPEAT, disarm_key[1]):
        log("热键 %s 注册失败，错误码 %d" % (args.disarm_hotkey, ctypes.get_last_error()))

    def lock(why):
        launch_guard(guard, why, args.dry_run)

    schedule = None
    if at:
        schedule = Schedule(at, args.idle_minutes, args.state_file)
        if schedule.armed:
            activity.last -= schedule.idle_secs  # 重启 / 重新登录时仍在模式中：没锁就马上锁
        activity.start()
        user32.SetTimer(None, 0, TICK_MS, None)

    log("热键守护已启动：%s 上锁%s%s%s" % (
        args.hotkey,
        "，%s 退出" % args.quit_hotkey if quit_key else "",
        "；%s，%s 关闭当天模式%s" % (schedule.describe(), args.disarm_hotkey,
                                  "（当前在模式中）" if schedule.armed else "") if schedule else "",
        "（dry-run 模式）" if args.dry_run else ""))
    if schedule:
        schedule.tick(lock)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            if msg.wParam == LOCK_ID:
                lock("热键触发")
            elif msg.wParam == QUIT_ID:
                log("收到退出热键，守护结束")
                break
            elif msg.wParam == DISARM_ID and schedule:
                schedule.disarm()
        elif msg.message == WM_TIMER and schedule:
            schedule.tick(lock)

    for i in (LOCK_ID, QUIT_ID, DISARM_ID):
        user32.UnregisterHotKey(None, i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
