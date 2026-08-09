#!/usr/bin/env python3
"""Stateless, recommendation-only help policy.

The program accepts one JSON object on stdin and emits one JSON object on
stdout.  It deliberately has no file, database, network, or model adapter.
Only identifiers, enums, booleans, sequence numbers, and SHA-256 bindings are
accepted; response text and conversation text are outside this boundary.
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
POLICY_ID = "recommend-help-fixed-v0.1.0"
MAX_INPUT_BYTES = 1_048_576

INTERVENTIONS = ("none", "brief", "scaffold")
BASELINE_ASSISTANCE_MODES = (*INTERVENTIONS, "direct_completion")
EFFECTS = ("unknown", "likely_gap", "demonstrated")
AUTHORITATIVE_KINDS = (
    "confirmed_correction",
    "attributable_baseline_outcome",
)
REASON_CODES = (
    "assistance_not_allowed",
    "matching_likely_gap",
    "matching_demonstrated",
    "matching_unknown",
    "unknown_high_impact",
    "no_matching_state",
)

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class InputError(Exception):
    """A stable, value-free validation failure."""

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
    value: Mapping[str, Any],
    *,
    required: set[str],
    path: str,
) -> None:
    actual = set(value)
    if actual - required:
        _fail("UNKNOWN_FIELD", path)
    if required - actual:
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


def _sequence(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("EXPECTED_INTEGER", path)
    if not 0 <= value <= 9_007_199_254_740_991:
        _fail("INTEGER_OUT_OF_RANGE", path)
    return value


def _list(value: Any, path: str, *, maximum: int = 256) -> list[Any]:
    if not isinstance(value, list):
        _fail("EXPECTED_ARRAY", path)
    if len(value) > maximum:
        _fail("ARRAY_TOO_LONG", path)
    return value


def _identifier_list(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
) -> tuple[str, ...]:
    items = tuple(
        _identifier(item, f"{path}/{index}")
        for index, item in enumerate(_list(value, path))
    )
    if len(items) < minimum:
        _fail("ARRAY_TOO_SHORT", path)
    if len(items) != len(set(items)):
        _fail("DUPLICATE_ITEM", path)
    return items


def _canonical_bytes(value: Any) -> bytes:
    """Canonical encoding for the deliberately number-light accepted domain."""

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
    required = {"project_id", "task_relation", "responsibility"}
    _exact_keys(item, required=required, path=path)
    return {
        "project_id": _identifier(item["project_id"], f"{path}/project_id"),
        "task_relation": _identifier(
            item["task_relation"], f"{path}/task_relation"
        ),
        "responsibility": _enum(
            item["responsibility"],
            ("ordinary", "approve_high_impact"),
            f"{path}/responsibility",
        ),
    }


def _scope_key(scope: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        scope["project_id"],
        scope["task_relation"],
        scope["responsibility"],
    )


def _human_responsibility(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "release_owner",
        "agent_execution_authority",
        "decision_class",
        "required_check_ids",
    }
    _exact_keys(item, required=required, path=path)
    release_owner = _enum(item["release_owner"], ("human",), f"{path}/release_owner")
    authority = _boolean(
        item["agent_execution_authority"],
        f"{path}/agent_execution_authority",
        constant=False,
    )
    decision_class = _enum(
        item["decision_class"],
        ("ordinary", "approve_high_impact"),
        f"{path}/decision_class",
    )
    checks = _identifier_list(
        item["required_check_ids"],
        f"{path}/required_check_ids",
        minimum=1,
    )
    return {
        "release_owner": release_owner,
        "agent_execution_authority": authority,
        "decision_class": decision_class,
        "required_check_ids": list(checks),
    }


def _baseline(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "baseline_id",
        "baseline_sha256",
        "required_content_ids",
        "optional_content_ids",
        "assistance_mode",
        "human_responsibility",
    }
    _exact_keys(item, required=required, path=path)
    required_ids = _identifier_list(
        item["required_content_ids"],
        f"{path}/required_content_ids",
        minimum=1,
    )
    optional_ids = _identifier_list(
        item["optional_content_ids"],
        f"{path}/optional_content_ids",
    )
    if set(required_ids) & set(optional_ids):
        _fail("CONTENT_CLASS_OVERLAP", path)
    return {
        "baseline_id": _identifier(item["baseline_id"], f"{path}/baseline_id"),
        "baseline_sha256": _sha256(
            item["baseline_sha256"], f"{path}/baseline_sha256"
        ),
        "required_content_ids": list(required_ids),
        "optional_content_ids": list(optional_ids),
        "assistance_mode": _enum(
            item["assistance_mode"],
            BASELINE_ASSISTANCE_MODES,
            f"{path}/assistance_mode",
        ),
        "human_responsibility": _human_responsibility(
            item["human_responsibility"], f"{path}/human_responsibility"
        ),
    }


def _seen_event(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {"event_id", "event_digest", "sequence"}
    _exact_keys(item, required=required, path=path)
    return {
        "event_id": _identifier(item["event_id"], f"{path}/event_id"),
        "event_digest": _sha256(item["event_digest"], f"{path}/event_digest"),
        "sequence": _sequence(item["sequence"], f"{path}/sequence"),
    }


def _relation_state(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "relation_scope",
        "effect",
        "source_event_id",
        "source_kind",
        "source_delivery_id",
        "sequence",
    }
    _exact_keys(item, required=required, path=path)
    source_kind = _enum(
        item["source_kind"], AUTHORITATIVE_KINDS, f"{path}/source_kind"
    )
    effect = _enum(item["effect"], EFFECTS, f"{path}/effect")
    if source_kind == "confirmed_correction":
        if effect == "demonstrated":
            _fail("CORRECTION_CANNOT_DEMONSTRATE", f"{path}/effect")
        if item["source_delivery_id"] is not None:
            _fail("INVALID_SOURCE_DELIVERY", f"{path}/source_delivery_id")
        source_delivery_id = None
    else:
        source_delivery_id = _identifier(
            item["source_delivery_id"], f"{path}/source_delivery_id"
        )
    return {
        "relation_scope": _scope(item["relation_scope"], f"{path}/relation_scope"),
        "effect": effect,
        "source_event_id": _identifier(
            item["source_event_id"], f"{path}/source_event_id"
        ),
        "source_kind": source_kind,
        "source_delivery_id": source_delivery_id,
        "sequence": _sequence(item["sequence"], f"{path}/sequence"),
    }


def _prior_delivered_baseline(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "relation_scope",
        "baseline_id",
        "baseline_sha256",
        "delivery_id",
        "delivery_receipt_sha256",
        "delivery_status",
        "attribution_window_id",
        "attribution_window_opened",
        "hypes_help_applied",
    }
    _exact_keys(item, required=required, path=path)
    return {
        "relation_scope": _scope(item["relation_scope"], f"{path}/relation_scope"),
        "baseline_id": _identifier(item["baseline_id"], f"{path}/baseline_id"),
        "baseline_sha256": _sha256(
            item["baseline_sha256"], f"{path}/baseline_sha256"
        ),
        "delivery_id": _identifier(item["delivery_id"], f"{path}/delivery_id"),
        "delivery_receipt_sha256": _sha256(
            item["delivery_receipt_sha256"], f"{path}/delivery_receipt_sha256"
        ),
        "delivery_status": _enum(
            item["delivery_status"], ("delivered",), f"{path}/delivery_status"
        ),
        "attribution_window_id": _identifier(
            item["attribution_window_id"], f"{path}/attribution_window_id"
        ),
        "attribution_window_opened": _boolean(
            item["attribution_window_opened"],
            f"{path}/attribution_window_opened",
            constant=True,
        ),
        "hypes_help_applied": _boolean(
            item["hypes_help_applied"],
            f"{path}/hypes_help_applied",
            constant=False,
        ),
    }


def _overlay(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    required = {
        "conversation_id",
        "revision",
        "seen_events",
        "relation_states",
        "prior_delivered_baselines",
    }
    _exact_keys(item, required=required, path=path)
    seen = [
        _seen_event(entry, f"{path}/seen_events/{index}")
        for index, entry in enumerate(_list(item["seen_events"], f"{path}/seen_events"))
    ]
    if len({entry["event_id"] for entry in seen}) != len(seen):
        _fail("DUPLICATE_EVENT_ID", f"{path}/seen_events")
    if [entry["sequence"] for entry in seen] != sorted(
        entry["sequence"] for entry in seen
    ) or len({entry["sequence"] for entry in seen}) != len(seen):
        _fail("EVENT_SEQUENCE_ORDER", f"{path}/seen_events")
    revision = _sequence(item["revision"], f"{path}/revision")
    if revision != len(seen):
        _fail("OVERLAY_REVISION_MISMATCH", f"{path}/revision")

    states = [
        _relation_state(entry, f"{path}/relation_states/{index}")
        for index, entry in enumerate(
            _list(item["relation_states"], f"{path}/relation_states")
        )
    ]
    state_keys = [_scope_key(entry["relation_scope"]) for entry in states]
    if state_keys != sorted(state_keys) or len(set(state_keys)) != len(state_keys):
        _fail("RELATION_STATE_ORDER", f"{path}/relation_states")
    seen_by_id = {entry["event_id"]: entry for entry in seen}
    for index, state in enumerate(states):
        source = seen_by_id.get(state["source_event_id"])
        if source is None or source["sequence"] != state["sequence"]:
            _fail("UNBOUND_RELATION_STATE", f"{path}/relation_states/{index}")
    prior_deliveries = [
        _prior_delivered_baseline(
            entry, f"{path}/prior_delivered_baselines/{index}"
        )
        for index, entry in enumerate(
            _list(
                item["prior_delivered_baselines"],
                f"{path}/prior_delivered_baselines",
            )
        )
    ]
    delivery_ids = [entry["delivery_id"] for entry in prior_deliveries]
    window_ids = [entry["attribution_window_id"] for entry in prior_deliveries]
    if delivery_ids != sorted(delivery_ids):
        _fail("DELIVERY_BINDING_ORDER", f"{path}/prior_delivered_baselines")
    if len(delivery_ids) != len(set(delivery_ids)) or len(window_ids) != len(
        set(window_ids)
    ):
        _fail("DUPLICATE_DELIVERY_BINDING", f"{path}/prior_delivered_baselines")
    deliveries_by_id = {
        entry["delivery_id"]: entry for entry in prior_deliveries
    }
    for index, state in enumerate(states):
        if state["source_kind"] == "attributable_baseline_outcome":
            delivered = deliveries_by_id.get(state["source_delivery_id"])
            if delivered is None:
                _fail(
                    "UNBOUND_OUTCOME_STATE",
                    f"{path}/relation_states/{index}/source_delivery_id",
                )
            if delivered["relation_scope"] != state["relation_scope"]:
                _fail(
                    "OUTCOME_STATE_SCOPE_MISMATCH",
                    f"{path}/relation_states/{index}/relation_scope",
                )
    return {
        "conversation_id": _identifier(
            item["conversation_id"], f"{path}/conversation_id"
        ),
        "revision": revision,
        "seen_events": seen,
        "relation_states": states,
        "prior_delivered_baselines": prior_deliveries,
    }


def _observation(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path)
    common = {"event_id", "sequence", "relation_scope", "kind", "effect"}
    kind = item.get("kind")
    if kind == "confirmed_correction":
        required = common | {"confirmed_by_user"}
    elif kind == "attributable_baseline_outcome":
        required = common | {
            "attribution_confirmed",
            "delivery_binding",
        }
    else:
        _fail("INVALID_EVENT_KIND", f"{path}/kind")
    _exact_keys(item, required=required, path=path)

    result: dict[str, Any] = {
        "event_id": _identifier(item["event_id"], f"{path}/event_id"),
        "sequence": _sequence(item["sequence"], f"{path}/sequence"),
        "relation_scope": _scope(item["relation_scope"], f"{path}/relation_scope"),
        "kind": kind,
        "effect": _enum(item["effect"], EFFECTS, f"{path}/effect"),
    }
    if kind == "confirmed_correction":
        result["confirmed_by_user"] = _boolean(
            item["confirmed_by_user"],
            f"{path}/confirmed_by_user",
            constant=True,
        )
        if result["effect"] not in ("unknown", "likely_gap"):
            _fail("CORRECTION_CANNOT_DEMONSTRATE", f"{path}/effect")
    elif kind == "attributable_baseline_outcome":
        result["attribution_confirmed"] = _boolean(
            item["attribution_confirmed"],
            f"{path}/attribution_confirmed",
            constant=True,
        )
        binding_path = f"{path}/delivery_binding"
        binding = _object(item["delivery_binding"], binding_path)
        binding_keys = {
            "plan_kind",
            "baseline_id",
            "baseline_sha256",
            "delivery_id",
            "delivery_receipt_sha256",
            "delivery_status",
            "attribution_window_id",
            "attribution_window_opened",
            "hypes_help_applied",
            "evaluation_criteria_complete",
            "outcome_id",
            "outcome_sha256",
        }
        _exact_keys(binding, required=binding_keys, path=binding_path)
        result["delivery_binding"] = {
            "plan_kind": _enum(
                binding["plan_kind"], ("baseline",), f"{binding_path}/plan_kind"
            ),
            "baseline_id": _identifier(
                binding["baseline_id"], f"{binding_path}/baseline_id"
            ),
            "baseline_sha256": _sha256(
                binding["baseline_sha256"], f"{binding_path}/baseline_sha256"
            ),
            "delivery_id": _identifier(
                binding["delivery_id"], f"{binding_path}/delivery_id"
            ),
            "delivery_receipt_sha256": _sha256(
                binding["delivery_receipt_sha256"],
                f"{binding_path}/delivery_receipt_sha256",
            ),
            "delivery_status": _enum(
                binding["delivery_status"],
                ("delivered",),
                f"{binding_path}/delivery_status",
            ),
            "attribution_window_id": _identifier(
                binding["attribution_window_id"],
                f"{binding_path}/attribution_window_id",
            ),
            "attribution_window_opened": _boolean(
                binding["attribution_window_opened"],
                f"{binding_path}/attribution_window_opened",
                constant=True,
            ),
            "hypes_help_applied": _boolean(
                binding["hypes_help_applied"],
                f"{binding_path}/hypes_help_applied",
                constant=False,
            ),
            "evaluation_criteria_complete": _boolean(
                binding["evaluation_criteria_complete"],
                f"{binding_path}/evaluation_criteria_complete",
            ),
            "outcome_id": _identifier(
                binding["outcome_id"], f"{binding_path}/outcome_id"
            ),
            "outcome_sha256": _sha256(
                binding["outcome_sha256"], f"{binding_path}/outcome_sha256"
            ),
        }
        if (
            result["effect"] == "demonstrated"
            and not result["delivery_binding"]["evaluation_criteria_complete"]
        ):
            _fail(
                "DEMONSTRATED_REQUIRES_COMPLETE_EVALUATION",
                f"{binding_path}/evaluation_criteria_complete",
            )
    return result


def _validate_request(value: Any) -> dict[str, Any]:
    item = _object(value, "$")
    required = {
        "schema_version",
        "expected_policy_id",
        "request_id",
        "conversation_id",
        "relation_scope",
        "assistance_allowed",
        "baseline_delivery_plan",
        "current_conversation_overlay",
        "observations",
    }
    _exact_keys(item, required=required, path="$")
    if item["schema_version"] != SCHEMA_VERSION:
        _fail("UNSUPPORTED_SCHEMA_VERSION", "$/schema_version")
    if item["expected_policy_id"] != POLICY_ID:
        _fail("POLICY_MISMATCH", "$/expected_policy_id")
    observations = [
        _observation(entry, f"$/observations/{index}")
        for index, entry in enumerate(_list(item["observations"], "$/observations"))
    ]

    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    unique_observations: list[dict[str, Any]] = []
    for observation in observations:
        event_id = observation["event_id"]
        event_digest = _digest(observation)
        prior = by_id.get(event_id)
        if prior is not None:
            if prior[0] != event_digest:
                _fail("EVENT_ID_CONFLICT", "$/observations")
            continue
        by_id[event_id] = (event_digest, observation)
        unique_observations.append(observation)
    sequences = [entry["sequence"] for entry in unique_observations]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        _fail("EVENT_SEQUENCE_ORDER", "$/observations")

    conversation_id = _identifier(item["conversation_id"], "$/conversation_id")
    overlay = _overlay(
        item["current_conversation_overlay"],
        "$/current_conversation_overlay",
    )
    if overlay["conversation_id"] != conversation_id:
        _fail("CONVERSATION_MISMATCH", "$/current_conversation_overlay/conversation_id")
    relation_scope = _scope(item["relation_scope"], "$/relation_scope")
    baseline = _baseline(
        item["baseline_delivery_plan"], "$/baseline_delivery_plan"
    )
    if (
        relation_scope["responsibility"]
        != baseline["human_responsibility"]["decision_class"]
    ):
        _fail("RESPONSIBILITY_MISMATCH", "$/relation_scope/responsibility")
    return {
        "schema_version": SCHEMA_VERSION,
        "expected_policy_id": POLICY_ID,
        "request_id": _identifier(item["request_id"], "$/request_id"),
        "conversation_id": conversation_id,
        "relation_scope": relation_scope,
        "assistance_allowed": _boolean(
            item["assistance_allowed"], "$/assistance_allowed"
        ),
        "baseline_delivery_plan": baseline,
        "current_conversation_overlay": overlay,
        "observations": unique_observations,
    }


def _apply_observations(
    overlay: Mapping[str, Any],
    observations: list[dict[str, Any]],
    relation_scope: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    seen = copy.deepcopy(overlay["seen_events"])
    states = copy.deepcopy(overlay["relation_states"])
    seen_by_id = {entry["event_id"]: entry for entry in seen}
    states_by_scope = {
        _scope_key(entry["relation_scope"]): entry for entry in states
    }
    prior_by_delivery_id = {
        entry["delivery_id"]: entry
        for entry in overlay["prior_delivered_baselines"]
    }
    max_seen_sequence = max((entry["sequence"] for entry in seen), default=-1)
    accepted: list[str] = []

    for observation in observations:
        event_digest = _digest(observation)
        prior = seen_by_id.get(observation["event_id"])
        if prior is not None:
            if prior["event_digest"] != event_digest:
                _fail("EVENT_ID_CONFLICT", "$/observations")
            continue
        # This invocation owns one relation only.  Another relation is neither
        # remembered nor projected into the target recommendation.
        if observation["relation_scope"] != relation_scope:
            continue
        if observation["kind"] == "attributable_baseline_outcome":
            binding = observation["delivery_binding"]
            delivered = prior_by_delivery_id.get(binding["delivery_id"])
            if delivered is None:
                _fail("DELIVERY_BINDING_NOT_FOUND", "$/observations")
            binding_projection = {
                key: binding[key]
                for key in (
                    "baseline_id",
                    "baseline_sha256",
                    "delivery_id",
                    "delivery_receipt_sha256",
                    "delivery_status",
                    "attribution_window_id",
                    "attribution_window_opened",
                    "hypes_help_applied",
                )
            }
            delivered_projection = {
                key: delivered[key]
                for key in binding_projection
            }
            if (
                binding_projection != delivered_projection
                or delivered["relation_scope"] != observation["relation_scope"]
            ):
                _fail("DELIVERY_BINDING_MISMATCH", "$/observations")
        if observation["sequence"] <= max_seen_sequence:
            _fail("EVENT_SEQUENCE_REWIND", "$/observations")
        seen_record = {
            "event_id": observation["event_id"],
            "event_digest": event_digest,
            "sequence": observation["sequence"],
        }
        seen.append(seen_record)
        seen_by_id[observation["event_id"]] = seen_record
        max_seen_sequence = observation["sequence"]
        state = {
            "relation_scope": copy.deepcopy(observation["relation_scope"]),
            "effect": observation["effect"],
            "source_event_id": observation["event_id"],
            "source_kind": observation["kind"],
            "source_delivery_id": (
                observation["delivery_binding"]["delivery_id"]
                if observation["kind"] == "attributable_baseline_outcome"
                else None
            ),
            "sequence": observation["sequence"],
        }
        states_by_scope[_scope_key(observation["relation_scope"])] = state
        accepted.append(observation["event_id"])

    next_overlay = {
        "conversation_id": overlay["conversation_id"],
        "revision": overlay["revision"] + len(accepted),
        "seen_events": sorted(seen, key=lambda item: item["sequence"]),
        "relation_states": [
            states_by_scope[key] for key in sorted(states_by_scope)
        ],
        "prior_delivered_baselines": copy.deepcopy(
            overlay["prior_delivered_baselines"]
        ),
    }
    return next_overlay, tuple(accepted)


def _fixed_policy(
    *,
    assistance_allowed: bool,
    decision_class: str,
    relation_scope: Mapping[str, str],
    overlay: Mapping[str, Any],
) -> tuple[str, str, tuple[str, ...]]:
    matching_state = next(
        (
            state
            for state in overlay["relation_states"]
            if state["relation_scope"] == relation_scope
        ),
        None,
    )
    basis = () if matching_state is None else (matching_state["source_event_id"],)
    effect = "unknown" if matching_state is None else matching_state["effect"]

    if not assistance_allowed:
        return "none", "assistance_not_allowed", basis
    if effect == "likely_gap":
        return "scaffold", "matching_likely_gap", basis
    if effect == "demonstrated":
        return "none", "matching_demonstrated", basis
    if decision_class == "approve_high_impact":
        return "scaffold", "unknown_high_impact", basis
    if matching_state is not None:
        return "brief", "matching_unknown", basis
    return "brief", "no_matching_state", basis


def recommend(request: Any) -> dict[str, Any]:
    """Validate and calculate one shadow receipt without retaining state."""

    validated = _validate_request(request)
    baseline = validated["baseline_delivery_plan"]
    baseline_before = _canonical_bytes(baseline)
    next_overlay, _accepted = _apply_observations(
        validated["current_conversation_overlay"],
        validated["observations"],
        validated["relation_scope"],
    )
    intervention, reason_code, basis_event_ids = _fixed_policy(
        assistance_allowed=validated["assistance_allowed"],
        decision_class=baseline["human_responsibility"]["decision_class"],
        relation_scope=validated["relation_scope"],
        overlay=next_overlay,
    )
    baseline_echo = copy.deepcopy(baseline)
    if _canonical_bytes(baseline_echo) != baseline_before:
        raise RuntimeError("baseline preservation invariant failed")

    structured_plan_digest = hashlib.sha256(baseline_before).hexdigest()
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "request_id": validated["request_id"],
        "baseline_delivery_plan": baseline_echo,
        "baseline_binding": {
            "baseline_sha256": baseline["baseline_sha256"],
            "input_plan_digest": structured_plan_digest,
            "output_plan_digest": structured_plan_digest,
            "unchanged": True,
        },
        "shadow_recommendation": {
            "intervention": intervention,
            "reason_code": reason_code,
            "basis_event_ids": list(basis_event_ids),
            "preserved_required_content_ids": copy.deepcopy(
                baseline["required_content_ids"]
            ),
            "preserved_human_responsibility": copy.deepcopy(
                baseline["human_responsibility"]
            ),
        },
        "next_overlay": next_overlay,
        "applied": False,
        "delivery_window_opened": False,
        "attribution_allowed": False,
        "state_update_attributed_to_recommendation": False,
        "persistent_write_count": 0,
        "policy_id": POLICY_ID,
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
        "applied": False,
        "delivery_window_opened": False,
        "attribution_allowed": False,
        "state_update_attributed_to_recommendation": False,
        "persistent_write_count": 0,
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


def main() -> int:
    try:
        value = loads_request(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = recommend(value)
        exit_code = 0
    except InputError as error:
        result = _error_receipt(error)
        exit_code = 2
    except Exception:
        # Never leak input fragments or a partial recommendation on an
        # unexpected calculation failure.
        result = _error_receipt(InputError(code="INTERNAL_ERROR", path="$"))
        exit_code = 3
    sys.stdout.buffer.write(_canonical_bytes(result) + b"\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
