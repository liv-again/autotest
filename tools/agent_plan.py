"""Contract and context helpers for Agent-authored mobile execution plans.

The Excel reader is intentionally kept separate from the action planner.  It
normalises rows and profile facts, but it never translates natural-language
Excel instructions into taps.  An external Agent must author an agent_action_plan
and the runtime validates that plan before it touches the device.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.agent_contract import ACTION_TYPES, KEY_NAMES, ORIENTATIONS
from tools.module_planner import build_module_plan


PLAN_SCHEMA_VERSION = "1.0"
PLAN_TYPE = "agent_action_plan"
PLANNER_MODEL_CONFIG_PATH = Path(__file__).resolve().with_name("agent_model_config.yaml")
DEFAULT_PLANNER_MODEL = "gpt-5.6-luna"

class AgentPlanError(ValueError):
    """Raised when an Agent plan is missing, stale, or unsafe to execute."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_planner_model_defaults(
    *,
    config_path: str | Path | None = None,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the planning model default exposed to the selected Agent.

    Planning is performed by the selected external Agent, so this helper does
    not invoke an LLM or force a provider.  It makes the project default and
    the exact environment override visible in the context used to author the
    action plan.  The generated plan must still record the model actually
    used by the Agent.
    """

    target = Path(config_path or PLANNER_MODEL_CONFIG_PATH).expanduser().resolve()
    config: Any = {}
    if target.is_file():
        try:
            config = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise AgentPlanError(f"无法读取规划模型配置 {target}: {exc}") from exc
    if not isinstance(config, Mapping):
        raise AgentPlanError(f"规划模型配置必须是 YAML 对象: {target}")

    planner = config.get("planner")
    if planner is None:
        planner = {}
    if not isinstance(planner, Mapping):
        raise AgentPlanError(f"规划模型配置的 planner 必须是对象: {target}")

    environment = environment if environment is not None else os.environ
    model_env = _text(planner.get("model_env")) or "SIXGILL_PLANNER_MODEL"
    configured_model = _text(planner.get("model")) or DEFAULT_PLANNER_MODEL
    override_model = _text(environment.get(model_env))
    model = override_model or configured_model
    source = f"environment:{model_env}" if override_model else f"file:{target.name}"
    return {
        "model": model,
        "source": source,
        "config_path": str(target),
        "environment_variable": model_env,
        "exact_wire_id_required": True,
    }


def _case_key(item: Mapping[str, Any]) -> tuple[str, int, str]:
    case_id = _text(item.get("case_id"))
    sheet = _text(item.get("sheet"))
    row = item.get("row")
    try:
        row_number = int(row)
    except (TypeError, ValueError) as exc:
        raise AgentPlanError(f"Agent 计划用例缺少合法 row: {item!r}") from exc
    if not case_id or not sheet:
        raise AgentPlanError(f"Agent 计划用例缺少 case_id 或 sheet: {item!r}")
    return case_id, row_number, sheet


def sha256_file(path: str | Path) -> str:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_nonempty_text(value: Any, label: str) -> str:
    result = _text(value)
    if not result:
        raise AgentPlanError(f"{label} 不能为空")
    return result


def _number(spec: Mapping[str, Any], key: str, *, integer: bool = False) -> int | float:
    value = spec.get(key)
    if isinstance(value, bool):
        raise AgentPlanError(f"动作 {spec.get('type')!r} 的 {key} 不能是布尔值")
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise AgentPlanError(f"动作 {spec.get('type')!r} 的 {key} 必须是数字") from exc
    if not math.isfinite(parsed):
        raise AgentPlanError(f"动作 {spec.get('type')!r} 的 {key} 必须是有限数字")
    return parsed


def validate_action(action: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise AgentPlanError(f"{location} 必须是对象")
    result = dict(action)
    action_type = _require_nonempty_text(action.get("type"), f"{location}.type")
    if action_type not in ACTION_TYPES:
        allowed = ", ".join(sorted(ACTION_TYPES))
        raise AgentPlanError(f"{location}.type={action_type!r} 不受支持；允许: {allowed}")
    result["type"] = action_type

    if action_type in {"tap_text", "assert_text"}:
        result["text"] = _require_nonempty_text(action.get("text"), f"{location}.text")
    elif action_type in {"tap_id", "assert_id"}:
        result["id"] = _require_nonempty_text(action.get("id"), f"{location}.id")
    elif action_type == "tap_xy":
        result["x"] = _number(action, "x", integer=True)
        result["y"] = _number(action, "y", integer=True)
        if result["x"] < 0 or result["y"] < 0:
            raise AgentPlanError(f"{location} 坐标不能为负数")
    elif action_type == "tap_bbox":
        for key in ("x1", "y1", "x2", "y2"):
            result[key] = _number(action, key, integer=True)
        if result["x2"] <= result["x1"] or result["y2"] <= result["y1"]:
            raise AgentPlanError(f"{location} 的 bbox 必须满足 x2>x1 且 y2>y1")
    elif action_type == "type_text":
        result["text"] = _require_nonempty_text(action.get("text"), f"{location}.text")
    elif action_type == "key":
        key = _require_nonempty_text(action.get("key"), f"{location}.key").upper()
        if key not in KEY_NAMES:
            raise AgentPlanError(f"{location}.key={key!r} 不受支持")
        result["key"] = key
    elif action_type == "swipe":
        for key in ("x1", "y1", "x2", "y2"):
            result[key] = _number(action, key, integer=True)
        duration = action.get("duration_ms", 350)
        try:
            duration_number = int(duration)
        except (TypeError, ValueError) as exc:
            raise AgentPlanError(f"{location}.duration_ms 必须是整数") from exc
        if not 1 <= duration_number <= 10_000:
            raise AgentPlanError(f"{location}.duration_ms 必须在 1..10000")
        result["duration_ms"] = duration_number
    elif action_type == "rotate":
        orientation = _require_nonempty_text(
            action.get("orientation"), f"{location}.orientation"
        ).casefold()
        aliases = {"竖屏": "portrait", "竖放": "portrait", "横屏": "landscape", "横放": "landscape"}
        orientation = aliases.get(orientation, orientation)
        if orientation not in ORIENTATIONS:
            raise AgentPlanError(f"{location}.orientation={orientation!r} 不受支持")
        result["orientation"] = orientation
    elif action_type == "wait":
        seconds = _number(action, "seconds")
        if not 0 <= seconds <= 10:
            raise AgentPlanError(f"{location}.seconds 必须在 0..10")
        result["seconds"] = seconds
    elif action_type == "observe":
        result["target"] = _text(action.get("target")) or "当前页面"

    if "after" in action:
        result["after"] = validate_target_page(action["after"], location=f"{location}.after")
    return result


def validate_target_page(target: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(target, Mapping):
        raise AgentPlanError(f"{location} 必须是对象")
    result = dict(target)
    fields = {
        "all_text": list,
        "any_text": list,
        "not_text": list,
        "all_ids": list,
        "any_ids": list,
        "not_ids": list,
        "selected_text": list,
        "selected_ids": list,
    }
    for key, expected_type in fields.items():
        value = target.get(key, [])
        if not isinstance(value, expected_type):
            raise AgentPlanError(f"{location}.{key} 必须是数组")
        result[key] = [_require_nonempty_text(item, f"{location}.{key}[]") for item in value]
    if "orientation" in target and target.get("orientation") not in (None, ""):
        orientation = _text(target.get("orientation")).casefold()
        aliases = {"竖屏": "portrait", "竖放": "portrait", "横屏": "landscape", "横放": "landscape"}
        orientation = aliases.get(orientation, orientation)
        if orientation not in ORIENTATIONS:
            raise AgentPlanError(f"{location}.orientation={orientation!r} 不受支持")
        result["orientation"] = orientation
    else:
        result.pop("orientation", None)
    criteria = sum(
        len(result.get(key) or [])
        for key in (
            "all_text",
            "any_text",
            "all_ids",
            "any_ids",
            "not_text",
            "not_ids",
            "selected_text",
            "selected_ids",
        )
    ) + (1 if result.get("orientation") else 0)
    if criteria == 0:
        raise AgentPlanError(f"{location} 至少要有一个可观察页面条件")
    result["description"] = _text(target.get("description")) or "Agent 计划目标页"
    return result


def validate_case_plan(item: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise AgentPlanError(f"{location} 必须是对象")
    result = dict(item)
    case_id, row, sheet = _case_key(item)
    result.update({"case_id": case_id, "row": row, "sheet": sheet})
    result["page_group_id"] = _require_nonempty_text(
        item.get("page_group_id"), f"{location}.page_group_id"
    )
    result["page_group_key"] = _require_nonempty_text(
        item.get("page_group_key"), f"{location}.page_group_key"
    )
    navigation = item.get("navigation", [])
    recovery_navigation = item.get("recovery_navigation", navigation)
    actions = item.get("actions")
    if not isinstance(navigation, list):
        raise AgentPlanError(f"{location}.navigation 必须是数组")
    if not isinstance(recovery_navigation, list):
        raise AgentPlanError(f"{location}.recovery_navigation 必须是数组")
    if not isinstance(actions, list):
        raise AgentPlanError(f"{location}.actions 必须是数组；不能让执行器重新解析 Excel 文本")
    result["navigation"] = [
        validate_action(action, location=f"{location}.navigation[{index}]")
        for index, action in enumerate(navigation)
    ]
    result["recovery_navigation"] = [
        validate_action(action, location=f"{location}.recovery_navigation[{index}]")
        for index, action in enumerate(recovery_navigation)
    ]
    result["actions"] = [
        validate_action(action, location=f"{location}.actions[{index}]")
        for index, action in enumerate(actions)
    ]
    result["target_page"] = validate_target_page(
        item.get("target_page"), location=f"{location}.target_page"
    )
    expected_observations = item.get("expected_observations", [])
    if not isinstance(expected_observations, list):
        raise AgentPlanError(f"{location}.expected_observations 必须是数组")
    result["expected_observations"] = [
        _require_nonempty_text(value, f"{location}.expected_observations[]")
        for value in expected_observations
    ]
    return result


def validate_action_plan(
    plan: Any,
    cases: Iterable[Mapping[str, Any]],
    *,
    source_path: str | Path | None = None,
    profile_path: str | Path | None = None,
) -> dict[tuple[str, int, str], dict[str, Any]]:
    if not isinstance(plan, Mapping):
        raise AgentPlanError("Agent 计划必须是 JSON 对象")
    if _text(plan.get("schema_version")) != PLAN_SCHEMA_VERSION:
        raise AgentPlanError(
            f"Agent 计划 schema_version 必须为 {PLAN_SCHEMA_VERSION}"
        )
    if _text(plan.get("plan_type")) != PLAN_TYPE:
        raise AgentPlanError(f"Agent 计划 plan_type 必须为 {PLAN_TYPE}")

    planner = plan.get("planner")
    if not isinstance(planner, Mapping):
        raise AgentPlanError("Agent 计划缺少 planner 元数据")
    agent_name = _require_nonempty_text(planner.get("agent"), "planner.agent")
    if len(agent_name) > 80 or any(ord(char) < 32 for char in agent_name):
        raise AgentPlanError("planner.agent 必须是长度不超过 80 的可打印名称")
    _require_nonempty_text(planner.get("model"), "planner.model")
    _require_nonempty_text(planner.get("prompt_version"), "planner.prompt_version")

    source = plan.get("source")
    if not isinstance(source, Mapping):
        raise AgentPlanError("Agent 计划缺少 source 元数据")
    source_hash = _require_nonempty_text(source.get("sha256"), "source.sha256").casefold()
    if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
        raise AgentPlanError("source.sha256 必须是 64 位十六进制 SHA-256")
    if source_path is not None:
        actual_hash = sha256_file(source_path)
        if actual_hash != source_hash:
            raise AgentPlanError(
                f"Agent 计划与当前 Excel 不一致: plan={source_hash}, current={actual_hash}"
            )
    if profile_path is not None:
        profile = plan.get("app_profile")
        if not isinstance(profile, Mapping):
            raise AgentPlanError("Agent 计划缺少 app_profile 元数据")
        profile_hash = _require_nonempty_text(profile.get("sha256"), "app_profile.sha256").casefold()
        if len(profile_hash) != 64 or any(char not in "0123456789abcdef" for char in profile_hash):
            raise AgentPlanError("app_profile.sha256 必须是 64 位十六进制 SHA-256")
        actual_profile_hash = sha256_file(profile_path)
        if actual_profile_hash != profile_hash:
            raise AgentPlanError(
                "Agent 计划与当前 App profile 不一致: "
                f"plan={profile_hash}, current={actual_profile_hash}"
            )

    plan_cases = plan.get("cases")
    if not isinstance(plan_cases, list):
        raise AgentPlanError("Agent 计划 cases 必须是数组")
    by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    group_sheets: dict[str, str] = {}
    for index, item in enumerate(plan_cases, start=1):
        validated = validate_case_plan(item, location=f"cases[{index}]")
        key = _case_key(validated)
        if key in by_key:
            raise AgentPlanError(f"Agent 计划存在重复用例: {key[2]}!{key[1]}")
        group_id = validated["page_group_id"]
        previous_sheet = group_sheets.get(group_id)
        if previous_sheet and previous_sheet != key[2]:
            raise AgentPlanError(
                f"Agent 页面组不能跨 Sheet 复用: {group_id} ({previous_sheet}/{key[2]})"
            )
        group_sheets[group_id] = key[2]
        by_key[key] = validated

    requested_keys: set[tuple[str, int, str]] = set()
    for index, case in enumerate(cases, start=1):
        key = _case_key(case)
        requested_keys.add(key)
        item = by_key.get(key)
        if item is None:
            raise AgentPlanError(
                f"Agent 计划缺少执行用例 {key[2]}!{key[1]} ({key[0]})"
            )
        if _text(item.get("case_id")) != _text(case.get("case_id")):
            raise AgentPlanError(f"Agent 计划 case_id 与 Excel 不一致: {key[2]}!{key[1]}")

    # An all-sheet plan can legitimately contain rows outside a retest or
    # probe queue.  They are retained for audit, but only requested rows are
    # returned to the runner and can be executed accidentally.
    return {key: by_key[key] for key in requested_keys}


def load_action_plan(
    path: str | Path,
    *,
    cases: Iterable[Mapping[str, Any]],
    source_path: str | Path | None = None,
    profile_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[tuple[str, int, str], dict[str, Any]]]:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    try:
        plan = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentPlanError(f"无法读取 Agent 计划 {target}: {exc}") from exc
    return plan, validate_action_plan(
        plan,
        cases,
        source_path=source_path,
        profile_path=profile_path,
    )


def build_agent_context(
    source: str | Path,
    *,
    sheet_names: Iterable[str] | None = None,
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create the facts that an Agent must read before authoring a plan."""

    context = build_module_plan(source, sheet_names, profile_path=profile_path)
    planner_model_defaults = load_planner_model_defaults()
    context["context_type"] = "excel_app_execution_context"
    context["planner_backend"] = "context_loader_only"
    context["planner_model_defaults"] = planner_model_defaults
    context["source_sha256"] = sha256_file(source)
    if profile_path and Path(profile_path).expanduser().is_file():
        context["profile_sha256"] = sha256_file(profile_path)
        context["app_profile"] = {
            "path": str(Path(profile_path).expanduser().resolve()),
            "sha256": context["profile_sha256"],
        }
    context["agent_prompt"] = (
        "你是当前选定的 Agent。先读取本 context 中每条 Excel 用例的实际字段和 App 画像，"
        "为每条需要执行的用例生成一个 agent_action_plan。navigation 只放公共导航，"
        "actions 只放本行实际动作；二者都必须使用允许的低层动作类型，不能把 Excel 原文"
        "直接交给执行器解析。target_page 必须给出可由 UI 树/截图观察的条件；"
        "expected_observations 用于执行后截图复核。请同时读取"
        "generic_planning_knowledge：涉及横屏列表时不要把标题作为唯一门禁；"
        "涉及滑动时比较滑动前后第一行股票名称和代码；目标元素未找到时按回到顶部后最多三屏、"
        "每屏查找一次的顺序规划；涉及排序时比较表头点击前后的前两条数据及指定字段的相反顺序。"
        "这些规则只在适用时加入计划，必须落实为可观察的前后检查，不能用动作调用成功替代结果验证。"
        f"规划元数据 planner.model 必须填写实际使用模型的精确 wire ID；当前项目默认值为"
        f" {planner_model_defaults['model']}，配置来源为 {planner_model_defaults['source']}。"
        "如果明确选择了其他 Agent/runtime，填写其实际模型 ID，不要填写界面显示别名。"
    )
    return context


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成用例解析上下文或校验结构化动作计划")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context_parser = subparsers.add_parser("context", help="读取 Excel/profile，生成 Agent 输入事实")
    context_parser.add_argument("--source", required=True)
    context_parser.add_argument("--profile")
    context_parser.add_argument("--sheet", action="append", dest="sheets")
    context_parser.add_argument("--out", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验 Agent 结构化动作计划")
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--source", required=True)
    validate_parser.add_argument("--profile")

    args = parser.parse_args(argv)
    if args.command == "context":
        document = build_agent_context(
            args.source,
            sheet_names=args.sheets,
            profile_path=args.profile,
        )
        output = Path(args.out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"out": str(output), "cases": document["expected_count"]}, ensure_ascii=False))
        return 0

    context = build_agent_context(args.source, profile_path=args.profile)
    cases = [case for module in context.get("modules", []) for case in module.get("cases", [])]
    plan, selected = load_action_plan(
        args.plan,
        cases=cases,
        source_path=args.source,
        profile_path=args.profile,
    )
    print(
        json.dumps(
            {
                "plan": str(Path(args.plan).expanduser().resolve()),
                "cases": len(plan.get("cases") or []),
                "validated_for_execution": len(selected),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
