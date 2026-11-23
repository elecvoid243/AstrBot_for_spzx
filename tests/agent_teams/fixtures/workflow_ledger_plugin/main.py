"""Controlled installed workflow component used by the restart and browser fixtures."""

import os

import aiohttp
from mcp.types import CallToolResult

from astrbot.api.star import Star, WorkflowComponent
from astrbot.core.agent.tool import ToolRecovery, ToolRecoveryResult


class WorkflowLedger(Star):
    """Record a deterministic typed value with a stable external identity."""

    def workflow_components(self):
        """Expose queryable and unqueryable variants of the ledger connector.

        Returns:
            Exact component declarations for the two recovery scenarios.
        """
        schema = {
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                }
            },
            "required": ["record"],
        }
        return tuple(
            WorkflowComponent(
                name=name,
                version="1",
                label=label,
                input_contract=schema,
                output_contract=schema,
                effect="external",
                knowledge_base=True,
                execute=self.record,
                recovery=recovery,
            )
            for name, label, recovery in (
                (
                    "record",
                    "Record to test ledger",
                    ToolRecovery(
                        query=self.query, idempotency_key_arg="invocation_key"
                    ),
                ),
                ("unqueryable", "Record without query", None),
            )
        )

    async def record(self, context, inputs):
        """Write exactly one delivery to the test system.

        Args:
            context: Server-authorized knowledge base binding and invocation ID.
            inputs: Typed object port.

        Returns:
            Structured data from the externally stored result.
        """
        if "invocation_key" in inputs:
            assert inputs["invocation_key"] == context.context.invocation_id
        async with aiohttp.ClientSession() as client:
            async with client.post(
                os.environ["ATN178_LEDGER_URL"] + "/record",
                json={
                    "identity": context.context.invocation_id,
                    "binding": context.context.knowledge_base_id,
                    "record": inputs["record"],
                    "credential": os.environ["ATN178_LEDGER_KEY"],
                },
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def query(self, context, identity, arguments):
        """Read the authoritative result of the original external invocation.

        Args:
            context: Current authorized component binding.
            identity: Original durable invocation.
            arguments: Original structured inputs.

        Returns:
            Native recovery evidence and the original structured receipt.
        """
        async with aiohttp.ClientSession() as client:
            async with client.post(
                os.environ["ATN178_LEDGER_URL"] + "/query",
                json={
                    "identity": identity,
                    "binding": context.context.knowledge_base_id,
                    "record": arguments["record"],
                    "credential": os.environ["ATN178_LEDGER_KEY"],
                },
            ) as response:
                response.raise_for_status()
                result = await response.json()
        return ToolRecoveryResult(
            status="succeeded" if result else "unknown",
            evidence="Independent ledger lookup.",
            results=[CallToolResult(content=[], structuredContent=result)]
            if result
            else [],
        )
