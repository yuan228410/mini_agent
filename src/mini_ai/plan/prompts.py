from __future__ import annotations

import json

from .schema import PlanArtifact


PLAN_MODE_INSTRUCTION = """你现在处于计划模式。目标是和用户充分讨论方案，而不是执行修改。

规则：
- 不要修改文件、运行有副作用的命令、提交代码或执行计划。
- 可以使用只读工具理解代码和上下文。
- 需求不明确时先提问；存在明显取舍时给出 2-3 个选项并标注推荐项。
- 如果整体方案需要选择，写入顶层 options；如果某个步骤内部有多个可选实现，写入该 step 的 decisions。decisions 支持单选/多选，用户也可以输入其他想法。
- 当用户选择/切换顶层方案时，必须把 selected_option_id 设置为用户选择，并基于该方案重写 summary、steps、risks、validation_strategy；同时重新检查新方案每个步骤是否存在方案内可选实现/参数/取舍，如有必须写入对应 step.decisions（单选/多选、推荐项、其他想法入口），不要只改 selected_option_id。
- 每轮都要把计划向可审批状态推进：澄清问题、方案选项、步骤、步骤内决策点、风险、验证方式。
- 目标未完成前保持目标导向自动推进：不要在中间询问“是否继续/是否进行下一步”，也不要输出“我是否可以继续修复/完成/执行剩余步骤”。只有缺少必要信息、存在必须由用户选择的方案/决策、等待最终批准执行、或用户手动取消时才停下来等用户。
- 当计划足够明确且所有顶层方案/步骤决策都已选择或不需要选择时，明确提示“等待用户确认后再执行”。
- 如果顶层 options 有多个候选且用户尚未选择，不要进入 awaiting_approval；保持 planning/awaiting_user，让 UI 先引导用户选择整体方案。
- 如果 step.decisions 中仍有未选择项（selected_option_ids 为空且 custom_value 为空），不要进入 awaiting_approval；保持 planning/awaiting_user，让 UI 按步骤引导用户选择。
- 面向用户的正文只写讨论、建议、澄清问题和确认提示；不要把 JSON 原文展示给用户。
- 回复末尾必须包含一个 JSON 计划产物 fenced block（仅供系统解析和 UI 渲染），格式如下：

```plan-artifact
{
  "goal": "用户目标",
  "summary": "计划摘要",
  "assumptions": [],
  "open_questions": [],
  "options": [
    {
      "id": "option-a",
      "title": "方案名",
      "summary": "方案说明",
      "pros": [],
      "cons": [],
      "risk_level": "low|medium|high",
      "estimated_effort": "",
      "recommended": true
    }
  ],
  "selected_option_id": null,
  "steps": [
    {
      "id": "step-1",
      "title": "步骤名",
      "description": "步骤说明",
      "files": [],
      "validation": [],
      "depends_on": [],
      "decisions": [
        {
          "id": "decision-1",
          "title": "该步骤需要用户选择的问题",
          "description": "为什么这里需要确认",
          "allow_multiple": false,
          "options": [
            {"id": "choice-a", "title": "选项名", "summary": "选项说明", "recommended": true}
          ],
          "selected_option_ids": [],
          "custom_value": ""
        }
      ]
    }
  ],
  "risks": [],
  "validation_strategy": []
}
```
"""


def build_plan_user_message(user_text: str, current_plan: dict | None = None, selected_option_id: str | None = None) -> str:
    parts = [PLAN_MODE_INSTRUCTION]
    if current_plan:
        parts.append("当前计划产物：\n```json\n" + json.dumps(current_plan, ensure_ascii=False, indent=2) + "\n```")
    if selected_option_id:
        parts.append(f"用户当前选择的方案：{selected_option_id}")
    parts.append("用户本轮输入：\n" + user_text)
    return "\n\n".join(parts)


def build_execution_instruction(plan: PlanArtifact | dict) -> str:
    if isinstance(plan, PlanArtifact):
        payload = plan.to_dict()
    else:
        payload = plan
    return """执行已批准的计划。

要求：
- 严格按已批准的 plan_id/revision 和 selected_option 执行。
- 执行开始时已有 TODO 列表按计划 steps 初始化；每完成/开始一个步骤，都要调用 update_todos 更新状态，让用户能在任务面板看到进度。
- 目标未完成前保持目标导向连续执行：不要在步骤之间询问“是否继续/是否进行下一步”，也不要输出“我是否可以继续修复/完成/执行剩余步骤”；按已批准计划自动推进，直到目标完成、遇到阻塞/越权/必须用户决策，或用户手动取消。
- 如果发现必须改变范围或方案，停止执行并请求用户重新修订计划，不要静默扩大范围。
- 完成后按计划中的 validation_strategy 做验证，不能验证的项目要说明原因。

已批准计划：
```json
%s
```
""" % json.dumps(payload, ensure_ascii=False, indent=2)
