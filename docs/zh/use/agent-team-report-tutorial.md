# 制作并审批多文档报告

本教程用两个 Agent 并行抽取受管 Markdown 文件中的事实，按来源顺序汇总，再由固定子流程生成报告。你可以在两轮内完成审阅，从 Dashboard Chat 重开同一条根运行，并下载已批准的 Markdown 文件。

下载 <a href="/examples/multi-document-report/multi-document-report.json" download>原生工作流包</a>、<a href="/examples/multi-document-report/DOC-A.md" download>DOC-A</a> 和 <a href="/examples/multi-document-report/DOC-B.md" download>DOC-B</a>。

## 配置模型与准备来源

1. 在 **模型提供商** 添加 OpenAI 兼容聊天模型，启用 **工具调用** 模态。使用本地模型时，填写本地服务器的 `/v1` 地址、服务器提供的模型名和所需密钥。可以设为工作流默认模型，也可以在导入时映射到本机模型。
2. 本例使用内置的读取文档节点及 `store_task_file`、`submit_task_result`，无需安装插件。如果所用工作流引用插件组件，请由管理员在 **插件** 安装并启用插件，再核对画布显示的组件版本。资产包携带依赖引用。参见[已安装插件组件](./agent-team-workflows.md#已安装插件组件)。
3. 打开 **更多功能 → Agent 团队 → 资产 → 工作流 → 新建工作流**，创建只有一个 Agent 的「准备报告来源」工作流。选择支持工具调用的模型，启用**使用结构化端口**。声明必填字符串输入 `material`，连到 Agent 的同名端口。将 Agent 设为结果任务，声明必填输出字段 `text`（字符串）和 `files`（每项有必填字符串 `file_id` 的数组）。
4. 任务提示填入「用 `store_task_file` 原样保存提供的 Markdown。文件名为文档标识加 .md。再用 `submit_task_result` 提交简短摘要和仅有一项的 files 数组，将该项的 file_id 设为工具返回的 ID。Markdown：{material}」。保存版本并启动两次，每次向**输入值（JSON）**粘贴下方一个对象。JSON 中的转义换行会保留标题和段落。

   ```json
   {"material":"# DOC-A\n\n## Survey results\n\nThe survey reached 37 households.\n"}
   ```

   ```json
   {"material":"# DOC-B\n\n## Planning target\n\nThe target is 40 households.\n"}
   ```

5. 打开两个已完成的运行，预览各自的附件并核对标题和户数。记下文件名和精确产物版本，供报告的受管文件选择器使用。

## 导入、试运行与发布

1. 在 **资产 → 工作流** 选择 **导入资产包**，选中下载的 JSON 后点击 **校验并预览**。逐项匹配本机执行配置档案和模型，再确认安装。包内附带固定依赖 **Write the sourced report**。
2. 打开 **Multi-document field report**。两个 **Read DOC** 节点各自连接一个 **Extract DOC** Agent。结构化输出包含 `source`、`heading` 和整数 `value`。**Collect results in source order** 汇合两条路径并把数组交给固定子流程。审批退回时，反馈会传给下一轮子流程。
3. 先选择**固定版本 → 恢复为草稿**。选中 **Read DOC-A**，打开**试运行草稿 → 仅选中节点**，指定 DOC-A 的精确版本及附件，再到工作台核对解析文本中的标题和户数。选择**完整流程**时，分别为两个输入指定精确附件。全图试运行也会生成自己的审批待办。在该次试运行的工作台批准报告，或完成退回及返工后，再核对最终结果。
4. 点击 **保存版本**发布已核对的草稿。回到 **资产 → 工作流 → 启动运行**，核对弹窗显示的固定版本，再选择同样的两个精确附件。

![Dashboard 画布中的两条来源分支和固定报告子流程](/images/agent-team-report-canvas-zh.png)

## 审批、恢复与导出

1. 在 Bot 运行工作台展开任务，检查两个抽取结果都进入 **Collect results in source order**。到 **待办与产物**预览首轮报告，填写 `Add a Source index to the attachment with headings for both documents.`，要求在附件加入带两个标题的来源索引，然后退回。
2. 在工作台选择 **在 Chat 中打开**。看到第二轮待办后，记下产物 ID 和版本。在隔离实例上，趁该待办还在等待时停止并重启 AstrBot。按需重新登录，从 Chat 侧栏重开原运行，核对待办和精确报告引用。
3. 预览第二轮报告及附件。核对 DOC-A、DOC-B 的标题、户数和新增 **Source index**。批准本轮，选择 **下载文件 · report.md** 保存 Markdown 附件。**导出已批准版本** 保存该批准产物的 JSON。通过 **打开运行工作台** 可以查看同一运行和决定。窄屏通过 **任务**、**执行过程**、**待办与产物** 切换面板。
4. 分享工作流定义时，使用 **资产 → 工作流 → 导出资产 JSON**。报告文件从获批运行下载。在已完成的 Chat 运行选择 **再次启动**，新运行沿用原固定版本及输入。

![重开的 Chat 运行会话显示获批版本和报告下载入口](/images/agent-team-report-chat-approved-zh.png)

## 排查引用核对失败的报告

下方截图使用一个受控的报告扩展样例。历史根运行固定使用报告及引用核对工作流的 **v3**。第一轮核对初稿并获得审批。第二轮沿用初稿，循环反馈传入已被替代但仍能读取的 PDF，冻结的引用核对节点拒绝了这个精确引用。前文的下载包涵盖报告准备与审批。以下投递步骤适用于已配置外部连接器和归档路径的工作流。

1. 从 **Bot → Agent 团队 → 运行 → 待处理** 打开失败的根运行，也可以在 **Dashboard Chat** 打开它的运行会话。展开执行图，在父图切到第 2 轮，选中 **Review report citations · v3**，点击 **调试失败的子调用**。确认预览显示第 2 轮父调用、子工作流版本和失败的 **Check citations** 尝试。
2. 在子作用域选择进入 **Check citations** 的 `pdf` 数据线，核对实际消费的文件 ID、产物版本和尝试。切到第 1 轮的子作用域可以对照成功时的读取。通往 **Export an optional appendix** 的数据线显示 **已跳过 · 未读取**。展开 **查看上游产物**，可以单独查看该作用域已经生成的初稿。
3. 保持 **原运行** 和 **仅执行选中的节点**。在替换输入处选择 `pdf`，填写有权读取的产物 ID 和精确版本，再选择 `citations.pdf`。确认 `draft` 显示 **沿用**、`pdf` 显示 **替换**、版本仍为 v3。启动分支，对照历史失败与本次成功结果。
4. 刷新后重开对照页。**打开父调用** 回到第 2 轮，**打开来源尝试** 回到该轮子作用域的尝试。验证已编辑的工作流时，重新打开预览并选择 **当前草稿**，确认显示的修订后启动单节点分支。此样例调整了草稿的输出契约，显式接纳两个已知引用。

![已跳过的可选附录数据线与上游产物](/images/report-debug-skipped-zh.png)

![单节点分支预览中显式选择当前草稿的修订](/images/report-debug-preview-zh.png)

### 验证审批和投递

1. 回到原失败尝试，选择 **从此节点运行下游**。核对冻结的执行范围和输入，选择修正后的 PDF。启动分支，打开 **运行工作台 → 待办与产物**，检查并批准分支新生成的精确产物版本。首轮审批仍与原来的产物对应。
2. 打开 **外部写入预览**，确认目标和参数。要验证连接器实际调用，点击一次 **执行真实写入**，重开分支后查看执行 ID 和回执。本样例的隔离连接器账本新增一笔投递，归档记录读到连接器返回的计数 `78`。
3. 要演练下游，另建一个下游分支并批准它的新产物。在投递预览中选择 **用测试值继续**。本例填入 `{"record":{"count":192}}`。打开归档记录的输入数据线，确认实际读取 `192` 并带有模拟来源。后续归档写入在自己的预览处暂停，账本没有因为演练新增记录。

![投递预览中的模拟回执编辑框](/images/report-debug-delivery-zh.png)

更多执行范围、恢复操作和自主团队成员尝试的只读检查，见[运行工作台指南](./agent-team-workflows.md#调试历史节点或其下游路径)。已构建 Dashboard 并安装 Playwright Chromium 的开发环境，可以用 `python -m tests.agent_teams.report_debug_browser --evidence data/report-debug-local` 复现受控联验。请选择尚未使用的证据目录。

## 旧入口对应

| 旧入口 | 当前入口 |
| --- | --- |
| 工作流画布弹窗 | **资产 → 工作流 → 编辑**，打开独立编辑页 |
| 独立运行／运行监控 | **运行 → 进行中／待处理／历史** |
| 仅从运行详情审批 | **运行 → 待处理**，或在 **Chat → 工作流运行 → 待办与产物**处理 |
| Chat 标题下取消运行 | **待办与产物 → 整个运行控制** |
| 消息平台旧运行链接 | 在原平台会话发送 `/asset status <运行 ID>`，取回受限链接 |

版本、权限和恢复的更多说明见 [Agent 团队资产与运行工作台](./agent-team-workflows.md)。
