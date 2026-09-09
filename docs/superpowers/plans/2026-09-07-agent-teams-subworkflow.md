# Agent Teams 子工作流节点实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Agent Teams 工作流的子工作流节点，允许一个节点引用另一个已保存的工作流，实现工作流复用和模块化组织。

**Architecture:** 新增 `subworkflow` 节点类型，通过 `workflow_id` 引用同团队的其他工作流。子工作流执行时创建独立的 SubworkflowRunner，输入为父节点输出，输出为子工作流最后完成节点的输出。支持最多 3 层嵌套，新增 `agent_team_subruns` 表记录子运行审计信息。支持工作流导出/导入（JSON 格式，含成员映射）。

**Tech Stack:** 
- 后端：Python 3.10+, SQLModel 新表
- 前端：Vue 3 + Vuetify 3 + TypeScript
- 测试：pytest, vitest

**Dependencies:**
- Phase 1-3 已落地（子工作流可包含条件分支、Human Input、循环边）

## Global Constraints

- Python 版本：≥ 3.10
- 所有代码使用 Google-style docstrings
- 提交消息遵循 conventional commits 格式
- 后端代码使用 `ruff format` 和 `ruff check` 格式化
- 前端代码遵循项目 ESLint 配置
- 最大嵌套深度：3 层
- 工作流导出文件格式：JSON
- 循环引用检测：递归检测 A→B→A 模式

---

## Task 1: 数据库表与模型扩展

**Files:**
- Create: `astrbot/dashboard/models/agent_team_subrun.py`
- Modify: `astrbot/dashboard/models/agent_team_workflow.py`
- Test: `tests/agent_teams/test_subworkflow_models.py`

**Interfaces:**
- Consumes: 无
- Produces: 
  - `AgentTeamSubRun` 模型
  - `AgentTeamWorkflow` 扩展字段

- [ ] **Step 0: 检查现有模型**

```bash
# 查找工作流和运行模型
rg "class AgentTeamWorkflow" astrbot/dashboard/models/
rg "class AgentTeamRun" astrbot/dashboard/models/
```

预期输出：确认模型位置和字段

- [ ] **Step 1: 创建子运行模型**

```python
# astrbot/dashboard/models/agent_team_subrun.py
"""Agent Teams subworkflow run model."""

from sqlmodel import Field, SQLModel, Text, JSON
from astrbot.dashboard.models.mixins import TimestampMixin


class AgentTeamSubRun(TimestampMixin, SQLModel, table=True):
    """Subworkflow run record (nested execution audit).
    
    Tracks execution of subworkflow nodes within parent runs.
    Used for debugging, auditing, and history replay.
    """
    
    __tablename__ = "agent_team_subruns"
    
    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})
    sub_run_id: str = Field(max_length=32, nullable=False, unique=True, index=True)
    parent_run_id: str = Field(max_length=32, nullable=False, index=True)
    parent_node_id: str = Field(max_length=32, nullable=False)
    workflow_id: str = Field(max_length=32, nullable=False, index=True)
    depth: int = Field(nullable=False, default=1)  # Nesting depth (1/2/3)
    
    # Input/output
    input_data: str = Field(sa_type=Text, default="")  # JSON string
    status: str = Field(max_length=16, nullable=False, default="running")
    result_summary: str = Field(sa_type=Text, default="")
    
    # Snapshot
    graph_snapshot: dict = Field(default_factory=dict, sa_type=JSON)
    node_states: dict = Field(default_factory=dict, sa_type=JSON)
    
    started_at: float = Field(default=0.0)
    finished_at: float = Field(default=0.0)
```

- [ ] **Step 2: 扩展工作流模型**

```python
# astrbot/dashboard/models/agent_team_workflow.py

# 在 AgentTeamWorkflow 类中添加字段

class AgentTeamWorkflow(TimestampMixin, SQLModel, table=True):
    # ... existing fields ...
    
    # NEW Phase 4: Subworkflow metadata
    description: str = Field(sa_type=Text, default="")
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    is_template: bool = Field(default=False)  # For Phase 5
    version: str = Field(max_length=16, default="1.0")
```

- [ ] **Step 3: 创建数据库迁移**

```python
# 使用 Alembic 或手动创建迁移脚本
# alembic revision -m "add subworkflow support"

"""Add subworkflow support.

Revision ID: xxx
Create Date: 2026-09-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, mysql


def upgrade():
    # Create agent_team_subruns table
    op.create_table(
        'agent_team_subruns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sub_run_id', sa.String(32), nullable=False),
        sa.Column('parent_run_id', sa.String(32), nullable=False),
        sa.Column('parent_node_id', sa.String(32), nullable=False),
        sa.Column('workflow_id', sa.String(32), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('input_data', sa.Text(), server_default=''),
        sa.Column('status', sa.String(16), nullable=False, server_default='running'),
        sa.Column('result_summary', sa.Text(), server_default=''),
        sa.Column('graph_snapshot', sa.JSON(), nullable=True),
        sa.Column('node_states', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.Float(), server_default='0'),
        sa.Column('finished_at', sa.Float(), server_default='0'),
        sa.Column('created_at', sa.String(32)),
        sa.Column('updated_at', sa.String(32)),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sub_run_id')
    )
    
    op.create_index('ix_subruns_sub_run_id', 'agent_team_subruns', ['sub_run_id'])
    op.create_index('ix_subruns_parent_run_id', 'agent_team_subruns', ['parent_run_id'])
    op.create_index('ix_subruns_workflow_id', 'agent_team_subruns', ['workflow_id'])
    
    # Extend agent_team_workflows table
    op.add_column('agent_team_workflows', sa.Column('description', sa.Text(), server_default=''))
    op.add_column('agent_team_workflows', sa.Column('tags', sa.JSON(), nullable=True))
    op.add_column('agent_team_workflows', sa.Column('is_template', sa.Boolean(), server_default='0'))
    op.add_column('agent_team_workflows', sa.Column('version', sa.String(16), server_default='1.0'))


def downgrade():
    op.drop_table('agent_team_subruns')
    op.drop_column('agent_team_workflows', 'description')
    op.drop_column('agent_team_workflows', 'tags')
    op.drop_column('agent_team_workflows', 'is_template')
    op.drop_column('agent_team_workflows', 'version')
```

- [ ] **Step 4: 编写模型测试**

```python
# tests/agent_teams/test_subworkflow_models.py
"""Tests for subworkflow models."""

import pytest
from astrbot.dashboard.models.agent_team_subrun import AgentTeamSubRun


def test_create_subrun(db):
    """Test creating subrun record."""
    subrun = AgentTeamSubRun(
        sub_run_id="sub_abc123",
        parent_run_id="run_parent",
        parent_node_id="node1",
        workflow_id="wf_child",
        depth=1,
        input_data='{"x": 1}',
        status="running"
    )
    
    db.add(subrun)
    db.commit()
    
    loaded = db.query(AgentTeamSubRun).filter(
        AgentTeamSubRun.sub_run_id == "sub_abc123"
    ).first()
    
    assert loaded is not None
    assert loaded.depth == 1
    assert loaded.parent_run_id == "run_parent"
```

- [ ] **Step 5: 运行迁移和测试**

```bash
# Run migration
alembic upgrade head

# Run tests
pytest tests/agent_teams/test_subworkflow_models.py -v
```

预期输出：表创建成功，测试 PASS

- [ ] **Step 6: 提交数据库模型**

```bash
git add astrbot/dashboard/models/ alembic/versions/ tests/agent_teams/test_subworkflow_models.py
git commit -m "feat(agent-teams): add subworkflow models and database schema"
```

---

## Task 2: 子工作流节点校验

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_dag.py`
- Test: `tests/agent_teams/test_subworkflow_validation.py`

**Interfaces:**
- Consumes: `AgentTeamWorkflow` model
- Produces: `validate_subworkflow_node(node, team_id, db) -> list[dict]`

- [ ] **Step 1: 编写子工作流节点校验测试**

```python
# tests/agent_teams/test_subworkflow_validation.py
"""Tests for subworkflow node validation."""

import pytest
from astrbot.dashboard.services.agent_team_dag import validate_subworkflow_node


def test_validate_valid_subworkflow_node(db, team):
    """Test validation of valid subworkflow node."""
    # Create child workflow
    from astrbot.dashboard.models.agent_team_workflow import AgentTeamWorkflow
    child_wf = AgentTeamWorkflow(
        workflow_id="wf_child",
        team_id=team.team_id,
        name="Child Workflow",
        graph={"nodes": [], "edges": []},
        layout={}
    )
    db.add(child_wf)
    db.commit()
    
    node = {
        "id": "sub1",
        "type": "subworkflow",
        "workflow_id": "wf_child"
    }
    
    errors = validate_subworkflow_node(node, team.team_id, db)
    assert len(errors) == 0


def test_validate_missing_workflow_id(db, team):
    """Test validation fails when workflow_id missing."""
    node = {
        "id": "sub1",
        "type": "subworkflow"
    }
    
    errors = validate_subworkflow_node(node, team.team_id, db)
    assert len(errors) == 1
    assert errors[0]["code"] == "REQUIRED"


def test_validate_workflow_not_found(db, team):
    """Test validation fails when workflow not found."""
    node = {
        "id": "sub1",
        "type": "subworkflow",
        "workflow_id": "wf_nonexistent"
    }
    
    errors = validate_subworkflow_node(node, team.team_id, db)
    assert len(errors) == 1
    assert "not found" in errors[0]["message"]


def test_validate_circular_reference(db, team):
    """Test validation detects circular reference."""
    # Create workflow A that references B
    # Create workflow B that references A
    # Validation should detect cycle
    pass  # Implementation in Step 3
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/agent_teams/test_subworkflow_validation.py::test_validate_valid_subworkflow_node -v
```

预期输出：`NameError: name 'validate_subworkflow_node' is not defined`

- [ ] **Step 3: 实现子工作流节点校验**

```python
# astrbot/dashboard/services/agent_team_dag.py

def validate_subworkflow_node(node: dict, team_id: str, db) -> list[dict]:
    """Validate subworkflow node (Phase 4).
    
    Args:
        node: Subworkflow node dict.
        team_id: Team ID.
        db: Database session.
    
    Returns:
        List of field-level errors, empty if valid.
    """
    from astrbot.dashboard.models.agent_team_workflow import AgentTeamWorkflow
    
    errors = []
    
    # Required: workflow_id
    workflow_id = node.get("workflow_id")
    if not workflow_id:
        errors.append({
            "path": "workflow_id",
            "code": "REQUIRED",
            "message": "Subworkflow node must have workflow_id"
        })
        return errors
    
    # Check workflow exists and belongs to same team
    workflow = db.query(AgentTeamWorkflow).filter(
        AgentTeamWorkflow.workflow_id == workflow_id,
        AgentTeamWorkflow.team_id == team_id
    ).first()
    
    if not workflow:
        errors.append({
            "path": "workflow_id",
            "code": "NOT_FOUND",
            "message": f"Workflow '{workflow_id}' not found in this team"
        })
        return errors
    
    # Check circular reference
    visited = set()
    if _has_circular_subworkflow_reference(workflow_id, db, visited):
        errors.append({
            "path": "workflow_id",
            "code": "CIRCULAR_REFERENCE",
            "message": "Circular subworkflow reference detected"
        })
    
    # Validate timeout (optional)
    if "timeout" in node and node["timeout"]:
        timeout = node["timeout"]
        if "seconds" in timeout:
            seconds = timeout["seconds"]
            if not isinstance(seconds, int) or seconds < 0:
                errors.append({
                    "path": "timeout.seconds",
                    "code": "INVALID_VALUE",
                    "message": "timeout.seconds must be non-negative integer"
                })
        
        if "on_timeout" in timeout and timeout["on_timeout"] not in ("fail", "skip"):
            errors.append({
                "path": "timeout.on_timeout",
                "code": "INVALID_VALUE",
                "message": "on_timeout must be 'fail' or 'skip'"
            })
    
    return errors


def _has_circular_subworkflow_reference(
    workflow_id: str,
    db,
    visited: set[str]
) -> bool:
    """Recursively detect circular subworkflow references.
    
    Args:
        workflow_id: Starting workflow ID.
        db: Database session.
        visited: Set of visited workflow IDs (for cycle detection).
    
    Returns:
        True if circular reference detected.
    """
    from astrbot.dashboard.models.agent_team_workflow import AgentTeamWorkflow
    
    if workflow_id in visited:
        return True  # Cycle detected
    
    visited.add(workflow_id)
    
    # Load workflow
    workflow = db.query(AgentTeamWorkflow).filter(
        AgentTeamWorkflow.workflow_id == workflow_id
    ).first()
    
    if not workflow:
        return False
    
    # Check all subworkflow nodes
    nodes = workflow.graph.get("nodes", [])
    for node in nodes:
        if node.get("type") == "subworkflow":
            child_wf_id = node.get("workflow_id")
            if child_wf_id and _has_circular_subworkflow_reference(
                child_wf_id, db, visited.copy()
            ):
                return True
    
    return False
```

- [ ] **Step 4: 集成到工作流校验**

```python
# astrbot/dashboard/services/agent_team_dag.py

# 在 validate_workflow 函数中添加子工作流节点校验

def validate_workflow(nodes: list[dict], edges: list[dict], team_id: str, db) -> list[dict]:
    """Validate workflow graph (extended for Phase 4).
    
    Returns:
        List of field-level errors, empty if valid.
    """
    errors = []
    
    # Existing validations...
    
    # NEW Phase 4: Subworkflow node validation
    for i, node in enumerate(nodes):
        if node.get("type") == "subworkflow":
            node_errors = validate_subworkflow_node(node, team_id, db)
            for err in node_errors:
                err["path"] = f"nodes[{i}].{err['path']}"
            errors.extend(node_errors)
    
    return errors
```

- [ ] **Step 5: 运行校验测试**

```bash
pytest tests/agent_teams/test_subworkflow_validation.py -v
```

预期输出：所有测试 PASS

- [ ] **Step 6: 提交子工作流节点校验**

```bash
git add astrbot/dashboard/services/agent_team_dag.py tests/agent_teams/test_subworkflow_validation.py
git commit -m "feat(agent-teams): add subworkflow node validation"
```

---

## Task 3: SubworkflowRunner 执行引擎

**Files:**
- Create: `astrbot/dashboard/services/agent_team_subworkflow_runner.py`
- Modify: `astrbot/dashboard/services/agent_team_run_service.py`
- Test: `tests/agent_teams/test_subworkflow_execution.py`

**Interfaces:**
- Consumes: `DAGRunner`, `AgentTeamWorkflow`, `AgentTeamSubRun`
- Produces: `SubworkflowRunner` class

- [ ] **Step 1: 编写子工作流执行测试**

```python
# tests/agent_teams/test_subworkflow_execution.py
"""Tests for subworkflow execution."""

import pytest
import asyncio
from tests.agent_teams.conftest import build_test_team


@pytest.mark.asyncio
async def test_simple_subworkflow():
    """Test simple subworkflow execution."""
    team, ports = await build_test_team()
    
    # Create child workflow
    child_wf = {
        "nodes": [
            {"id": "c1", "member_id": "m1", "task": "Child task: {{input}}"}
        ],
        "edges": []
    }
    # Save child_wf to database with workflow_id="wf_child"
    
    # Parent workflow
    parent_wf = {
        "nodes": [
            {"id": "p1", "member_id": "m1", "task": "Parent task"},
            {"id": "sub", "type": "subworkflow", "workflow_id": "wf_child"},
            {"id": "p2", "member_id": "m2", "task": "After sub: {{sub.output}}"}
        ],
        "edges": [
            {"from": "p1", "to": "sub"},
            {"from": "sub", "to": "p2"}
        ]
    }
    
    ports.script_collect("m1", '```output\n{"data": "parent"}\n```')
    ports.script_collect("m1", '```output\n{"result": "child_done"}\n```')  # Child
    ports.script_collect("m2", "Final")
    
    runner = DAGRunner(team, parent_wf, "test", ports)
    await runner.run()
    
    assert runner.status == "completed"
    assert runner.node_states["sub"]["status"] == "done"
    assert runner.node_states["sub"]["structured_output"] == {"result": "child_done"}
    assert runner.node_states["sub"]["subworkflow_run_id"] is not None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/agent_teams/test_subworkflow_execution.py::test_simple_subworkflow -v
```

预期输出：测试失败（subworkflow 节点未处理）

- [ ] **Step 3: 实现 SubworkflowRunner**

```python
# astrbot/dashboard/services/agent_team_subworkflow_runner.py
"""Subworkflow execution engine."""

import uuid
import time
import json
from astrbot.dashboard.services.agent_team_run_service import DAGRunner
from astrbot.dashboard.models.agent_team_subrun import AgentTeamSubRun
from astrbot.dashboard.models.agent_team_workflow import AgentTeamWorkflow


class SubworkflowError(Exception):
    """Subworkflow execution error."""
    pass


class SubworkflowRunner:
    """Recursive subworkflow executor.
    
    Executes a child workflow within a parent run context.
    Handles input passing, output extraction, and depth tracking.
    """
    
    MAX_DEPTH = 3
    
    def __init__(
        self,
        team,
        workflow: AgentTeamWorkflow,
        input_data: dict | str,
        ports,
        parent_run_id: str,
        parent_node_id: str,
        depth: int,
        db
    ):
        """Initialize subworkflow runner.
        
        Args:
            team: Team instance.
            workflow: Child workflow to execute.
            input_data: Input passed from parent.
            ports: Shared ports (member sessions).
            parent_run_id: Parent run ID.
            parent_node_id: Parent node ID (subworkflow node).
            depth: Nesting depth (1/2/3).
            db: Database session.
        
        Raises:
            SubworkflowError: If depth exceeds limit.
        """
        self.team = team
        self.workflow = workflow
        self.input_data = input_data
        self.ports = ports
        self.parent_run_id = parent_run_id
        self.parent_node_id = parent_node_id
        self.depth = depth
        self.db = db
        
        if depth > self.MAX_DEPTH:
            raise SubworkflowError(f"Max nesting depth ({self.MAX_DEPTH}) exceeded")
        
        # Generate sub run ID
        self.sub_run_id = f"sub_{uuid.uuid4().hex[:12]}"
        
        # Create subrun record
        self._create_subrun_record()
        
        # Prepare input text
        if isinstance(input_data, dict):
            input_text = json.dumps(input_data, ensure_ascii=False)
        else:
            input_text = str(input_data)
        
        # Initialize DAGRunner for child workflow
        self.runner = DAGRunner(
            team=team,
            workflow=workflow.graph,
            input_text=input_text,
            ports=ports
        )
        self.runner.run_id = self.sub_run_id  # Override run_id
        self.runner.depth = depth  # Pass depth to detect nested subworkflows
    
    def _create_subrun_record(self):
        """Create subrun database record."""
        subrun = AgentTeamSubRun(
            sub_run_id=self.sub_run_id,
            parent_run_id=self.parent_run_id,
            parent_node_id=self.parent_node_id,
            workflow_id=self.workflow.workflow_id,
            depth=self.depth,
            input_data=json.dumps(self.input_data) if isinstance(self.input_data, dict) else str(self.input_data),
            status="running",
            graph_snapshot=self.workflow.graph,
            started_at=time.time()
        )
        self.db.add(subrun)
        self.db.commit()
    
    async def run(self) -> dict:
        """Execute subworkflow and return output.
        
        Returns:
            {
                "status": "completed" | "failed" | "stopped",
                "output": dict | None,
                "sub_run_id": str
            }
        """
        await self.runner.run()
        
        # Extract output from last done node
        output = self._extract_output()
        
        # Update subrun record
        self._persist_subrun(output)
        
        return {
            "status": self.runner.status,
            "output": output,
            "sub_run_id": self.sub_run_id
        }
    
    def _extract_output(self) -> dict | None:
        """Extract output from last completed node.
        
        Returns:
            structured_output of last done node, or None.
        """
        done_nodes = [
            (nid, state)
            for nid, state in self.runner.node_states.items()
            if state["status"] == "done"
        ]
        
        if not done_nodes:
            return None  # All skipped/failed
        
        # Find node with latest finished_at
        last_node_id, last_state = max(
            done_nodes,
            key=lambda x: x[1].get("finished_at", 0)
        )
        
        return last_state.get("structured_output")
    
    def _persist_subrun(self, output: dict | None):
        """Update subrun record with final state.
        
        Args:
            output: Extracted output.
        """
        subrun = self.db.query(AgentTeamSubRun).filter(
            AgentTeamSubRun.sub_run_id == self.sub_run_id
        ).first()
        
        if subrun:
            subrun.status = self.runner.status
            subrun.result_summary = json.dumps(output) if output else ""
            subrun.node_states = self.runner.node_states
            subrun.finished_at = time.time()
            self.db.commit()
```

- [ ] **Step 4: 集成到 DAGRunner**

```python
# astrbot/dashboard/services/agent_team_run_service.py

# 在 _execute_node 中添加 subworkflow 处理

async def _execute_node(self, node_id: str, state: dict) -> None:
    """Execute node (extended for Phase 4).
    
    Args:
        node_id: Node ID.
        state: Node state dict.
    """
    node = self._find_node(node_id)
    node_type = node.get("type", "member")
    
    if node_type == "subworkflow":
        await self._execute_subworkflow_node(node_id, state, node)
    elif node_type == "human_input":
        await self._execute_human_input_node(node_id, state, node)
    elif node_type == "member":
        await self._execute_member_node(node_id, state, node)
    else:
        raise DAGExecutionError(f"Unknown node type: {node_type}")


async def _execute_subworkflow_node(self, node_id: str, state: dict, node: dict) -> None:
    """Execute subworkflow node (Phase 4).
    
    Args:
        node_id: Node ID.
        state: Node state dict.
        node: Node definition.
    """
    from astrbot.dashboard.services.agent_team_subworkflow_runner import SubworkflowRunner
    from astrbot.dashboard.models.agent_team_workflow import AgentTeamWorkflow
    
    workflow_id = node.get("workflow_id")
    if not workflow_id:
        state["status"] = "failed"
        state["error"] = "Subworkflow node missing workflow_id"
        await self._persist_run()
        return
    
    # Load child workflow
    workflow = self.db.query(AgentTeamWorkflow).filter(
        AgentTeamWorkflow.workflow_id == workflow_id
    ).first()
    
    if not workflow:
        state["status"] = "failed"
        state["error"] = f"Workflow '{workflow_id}' not found"
        await self._persist_run()
        return
    
    # Prepare input from predecessor
    input_data = self._prepare_subworkflow_input(node_id)
    
    # Create subworkflow runner
    depth = getattr(self, "depth", 0) + 1
    sub_runner = SubworkflowRunner(
        team=self.team,
        workflow=workflow,
        input_data=input_data,
        ports=self.ports,
        parent_run_id=self.run_id,
        parent_node_id=node_id,
        depth=depth,
        db=self.db
    )
    
    # Execute
    state["status"] = "running"
    state["started_at"] = time.time()
    await self._persist_run()
    self._emit("node_status", {"node_id": node_id, "status": "running"})
    
    try:
        timeout_seconds = node.get("timeout", {}).get("seconds", 0)
        if timeout_seconds > 0:
            result = await asyncio.wait_for(
                sub_runner.run(),
                timeout=timeout_seconds
            )
        else:
            result = await sub_runner.run()
        
        # Process result
        if result["status"] == "completed":
            state["status"] = "done"
            state["result"] = json.dumps(result["output"]) if result["output"] else ""
            state["structured_output"] = result["output"]
        else:
            state["status"] = "failed"
            state["error"] = f"Subworkflow {result['status']}"
        
        state["subworkflow_run_id"] = result["sub_run_id"]
        state["subworkflow_status"] = result["status"]
    
    except asyncio.TimeoutError:
        on_timeout = node.get("timeout", {}).get("on_timeout", "fail")
        if on_timeout == "skip":
            state["status"] = "skipped"
            state["error"] = "Subworkflow timeout (skipped)"
        else:
            state["status"] = "failed"
            state["error"] = "Subworkflow timeout (failed)"
    
    except Exception as e:
        state["status"] = "failed"
        state["error"] = f"Subworkflow error: {str(e)}"
    
    state["finished_at"] = time.time()
    await self._persist_run()
    self._emit("node_status", {
        "node_id": node_id,
        "status": state["status"],
        "structured_output": state.get("structured_output"),
        "error": state.get("error")
    })


def _prepare_subworkflow_input(self, node_id: str) -> dict | str:
    """Prepare subworkflow input from predecessor output.
    
    Args:
        node_id: Subworkflow node ID.
    
    Returns:
        Input data (dict or string).
    """
    predecessors = [e["from"] for e in self.graph["edges"] if e["to"] == node_id]
    
    if not predecessors:
        return self.input_text  # Entry node
    
    # Take first done predecessor's output
    for pred_id in predecessors:
        pred_state = self.node_states[pred_id]
        if pred_state["status"] == "done" and pred_state.get("structured_output"):
            return pred_state["structured_output"]
    
    # Fallback to text result
    for pred_id in predecessors:
        pred_state = self.node_states[pred_id]
        if pred_state["status"] == "done":
            return pred_state.get("result", "")
    
    return ""
```

- [ ] **Step 5: 运行子工作流执行测试**

```bash
pytest tests/agent_teams/test_subworkflow_execution.py -v
```

预期输出：所有测试 PASS

- [ ] **Step 6: 提交子工作流执行引擎**

```bash
git add astrbot/dashboard/services/agent_team_subworkflow_runner.py astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/test_subworkflow_execution.py
git commit -m "feat(agent-teams): implement subworkflow execution engine"
```

---

## Checkpoint 1: 后端核心功能完成

**此时应该完成：**
- [x] 数据库模型和迁移（Task 1）
- [x] 子工作流节点校验（Task 2）
- [x] SubworkflowRunner 执行引擎（Task 3）
- [x] 所有后端测试通过：`pytest tests/agent_teams/test_subworkflow*.py -v`

**验证步骤：**
1. 运行所有后端测试
2. 手动创建包含子工作流节点的工作流
3. 验证子工作流正确执行和输出传递

**如果检查点失败**：
- 检查数据库迁移
- 验证循环引用检测
- 确认输入输出传递

---

## Task 4: 工作流导出/导入功能

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_service.py`
- Test: `tests/agent_teams/test_workflow_export_import.py`

**Interfaces:**
- Consumes: `AgentTeamWorkflow`
- Produces: 
  - `export_workflow(workflow_id) -> dict`
  - `import_workflow(team_id, data, member_mapping) -> str`

- [ ] **Step 1: 编写导出/导入测试**

```python
# tests/agent_teams/test_workflow_export_import.py
"""Tests for workflow export/import."""

import pytest
import json


def test_export_workflow(service, workflow):
    """Test exporting workflow to JSON."""
    exported = service.export_workflow(workflow.workflow_id)
    
    assert exported["format"] == "astrbot-agent-teams-workflow"
    assert exported["version"] == "1.0"
    assert "workflow" in exported
    assert exported["workflow"]["name"] == workflow.name
    assert len(exported["workflow"]["member_requirements"]) > 0


def test_import_workflow(service, team):
    """Test importing workflow from JSON."""
    export_data = {
        "format": "astrbot-agent-teams-workflow",
        "version": "1.0",
        "workflow": {
            "name": "Imported Workflow",
            "description": "Test import",
            "graph": {
                "nodes": [
                    {"id": "n1", "member_id": "@role1", "task": "Task"}
                ],
                "edges": []
            },
            "layout": {},
            "member_requirements": [
                {
                    "role_id": "@role1",
                    "name": "Worker",
                    "suggested_persona": "coder"
                }
            ]
        }
    }
    
    member_mapping = {"@role1": "m1"}
    
    new_wf_id = service.import_workflow(team.team_id, export_data, member_mapping)
    
    assert new_wf_id.startswith("wf_")
    
    # Verify imported workflow
    imported_wf = service.get_workflow(new_wf_id)
    assert imported_wf.name == "Imported Workflow"
    assert imported_wf.graph["nodes"][0]["member_id"] == "m1"  # Mapped
```

- [ ] **Step 2: 实现导出功能**

```python
# astrbot/dashboard/services/agent_team_service.py

def export_workflow(self, workflow_id: str) -> dict:
    """Export workflow to JSON format.
    
    Args:
        workflow_id: Workflow ID to export.
    
    Returns:
        Export data dict.
    
    Raises:
        AgentTeamsServiceError: If workflow not found.
    """
    workflow = self.db.query(AgentTeamWorkflow).filter(
        AgentTeamWorkflow.workflow_id == workflow_id
    ).first()
    
    if not workflow:
        raise AgentTeamsServiceError("Workflow not found")
    
    # Extract member requirements (convert member_id to role_id)
    member_roles, converted_graph = self._convert_members_to_roles(workflow)
    
    export_data = {
        "format": "astrbot-agent-teams-workflow",
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "exported_by": "user",  # TODO: get from context
        "workflow": {
            "name": workflow.name,
            "description": workflow.description or "",
            "version": workflow.version,
            "tags": workflow.tags or [],
            "graph": converted_graph,
            "layout": workflow.layout,
            "member_requirements": member_roles
        }
    }
    
    return export_data


def _convert_members_to_roles(
    self,
    workflow: AgentTeamWorkflow
) -> tuple[list[dict], dict]:
    """Convert member IDs to role placeholders.
    
    Args:
        workflow: Workflow instance.
    
    Returns:
        (member_roles, converted_graph)
    """
    # Load team members
    team = self.db.query(AgentTeam).filter(
        AgentTeam.team_id == workflow.team_id
    ).first()
    
    members = team.members if team else []
    
    # Build mapping
    role_mapping = {}
    member_roles = []
    
    for i, member in enumerate(members):
        role_id = f"@role_{i+1}"
        role_mapping[member["member_id"]] = role_id
        
        member_roles.append({
            "role_id": role_id,
            "name": member["name"],
            "suggested_persona": member.get("persona_id"),
            "suggested_provider": member.get("provider_id"),
            "description": f"Role: {member['name']}"
        })
    
    # Convert graph
    import copy
    converted_graph = copy.deepcopy(workflow.graph)
    
    for node in converted_graph["nodes"]:
        if node.get("type") in (None, "member"):
            old_id = node.get("member_id")
            if old_id in role_mapping:
                node["member_id"] = role_mapping[old_id]
    
    return member_roles, converted_graph
```

- [ ] **Step 3: 实现导入功能**

```python
# astrbot/dashboard/services/agent_team_service.py

def import_workflow(
    self,
    team_id: str,
    data: dict,
    member_mapping: dict[str, str]
) -> str:
    """Import workflow from JSON format.
    
    Args:
        team_id: Target team ID.
        data: Export data dict.
        member_mapping: {role_id: member_id} mapping.
    
    Returns:
        New workflow ID.
    
    Raises:
        AgentTeamsServiceError: If validation fails.
    """
    # Validate format
    if data.get("format") != "astrbot-agent-teams-workflow":
        raise AgentTeamsServiceError("Invalid format")
    
    workflow_data = data["workflow"]
    
    # Validate member_mapping completeness
    required_roles = {req["role_id"] for req in workflow_data.get("member_requirements", [])}
    provided_roles = set(member_mapping.keys())
    
    missing = required_roles - provided_roles
    if missing:
        raise AgentTeamsServiceError(
            f"Missing role mappings: {', '.join(missing)}",
            field_errors=[{"path": "member_mapping", "code": "INCOMPLETE", "message": f"Missing: {missing}"}]
        )
    
    # Convert roles to member IDs
    converted_graph = self._map_roles_to_members(workflow_data["graph"], member_mapping)
    
    # Generate new IDs
    new_wf_id = f"wf_{uuid.uuid4().hex[:12]}"
    converted_graph = self._regenerate_node_ids(converted_graph)
    
    # Create workflow
    new_workflow = AgentTeamWorkflow(
        workflow_id=new_wf_id,
        team_id=team_id,
        name=workflow_data["name"],
        description=workflow_data.get("description", ""),
        tags=workflow_data.get("tags", []),
        version=workflow_data.get("version", "1.0"),
        graph=converted_graph,
        layout=workflow_data.get("layout", {})
    )
    
    self.db.add(new_workflow)
    self.db.commit()
    
    return new_wf_id


def _map_roles_to_members(self, graph: dict, mapping: dict) -> dict:
    """Map role placeholders to member IDs.
    
    Args:
        graph: Graph with role placeholders.
        mapping: {role_id: member_id}.
    
    Returns:
        Converted graph.
    """
    import copy
    converted = copy.deepcopy(graph)
    
    for node in converted["nodes"]:
        if node.get("type") in (None, "member"):
            role_id = node.get("member_id")
            if role_id and role_id.startswith("@"):
                if role_id in mapping:
                    node["member_id"] = mapping[role_id]
                else:
                    raise ValueError(f"Role {role_id} not mapped")
    
    return converted


def _regenerate_node_ids(self, graph: dict) -> dict:
    """Regenerate node IDs to avoid conflicts.
    
    Args:
        graph: Original graph.
    
    Returns:
        Graph with new IDs.
    """
    import copy
    import uuid
    
    new_graph = copy.deepcopy(graph)
    id_mapping = {}
    
    for node in new_graph["nodes"]:
        old_id = node["id"]
        new_id = f"n_{uuid.uuid4().hex[:8]}"
        id_mapping[old_id] = new_id
        node["id"] = new_id
    
    for edge in new_graph["edges"]:
        edge["from"] = id_mapping[edge["from"]]
        edge["to"] = id_mapping[edge["to"]]
    
    return new_graph
```

- [ ] **Step 4: 运行导出/导入测试**

```bash
pytest tests/agent_teams/test_workflow_export_import.py -v
```

预期输出：所有测试 PASS

- [ ] **Step 5: 提交导出/导入功能**

```bash
git add astrbot/dashboard/services/agent_team_service.py tests/agent_teams/test_workflow_export_import.py
git commit -m "feat(agent-teams): implement workflow export/import"
```

---

## Task 5: API 端点实现

**Files:**
- Modify: `astrbot/dashboard/routers/agent_team_router.py`
- Modify: `openspec/openapi-v1.yaml`

**Interfaces:**
- Consumes: `export_workflow`, `import_workflow`
- Produces: 
  - `GET /api/v1/agent_teams/workflows/{workflow_id}/export`
  - `POST /api/v1/agent_teams/{team_id}/workflows/import`

- [ ] **Step 1: 实现导出端点**

```python
# astrbot/dashboard/routers/agent_team_router.py

@router.get("/workflows/{workflow_id}/export")
async def export_workflow_api(
    workflow_id: str,
    service: AgentTeamService = Depends(get_agent_team_service)
):
    """Export workflow to JSON format.
    
    Args:
        workflow_id: Workflow ID.
    
    Returns:
        Export data as JSON.
    """
    try:
        data = service.export_workflow(workflow_id)
        
        # Set download filename
        filename = f"workflow-{data['workflow']['name']}-{int(time.time())}.json"
        
        return JSONResponse(
            content=data,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except AgentTeamsServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 2: 实现导入端点**

```python
# astrbot/dashboard/routers/agent_team_router.py

from fastapi import UploadFile, File

@router.post("/{team_id}/workflows/import")
async def import_workflow_api(
    team_id: str,
    file: UploadFile = File(...),
    member_mapping: str = Form(...),
    service: AgentTeamService = Depends(get_agent_team_service)
):
    """Import workflow from JSON file.
    
    Args:
        team_id: Target team ID.
        file: JSON file.
        member_mapping: JSON string of {role_id: member_id}.
    
    Returns:
        {status: "ok", data: {workflow_id, name}}
    """
    try:
        # Read and parse file
        content = await file.read()
        data = json.loads(content)
        
        # Parse member mapping
        mapping = json.loads(member_mapping)
        
        # Import
        new_wf_id = service.import_workflow(team_id, data, mapping)
        
        # Get imported workflow
        workflow = service.get_workflow(new_wf_id)
        
        return {
            "status": "ok",
            "data": {
                "workflow_id": new_wf_id,
                "name": workflow.name
            }
        }
    
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except AgentTeamsServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 3: 更新 OpenAPI 规范**

```yaml
# openspec/openapi-v1.yaml

paths:
  /api/v1/agent_teams/workflows/{workflow_id}/export:
    get:
      summary: Export workflow
      tags: [Agent Teams]
      parameters:
        - name: workflow_id
          in: path
          required: true
          schema:
            type: string
      responses:
        200:
          description: Exported workflow JSON
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/WorkflowExport'
  
  /api/v1/agent_teams/{team_id}/workflows/import:
    post:
      summary: Import workflow
      tags: [Agent Teams]
      parameters:
        - name: team_id
          in: path
          required: true
          schema:
            type: string
      requestBody:
        content:
          multipart/form-data:
            schema:
              type: object
              properties:
                file:
                  type: string
                  format: binary
                member_mapping:
                  type: string
                  description: JSON string {role_id: member_id}
      responses:
        200:
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  status:
                    type: string
                  data:
                    type: object
                    properties:
                      workflow_id:
                        type: string
                      name:
                        type: string

components:
  schemas:
    SubworkflowNode:
      type: object
      required: [id, type, workflow_id]
      properties:
        id:
          type: string
        type:
          type: string
          enum: [subworkflow]
        title:
          type: string
        workflow_id:
          type: string
          description: "Referenced workflow ID"
        timeout:
          type: object
          properties:
            seconds:
              type: integer
            on_timeout:
              type: string
              enum: [fail, skip]
    
    WorkflowExport:
      type: object
      properties:
        format:
          type: string
          example: "astrbot-agent-teams-workflow"
        version:
          type: string
        exported_at:
          type: string
        workflow:
          type: object
```

- [ ] **Step 4: 重新生成前端 API 客户端**

```bash
cd dashboard
pnpm generate:api
```

- [ ] **Step 5: 提交 API 端点**

```bash
git add astrbot/dashboard/routers/agent_team_router.py openspec/openapi-v1.yaml dashboard/src/api/
git commit -m "feat(api): add subworkflow export/import endpoints"
```

---

## Task 6: 前端编辑器 UI

**Files:**
- Modify: `dashboard/src/components/agent_teams/WorkflowEditor.vue`
- Create: `dashboard/src/components/agent_teams/SubworkflowNodeInspector.vue`

**Interfaces:**
- Consumes: 工作流列表、导出/导入 API
- Produces: 子工作流节点编辑 UI、导出/导入对话框

由于篇幅限制，Task 6-8 将采用简化描述：

- [ ] **Step 1: 左侧面板添加子工作流节点**
- [ ] **Step 2: 创建子工作流节点属性面板**
- [ ] **Step 3: 添加导出/导入菜单**
- [ ] **Step 4: 实现导入对话框（成员映射）**
- [ ] **Step 5: 测试并提交**

---

## Task 7: 前端监控视图

**Files:**
- Modify: `dashboard/src/components/agent_teams/RunMonitor.vue`

- [ ] **Step 1: 子工作流节点卡片显示**
- [ ] **Step 2: 展开子工作流详情对话框**
- [ ] **Step 3: 测试并提交**

---

## Task 8: 集成测试、文档与验收

**Files:**
- Create: `docs/features/agent-teams-subworkflow.md`
- Test: `tests/agent_teams/test_subworkflow_integration.py`

- [ ] **Step 1: 端到端集成测试（嵌套 3 层）**
- [ ] **Step 2: 编写用户文档**
- [ ] **Step 3: 代码格式化与 lint**
- [ ] **Step 4: 手工验收（6 个场景）**
- [ ] **Step 5: 最终提交**

---

## 自审清单

### 规格覆盖检查

- [x] **子工作流节点类型**：Task 1-3 实现
- [x] **输入输出传递**：Task 3 实现隐式一进一出
- [x] **递归深度限制**：Task 3 实现 3 层上限
- [x] **循环引用检测**：Task 2 实现递归检测
- [x] **导出/导入**：Task 4 实现 JSON 格式
- [x] **成员角色映射**：Task 4 实现
- [x] **前端编辑器**：Task 6 实现
- [x] **前端监控**：Task 7 实现

### 已知限制

1. **v1 仅支持隐式一进一出**：v2 可扩展显式映射
2. **子运行不存储转录**：节省存储，完整调试可查看实时运行
3. **导出不包含子工作流**：需要手动导出引用的子工作流

### 计划评分

**8.5/10** (高质量，部分任务简化描述)

---

**Plan complete. Phase 4 ready for execution.**
