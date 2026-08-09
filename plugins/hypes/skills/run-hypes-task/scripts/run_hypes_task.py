#!/usr/bin/env python3
"""Stateless field loop for one explicitly activated Hypes task.

The caller carries the returned session object between invocations.  This
program accepts only bounded structured records and writes no file, database,
log, or network state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn


SCHEMA_VERSION = "0.1.0"
POLICY_ID = "hypes-field-fixed-v0.1.0"
TASK_CONTRACT_ID = "judgment-handoff-v0"
MAX_INPUT_BYTES = 1_048_576
MAX_OBSERVATIONS = 128
MAX_DELIVERIES = 64

TRIAL_CONDITIONS = ("baseline", "scope_filter", "hypes_proposal")
RESPONSIBILITIES = ("ordinary", "approve_high_impact")
INFORMATION_DEPTHS = ("minimal", "standard", "expanded")
SUPPORT_MODES = ("none", "example", "scaffold")
DIALOGUE_MOVES = ("answer", "ask", "challenge", "defer")
RESPONSIBILITY_MOVES = ("deliver", "request_confirmation", "defer")
OUTCOME_VALUES = ("yes", "no", "not_observed", "not_applicable")

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class InputError(Exception):
    code: str
    path: str

    def __str__(self) -> str:
        return f"{self.code} at {self.path}"


def _fail(code: str, path: str) -> NoReturn:
    raise InputError(code=code, path=path)


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail("EXPECTED_OBJECT", path)
    return value


def _exact_keys(
    value: Mapping[str, Any], *, required: set[str], path: str
) -> None:
    if set(value) - required:
        _fail("UNKNOWN_FIELD", path)
    if required - set(value):
        _fail("MISSING_FIELD", path)


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail("INVALID_IDENTIFIER", path)
    return value


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("INVALID_SHA256", path)
    return value


def _enum(value: Any, allowed: tuple[str, ...], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("INVALID_ENUM", path)
    return value


def _boolean(value: Any, path: str, *, constant: bool | None = None) -> bool:
    if not isinstance(value, bool):
        _fail("EXPECTED_BOOLEAN", path)
    if constant is not None and value is not constant:
        _fail("INVALID_CONSTANT", path)
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("EXPECTED_INTEGER", path)
    if not 0 <= value <= 9_007_199_254_740_991:
        _fail("INTEGER_OUT_OF_RANGE", path)
    return value


def _array(value: Any, path: str, *, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        _fail("EXPECTED_ARRAY", path)
    if len(value) > maximum:
        _fail("ARRAY_TOO_LONG", path)
    return value


def _identifier_list(
    value: Any, path: str, *, minimum: int = 0, maximum: int = 64
) -> list[str]:
    result = [
        _identifier(item, f"{path}/{index}")
        for index, item in enumerate(_array(value, path, maximum=maximum))
    ]
    if len(result) < minimum:
        _fail("ARRAY_TOO_SHORT", path)
    if len(result) != len(set(result)):
        _fail("DUPLICATE_ITEM", path)
    return result


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("NOT_CANONICALIZABLE", "$")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _scope(value: Any, path: str) -> dict[str, str]:
    item = _object(value, path)
    _exact_keys(
        item,
        required={"project_id", "task_relation", "responsibility"},
        path=path,
    )
    return {
        "project_id": _identifier(item["project_id"], f"{path}/project_id"),
        "task_relation": _identifier(
            item["task_relation"], f"{path}/task_relation"
        ),
        "responsibility": _enum(
            item["responsibility"],
            RESPONSIBILITIES,
            f"{path}/responsibility",
        ),
    }


def _scope_key(value: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        value["project_id"],
        value["task_relation"],
        value["responsibility"],
    )


def _strategy(value: Any, path: str) -> dict[str, str]:
    item = _object(value, path)
    _exact_keys(
        item,
        required={
            "information_depth",
            "support_mode",
            "dialogue_move",
            "responsibility_move",
        },
        path=path,
    )
    return {
        "information_depth": _enum(
            item["information_depth"],
            INFORMATION_DEPTHS,
            f"{path}/information_depth",
        ),
        "support_mode": _enum(
            item["support_mode"], SUPPORT_MODES, f"{path}/support_mode"
        ),
        "dialogue_move": _enum(
            item["dialogue_move"], DIALOGUE_MOVES, f"{path}/dialogue_move"
        ),
        "responsibility_move": _enum(
            item["responsibility_move"],
            RESPONSIBILITY_MOVES,
            f"{path}/responsibility_move",
        ),
    }


def _human_responsibility(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    _exact_keys(
        item,
        required={
            "release_owner",
            "agent_execution_authority",
            "decision_class",
            "required_check_ids",
        },
        path=path,
    )
    result = {
        "release_owner": _enum(
            item["release_owner"], ("human",), f"{path}/release_owner"
        ),
        "agent_execution_authority": _boolean(
            item["agent_execution_authority"],
            f"{path}/agent_execution_authority",
            constant=False,
        ),
        "decision_class": _enum(
            item["decision_class"],
            RESPONSIBILITIES,
            f"{path}/decision_class",
        ),
        "required_check_ids": _identifier_list(
            item["required_check_ids"],
            f"{path}/required_check_ids",
            minimum=1,
        ),
    }
    return result


def _baseline(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    _exact_keys(
        item,
        required={
            "baseline_id",
            "baseline_sha256",
            "required_content_ids",
            "optional_content_ids",
            "delivery_strategy",
            "human_responsibility",
        },
        path=path,
    )
    strategy = _strategy(item["delivery_strategy"], f"{path}/delivery_strategy")
    responsibility = _human_responsibility(
        item["human_responsibility"], f"{path}/human_responsibility"
    )
    if (
        responsibility["decision_class"] == "approve_high_impact"
        and strategy["responsibility_move"] != "request_confirmation"
    ):
        _fail("HIGH_IMPACT_REQUIRES_CONFIRMATION", f"{path}/delivery_strategy")
    return {
        "baseline_id": _identifier(item["baseline_id"], f"{path}/baseline_id"),
        "baseline_sha256": _sha256(
            item["baseline_sha256"], f"{path}/baseline_sha256"
        ),
        "required_content_ids": _identifier_list(
            item["required_content_ids"],
            f"{path}/required_content_ids",
            minimum=1,
        ),
        "optional_content_ids": _identifier_list(
            item["optional_content_ids"], f"{path}/optional_content_ids"
        ),
        "delivery_strategy": strategy,
        "human_responsibility": responsibility,
    }


def _delivery(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "turn_id",
        "turn_sequence",
        "relation_scope",
        "baseline_id",
        "baseline_sha256",
        "selected_source",
        "selected_strategy",
        "required_content_ids",
        "human_responsibility",
        "hypes_help_applied",
        "delivery_id",
        "delivery_receipt_sha256",
        "delivery_status",
        "attribution_window_opened",
        "caller_attested",
        "delivery_digest",
    }
    _exact_keys(item, required=required, path=path)
    result = {
        "turn_id": _identifier(item["turn_id"], f"{path}/turn_id"),
        "turn_sequence": _integer(
            item["turn_sequence"], f"{path}/turn_sequence"
        ),
        "relation_scope": _scope(item["relation_scope"], f"{path}/relation_scope"),
        "baseline_id": _identifier(item["baseline_id"], f"{path}/baseline_id"),
        "baseline_sha256": _sha256(
            item["baseline_sha256"], f"{path}/baseline_sha256"
        ),
        "selected_source": _enum(
            item["selected_source"], ("baseline", "hypes"), f"{path}/selected_source"
        ),
        "selected_strategy": _strategy(
            item["selected_strategy"], f"{path}/selected_strategy"
        ),
        "required_content_ids": _identifier_list(
            item["required_content_ids"],
            f"{path}/required_content_ids",
            minimum=1,
        ),
        "human_responsibility": _human_responsibility(
            item["human_responsibility"], f"{path}/human_responsibility"
        ),
        "hypes_help_applied": _boolean(
            item["hypes_help_applied"], f"{path}/hypes_help_applied"
        ),
        "delivery_id": _identifier(item["delivery_id"], f"{path}/delivery_id"),
        "delivery_receipt_sha256": _sha256(
            item["delivery_receipt_sha256"],
            f"{path}/delivery_receipt_sha256",
        ),
        "delivery_status": _enum(
            item["delivery_status"], ("delivered",), f"{path}/delivery_status"
        ),
        "attribution_window_opened": _boolean(
            item["attribution_window_opened"],
            f"{path}/attribution_window_opened",
            constant=True,
        ),
        "caller_attested": _boolean(
            item["caller_attested"], f"{path}/caller_attested", constant=True
        ),
    }
    expected_help = result["selected_source"] == "hypes"
    if result["hypes_help_applied"] is not expected_help:
        _fail("HELP_APPLICATION_MISMATCH", f"{path}/hypes_help_applied")
    actual_digest = _sha256(item["delivery_digest"], f"{path}/delivery_digest")
    if _digest(result) != actual_digest:
        _fail("DELIVERY_DIGEST_MISMATCH", f"{path}/delivery_digest")
    result["delivery_digest"] = actual_digest
    return result


def _outcome_vector(value: Any, path: str) -> dict[str, str]:
    item = _object(value, path)
    required = {
        "decision_progress",
        "error_detection",
        "independent_followup",
        "responsibility_understanding",
    }
    _exact_keys(item, required=required, path=path)
    return {
        key: _enum(item[key], OUTCOME_VALUES, f"{path}/{key}")
        for key in sorted(required)
    }


def _observation(
    value: Any,
    path: str,
    *,
    deliveries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    item = _object(value, path)
    kind = _enum(
        item.get("kind"),
        ("confirmed_correction", "attributable_field_outcome"),
        f"{path}/kind",
    )
    common = {"event_id", "sequence", "kind", "relation_scope"}
    if kind == "confirmed_correction":
        required = common | {"effect", "confirmed_by_user"}
        _exact_keys(item, required=required, path=path)
        return {
            "event_id": _identifier(item["event_id"], f"{path}/event_id"),
            "sequence": _integer(item["sequence"], f"{path}/sequence"),
            "kind": kind,
            "relation_scope": _scope(
                item["relation_scope"], f"{path}/relation_scope"
            ),
            "effect": _enum(
                item["effect"], ("unknown", "likely_gap"), f"{path}/effect"
            ),
            "confirmed_by_user": _boolean(
                item["confirmed_by_user"],
                f"{path}/confirmed_by_user",
                constant=True,
            ),
        }

    required = common | {
        "source",
        "delivery_id",
        "delivery_receipt_sha256",
        "outcome_id",
        "outcome_sha256",
        "outcome_vector",
    }
    _exact_keys(item, required=required, path=path)
    delivery_id = _identifier(item["delivery_id"], f"{path}/delivery_id")
    if delivery_id not in deliveries:
        _fail("DELIVERY_NOT_FOUND", f"{path}/delivery_id")
    delivery = deliveries[delivery_id]
    receipt_digest = _sha256(
        item["delivery_receipt_sha256"], f"{path}/delivery_receipt_sha256"
    )
    if receipt_digest != delivery["delivery_receipt_sha256"]:
        _fail("DELIVERY_BINDING_MISMATCH", f"{path}/delivery_receipt_sha256")
    relation_scope = _scope(item["relation_scope"], f"{path}/relation_scope")
    if _scope_key(relation_scope) != _scope_key(delivery["relation_scope"]):
        _fail("DELIVERY_SCOPE_MISMATCH", f"{path}/relation_scope")
    return {
        "event_id": _identifier(item["event_id"], f"{path}/event_id"),
        "sequence": _integer(item["sequence"], f"{path}/sequence"),
        "kind": kind,
        "relation_scope": relation_scope,
        "source": _enum(
            item["source"],
            ("user_confirmed", "project_evaluator"),
            f"{path}/source",
        ),
        "delivery_id": delivery_id,
        "delivery_receipt_sha256": receipt_digest,
        "outcome_id": _identifier(item["outcome_id"], f"{path}/outcome_id"),
        "outcome_sha256": _sha256(
            item["outcome_sha256"], f"{path}/outcome_sha256"
        ),
        "outcome_vector": _outcome_vector(
            item["outcome_vector"], f"{path}/outcome_vector"
        ),
    }


def _pending(value: Any, path: str) -> dict[str, Any] | None:
    if value is None:
        return None
    item = _object(value, path)
    required = {
        "lifecycle",
        "turn_id",
        "turn_sequence",
        "request_id",
        "relation_scope",
        "baseline_delivery_plan",
        "recommended_strategy",
        "reason_code",
        "basis_event_ids",
        "proposal_differs_from_baseline",
        "selected_source",
        "user_confirmed_hypes_selection",
        "selected_strategy",
        "pending_digest",
    }
    _exact_keys(item, required=required, path=path)
    lifecycle = _enum(
        item["lifecycle"], ("prepared", "committed"), f"{path}/lifecycle"
    )
    selected_source = item["selected_source"]
    selected_strategy = item["selected_strategy"]
    user_confirmed = _boolean(
        item["user_confirmed_hypes_selection"],
        f"{path}/user_confirmed_hypes_selection",
    )
    if lifecycle == "prepared":
        if selected_source is not None or selected_strategy is not None or user_confirmed:
            _fail("PREPARED_SELECTION_MUST_BE_EMPTY", path)
    else:
        selected_source = _enum(
            selected_source,
            ("baseline", "hypes"),
            f"{path}/selected_source",
        )
        selected_strategy = _strategy(
            selected_strategy, f"{path}/selected_strategy"
        )
    result = {
        "lifecycle": lifecycle,
        "turn_id": _identifier(item["turn_id"], f"{path}/turn_id"),
        "turn_sequence": _integer(
            item["turn_sequence"], f"{path}/turn_sequence"
        ),
        "request_id": _identifier(item["request_id"], f"{path}/request_id"),
        "relation_scope": _scope(item["relation_scope"], f"{path}/relation_scope"),
        "baseline_delivery_plan": _baseline(
            item["baseline_delivery_plan"], f"{path}/baseline_delivery_plan"
        ),
        "recommended_strategy": _strategy(
            item["recommended_strategy"], f"{path}/recommended_strategy"
        ),
        "reason_code": _enum(
            item["reason_code"],
            (
                "control_baseline",
                "assistance_not_allowed",
                "matching_likely_gap",
                "matching_demonstrated",
                "prior_help_not_effective",
                "unknown_high_impact",
                "no_matching_state",
            ),
            f"{path}/reason_code",
        ),
        "basis_event_ids": _identifier_list(
            item["basis_event_ids"], f"{path}/basis_event_ids"
        ),
        "proposal_differs_from_baseline": _boolean(
            item["proposal_differs_from_baseline"],
            f"{path}/proposal_differs_from_baseline",
        ),
        "selected_source": selected_source,
        "user_confirmed_hypes_selection": user_confirmed,
        "selected_strategy": selected_strategy,
    }
    actual = _sha256(item["pending_digest"], f"{path}/pending_digest")
    if _digest(result) != actual:
        _fail("PENDING_DIGEST_MISMATCH", f"{path}/pending_digest")
    result["pending_digest"] = actual
    return result


def _state(value: Any, path: str = "$/field_session") -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "schema_version",
        "field_policy_id",
        "field_session_id",
        "conversation_id",
        "task_contract_id",
        "trial_condition",
        "status",
        "observations",
        "deliveries",
        "pending_turn",
        "persistent_write_count",
        "state_digest",
    }
    _exact_keys(item, required=required, path=path)
    if item["schema_version"] != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_MISMATCH", f"{path}/schema_version")
    if item["field_policy_id"] != POLICY_ID:
        _fail("POLICY_MISMATCH", f"{path}/field_policy_id")
    if item["task_contract_id"] != TASK_CONTRACT_ID:
        _fail("TASK_CONTRACT_MISMATCH", f"{path}/task_contract_id")

    deliveries_list = _array(
        item["deliveries"], f"{path}/deliveries", maximum=MAX_DELIVERIES
    )
    deliveries = [
        _delivery(entry, f"{path}/deliveries/{index}")
        for index, entry in enumerate(deliveries_list)
    ]
    delivery_ids = [entry["delivery_id"] for entry in deliveries]
    if len(delivery_ids) != len(set(delivery_ids)):
        _fail("DUPLICATE_DELIVERY_ID", f"{path}/deliveries")
    delivery_map = {entry["delivery_id"]: entry for entry in deliveries}

    observation_list = _array(
        item["observations"],
        f"{path}/observations",
        maximum=MAX_OBSERVATIONS,
    )
    observations = [
        _observation(
            entry,
            f"{path}/observations/{index}",
            deliveries=delivery_map,
        )
        for index, entry in enumerate(observation_list)
    ]
    event_ids = [entry["event_id"] for entry in observations]
    if len(event_ids) != len(set(event_ids)):
        _fail("DUPLICATE_EVENT_ID", f"{path}/observations")
    sequences = [entry["sequence"] for entry in observations]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        _fail("NON_MONOTONIC_SEQUENCE", f"{path}/observations")

    result = {
        "schema_version": SCHEMA_VERSION,
        "field_policy_id": POLICY_ID,
        "field_session_id": _identifier(
            item["field_session_id"], f"{path}/field_session_id"
        ),
        "conversation_id": _identifier(
            item["conversation_id"], f"{path}/conversation_id"
        ),
        "task_contract_id": TASK_CONTRACT_ID,
        "trial_condition": _enum(
            item["trial_condition"],
            TRIAL_CONDITIONS,
            f"{path}/trial_condition",
        ),
        "status": _enum(item["status"], ("active", "closed"), f"{path}/status"),
        "observations": observations,
        "deliveries": deliveries,
        "pending_turn": _pending(item["pending_turn"], f"{path}/pending_turn"),
        "persistent_write_count": _integer(
            item["persistent_write_count"], f"{path}/persistent_write_count"
        ),
    }
    if result["persistent_write_count"] != 0:
        _fail("PERSISTENT_WRITE_FORBIDDEN", f"{path}/persistent_write_count")
    actual = _sha256(item["state_digest"], f"{path}/state_digest")
    if _digest(result) != actual:
        _fail("STATE_DIGEST_MISMATCH", f"{path}/state_digest")
    result["state_digest"] = actual
    return result


def _seal_state(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("state_digest", None)
    result["state_digest"] = _digest(result)
    return result


def _seal_pending(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("pending_digest", None)
    result["pending_digest"] = _digest(result)
    return result


def _projection(
    observations: list[Mapping[str, Any]],
    deliveries: Mapping[str, Mapping[str, Any]],
    relation_scope: Mapping[str, str],
    *,
    corrections_only: bool,
) -> dict[str, Any]:
    capability = "unknown"
    assistance_effect = "unknown"
    basis: list[str] = []
    target = _scope_key(relation_scope)
    for event in observations:
        if _scope_key(event["relation_scope"]) != target:
            continue
        if event["kind"] == "confirmed_correction":
            capability = event["effect"]
            assistance_effect = "unknown"
            basis = [event["event_id"]]
            continue
        if corrections_only:
            continue
        delivery = deliveries[event["delivery_id"]]
        vector = event["outcome_vector"]
        values = tuple(vector.values())
        has_failure = "no" in values
        complete_independent_success = (
            vector["decision_progress"] == "yes"
            and vector["responsibility_understanding"] == "yes"
            and vector["independent_followup"] == "yes"
            and vector["error_detection"] in ("yes", "not_applicable")
        )
        if has_failure:
            capability = "likely_gap"
        elif complete_independent_success:
            capability = "demonstrated"
        if delivery["hypes_help_applied"]:
            assistance_effect = "not_helpful" if has_failure else (
                "helpful" if vector["decision_progress"] == "yes" else "unknown"
            )
        basis = [event["event_id"]]
    return {
        "capability": capability,
        "assistance_effect": assistance_effect,
        "basis_event_ids": basis,
    }


def _recommend_strategy(
    *,
    baseline: Mapping[str, Any],
    relation_scope: Mapping[str, str],
    assistance_allowed: bool,
    trial_condition: str,
    observations: list[Mapping[str, Any]],
    deliveries: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, str], str, list[str]]:
    baseline_strategy = copy.deepcopy(baseline["delivery_strategy"])
    if trial_condition == "baseline":
        return baseline_strategy, "control_baseline", []
    if not assistance_allowed:
        return baseline_strategy, "assistance_not_allowed", []
    projection = _projection(
        observations,
        deliveries,
        relation_scope,
        corrections_only=trial_condition == "scope_filter",
    )
    responsibility_move = baseline_strategy["responsibility_move"]
    if relation_scope["responsibility"] == "approve_high_impact":
        responsibility_move = "request_confirmation"
    if projection["capability"] == "likely_gap":
        strategy = {
            "information_depth": "expanded",
            "support_mode": "scaffold",
            "dialogue_move": "answer",
            "responsibility_move": responsibility_move,
        }
        reason = "matching_likely_gap"
    elif projection["capability"] == "demonstrated":
        strategy = {
            "information_depth": "minimal",
            "support_mode": "none",
            "dialogue_move": "answer",
            "responsibility_move": responsibility_move,
        }
        reason = "matching_demonstrated"
    elif projection["assistance_effect"] == "not_helpful":
        strategy = {
            "information_depth": "standard",
            "support_mode": "scaffold",
            "dialogue_move": "ask",
            "responsibility_move": responsibility_move,
        }
        reason = "prior_help_not_effective"
    elif relation_scope["responsibility"] == "approve_high_impact":
        strategy = {
            "information_depth": "standard",
            "support_mode": "scaffold",
            "dialogue_move": "ask",
            "responsibility_move": "request_confirmation",
        }
        reason = "unknown_high_impact"
    else:
        strategy = {
            "information_depth": "standard",
            "support_mode": "example",
            "dialogue_move": "answer",
            "responsibility_move": responsibility_move,
        }
        reason = "no_matching_state"
    return strategy, reason, projection["basis_event_ids"]


def _validate_request_base(value: Any) -> tuple[Mapping[str, Any], str, str]:
    item = _object(value, "$")
    schema = item.get("schema_version")
    if schema != SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_MISMATCH", "$/schema_version")
    if item.get("expected_policy_id") != POLICY_ID:
        _fail("POLICY_MISMATCH", "$/expected_policy_id")
    operation = _enum(
        item.get("operation"),
        ("start", "prepare", "commit", "attest_delivery", "close"),
        "$/operation",
    )
    request_id = _identifier(item.get("request_id"), "$/request_id")
    return item, operation, request_id


def _merge_observations(
    state: Mapping[str, Any], raw: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    delivery_map = {entry["delivery_id"]: entry for entry in state["deliveries"]}
    incoming = [
        _observation(
            value,
            f"$/observations/{index}",
            deliveries=delivery_map,
        )
        for index, value in enumerate(
            _array(raw, "$/observations", maximum=MAX_OBSERVATIONS)
        )
    ]
    existing_by_id = {event["event_id"]: event for event in state["observations"]}
    last_sequence = state["observations"][-1]["sequence"] if state["observations"] else -1
    accepted_ids: list[str] = []
    merged = copy.deepcopy(state["observations"])
    for event in incoming:
        previous = existing_by_id.get(event["event_id"])
        if previous is not None:
            if _canonical_bytes(previous) != _canonical_bytes(event):
                _fail("CONFLICTING_EVENT_ID", "$/observations")
            continue
        if event["sequence"] <= last_sequence:
            _fail("OUT_OF_ORDER_EVENT", "$/observations")
        merged.append(event)
        existing_by_id[event["event_id"]] = event
        accepted_ids.append(event["event_id"])
        last_sequence = event["sequence"]
    if len(merged) > MAX_OBSERVATIONS:
        _fail("ARRAY_TOO_LONG", "$/observations")
    return merged, accepted_ids


def _summary(state: Mapping[str, Any]) -> dict[str, Any]:
    outcomes = [
        event
        for event in state["observations"]
        if event["kind"] == "attributable_field_outcome"
    ]
    corrections = [
        event
        for event in state["observations"]
        if event["kind"] == "confirmed_correction"
    ]
    dimension_counts = {
        dimension: {value: 0 for value in OUTCOME_VALUES}
        for dimension in (
            "decision_progress",
            "error_detection",
            "independent_followup",
            "responsibility_understanding",
        )
    }
    for event in outcomes:
        for dimension, value in event["outcome_vector"].items():
            dimension_counts[dimension][value] += 1
    return {
        "trial_condition": state["trial_condition"],
        "delivered_turn_count": len(state["deliveries"]),
        "hypes_applied_turn_count": sum(
            1 for delivery in state["deliveries"] if delivery["hypes_help_applied"]
        ),
        "baseline_turn_count": sum(
            1 for delivery in state["deliveries"] if not delivery["hypes_help_applied"]
        ),
        "confirmed_correction_count": len(corrections),
        "attributable_outcome_count": len(outcomes),
        "outcome_dimension_counts": dimension_counts,
        "aggregate_reward": None,
    }


def process(value: Any) -> dict[str, Any]:
    item, operation, request_id = _validate_request_base(value)
    common = {"schema_version", "expected_policy_id", "operation", "request_id"}

    if operation == "start":
        required = common | {
            "field_session_id",
            "conversation_id",
            "task_contract_id",
            "trial_condition",
        }
        _exact_keys(item, required=required, path="$")
        if item["task_contract_id"] != TASK_CONTRACT_ID:
            _fail("TASK_CONTRACT_MISMATCH", "$/task_contract_id")
        state = _seal_state(
            {
                "schema_version": SCHEMA_VERSION,
                "field_policy_id": POLICY_ID,
                "field_session_id": _identifier(
                    item["field_session_id"], "$/field_session_id"
                ),
                "conversation_id": _identifier(
                    item["conversation_id"], "$/conversation_id"
                ),
                "task_contract_id": TASK_CONTRACT_ID,
                "trial_condition": _enum(
                    item["trial_condition"], TRIAL_CONDITIONS, "$/trial_condition"
                ),
                "status": "active",
                "observations": [],
                "deliveries": [],
                "pending_turn": None,
                "persistent_write_count": 0,
            }
        )
        result: dict[str, Any] = {"started": True}
    else:
        state = _state(item.get("field_session"))
        if state["status"] == "closed" and operation != "close":
            _fail("SESSION_CLOSED", "$/field_session/status")
        if operation == "prepare":
            required = common | {
                "field_session",
                "turn_id",
                "relation_scope",
                "assistance_allowed",
                "baseline_delivery_plan",
                "observations",
            }
            _exact_keys(item, required=required, path="$")
            if state["pending_turn"] is not None:
                _fail("PENDING_TURN_EXISTS", "$/field_session/pending_turn")
            baseline = _baseline(
                item["baseline_delivery_plan"], "$/baseline_delivery_plan"
            )
            relation_scope = _scope(item["relation_scope"], "$/relation_scope")
            if (
                relation_scope["responsibility"]
                != baseline["human_responsibility"]["decision_class"]
            ):
                _fail("RESPONSIBILITY_SCOPE_MISMATCH", "$/relation_scope")
            observations, accepted_ids = _merge_observations(
                state, item["observations"]
            )
            state["observations"] = observations
            delivery_map = {
                entry["delivery_id"]: entry for entry in state["deliveries"]
            }
            recommended, reason, basis = _recommend_strategy(
                baseline=baseline,
                relation_scope=relation_scope,
                assistance_allowed=_boolean(
                    item["assistance_allowed"], "$/assistance_allowed"
                ),
                trial_condition=state["trial_condition"],
                observations=observations,
                deliveries=delivery_map,
            )
            pending = _seal_pending(
                {
                    "lifecycle": "prepared",
                    "turn_id": _identifier(item["turn_id"], "$/turn_id"),
                    "turn_sequence": len(state["deliveries"]) + 1,
                    "request_id": request_id,
                    "relation_scope": relation_scope,
                    "baseline_delivery_plan": baseline,
                    "recommended_strategy": recommended,
                    "reason_code": reason,
                    "basis_event_ids": basis,
                    "proposal_differs_from_baseline": (
                        _canonical_bytes(recommended)
                        != _canonical_bytes(baseline["delivery_strategy"])
                    ),
                    "selected_source": None,
                    "user_confirmed_hypes_selection": False,
                    "selected_strategy": None,
                }
            )
            state["pending_turn"] = pending
            state = _seal_state(state)
            result = {
                "accepted_observation_ids": accepted_ids,
                "proposal": {
                    "recommended_strategy": copy.deepcopy(recommended),
                    "reason_code": reason,
                    "basis_event_ids": list(basis),
                    "differs_from_baseline": pending[
                        "proposal_differs_from_baseline"
                    ],
                    "applied": False,
                },
                "preserved_required_content_ids": copy.deepcopy(
                    baseline["required_content_ids"]
                ),
                "preserved_human_responsibility": copy.deepcopy(
                    baseline["human_responsibility"]
                ),
            }
        elif operation == "commit":
            required = common | {
                "field_session",
                "pending_plan_digest",
                "selected_source",
                "user_confirmed_hypes_selection",
            }
            _exact_keys(item, required=required, path="$")
            pending = state["pending_turn"]
            if pending is None or pending["lifecycle"] != "prepared":
                _fail("NO_PREPARED_TURN", "$/field_session/pending_turn")
            if _sha256(item["pending_plan_digest"], "$/pending_plan_digest") != pending[
                "pending_digest"
            ]:
                _fail("PENDING_BINDING_MISMATCH", "$/pending_plan_digest")
            selected_source = _enum(
                item["selected_source"],
                ("baseline", "hypes"),
                "$/selected_source",
            )
            confirmed = _boolean(
                item["user_confirmed_hypes_selection"],
                "$/user_confirmed_hypes_selection",
            )
            if selected_source == "hypes":
                if state["trial_condition"] == "baseline":
                    _fail("HYPES_SELECTION_FORBIDDEN_IN_CONTROL", "$/selected_source")
                if not pending["proposal_differs_from_baseline"]:
                    _fail("NO_HYPES_DIFFERENCE_TO_APPLY", "$/selected_source")
                if not confirmed:
                    _fail("HYPES_SELECTION_REQUIRES_CONFIRMATION", "$/selected_source")
                selected_strategy = pending["recommended_strategy"]
            else:
                if confirmed:
                    _fail("BASELINE_SELECTION_CANNOT_CONFIRM_HYPES", "$/selected_source")
                selected_strategy = pending["baseline_delivery_plan"][
                    "delivery_strategy"
                ]
            pending.update(
                {
                    "lifecycle": "committed",
                    "selected_source": selected_source,
                    "user_confirmed_hypes_selection": confirmed,
                    "selected_strategy": copy.deepcopy(selected_strategy),
                }
            )
            pending = _seal_pending(pending)
            state["pending_turn"] = pending
            state = _seal_state(state)
            result = {
                "committed_plan": {
                    "turn_id": pending["turn_id"],
                    "selected_source": selected_source,
                    "selected_strategy": copy.deepcopy(selected_strategy),
                    "required_content_ids": copy.deepcopy(
                        pending["baseline_delivery_plan"]["required_content_ids"]
                    ),
                    "human_responsibility": copy.deepcopy(
                        pending["baseline_delivery_plan"]["human_responsibility"]
                    ),
                    "actual_delivery_not_yet_proven": True,
                }
            }
        elif operation == "attest_delivery":
            required = common | {
                "field_session",
                "pending_commit_digest",
                "delivery_id",
                "delivery_receipt_sha256",
                "delivered",
                "caller_attested",
            }
            _exact_keys(item, required=required, path="$")
            pending = state["pending_turn"]
            if pending is None or pending["lifecycle"] != "committed":
                _fail("NO_COMMITTED_TURN", "$/field_session/pending_turn")
            if _sha256(
                item["pending_commit_digest"], "$/pending_commit_digest"
            ) != pending["pending_digest"]:
                _fail("PENDING_BINDING_MISMATCH", "$/pending_commit_digest")
            _boolean(item["delivered"], "$/delivered", constant=True)
            _boolean(
                item["caller_attested"], "$/caller_attested", constant=True
            )
            delivery_id = _identifier(item["delivery_id"], "$/delivery_id")
            if any(
                entry["delivery_id"] == delivery_id for entry in state["deliveries"]
            ):
                _fail("DUPLICATE_DELIVERY_ID", "$/delivery_id")
            delivery_without_digest = {
                "turn_id": pending["turn_id"],
                "turn_sequence": pending["turn_sequence"],
                "relation_scope": copy.deepcopy(pending["relation_scope"]),
                "baseline_id": pending["baseline_delivery_plan"]["baseline_id"],
                "baseline_sha256": pending["baseline_delivery_plan"][
                    "baseline_sha256"
                ],
                "selected_source": pending["selected_source"],
                "selected_strategy": copy.deepcopy(pending["selected_strategy"]),
                "required_content_ids": copy.deepcopy(
                    pending["baseline_delivery_plan"]["required_content_ids"]
                ),
                "human_responsibility": copy.deepcopy(
                    pending["baseline_delivery_plan"]["human_responsibility"]
                ),
                "hypes_help_applied": pending["selected_source"] == "hypes",
                "delivery_id": delivery_id,
                "delivery_receipt_sha256": _sha256(
                    item["delivery_receipt_sha256"],
                    "$/delivery_receipt_sha256",
                ),
                "delivery_status": "delivered",
                "attribution_window_opened": True,
                "caller_attested": True,
            }
            delivery = dict(delivery_without_digest)
            delivery["delivery_digest"] = _digest(delivery_without_digest)
            state["deliveries"].append(delivery)
            state["pending_turn"] = None
            state = _seal_state(state)
            result = {
                "delivery_record": copy.deepcopy(delivery),
                "platform_delivery_independently_proven": False,
            }
        else:
            required = common | {"field_session"}
            _exact_keys(item, required=required, path="$")
            if state["pending_turn"] is not None:
                _fail("PENDING_TURN_EXISTS", "$/field_session/pending_turn")
            state["status"] = "closed"
            state = _seal_state(state)
            result = {"summary": _summary(state)}

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "operation": operation,
        "request_id": request_id,
        "field_session": state,
        "result": result,
        "persistent_write_count": 0,
        "canonical_json": "stdlib-jcs-subset",
        "hash_algorithm": "sha256",
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_JSON_KEY", "$")
        result[key] = value
    return result


def loads_request(data: bytes) -> Any:
    if not data:
        _fail("EMPTY_INPUT", "$")
    if len(data) > MAX_INPUT_BYTES:
        _fail("INPUT_TOO_LARGE", "$")
    try:
        return json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: _fail("NON_FINITE_NUMBER", "$"),
        )
    except UnicodeDecodeError:
        _fail("INVALID_UTF8", "$")
    except json.JSONDecodeError:
        _fail("INVALID_JSON", "$")


def _error_receipt(error: InputError) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "invalid_input",
        "error": {"code": error.code, "path": error.path},
        "persistent_write_count": 0,
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


def main() -> int:
    try:
        value = loads_request(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = process(value)
        exit_code = 0
    except InputError as error:
        result = _error_receipt(error)
        exit_code = 2
    except Exception:
        result = _error_receipt(InputError(code="INTERNAL_ERROR", path="$"))
        exit_code = 3
    sys.stdout.buffer.write(_canonical_bytes(result) + b"\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
