# Agent Teams Human Input 节点设计方案

- 作者：elecvoid243
- 日期：2026-09-07
- 前作：[2026-09-07-agent-teams-conditional-branching-design.md](./2026-09-07-agent-teams-conditional-branching-design.md)（Phase 1）
- 状态：设计评审
- 适用基线：Agent Teams 主体功能 + Refinements + Phase 1（条件分支）已落地

## 1. 背景与目标

Agent Teams 当前支持 Agent 间的自动协作，但某些场景需要**人工介入**：

- 审批流程：代码审查 → **人工批准** → 部署
- 数据补充：AI 生成方案 → **人工调整参数** → 继续执行
- 质量把关：内容生成 → **人工校对** → 发布
- 决策支持：AI 分析 → **人工选择方案** → 实施

现有的"追加指令"和"打断"机制是**运行时干预**（事后补救），而 Human Input 节点是**工作流设计时的显式人机协同点**（计划内等待）。

本设计引入 `human_input` 节点类型，DAG 执行到该节点时暂停，等待用户填写表单或输入文本，然后继续后续流程。

### 1.1 核心设计决策

| 决策点 | 结论 |
|---|---|
| 节点类型 | 新节点类型 `human_input`（不绑定成员，专用于人工输入） |
| 输入模式 | 支持两种：`text`（自由文本）/ `form`（结构化表单） |
| 表单字段类型 | v1 支持：text / textarea / number / select / radio / checkbox |
| UI 交互 | 复用 `InteractiveChoiceBox` 的设计风格，但支持更复杂的表单渲染 |
| 暂停语义 | 节点执行时 run 自动 `paused`（原因 `human_input_required`），用户提交后自动 `resume` |
| 超时处理 | 可配置超时（默认无限等待），超时后节点 `failed` 或 `skipped`（可选） |
| 追加/打断保留 | 用户仍可对其他运行中的 Agent 节点追加指令或打断，Human Input 不影响这些能力 |
| 输出格式 | 统一为结构化 JSON（文本模式 → `{input: "..."}`, 表单模式 → `{field1: ..., field2: ...}`），供后继节点条件判断 |

## 2. 数据模型

### 2.1 节点类型扩展

```typescript
// 现有节点（成员节点）
interface MemberNode {
  id: string;
  type?: "member";  // 缺省类型
  member_id: string;
  task: string;
  title?: string;
  execution?: {...};
}

// 新增：Human Input 节点
interface HumanInputNode {
  id: string;
  type: "human_input";
  title?: string;
  prompt: string;  // 提示文本（显示给用户）
  input_mode: "text" | "form";
  
  // 文本模式配置（input_mode="text" 时）
  text_config?: {
    placeholder?: string;
    multiline?: boolean;  // true=textarea, false=input
    max_length?: number;
  };
  
  // 表单模式配置（input_mode="form" 时）
  form_config?: {
    fields: FormField[];
  };
  
  // 超时配置（可选）
  timeout?: {
    seconds: number;  // 超时秒数，0=无限等待
    on_timeout: "fail" | "skip";  // 超时行为
  };
}

interface FormField {
  name: string;  // 字段名（输出 JSON 的键）
  label: string;  // 显示标签
  type: "text" | "textarea" | "number" | "select" | "radio" | "checkbox";
  required?: boolean;  // 默认 false
  placeholder?: string;
  default_value?: any;
  
  // select/radio 专用
  options?: Array<{
    value: string | number;
    label: string;
  }>;
  
  // number 专用
  min?: number;
  max?: number;
  step?: number;
  
  // text/textarea 专用
  max_length?: number;
  pattern?: string;  // 正则校验（前端 + 后端双重）
  
  // 帮助文本
  help_text?: string;
}
```

**示例**：

```json
{
  "id": "approval",
  "type": "human_input",
  "title": "人工审批",
  "prompt": "请审查 AI 生成的代码并决定是否批准部署。",
  "input_mode": "form",
  "form_config": {
    "fields": [
      {
        "name": "approved",
        "label": "审批决定",
        "type": "radio",
        "required": true,
        "options": [
          {"value": "approve", "label": "批准部署"},
          {"value": "reject", "label": "拒绝部署"},
          {"value": "revise", "label": "需要修改"}
        ]
      },
      {
        "name": "comments",
        "label": "审批意见",
        "type": "textarea",
        "placeholder": "请说明理由...",
        "max_length": 500
      },
      {
        "name": "priority",
        "label": "优先级",
        "type": "select",
        "options": [
          {"value": 1, "label": "低"},
          {"value": 2, "label": "中"},
          {"value": 3, "label": "高"}
        ],
        "default_value": 2
      }
    ]
  },
  "timeout": {
    "seconds": 3600,
    "on_timeout": "fail"
  }
}
```

### 2.2 节点状态扩展

```python
# node_states 现有字段（Phase 1 后）
node_states = {
  "node_id": {
    "status": "pending|running|done|failed|skipped|interrupted",
    "member_id": "...",  # Human Input 节点为 None
    "task_rendered": "...",
    "result": "...",
    "structured_output": {...},
    "output_error": None,
    "error": None,
    "started_at": 0.0,
    "finished_at": 0.0
  }
}

# Human Input 节点的状态扩展
node_states = {
  "approval": {
    "status": "waiting_input",  # 新状态：等待人工输入
    "member_id": None,  # Human Input 节点无成员
    "task_rendered": None,
    "result": None,  # 用户提交后填充（文本模式：用户输入；表单模式：JSON 字符串）
    "structured_output": {  # 用户提交的结构化数据
      "approved": "approve",
      "comments": "代码质量良好",
      "priority": 3
    },
    "output_error": None,
    "error": None,
    "started_at": 1234567890.0,
    "finished_at": 1234567891.0,
    "timeout_at": 1234571490.0  # 新字段：超时时间戳（0=无限等待）
  }
}
```

新增状态：
- `waiting_input`：节点等待用户输入（run 处于 `paused` 状态，原因 `human_input_required`）

### 2.3 运行暂停原因扩展

```python
# 现有暂停原因
PAUSE_REASONS = [
    "node_failed",          # 节点失败（failure_policy=pause）
    "node_interrupted",     # 节点被打断
    "node_output_invalid",  # 节点输出 JSON 格式错误（Phase 1）
    "max_rounds_reached",   # 自动编排达到轮次上限
]

# 新增
PAUSE_REASONS.append("human_input_required")  # 等待人工输入
```

## 3. 后端实现

### 3.1 DAGRunner 改动

**文件**：`astrbot/dashboard/services/agent_team_run_service.py`

#### 3.1.1 节点类型分发

```python
async def _execute_node(self, node_id: str, state: dict) -> None:
    """执行单个节点（现有方法扩展）。"""
    node = self._find_node(node_id)
    node_type = node.get("type", "member")
    
    if node_type == "human_input":
        await self._execute_human_input_node(node_id, state, node)
    elif node_type == "member":
        await self._execute_member_node(node_id, state, node)
    else:
        raise DAGExecutionError(f"Unknown node type: {node_type}")

async def _execute_member_node(self, node_id: str, state: dict, node: dict) -> None:
    """执行成员节点（现有逻辑提取）。"""
    # ... 现有 deliver + collect 逻辑 ...
    pass
```

#### 3.1.2 Human Input 节点执行

```python
async def _execute_human_input_node(self, node_id: str, state: dict, node: dict) -> None:
    """执行 Human Input 节点。
    
    流程：
    1. 节点状态置为 waiting_input
    2. run 暂停（原因 human_input_required）
    3. 发射 human_input 事件（前端渲染表单）
    4. 等待用户提交（通过 resume_run API + 提交数据）
    5. 校验输入
    6. 节点 done，继续后继
    """
    state["status"] = "waiting_input"
    state["started_at"] = time.time()
    
    # 配置超时
    timeout_config = node.get("timeout", {})
    timeout_seconds = timeout_config.get("seconds", 0)
    if timeout_seconds > 0:
        state["timeout_at"] = state["started_at"] + timeout_seconds
    else:
        state["timeout_at"] = 0  # 无限等待
    
    # 持久化
    await self._persist_run()
    
    # 发射事件（前端订阅并渲染）
    self._emit("human_input", {
        "node_id": node_id,
        "title": node.get("title", "人工输入"),
        "prompt": node.get("prompt", ""),
        "input_mode": node.get("input_mode", "text"),
        "text_config": node.get("text_config"),
        "form_config": node.get("form_config"),
        "timeout_at": state["timeout_at"],
    })
    
    # 暂停运行（等待用户提交）
    await self._pause_run("human_input_required", node_id)
    
    # 如果配置了超时，启动超时监控
    if state["timeout_at"] > 0:
        self._schedule_input_timeout(node_id, state["timeout_at"])

def _schedule_input_timeout(self, node_id: str, timeout_at: float) -> None:
    """调度超时检查（后台任务）。"""
    async def _check_timeout():
        await asyncio.sleep(max(timeout_at - time.time(), 0))
        
        # 超时时检查节点是否仍在等待
        if self.node_states[node_id]["status"] == "waiting_input":
            await self._handle_input_timeout(node_id)
    
    # 将超时任务添加到运行器的任务集合
    self._timeout_tasks[node_id] = asyncio.create_task(_check_timeout())

async def _handle_input_timeout(self, node_id: str) -> None:
    """处理输入超时。"""
    node = self._find_node(node_id)
    state = self.node_states[node_id]
    timeout_config = node.get("timeout", {})
    on_timeout = timeout_config.get("on_timeout", "fail")
    
    if on_timeout == "skip":
        state["status"] = "skipped"
        state["error"] = "User input timeout"
        self._emit("node_status", {
            "node_id": node_id,
            "status": "skipped",
            "error": state["error"]
        })
    else:  # fail
        state["status"] = "failed"
        state["error"] = "User input timeout"
        self._emit("node_status", {
            "node_id": node_id,
            "status": "failed",
            "error": state["error"]
        })
        # 走失败策略
        if self.config["failure_policy"] == "pause":
            return  # 已暂停
        # auto_skip 则继续
    
    state["finished_at"] = time.time()
    await self._persist_run()
    
    # 如果失败策略是 auto_skip，唤醒执行继续
    if on_timeout == "skip" or self.config["failure_policy"] == "auto_skip":
        self._unlock_successors(node_id)
```

#### 3.1.3 用户提交输入（resume 路径）

```python
# AgentTeamRunService 新增方法
async def submit_human_input(
    self,
    username: str,
    run_id: str,
    node_id: str,
    input_data: dict
) -> dict:
    """提交 Human Input 节点的用户输入。
    
    Args:
        username: 用户名（权限校验）
        run_id: 运行 ID
        node_id: Human Input 节点 ID
        input_data: 用户提交的数据
            - 文本模式: {"input": "用户输入的文本"}
            - 表单模式: {field1: value1, field2: value2, ...}
    
    Returns:
        {"message": "Input submitted", "node_id": ...}
    
    Raises:
        AgentTeamsServiceError: 权限错误、节点不存在、状态错误、校验失败
    """
    # 1. 权限校验
    run_row = await self._require_run(username, run_id)
    team = await self._get_team(run_row.team_id)
    
    # 2. 查找运行器
    runner = self._active_runners.get(run_id)
    if not runner:
        raise AgentTeamsServiceError(f"Run {run_id} is not active")
    
    # 3. 校验节点状态
    node = runner._find_node(node_id)
    if not node:
        raise AgentTeamsServiceError(f"Node {node_id} not found")
    if node.get("type") != "human_input":
        raise AgentTeamsServiceError(f"Node {node_id} is not a human_input node")
    
    state = runner.node_states.get(node_id)
    if not state or state["status"] != "waiting_input":
        raise AgentTeamsServiceError(
            f"Node {node_id} is not waiting for input (status: {state['status']})"
        )
    
    # 4. 校验输入数据
    validation_errors = self._validate_human_input(node, input_data)
    if validation_errors:
        raise AgentTeamsServiceError(
            "Input validation failed",
            field_errors=validation_errors
        )
    
    # 5. 填充节点状态
    state["status"] = "done"
    state["finished_at"] = time.time()
    state["structured_output"] = input_data
    
    # 文本模式：result = 用户输入文本
    # 表单模式：result = JSON 字符串（便于转录查看）
    if node.get("input_mode") == "text":
        state["result"] = input_data.get("input", "")
    else:
        state["result"] = json.dumps(input_data, ensure_ascii=False, indent=2)
    
    # 6. 取消超时任务
    timeout_task = runner._timeout_tasks.pop(node_id, None)
    if timeout_task:
        timeout_task.cancel()
    
    # 7. 发射事件
    runner._emit("node_status", {
        "node_id": node_id,
        "status": "done",
        "structured_output": input_data
    })
    
    # 8. 持久化
    await runner._persist_run()
    
    # 9. 恢复运行（解锁后继节点）
    if run_row.status == "paused":
        await self.resume_run(username, run_id)
    else:
        # 如果 run 未暂停（其他节点并行运行中），直接解锁后继
        runner._unlock_successors(node_id)
    
    return {"message": "Input submitted", "node_id": node_id}

def _validate_human_input(self, node: dict, input_data: dict) -> list[dict]:
    """校验用户输入。
    
    Returns:
        字段级错误列表，空列表=通过
    """
    errors = []
    input_mode = node.get("input_mode", "text")
    
    if input_mode == "text":
        # 文本模式：校验 input 字段
        if "input" not in input_data:
            errors.append({
                "path": "input",
                "code": "REQUIRED",
                "message": "Input field is required"
            })
        else:
            text_config = node.get("text_config", {})
            max_length = text_config.get("max_length")
            if max_length and len(input_data["input"]) > max_length:
                errors.append({
                    "path": "input",
                    "code": "MAX_LENGTH",
                    "message": f"Input exceeds max length {max_length}"
                })
    
    elif input_mode == "form":
        # 表单模式：逐字段校验
        form_config = node.get("form_config", {})
        fields = form_config.get("fields", [])
        
        for field in fields:
            field_name = field["name"]
            field_value = input_data.get(field_name)
            
            # 必填校验
            if field.get("required", False) and field_value is None:
                errors.append({
                    "path": field_name,
                    "code": "REQUIRED",
                    "message": f"Field '{field['label']}' is required"
                })
                continue
            
            if field_value is None:
                continue  # 非必填且未填
            
            # 类型校验
            field_type = field["type"]
            if field_type == "number":
                if not isinstance(field_value, (int, float)):
                    errors.append({
                        "path": field_name,
                        "code": "INVALID_TYPE",
                        "message": f"Field '{field['label']}' must be a number"
                    })
                else:
                    # 范围校验
                    if "min" in field and field_value < field["min"]:
                        errors.append({
                            "path": field_name,
                            "code": "MIN_VALUE",
                            "message": f"Value must be >= {field['min']}"
                        })
                    if "max" in field and field_value > field["max"]:
                        errors.append({
                            "path": field_name,
                            "code": "MAX_VALUE",
                            "message": f"Value must be <= {field['max']}"
                        })
            
            elif field_type in ("text", "textarea"):
                if not isinstance(field_value, str):
                    errors.append({
                        "path": field_name,
                        "code": "INVALID_TYPE",
                        "message": f"Field '{field['label']}' must be a string"
                    })
                else:
                    max_length = field.get("max_length")
                    if max_length and len(field_value) > max_length:
                        errors.append({
                            "path": field_name,
                            "code": "MAX_LENGTH",
                            "message": f"Exceeds max length {max_length}"
                        })
                    
                    pattern = field.get("pattern")
                    if pattern:
                        import re
                        if not re.match(pattern, field_value):
                            errors.append({
                                "path": field_name,
                                "code": "INVALID_FORMAT",
                                "message": f"Does not match required format"
                            })
            
            elif field_type in ("select", "radio"):
                valid_values = [opt["value"] for opt in field.get("options", [])]
                if field_value not in valid_values:
                    errors.append({
                        "path": field_name,
                        "code": "INVALID_OPTION",
                        "message": f"Invalid option value"
                    })
            
            elif field_type == "checkbox":
                if not isinstance(field_value, bool):
                    errors.append({
                        "path": field_name,
                        "code": "INVALID_TYPE",
                        "message": f"Field '{field['label']}' must be boolean"
                    })
    
    return errors
```

### 3.2 工作流校验增强

**文件**：`astrbot/dashboard/services/agent_team_dag.py`

```python
def validate_workflow_graph(nodes: list[dict], edges: list[dict], members: list[dict]) -> list[dict]:
    """工作流校验（现有方法扩展）。"""
    errors = []
    
    # 现有校验（DAG、节点数上限等）
    # ...
    
    # 新增：Human Input 节点校验
    for i, node in enumerate(nodes):
        if node.get("type") == "human_input":
            # 1. 不能绑定成员
            if "member_id" in node and node["member_id"]:
                errors.append({
                    "path": f"nodes[{i}].member_id",
                    "code": "INVALID",
                    "message": "Human input node cannot have a member_id"
                })
            
            # 2. 必须有 prompt
            if not node.get("prompt"):
                errors.append({
                    "path": f"nodes[{i}].prompt",
                    "code": "REQUIRED",
                    "message": "Human input node must have a prompt"
                })
            
            # 3. input_mode 合法性
            input_mode = node.get("input_mode", "text")
            if input_mode not in ("text", "form"):
                errors.append({
                    "path": f"nodes[{i}].input_mode",
                    "code": "INVALID",
                    "message": "input_mode must be 'text' or 'form'"
                })
            
            # 4. 表单模式必须有 fields
            if input_mode == "form":
                form_config = node.get("form_config", {})
                fields = form_config.get("fields", [])
                if not fields:
                    errors.append({
                        "path": f"nodes[{i}].form_config.fields",
                        "code": "REQUIRED",
                        "message": "Form mode must have at least one field"
                    })
                
                # 校验字段定义
                for j, field in enumerate(fields):
                    if not field.get("name"):
                        errors.append({
                            "path": f"nodes[{i}].form_config.fields[{j}].name",
                            "code": "REQUIRED",
                            "message": "Field must have a name"
                        })
                    if not field.get("label"):
                        errors.append({
                            "path": f"nodes[{i}].form_config.fields[{j}].label",
                            "code": "REQUIRED",
                            "message": "Field must have a label"
                        })
                    if field.get("type") not in ("text", "textarea", "number", "select", "radio", "checkbox"):
                        errors.append({
                            "path": f"nodes[{i}].form_config.fields[{j}].type",
                            "code": "INVALID",
                            "message": "Invalid field type"
                        })
                    
                    # select/radio 必须有 options
                    if field.get("type") in ("select", "radio"):
                        if not field.get("options"):
                            errors.append({
                                "path": f"nodes[{i}].form_config.fields[{j}].options",
                                "code": "REQUIRED",
                                "message": f"{field['type']} field must have options"
                            })
            
            # 5. 超时配置合法性
            timeout_config = node.get("timeout", {})
            if timeout_config:
                if "seconds" in timeout_config:
                    if not isinstance(timeout_config["seconds"], (int, float)) or timeout_config["seconds"] < 0:
                        errors.append({
                            "path": f"nodes[{i}].timeout.seconds",
                            "code": "INVALID",
                            "message": "Timeout seconds must be >= 0"
                        })
                if "on_timeout" in timeout_config:
                    if timeout_config["on_timeout"] not in ("fail", "skip"):
                        errors.append({
                            "path": f"nodes[{i}].timeout.on_timeout",
                            "code": "INVALID",
                            "message": "on_timeout must be 'fail' or 'skip'"
                        })
    
    return errors
```

## 4. API 设计

### 4.1 提交输入端点（新增）

```
POST /api/v1/agent_teams/runs/{run_id}/nodes/{node_id}/submit_input
```

**请求体**（文本模式）：
```json
{
  "input": "我批准部署，代码质量良好。"
}
```

**请求体**（表单模式）：
```json
{
  "approved": "approve",
  "comments": "代码质量良好，建议部署",
  "priority": 3
}
```

**响应**（成功）：
```json
{
  "status": "ok",
  "data": {
    "message": "Input submitted",
    "node_id": "approval"
  }
}
```

**响应**（校验失败）：
```json
{
  "status": "error",
  "message": "Input validation failed",
  "data": {
    "fields": [
      {
        "path": "approved",
        "code": "REQUIRED",
        "message": "Field '审批决定' is required"
      }
    ]
  }
}
```

### 4.2 SSE 事件扩展

**`human_input` 事件**（节点开始等待）：
```json
{
  "type": "human_input",
  "node_id": "approval",
  "title": "人工审批",
  "prompt": "请审查 AI 生成的代码并决定是否批准部署。",
  "input_mode": "form",
  "form_config": {
    "fields": [...]
  },
  "timeout_at": 1234571490.0
}
```

**`node_status` 事件**（用户提交后）：
```json
{
  "type": "node_status",
  "node_id": "approval",
  "status": "done",
  "structured_output": {
    "approved": "approve",
    "comments": "代码质量良好",
    "priority": 3
  }
}
```

**`paused` 事件**：
```json
{
  "type": "paused",
  "reason": "human_input_required",
  "node_id": "approval"
}
```

## 5. 前端实现

### 5.1 编辑器扩展

**文件**：`dashboard/src/components/agent_teams/WorkflowEditor.vue`

#### 5.1.1 节点类型选择器

左侧成员面板增加"特殊节点"分组：

```vue
<template>
  <div class="workflow-editor-sidebar">
    <!-- 现有：成员列表 -->
    <div class="members-section">
      <h3>团队成员</h3>
      <div
        v-for="member in members"
        :key="member.member_id"
        class="member-card"
        draggable="true"
        @dragstart="onDragMember(member)"
      >
        {{ member.name }}
      </div>
    </div>
    
    <!-- 新增：特殊节点 -->
    <div class="special-nodes-section">
      <h3>特殊节点</h3>
      <div
        class="special-node-card"
        draggable="true"
        @dragstart="onDragSpecialNode('human_input')"
      >
        <v-icon size="small">mdi-account-question</v-icon>
        人工输入
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
function onDragSpecialNode(nodeType: string) {
  // 拖拽数据：节点类型
  event.dataTransfer?.setData('nodeType', nodeType);
}

function onCanvasDrop(event: DragEvent) {
  const memberIdDragged = event.dataTransfer?.getData('memberId');
  const nodeTypeDragged = event.dataTransfer?.getData('nodeType');
  
  if (memberIdDragged) {
    // 现有：创建成员节点
    createMemberNode(memberIdDragged, position);
  } else if (nodeTypeDragged === 'human_input') {
    // 新增：创建 Human Input 节点
    createHumanInputNode(position);
  }
}

function createHumanInputNode(position: {x: number, y: number}) {
  const nodeId = `hi_${Date.now()}`;
  const newNode: HumanInputNode = {
    id: nodeId,
    type: 'human_input',
    title: '人工输入',
    prompt: '请输入...',
    input_mode: 'text',
    text_config: {
      placeholder: '在此输入',
      multiline: false
    }
  };
  
  workflowGraph.nodes.push(newNode);
  workflowLayout[nodeId] = position;
}
</script>
```

#### 5.1.2 Human Input 节点属性面板

选中 Human Input 节点时，右侧属性面板显示：

```vue
<template>
  <div v-if="selectedNode?.type === 'human_input'" class="node-inspector-human-input">
    <h3>人工输入节点</h3>
    
    <v-text-field
      v-model="selectedNode.title"
      label="节点标题"
      density="compact"
    />
    
    <v-textarea
      v-model="selectedNode.prompt"
      label="提示文本"
      placeholder="向用户说明需要输入什么..."
      rows="3"
    />
    
    <v-select
      v-model="selectedNode.input_mode"
      :items="[
        {value: 'text', title: '文本输入'},
        {value: 'form', title: '表单输入'}
      ]"
      label="输入模式"
      density="compact"
    />
    
    <!-- 文本模式配置 -->
    <div v-if="selectedNode.input_mode === 'text'" class="text-config">
      <v-text-field
        v-model="selectedNode.text_config.placeholder"
        label="占位符"
        density="compact"
      />
      <v-checkbox
        v-model="selectedNode.text_config.multiline"
        label="多行输入"
        density="compact"
      />
      <v-text-field
        v-model.number="selectedNode.text_config.max_length"
        label="最大长度"
        type="number"
        density="compact"
      />
    </div>
    
    <!-- 表单模式配置 -->
    <div v-if="selectedNode.input_mode === 'form'" class="form-config">
      <div class="form-fields-header">
        <h4>表单字段</h4>
        <v-btn
          size="small"
          variant="text"
          icon="mdi-plus"
          @click="addFormField"
        />
      </div>
      
      <div
        v-for="(field, index) in selectedNode.form_config.fields"
        :key="index"
        class="form-field-editor"
      >
        <v-expansion-panel>
          <v-expansion-panel-title>
            {{ field.label || `字段 ${index + 1}` }}
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <v-text-field
              v-model="field.name"
              label="字段名"
              density="compact"
              required
            />
            <v-text-field
              v-model="field.label"
              label="显示标签"
              density="compact"
              required
            />
            <v-select
              v-model="field.type"
              :items="[
                {value: 'text', title: '单行文本'},
                {value: 'textarea', title: '多行文本'},
                {value: 'number', title: '数字'},
                {value: 'select', title: '下拉选择'},
                {value: 'radio', title: '单选按钮'},
                {value: 'checkbox', title: '复选框'}
              ]"
              label="字段类型"
              density="compact"
            />
            <v-checkbox
              v-model="field.required"
              label="必填"
              density="compact"
            />
            
            <!-- select/radio 的选项编辑器 -->
            <div v-if="field.type === 'select' || field.type === 'radio'" class="field-options">
              <h5>选项列表</h5>
              <div v-for="(opt, optIdx) in field.options" :key="optIdx" class="option-row">
                <v-text-field
                  v-model="opt.value"
                  label="值"
                  density="compact"
                />
                <v-text-field
                  v-model="opt.label"
                  label="标签"
                  density="compact"
                />
                <v-btn
                  icon="mdi-delete"
                  size="small"
                  variant="text"
                  @click="field.options.splice(optIdx, 1)"
                />
              </div>
              <v-btn
                size="small"
                variant="text"
                @click="field.options.push({value: '', label: ''})"
              >
                添加选项
              </v-btn>
            </div>
            
            <v-btn
              color="error"
              size="small"
              variant="text"
              @click="removeFormField(index)"
            >
              删除字段
            </v-btn>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </div>
    </div>
    
    <!-- 超时配置 -->
    <v-expansion-panels class="mt-4">
      <v-expansion-panel>
        <v-expansion-panel-title>超时设置（可选）</v-expansion-panel-title>
        <v-expansion-panel-text>
          <v-text-field
            v-model.number="selectedNode.timeout.seconds"
            label="超时秒数（0=无限等待）"
            type="number"
            min="0"
            density="compact"
          />
          <v-select
            v-model="selectedNode.timeout.on_timeout"
            :items="[
              {value: 'fail', title: '失败'},
              {value: 'skip', title: '跳过'}
            ]"
            label="超时行为"
            density="compact"
          />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </div>
</template>
```

#### 5.1.3 节点卡片显示

画布上 Human Input 节点的自定义样式：

```vue
<template>
  <div class="human-input-node">
    <div class="node-header">
      <v-icon size="small" color="primary">mdi-account-question</v-icon>
      <span class="node-title">{{ node.title || '人工输入' }}</span>
    </div>
    <div class="node-body">
      <div class="node-prompt">{{ truncate(node.prompt, 50) }}</div>
      <div class="node-mode-badge">
        <v-chip size="x-small" variant="tonal">
          {{ node.input_mode === 'text' ? '文本' : '表单' }}
        </v-chip>
      </div>
    </div>
  </div>
</template>

<style scoped>
.human-input-node {
  border: 2px dashed #2196f3;
  background: #e3f2fd;
}
</style>
```

### 5.2 监控视图扩展

**文件**：`dashboard/src/components/agent_teams/RunMonitor.vue`

#### 5.2.1 Human Input 对话框

新增组件：`dashboard/src/components/agent_teams/HumanInputDialog.vue`

```vue
<template>
  <v-dialog
    v-model="isOpen"
    max-width="700px"
    persistent
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ humanInputEvent.title }}
      </v-card-title>
      
      <v-card-text class="pa-6">
        <v-alert
          type="info"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          {{ humanInputEvent.prompt }}
        </v-alert>
        
        <!-- 倒计时显示 -->
        <div v-if="remainingSeconds > 0" class="timeout-hint mb-4">
          <v-icon size="small" color="warning">mdi-clock-alert-outline</v-icon>
          剩余时间：{{ formatTime(remainingSeconds) }}
        </div>
        
        <!-- 文本模式 -->
        <v-textarea
          v-if="humanInputEvent.input_mode === 'text'"
          v-model="inputData.input"
          :placeholder="humanInputEvent.text_config?.placeholder"
          :rows="humanInputEvent.text_config?.multiline ? 5 : 1"
          :counter="humanInputEvent.text_config?.max_length"
          :error-messages="fieldErrors.input"
          autofocus
        />
        
        <!-- 表单模式 -->
        <div v-if="humanInputEvent.input_mode === 'form'" class="form-fields">
          <div
            v-for="field in humanInputEvent.form_config.fields"
            :key="field.name"
            class="form-field mb-4"
          >
            <!-- 文本 -->
            <v-text-field
              v-if="field.type === 'text'"
              v-model="inputData[field.name]"
              :label="field.label + (field.required ? ' *' : '')"
              :placeholder="field.placeholder"
              :counter="field.max_length"
              :error-messages="fieldErrors[field.name]"
              :hint="field.help_text"
              density="comfortable"
            />
            
            <!-- 多行文本 -->
            <v-textarea
              v-else-if="field.type === 'textarea'"
              v-model="inputData[field.name]"
              :label="field.label + (field.required ? ' *' : '')"
              :placeholder="field.placeholder"
              :counter="field.max_length"
              :error-messages="fieldErrors[field.name]"
              :hint="field.help_text"
              rows="3"
              density="comfortable"
            />
            
            <!-- 数字 -->
            <v-text-field
              v-else-if="field.type === 'number'"
              v-model.number="inputData[field.name]"
              type="number"
              :label="field.label + (field.required ? ' *' : '')"
              :min="field.min"
              :max="field.max"
              :step="field.step"
              :error-messages="fieldErrors[field.name]"
              :hint="field.help_text"
              density="comfortable"
            />
            
            <!-- 下拉选择 -->
            <v-select
              v-else-if="field.type === 'select'"
              v-model="inputData[field.name]"
              :items="field.options"
              item-title="label"
              item-value="value"
              :label="field.label + (field.required ? ' *' : '')"
              :error-messages="fieldErrors[field.name]"
              :hint="field.help_text"
              density="comfortable"
            />
            
            <!-- 单选按钮 -->
            <div v-else-if="field.type === 'radio'">
              <div class="field-label">
                {{ field.label }}{{ field.required ? ' *' : '' }}
              </div>
              <v-radio-group
                v-model="inputData[field.name]"
                :error-messages="fieldErrors[field.name]"
                :hint="field.help_text"
              >
                <v-radio
                  v-for="opt in field.options"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </v-radio-group>
            </div>
            
            <!-- 复选框 -->
            <v-checkbox
              v-else-if="field.type === 'checkbox'"
              v-model="inputData[field.name]"
              :label="field.label"
              :error-messages="fieldErrors[field.name]"
              :hint="field.help_text"
              density="comfortable"
            />
          </div>
        </div>
      </v-card-text>
      
      <v-card-actions class="pa-4">
        <v-spacer />
        <v-btn
          variant="text"
          @click="onCancel"
        >
          取消
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="submitting"
          @click="onSubmit"
        >
          提交
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue';
import { agentTeamsApi } from '@/api/v1';
import { extractApiError } from '@/utils/extractApiError';
import { useToast } from '@/utils/toast';

interface Props {
  humanInputEvent: any | null;
  runId: string;
}

const props = defineProps<Props>();
const emit = defineEmits(['close', 'submitted']);

const isOpen = computed(() => props.humanInputEvent !== null);
const inputData = ref<Record<string, any>>({});
const fieldErrors = ref<Record<string, string>>({});
const submitting = ref(false);
const remainingSeconds = ref(0);
let countdownInterval: number | null = null;

// 初始化默认值
watch(() => props.humanInputEvent, (event) => {
  if (!event) return;
  
  inputData.value = {};
  fieldErrors.value = {};
  
  if (event.input_mode === 'text') {
    inputData.value.input = '';
  } else if (event.input_mode === 'form') {
    for (const field of event.form_config.fields) {
      if (field.default_value !== undefined) {
        inputData.value[field.name] = field.default_value;
      }
    }
  }
  
  // 启动倒计时
  if (event.timeout_at > 0) {
    startCountdown(event.timeout_at);
  }
}, { immediate: true });

function startCountdown(timeoutAt: number) {
  const update = () => {
    remainingSeconds.value = Math.max(0, Math.floor(timeoutAt - Date.now() / 1000));
    if (remainingSeconds.value === 0) {
      stopCountdown();
      emit('close');  // 超时自动关闭
    }
  };
  
  update();
  countdownInterval = window.setInterval(update, 1000);
}

function stopCountdown() {
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}时${m}分${s}秒`;
  if (m > 0) return `${m}分${s}秒`;
  return `${s}秒`;
}

async function onSubmit() {
  submitting.value = true;
  fieldErrors.value = {};
  
  const { error, success } = useToast();
  
  try {
    await agentTeamsApi.submitHumanInput(
      props.runId,
      props.humanInputEvent.node_id,
      inputData.value
    );
    
    success('输入已提交');
    stopCountdown();
    emit('submitted');
    emit('close');
  } catch (err) {
    const extracted = extractApiError(err, '提交失败');
    error(extracted.message);
    
    // 映射字段级错误
    if (extracted.fields.length > 0) {
      for (const field of extracted.fields) {
        fieldErrors.value[field.path] = field.message;
      }
    }
  } finally {
    submitting.value = false;
  }
}

function onCancel() {
  stopCountdown();
  emit('close');
}

onUnmounted(() => {
  stopCountdown();
});
</script>

<style scoped>
.timeout-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #f57c00;
}

.field-label {
  font-size: 14px;
  margin-bottom: 8px;
  color: rgba(0, 0, 0, 0.6);
}
</style>
```

#### 5.2.2 监控页集成

```vue
<template>
  <div class="run-monitor">
    <!-- 现有：工具栏、网格、DAG 视图 -->
    
    <!-- 新增：Human Input 对话框 -->
    <HumanInputDialog
      :human-input-event="pendingHumanInput"
      :run-id="runState?.run_id"
      @close="pendingHumanInput = null"
      @submitted="onHumanInputSubmitted"
    />
  </div>
</template>

<script setup lang="ts">
import HumanInputDialog from './HumanInputDialog.vue';

const pendingHumanInput = ref<any>(null);

// SSE 事件处理（现有 reducer 扩展）
function handleSseEvent(event: any) {
  if (event.type === 'human_input') {
    // 弹出输入对话框
    pendingHumanInput.value = event;
  }
  
  // ... 其他事件处理 ...
}

function onHumanInputSubmitted() {
  // 提交后自动恢复运行（后端已处理）
  // 前端只需等待后续 node_status/resumed 事件
}
</script>
```

### 5.3 DAG 进度视图

Human Input 节点在 DAG 视图中的显示：

```vue
<MemberFlowNode
  v-if="node.type === 'human_input'"
  :node="node"
  :state="nodeStates[node.id]"
  class="human-input-flow-node"
>
  <template #icon>
    <v-icon color="primary">mdi-account-question</v-icon>
  </template>
  
  <template #status-badge>
    <v-chip
      v-if="nodeStates[node.id]?.status === 'waiting_input'"
      size="small"
      color="warning"
      variant="tonal"
    >
      等待输入
    </v-chip>
  </template>
</MemberFlowNode>
```

## 6. 与现有功能的协同

### 6.1 追加指令 & 打断

Human Input 节点**不影响**现有的成员节点追加指令和打断能力：

- **场景**：工作流中有 Human Input 节点在等待，同时成员节点正在执行
- **用户操作**：可以点击成员节点的对话框，追加指令或打断该成员
- **互不冲突**：Human Input 对话框和成员对话框是独立的，可以同时打开

### 6.2 条件分支集成

Human Input 节点的输出可用于条件分支（Phase 1）：

```json
{
  "nodes": [
    {
      "id": "approval",
      "type": "human_input",
      "prompt": "请审批",
      "input_mode": "form",
      "form_config": {
        "fields": [
          {"name": "decision", "type": "select", "options": [
            {"value": "approve", "label": "批准"},
            {"value": "reject", "label": "拒绝"}
          ]}
        ]
      }
    },
    {"id": "deploy", "member_id": "deployer", "task": "部署"},
    {"id": "rollback", "member_id": "developer", "task": "回滚"}
  ],
  "edges": [
    {"from": "approval", "to": "deploy", "condition": "{{approval.output.decision}} == 'approve'"},
    {"from": "approval", "to": "rollback", "condition": "{{approval.output.decision}} == 'reject'"}
  ]
}
```

### 6.3 失败策略

Human Input 节点超时失败时，遵循团队的 `failure_policy`：
- `pause`：run 暂停，用户可 retry（重新弹输入框）或 skip
- `auto_skip`：节点 skipped，后继节点按条件边规则决定是否执行

## 7. 测试策略

### 7.1 后端 pytest

**文件**：`tests/agent_teams/test_human_input.py`

```python
@pytest.mark.asyncio
async def test_human_input_text_mode(build_test_team):
    """测试文本模式 Human Input 节点。"""
    team, ports = await build_test_team()
    
    workflow = {
        "nodes": [
            {
                "id": "input1",
                "type": "human_input",
                "prompt": "请输入你的名字",
                "input_mode": "text",
                "text_config": {"placeholder": "姓名"}
            },
            {
                "id": "greet",
                "member_id": "m1",
                "task": "向 {{input1.output.input}} 打招呼"
            }
        ],
        "edges": [{"from": "input1", "to": "greet"}]
    }
    
    runner = DAGRunner(team, workflow, "test", ports)
    run_task = asyncio.create_task(runner.run())
    
    # 等待 human_input 事件
    await asyncio.sleep(0.1)
    assert runner.node_states["input1"]["status"] == "waiting_input"
    assert runner.status == "paused"
    
    # 提交输入
    service = AgentTeamRunService(db, ports)
    await service.submit_human_input("owner", runner.run_id, "input1", {"input": "Alice"})
    
    # 等待完成
    await run_task
    
    assert runner.node_states["input1"]["status"] == "done"
    assert runner.node_states["input1"]["structured_output"] == {"input": "Alice"}
    assert "Alice" in runner.node_states["greet"]["task_rendered"]

@pytest.mark.asyncio
async def test_human_input_form_validation():
    """测试表单模式校验。"""
    node = {
        "type": "human_input",
        "input_mode": "form",
        "form_config": {
            "fields": [
                {"name": "age", "type": "number", "required": True, "min": 0, "max": 120},
                {"name": "email", "type": "text", "pattern": r"^[^@]+@[^@]+\.[^@]+$"}
            ]
        }
    }
    
    service = AgentTeamRunService(db, None)
    
    # 必填字段缺失
    errors = service._validate_human_input(node, {})
    assert any(e["path"] == "age" and e["code"] == "REQUIRED" for e in errors)
    
    # 范围校验
    errors = service._validate_human_input(node, {"age": 150})
    assert any(e["path"] == "age" and e["code"] == "MAX_VALUE" for e in errors)
    
    # 正则校验
    errors = service._validate_human_input(node, {"age": 25, "email": "invalid"})
    assert any(e["path"] == "email" and e["code"] == "INVALID_FORMAT" for e in errors)
    
    # 通过
    errors = service._validate_human_input(node, {"age": 25, "email": "user@example.com"})
    assert len(errors) == 0

@pytest.mark.asyncio
async def test_human_input_timeout():
    """测试输入超时。"""
    workflow = {
        "nodes": [
            {
                "id": "input1",
                "type": "human_input",
                "prompt": "请输入",
                "input_mode": "text",
                "timeout": {"seconds": 1, "on_timeout": "skip"}
            },
            {"id": "next", "member_id": "m1", "task": "继续"}
        ],
        "edges": [{"from": "input1", "to": "next"}]
    }
    
    runner = DAGRunner(team, workflow, "test", ports)
    await runner.run()
    
    # 超时后节点 skipped
    assert runner.node_states["input1"]["status"] == "skipped"
    assert runner.node_states["input1"]["error"] == "User input timeout"
    assert runner.node_states["next"]["status"] == "skipped"  # 无条件边 → 跳过
```

### 7.2 前端 vitest

**文件**：`dashboard/src/components/agent_teams/HumanInputDialog.spec.ts`

```typescript
it('renders text mode input', () => {
  const event = {
    node_id: 'input1',
    title: '输入姓名',
    prompt: '请输入你的名字',
    input_mode: 'text',
    text_config: {placeholder: '姓名', multiline: false}
  };
  
  const wrapper = mount(HumanInputDialog, {
    props: {humanInputEvent: event, runId: 'run1'}
  });
  
  expect(wrapper.find('textarea').exists()).toBe(false);
  expect(wrapper.find('input[type="text"]').exists()).toBe(true);
});

it('renders form mode fields', () => {
  const event = {
    node_id: 'approval',
    input_mode: 'form',
    form_config: {
      fields: [
        {name: 'approved', type: 'radio', label: '审批', options: [...]},
        {name: 'comments', type: 'textarea', label: '意见'}
      ]
    }
  };
  
  const wrapper = mount(HumanInputDialog, {
    props: {humanInputEvent: event, runId: 'run1'}
  });
  
  expect(wrapper.findAll('.v-radio').length).toBeGreaterThan(0);
  expect(wrapper.find('textarea').exists()).toBe(true);
});

it('submits data and emits event', async () => {
  // ... mock API ...
  await wrapper.find('button[type="submit"]').trigger('click');
  
  expect(agentTeamsApi.submitHumanInput).toHaveBeenCalledWith(
    'run1',
    'input1',
    {input: 'test'}
  );
  expect(wrapper.emitted('submitted')).toBeTruthy();
});

it('shows countdown timer', async () => {
  const event = {
    node_id: 'input1',
    input_mode: 'text',
    timeout_at: Date.now() / 1000 + 60
  };
  
  const wrapper = mount(HumanInputDialog, {
    props: {humanInputEvent: event, runId: 'run1'}
  });
  
  await nextTick();
  expect(wrapper.text()).toContain('剩余时间');
  expect(wrapper.text()).toMatch(/\d+秒/);
});
```

### 7.3 手工验收

1. **文本输入流**：拖拽 Human Input 节点 → 配置提示文本 → 运行 → 弹框输入 → 提交 → 后继节点引用输入
2. **表单多字段**：配置包含 select/radio/number 的表单 → 运行 → 填写 → 校验错误提示 → 修改 → 提交成功
3. **超时跳过**：配置 10 秒超时 + skip → 运行 → 不输入 → 10 秒后节点自动 skipped
4. **条件分支集成**：Human Input 表单输出 → 后继条件边根据输出选择路径
5. **并行场景**：Human Input 等待中 + 其他成员节点运行 → 追加指令到成员节点 → Human Input 对话框仍可用

## 8. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| Human Input 绑定成员会话 | 混淆语义（成员 = Agent，Human Input = 用户）；成员会话历史会包含人工输入消息（污染） |
| 直接复用 `ask_user_choice` 工具 | `ask_user_choice` 是**Agent 运行时调用的工具**（随机出现），Human Input 是**工作流设计时的显式节点**（计划内等待）；语义与生命周期完全不同 |
| 输入提交后不自动 resume | 增加用户操作步骤；自动 resume 更符合直觉（输入就是为了让流程继续） |
| 支持文件上传字段类型 | v1 范围过大；文件处理需额外存储与安全机制；留待 v2 |
| Human Input 节点可配置多个表单页 | 过度复杂；多步骤输入可通过多个串联 Human Input 节点实现 |

## 9. 未来扩展方向

1. **字段类型扩展**：
   - `file`：文件上传（需对接附件存储）
   - `date` / `datetime`：日期时间选择器
   - `slider`：滑块（范围选择）
   - `color`：颜色选择器
   
2. **动态表单**：
   - 字段可见性条件（`visible_when: "{{field1}} == 'value'"`）
   - 选项动态加载（调用 API 获取选项列表）
   
3. **审批流增强**：
   - 多人审批（需 N 人批准才继续）
   - 审批历史记录（谁在什么时间批准/拒绝）
   
4. **输入预填充**：
   - 从前驱节点输出预填表单字段（`default_value: "{{prev.output.suggestion}}"`）
   
5. **富文本编辑器**：
   - textarea 升级为 Markdown/WYSIWYG 编辑器

## 10. 迁移与兼容性

- **现有工作流零改动**：只包含 `member` 节点的工作流行为不变
- **节点类型识别**：缺省 `type` 字段的节点默认为 `member` 节点（向后兼容）
- **API 扩展**：新增 `/submit_input` 端点，现有端点不受影响
- **前端渐进增强**：编辑器识别 `type="human_input"` 节点，不识别的客户端将其视为普通节点（降级显示）

---

## 附录 A：完整示例工作流

```json
{
  "name": "智能文章发布流程",
  "graph": {
    "nodes": [
      {
        "id": "generate",
        "type": "member",
        "member_id": "writer",
        "title": "AI 生成文章",
        "task": "根据主题 '{{input}}' 撰写一篇博客文章"
      },
      {
        "id": "review",
        "type": "human_input",
        "title": "人工审校",
        "prompt": "请审阅 AI 生成的文章，决定是否发布或需要修改。",
        "input_mode": "form",
        "form_config": {
          "fields": [
            {
              "name": "decision",
              "label": "审阅决定",
              "type": "radio",
              "required": true,
              "options": [
                {"value": "publish", "label": "直接发布"},
                {"value": "revise", "label": "需要修改"},
                {"value": "reject", "label": "拒绝发布"}
              ]
            },
            {
              "name": "feedback",
              "label": "修改意见",
              "type": "textarea",
              "placeholder": "如需修改，请说明具体建议...",
              "max_length": 1000
            },
            {
              "name": "priority",
              "label": "发布优先级",
              "type": "select",
              "options": [
                {"value": "low", "label": "低"},
                {"value": "normal", "label": "正常"},
                {"value": "high", "label": "高"}
              ],
              "default_value": "normal"
            }
          ]
        },
        "timeout": {
          "seconds": 7200,
          "on_timeout": "fail"
        }
      },
      {
        "id": "revise",
        "type": "member",
        "member_id": "writer",
        "title": "修改文章",
        "task": "根据反馈意见修改文章：{{review.output.feedback}}"
      },
      {
        "id": "publish",
        "type": "member",
        "member_id": "publisher",
        "title": "发布文章",
        "task": "将文章发布到博客平台（优先级：{{review.output.priority}}）"
      },
      {
        "id": "archive",
        "type": "member",
        "member_id": "archivist",
        "title": "归档",
        "task": "归档被拒绝的文章草稿"
      }
    ],
    "edges": [
      {"from": "generate", "to": "review"},
      {
        "from": "review",
        "to": "publish",
        "condition": "{{review.output.decision}} == 'publish'",
        "label": "直接发布"
      },
      {
        "from": "review",
        "to": "revise",
        "condition": "{{review.output.decision}} == 'revise'",
        "label": "修改"
      },
      {
        "from": "review",
        "to": "archive",
        "condition": "{{review.output.decision}} == 'reject'",
        "label": "拒绝"
      },
      {"from": "revise", "to": "review", "label": "重新审阅"}
    ]
  }
}
```

**执行流程示例**：
1. AI 生成文章 → 人工审校节点暂停
2. 用户填表：decision=revise, feedback="标题需要改进", priority=high
3. 条件边 `review → revise` 满足，AI 修改文章
4. 修改完成后再次回到审校节点（循环边）
5. 用户填表：decision=publish
6. 条件边 `review → publish` 满足，发布文章
7. 运行完成
