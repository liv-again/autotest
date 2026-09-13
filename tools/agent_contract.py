"""Shared contracts used by Agent planning and runtime recovery.

The planner and the runtime recovery path are different workflows, but they
must agree on the same low-level action vocabulary. Keeping the enums and the
recovery output instructions here prevents the external Agent bridge from
drifting away from the executor validator.
"""

from __future__ import annotations


# These are deliberately low-level primitives. There is no free-form shell
# action and no action that asks the executor to reinterpret an Excel row.
ACTION_TYPES = {
    "tap_text",
    "tap_id",
    "tap_xy",
    "tap_bbox",
    "type_text",
    "key",
    "swipe",
    "rotate",
    "wait",
    "observe",
    "assert_text",
    "assert_id",
}
KEY_NAMES = {"BACK", "HOME", "ENTER", "DEL", "TAB", "ESC", "MENU"}
ORIENTATIONS = {"portrait", "landscape"}


RECOVERY_SCHEMA_VERSION = "1.0"
RECOVERY_PLAN_TYPE = "agent_runtime_recovery"
RECOVERY_DECISIONS = {
    "retry_current_action",
    "restart_case",
    "blocked",
}
RECOVERY_RESET_MODES = {"none", "module", "cold_start"}
RECOVERY_REPLAY_SAFETY = {"not_applicable", "safe", "uncertain"}
MAX_RECOVERY_ACTIONS = 16
MAX_RECOVERY_REASON_LENGTH = 4000


def recovery_contract_instructions() -> str:
    """Return the exact output contract embedded in recovery prompts."""

    action_types = ", ".join(sorted(ACTION_TYPES))
    return f"""恢复输出必须严格符合以下契约：
- schema_version 只能是字符串 "{RECOVERY_SCHEMA_VERSION}"。
- plan_type 只能是字符串 "{RECOVERY_PLAN_TYPE}"。
- agent 必须是对象，包含非空的 name、model、prompt_version；name/model 必须对应当前实际使用的 Agent 配置。
- decision 只能是 "retry_current_action"、"restart_case" 或 "blocked"；不能使用 recover、retry、continue 等别名。
- reset 只能是 "none"、"module" 或 "cold_start"。
- replay_safety 只能是 "not_applicable"、"safe" 或 "uncertain"。
- actions 必须是数组，最多 {MAX_RECOVERY_ACTIONS} 个动作；动作类型只能是：{action_types}。
- 如果 decision=blocked，actions 必须为空数组。
- 如果 decision 不是 blocked，replay_safety 必须是 safe。
- 如果无法确认重试或重放安全，必须返回 decision=blocked 且 actions=[]。
- 这是当前用例接管会话的一轮响应；如果请求包含 prior_attempts，必须结合此前轮次判断，不能因为上一轮恢复成功就假定整条用例已完成。

动作字段必须使用下面的结构，不要把坐标写成一整个字符串或自然语言：
- tap_id: {{"type":"tap_id","id":"控件 resource-id"}}
- tap_text: {{"type":"tap_text","text":"可见文字"}}
- tap_xy: {{"type":"tap_xy","x":540,"y":420}}
- type_text: {{"type":"type_text","text":"要输入的内容"}}
- key: {{"type":"key","key":"DEL"}}
- swipe: {{"type":"swipe","x1":540,"y1":1900,"x2":540,"y2":700,"duration_ms":350}}
- rotate: {{"type":"rotate","orientation":"portrait"}}
- wait: {{"type":"wait","seconds":3}}
- assert_id: {{"type":"assert_id","id":"目标 resource-id"}}
- assert_text: {{"type":"assert_text","text":"目标文字"}}

必须只输出一个 JSON 对象，不要输出 Markdown、代码围栏、解释文字、shell 命令或 ADB 命令。"""
