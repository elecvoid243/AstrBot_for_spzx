"""Live, isolated Dashboard acceptance for the installed workflow component fixture."""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from tests.agent_teams.test_workflow_plugin_restart import external_ledger, ledger_graph

expect.set_options(timeout=25000)

REPO = Path(__file__).resolve().parents[2]
SCRATCH = REPO / ".pytest_cache" / f"atn178-browser-{uuid4().hex[:8]}"
PASSWORD = "Fixture7" + uuid4().hex
PLUGIN = "astrbot_plugin_workflow_ledger"
VALUES = {"record": {"count": 41}}


def available_port():
    """Find an unused loopback port for one isolated process."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def instance(label, ledger):
    """Boot a fresh real core and Vite proxy in the current worktree."""
    root = SCRATCH / label
    root.mkdir(parents=True)
    port, vite_port = available_port(), available_port()
    dashboard = REPO / "dashboard"
    config = root / "vite.config.ts"
    config.write_text(
        f'import {{ mergeConfig }} from "{dashboard.as_posix()}/node_modules/vite/dist/node/index.js";\n'
        f'import config from "{dashboard.as_posix()}/vite.config.ts";\n'
        f'export default (env) => mergeConfig(config(env), {{server: {{host:"127.0.0.1", port:{vite_port},'
        f' strictPort:true, proxy:{{"/api":{{target:"http://127.0.0.1:{port}",changeOrigin:true,ws:true}}}}}}}});\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ASTRBOT_ROOT": str(root),
        "ASTRBOT_DASHBOARD_PORT": str(port),
        "ASTRBOT_DASHBOARD_HOST": "127.0.0.1",
        "ASTRBOT_DASHBOARD_INITIAL_PASSWORD": PASSWORD,
        "ATN178_LEDGER_KEY": "local-ledger-secret",
        "ATN178_LEDGER_URL": ledger.url,
        "PYTHONIOENCODING": "utf-8",
    }
    with ExitStack() as stack:

        def launch(command, cwd, listen, log_name):
            """Start one supervised test process on its assigned loopback port."""
            log = stack.enter_context((root / log_name).open("ab"))
            proc = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
            )

            def cleanup():
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=15)

            stack.callback(cleanup)
            for _ in range(200):
                assert proc.poll() is None, (root / log_name).read_text(
                    encoding="utf-8"
                )
                with socket.socket() as client:
                    client.settimeout(0.2)
                    if client.connect_ex(("127.0.0.1", listen)) == 0:
                        return proc
                time.sleep(0.1)
            raise AssertionError(f"{log_name} did not start in {root}")

        core_command = [sys.executable, "main.py", "--webui-dir", "dashboard"]
        core = launch(core_command, REPO, port, "core.log")
        launch(
            [
                shutil.which("node"),
                "node_modules/vite/bin/vite.js",
                "--config",
                str(config),
            ],
            dashboard,
            vite_port,
            "vite.log",
        )

        def restart():
            """Restart the actual core against the same isolated data directory."""
            nonlocal core
            core.terminate()
            core.wait(timeout=15)
            core = launch(core_command, REPO, port, "core.log")

        yield SimpleNamespace(url=f"http://127.0.0.1:{vite_port}", restart=restart)


def checked(response):
    """Return a successful Dashboard payload or display its diagnostic."""
    assert response.ok, (response.url, response.status, response.text()[:2500])
    body = response.json()
    assert body["status"] == "ok", body
    return body["data"]


class Dashboard:
    """One real authenticated Dashboard account and browser."""

    def __init__(self, browser, base):
        self.base = base
        self.context = browser.new_context(viewport={"width": 1550, "height": 1000})
        self.context.set_default_timeout(25000)
        credentials = {"username": "astrbot", "password": PASSWORD}
        checked(
            self.context.request.post(base + "/api/v1/auth/login", data=credentials)
        )
        self.set_cookie_auth()
        new_password = PASSWORD + "Changed7"
        self.api(
            "PATCH",
            "auth/account",
            {
                "password": PASSWORD,
                "new_password": new_password,
                "confirm_password": new_password,
            },
        )
        identity = checked(
            self.context.request.post(
                base + "/api/v1/auth/login",
                data={**credentials, "password": new_password},
            )
        )
        self.set_cookie_auth()
        self.context.route(
            base + "/api/**",
            lambda route: route.continue_(
                headers={**route.request.headers, **self.headers}
            ),
        )
        storage = {
            **{
                key: identity.get(key, "")
                for key in ("token", "role", "account_id", "resource_owner")
            },
            "user": identity["username"],
            "astrbot-locale": "en-US",
            "astrbot:first_notice_seen:v1": "1",
        }
        self.context.add_init_script(
            "Object.entries("
            + json.dumps(storage)
            + ").forEach(([k,v]) => { if (!localStorage.getItem(k)) localStorage.setItem(k,v) });"
        )
        self.page = self.context.new_page()
        self.page.on(
            "pageerror", lambda error: print(f"Browser error: {error}", flush=True)
        )

    def set_cookie_auth(self):
        """Use the JWT issued by this instance."""
        token = next(
            c["value"]
            for c in self.context.cookies()
            if c["name"] == "astrbot_dashboard_jwt"
        )
        self.headers = {"Authorization": "Bearer " + token}

    def raw(self, method, path, data=None):
        """Call the real versioned Dashboard endpoint."""
        return self.context.request.fetch(
            self.base + "/api/v1/" + path,
            method=method,
            headers=self.headers,
            data=data,
        )

    def api(self, method, path, data=None):
        """Call a Dashboard endpoint and read the successful response."""
        return checked(self.raw(method, path, data))

    def install(self, *, headers=None):
        """Send the controlled connector to the real plugin upload endpoint."""
        buffer = io.BytesIO()
        fixture = Path(__file__).parent / "fixtures" / "workflow_ledger_plugin"
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in ("main.py", "metadata.yaml"):
                archive.write(fixture / name, name)
        return self.context.request.post(
            self.base + "/api/v1/plugins/install/upload",
            headers=self.headers if headers is None else headers,
            multipart={
                "file": {
                    "name": PLUGIN + ".zip",
                    "mimeType": "application/zip",
                    "buffer": buffer.getvalue(),
                }
            },
        )

    def create_resource(self, title, ledger):
        """Create a genuinely local KB with an isolated embedding service."""
        self.api(
            "POST",
            "providers",
            {
                "config": {
                    "id": "test-embedding",
                    "type": "openai_embedding",
                    "provider_type": "embedding",
                    "embedding_model": "test-embedding",
                    "enable": True,
                    "embedding_api_key": "fixture-key",
                    "embedding_api_base": ledger.url + "/v1",
                    "embedding_dimensions": 3,
                }
            },
        )
        return self.api(
            "POST",
            "knowledge-bases",
            {
                "name": title,
                "embedding_provider_id": "test-embedding",
            },
        )["kb_id"]

    def editor(self, identity):
        """Open the real workflow editor for the selected draft."""
        self.page.goto(
            self.base + f"/#/agent-teams/workflows/{identity}",
            wait_until="domcontentloaded",
        )
        expect(self.page.locator(".graph-stage")).to_be_visible()

    def completed(self, run_id):
        """Wait for a fixed terminal result through the normal run endpoint."""
        for _ in range(160):
            run = self.api("GET", f"workflow_runs/{run_id}")
            if run["status"] not in {"pending", "running"}:
                assert run["status"] == "completed", [
                    (t["status"], t["state"].get("reason")) for t in run["tasks"]
                ]
                return run
            self.page.wait_for_timeout(120)
        raise AssertionError("Workflow did not finish")


def main():
    """Validate the live canvas, plugin lifecycle and cross-instance fixed version."""
    with (
        contextmanager(external_ledger.__wrapped__)() as ledger,
        sync_playwright() as play,
    ):
        browser = play.chromium.launch()
        with (
            instance("source", ledger) as source_instance,
            instance("target", ledger) as target_instance,
        ):
            source = Dashboard(browser, source_instance.url)
            target = Dashboard(browser, target_instance.url)
            assert source.api("GET", "workflow_components") == []
            credentials = {"username": "viewer", "password": PASSWORD + "User7"}
            source.api("POST", "accounts", credentials)
            ordinary = browser.new_context()
            checked(
                ordinary.request.post(
                    source.base + "/api/v1/auth/login", data=credentials
                )
            )
            token = next(
                c["value"]
                for c in ordinary.cookies()
                if c["name"] == "astrbot_dashboard_jwt"
            )
            denied = {"Authorization": "Bearer " + token}
            assert source.install(headers=denied).status == 403
            assert (
                ordinary.request.get(
                    source.base + "/api/v1/workflow_components", headers=denied
                ).status
                == 403
            )
            checked(source.install())
            component = source.api("GET", "workflow_components")[0]
            assert component["reference"]["id"] == f"workflow_tests/{PLUGIN}/record"
            source_kb = source.create_resource("Source ledger references", ledger)
            target_kb = target.create_resource("Target ledger references", ledger)
            assert source_kb != target_kb
            definition = ledger_graph(component, source_kb)
            definition["tasks"] = [definition["tasks"][1]]
            result = definition["tasks"][0]
            result.update(inputs={}, position={"x": 560, "y": 100})
            definition["edges"] = []
            created = source.api(
                "POST", "agent_drafts/workflow", {"body": {"definition": definition}}
            )
            identity = created["asset_id"]
            page = source.page

            def unavailable(route):
                """Simulate a failed component catalog request without changing the registry."""
                route.fulfill(status=500, body="Unavailable")

            page.route("**/api/v1/workflow_components", unavailable)
            source.editor(identity)
            catalog_error = page.get_by_role("alert").filter(
                has_text="The installed component catalog could not be loaded."
            )
            expect(catalog_error).to_be_visible()
            page.unroute("**/api/v1/workflow_components", unavailable)
            catalog_error.get_by_role("button", name="Refresh").click()
            add = page.get_by_test_id("add-plugin-component").filter(
                has_text="Record to test ledger"
            )
            with page.expect_response(
                lambda r: (
                    r.request.method in ("PATCH", "PUT") and "/agent_drafts/" in r.url
                )
            ) as saved:
                add.click()
            edited = checked(saved.value)
            plugin_task = next(
                t for t in edited["body"]["definition"]["tasks"] if t.get("component")
            )
            node_id = plugin_task["id"]
            page.locator(
                f'.vue-flow__node[data-id="{node_id}"] .member-flow-node'
            ).click()
            chooser = page.get_by_test_id("native-knowledge-base")
            with page.expect_response(
                lambda r: (
                    r.request.method in ("PATCH", "PUT") and "/agent_drafts/" in r.url
                )
            ) as saved:
                chooser.locator(".v-field").click()
                page.get_by_role("option", name="Source ledger references").click()
            edited = checked(saved.value)
            draft = edited["body"]["definition"]
            plugin_task = next(t for t in draft["tasks"] if t["id"] == node_id)
            plugin_task["position"] = {"x": 230, "y": 100}
            source.api(
                "PUT",
                f"agent_drafts/workflow/{identity}",
                {
                    "expected_revision": edited["revision"],
                    "body": {"definition": draft},
                },
            )
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".graph-stage")).to_be_visible()

            def connect(origin, destination):
                """Connect two Vue Flow handles with the browser pointer."""
                expect(origin).to_be_visible()
                expect(destination).to_be_visible()
                points = [
                    (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    for box in (origin.bounding_box(), destination.bounding_box())
                ]
                page.mouse.move(*points[0])
                page.mouse.down()
                page.mouse.move(*points[1], steps=20)
                page.mouse.up()

            def node(ident):
                """Select the real canvas node by persisted identity."""
                return page.locator(f'.vue-flow__node[data-id="{ident}"]')

            def is_draft_save(response):
                """Match the autosave caused by this connection."""
                return (
                    response.request.method == "PUT"
                    and "/agent_drafts/" in response.url
                )

            with page.expect_response(is_draft_save) as saved:
                connect(
                    node(node_id).locator(".at-control-handle.source"),
                    node("result").locator(".at-control-handle.target"),
                )
            checked(saved.value)
            with page.expect_response(is_draft_save) as saved:
                connect(
                    node("__input").locator('[data-handleid="data-out:record"]'),
                    node(node_id).locator('[data-handleid="data-in:record"]'),
                )
            checked(saved.value)
            with page.expect_response(is_draft_save) as saved:
                connect(
                    node(node_id).locator('[data-handleid="data-out:record.count"]'),
                    node("result").locator('[data-handleid="data-in:count"]'),
                )
            linked = checked(saved.value)["body"]["definition"]
            bindings = {item["id"]: item["inputs"] for item in linked["tasks"]}
            assert bindings[node_id]["record"]["input"] == "record"
            assert bindings["result"]["count"]["field"] == "record.count"
            for language in ("en-US", "zh-CN"):
                page.evaluate(
                    "(locale) => localStorage.setItem('astrbot-locale', locale)",
                    language,
                )
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(".graph-stage")).to_be_visible()
                page.locator(
                    f'.vue-flow__node[data-id="{node_id}"] .member-flow-node'
                ).click()
                expect(page.get_by_test_id("plugin-component-version")).to_be_visible()
                page.screenshot(path=SCRATCH / f"canvas-{language}.jpg", quality=86)
            page.evaluate("localStorage.setItem('astrbot-locale','en-US')")
            page.reload(wait_until="domcontentloaded")
            source.editor(identity)
            page.get_by_role("button", name="Test draft", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_test_id("structured-input-values").locator(
                "textarea"
            ).first.fill(json.dumps(VALUES))
            with page.expect_response(
                lambda r: r.url.endswith("/trials") and r.request.method == "POST"
            ) as trial:
                dialog.get_by_role("button", name="Start test", exact=True).click()
            assert source.completed(checked(trial.value)["run_id"])["result"] == {
                "count": 42
            }
            source.editor(identity)
            with page.expect_response(
                lambda r: r.url.endswith("/publish") and r.request.method == "POST"
            ) as publish:
                page.get_by_test_id("publish-workflow").click()
            assert checked(publish.value)["version"] == 1
            package = json.loads(
                source.raw("GET", f"workflow_assets/{identity}/export?version=1").text()
            )
            package = package["data"]
            assert package["format_version"] == 4
            assert "local-ledger-secret" not in json.dumps(package)
            request = {
                "package": package,
                "selections": [
                    {
                        "kind": "knowledge_bases",
                        "source_id": source_kb,
                        "target_id": target_kb,
                    }
                ],
            }
            assert not target.api("POST", "workflow_assets/import/preview", request)[
                "ready"
            ]
            checked(target.install())
            assert target.api("POST", "workflow_assets/import/preview", request)[
                "ready"
            ]
            target.api("PATCH", f"plugins/{PLUGIN}/enabled", {"enabled": False})
            target_instance.restart()
            assert target.api("GET", "workflow_components") == []
            assert not target.raw("POST", "workflow_assets/import", request).ok
            assert target.api("GET", "workflow_assets")["assets"] == []
            target.api("PATCH", f"plugins/{PLUGIN}/enabled", {"enabled": True})
            installed = target.api("POST", "workflow_assets/import", request)["asset"]
            run = target.api(
                "POST",
                f"workflow_assets/{installed['asset_id']}/runs",
                {
                    "version": installed["version"],
                    "inputs": VALUES,
                },
            )
            assert target.completed(run["run_id"])["result"] == {"count": 42}
            assert [effect["binding"] for effect in ledger.effects] == [
                source_kb,
                target_kb,
            ]
            print(
                f"Canvas, fixed port propagation, plugin lifecycle and migration passed. Evidence: {SCRATCH}"
            )


if __name__ == "__main__":
    main()
