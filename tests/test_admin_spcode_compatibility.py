"""Administrator project loading through real SPCode routes and Dashboard auth."""

import importlib
import os
import sys
import types

import pytest

from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root_variable", ["ASTRBOT_TEST_SPCODE_ROOT", "ASTRBOT_TEST_LEGACY_SPCODE_ROOT"]
)
async def test_admin_loads_manual_project_without_relaxing_user_access(
    ordinary_chat, monkeypatch, tmp_path, root_variable
):
    """Keep host directory selection while enforcing every supplied Chat owner.

    Args:
        ordinary_chat: Real Dashboard, database and three authenticated accounts.
        monkeypatch: Isolate plugin registration and process-wide plugin state.
        tmp_path: Disposable host directory outside any bound Chat project.
        root_variable: Checkout containing the current or legacy plugin handlers.
    """
    root = os.environ.get(root_variable)
    if not root:
        pytest.skip(f"Set {root_variable} to exercise actual plugin handlers")
    package = types.ModuleType(f"admin_compat_{root_variable.lower()}")
    package.__path__ = [root]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    main = importlib.import_module(f"{package.__name__}.main")
    webapi = importlib.import_module(f"{package.__name__}.tools.webapi")
    project_module = importlib.import_module(
        f"{package.__name__}.tools.project.manager"
    )
    client, server, core, db, admin, users = ordinary_chat
    registered = []
    monkeypatch.setattr(
        core.star_context,
        "register_web_api",
        lambda route, view_handler, methods, desc: registered.append(
            (route, view_handler, methods, desc)
        ),
        raising=False,
    )
    monkeypatch.setattr(core.star_context, "registered_web_apis", registered)
    monkeypatch.setattr(
        core.star_context, "get_config", lambda umo: core.astrbot_config, raising=False
    )
    plugin = main.SPCodeToolkit.__new__(main.SPCodeToolkit)
    plugin.context = core.star_context
    plugin._config = dict(main._DEFAULT_CONFIG)
    plugin._webapi_active = True
    plugin.project = project_module.ProjectManager(plugin)
    plugin.agentsmd = main.AgentsmdSubsystem(
        plugin=plugin, is_path_safe=main._is_path_safe
    )
    webapi.register_webapi_routes(plugin)
    identities = []
    for account in [admin, *users]:
        headers = {"Authorization": f"Bearer {account['token']}"}
        sid = (await client.get("/api/v1/chat/sessions/new", headers=headers)).json()[
            "data"
        ]["session_id"]
        origin = f"webchat:FriendMessage:webchat!{account['resource_owner']}!{sid}"
        identities.append((headers, origin))
    admin_headers, admin_origin = identities[0]
    directory = tmp_path / "manually-selected"
    directory.mkdir()
    (directory / "example.txt").write_text("administrator project")
    for base in ["/api/v1/plugins/extensions/spcode", "/api/plug/spcode"]:
        for route in ["home-directory", "drives", "file-browser"]:
            for origin in [None, admin_origin]:
                params = {"path": str(directory)} if route == "file-browser" else {}
                if origin:
                    params["umo"] = origin
                response = await client.get(
                    f"{base}/{route}", headers=admin_headers, params=params
                )
                assert response.status_code == 200, response.text
                if route == "file-browser":
                    assert "example.txt" in response.text
        loaded = await client.post(
            f"{base}/project-load",
            headers=admin_headers,
            json={
                "umo": admin_origin,
                "directory": str(directory),
                "no_agentsmd": True,
                "no_codegraph": True,
            },
        )
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["data"]["success"], loaded.text
        status = await client.get(
            f"{base}/project-status",
            headers=admin_headers,
            params={"umo": admin_origin},
        )
        assert status.json()["data"]["loaded"] is True, status.text
        assert "all_loaded_count" not in status.text
        # Neither a privileged account nor another user can address this session.
        for caller, own_origin in identities:
            for _, other_origin in identities:
                if own_origin == other_origin:
                    continue
                denied = await client.post(
                    f"{base}/project-unload", headers=caller, json={"umo": other_origin}
                )
                assert denied.status_code == 403, denied.text
            if caller is not admin_headers:
                denied = await client.get(
                    f"{base}/file-browser",
                    headers=caller,
                    params={"umo": own_origin, "path": str(directory)},
                )
                assert denied.status_code == 403, denied.text
        ambiguous = await client.get(f"{base}/project-status", headers=admin_headers)
        assert ambiguous.status_code == 403, ambiguous.text
        conflicting = await client.post(
            f"{base}/project-unload",
            headers=admin_headers,
            params={"umo": identities[1][1]},
            json={"umo": admin_origin},
        )
        assert conflicting.status_code == 400, conflicting.text
        unloaded = await client.post(
            f"{base}/project-unload",
            headers=admin_headers,
            json={"umo": admin_origin},
        )
        assert unloaded.json()["data"]["success"], unloaded.text
