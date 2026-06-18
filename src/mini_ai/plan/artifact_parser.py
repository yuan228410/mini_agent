from __future__ import annotations

import json
import re

from ..utils import now_ts
from .schema import PlanArtifact, PlanDecision, PlanDecisionOption, PlanOption, PlanStep, new_plan_id

_PLAN_BLOCK_RE = re.compile(r"```plan-artifact\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def _loads_json_object(text: str) -> dict | None:
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        raw = json.loads(stripped)
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _find_trailing_plan_json(text: str) -> tuple[dict, int, int] | None:
    decoder = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"\{", text)][::-1]:
        try:
            raw, end_rel = decoder.raw_decode(text[start:])
        except Exception:
            continue
        end = start + end_rel
        if text[end:].strip():
            continue
        if isinstance(raw, dict) and _looks_like_plan_artifact(raw):
            return raw, start, end
    return None


def strip_artifact_blocks(text: str) -> str:
    text = _PLAN_BLOCK_RE.sub("", text)
    text = _JSON_BLOCK_RE.sub("", text)
    trailing = _find_trailing_plan_json(text)
    if trailing:
        _, start, end = trailing
        text = text[:start] + text[end:]
    elif _looks_like_plan_artifact(_loads_json_object(text)):
        return ""
    return text.strip()


def _looks_like_plan_artifact(raw: dict | None) -> bool:
    if not raw:
        return False
    return bool({"goal", "summary", "steps", "options"} & set(raw.keys()))


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _option(data: dict, idx: int) -> PlanOption:
    risk = str(data.get("risk_level") or "medium")
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    return PlanOption(
        id=str(data.get("id") or f"option-{idx + 1}"),
        title=str(data.get("title") or f"方案 {idx + 1}"),
        summary=str(data.get("summary") or ""),
        pros=_as_str_list(data.get("pros")),
        cons=_as_str_list(data.get("cons")),
        risk_level=risk,  # type: ignore[arg-type]
        estimated_effort=str(data.get("estimated_effort") or ""),
        recommended=bool(data.get("recommended")),
    )


def _decision_option(data: dict, idx: int) -> PlanDecisionOption:
    return PlanDecisionOption(
        id=str(data.get("id") or f"choice-{idx + 1}"),
        title=str(data.get("title") or f"选项 {idx + 1}"),
        summary=str(data.get("summary") or ""),
        recommended=bool(data.get("recommended")),
    )


def _decision(data: dict, idx: int) -> PlanDecision:
    return PlanDecision(
        id=str(data.get("id") or f"decision-{idx + 1}"),
        title=str(data.get("title") or f"决策 {idx + 1}"),
        description=str(data.get("description") or ""),
        allow_multiple=bool(data.get("allow_multiple")),
        options=[_decision_option(o, i) for i, o in enumerate(data.get("options") or []) if isinstance(o, dict)],
        selected_option_ids=_as_str_list(data.get("selected_option_ids")),
        custom_value=str(data.get("custom_value") or ""),
    )


def _step(data: dict, idx: int) -> PlanStep:
    return PlanStep(
        id=str(data.get("id") or f"step-{idx + 1}"),
        title=str(data.get("title") or f"步骤 {idx + 1}"),
        description=str(data.get("description") or ""),
        files=_as_str_list(data.get("files")),
        validation=_as_str_list(data.get("validation")),
        depends_on=_as_str_list(data.get("depends_on")),
        decisions=[_decision(d, i) for i, d in enumerate(data.get("decisions") or []) if isinstance(d, dict)],
    )


def parse_plan_artifact(text: str, *, previous: dict | None = None, goal: str = "") -> PlanArtifact | None:
    match = _PLAN_BLOCK_RE.search(text) or _JSON_BLOCK_RE.search(text)
    if match:
        try:
            raw = json.loads(match.group(1))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
    else:
        trailing = _find_trailing_plan_json(text)
        raw = trailing[0] if trailing else _loads_json_object(text)
        if not _looks_like_plan_artifact(raw):
            return None

    previous = previous or {}
    plan_id = str(previous.get("plan_id") or raw.get("plan_id") or new_plan_id())
    revision = int(previous.get("revision") or 0) + 1
    created_at = str(previous.get("created_at") or now_ts())
    selected = raw.get("selected_option_id", previous.get("selected_option_id"))
    options = [_option(o, i) for i, o in enumerate(raw.get("options") or []) if isinstance(o, dict)]
    steps = [_step(s, i) for i, s in enumerate(raw.get("steps") or []) if isinstance(s, dict)]

    open_questions = _as_str_list(raw.get("open_questions"))
    has_unselected_top_option = len(options) > 1 and not selected
    has_unselected_decision = any(
        decision.options and not decision.selected_option_ids and not decision.custom_value.strip()
        for step in steps
        for decision in step.decisions
    )
    status = "awaiting_user" if (open_questions or has_unselected_top_option or has_unselected_decision) else "awaiting_approval"

    return PlanArtifact(
        plan_id=plan_id,
        revision=revision,
        status=status,  # type: ignore[arg-type]
        goal=str(raw.get("goal") or previous.get("goal") or goal),
        summary=str(raw.get("summary") or ""),
        assumptions=_as_str_list(raw.get("assumptions")),
        open_questions=open_questions,
        options=options,
        selected_option_id=str(selected) if selected else None,
        steps=steps,
        risks=_as_str_list(raw.get("risks")),
        validation_strategy=_as_str_list(raw.get("validation_strategy")),
        created_at=created_at,
        updated_at=now_ts(),
    )
