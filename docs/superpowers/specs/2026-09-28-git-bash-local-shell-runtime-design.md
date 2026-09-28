# 本地 Shell 运行时（Git Bash 支持）— 设计文档

| 项目 | 内容 |
|------|------|
| 主题 | 本地 Computer Use 运行时的 shell 选择：Git Bash / PowerShell 7 / Windows PowerShell 5.1 / cmd.exe |
| 日期 | 2026-09-28 |
| 状态 | Implemented — 已合入 `all`（merge `fcf32ecc9`） |
| 关联代码 | `astrbot/core/computer/booters/local.py`（`ShellSpec` / `_find_git_bash` / `resolve_local_shell` / `exec` / `exec_managed` / `_terminate_process`）<br>`astrbot/core/astr_main_agent.py`（`_build_local_mode_prompt`）<br>`astrbot/core/tools/computer_tools/shell.py`（`ExecuteShellTool.call`）<br>`astrbot/core/config/default.py`（默认值 + 面板 schema）<br>`dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/config-metadata.json`（面板文案，**新增配置项时必须同步全部四个语言**，否则前端显示键名） |
| 关联配置 | `provider_settings.computer_use_local_shell`，默认 `auto` |
| 实施计划 | `docs/superpowers/plans/2026-09-28-git-bash-local-shell-runtime.md` |
| 测试 | `tests/test_local_shell_resolver.py`、`tests/test_local_shell_component.py`、`tests/test_local_shell_config.py`、`tests/unit/test_astr_main_agent.py` |

本文记录的是**实测结论与判断依据**，不是使用说明。文中每个数值都来自 Windows 10/11 + Git for Windows 2.38（Python 3.13）的实机探测，复验方法见 §11。

---

## 1. 背景

改动前，Windows 上的本地 shell 由 `resolve_windows_shell()` 决定，只有两个分支：PATH 上有 `pwsh` 就用 PowerShell 7，否则用 `powershell.exe`。它返回裸字符串，调用方各自把 `-NoLogo -NoProfile -NonInteractive -Command` 拼在命令前面。

这带来三个问题：

1. **模型能力受限**：本地模式下模型只能写 PowerShell。Unix 工具链（`grep` / `sed` / `find` / 管道）不可用。
2. **无法配置**：想换 shell 只能改代码。
3. **两处既有缺陷**（见 §4、§5.3）。

目标：以 Git Bash 为 Windows 默认 shell（未安装则依次回退 pwsh → powershell），并让用户可显式选择。

---

## 2. 结论速查

| 主题 | 结论 |
|---|---|
| 检测 Git Bash | 由 `which("git")` 反推安装目录。**绝不能用 `which("bash")`**（§3） |
| 终止进程树 | `CTRL_BREAK_EVENT` 优先，`taskkill /F /T` 兜底。单独用 taskkill 会泄漏后台作业（§4） |
| 编码 | Git Bash 全线原生 UTF-8，现有解码器直接可用（§5） |
| 输出缓冲 | bash 自身逐行实时；块缓冲来自管道子进程，靠提示词指导缓解（§6） |
| 中断语义 | 可用，但是**硬杀**，不触发 bash trap（§7） |
| Python 解释器 | 提示词在 Windows 下给出 `sys.executable` 作为默认解释器；跑项目代码前须先查项目自带环境并与用户确认（§10） |
| 启动开销 | 比 PowerShell 5.1 快 1.8–2.6 倍（§8） |
| PTY | `winpty` 存在但未采用，属独立增强（§9） |

---

## 3. 为什么不能用 `shutil.which("bash")`

在参考机器上实测：

```
shutil.which("git")   -> D:\Program Files\Git\cmd\git.EXE
shutil.which("bash")  -> C:\Users\...\AppData\Local\Microsoft\WindowsApps\bash.EXE   ← 陷阱
```

`which("bash")` 命中的是 **WSL 启动器桩**：它指向完全不同的文件系统与 shell 语义（Linux 路径、不同的 `/home`）。用它意味着模型在"Git Bash"里写的 `/c/...` 路径全部失效，而且失败方式很难诊断。

**采用的检测顺序**（`_find_git_bash()`）：

1. `shutil.which("git")` → 从 `git.exe` 所在目录**逐级向上**遍历祖先目录，在每个祖先下依次探测 `bin/bash.exe`、`usr/bin/bash.exe`（跳过盘符根）
2. `%ProgramFiles%\Git\bin\bash.exe`
3. `%ProgramFiles(x86)%\Git\bin\bash.exe`
4. `%LOCALAPPDATA%\Programs\Git\bin\bash.exe`

第 1 步是关键：参考机器的 Git 装在 **D 盘**，纯 well-known 路径全部漏检。第 2–4 步只是兜底。

### 3.1 为什么必须逐级向上，而不是固定深度

`git.exe` 在 Git for Windows 里有**三个可能位置**，具体命中哪个由 PATH 顺序决定：

| 启动方式 | `which("git")` 返回 | 若按固定 `parent.parent` 推导 |
|---|---|---|
| 常规启动（PATH 含 `Git\cmd`） | `Git\cmd\git.EXE` | `Git` ✅ 正确 |
| **从 Git Bash 内启动**（PATH 含 `Git\mingw64\bin`） | `Git\mingw64\bin\git.EXE` | `Git\mingw64` ❌ **找不到** |

`Git\mingw64\bin\bash.exe` 并不存在，所以第二行会让检测静默失败并回退到 PowerShell——**没有任何报错**。

这个缺陷是初版实现引入的：`Path(git).resolve().parent.parent` 只在 PATH 恰好含 `Git\cmd` 时成立。它躲过了第一轮探测，因为探测用的 `astrbot_execute_python` 跑在 AstrBot 自身进程里（PATH 只有 `Git\cmd`），而验证提示词时用的子进程继承了 Git Bash 的 PATH，两者结果不一致才暴露出来。

> **维护提示**：判断"当前 shell 是哪一个"时，不要依赖 `which` 的返回路径深度。回归测试见 `test_find_git_bash_derives_from_mingw64_git_on_path`。

**刻意不做缓存**：每次探测只是几次 `is_file()`，而缓存需要在测试里处理失效，得不偿失。

**解析顺序**：`auto` = git_bash → pwsh → powershell。必须保留第三级——参考机器上 `pwsh` 也未安装，两级回退会硬失败。显式配置了未安装的 shell 时记 warning 并回退到 `auto`，避免一个过期的配置值让 shell 执行彻底不可用。

---

## 4. 进程生命周期：为什么 `CTRL_BREAK_EVENT` 优先于 `taskkill`

这是本次改动中最反直觉、也最重要的一处。

### 4.1 实测

| 场景 | `taskkill /F /T` | `CTRL_BREAK_EVENT` |
|---|---|---|
| `bash -c "sleep 500"` | 干净 | 干净 |
| `bash -c "sleep 500 & sleep 500"` | **泄漏两个 `sleep.exe`** | 干净，0 残留 |
| 前台路径（`CREATE_NO_WINDOW`）+ 后台作业 | **泄漏** | 干净 |

原因：MSYS2 的**后台作业**会脱离 Windows 的父子 PID 链，而 `taskkill /T` 正是沿这条链遍历的。所以 `taskkill` 报告"已终止"却留下了真正的负载进程。

### 4.2 控制台是前提

`CTRL_BREAK_EVENT` **无法投递到用 `CREATE_NO_WINDOW` 创建的进程**——该标志会直接丢弃控制台。因此 `exec()` 在 git_bash 家族下改用 `CREATE_NEW_PROCESS_GROUP` + 隐藏 `STARTUPINFO`（`wShowWindow = 0`）。后者在 `pythonw.exe` 下也不会闪窗，这一点 `exec_managed` 早已采用。

### 4.3 语义代价：硬杀，不触发 trap

实测：

```
脚本: trap "echo TRAP-FIRED; exit 130" INT TERM; echo READY; sleep 300
外部: CTRL_BREAK_EVENT
结果: rc=0xC000013A，输出只有 READY —— trap DID NOT fire
```

Windows 侧的控制台事件**绕过了 MSYS2 的 POSIX 信号层**。含义：

- 对 `git_bash`，`astrbot_shell_session` 的 `interrupt` 与 `terminate` 语义塌缩为同一个动作；
- 用户脚本里的 trap 清理**不会执行**；
- 需要优雅关闭时，应使用 §7 的 `kill -TERM <msys_pid>` 路径。

### 4.4 必须容忍进程已退出

`CTRL_BREAK_EVENT` 让进程退出后，随后的 `taskkill` 会因 PID 已消失而返回非零，失败分支若再调用 `terminate()`，asyncio 的传输层会抛 `ProcessLookupError`。

这个缺陷在单元测试里**看不见**——测试用的 `FakeProcess.terminate()` 是空操作。它是靠端到端跑真实 Git Bash 才暴露的。修复方式是在 `terminate()` 前检查 `returncode is None`，回归测试见 `test_terminate_process_tolerates_already_exited_git_bash`。

> **维护提示**：改动 `_terminate_process` 时，不要只依赖 mock 测试。§11 的端到端脚本是唯一能覆盖这条路径的手段。

---

## 5. 编码

### 5.1 Git Bash 全线 UTF-8

| 命令 | 输出编码 |
|---|---|
| `ls` / `find` / `grep` / `cat` / `wc` / `stat` | 原生 UTF-8 |
| 中文作为 argv | UTF-8，参数完整传递 |
| 中文作为 cwd | 正常（`G:\github\X` → `pwd` 返回 `/g/github/X`） |
| `locale` | `LC_CTYPE="C.UTF-8"` —— MSYS2 默认已是 UTF-8 |

**无需注入任何环境变量**，现有 `_decode_shell_output`（UTF-8 优先 → GBK 兜底）直接可用。

### 5.2 已知限制：单缓冲区混合编码

若一条命令**同时**输出 bash 工具的 UTF-8 和 Windows 原生工具的 GBK（例如 `cmd //c chcp`），解码器只能整体二选一，必然有一半乱码。这属于已知限制，不引入流式嗅探就无法根治。

### 5.3 顺带修复的静默乱码

改动前 PowerShell 是唯一选择，而 PowerShell 输出的是 **GBK 字节**。问题在于：`目录` 的 GBK 编码 `C4 BF C2 BC` **恰好是合法 UTF-8**，会被解码成 `Ŀ¼`：

```
PowerShell 输出 b'\xc4\xbf\xc2\xbc\r\n'
  → 先试 UTF-8 → 解码"成功" → 返回 'Ŀ¼'
  → 静默错误，没有任何异常信号
```

抽样 7 个常见中文词，`目录` 命中此陷阱（约 1/7）。切到 Git Bash 后该类缺陷消失；**但用户若显式选回 PowerShell，缺陷依旧存在**——配置项因此带上了编码一致性的含义。

---

## 6. 输出缓冲

### 6.1 bash 自身没有问题

按 `exec_managed` 的真实读取循环实测（5 行、每秒一行）：

| 命令 | 3 秒内到达块数 |
|---|---|
| bash 内建 `echo` 循环 | 3 / 5 ✅ |
| 外部 `/usr/bin/echo` | 3 / 5 ✅ |
| `\| cat` | 3 / 5 ✅ |
| `tail -f` | 5 / 5 ✅ |
| PowerShell 循环（对照） | 3 / 5 ✅ |

**bash 与 PowerShell 的流式表现逐项对齐，切换 shell 没有引入退化。**

### 6.2 真正的风险在管道子进程

```
bash -c 'for i in 1..5; do echo "L$i"; sleep 1; done | sed ""'
  → 0 块在 3 秒内到达，全部 5 行在 t=5.35s 一次性吐出
```

这是 `sed` / `awk` / `grep` 等 stdio 程序输出到管道时的块缓冲，**与 shell 无关**。但 Git Bash 显著放大了暴露面：Unix 管道是 bash 的惯用写法，而这些恰好是块缓冲工具。

### 6.3 缓解手段（均已实测可用）

| 手段 | 可用性 | 效果 |
|---|---|---|
| `sed -u ''` | GNU sed 4.8 | ✅ 3/5 |
| `stdbuf -oL sed ''` | `/usr/bin/stdbuf` | ✅ 3/5 |
| `grep --line-buffered` | `/usr/bin/grep` | ✅ 3/5 |
| `awk '{print; fflush()}'` | GNU Awk 5.0.0 | ✅ 3/5 |

这些写进了 git_bash 分支的系统提示词。彻底根治需要 PTY（§9）。

---

## 7. 信号语义

### 7.1 bash 内部 `kill` 符合 POSIX

| 信号 | 子进程退出码 |
|---|---|
| `kill -INT` | 130（128+2） |
| `kill -TERM` | 143（128+15） |
| `kill -9` | 137（128+9） |

### 7.2 跨会话发信号可用

MSYS2 维护跨实例共享的进程表：

```
会话 A: sleep 300 & echo "PID=$!"   ->  PID=250
会话 B: kill -0 250    -> rc=0        （进程存在）
会话 B: kill -TERM 250 -> rc=0        （信号送达，A 终止）
```

因此模型可以**在后续的 `astrbot_execute_shell` 调用里关掉先前启动的服务**。这是 PowerShell 下没有的工作流，也是 §4.3 提到的优雅关闭路径。

---

## 8. 启动开销

中位数，n=12，`CREATE_NO_WINDOW`：

| 命令 | median |
|---|---|
| `bash -c 'echo hi'` | **77.8 ms** |
| `bash -c 'ls /c \| head -1'` | **133.2 ms** |
| `powershell -Command 'echo hi'` | 199.1 ms |
| `powershell -Command 'Get-ChildItem'` | 242.4 ms |

Git Bash 是 MSYS2 原生程序，不像 PowerShell 5.1 需要加载 .NET 运行时。**每次工具调用的延迟下降 1.8–2.6 倍**，属附带收益。

（`pwsh` 在参考机器上未安装，未纳入对比。）

---

## 9. 未走的路线：winpty / PTY

`winpty` 随 Git for Windows 一同提供（`/usr/bin/winpty`），能分配 PTY。PTY 下所有程序都看到 tty，**自动行缓冲**，且需要 TTY 的交互式 CLI 也能正常工作。

**未采用的原因**：需要重构 spawn 架构（`exec_managed` 目前直接读 pipe，改为经 winpty 中转），且 winpty 的进程管理与中断语义需要重新探测。

**适用场景**：若将来要根治 §6 的缓冲问题、或支持交互式程序，这是唯一路径。届时需要重新验证 §4 的终止语义是否仍然成立。

---

## 10. 已知的既有缺陷（本次未修）

1. **PowerShell 5.1 不响应 `CTRL_BREAK_EVENT`**。实测 15 秒无响应，而对照组 `python` 与 `bash` 都是 0.00 秒退出。这意味着 `astrbot_shell_session` 的 `interrupt` 动作在 PowerShell 家族下**本就是无效的**，只有 `terminate` 有效。切到 Git Bash 反而让这个动作真正生效。
2. **`python` 在 Git Bash 中解析到 Windows 应用商店桩**，表现为 `rc=0` 且**输出为空**的静默成功。提示词把 AstrBot 自身的解释器（`sys.executable`）写成默认值，并引导优先使用 `astrbot_execute_python`；**跑项目代码前要求先检查项目是否自带环境（venv / .venv / conda / uv），再与用户确认用哪个解释器**——因为项目往往有自己的环境，直接用 AstrBot 的解释器是错的。这条指引**仅 Windows 生效**。
3. **`git status` 默认对 CJK 文件名做八进制转义**（`?? "\347\233\256\345\275\225/"`）。提示词建议加 `-c core.quotepath=false`。

---

## 11. 如何复验

本文所有数值都可以重新测量。建议的最小复验集：

1. **检测**：`resolve_local_shell()` 的 family 是否为 `git_bash`；把 `which` 桩成返回 WindowsApps 路径，确认它**不会**被选中。
2. **终止**：启动 `sleep 300 & sleep 300`，用 `astrbot_shell_session` 终止会话，确认 `tasklist` 中没有残留 `sleep.exe`。
3. **编码**：`echo '目录 中文'` 是否原样返回。
4. **端到端**：跑一次真实 Git Bash 的 `uname -a`，应返回 `MINGW64_NT-...`。

> 第 2、4 项**必须对真实进程跑**。§4.4 的缺陷就是被 mock 掩盖的——用 `FakeProcess` 写单元测试只能证明信号被发送，不能证明进程树被清理。

相关的单元测试：

```bash
pytest tests/test_local_shell_resolver.py tests/test_local_shell_component.py tests/test_local_shell_config.py -q
```
