"""Reusable production Dashboard/SQLite fixture for multi-account slices."""

import asyncio

import pytest_asyncio

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.utils.auth_password import hash_dashboard_password
from astrbot.dashboard.server import AstrBotDashboard
from tests.test_fastapi_v1_dashboard import fake_core_lifecycle as fake_core_lifecycle

PASSWORD = "OriginalAdminPass123!"


@pytest_asyncio.fixture
async def account_dashboard(fake_core_lifecycle, tmp_path):
    """Build the production dashboard with persisted credentials and resources."""
    core = fake_core_lifecycle
    config = AstrBotConfig(str(tmp_path / "cmd_config.json"))
    config["dashboard"].update(
        username="original-admin",
        pbkdf2_password=hash_dashboard_password(PASSWORD),
        password_change_required=False,
        jwt_secret="account-migration-test-secret-32-bytes",
    )
    config.save_config()
    core.astrbot_config = config
    core.astrbot_config_mgr.confs["default"] = config
    core.astrbot_config_mgr.default_conf = config
    db = SQLiteDatabase(str(tmp_path / "accounts.db"))
    await db.initialize()
    db.inited = True
    core.platform_message_history_manager = PlatformMessageHistoryManager(db)
    server = AstrBotDashboard(core, db, asyncio.Event())
    server.app.testing = True
    yield server, core, db
    for run in list(server.asgi_app.state.services.chat.chat_runs.values()):
        if run.task and not run.task.done():
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
    await db.engine.dispose()
