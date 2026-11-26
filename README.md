# Input Guard

Windows 上的「实体输入锁」：按一下热键，**坐在电脑前的人按键盘、动鼠标全部无效**，
而程序注入的输入（自动化脚本、远程控制）照常工作。盲打密码解锁。

- 全局热键立即上锁，默认 `Ctrl+Alt+L`，可自定义
- 不弹窗口、没有倒计时、不弹 UAC（装一次后）
- 登录后自动生效
- 可选定时自动上锁：每天到点没锁就锁，解锁后闲置一段时间再锁，直到有人手动关闭
- 桌面 `InputGuard.vbs` 双击也能上锁
- 日志记录每次上锁 / 解锁时间、期间被挡掉多少次实体输入

## 安装

要求：Windows 10/11，Python 3（[python.org](https://www.python.org/downloads/)）。不需要任何第三方包。

```powershell
git clone https://github.com/jiazheng1277/input-guard.git
cd input-guard
powershell -ExecutionPolicy Bypass -File install.ps1
```

1. UAC 提示点「是」
2. 弹出的窗口里设解锁密码（小写字母 + 数字，至少 6 位）。只存加盐哈希，不存明文
3. 完成。按 `Ctrl+Alt+L` 试一下，盲打密码解锁

## 配置

| 想做的事 | 命令 |
| --- | --- |
| 换上锁热键 | `install.ps1 -Hotkey "ctrl+shift+f12"` |
| 换 / 关掉退出守护的热键 | `install.ps1 -QuitHotkey "ctrl+alt+q"` / `-QuitHotkey none` |
| 定时自动上锁 | `install.ps1 -AutoLockAt 21:30 -IdleMinutes 15` |
| 换关闭当天模式的热键 | `install.ps1 -AutoLockAt 21:30 -DisarmHotkey "ctrl+alt+u"` |
| 关掉定时自动上锁 | `install.ps1`（不带 `-AutoLockAt`，默认 `none`） |
| 重设密码 | `install.ps1 -SetPassword` |
| 不要桌面图标 | `install.ps1 -NoDesktopIcon` |
| 卸载 | `install.ps1 -Uninstall` |

（都要加 `powershell -ExecutionPolicy Bypass -File` 前缀。）重跑安装会覆盖旧配置，已设的密码保留。

热键写法：`ctrl` / `alt` / `shift` / `win` 任意组合 + 一个主键（`a-z`、`0-9`、`f1-f24`、
`pause`、`space`、`home`、`end`、`insert`、`delete`、`pageup`、`pagedown`），用 `+` 连接。
普通键必须配修饰键，免得打字时误触。

## 使用

- **上锁**：按热键，或双击桌面 `InputGuard.vbs`
- **解锁**：直接盲打密码（没有输入框，大小写不限）
- **退出热键守护**：`Ctrl+Alt+Q`（下次登录自动重启）
- **逃生口**：`Ctrl+Alt+Del` 任何程序都拦不了，从那里可以登出或重启

### 定时自动上锁（`-AutoLockAt`）

1. 每天到设定时间（如 21:30）进入「自动上锁模式」，当时没锁就立刻锁上
2. 模式是单向的，**不会自己结束**：输密码解锁后可以正常用，实体键鼠闲置 `-IdleMinutes` 分钟（默认 15）就再锁
3. 解锁状态下按 `Ctrl+Alt+U` 关闭模式，当天不再自动锁，第二天到点重新开始
4. 从不自动解锁；重启 / 重新登录时如果还在模式中，没锁就马上锁
5. 闲置只算实体键鼠：自动化脚本、远程控制的注入输入不算「有人在用」，所以通宵跑脚本也会照样锁

模式状态存在 `~/.input_guard_state.json`。

## 文件

| 文件 | 作用 |
| --- | --- |
| `input_guard.py` | 上锁本体：低级键鼠钩子，吞掉实体输入、放行注入输入、识别盲打密码 |
| `hotkey_guard.py` | 后台热键守护，按热键无窗口启动 `input_guard.py` |
| `run_lock_hidden.pyw` | 无窗口上锁一次（计划任务 `InputGuard Lock` 用） |
| `install.ps1` | 安装 / 重新配置 / 卸载 |

运行数据都在用户目录，不在仓库里：

- `~/.input_guard.json`：密码的盐和哈希
- `~/input_guard_log.txt`：上锁 / 解锁日志
- `~/input_guard_err.txt`：上锁进程的错误输出（排查用）
- `~/input_guard_install.txt`：最近一次安装的结果

## 注意

- 这是防「有人顺手碰电脑」的工具，不是安全边界：能 `Ctrl+Alt+Del` 登出的人就能绕过；
  能读你用户目录的人可以离线暴力破解短密码。
- 上锁期间电脑不会睡眠。
- 以管理员权限运行（计划任务「最高权限」），这样前台是管理员窗口（如任务管理器）时也拦得住。

## 许可证

[MIT](LICENSE)
