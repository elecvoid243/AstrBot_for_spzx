# Agent Teams 条件分支设计方案

- 作者：elecvoid243
- 日期：2026-09-07
- 前作：[2026-09-05-agent-teams-refinements-design.md](./2026-09-05-agent-teams-refinements-design.md)（已落地）
- 状态：设计评审
- 适用基线：Agent Teams 主体功能 + Refinements Plan 1-3 已落地

## 1. 背景与目标

Agent Teams 当前 DAG 编排是**静态无环图**，所有节点按拓扑顺序严格执行。实际应用中需要根据节点执行结果动态选择后续路径，例如：

- 代码审查节点 → 通过分支（部署）/ 拒绝分支（返工）
- 数据验证节点 → 合格分支（继续）/ 异常分支（告警）
- 风险评估节点 → 高风险分支（人工介入）/ 低风险分支（自动处理）

本设计引入**条件分支**能力：节点可输出结构化数据，出边根据条件表达式动态决定是否执行。这是后续循环边（Phase 3）和子工作流（Phase 4）的基础。

### 1.1 核心设计决策

| 决策点 | 结论 |
|---|---|
| 输出格式 | Markdown 标记块 ` ```output ... ``` `，内含 JSON（对 LLM 友好） |
| 条件表达式 | 支持逻辑运算（`&&`/`||`/`!`）+ 比较（`==`/`!=`/`>`等）+ 访问路径（`{{node.output.field}}`） |
| 存储方式 | `node_states[id].structured_output` 独立字段存解析后的 JSON（不混入 `result` 文本） |
| 混合模式 | 同一节点的出边可混合：有 `condition` 的边求值检查；无 `condition` 的边总是执行 |
| 错误处理 | 条件表达式语法错误 → run `failed`（编辑器校验 + 运行时终审）；节点输出 JSON 格式错误 → run `paused`（用户可修复） |
| 兼容性 | 现有工作流（边无 `condition` 字段）行为不变；新字段缺省 = 无条件边 |

## 2. 数据模型变更

### 2.1 边（Edge）扩展

```typescript
// 现有
interface WorkflowEdge {
  from: string;  // 源节点 id
  to: string;    // 目标节点 id
}

// 扩展后
interface WorkflowEdge {
  from: string;
  to: string;
  condition?: string;  // 条件表达式，缺省 = 无条件（总是执行）
  label?: string;      // 边标签（UI 显示，如 "通过" / "拒绝"）
}
```

**示例**：
```json
{
  "edges": [
    {
      "from": "review",
      "to": "deploy",
      "condition": "{{review.output.approved}} == true",
      "label": "通过"
    },
    {
      "from": "review",
      "to": "rework",
      "condition": "{{review.output.approved}} == false",
      "label": "拒绝"
    },
    {
      "from": "review",
      "to": "notify",
      "label": "通知"
    }
  ]
}
```

上例中：
- `review → deploy` 和 `review → rework` 为条件边，根据 `approved` 字段决定
- `review → notify` 为无条件边，总是执行（如发送通知）

### 2.2 节点状态（node_states）扩展

```python
# 现有
node_states = {
  "node_id": {
    "status": "done",
    "member_id": "m1",
    "task_rendered": "审查代码...",
    "result": "代码质量良好，批准合并。\n\n```output\n{\"approved\": true, \"score\": 95}\n```",
    "error": None,
    "started_at": 1234567890.0,
    "finished_at": 1234567891.0
  }
}

# 扩展后
node_states = {
  "node_id": {
    "status": "done",
    "member_id": "m1",
    "task_rendered": "审查代码...",
    "result": "代码质量良好，批准合并。\n\n```output\n{\"approved\": true, \"score\": 95}\n```",
    "structured_output": {"approved": True, "score": 95},  # 新增：解析后的 JSON
    "output_error": None,  # 新增：输出解析错误原因（JSON 格式错误时填充）
    "error": None,
    "started_at": 1234567890.0,
    "finished_at": 1234567891.0
  }
}
```

- `structured_output`：Markdown 块中 JSON 的解析结果（dict/list/None）
- `output_error`：解析失败时的错误原因（如 "JSON decode error: Expecting ',' delimiter"）

### 2.3 运行表（AgentTeamRun）无改动

`node_states` 字段已是 JSON 类型，扩展键无需迁移。现有 runs 的 `node_states` 不含新键 → 默认值语义（`structured_output=None`）。

## 3. 输出标记块协议

### 3.1 成员 Prompt 引导

节点任务模板中需明确指示成员输出结构化数据：

```markdown
你的任务是审查以下代码并决定是否批准合并。

代码：
{{input}}

请在回复末尾用以下格式输出决策：

```output
{
  "approved": true,  // true=批准，false=拒绝
  "score": 95,       // 质量评分 0-100
  "issues": []       // 发现的问题列表（可选）
}
```
```

### 3.2 解析规则

**正则提取**：
```python
import re
import json

OUTPUT_BLOCK_RE = re.compile(
    r'```output\s*\n(.*?)\n```',
    re.DOTALL | re.IGNORECASE
)

def extract_structured_output(result_text: str) -> tuple[dict | list | None, str | None]:
    """从成员回复中提取结构化输出。
    
    Returns:
        (parsed_json, error_message)
        - parsed_json: 解析成功的 dict/list，或 None（无标记块或解析失败）
        - error_message: 解析错误原因，或 None（成功或无块）
    """
    match = OUTPUT_BLOCK_RE.search(result_text)
    if not match:
        return None, None  # 无标记块 → 正常（节点可能不需要输出）
    
    json_str = match.group(1).strip()
    try:
        parsed = json.loads(json_str)
        if not isinstance(parsed, (dict, list)):
            return None, "Output must be JSON object or array"
        return parsed, None
    except json.JSONDecodeError as e:
        return None, f"JSON decode error: {e.msg} at position {e.pos}"
```

**行为**：
- 无标记块 → `structured_output = None`（合法，节点无需输出）
- 有标记块但 JSON 非法 → `output_error` 填充，run `paused`
- 有标记块且合法 → `structured_output` 填充

### 3.3 多个标记块的处理

如果成员回复中有多个 ` ```output ... ``` ` 块 → **取最后一个**（LLM 可能先尝试再修正）。

## 4. 条件表达式引擎

### 4.1 语法规范

**变量访问**：`{{<node_id>.output.<field>}}`
- `<node_id>`：节点 ID（必须是当前节点的前驱）
- `<field>`：JSON 路径，支持点访问（`a.b.c`）和数组索引（`list[0]`）

**运算符**（优先级从高到低）：
| 运算符 | 说明 | 示例 |
|---|---|---|
| `!` | 逻辑非 | `!{{n1.output.flag}}` |
| `>` `<` `>=` `<=` | 比较 | `{{n1.output.score}} > 80` |
| `==` `!=` | 相等 | `{{n1.output.status}} == "success"` |
| `&&` | 逻辑与 | `{{a.output.x}} > 0 && {{b.output.y}} < 10` |
| `||` | 逻辑或 | `{{a.output.ok}} || {{b.output.ok}}` |

**字面量**：
- 布尔：`true` / `false`
- 数字：`123` / `45.67` / `-10`
- 字符串：`"hello"` / `'world'`（双引号或单引号）
- null：`null`

**括号**：`( ... )` 改变优先级

**示例**：
```python
# 简单比较
"{{review.output.score}} >= 80"

# 逻辑与
"{{validate.output.passed}} == true && {{review.output.approved}} == true"

# 复杂表达式
"({{risk.output.level}} == \"high\" || {{risk.output.score}} > 90) && !{{override.output.skip}}"

# 访问嵌套字段
"{{data.output.metadata.status}} == \"ready\""

# 数组索引
"{{results.output.items[0].success}} == true"
```

### 4.2 实现：轻量求值器

**不使用 `eval()`**（安全风险）。实现专用解析器（LL 递归下降或 Lark 语法库）。

**选项 A：手写递归下降**（~200 行，零依赖）
```python
class ConditionEvaluator:
    """条件表达式求值器（递归下降解析）。"""
    
    def evaluate(self, expr: str, context: dict) -> tuple[bool, str | None]:
        """求值条件表达式。
        
        Args:
            expr: 条件表达式字符串
            context: 上下文 {node_id: {output: {...}}}
        
        Returns:
            (result: bool, error: str | None)
            - result: 求值结果（错误时为 False）
            - error: 语法/运行时错误原因，或 None（成功）
        """
        try:
            tokens = self._tokenize(expr)
            result = self._parse_or(tokens, context)
            return bool(result), None
        except ConditionSyntaxError as e:
            return False, f"Syntax error: {e}"
        except ConditionRuntimeError as e:
            return False, f"Runtime error: {e}"
    
    def _tokenize(self, expr: str) -> list[Token]: ...
    def _parse_or(self, tokens, context): ...   # || 层
    def _parse_and(self, tokens, context): ...  # && 层
    def _parse_compare(self, tokens, context): ...  # == != > < >= <= 层
    def _parse_unary(self, tokens, context): ...    # ! 层
    def _parse_primary(self, tokens, context): ...  # 变量/字面量/括号
    def _resolve_variable(self, var_path: str, context: dict): ...  # {{...}} 展开
```

**选项 B：Lark 语法库**（~50 行，外部依赖 `pip install lark`）
```python
from lark import Lark, Transformer, v_args

CONDITION_GRAMMAR = r"""
    ?expr: or_expr
    ?or_expr: and_expr ("||" and_expr)*
    ?and_expr: compare_expr ("&&" compare_expr)*
    ?compare_expr: unary_expr (COMP_OP unary_expr)?
    ?unary_expr: "!" unary_expr | primary
    ?primary: variable | literal | "(" expr ")"
    
    variable: "{{" PATH "}}"
    literal: "true" | "false" | "null" | NUMBER | STRING
    
    COMP_OP: "==" | "!=" | ">=" | "<=" | ">" | "<"
    PATH: /[a-zA-Z_][a-zA-Z0-9_.]*/
    NUMBER: /-?\d+(\.\d+)?/
    STRING: /"[^"]*"/ | /'[^']*'/
    
    %import common.WS
    %ignore WS
"""

class ConditionTransformer(Transformer):
    def __init__(self, context: dict):
        self.context = context
    
    @v_args(inline=True)
    def variable(self, path): ...  # 展开 {{node.output.field}}
    
    @v_args(inline=True)
    def or_expr(self, *args): ...  # 逻辑或
    
    # ... 其他规则
```

**推荐**：先用选项 A（零依赖，完全控制），后续如需更复杂语法再考虑 Lark。

### 4.3 变量解析

```python
def _resolve_variable(self, var_path: str, context: dict) -> Any:
    """解析变量路径 {{node_id.output.field.subfield[0]}}。
    
    Raises:
        ConditionRuntimeError: 节点不存在、output 为空、字段不存在等
    """
    # 1. 提取节点 ID（第一个点之前）
    parts = var_path.split('.', 1)
    if len(parts) < 2 or parts[1] != 'output':
        raise ConditionRuntimeError(f"Variable must be {{<node>.output.*}}, got {var_path}")
    
    node_id = parts[0]
    if node_id not in context:
        raise ConditionRuntimeError(f"Node '{node_id}' not found in context")
    
    output = context[node_id].get('output')
    if output is None:
        raise ConditionRuntimeError(f"Node '{node_id}' has no structured output")
    
    # 2. 解析剩余路径（支持点访问和数组索引）
    field_path = var_path[len(node_id) + len('.output.'):]
    return self._access_path(output, field_path)

def _access_path(self, obj: Any, path: str) -> Any:
    """访问嵌套字段，如 'user.name' 或 'items[0].id'。"""
    import re
    
    if not path:
        return obj
    
    # 分割路径（支持 'a.b[0].c'）
    tokens = re.findall(r'[^.\[\]]+|\[\d+\]', path)
    current = obj
    
    for token in tokens:
        if token.startswith('[') and token.endswith(']'):
            # 数组索引
            idx = int(token[1:-1])
            if not isinstance(current, list) or idx >= len(current):
                raise ConditionRuntimeError(f"Invalid array access: {token}")
            current = current[idx]
        else:
            # 字段访问
            if not isinstance(current, dict) or token not in current:
                raise ConditionRuntimeError(f"Field '{token}' not found")
            current = current[token]
    
    return current
```

## 5. 后端实现

### 5.1 DAGRunner 改动

**文件**：`astrbot/dashboard/services/agent_team_run_service.py`

#### 5.1.1 输出解析（节点完成时）

```python
async def _execute_node(self, node_id: str, state: dict) -> None:
    """执行单个节点（现有方法扩展）。"""
    # ... 现有 deliver + collect 逻辑 ...
    
    # 收集成功后解析输出
    if state["status"] == "done":
        result_text = state["result"]
        structured_output, output_error = extract_structured_output(result_text)
        state["structured_output"] = structured_output
        state["output_error"] = output_error
        
        if output_error:
            # JSON 解析失败 → paused（用户可修复）
            state["status"] = "failed"  # 先标记失败
            state["error"] = f"输出格式错误: {output_error}"
            self._emit("node_status", {
                "node_id": node_id,
                "status": "failed",
                "error": state["error"],
                "output_error": output_error  # 前端可高亮标记块
            })
            await self._pause_run("node_output_invalid", node_id)
            return  # 不继续后继节点
    
    # 持久化（现有逻辑已按转换落盘）
    await self._persist_run()
    
    # ... 现有后继解锁逻辑 ...
```

#### 5.1.2 后继节点解锁（条件边过滤）

```python
def _get_ready_nodes(self) -> list[str]:
    """获取就绪节点（现有方法扩展）。"""
    ready = []
    
    for node_id, state in self.node_states.items():
        if state["status"] != "pending":
            continue
        
        # 检查所有入边的源节点
        predecessors = self._get_predecessors(node_id)
        if not all(self.node_states[pred]["status"] in ("done", "skipped") 
                   for pred in predecessors):
            continue  # 前驱未完成
        
        # **新增**：检查入边条件
        if not self._check_incoming_conditions(node_id):
            # 所有入边条件都不满足 → 跳过该节点
            state["status"] = "skipped"
            state["error"] = "No incoming edge condition satisfied"
            self._emit("node_status", {"node_id": node_id, "status": "skipped"})
            continue
        
        ready.append(node_id)
    
    return ready

def _check_incoming_conditions(self, node_id: str) -> bool:
    """检查是否至少有一条入边的条件满足。
    
    Returns:
        True: 至少一条边满足（或有无条件边）
        False: 所有条件边都不满足且无无条件边
    """
    incoming_edges = [e for e in self.graph["edges"] if e["to"] == node_id]
    
    if not incoming_edges:
        return True  # 入口节点（无前驱）
    
    # 分离条件边和无条件边
    conditional_edges = [e for e in incoming_edges if "condition" in e and e["condition"]]
    unconditional_edges = [e for e in incoming_edges if "condition" not in e or not e["condition"]]
    
    # 有无条件边 → 总是执行
    if unconditional_edges:
        return True
    
    # 全是条件边 → 至少一条满足
    if not conditional_edges:
        return True  # 理论上不可能（incoming_edges 非空）
    
    context = self._build_condition_context()
    evaluator = ConditionEvaluator()
    
    for edge in conditional_edges:
        result, error = evaluator.evaluate(edge["condition"], context)
        if error:
            # 条件表达式语法错误 → 运行失败（终审）
            self._fail_run(f"Edge condition syntax error ({edge['from']}→{edge['to']}): {error}")
            raise DAGExecutionError(error)  # 中断执行
        if result:
            return True  # 至少一条满足
    
    return False  # 所有条件边都不满足

def _build_condition_context(self) -> dict:
    """构建条件求值上下文。"""
    return {
        node_id: {"output": state.get("structured_output")}
        for node_id, state in self.node_states.items()
    }
```

### 5.2 条件求值器实现

**新文件**：`astrbot/dashboard/services/agent_team_condition.py`

```python
"""Agent Teams 条件表达式求值器（手写递归下降解析）。"""

import re
from typing import Any


class ConditionSyntaxError(ValueError):
    """条件表达式语法错误。"""
    pass


class ConditionRuntimeError(ValueError):
    """条件表达式运行时错误（如变量不存在）。"""
    pass


class Token:
    """词法单元。"""
    def __init__(self, type: str, value: Any, pos: int):
        self.type = type    # 类型：VAR/NUM/STR/BOOL/NULL/OP/LPAREN/RPAREN/EOF
        self.value = value  # 值
        self.pos = pos      # 位置


class ConditionEvaluator:
    """条件表达式求值器。
    
    语法（EBNF）：
        expr       → or_expr
        or_expr    → and_expr ( "||" and_expr )*
        and_expr   → compare ( "&&" compare )*
        compare    → unary ( COMP_OP unary )?
        unary      → "!" unary | primary
        primary    → variable | literal | "(" expr ")"
        variable   → "{{" PATH "}}"
        literal    → BOOL | NULL | NUMBER | STRING
        COMP_OP    → "==" | "!=" | ">=" | "<=" | ">" | "<"
    """
    
    # 词法正则
    TOKEN_PATTERNS = [
        ('VAR',    r'\{\{[a-zA-Z_][a-zA-Z0-9_.\[\]]*\}\}'),
        ('NUM',    r'-?\d+(\.\d+)?'),
        ('STR',    r'"[^"]*"|\'[^\']*\''),
        ('BOOL',   r'\b(true|false)\b'),
        ('NULL',   r'\bnull\b'),
        ('OP',     r'==|!=|>=|<=|>|<|&&|\|\||!'),
        ('LPAREN', r'\('),
        ('RPAREN', r'\)'),
        ('WS',     r'\s+'),
    ]
    TOKEN_RE = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in TOKEN_PATTERNS))
    
    def __init__(self):
        self.tokens: list[Token] = []
        self.pos = 0
    
    def evaluate(self, expr: str, context: dict) -> tuple[bool, str | None]:
        """求值条件表达式。
        
        Args:
            expr: 条件表达式字符串
            context: {node_id: {"output": {...}}}
        
        Returns:
            (result: bool, error: str | None)
        """
        try:
            self.tokens = self._tokenize(expr)
            self.pos = 0
            self.context = context
            result = self._parse_or()
            if self._current().type != 'EOF':
                raise ConditionSyntaxError(f"Unexpected token after expression: {self._current().value}")
            return bool(result), None
        except (ConditionSyntaxError, ConditionRuntimeError) as e:
            return False, str(e)
    
    def _tokenize(self, expr: str) -> list[Token]:
        """词法分析。"""
        tokens = []
        pos = 0
        for match in self.TOKEN_RE.finditer(expr):
            kind = match.lastgroup
            value = match.group()
            if kind == 'WS':
                continue  # 跳过空白
            tokens.append(Token(kind, value, pos))
            pos = match.end()
        
        if pos != len(expr):
            raise ConditionSyntaxError(f"Invalid character at position {pos}: {expr[pos]}")
        
        tokens.append(Token('EOF', None, len(expr)))
        return tokens
    
    def _current(self) -> Token:
        """当前 token。"""
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token('EOF', None, -1)
    
    def _advance(self) -> Token:
        """消费当前 token 并前进。"""
        token = self._current()
        self.pos += 1
        return token
    
    def _expect(self, expected: str) -> Token:
        """期望特定 token。"""
        token = self._current()
        if token.type != expected and token.value != expected:
            raise ConditionSyntaxError(f"Expected {expected}, got {token.value}")
        return self._advance()
    
    # 语法分析（递归下降）
    
    def _parse_or(self) -> Any:
        """or_expr → and_expr ( "||" and_expr )*"""
        left = self._parse_and()
        while self._current().value == '||':
            self._advance()
            right = self._parse_and()
            left = left or right
        return left
    
    def _parse_and(self) -> Any:
        """and_expr → compare ( "&&" compare )*"""
        left = self._parse_compare()
        while self._current().value == '&&':
            self._advance()
            right = self._parse_compare()
            left = left and right
        return left
    
    def _parse_compare(self) -> Any:
        """compare → unary ( COMP_OP unary )?"""
        left = self._parse_unary()
        op = self._current().value
        if op in ('==', '!=', '>', '<', '>=', '<='):
            self._advance()
            right = self._parse_unary()
            if op == '==': return left == right
            if op == '!=': return left != right
            if op == '>': return left > right
            if op == '<': return left < right
            if op == '>=': return left >= right
            if op == '<=': return left <= right
        return left
    
    def _parse_unary(self) -> Any:
        """unary → "!" unary | primary"""
        if self._current().value == '!':
            self._advance()
            return not self._parse_unary()
        return self._parse_primary()
    
    def _parse_primary(self) -> Any:
        """primary → variable | literal | "(" expr ")" """
        token = self._current()
        
        if token.type == 'VAR':
            var_path = token.value[2:-2]  # 去掉 {{ }}
            self._advance()
            return self._resolve_variable(var_path)
        
        if token.type == 'BOOL':
            self._advance()
            return token.value == 'true'
        
        if token.type == 'NULL':
            self._advance()
            return None
        
        if token.type == 'NUM':
            self._advance()
            return float(token.value) if '.' in token.value else int(token.value)
        
        if token.type == 'STR':
            self._advance()
            return token.value[1:-1]  # 去掉引号
        
        if token.type == 'LPAREN':
            self._advance()
            result = self._parse_or()
            self._expect('RPAREN')
            return result
        
        raise ConditionSyntaxError(f"Unexpected token: {token.value}")
    
    def _resolve_variable(self, var_path: str) -> Any:
        """解析变量 node_id.output.field.subfield[0]。"""
        parts = var_path.split('.', 2)
        if len(parts) < 2 or parts[1] != 'output':
            raise ConditionRuntimeError(
                f"Variable must be {{{{<node>.output.*}}}}, got {{{{{var_path}}}}}"
            )
        
        node_id = parts[0]
        if node_id not in self.context:
            raise ConditionRuntimeError(f"Node '{node_id}' not found")
        
        output = self.context[node_id].get('output')
        if output is None:
            raise ConditionRuntimeError(f"Node '{node_id}' has no structured output")
        
        if len(parts) == 2:
            return output  # {{node.output}}
        
        field_path = parts[2]
        return self._access_path(output, field_path)
    
    def _access_path(self, obj: Any, path: str) -> Any:
        """访问嵌套字段 'a.b[0].c'。"""
        tokens = re.findall(r'[^.\[\]]+|\[\d+\]', path)
        current = obj
        
        for token in tokens:
            if token.startswith('['):
                idx = int(token[1:-1])
                if not isinstance(current, list):
                    raise ConditionRuntimeError(f"Cannot index non-list with {token}")
                if idx >= len(current):
                    raise ConditionRuntimeError(f"Index {idx} out of range")
                current = current[idx]
            else:
                if not isinstance(current, dict):
                    raise ConditionRuntimeError(f"Cannot access field '{token}' on non-dict")
                if token not in current:
                    raise ConditionRuntimeError(f"Field '{token}' not found")
                current = current[token]
        
        return current


def extract_structured_output(result_text: str) -> tuple[dict | list | None, str | None]:
    """从成员回复中提取 ```output ... ``` 标记块的 JSON。"""
    pattern = re.compile(r'```output\s*\n(.*?)\n```', re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(result_text)
    
    if not matches:
        return None, None  # 无标记块
    
    json_str = matches[-1].strip()  # 取最后一个块
    try:
        import json
        parsed = json.loads(json_str)
        if not isinstance(parsed, (dict, list)):
            return None, "Output must be JSON object or array"
        return parsed, None
    except json.JSONDecodeError as e:
        return None, f"JSON decode error: {e.msg} at position {e.pos}"
```

### 5.3 工作流校验增强

**文件**：`astrbot/dashboard/services/agent_team_dag.py`

```python
def validate_workflow_conditions(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """校验条件边的表达式语法（编辑器保存时调用）。
    
    Returns:
        字段级错误列表 [{path, code, message}]，空列表 = 通过
    """
    errors = []
    evaluator = ConditionEvaluator()
    node_ids = {n["id"] for n in nodes}
    
    for i, edge in enumerate(edges):
        condition = edge.get("condition")
        if not condition:
            continue  # 无条件边跳过
        
        # 语法检查（空上下文，仅验证语法）
        _, error = evaluator.evaluate(condition, {})
        if error:
            errors.append({
                "path": f"edges[{i}].condition",
                "code": "INVALID_SYNTAX",
                "message": f"Condition syntax error: {error}"
            })
            continue
        
        # 检查引用的节点是否存在
        referenced = _extract_referenced_nodes(condition)
        for node_id in referenced:
            if node_id not in node_ids:
                errors.append({
                    "path": f"edges[{i}].condition",
                    "code": "UNKNOWN_NODE",
                    "message": f"Condition references unknown node '{node_id}'"
                })
    
    return errors

def _extract_referenced_nodes(condition: str) -> set[str]:
    """提取条件表达式中引用的节点 ID。"""
    import re
    pattern = r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)\.'
    return set(re.findall(pattern, condition))
```

**集成**：
```python
# agent_team_service.py
def _validate_workflow_graph(self, graph: dict) -> None:
    """工作流校验（现有方法扩展）。"""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    
    # 现有校验（DAG、节点数、成员存在性等）
    # ...
    
    # 新增：条件表达式校验
    condition_errors = validate_workflow_conditions(nodes, edges)
    if condition_errors:
        raise AgentTeamsServiceError(
            "Workflow validation failed",
            field_errors=condition_errors
        )
```

## 6. 前端实现

### 6.1 编辑器 UI 扩展

**文件**：`dashboard/src/components/agent_teams/WorkflowEditor.vue`

#### 6.1.1 边条件编辑

选中边时，右侧属性面板显示：

```vue
<template>
  <div v-if="selectedEdge" class="edge-inspector">
    <h3>边属性</h3>
    
    <v-text-field
      v-model="selectedEdge.label"
      label="边标签"
      placeholder="如：通过、拒绝"
      density="compact"
    />
    
    <v-textarea
      v-model="selectedEdge.condition"
      label="条件表达式（可选）"
      placeholder="如：{{review.output.approved}} == true"
      rows="3"
      :error-messages="edgeConditionError"
      @blur="validateEdgeCondition"
    >
      <template #append-inner>
        <v-tooltip location="top">
          <template #activator="{ props }">
            <v-icon v-bind="props" size="small">mdi-help-circle-outline</v-icon>
          </template>
          <div class="condition-help">
            <p><strong>语法示例：</strong></p>
            <code>{{node.output.field}} == "value"</code><br>
            <code>{{a.output.score}} > 80 && {{b.output.ok}}</code>
          </div>
        </v-tooltip>
      </template>
    </v-textarea>
    
    <div v-if="selectedEdge.condition" class="edge-condition-hint">
      <v-icon size="small" color="info">mdi-information</v-icon>
      此边仅在条件满足时执行
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { validateConditionSyntax } from '@/utils/conditionValidator';

const selectedEdge = ref<WorkflowEdge | null>(null);
const edgeConditionError = ref<string>('');

function validateEdgeCondition() {
  if (!selectedEdge.value?.condition) {
    edgeConditionError.value = '';
    return;
  }
  
  const error = validateConditionSyntax(
    selectedEdge.value.condition,
    availableNodeIds.value
  );
  edgeConditionError.value = error || '';
}
</script>
```

#### 6.1.2 变量快捷插入

节点属性面板的"任务模板"区域增加"输出变量"chips：

```vue
<div class="output-variables-hint">
  <p>如果需要后继节点根据此节点输出做条件判断，可在回复末尾添加：</p>
  <pre class="code-hint">```output
{
  "field": "value"
}
```</pre>
  <v-btn
    variant="text"
    size="small"
    @click="insertOutputTemplate"
  >
    插入输出模板
  </v-btn>
</div>
```

### 6.2 监控视图增强

**文件**：`dashboard/src/components/agent_teams/RunMonitor.vue`

#### 6.2.1 节点状态显示

节点窗格显示解析的输出（折叠）：

```vue
<div v-if="nodeState.structured_output" class="node-output">
  <v-expansion-panels variant="accordion" density="compact">
    <v-expansion-panel>
      <v-expansion-panel-title>
        <v-icon size="small" class="mr-2">mdi-code-json</v-icon>
        结构化输出
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <pre class="output-json">{{ JSON.stringify(nodeState.structured_output, null, 2) }}</pre>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</div>

<div v-if="nodeState.output_error" class="node-output-error">
  <v-alert type="error" density="compact">
    <strong>输出解析失败：</strong>{{ nodeState.output_error }}
  </v-alert>
</div>
```

#### 6.2.2 DAG 进度视图边着色

条件边根据求值结果着色：

```vue
<VueFlowEdge
  :id="edge.id"
  :source="edge.source"
  :target="edge.target"
  :label="edge.label"
  :style="{
    stroke: getEdgeColor(edge),
    strokeWidth: edge.condition ? 2 : 1,
    strokeDasharray: edge.condition ? '5,5' : 'none'
  }"
/>

<script setup lang="ts">
function getEdgeColor(edge: WorkflowEdge): string {
  if (!edge.condition) return '#666';  // 无条件边：灰色
  
  const sourceNode = nodeStates.value[edge.source];
  if (sourceNode?.status !== 'done') return '#999';  // 前驱未完成：浅灰
  
  // 求值条件（前端复用后端求值器的 WASM 版本，或简化版）
  const satisfied = evaluateCondition(edge.condition, nodeStates.value);
  return satisfied ? '#4caf50' : '#f44336';  // 满足：绿，不满足：红
}
</script>
```

### 6.3 前端条件求值器

**新文件**：`dashboard/src/utils/conditionValidator.ts`

```typescript
/**
 * 前端条件表达式校验器（简化版，仅做语法检查和节点引用检查）。
 * 
 * 完整求值逻辑在后端；前端用于编辑器实时反馈。
 */

export function validateConditionSyntax(
  condition: string,
  availableNodeIds: string[]
): string | null {
  // 1. 检查括号匹配
  let depth = 0;
  for (const char of condition) {
    if (char === '(') depth++;
    if (char === ')') depth--;
    if (depth < 0) return '括号不匹配';
  }
  if (depth !== 0) return '括号不匹配';
  
  // 2. 检查变量格式
  const varPattern = /\{\{([a-zA-Z_][a-zA-Z0-9_.[\]]*)\}\}/g;
  const vars = [...condition.matchAll(varPattern)];
  for (const match of vars) {
    const varPath = match[1];
    const parts = varPath.split('.');
    if (parts.length < 2 || parts[1] !== 'output') {
      return `变量格式错误：${match[0]}（应为 {{node.output.field}}）`;
    }
    const nodeId = parts[0];
    if (!availableNodeIds.includes(nodeId)) {
      return `引用了不存在的节点：${nodeId}`;
    }
  }
  
  // 3. 检查运算符合法性（简化：正则匹配）
  const invalidChars = /[^a-zA-Z0-9_.\[\]{}()\s"'&|!=<>]/g;
  const invalid = condition.match(invalidChars);
  if (invalid) {
    return `非法字符：${invalid[0]}`;
  }
  
  return null;  // 通过
}

export function extractReferencedNodes(condition: string): string[] {
  const pattern = /\{\{([a-zA-Z_][a-zA-Z0-9_]*)\./g;
  const matches = [...condition.matchAll(pattern)];
  return [...new Set(matches.map(m => m[1]))];
}
```

## 7. API 变更

### 7.1 工作流保存（现有端点扩展）

**请求**（`PUT /agent_teams/workflows/{workflow_id}`）：
```json
{
  "name": "代码审查流程",
  "graph": {
    "nodes": [...],
    "edges": [
      {
        "from": "review",
        "to": "deploy",
        "condition": "{{review.output.approved}} == true",
        "label": "通过"
      }
    ]
  }
}
```

**响应**（新增字段级错误）：
```json
{
  "status": "error",
  "message": "Workflow validation failed",
  "data": {
    "fields": [
      {
        "path": "edges[0].condition",
        "code": "INVALID_SYNTAX",
        "message": "Condition syntax error: unexpected token '='"
      }
    ]
  }
}
```

### 7.2 运行监控 SSE（事件扩展）

**`node_status` 事件**（新增字段）：
```json
{
  "type": "node_status",
  "node_id": "review",
  "status": "done",
  "structured_output": {"approved": true, "score": 95},
  "output_error": null
}
```

**`node_status` 事件（输出错误）**：
```json
{
  "type": "node_status",
  "node_id": "review",
  "status": "failed",
  "error": "输出格式错误: JSON decode error: Expecting ',' delimiter",
  "output_error": "JSON decode error: Expecting ',' delimiter at position 45"
}
```

**`paused` 事件（输出错误暂停）**：
```json
{
  "type": "paused",
  "reason": "node_output_invalid",
  "node_id": "review"
}
```

### 7.3 OpenAPI 更新

**`openspec/openapi-v1.yaml`**：
```yaml
components:
  schemas:
    WorkflowEdge:
      type: object
      required: [from, to]
      properties:
        from:
          type: string
        to:
          type: string
        condition:
          type: string
          description: "条件表达式（可选）。缺省 = 无条件边"
          example: "{{review.output.approved}} == true"
        label:
          type: string
          description: "边标签（UI 显示）"
          example: "通过"
    
    NodeState:
      type: object
      properties:
        # ... 现有字段 ...
        structured_output:
          type: object
          description: "节点输出的解析后 JSON（可为 null）"
          nullable: true
        output_error:
          type: string
          description: "输出解析错误原因（可为 null）"
          nullable: true
```

## 8. 测试策略

### 8.1 后端 pytest

**文件**：`tests/agent_teams/test_conditional_branching.py`

```python
import pytest
from astrbot.dashboard.services.agent_team_condition import (
    ConditionEvaluator,
    extract_structured_output,
)

def test_extract_structured_output_valid():
    """测试输出标记块提取。"""
    text = """分析结果如下：
    
```output
{"approved": true, "score": 95}
```
    """
    output, error = extract_structured_output(text)
    assert error is None
    assert output == {"approved": True, "score": 95}

def test_extract_structured_output_no_block():
    """无标记块应返回 None（非错误）。"""
    text = "普通回复文本"
    output, error = extract_structured_output(text)
    assert output is None
    assert error is None

def test_extract_structured_output_invalid_json():
    """JSON 格式错误应返回错误原因。"""
    text = """
```output
{approved: true}
```
    """
    output, error = extract_structured_output(text)
    assert output is None
    assert "JSON decode error" in error

def test_condition_evaluator_simple():
    """简单比较。"""
    evaluator = ConditionEvaluator()
    context = {"n1": {"output": {"score": 85}}}
    
    result, error = evaluator.evaluate("{{n1.output.score}} > 80", context)
    assert error is None
    assert result is True
    
    result, error = evaluator.evaluate("{{n1.output.score}} < 80", context)
    assert result is False

def test_condition_evaluator_logical_operators():
    """逻辑运算。"""
    evaluator = ConditionEvaluator()
    context = {
        "a": {"output": {"ok": True}},
        "b": {"output": {"ok": False}},
    }
    
    result, _ = evaluator.evaluate("{{a.output.ok}} && {{b.output.ok}}", context)
    assert result is False
    
    result, _ = evaluator.evaluate("{{a.output.ok}} || {{b.output.ok}}", context)
    assert result is True
    
    result, _ = evaluator.evaluate("!{{b.output.ok}}", context)
    assert result is True

def test_condition_evaluator_nested_access():
    """嵌套字段访问。"""
    evaluator = ConditionEvaluator()
    context = {
        "data": {"output": {"user": {"name": "Alice"}, "items": [{"id": 1}]}}
    }
    
    result, _ = evaluator.evaluate('{{data.output.user.name}} == "Alice"', context)
    assert result is True
    
    result, _ = evaluator.evaluate("{{data.output.items[0].id}} == 1", context)
    assert result is True

def test_condition_evaluator_syntax_error():
    """语法错误应返回错误信息。"""
    evaluator = ConditionEvaluator()
    result, error = evaluator.evaluate("{{n1.output.x}} ===", {})
    assert result is False
    assert error is not None
    assert "Syntax error" in error or "Unexpected" in error

def test_condition_evaluator_runtime_error():
    """运行时错误（如节点不存在）。"""
    evaluator = ConditionEvaluator()
    context = {"n1": {"output": {"x": 1}}}
    
    result, error = evaluator.evaluate("{{n2.output.x}} > 0", context)
    assert result is False
    assert "not found" in error

@pytest.mark.asyncio
async def test_dag_runner_conditional_branching(build_test_team):
    """集成测试：DAG 条件分支执行。"""
    team, ports = await build_test_team()
    
    workflow = {
        "nodes": [
            {"id": "check", "member_id": "m1", "task": "检查数据，输出 approved 字段"},
            {"id": "process_a", "member_id": "m2", "task": "流程 A"},
            {"id": "process_b", "member_id": "m2", "task": "流程 B"},
        ],
        "edges": [
            {"from": "check", "to": "process_a", "condition": "{{check.output.approved}} == true"},
            {"from": "check", "to": "process_b", "condition": "{{check.output.approved}} == false"},
        ],
    }
    
    # 脚本化 ports：check 节点返回 approved=true
    ports.script_collect("m1", """检查完成。
    
```output
{"approved": true}
```
    """)
    ports.script_collect("m2", "流程 A 完成")
    
    runner = DAGRunner(team, workflow, "test input", ports)
    await runner.run()
    
    # 断言：process_a 执行，process_b 跳过
    assert runner.node_states["check"]["status"] == "done"
    assert runner.node_states["check"]["structured_output"] == {"approved": True}
    assert runner.node_states["process_a"]["status"] == "done"
    assert runner.node_states["process_b"]["status"] == "skipped"
```

### 8.2 前端 vitest

**文件**：`dashboard/src/utils/conditionValidator.spec.ts`

```typescript
import { describe, it, expect } from 'vitest';
import { validateConditionSyntax, extractReferencedNodes } from './conditionValidator';

describe('validateConditionSyntax', () => {
  it('accepts valid simple condition', () => {
    const error = validateConditionSyntax('{{n1.output.x}} > 0', ['n1']);
    expect(error).toBeNull();
  });
  
  it('rejects invalid variable format', () => {
    const error = validateConditionSyntax('{{n1.x}} > 0', ['n1']);
    expect(error).toContain('格式错误');
  });
  
  it('rejects unknown node reference', () => {
    const error = validateConditionSyntax('{{n2.output.x}} > 0', ['n1']);
    expect(error).toContain('不存在的节点');
  });
  
  it('rejects unmatched parentheses', () => {
    const error = validateConditionSyntax('(({{n1.output.x}} > 0)', ['n1']);
    expect(error).toContain('括号');
  });
});

describe('extractReferencedNodes', () => {
  it('extracts single node', () => {
    const nodes = extractReferencedNodes('{{review.output.approved}} == true');
    expect(nodes).toEqual(['review']);
  });
  
  it('extracts multiple nodes', () => {
    const nodes = extractReferencedNodes('{{a.output.x}} && {{b.output.y}}');
    expect(nodes).toEqual(['a', 'b']);
  });
  
  it('deduplicates node ids', () => {
    const nodes = extractReferencedNodes('{{n1.output.x}} > {{n1.output.y}}');
    expect(nodes).toEqual(['n1']);
  });
});
```

**文件**：`dashboard/src/components/agent_teams/WorkflowEditor.spec.ts`

```typescript
it('shows condition field when edge is selected', async () => {
  // ... 选中边 ...
  const conditionInput = wrapper.find('[label="条件表达式"]');
  expect(conditionInput.exists()).toBe(true);
});

it('validates condition syntax on blur', async () => {
  // ... 输入非法条件 ...
  await conditionInput.trigger('blur');
  expect(wrapper.text()).toContain('语法错误');
});
```

### 8.3 手工验收

1. **简单分支**：代码审查节点 → approved=true 走部署分支，approved=false 走返工分支
2. **多条件**：风险评估 → `level=="high" || score > 90` 走人工审批，否则自动处理
3. **输出错误恢复**：节点输出 JSON 格式错误 → run paused → 用户修复任务模板 → retry → 继续
4. **无条件边混合**：审查节点 → 条件边（部署/返工）+ 无条件边（通知），通知总是执行
5. **边着色**：DAG 进度视图中，满足条件的边显示绿色，不满足显示红色

## 9. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| LLM 路由节点（专门决策节点） | 额外调用开销；节点输出门控更通用（成员自然决策） |
| 结构化输出 API（OpenAI JSON mode） | 依赖模型支持；Markdown 块更通用且对所有模型友好 |
| eval() 求值 | 安全风险（代码注入）；手写解析器完全可控 |
| 边条件必须全覆盖（不允许无条件边） | 过于严格；混合模式更灵活（如"通知"类边） |
| 输出错误直接失败（不 paused） | 失去修复机会；paused + retry 更友好 |

## 10. 未来扩展方向

本设计为后续功能奠定基础：

1. **Phase 3: 循环边** — 复用条件求值引擎，增加 `max_iterations` 计数器
2. **Phase 4: 子工作流** — 子图内部节点的条件边遵循相同语义
3. **条件表达式增强**：
   - 内置函数（`length({{n.output.list}})`、`contains({{n.output.text}}, "关键词")`）
   - 正则匹配（`matches({{n.output.text}}, "^[0-9]+$")`）
   - 时间比较（`{{n.output.timestamp}} > now() - 3600`）
4. **可视化条件编辑器** — 拖拽式表达式构建器（降低语法门槛）

## 11. 迁移与兼容性

- **现有工作流零改动**：边无 `condition` 字段 = 无条件边，行为不变
- **运行状态表**：`node_states` 的新键（`structured_output`/`output_error`）在老运行中为 None（合法值）
- **前端向后兼容**：编辑器检测 `edge.condition` 字段是否存在，不存在则不显示条件编辑 UI
- **API 版本**：v1 端点扩展字段（可选），不破坏现有客户端

---

## 附录 A：条件表达式 EBNF 语法

```ebnf
expr       ::= or_expr
or_expr    ::= and_expr ( "||" and_expr )*
and_expr   ::= compare ( "&&" compare )*
compare    ::= unary ( COMP_OP unary )?
unary      ::= "!" unary | primary
primary    ::= variable | literal | "(" expr ")"

variable   ::= "{{" PATH "}}"
literal    ::= BOOL | NULL | NUMBER | STRING

COMP_OP    ::= "==" | "!=" | ">=" | "<=" | ">" | "<"
PATH       ::= [a-zA-Z_][a-zA-Z0-9_.\[\]]*
BOOL       ::= "true" | "false"
NULL       ::= "null"
NUMBER     ::= -?[0-9]+(\.[0-9]+)?
STRING     ::= '"' [^"]* '"' | "'" [^']* "'"
```

## 附录 B：示例工作流

```json
{
  "name": "智能代码审查流程",
  "graph": {
    "nodes": [
      {
        "id": "static_check",
        "member_id": "linter",
        "title": "静态检查",
        "task": "运行 ESLint 检查代码，输出 passed 和 error_count 字段"
      },
      {
        "id": "security_scan",
        "member_id": "security",
        "title": "安全扫描",
        "task": "扫描安全漏洞，输出 vulnerabilities 列表"
      },
      {
        "id": "human_review",
        "member_id": "reviewer",
        "title": "人工审查",
        "task": "人工审查代码质量，输出 approved 和 comments"
      },
      {
        "id": "deploy",
        "member_id": "deployer",
        "title": "部署",
        "task": "部署到生产环境"
      },
      {
        "id": "fix",
        "member_id": "developer",
        "title": "修复",
        "task": "根据反馈修复代码"
      },
      {
        "id": "notify",
        "member_id": "notifier",
        "title": "通知",
        "task": "发送审查结果通知"
      }
    ],
    "edges": [
      {"from": "static_check", "to": "security_scan"},
      {"from": "security_scan", "to": "human_review", 
       "condition": "{{security_scan.output.vulnerabilities}} == []",
       "label": "无漏洞"},
      {"from": "security_scan", "to": "fix",
       "condition": "{{security_scan.output.vulnerabilities}} != []",
       "label": "发现漏洞"},
      {"from": "human_review", "to": "deploy",
       "condition": "{{human_review.output.approved}} == true && {{static_check.output.passed}} == true",
       "label": "通过"},
      {"from": "human_review", "to": "fix",
       "condition": "{{human_review.output.approved}} == false",
       "label": "拒绝"},
      {"from": "human_review", "to": "notify", "label": "发通知"}
    ]
  }
}
```

**执行流程示例**：
1. `static_check` 执行 → 输出 `{passed: true, error_count: 0}`
2. `security_scan` 执行 → 输出 `{vulnerabilities: []}`
3. 条件边 `security_scan → human_review` 满足（无漏洞），执行人工审查
4. `human_review` 输出 `{approved: true, comments: "代码质量良好"}`
5. 条件边 `human_review → deploy` 满足（approved=true 且 static_check.passed=true），部署
6. 无条件边 `human_review → notify` 总是执行，发送通知
7. 运行完成，节点 `fix` 被跳过（所有入边条件不满足）
