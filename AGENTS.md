# Instructions for AI agents

This repo installs Input Guard on Windows: a global hotkey that blocks the physical keyboard and mouse
while letting injected input through, until the user blind-types their password.

## Install for the user

1. Ask the user which lock hotkey they want (default `ctrl+alt+l`). Valid format: any of
   `ctrl` / `alt` / `shift` / `win` + one key (`a-z`, `0-9`, `f1`-`f24`, `pause`, `space`, `home`,
   `end`, `insert`, `delete`, `pageup`, `pagedown`), joined with `+`. You can validate with
   `python hotkey_guard.py --check "<hotkey>"` (prints `ok`, exit 0).
   Also ask whether they want scheduled auto-lock, and at what time (`HH:MM`, e.g. `21:30`) and
   idle minutes (default 15). From that time each day it locks if not locked, then relocks after
   that many minutes of no *physical* input, until the user presses the disarm hotkey
   (default `ctrl+alt+u`). It never auto-unlocks. Validate with `python hotkey_guard.py --check-time "21:30"`.
2. Run from the repo root (absolute path, the repo must stay where it is; tasks point at it):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Hotkey "<hotkey>" [-AutoLockAt 21:30 -IdleMinutes 15]
   ```
   Warn the user: if the install runs after today's `-AutoLockAt` time, it locks right away.
3. Tell the user, before running it, that they must:
   - click **Yes** on the UAC prompt, and
   - type the unlock password in the window that pops up (lowercase letters + digits, 6+ chars),
     only on first install or with `-SetPassword`.
4. The command waits for the elevated installer and prints the result. Exit code `0` = success.
   The result is also in `%USERPROFILE%\input_guard_install.txt`.

## Rules

- **Never ask for, type, generate, or store the password.** The installer opens its own window
  for the user to type it; only a salted hash is saved to `~/.input_guard.json`.
- **Do not trigger the lock to "test" it** (no simulated hotkey, no running `input_guard.py`
  without `--test`). It blocks the user's keyboard and mouse. Ask the user to press the hotkey.
- To check it is running: `Get-ScheduledTask 'InputGuard*'` (Hotkey task should be `Running`),
  and the last line of `~/input_guard_log.txt` should say `热键守护已启动`.
- Lock failures land in `~/input_guard_err.txt`.

## Other commands

| Goal | Flags |
| --- | --- |
| Change hotkey | `-Hotkey "..."` (re-run install; password kept) |
| Quit-daemon hotkey | `-QuitHotkey "..."` or `-QuitHotkey none` |
| Scheduled auto-lock | `-AutoLockAt 21:30 -IdleMinutes 15 [-DisarmHotkey "ctrl+alt+u"]`; omit to turn off |
| Reset password | `-SetPassword` |
| No desktop icon | `-NoDesktopIcon` |
| Uninstall | `-Uninstall` |
