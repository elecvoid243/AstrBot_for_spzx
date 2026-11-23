"""Versioned contracts supplied by installed workflow component plugins."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolRecovery
from astrbot.core.agent_teams.result_tool import validate_inline_port_contract

PLUGIN_COMPONENT = "plugin_component_v1"


class ComponentReference(BaseModel):
    """Exact registered component and its immutable contract fingerprint."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=5, max_length=300)
    version: str = Field(min_length=1, max_length=64, pattern=r"^[\w.+-]+$")
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class WorkflowComponentContext:
    """Authorized bindings and identity for one invocation of a component."""

    invocation_id: str
    knowledge_base_id: str | None


@dataclass(frozen=True)
class WorkflowComponent:
    """A typed, versioned workflow node supplied by a Star instance.

    Args:
        name: Stable component identifier within the plugin.
        version: Exact component version. Bump it when behavior changes.
        label: Display name for the node library.
        input_contract: Native object schema for named input ports.
        output_contract: Native object schema for named output ports.
        execute: Async operation returning a structured result.
        effect: Whether the operation can have external effects.
        knowledge_base: Whether this component requires read access to a bound KB.
        recovery: Native recovery guarantees for an externally effectful call.
    """

    name: str
    version: str
    label: str
    input_contract: dict
    output_contract: dict
    execute: Callable[[ContextWrapper[WorkflowComponentContext], dict], Awaitable[dict]]
    effect: Literal["read_only", "external"]
    knowledge_base: bool = False
    recovery: ToolRecovery | None = None

    def descriptor(self, plugin_id: str) -> dict:
        """Build the public, portable declaration without any runtime handlers.

        Args:
            plugin_id: Identity supplied by the AstrBot plugin loader.

        Returns:
            A catalog entry with its canonical contract fingerprint.

        Raises:
            ValueError: The declaration uses an unsupported contract.
        """
        if (
            not self.name
            or len(self.name) > 64
            or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in self.name)
            or not self.label.strip()
            or len(self.label) > 128
            or not callable(self.execute)
            or self.effect not in ("read_only", "external")
            or type(self.knowledge_base) is not bool
            or self.recovery is not None
            and self.effect != "external"
        ):
            raise ValueError("Invalid workflow component declaration")
        for contract in (self.input_contract, self.output_contract):
            validate_inline_port_contract(contract)
            if (
                not isinstance(contract, dict)
                or contract.get("type") != "object"
                or not isinstance(contract.get("properties"), dict)
            ):
                raise ValueError(
                    "Workflow component ports must declare object properties"
                )
        if "files" in self.output_contract["properties"]:
            raise ValueError(
                "Use semantic output ports to return bound file references"
            )
        guarantees = self.recovery_declaration
        declaration = {
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "effect": self.effect,
            "permissions": ["knowledge_base:read"] if self.knowledge_base else [],
            "recovery": guarantees,
        }
        canonical = json.dumps(
            declaration,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        reference = ComponentReference(
            id=f"{plugin_id}/{self.name}",
            version=self.version,
            contract_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        )
        return {
            "reference": reference.model_dump(),
            "label": self.label,
            **copy.deepcopy(declaration),
        }

    @property
    def recovery_declaration(self) -> dict | None:
        """Expose only the guarantees that the native receipt can verify."""
        if self.effect == "read_only":
            return ToolRecovery(safe_retry=True).declaration
        return self.recovery.declaration if self.recovery else None
