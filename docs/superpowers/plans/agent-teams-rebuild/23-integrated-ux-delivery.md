# [Agent Teams][AT23] 联验完整协作流程并交付稳定的响应式界面

发布状态：已批准并发布至 [#148](https://github.com/elecvoid243/AstrBot_for_spzx/issues/148)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT12 · #137](https://github.com/elecvoid243/AstrBot_for_spzx/issues/137)、[AT13 · #138](https://github.com/elecvoid243/AstrBot_for_spzx/issues/138)、[AT15 · #140](https://github.com/elecvoid243/AstrBot_for_spzx/issues/140)、[AT19 · #144](https://github.com/elecvoid243/AstrBot_for_spzx/issues/144)、[AT21 · #146](https://github.com/elecvoid243/AstrBot_for_spzx/issues/146)、[AT22 · #147](https://github.com/elecvoid243/AstrBot_for_spzx/issues/147)  
对应规范用户故事：15、25、31、37、57、58、60

## 构建内容

用户在正式 Dashboard 从设计到运行、人工返工、恢复及资产整理完成整条流程，桌面与窄屏均有清晰、稳定、美观的使用体验。

## 验收标准

- [x] 真实后端演示直接配置／角色复用、草稿版本、试运行、并行和子调用、动态提问、精确产物审批及有限返工的完整闭环。
- [x] 桌面默认任务树／过程／待办与产物三栏，执行图可切换；窄屏可切换所需面板且能完成同等核心操作。
- [x] 所有者与受限查看者看到符合权限的过程、上下文和产物；暂停、取消、重启与两端回答联验通过。
- [x] 后台轮询／事件更新、空态与慢请求不造成页签闪回、整页重挂、选择重置或正在填写答案丢失。
- [x] 保持 AstrBot 的视觉基调、清楚的信息层级和间距，检查文本可读性、焦点、键盘操作、错误状态和弹窗；不将原型 A／B／C 做成额外产品设置。
- [x] 中英文文档及真实截图与实际导航一致，提供旧入口到新入口映射，按仓库规则记录必要导航变更。
- [x] 新格式集成及各票行为测试已通过；本票补端到端与布局回归，不替其他票补本应随实现完成的单项测试。
- [x] 完成相关后端检查、前端类型／构建及关键浏览器场景，记录受支持平台上的实际验证范围，不用静态原型截图替代运行证据。
- [x] 最终交付不包含旧数据迁移、普通会话协作工具、通用自动化节点、单版本删除或隐藏模型审批。

## 验收记录

2026-11-03 已通过协调者验收。真实 Dashboard、隔离数据库与受控模型完成流程联验；三种试运行、人工返工、暂停保存答案、恢复与访问隔离均已核对。全组件组合 227 项通过，历史筛选补充回归 5 项通过，最终类型/构建和 Ruff 通过。实际验证范围为 Windows x64、桌面及 768px/390px 窄屏；其他操作系统未做实机验证。详细证据及限制见 README。

## 测试决策

以真实 Dashboard、临时数据库、可控流式模型／工具和平台适配器进行端到端验收；覆盖正常闭环、真实进程恢复、窄屏与键盘及页面稳定性。

## 被以下阻塞

- [AT12 · #137](https://github.com/elecvoid243/AstrBot_for_spzx/issues/137) — 按模型调用查看真实上下文及注入来源
- [AT13 · #138](https://github.com/elecvoid243/AstrBot_for_spzx/issues/138) — 切换多成员过程并在断线后连续回看
- [AT15 · #140](https://github.com/elecvoid243/AstrBot_for_spzx/issues/140) — 对草稿执行单节点、局部与完整试运行
- [AT19 · #144](https://github.com/elecvoid243/AstrBot_for_spzx/issues/144) — 按已展示推荐处理可配置的人工超时
- [AT21 · #146](https://github.com/elecvoid243/AstrBot_for_spzx/issues/146) — 在原聊天和 Dashboard 处理同一人工请求
- [AT22 · #147](https://github.com/elecvoid243/AstrBot_for_spzx/issues/147) — 删除资产并保留已有固定引用与运行历史

## 进一步说明

这是最终集成／交付票，阻塞项仅列当前终端依赖；各票自行承担对应测试、接口生成和文档，不把它变成所有测试与前端工作的堆积票。
