# Agent Teams 模板库设计方案

- 作者：elecvoid243
- 日期：2026-09-07
- 前作：
  - [2026-09-07-agent-teams-conditional-branching-design.md](./2026-09-07-agent-teams-conditional-branching-design.md)（Phase 1）
  - [2026-09-07-agent-teams-human-input-design.md](./2026-09-07-agent-teams-human-input-design.md)（Phase 2）
  - [2026-09-07-agent-teams-loop-edges-design.md](./2026-09-07-agent-teams-loop-edges-design.md)（Phase 3）
  - [2026-09-07-agent-teams-subworkflow-design.md](./2026-09-07-agent-teams-subworkflow-design.md)（Phase 4）
- 状态：设计评审
- 适用基线：Agent Teams 主体功能 + Refinements + Phase 1-4 已落地

## 1. 背景与目标

Phase 4 实现了工作流的 JSON 导出/导入，但仍是**点对点分享**（文件传输）。模板库将其升级为**内置的工作流市场**：

1. **发布模板**：用户将工作流标记为模板并发布到库中（公开/私有）
2. **浏览与搜索**：其他用户在 Dashboard 浏览模板、按标签搜索、预览结构
3. **一键导入**：点击导入，自动映射成员，创建到自己的团队
4. **评分与反馈**：使用后评分、留言，帮助他人选择优质模板
5. **版本管理**：模板作者可发布新版本，用户可选择更新

### 1.1 核心设计决策

| 决策点 | 结论 |
|---|---|
| 存储位置 | 服务器端数据库表 `workflow_templates`（非文件系统） |
| 权限模型 | 公开模板（所有人可见）/ 私有模板（仅作者与指定团队可见）|
| 版本策略 | 每次发布创建新版本记录；用户可选择导入特定版本 |
| 作者归属 | 记录 `author_username`（创建者）和 `source_team_id`（源团队） |
| 成员占位符 | 模板中成员 ID 替换为角色占位符（如 `@reviewer`、`@coder`），导入时映射 |
| 评分系统 | 5 星评分 + 文本评论；聚合显示平均分与总下载数 |
| 搜索与过滤 | 按名称、标签、作者、评分、下载数搜索与排序 |
| 更新通知 | 用户导入模板后记录来源，作者发布新版本时通知（可选） |

## 2. 数据模型

### 2.1 模板表（新增）

```python
class WorkflowTemplate(TimestampMixin, SQLModel, table=True):
    """工作流模板（可公开分享的工作流）。"""
    
    __tablename__ = "workflow_templates"
    
    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})
    template_id: str = Field(max_length=32, nullable=False, unique=True)  # 模板唯一 ID
    
    # 基本信息
    name: str = Field(max_length=128, nullable=False)
    description: str = Field(sa_type=Text, default="")
    author_username: str = Field(max_length=64, nullable=False, index=True)  # 作者
    source_team_id: str = Field(max_length=32, nullable=False)  # 源团队（所有权校验）
    
    # 版本
    version: str = Field(max_length=16, nullable=False, default="1.0")  # 当前版本
    version_history: list = Field(default_factory=list, sa_type=JSON)  # [{version, published_at, changes}]
    
    # 分类与标签
    category: str = Field(max_length=32, default="general")  # 分类（如 data, content, automation）
    tags: list = Field(default_factory=list, sa_type=JSON)  # 标签数组
    
    # 工作流内容
    graph: dict = Field(default_factory=dict, sa_type=JSON)  # {nodes, edges}（成员 ID 已替换为角色占位符）
    layout: dict = Field(default_factory=dict, sa_type=JSON)  # 画布布局
    
    # 成员角色定义
    member_roles: list = Field(default_factory=list, sa_type=JSON)
    # [{role_id: "@reviewer", name: "审阅者", suggested_persona: "code-reviewer", description: "负责代码审查"}]
    
    # 权限与可见性
    visibility: str = Field(max_length=16, nullable=False, default="private")  # public | private
    allowed_teams: list = Field(default_factory=list, sa_type=JSON)  # 私有模板允许访问的团队 ID 列表
    
    # 统计
    download_count: int = Field(default=0, nullable=False)  # 导入次数
    rating_average: float = Field(default=0.0, nullable=False)  # 平均评分（0-5）
    rating_count: int = Field(default=0, nullable=False)  # 评分数量
    
    # 状态
    status: str = Field(max_length=16, nullable=False, default="published")  # published | archived
    
    __table_args__ = (
        Index("ix_templates_visibility_status", "visibility", "status"),
        Index("ix_templates_category_tags", "category"),
    )
```

### 2.2 模板使用记录表（新增）

```python
class TemplateUsage(TimestampMixin, SQLModel, table=True):
    """模板使用记录（跟踪谁导入了哪个模板）。"""
    
    __tablename__ = "template_usages"
    
    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})
    template_id: str = Field(max_length=32, nullable=False, index=True)
    template_version: str = Field(max_length=16, nullable=False)  # 导入时的版本
    
    user_username: str = Field(max_length=64, nullable=False, index=True)  # 导入者
    team_id: str = Field(max_length=32, nullable=False)  # 导入到的团队
    workflow_id: str = Field(max_length=32, nullable=False)  # 创建的工作流 ID
    
    # 是否接收更新通知
    notify_updates: bool = Field(default=True, nullable=False)
    
    __table_args__ = (
        Index("ix_template_usages_template_user", "template_id", "user_username"),
    )
```

### 2.3 评分表（新增）

```python
class TemplateRating(TimestampMixin, SQLModel, table=True):
    """模板评分与评论。"""
    
    __tablename__ = "template_ratings"
    
    id: int | None = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})
    template_id: str = Field(max_length=32, nullable=False, index=True)
    user_username: str = Field(max_length=64, nullable=False, index=True)
    
    rating: int = Field(nullable=False)  # 1-5 星
    comment: str = Field(sa_type=Text, default="")  # 评论（可选）
    
    __table_args__ = (
        UniqueConstraint("template_id", "user_username", name="uix_template_user_rating"),
    )
```

### 2.4 工作流表扩展

```python
# AgentTeamWorkflow 扩展（Phase 4 已有部分字段）
class AgentTeamWorkflow:
    # ... 现有字段 ...
    
    # 新增：模板来源
    template_source: dict | None = Field(default=None, sa_type=JSON)
    # {template_id, version, imported_at}（从模板导入时记录）
```

## 3. 成员角色占位符

### 3.1 发布时的转换

```python
def _convert_members_to_roles(workflow: dict, team_members: list[dict]) -> tuple[dict, list[dict]]:
    """将工作流中的 member_id 转换为角色占位符。
    
    Returns:
        (转换后的 workflow, member_roles 定义)
    """
    # 建立成员 ID → 角色映射
    role_mapping = {}  # {member_id: role_id}
    member_roles = []
    
    for i, member in enumerate(team_members):
        role_id = f"@role_{i+1}"  # 或更语义化：@coder, @reviewer
        role_mapping[member["member_id"]] = role_id
        
        member_roles.append({
            "role_id": role_id,
            "name": member["name"],
            "suggested_persona": member.get("persona_id"),
            "suggested_provider": member.get("provider_id"),
            "description": f"角色：{member['name']}"
        })
    
    # 替换节点中的 member_id
    converted_graph = copy.deepcopy(workflow["graph"])
    for node in converted_graph["nodes"]:
        if node.get("type") in (None, "member"):
            old_id = node.get("member_id")
            if old_id in role_mapping:
                node["member_id"] = role_mapping[old_id]
    
    return {"graph": converted_graph, "layout": workflow["layout"]}, member_roles
```

### 3.2 导入时的映射

```python
def _map_roles_to_members(
    template_graph: dict,
    role_mapping: dict[str, str]  # {role_id: target_member_id}
) -> dict:
    """将模板中的角色占位符替换为目标团队的成员 ID。"""
    converted_graph = copy.deepcopy(template_graph)
    
    for node in converted_graph["nodes"]:
        if node.get("type") in (None, "member"):
            role_id = node.get("member_id")
            if role_id and role_id.startswith("@"):
                if role_id in role_mapping:
                    node["member_id"] = role_mapping[role_id]
                else:
                    raise TemplateMappingError(f"Role {role_id} not mapped")
    
    return converted_graph
```

## 4. 后端 API

### 4.1 模板 CRUD

#### 发布模板

```
POST /api/v1/agent_teams/templates
Content-Type: application/json
```

**请求体**：
```json
{
  "workflow_id": "wf_123",  // 源工作流 ID
  "name": "代码审查流程",
  "description": "标准的代码审查工作流，包含静态检查、安全扫描、人工审查",
  "category": "development",
  "tags": ["code-review", "ci-cd", "quality"],
  "visibility": "public",  // public | private
  "version": "1.0"
}
```

**逻辑**：
1. 权限校验：用户是工作流所属团队的 owner
2. 加载工作流，转换成员 ID 为角色占位符
3. 创建 `WorkflowTemplate` 记录
4. 返回 `template_id`

#### 更新模板（发布新版本）

```
POST /api/v1/agent_teams/templates/{template_id}/versions
```

**请求体**：
```json
{
  "version": "1.1",
  "changes": "添加了超时配置，优化了审查流程",
  "workflow_id": "wf_123_v2"  // 新版本的工作流
}
```

**逻辑**：
1. 权限校验：用户是模板作者
2. 追加 `version_history`
3. 更新 `graph`、`version` 字段
4. 通知已导入该模板且开启通知的用户

#### 列出模板

```
GET /api/v1/agent_teams/templates?
    category=development&
    tags=code-review&
    search=代码&
    visibility=public&
    sort=downloads&
    page=1&
    limit=20
```

**响应**：
```json
{
  "status": "ok",
  "data": {
    "templates": [
      {
        "template_id": "tpl_abc123",
        "name": "代码审查流程",
        "description": "...",
        "author_username": "astrbot",
        "version": "1.1",
        "category": "development",
        "tags": ["code-review", "ci-cd"],
        "rating_average": 4.5,
        "rating_count": 23,
        "download_count": 156,
        "preview": {
          "node_count": 5,
          "member_roles_count": 3
        },
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-05T14:30:00Z"
      }
    ],
    "total": 45,
    "page": 1,
    "limit": 20
  }
}
```

#### 获取模板详情

```
GET /api/v1/agent_teams/templates/{template_id}
```

**响应**：
```json
{
  "status": "ok",
  "data": {
    "template_id": "tpl_abc123",
    "name": "代码审查流程",
    "description": "...",
    "author_username": "astrbot",
    "version": "1.1",
    "version_history": [
      {"version": "1.0", "published_at": "2026-09-01T10:00:00Z", "changes": "首次发布"},
      {"version": "1.1", "published_at": "2026-09-05T14:30:00Z", "changes": "添加超时配置"}
    ],
    "category": "development",
    "tags": ["code-review", "ci-cd"],
    "graph": {...},  // 完整图结构（供预览）
    "layout": {...},
    "member_roles": [
      {"role_id": "@reviewer", "name": "审阅者", "suggested_persona": "code-reviewer"},
      {"role_id": "@tester", "name": "测试员", "suggested_persona": "tester"}
    ],
    "rating_average": 4.5,
    "rating_count": 23,
    "download_count": 156,
    "visibility": "public"
  }
}
```

### 4.2 模板导入

```
POST /api/v1/agent_teams/{team_id}/workflows/from_template
```

**请求体**：
```json
{
  "template_id": "tpl_abc123",
  "template_version": "1.1",  // 可选，缺省 = 最新版本
  "role_mapping": {
    "@reviewer": "m1",
    "@tester": "m2"
  },
  "workflow_name": "项目 A 代码审查"  // 可选，覆盖模板名称
}
```

**逻辑**：
1. 权限校验：用户是目标团队成员
2. 加载模板
3. 校验 `role_mapping` 完整性
4. 替换角色占位符为目标成员 ID
5. 创建新工作流，记录 `template_source`
6. 创建 `TemplateUsage` 记录
7. 递增模板 `download_count`
8. 返回新 `workflow_id`

**响应**：
```json
{
  "status": "ok",
  "data": {
    "workflow_id": "wf_new_456",
    "name": "项目 A 代码审查",
    "template_source": {
      "template_id": "tpl_abc123",
      "version": "1.1",
      "imported_at": "2026-09-07T22:00:00Z"
    }
  }
}
```

### 4.3 评分系统

#### 提交评分

```
POST /api/v1/agent_teams/templates/{template_id}/ratings
```

**请求体**：
```json
{
  "rating": 5,  // 1-5
  "comment": "非常好用的模板，节省了大量时间！"
}
```

**逻辑**：
1. 权限校验：用户已导入过该模板（`TemplateUsage` 表检查）
2. Upsert `TemplateRating`（同用户覆盖旧评分）
3. 重新计算模板的 `rating_average` 和 `rating_count`
4. 返回新平均分

#### 列出评分

```
GET /api/v1/agent_teams/templates/{template_id}/ratings?page=1&limit=10
```

**响应**：
```json
{
  "status": "ok",
  "data": {
    "ratings": [
      {
        "user_username": "user1",
        "rating": 5,
        "comment": "非常好用！",
        "created_at": "2026-09-06T10:00:00Z"
      }
    ],
    "average": 4.5,
    "total": 23
  }
}
```

## 5. 前端实现

### 5.1 模板库浏览页面

**新页面**：`dashboard/src/views/TemplateLibraryPage.vue`

```vue
<template>
  <div class="template-library-page">
    <v-container fluid class="pa-4 pa-md-6">
      <div class="page-header">
        <h1>工作流模板库</h1>
        <v-btn
          color="primary"
          variant="tonal"
          prepend-icon="mdi-publish"
          @click="showPublishDialog = true"
        >
          发布模板
        </v-btn>
      </div>
      
      <!-- 搜索与过滤 -->
      <v-row class="mt-4">
        <v-col cols="12" md="8">
          <v-text-field
            v-model="searchQuery"
            prepend-inner-icon="mdi-magnify"
            label="搜索模板"
            placeholder="输入名称或标签..."
            clearable
            @input="onSearch"
          />
        </v-col>
        <v-col cols="12" md="4">
          <v-select
            v-model="selectedCategory"
            :items="categories"
            label="分类"
            clearable
          />
        </v-col>
      </v-row>
      
      <v-row>
        <v-col cols="12" md="3">
          <v-select
            v-model="sortBy"
            :items="sortOptions"
            label="排序"
            density="compact"
          />
          <v-chip-group v-model="selectedTags" column multiple>
            <v-chip
              v-for="tag in popularTags"
              :key="tag"
              filter
              variant="outlined"
            >
              {{ tag }}
            </v-chip>
          </v-chip-group>
        </v-col>
        
        <v-col cols="12" md="9">
          <!-- 模板卡片网格 -->
          <v-row>
            <v-col
              v-for="template in templates"
              :key="template.template_id"
              cols="12"
              sm="6"
              lg="4"
            >
              <v-card class="template-card" @click="onTemplateClick(template)">
                <v-card-title>{{ template.name }}</v-card-title>
                <v-card-subtitle>
                  <v-icon size="small">mdi-account</v-icon>
                  {{ template.author_username }}
                  <v-chip size="x-small" class="ml-2">v{{ template.version }}</v-chip>
                </v-card-subtitle>
                
                <v-card-text>
                  <p class="template-description">
                    {{ truncate(template.description, 100) }}
                  </p>
                  
                  <div class="template-meta">
                    <v-chip size="small" variant="text">
                      <v-icon size="small">mdi-star</v-icon>
                      {{ template.rating_average.toFixed(1) }}
                      ({{ template.rating_count }})
                    </v-chip>
                    <v-chip size="small" variant="text">
                      <v-icon size="small">mdi-download</v-icon>
                      {{ template.download_count }}
                    </v-chip>
                  </div>
                  
                  <div class="template-tags">
                    <v-chip
                      v-for="tag in template.tags.slice(0, 3)"
                      :key="tag"
                      size="x-small"
                      variant="tonal"
                    >
                      {{ tag }}
                    </v-chip>
                  </div>
                </v-card-text>
                
                <v-card-actions>
                  <v-spacer />
                  <v-btn
                    variant="text"
                    size="small"
                    @click.stop="onPreview(template)"
                  >
                    预览
                  </v-btn>
                  <v-btn
                    color="primary"
                    variant="tonal"
                    size="small"
                    @click.stop="onImport(template)"
                  >
                    导入
                  </v-btn>
                </v-card-actions>
              </v-card>
            </v-col>
          </v-row>
          
          <!-- 分页 -->
          <div class="text-center mt-4">
            <v-pagination
              v-model="currentPage"
              :length="totalPages"
              @update:model-value="loadTemplates"
            />
          </div>
        </v-col>
      </v-row>
    </v-container>
    
    <!-- 模板详情对话框 -->
    <TemplateDetailDialog
      v-model="showDetailDialog"
      :template="selectedTemplate"
      @import="onImport"
      @rate="onRate"
    />
    
    <!-- 发布模板对话框 -->
    <PublishTemplateDialog
      v-model="showPublishDialog"
      @published="onTemplatePublished"
    />
    
    <!-- 导入模板对话框 -->
    <ImportTemplateDialog
      v-model="showImportDialog"
      :template="importingTemplate"
      @imported="onTemplateImported"
    />
  </div>
</template>

<script setup lang="ts">
const templates = ref<any[]>([]);
const searchQuery = ref('');
const selectedCategory = ref<string | null>(null);
const selectedTags = ref<string[]>([]);
const sortBy = ref('downloads');
const currentPage = ref(1);
const totalPages = ref(1);

const categories = [
  {value: 'development', title: '开发'},
  {value: 'data', title: '数据处理'},
  {value: 'content', title: '内容生成'},
  {value: 'automation', title: '自动化'},
  {value: 'general', title: '通用'}
];

const sortOptions = [
  {value: 'downloads', title: '下载量'},
  {value: 'rating', title: '评分'},
  {value: 'created_at', title: '最新'}
];

async function loadTemplates() {
  try {
    const params = {
      search: searchQuery.value,
      category: selectedCategory.value,
      tags: selectedTags.value.join(','),
      sort: sortBy.value,
      page: currentPage.value,
      limit: 12
    };
    
    const result = await templateApi.listTemplates(params);
    templates.value = result.data.templates;
    totalPages.value = Math.ceil(result.data.total / 12);
  } catch (err) {
    error('加载模板失败');
  }
}

onMounted(() => {
  loadTemplates();
});
</script>
```

### 5.2 模板详情对话框

```vue
<template>
  <v-dialog v-model="isOpen" max-width="900px" scrollable>
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ template.name }}
        <v-chip size="small" class="ml-2">v{{ template.version }}</v-chip>
      </v-card-title>
      
      <v-card-subtitle class="pl-6">
        作者：{{ template.author_username }}
        <v-chip size="x-small" class="ml-2">{{ getCategoryName(template.category) }}</v-chip>
      </v-card-subtitle>
      
      <v-card-text class="pa-6">
        <!-- 评分与下载 -->
        <div class="template-stats">
          <v-rating
            :model-value="template.rating_average"
            readonly
            density="compact"
            size="small"
          />
          <span>{{ template.rating_average.toFixed(1) }} ({{ template.rating_count }} 条评价)</span>
          <v-chip size="small" variant="text">
            <v-icon>mdi-download</v-icon>
            {{ template.download_count }} 次下载
          </v-chip>
        </div>
        
        <!-- 描述 -->
        <div class="template-description mt-4">
          <h4>描述</h4>
          <p>{{ template.description }}</p>
        </div>
        
        <!-- 成员角色 -->
        <div class="member-roles mt-4">
          <h4>所需成员角色</h4>
          <v-list density="compact">
            <v-list-item
              v-for="role in template.member_roles"
              :key="role.role_id"
            >
              <template #prepend>
                <v-icon>mdi-account</v-icon>
              </template>
              <v-list-item-title>{{ role.name }}</v-list-item-title>
              <v-list-item-subtitle>{{ role.description }}</v-list-item-subtitle>
              <template #append>
                <v-chip v-if="role.suggested_persona" size="x-small">
                  推荐：{{ role.suggested_persona }}
                </v-chip>
              </template>
            </v-list-item>
          </v-list>
        </div>
        
        <!-- 工作流预览（只读 DAG） -->
        <div class="workflow-preview mt-4">
          <h4>工作流结构</h4>
          <TeamsFlowCanvas
            :nodes="template.graph.nodes"
            :edges="template.graph.edges"
            :layout="template.layout"
            :readonly="true"
            :compact="true"
          />
        </div>
        
        <!-- 标签 -->
        <div class="template-tags mt-4">
          <v-chip
            v-for="tag in template.tags"
            :key="tag"
            size="small"
            variant="tonal"
          >
            {{ tag }}
          </v-chip>
        </div>
        
        <!-- 版本历史 -->
        <v-expansion-panels class="mt-4">
          <v-expansion-panel>
            <v-expansion-panel-title>版本历史</v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-timeline density="compact" side="end">
                <v-timeline-item
                  v-for="ver in template.version_history"
                  :key="ver.version"
                  dot-color="primary"
                  size="small"
                >
                  <template #opposite>
                    <strong>v{{ ver.version }}</strong>
                  </template>
                  <div>
                    <div>{{ formatDate(ver.published_at) }}</div>
                    <div class="text-caption">{{ ver.changes }}</div>
                  </div>
                </v-timeline-item>
              </v-timeline>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        
        <!-- 评论区 -->
        <div class="template-ratings mt-4">
          <h4>用户评价</h4>
          <v-list>
            <v-list-item
              v-for="rating in ratings"
              :key="rating.id"
            >
              <template #prepend>
                <v-avatar size="32">{{ rating.user_username[0] }}</v-avatar>
              </template>
              <v-list-item-title>{{ rating.user_username }}</v-list-item-title>
              <v-list-item-subtitle>
                <v-rating
                  :model-value="rating.rating"
                  readonly
                  density="compact"
                  size="x-small"
                />
                <div>{{ rating.comment }}</div>
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </div>
      </v-card-text>
      
      <v-card-actions class="pa-4">
        <v-btn
          v-if="canRate"
          variant="text"
          prepend-icon="mdi-star"
          @click="emit('rate', template)"
        >
          评分
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">关闭</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          prepend-icon="mdi-download"
          @click="emit('import', template)"
        >
          导入到团队
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
```

### 5.3 发布模板对话框

```vue
<template>
  <v-dialog v-model="isOpen" max-width="600px">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">发布为模板</v-card-title>
      
      <v-card-text class="pa-6">
        <v-select
          v-model="selectedWorkflowId"
          :items="availableWorkflows"
          item-title="name"
          item-value="workflow_id"
          label="选择工作流 *"
          required
        />
        
        <v-text-field
          v-model="templateName"
          label="模板名称 *"
          required
        />
        
        <v-textarea
          v-model="templateDescription"
          label="模板描述"
          rows="3"
          hint="详细说明模板的用途和适用场景"
          persistent-hint
        />
        
        <v-select
          v-model="templateCategory"
          :items="categories"
          label="分类 *"
          required
        />
        
        <v-combobox
          v-model="templateTags"
          :items="suggestedTags"
          label="标签"
          multiple
          chips
          hint="输入后按回车添加"
          persistent-hint
        />
        
        <v-select
          v-model="templateVisibility"
          :items="[
            {value: 'public', title: '公开（所有人可见）'},
            {value: 'private', title: '私有（仅自己可见）'}
          ]"
          label="可见性 *"
        />
        
        <v-text-field
          v-model="templateVersion"
          label="版本号"
          placeholder="1.0"
        />
      </v-card-text>
      
      <v-card-actions class="pa-4">
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">取消</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="publishing"
          :disabled="!isValid"
          @click="onPublish"
        >
          发布
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
async function onPublish() {
  publishing.value = true;
  
  try {
    const payload = {
      workflow_id: selectedWorkflowId.value,
      name: templateName.value,
      description: templateDescription.value,
      category: templateCategory.value,
      tags: templateTags.value,
      visibility: templateVisibility.value,
      version: templateVersion.value || '1.0'
    };
    
    const result = await templateApi.publishTemplate(payload);
    
    success(`模板"${templateName.value}"已发布`);
    emit('published', result.data);
    emit('update:modelValue', false);
    
  } catch (err) {
    error('发布失败：' + extractApiError(err).message);
  } finally {
    publishing.value = false;
  }
}
</script>
```

### 5.4 菜单入口

```vue
<!-- dashboard/src/layouts/DashboardLayout.vue -->
<v-list-item
  to="/template-library"
  prepend-icon="mdi-store"
>
  <v-list-item-title>模板库</v-list-item-title>
</v-list-item>
```

## 6. 典型使用流程

### 6.1 发布模板

1. 用户在自己的团队中创建并完善工作流
2. 点击"发布为模板"
3. 填写模板信息（名称、描述、分类、标签、可见性）
4. 系统自动将成员 ID 转换为角色占位符
5. 模板发布到库中

### 6.2 浏览与导入

1. 用户进入"模板库"页面
2. 按分类/标签/评分筛选
3. 点击模板卡片查看详情（预览 DAG、评价、版本历史）
4. 点击"导入到团队"
5. 选择目标团队
6. 映射角色到团队成员（如 `@reviewer` → `m1`）
7. 系统创建新工作流，自动替换成员 ID
8. 用户可直接使用或进一步编辑

### 6.3 评分与反馈

1. 用户导入并使用模板后
2. 返回模板详情页
3. 点击"评分"，选择 1-5 星 + 可选评论
4. 提交后更新模板平均分
5. 其他用户可查看评价参考

### 6.4 版本更新

1. 模板作者改进工作流
2. 发布新版本（如 v1.1）
3. 系统通知已导入该模板的用户
4. 用户可选择更新（重新导入最新版本）或继续使用旧版本

## 7. 测试策略

### 7.1 后端 pytest

```python
def test_publish_template(client, auth_token):
    """测试发布模板。"""
    payload = {
        "workflow_id": "wf_123",
        "name": "测试模板",
        "description": "描述",
        "category": "development",
        "tags": ["test"],
        "visibility": "public",
        "version": "1.0"
    }
    
    response = client.post(
        "/api/v1/agent_teams/templates",
        json=payload,
        headers={"Authorization": f"Bearer {auth_token}"}
    )
    
    assert response.status_code == 200
    assert response.json()["data"]["template_id"].startswith("tpl_")

def test_role_conversion():
    """测试成员 ID → 角色占位符转换。"""
    workflow = {
        "graph": {
            "nodes": [
                {"id": "n1", "member_id": "m1", "task": "task"}
            ]
        }
    }
    
    members = [{"member_id": "m1", "name": "审阅者"}]
    
    converted, roles = _convert_members_to_roles(workflow, members)
    
    assert converted["graph"]["nodes"][0]["member_id"].startswith("@")
    assert len(roles) == 1
    assert roles[0]["name"] == "审阅者"

def test_import_template_with_mapping(client, auth_token):
    """测试导入模板并映射角色。"""
    payload = {
        "template_id": "tpl_123",
        "role_mapping": {"@reviewer": "m1", "@tester": "m2"}
    }
    
    response = client.post(
        "/api/v1/agent_teams/team_1/workflows/from_template",
        json=payload,
        headers={"Authorization": f"Bearer {auth_token}"}
    )
    
    assert response.status_code == 200
    assert response.json()["data"]["workflow_id"].startswith("wf_")

def test_rating_aggregation():
    """测试评分聚合。"""
    # 提交 3 条评分：5, 4, 3
    for rating in [5, 4, 3]:
        submit_rating("tpl_123", f"user{rating}", rating, "")
    
    template = db.query(WorkflowTemplate).filter(...).first()
    assert template.rating_average == 4.0
    assert template.rating_count == 3

def test_private_template_access():
    """测试私有模板权限。"""
    # 用户 A 发布私有模板
    # 用户 B 无法访问
    # 用户 C（在 allowed_teams 中）可访问
    pass
```

### 7.2 前端 vitest

```typescript
it('renders template cards with stats', () => {
  const templates = [
    {
      template_id: 'tpl_1',
      name: 'Test Template',
      rating_average: 4.5,
      download_count: 100
    }
  ];
  
  const wrapper = mount(TemplateLibraryPage, {
    data: () => ({templates})
  });
  
  expect(wrapper.text()).toContain('Test Template');
  expect(wrapper.text()).toContain('4.5');
  expect(wrapper.text()).toContain('100');
});

it('filters templates by category', async () => {
  const wrapper = mount(TemplateLibraryPage);
  
  await wrapper.find('[data-test="category-select"]').setValue('development');
  
  expect(mockApi.listTemplates).toHaveBeenCalledWith(
    expect.objectContaining({category: 'development'})
  );
});

it('maps roles to members on import', async () => {
  const template = {
    member_roles: [
      {role_id: '@reviewer', name: '审阅者'}
    ]
  };
  
  const wrapper = mount(ImportTemplateDialog, {
    props: {template, availableMembers: [{member_id: 'm1', name: 'Alice'}]}
  });
  
  await wrapper.find('[data-role="@reviewer"]').setValue('m1');
  await wrapper.find('[data-test="import-btn"]').trigger('click');
  
  expect(mockApi.importTemplate).toHaveBeenCalledWith(
    expect.objectContaining({
      role_mapping: {'@reviewer': 'm1'}
    })
  );
});
```

### 7.3 手工验收

1. **发布公开模板**：创建工作流 → 发布为公开模板 → 在库中能搜到
2. **导入并映射**：浏览模板 → 预览结构 → 导入 → 映射 3 个角色 → 成功创建工作流
3. **评分系统**：导入后评分 5 星 + 评论 → 模板页显示更新的平均分
4. **版本更新**：作者发布 v1.1 → 用户收到通知 → 查看变更日志 → 选择更新
5. **私有模板**：发布私有模板 → 其他用户不可见 → 添加到 allowed_teams → 指定用户可见
6. **搜索过滤**：按标签 "code-review" 搜索 → 仅显示相关模板；按评分排序

## 8. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| 文件系统存储模板 | 不利于搜索、权限控制、统计；数据库更适合结构化查询 |
| 模板跨用户直接共享（无库） | 缺少发现机制；评分/下载统计无法实现 |
| 自动更新已导入的工作流 | 破坏性；用户可能已自定义，强制更新会丢失改动 |
| 模板付费机制 | v1 不做商业化；保持开源社区性质 |
| 无版本管理（仅最新版） | 用户无法回退；作者无法追踪变更历史 |

## 9. 未来扩展方向

1. **官方认证**：
   - AstrBot 官方团队审核并认证高质量模板
   - 显示"官方推荐"徽标

2. **高级搜索**：
   - 按节点数、成员数筛选
   - "类似模板"推荐（基于图结构相似度）

3. **模板集合**：
   - 用户创建"收藏夹"保存喜欢的模板
   - 创建"模板包"（多个相关模板打包）

4. **数据分析**：
   - 模板使用热力图
   - 最受欢迎的标签组合
   - 用户留存率（导入后持续使用的比例）

5. **社区互动**：
   - 模板讨论区（Q&A）
   - Fork 机制（在他人模板基础上改进并发布新版本）

## 10. 迁移与兼容性

- **新表独立**：3 张新表不影响现有数据
- **工作流表扩展**：`template_source` 字段可为空（现有工作流 = null）
- **渐进式上线**：Phase 4 的导出/导入仍可用；模板库作为增强方式
- **权限隔离**：私有模板不会泄露给未授权用户
- **数据清理**：已删除模板的 `TemplateUsage` 记录保留（审计需要）

---

## 附录 A：模板分类体系

| 分类 | 描述 | 示例标签 |
|---|---|---|
| `development` | 软件开发 | code-review, ci-cd, testing, deployment |
| `data` | 数据处理 | etl, preprocessing, analysis, visualization |
| `content` | 内容生成 | blog, documentation, social-media, translation |
| `automation` | 自动化任务 | workflow, monitoring, alert, report |
| `general` | 通用模板 | template, starter, example |

## 附录 B：评分指南（给用户的建议）

- ⭐ 1 星：无法使用或严重问题
- ⭐⭐ 2 星：勉强可用但问题较多
- ⭐⭐⭐ 3 星：基本可用，符合描述
- ⭐⭐⭐⭐ 4 星：好用，推荐使用
- ⭐⭐⭐⭐⭐ 5 星：非常出色，节省大量时间

## 附录 C：模板发布最佳实践

1. **详细描述**：说明适用场景、输入要求、预期输出
2. **语义化角色名**：用 `@reviewer` 而非 `@role_1`
3. **版本号规范**：遵循语义化版本（major.minor.patch）
4. **变更日志**：每个版本清晰记录改动
5. **标签精准**：选择最相关的 3-5 个标签
6. **测试充分**：发布前在自己团队验证可用性
