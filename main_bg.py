"""AstrBot 后台运行入口 (System Tray Edition)

与 ``main.py`` 功能完全一致（保持同步），但额外提供：
1. 适配 ``pythonw.exe`` 运行（无控制台窗口）；
2. 在 Windows 右下角系统托盘显示图标；
3. 托盘菜单提供「打开 WebUI / 打开日志 / 退出」操作；
4. ``--open-browser`` 参数：服务就绪后自动用默认浏览器打开 WebUI。

依赖：
    - pystray   (托盘图标)
    - Pillow    (图标加载, AstrBot 已自带)

启动方式（推荐）::

    pythonw.exe main_bg.py

或者带控制台调试::

    python main_bg.py

启动后自动打开浏览器::

    python main_bg.py --open-browser

作者: elecvoid243
同步时间: 2026-08-16 21:20 (CST)
同步基线: main.py @ 21f41c239 (refactor: simplify updater architecture, #9493)
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

# -----------------------------------------------------------------------------
# pythonw 下 stdout/stderr 为 None，loguru / print 写日志时会抛 OSError。
# 必须在 import astrbot 之前把它们重定向到一个真实可写的对象 / 文件。
# -----------------------------------------------------------------------------
_LOG_DIR = Path(__file__).parent / "data" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_BG_LOG_FILE = _LOG_DIR / "astrbot.log"


def _ensure_stdio() -> None:
    """确保 sys.stdout / sys.stderr 可写（pythonw 下二者为 None）。"""
    if sys.stdout is None or sys.stderr is None:
        # 以行缓冲方式打开，方便实时查看
        f = open(_BG_LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f


_ensure_stdio()

# -----------------------------------------------------------------------------
# 以下区域与 main.py 保持同步（导入 runtime_bootstrap 之后才能 import astrbot）
# -----------------------------------------------------------------------------
import runtime_bootstrap  # noqa: E402

runtime_bootstrap.initialize_runtime_bootstrap()

DASHBOARD_RESET_PASSWORD_ENV = "ASTRBOT_RESET_DASHBOARD_PASSWORD"


def _apply_startup_env_flags(argv: list[str]) -> None:
    """Apply startup flags that must take effect before core imports.

    Args:
        argv: Command-line arguments excluding the executable name.
    """

    if "-h" in argv or "--help" in argv:
        return

    startup_parser = argparse.ArgumentParser(add_help=False)
    startup_parser.add_argument("--reset-password", action="store_true")
    startup_args, _ = startup_parser.parse_known_args(argv)
    if startup_args.reset_password:
        os.environ[DASHBOARD_RESET_PASSWORD_ENV] = "1"


_apply_startup_env_flags(sys.argv[1:])

from astrbot.core import LogBroker, LogManager, db_helper, logger  # noqa: E402
from astrbot.core.config.default import VERSION  # noqa: E402
from astrbot.core.initial_loader import InitialLoader  # noqa: E402
from astrbot.core.updater import AstrBotUpdater  # noqa: E402
from astrbot.core.utils.astrbot_path import (  # noqa: E402
    get_astrbot_config_path,
    get_astrbot_knowledge_base_path,
    get_astrbot_plugin_path,
    get_astrbot_root,
    get_astrbot_site_packages_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.runtime_env import is_packaged_desktop_runtime  # noqa: E402

# 将父目录添加到 sys.path
sys.path.append(Path(__file__).parent.as_posix())

logo_tmpl = r"""
     ___           _______.___________..______      .______     ______   .___________.
    /   \         /       |           ||   _  \     |   _  \   /  __  \  |           |
   /  ^  \       |   (----`---|  |----`|  |_)  |    |  |_)  | |  |  |  | `---|  |----`
  /  /_\  \       \   \       |  |     |      /     |   _  <  |  |  |  |     |  |
 /  _____  \  .----)   |      |  |     |  |\  \----.|  |_)  | |  `--'  |     |  |
/__/     \__\ |_______/       |__|     | _| `._____||______/   \______/      |__|

"""

# 托盘图标路径
TRAY_ICON_PATH = Path(__file__).parent / "docs" / "public" / "logo.png"

# 命令行配置路径（需从中读取 dashboard host/port）
_CMD_CONFIG_PATH = Path(__file__).parent / "data" / "cmd_config.json"


def _get_default_webui_url() -> str:
    """构造 WebUI 访问地址，与服务端绑定逻辑保持一致。

    读取优先级与 ``astrbot.dashboard.server.AstrBotDashboard.run()`` 对齐：
    环境变量 DASHBOARD_HOST / ASTRBOT_DASHBOARD_HOST / DASHBOARD_PORT /
    ASTRBOT_DASHBOARD_PORT 优先于 cmd_config.json 中 dashboard.host / dashboard.port。
    通配监听地址（如 0.0.0.0）对浏览器不可达，映射为 127.0.0.1；
    dashboard.ssl.enable 开启时使用 https 协议。
    配置读取失败时回退到 http://127.0.0.1:6185，不阻断托盘功能。

    Returns:
        WebUI 的浏览器可访问地址。
    """
    import json

    default_host, default_port = "127.0.0.1", 6185

    # 环境变量优先，与 dashboard server 的绑定逻辑保持一致
    host = os.environ.get("DASHBOARD_HOST") or os.environ.get("ASTRBOT_DASHBOARD_HOST")
    port = os.environ.get("DASHBOARD_PORT") or os.environ.get("ASTRBOT_DASHBOARD_PORT")
    scheme = "http"

    try:
        with open(_CMD_CONFIG_PATH, encoding="utf-8-sig") as f:
            dashboard = json.load(f).get("dashboard") or {}
        if not host:
            host = dashboard.get("host", default_host)
        if not port:
            port = dashboard.get("port", default_port)
        ssl_config = dashboard.get("ssl") or {}
        if ssl_config.get("enable"):
            scheme = "https"
    except Exception:
        # 读取失败时回退到默认值，不阻断托盘功能
        pass

    host = host or default_host
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    port = port or default_port

    return f"{scheme}://{host}:{port}"


# =============================================================================
# 与 main.py 保持同步的核心逻辑
# =============================================================================
def check_env() -> None:
    if not (sys.version_info.major == 3 and sys.version_info.minor >= 10):
        logger.error("Please run this project with Python 3.10 or later.")
        sys.exit()

    astrbot_root = get_astrbot_root()
    if astrbot_root not in sys.path:
        sys.path.insert(0, astrbot_root)

    site_packages_path = get_astrbot_site_packages_path()
    if not is_packaged_desktop_runtime() and site_packages_path not in sys.path:
        sys.path.append(site_packages_path)

    os.makedirs(get_astrbot_config_path(), exist_ok=True)
    os.makedirs(get_astrbot_plugin_path(), exist_ok=True)
    os.makedirs(get_astrbot_temp_path(), exist_ok=True)
    os.makedirs(get_astrbot_knowledge_base_path(), exist_ok=True)
    os.makedirs(site_packages_path, exist_ok=True)

    # 针对问题 #181 的临时解决方案
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")


async def check_dashboard_files(webui_dir: str | None = None):
    """Resolve and repair dashboard static files for startup.

    Args:
        webui_dir: Optional explicit WebUI directory path from CLI.

    Returns:
        The directory path to serve, or None when no usable WebUI can be prepared.
    """

    # 指定webui目录
    if webui_dir:
        if os.path.exists(webui_dir):
            logger.info("Using WebUI directory: %s", webui_dir)
            return webui_dir
        logger.warning("WebUI directory not found: %s. Using default.", webui_dir)

    try:
        return str(await AstrBotUpdater().ensure_dashboard())
    except Exception as e:
        logger.critical(f"Failed to download dashboard files: {e}.")
        return None


async def main_async(
    webui_dir_arg: str | None,
    log_broker: LogBroker,
    stop_event: asyncio.Event,
) -> None:
    """主异步入口（后台版：额外支持托盘 stop_event 优雅退出）

    Parameters
    ----------
    webui_dir_arg : str | None
        WebUI 静态文件目录。
    log_broker : LogBroker
        日志代理。
    stop_event : asyncio.Event
        外部（托盘线程）触发的停止事件。设置后本协程会优雅退出。
    """
    webui_dir = await check_dashboard_files(webui_dir_arg)
    if webui_dir is None:
        logger.warning(
            "Dashboard file validation failed, so WebUI features will be unavailable. "
            "Check the network connection or specify the --webui-dir argument manually."
        )

    db = db_helper

    logger.info(logo_tmpl)

    core_lifecycle = InitialLoader(db, log_broker)
    panel = None
    if sys.platform == "win32":
        from astrbot.dashboard.desktop_reminders import DesktopReminders

        panel = DesktopReminders(db, _get_default_webui_url)
        core_lifecycle.notify_project_todo = panel.sync
    core_lifecycle.webui_dir = webui_dir

    # 将 InitialLoader.start() 包装到 task，并与 stop_event 共同等待
    core_task = asyncio.create_task(core_lifecycle.start(), name="astrbot-core")
    stop_task = asyncio.create_task(stop_event.wait(), name="bg-stop-waiter")

    try:
        await asyncio.wait({core_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not core_task.done():
            logger.info("Stopping AstrBot background runtime")
            core_task.cancel()
        stop_task.cancel()
        await asyncio.gather(core_task, stop_task, return_exceptions=True)
        if panel is not None:
            await panel.close()


# =============================================================================
# 后台 / 托盘相关逻辑
# =============================================================================
class AstrBotBackground:
    """AstrBot 后台运行管理器，负责协调 asyncio loop 与 pystray 托盘。"""

    def __init__(self, webui_dir_arg: str | None, open_browser: bool = False) -> None:
        self.webui_dir_arg = webui_dir_arg
        self.open_browser = open_browser
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event: asyncio.Event | None = None
        self.core_thread: threading.Thread | None = None
        self.tray_icon = None  # type: ignore[assignment]

    # ---- asyncio 线程 -------------------------------------------------------
    def _run_core(self) -> None:
        """在独立线程中运行 asyncio 事件循环。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.stop_event = asyncio.Event()

        # 启动日志代理（与 main.py 保持一致）
        log_broker = LogBroker()
        LogManager.set_queue_handler(logger, log_broker)

        try:
            self.loop.run_until_complete(
                main_async(self.webui_dir_arg, log_broker, self.stop_event)
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"AstrBot background runtime error: {e}")
        finally:
            try:
                # 清理悬挂任务
                pending = asyncio.all_tasks(self.loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self.loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                self.loop.close()
            # 核心退出后，确保托盘也退出
            if self.tray_icon is not None:
                try:
                    self.tray_icon.stop()
                except Exception:  # noqa: BLE001
                    pass

    def start_core(self) -> None:
        self.core_thread = threading.Thread(
            target=self._run_core, name="astrbot-core", daemon=True
        )
        self.core_thread.start()

    def request_stop(self) -> None:
        """从托盘线程安全地请求停止 asyncio 循环。"""
        if self.loop is not None and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)

    # ---- 托盘 ---------------------------------------------------------------
    def _build_tray_icon(self):
        """构建 pystray Icon 对象。"""
        import pystray
        from PIL import Image

        # 加载图标；若找不到则使用纯色占位图
        if TRAY_ICON_PATH.exists():
            image = Image.open(TRAY_ICON_PATH)
        else:
            logger.warning(
                f"Tray icon not found at {TRAY_ICON_PATH}, using placeholder."
            )
            image = Image.new("RGBA", (64, 64), (66, 133, 244, 255))

        def on_open_webui(icon, item):  # noqa: ARG001
            webbrowser.open(_get_default_webui_url())

        def on_open_log(icon, item):  # noqa: ARG001
            try:
                os.startfile(str(_BG_LOG_FILE))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                webbrowser.open(_BG_LOG_FILE.as_uri())

        def on_quit(icon, item):  # noqa: ARG001
            logger.info("User quit AstrBot from tray")
            self.request_stop()
            # 等待核心线程结束（最多 10 秒），然后停止托盘
            if self.core_thread is not None:
                self.core_thread.join(timeout=10)
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("打开 WebUI", on_open_webui, default=True),
            pystray.MenuItem("打开日志文件", on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 AstrBot", on_quit),
        )

        icon = pystray.Icon(
            name="AstrBot",
            icon=image,
            title=f"AstrBot v{VERSION} (后台运行中)",
            menu=menu,
        )
        return icon

    def _open_browser_when_ready(self) -> None:
        """等待 dashboard 端口可连接后，用默认浏览器打开 WebUI。

        每 0.5s 探测一次 TCP 端口，最长等待 60s，确保浏览器打开时页面
        已经可以访问。本方法运行在 daemon 线程中，任何失败仅记录日志，
        绝不阻断托盘与退出流程。

        Returns:
            None
        """
        url = _get_default_webui_url()
        host = urlparse(url).hostname or "127.0.0.1"
        port = urlparse(url).port or 6185
        deadline = time.monotonic() + 60

        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1):
                    webbrowser.open(url)
                    logger.info("Opened WebUI in browser: %s", url)
                    return
            except OSError:
                time.sleep(0.5)

        logger.warning(
            "WebUI did not become reachable at %s within 60s, skipping browser open.",
            url,
        )

    def run(self) -> None:
        # 先启动核心
        self.start_core()
        if self.open_browser:
            threading.Thread(
                target=self._open_browser_when_ready,
                name="open-browser",
                daemon=True,
            ).start()
        # 再启动托盘（阻塞主线程，直到 icon.stop() 被调用）
        self.tray_icon = self._build_tray_icon()
        self.tray_icon.run()
        # 托盘退出后，确保核心线程也退出
        self.request_stop()
        if self.core_thread is not None:
            self.core_thread.join(timeout=10)


# =============================================================================
# 入口
# =============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AstrBot (Background / Tray Mode)")
    parser.add_argument(
        "--webui-dir",
        type=str,
        help="Specify the directory path for WebUI static files",
        default=None,
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help=(
            "Reset the dashboard initial password on startup and print it in "
            "startup logs"
        ),
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Automatically open the WebUI in the default browser after startup",
    )
    args = parser.parse_args()

    check_env()

    try:
        app = AstrBotBackground(args.webui_dir, args.open_browser)
        app.run()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Failed to start main_bg.py: {e}")
        sys.exit(1)
