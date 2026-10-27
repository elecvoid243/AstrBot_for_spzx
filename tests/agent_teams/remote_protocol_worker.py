"""Durable public pipeline checkpoint harness independent of task admission."""

import asyncio
import copy
import json
import sqlite3
import uuid
from pathlib import Path

from astrbot.core.agent_team_execution import execute_team_turn
from astrbot.core.config.agent_runner import get_agent_runner_capabilities


async def run_protocol_worker(folder: Path, action: str, scenario: str, ports, config):
    """Exercise real remote adapters through the public execution checkpoint seam.

    Args:
        folder: Durable callback storage shared across killed worker processes.
        action: Start the original invocation or restore its saved checkpoint.
        scenario: Provider and exact process or persistence loss boundary.
        ports: Real WebChat pipeline delivery and collection ports.
        config: Actual selected remote runner configuration.
    """
    _, window = scenario.split(":", 1)
    connection = sqlite3.connect(folder / "protocol.db")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
    )
    row = connection.execute("SELECT data FROM observations WHERE id=1").fetchone()
    state = (
        json.loads(row[0])
        if row
        else {
            "snapshot": {
                "runner": get_agent_runner_capabilities(config["agent_runner"]),
                "member": {
                    "session_id": uuid.uuid4().hex,
                    "member_id": uuid.uuid4().hex,
                    "system_prompt": "Check evidence.",
                },
            },
            "state": {"attempt_id": uuid.uuid4().hex, "task_id": uuid.uuid4().hex},
            "checkpoint": None,
            "process": [],
            "receipts": {},
            "status": "running",
        }
    )
    member = state["snapshot"]["member"]
    member["umo"] = "webchat:FriendMessage:" + member["session_id"]

    def persist():
        """Commit callback observations before allowing dependent remote work."""
        connection.execute(
            "INSERT OR REPLACE INTO observations VALUES (1, ?)", (json.dumps(state),)
        )
        connection.commit()

    def view():
        """Read public callback observations without interpreting text as a result.

        Returns:
            Complete checkpoint, process and usage observations for the parent.
        """
        checkpoint = state["checkpoint"] or {}
        runner = state["snapshot"]["runner"]
        receipts = [
            receipt
            for receipt in state["receipts"].values()
            if receipt["kind"] == "model"
        ]
        reported = [receipt.get("usage", {}) for receipt in receipts]
        totals = [
            usage.get(
                "total_tokens",
                usage["input_tokens"] + usage["output_tokens"]
                if "input_tokens" in usage and "output_tokens" in usage
                else None,
            )
            for usage in reported
        ]
        return {
            **copy.deepcopy(state),
            "recovery": {
                "boundary": checkpoint.get("phase"),
                "capability": runner["recovery"],
                "remote_identity": checkpoint.get("remote", {}).get("identity"),
                "available": bool(
                    checkpoint.get("phase") == "remote_result"
                    or checkpoint.get("remote", {}).get("identity")
                    and "remote_query" in runner["capabilities"]
                ),
            },
            "usage": {
                "model_calls": len(receipts),
                "tokens": {
                    "reported": sum(total for total in totals if total is not None)
                    if any(total is not None for total in totals)
                    else None,
                    "unknown_calls": sum(total is None for total in totals),
                },
            },
        }

    async def save(data):
        """Commit the actual adapter checkpoint before the selected loss boundary.

        Args:
            data: Complete production RemoteAgentCheckpoint payload.
        """
        if (
            action == "protocol_start"
            and window == "chat_thought_checkpoint_failure"
            and len(data.get("remote", {}).get("events", {})) > 1
        ):
            raise OSError("Injected remote event checkpoint save failure")
        state["checkpoint"] = copy.deepcopy(data)
        persist()
        boundary = (
            "remote_result"
            if window
            in {
                "result_saved",
                "end_success",
                "chat_result_saved",
                "chat_thoughts",
                "chat_workflow_events",
                "usage_missing",
                "usage_zero",
            }
            else "remote_running"
        )
        if (
            action == "protocol_start"
            and data["phase"] == boundary
            and window
            not in {
                "end_interrupted",
                "identity_missing",
                "chat_unknown",
                "chat_eof",
                "partial_eof",
                "accepted_disconnect",
                "accepted_redirect",
                "chat_thought_process_failure",
                "chat_thought_checkpoint_failure",
            }
        ):
            (folder / "kill-ready.json").write_text(
                json.dumps({"run_id": state["state"]["attempt_id"]}), encoding="utf-8"
            )
            await asyncio.Future()

    async def record(record_id, kind, data):
        """Persist provider process events at the public callback boundary.

        Args:
            record_id: Stable production event identity.
            kind: Production model response or provider process event kind.
            data: Original unmodified provider evidence.
        """
        if (
            action == "protocol_start"
            and window == "chat_thought_process_failure"
            and data.get("event", {}).get("observation")
        ):
            raise OSError("Injected remote process save failure")
        if not any(record["record_id"] == record_id for record in state["process"]):
            state["process"].append(
                {
                    "record_id": record_id,
                    "kind": kind,
                    "data": data,
                    "attempt_id": state["state"]["attempt_id"],
                    "task_id": state["state"]["task_id"],
                    "member_id": member["member_id"],
                }
            )
            persist()

    async def begin_usage(receipt):
        """Durably observe each admitted actual delivery.

        Args:
            receipt: Production delivery identity and kind.

        Returns:
            Saved delivery observation.
        """
        state["receipts"].setdefault(receipt["receipt_id"], copy.deepcopy(receipt))
        persist()
        return receipt

    async def report_usage(receipt_id, usage):
        """Keep reported zero distinct from an absent provider report.

        Args:
            receipt_id: Original production delivery identity.
            usage: Actual parsed production provider usage.

        Returns:
            Saved usage observation.
        """
        state["receipts"][receipt_id]["usage"] = copy.deepcopy(usage)
        persist()
        return usage

    persist()
    before = view()
    (folder / "started.json").write_text(
        json.dumps({"run_id": state["state"]["attempt_id"]}), encoding="utf-8"
    )
    error = None
    try:
        reply, parts = await execute_team_turn(
            ports=ports,
            member=member,
            run_id=state["state"]["attempt_id"],
            team_id="protocol-observation",
            username="alice",
            turn_id=state["state"]["attempt_id"],
            text="Find the original evidence.",
            execution={
                "config_id": "default",
                "tools": [],
                "skills": [],
                "kb_names": [],
                "message_id": uuid.uuid4().hex,
            },
            checkpoint=state["checkpoint"],
            save_checkpoint=save,
            record_process=record,
            begin_usage=begin_usage,
            report_usage=report_usage,
        )
        state["state"]["response"] = {"text": reply, "parts": parts}
        state["status"] = "response_saved"
    except Exception as exc:
        error = {"status": 409, "message": str(exc)}
        state["status"] = "blocked"
        state["state"]["reason"] = str(exc)
    persist()
    if action == "protocol_start":
        (folder / "kill-ready.json").write_text(
            json.dumps({"run_id": state["state"]["attempt_id"]}), encoding="utf-8"
        )
        await asyncio.Future()
    after = view()
    repeated = None
    if error is None:
        reply, parts = await execute_team_turn(
            ports=ports,
            member=member,
            run_id=state["state"]["attempt_id"],
            team_id="protocol-observation",
            username="alice",
            turn_id=state["state"]["attempt_id"],
            text="Find the original evidence.",
            execution={
                "config_id": "default",
                "tools": [],
                "skills": [],
                "kb_names": [],
                "message_id": uuid.uuid4().hex,
            },
            checkpoint=state["checkpoint"],
            save_checkpoint=save,
            record_process=record,
            begin_usage=begin_usage,
            report_usage=report_usage,
        )
        repeated = {"response": {"text": reply, "parts": parts}, "observations": view()}
    (folder / "recovered.json").write_text(
        json.dumps(
            {
                "before": before,
                "stopped": before,
                "after": after,
                "repeated": repeated,
                "error": error,
            }
        ),
        encoding="utf-8",
    )
    connection.close()
