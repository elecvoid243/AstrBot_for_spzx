"""A real connector and workflow engine restart, with an independent HTTP ledger."""

import asyncio
import copy
import json
import os
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import DashboardAccount
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.star.star import StarMetadata
from tests.agent_teams.fixtures.workflow_ledger_plugin.main import WorkflowLedger
from tests.agent_teams.test_workflow_assets import completed_run


@pytest.fixture
def external_ledger():
    """Run an independent external recorder for calls and status queries."""
    effects, queries, deliveries = [], [], []
    response_gate = threading.Event()
    query_unknown = threading.Event()
    drop_receipt = threading.Event()
    response_gate.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/embeddings":
                inputs = (
                    request["input"]
                    if isinstance(request["input"], list)
                    else [request["input"]]
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "object": "list",
                            "model": "test-embedding",
                            "data": [
                                {
                                    "object": "embedding",
                                    "index": i,
                                    "embedding": [1, 0, 0],
                                }
                                for i, _ in enumerate(inputs)
                            ],
                            "usage": {"prompt_tokens": 1, "total_tokens": 1},
                        }
                    ).encode()
                )
                return
            assert request.pop("credential") == "local-ledger-secret"
            if self.path == "/record":
                deliveries.append(request)
                if not any(item["identity"] == request["identity"] for item in effects):
                    effects.append(request)
                response_gate.wait(timeout=30)
                if drop_receipt.is_set():
                    return
                result = {"record": {"count": request["record"]["count"] + 1}}
            else:
                queries.append(request)
                result = next(
                    (
                        {"record": {"count": item["record"]["count"] + 1}}
                        for item in effects
                        if item == request and not query_unknown.is_set()
                    ),
                    None,
                )
            with suppress(ConnectionError):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{server.server_port}",
                effects=effects,
                queries=queries,
                deliveries=deliveries,
                query_unknown=query_unknown,
                drop_receipt=drop_receipt,
                gate=response_gate,
            )
        finally:
            response_gate.set()
            server.shutdown()
            thread.join(timeout=2)


async def fixture_service(directory, binding):
    """Open an independent SQLite service with the same installed plugin declaration.

    Args:
        directory: Temporary instance directory.
        binding: Exact local test knowledge base identity.

    Returns:
        A configured workflow service and its plugin metadata.
    """
    directory.mkdir(parents=True, exist_ok=True)
    db = SQLiteDatabase(str(directory / "workflows.db"))
    await db.initialize()

    async def catalog():
        return {
            **{
                name: []
                for name in ("profiles", "models", "personas", "tools", "skills")
            },
            "knowledge_bases": [{"id": binding, "name": "Test ledger binding"}],
        }

    async def get_kb(kb_id):
        return SimpleNamespace(init_error=None) if kb_id == binding else None

    plugin = StarMetadata(
        name="astrbot_plugin_workflow_ledger", author="workflow_tests"
    )
    instance = WorkflowLedger(SimpleNamespace())
    plugin.star_cls = instance
    plugin.workflow_components = instance.workflow_components()
    core = SimpleNamespace(
        kb_manager=SimpleNamespace(get_kb=get_kb),
        star_context=SimpleNamespace(get_all_stars=lambda: [plugin]),
    )
    return WorkflowService(
        db, RoleAssetService(db, core, catalog_loader=catalog)
    ), plugin


async def configure_owner(service):
    """Create the isolated administrator account used by the fixture."""
    async with service.db.get_db() as session:
        session.add(
            DashboardAccount(
                account_id="alice",
                username="alice",
                password_hash="unused",
                role="admin",
            )
        )
        await session.commit()


def ledger_graph(component, binding):
    """Build the portable typed graph used by the external ledger scenarios."""
    output = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
    }
    return {
        "name": "Workflow ledger",
        "port_contract_version": 2,
        "input_contract": component["input_contract"],
        "result_task_id": "result",
        "edges": [{"source": "ledger", "target": "result"}],
        "tasks": [
            {
                "id": "ledger",
                "name": component["label"],
                "transform": "plugin_component_v1",
                "component": component["reference"],
                "input_contract": component["input_contract"],
                "output_contract": component["output_contract"],
                "knowledge_base_id": binding,
                "inputs": {"record": {"input": "record"}},
            },
            {
                "id": "result",
                "name": "Count from ledger",
                "transform": "map_fields",
                "input_contract": output,
                "output_contract": output,
                "inputs": {"count": {"task_id": "ledger", "field": "record.count"}},
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ["before-receipt", "after-receipt", "unknown", "ambiguous-query", "paused-query"],
)
async def test_external_component_process_restart(
    tmp_path, monkeypatch, external_ledger, boundary
):
    """Recover the same tool identity after killing a real worker process."""
    ledger = external_ledger
    if boundary != "after-receipt":
        ledger.gate.clear()
    if boundary == "ambiguous-query":
        ledger.query_unknown.set()
    env = {
        **os.environ,
        "ASTRBOT_ROOT": str(tmp_path / "core"),
        "ATN178_LEDGER_URL": ledger.url,
        "ATN178_LEDGER_KEY": "local-ledger-secret",
    }
    monkeypatch.setenv("ATN178_LEDGER_URL", ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    worktree = Path(__file__).resolve().parents[2]
    log_path = tmp_path / "worker.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.agent_teams.test_workflow_plugin_restart",
            str(tmp_path),
            boundary,
            cwd=worktree,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 35
            marker = tmp_path / "run.json"
            pause_requested = False
            while time.monotonic() < deadline:
                ready = (
                    (tmp_path / "receipt-committed").exists()
                    if boundary == "after-receipt"
                    else bool(ledger.effects)
                )
                if marker.exists() and ready:
                    if boundary != "paused-query" or (tmp_path / "paused").exists():
                        break
                    if not pause_requested:
                        assert process.stdin is not None
                        process.stdin.write(b"pause\n")
                        await process.stdin.drain()
                        pause_requested = True
                assert process.returncode is None, log_path.read_text(encoding="utf-8")
                await asyncio.sleep(0.02)
            else:
                pytest.fail(log_path.read_text(encoding="utf-8"))
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)
            ledger.gate.set()
    root_id = json.loads(marker.read_text(encoding="utf-8"))
    service, _ = await fixture_service(tmp_path, "source-kb")
    try:
        await service.boot_sweep()
        root = await service.get_run("alice", root_id)
        original = root["tasks"][0]
        task = await service.get_run("alice", original["run_id"])
        call = task["tool_calls"][0]
        assert call["record_id"] == ledger.effects[0]["identity"]
        assert call["attempt_id"] == original["state"]["attempt_id"]
        if boundary == "unknown":
            with pytest.raises(RoleAssetError, match="requires confirmation"):
                await service.recover_run("alice", original["run_id"])
            task = await service.get_run("alice", original["run_id"])
            assert task["tool_calls"][0]["status"] == "unknown"
            assert (await service.get_run("alice", root_id))["result"] is None
        elif boundary == "ambiguous-query":
            await service.recover_run("alice", original["run_id"])
            root = await completed_run(service, root_id)
            task = await service.get_run("alice", original["run_id"])
            call = task["tool_calls"][0]
            assert call["status"] == "unknown" and root["result"] is None
            assert (
                call["recovery"]["declaration"]["idempotency_key_arg"]
                == "invocation_key"
            )
            assert call["recovery"]["queries"][-1]["status"] == "unknown"
        else:
            if boundary == "paused-query":
                assert root["control"]["status"] == "paused"
                await service.control_run(
                    "alice",
                    root_id,
                    {
                        "request_id": "continue-after-restart",
                        "action": "continue",
                        "expected_revision": 1,
                    },
                )
            else:
                await service.recover_run("alice", original["run_id"])
            root = await completed_run(service, root_id)
            assert root["result"] == {"count": 42}, root
            assert root["tasks"][1]["result_ref"]["version"] == 1
            task = await service.get_run("alice", original["run_id"])
            call = task["tool_calls"][0]
            assert call["status"] == "applied"
            assert call["raw_results"][0]["structuredContent"] == {
                "record": {"count": 42}
            }
            assert call["record_id"] == ledger.effects[0]["identity"]
            assert call["attempt_id"] == original["state"]["attempt_id"]
            events = await service.get_events("alice", root_id, after=1)
            completed = [
                event
                for event in events["events"]
                if event["run_id"] == root["tasks"][1]["run_id"]
                and event["kind"] == "task.completed"
            ]
            assert len(completed) == 1
        assert ledger.effects == [
            {
                "identity": call["record_id"],
                "binding": "source-kb",
                "record": {"count": 41},
            }
        ]
        assert ledger.deliveries == ledger.effects
        assert ledger.queries == (
            ledger.effects
            if boundary in {"before-receipt", "ambiguous-query", "paused-query"}
            else []
        )
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_component_package_rebinds_exact_version(
    tmp_path, monkeypatch, external_ledger
):
    """Import the contract with the target's local authorized resource identity."""
    monkeypatch.setenv("ATN178_LEDGER_URL", external_ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    source, source_plugin = await fixture_service(tmp_path / "source", "source-kb")
    target, plugin = await fixture_service(tmp_path / "target", "target-kb")
    try:
        for service in (source, target):
            await configure_owner(service)
        entry = (await source.list_components("alice"))[0]
        asset = await source.save_asset(
            "alice", {"definition": ledger_graph(entry, "source-kb")}
        )
        source_plugin.activated = False
        parent = {
            "name": "Parent of fixed connector",
            "port_contract_version": 2,
            "entry_task_ids": ["child"],
            "input_contract": entry["input_contract"],
            "result_task_id": "child",
            "tasks": [
                {
                    "id": "child",
                    "name": "Fixed connector workflow",
                    "workflow": {"asset_id": asset["asset_id"], "version": 1},
                    "input_contract": entry["input_contract"],
                    "output_contract": asset["definition"]["tasks"][1][
                        "output_contract"
                    ],
                    "inputs": {"record": {"input": "record"}},
                }
            ],
        }
        with pytest.raises(RoleAssetError) as missing:
            await source.save_asset("alice", {"definition": parent})
        assert missing.value.fields[0]["path"].endswith(".component")
        source_plugin.activated = True
        await source.save_asset("alice", {"definition": parent})
        package = await source.export_asset("alice", asset["asset_id"], 1)
        assert package["format_version"] == 4
        assert package["definition"]["tasks"][0]["component"] == entry["reference"]
        assert "local-ledger-secret" not in json.dumps(package)
        assert "async def" not in json.dumps(package)
        request = {
            "package": package,
            "selections": [
                {
                    "kind": "knowledge_bases",
                    "source_id": "source-kb",
                    "target_id": "target-kb",
                }
            ],
        }
        plugin.activated = False
        preview = await target.preview_import("alice", request)
        assert not preview["ready"] and preview["issues"][0]["code"] == "MISSING"
        with pytest.raises(RoleAssetError):
            await target.import_asset("alice", request)
        assert (await target.list_assets("alice"))["assets"] == []
        plugin.activated = True
        assert (await target.preview_import("alice", request))["ready"]
        preview_import = target.preview_import

        async def disable_after_preview(*args):
            preview = await preview_import(*args)
            plugin.activated = False
            return preview

        with monkeypatch.context() as race:
            race.setattr(target, "preview_import", disable_after_preview)
            with pytest.raises(RoleAssetError, match="Install and enable"):
                await target.import_asset("alice", request)
        assert (await target.list_assets("alice"))["assets"] == []
        plugin.activated = True
        installed = (await target.import_asset("alice", request))["asset"]
        assert (await target.import_asset("alice", request))["existing"]
        assert installed["definition"]["tasks"][0]["knowledge_base_id"] == "target-kb"
        run = await target.start_run(
            "alice",
            installed["asset_id"],
            {
                "version": installed["version"],
                "inputs": {"record": {"count": 41}},
            },
        )
        assert (await completed_run(target, run["run_id"]))["result"] == {"count": 42}
        assert [effect["binding"] for effect in external_ledger.effects] == [
            "target-kb"
        ]
        incompatible = copy.deepcopy(request)
        incompatible["package"]["definition"]["tasks"][0]["component"][
            "contract_digest"
        ] = "a" * 64
        assert not (await target.preview_import("alice", incompatible))["ready"]
        async with target.db.get_db() as session:
            account = await session.get(DashboardAccount, "alice")
            account.role = "user"
            await session.commit()
        with pytest.raises(RoleAssetError) as denied:
            await target.start_run(
                "alice",
                installed["asset_id"],
                {
                    "version": installed["version"],
                    "inputs": {"record": {"count": 41}},
                },
            )
        assert denied.value.status == 403
        assert len(external_ledger.effects) == 1
    finally:
        await source.shutdown()
        await target.shutdown()


async def worker(directory, boundary):
    """Publish and run a frozen component before the parent kills this process."""
    service, _ = await fixture_service(directory, "source-kb")
    await configure_owner(service)
    if boundary == "after-receipt":
        save_checkpoint = service.save_checkpoint

        async def gated_checkpoint(*args):
            await save_checkpoint(*args)
            if args[-1]["phase"] == "tool_result":
                (directory / "receipt-committed").touch()
                await asyncio.Event().wait()

        service.save_checkpoint = gated_checkpoint
    entry = (await service.list_components("alice"))[boundary == "unknown"]
    asset = await service.save_asset(
        "alice", {"definition": ledger_graph(entry, "source-kb")}
    )
    run = await service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"record": {"count": 41}}}
    )
    (directory / "run.json").write_text(json.dumps(run["run_id"]), encoding="utf-8")
    if boundary == "paused-query":
        assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "pause"
        await service.control_run(
            "alice",
            run["run_id"],
            {
                "request_id": "pause-at-external-ledger",
                "action": "pause",
                "expected_revision": 0,
            },
        )
        (directory / "paused").touch()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(worker(Path(sys.argv[1]), sys.argv[2]))
