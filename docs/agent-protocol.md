# 项目 Agent 协议

本文件是 codex-agent-protocol 在本项目的生成入口。role catalog 与角色机器权威见 `docs/agent-protocol/pack.json`；角色行为说明见 `docs/agent-protocol/roles/`。

```yaml
protocol_version: 1
pack_id: software-development
pack_version: 1.0.0
role_count: 5
available_roles:
  - role_id: product
    display_name: 若命
    summary: 明确问题、目标与用户价值，维护需求边界。
    authority: 负责问题定义、目标取舍和需求范围；不单独决定实现细节。
    formal_results: [problem_statement, requirements_scope, acceptance_goals]
  - role_id: engineering
    display_name: 听云
    summary: 负责技术实现、代码变更与实现约束。
    authority: 负责实现方案、代码和技术边界；不替代产品验收或独立质量结论。
    formal_results: [implementation, technical_decisions]
  - role_id: qa
    display_name: 观止
    summary: 验证行为是否符合要求并报告可复现问题。
    authority: 负责验证范围、测试证据和缺陷报告；不改变需求或实现。
    formal_results: [verification_report, defect_report]
  - role_id: review
    display_name: 镜花
    summary: 检查变更风险、协议一致性与结果完整性。
    authority: 负责独立审查和风险结论；不直接接管实现或需求决策。
    formal_results: [review_findings, risk_assessment]
  - role_id: ux
    display_name: 清秋
    summary: 关注使用路径、交互清晰度和用户体验约束。
    authority: 负责体验问题、交互建议和可用性边界；不替代产品范围或工程实现决策。
    formal_results: [ux_findings, interaction_recommendations]
pack_json_path: docs/agent-protocol/pack.json
roles_dir: docs/agent-protocol/roles/
bootstrap_order:
  - PROTOCOL_BOOTSTRAP
  - IDENTITY_ASSIGNMENT
  - ROLE_CONTRACT
  - TASK
```

## 使用方式（本项目约定）

- **主会话（controller）角色声明**：本项目的父会话定位为 **product / 若命**（role_id=product）：
  - authority：问题定义、目标取舍、需求范围、验收口径；
  - 主会话负责：承接用户需求、拆解任务、决定委派与验收标准、记录结果；
  - 主会话在本地直接完成普通任务（含工程实现）时，属于协议允许的"controller 本地完成"，**不产生 formal role result**（formal result 仅由 child assignment 产生）；工程实现如需 formal 归属，应委派给 engineering/听云。
- **主会话**：本地完成普通任务时无需创建 assignment（child_handle=parent 才需要）；批量/大型委派时，把任务按角色拆给子 agent，子 agent 按 `bootstrap_order` 顺序接收四段 bootstrap。
- **常见委派模式**（省父会话 token）：
  - 大批量重构/迁移 → `engineering`（听云）：改指定文件清单，返回 formal result（implementation / technical_decisions）+ changed_files + evidence + next_action；
  - 改动后验证 → `qa`（观止）：只跑测试/走查，返回 verification_report / defect_report，**过程输出不回流**；
  - 风险评审 → `review`（镜花）：返回 review_findings / risk_assessment。
- **formal result 归属**：委派 child 时由父侧在本地 assignment 记录 child_handle（从 native subagent transport 观察获得），child 不得自报 handle；无 assignment 的 controller 结果不产生 formal result。
- 本项目角色可以**叠加**（同一子 agent 先 qa 后 review，或一个子会话内按序走多个角色），但每个 formal result 必须落在对应角色的 authority 边界内。

## 当前状态

- 协议已初始化（pack.json 来自 codex-agent-protocol 的 software-development role pack，原样投影）；
- **已实际启用**：QA 走查委派（参见下方记录），formal result 由父会话从 native transport 观察 child_handle 并归属。

## 操作说明（启用协作 / 委派验证）

1. **准备**：先读 `AGENTS.md` + `docs/索引.md`；本次验证读 `docs/验证清单.md`。
2. **启动本地服务**：`./scripts/dev_server.sh`（自动加载 .env，含 AI key）。
3. **委派给 qa（观止）**：父会话用 background subagent，prompt 按四段 bootstrap：
   `PROTOCOL_BOOTSTRAP（协议版本+pack） → IDENTITY_ASSIGNMENT（role: qa / display_name: 观止 / authority: 只验证不改码） → ROLE_CONTRACT（只读约束） → TASK（指向 docs/验证清单.md，附本地环境地址/账号）`
4. **收结果**：子 agent 返回 `verification_report`（status/items+evidence/findings/residual_risk）；父会话附加 child_handle（从 transport 观察），正式记录 assignment。
5. **处置**：NEEDS_FIX → 按 defect 修复 → 复跑同一套走查（可复用该子 agent 会话 send_message）。
6. 其它委派：重构→engineering（听云）、评审→review（镜花）、需求→product（若命）、交互→ux（清秋）。

## 验证记录

- **2026-09（线上）/ 本地首轮**：qa（观止）验证奖金列/两期发薪/切月修复/薪资找平/对账/AI分析/page_state——`PASS_WITH_SCOPE`，无 defect；发现并确认 9 月残留（已清）、AI key 403（已换 deepseek-v4-flash 恢复）。
- **本地清单版（docs/验证清单.md）**：本轮执行，见 qa 返回的 verification_report。
