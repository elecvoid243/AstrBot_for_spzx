# Workflow calls to autonomous teams

- Workflow Assets → New Canvas Workflow now authors fixed-version autonomous team calls with declared inputs and output contracts. The existing asset and run entry points remain in place.
- Child scopes use independent member conversations, the shared task executor, root concurrency and whole-run controls. Parent waits release capacity, including at a root limit of one.
- Scope creation, parent waiting and exact result handoff persist atomically. Recovery retains original scopes and Attempts; child task retries remain explicit new Attempts. Frozen child versions and resource settings survive later edits.
- Independent Runs → View Run shows real child tasks, scope relationships, local waiting reasons, immutable outputs and saved model/step counts. The canvas and run detail support collapsed child definitions and execution graphs.
- Parent/child ancestry does not grant message or artifact access across scopes. Declared exact inputs and outputs define the handoff.
- Parent revisions retain already returned files while rejecting other stored files. Team version selection ignores responses from superseded choices or expired editor contexts.
- Coordinator calls to saved workflows and detailed budget reporting remain separate tasks.

# 工作流调用自主团队

- **工作流资产 → 新建画布工作流** 可配置固定版本自主团队节点、声明输入与输出契约；现有资产和运行入口保持原路径。
- 子作用域使用独立成员会话、公共执行器、根并发及全局控制。父等待释放容量，根并发为 1 也能推进。
- **独立运行 → 查看运行** 展示实际子任务、父子关联、等待原因、版本产物和已保存调用／步骤计数；支持折叠子定义和实际子图。
- 子创建、父等待与精确输出交接保持持久一致；恢复沿用原作用域和 Attempt，重试子任务显式创建新 Attempt。父子关联不会扩大消息或产物可见范围。
- 中英文操作说明和正式页面截图同步更新。反向调用与预算精细汇总由后续任务交付。
