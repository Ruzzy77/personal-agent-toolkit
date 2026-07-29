"""Strict development-regression annotations for extraction projections."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .database import encode_json
from .errors import CorpusError

# This serialization ID is stable because private golden annotations already
# use it. It is not the current plugin identity.
GOLDEN_SCHEMA_VERSION = "work-corpus.extraction-golden.v1"
GOLDEN_QUESTION = "does_current_projection_match_pinned_observation"
GOLDEN_CLAIM_CEILING = "single_document_development_regression_only"
ANNOTATION_ID_RE = re.compile(r"^golden_[0-9a-f]{32}$")
ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+(?:Z|[+-]\d{2}:?\d{2})?$"
)
UNSTABLE_ISSUE_DETAIL_KEYS = {
    "absolute_path",
    "code",
    "command",
    "completed_at",
    "created_at",
    "duration",
    "elapsed",
    "error",
    "exception",
    "executable",
    "host",
    "message",
    "path",
    "pid",
    "severity",
    "source_name",
    "source_path",
    "stage",
    "started_at",
    "stderr",
    "stdout",
    "structural_locator",
    "timestamp",
    "updated_at",
    "uri",
    "url",
}


class GoldenEvaluationError(CorpusError):
    code = "golden_evaluation_error"


def _require_object(value: object, *, field: str) -> dict:
    if not isinstance(value, dict):
        raise GoldenEvaluationError(f"{field} must be an object")
    return value


def _require_keys(
    value: dict,
    *,
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        raise GoldenEvaluationError(
            f"{field} has invalid fields",
            details={"field": field, "missing": missing, "unknown": unknown},
        )


def _require_text(value: object, *, field: str, max_length: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise GoldenEvaluationError(
            f"{field} must contain 1-{max_length} characters"
        )
    return value


def _require_annotation_id(value: object) -> str:
    annotation_id = _require_text(
        value,
        field="annotation.annotation_id",
        max_length=39,
    )
    if ANNOTATION_ID_RE.fullmatch(annotation_id) is None:
        raise GoldenEvaluationError(
            "annotation.annotation_id must be an opaque golden_<32 lowercase hex> id"
        )
    return annotation_id


def _require_sha256(value: object, *, field: str) -> str:
    text = _require_text(value, field=field, max_length=64)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise GoldenEvaluationError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _require_ratio(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoldenEvaluationError(f"{field} must be a number between 0 and 1")
    ratio = float(value)
    if not 0 <= ratio <= 1:
        raise GoldenEvaluationError(f"{field} must be a number between 0 and 1")
    return ratio


def _validate_count_rule(value: object, *, field: str) -> dict:
    rule = _require_object(value, field=field)
    _require_keys(
        rule,
        field=field,
        required=set(),
        optional={"exact", "minimum", "maximum"},
    )
    if not rule:
        raise GoldenEvaluationError(f"{field} requires exact, minimum, or maximum")
    normalized = {}
    for key, count in rule.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise GoldenEvaluationError(f"{field}.{key} must be a non-negative integer")
        normalized[key] = count
    if (
        "minimum" in normalized
        and "maximum" in normalized
        and normalized["minimum"] > normalized["maximum"]
    ):
        raise GoldenEvaluationError(f"{field} minimum may not exceed maximum")
    return normalized


def validate_golden_annotation(value: object) -> dict:
    annotation = _require_object(value, field="annotation")
    _require_keys(
        annotation,
        field="annotation",
        required={
            "schema_version",
            "annotation_id",
            "evaluation",
            "provenance",
            "subject",
            "expected_projection",
            "criteria",
        },
    )
    if annotation["schema_version"] != GOLDEN_SCHEMA_VERSION:
        raise GoldenEvaluationError(
            "unsupported golden annotation schema",
            details={
                "received": annotation["schema_version"],
                "supported": GOLDEN_SCHEMA_VERSION,
            },
        )
    annotation_id = _require_annotation_id(annotation["annotation_id"])

    evaluation = _require_object(annotation["evaluation"], field="annotation.evaluation")
    _require_keys(
        evaluation,
        field="annotation.evaluation",
        required={
            "role",
            "question",
            "numeric_criterion",
            "effective_sampling_unit",
            "claim_ceiling",
        },
    )
    if evaluation["role"] != "development_regression":
        raise GoldenEvaluationError(
            "checked-in or private pilot goldens must declare development_regression"
        )
    if evaluation["question"] != GOLDEN_QUESTION:
        raise GoldenEvaluationError(
            f"evaluation.question must be {GOLDEN_QUESTION}"
        )
    if evaluation["numeric_criterion"] != "all_checks_pass":
        raise GoldenEvaluationError(
            "numeric_criterion must be all_checks_pass for extraction goldens"
        )
    if evaluation["effective_sampling_unit"] != "source_document":
        raise GoldenEvaluationError(
            "effective_sampling_unit must be source_document"
        )
    if evaluation["claim_ceiling"] != GOLDEN_CLAIM_CEILING:
        raise GoldenEvaluationError(
            f"evaluation.claim_ceiling must be {GOLDEN_CLAIM_CEILING}"
        )
    normalized_evaluation = {
        "role": "development_regression",
        "question": GOLDEN_QUESTION,
        "numeric_criterion": "all_checks_pass",
        "effective_sampling_unit": "source_document",
        "claim_ceiling": GOLDEN_CLAIM_CEILING,
    }

    provenance = _require_object(annotation["provenance"], field="annotation.provenance")
    _require_keys(
        provenance,
        field="annotation.provenance",
        required={"source_class", "contains_source_content"},
    )
    if provenance["source_class"] not in {"synthetic", "private_resident_regression"}:
        raise GoldenEvaluationError(
            "provenance.source_class must be synthetic or private_resident_regression"
        )
    if provenance["contains_source_content"] is not False:
        raise GoldenEvaluationError(
            "golden annotations may not contain copied source content"
        )

    subject = _require_object(annotation["subject"], field="annotation.subject")
    _require_keys(
        subject,
        field="annotation.subject",
        required={"corpus_id", "document_id", "revision_sha256"},
    )
    normalized_subject = {
        "corpus_id": _require_text(
            subject["corpus_id"], field="annotation.subject.corpus_id", max_length=128
        ),
        "document_id": _require_text(
            subject["document_id"],
            field="annotation.subject.document_id",
            max_length=200,
        ),
        "revision_sha256": _require_sha256(
            subject["revision_sha256"],
            field="annotation.subject.revision_sha256",
        ),
    }

    expected = _require_object(
        annotation["expected_projection"],
        field="annotation.expected_projection",
    )
    _require_keys(
        expected,
        field="annotation.expected_projection",
        required={
            "adapter_id",
            "adapter_version",
            "config_hash",
            "completeness_state",
        },
    )
    if expected["completeness_state"] not in {"complete", "partial"}:
        raise GoldenEvaluationError(
            "expected_projection.completeness_state must be complete or partial"
        )
    normalized_expected = {
        "adapter_id": _require_text(
            expected["adapter_id"],
            field="annotation.expected_projection.adapter_id",
            max_length=300,
        ),
        "adapter_version": _require_text(
            expected["adapter_version"],
            field="annotation.expected_projection.adapter_version",
            max_length=300,
        ),
        "config_hash": _require_sha256(
            expected["config_hash"],
            field="annotation.expected_projection.config_hash",
        ),
        "completeness_state": expected["completeness_state"],
    }

    criteria = _require_object(annotation["criteria"], field="annotation.criteria")
    _require_keys(
        criteria,
        field="annotation.criteria",
        required={"unit_count", "projection_observation_sha256"},
        optional={
            "unit_type_counts",
            "derivation_method_counts",
            "geometry_coverage_min",
            "confidence_coverage_min",
            "required_issue_codes",
            "forbidden_issue_codes",
            "current_source_observation_required",
        },
    )
    normalized_criteria: dict = {
        "unit_count": _validate_count_rule(
            criteria["unit_count"], field="annotation.criteria.unit_count"
        ),
        "projection_observation_sha256": _require_sha256(
            criteria["projection_observation_sha256"],
            field="annotation.criteria.projection_observation_sha256",
        ),
    }
    for key in ("unit_type_counts", "derivation_method_counts"):
        values = criteria.get(key, {})
        values = _require_object(values, field=f"annotation.criteria.{key}")
        normalized_criteria[key] = {
            _require_text(name, field=f"annotation.criteria.{key}.name", max_length=200):
            _validate_count_rule(
                rule,
                field=f"annotation.criteria.{key}.{name}",
            )
            for name, rule in values.items()
        }
    for key in ("geometry_coverage_min", "confidence_coverage_min"):
        if key in criteria:
            normalized_criteria[key] = _require_ratio(
                criteria[key], field=f"annotation.criteria.{key}"
            )
    for key in ("required_issue_codes", "forbidden_issue_codes"):
        values = criteria.get(key, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise GoldenEvaluationError(f"annotation.criteria.{key} must be a string list")
        if len(values) != len(set(values)):
            raise GoldenEvaluationError(
                f"annotation.criteria.{key} may not contain duplicates"
            )
        normalized_criteria[key] = sorted(values)
    required_observation = criteria.get("current_source_observation_required", True)
    if not isinstance(required_observation, bool):
        raise GoldenEvaluationError(
            "criteria.current_source_observation_required must be boolean"
        )
    normalized_criteria["current_source_observation_required"] = required_observation

    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "annotation_id": annotation_id,
        "evaluation": normalized_evaluation,
        "provenance": dict(provenance),
        "subject": normalized_subject,
        "expected_projection": normalized_expected,
        "criteria": normalized_criteria,
    }


def load_golden_annotation(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenEvaluationError(
            "could not read golden annotation",
            details={
                "path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
                "error_type": type(exc).__name__,
            },
        ) from exc
    return validate_golden_annotation(payload)


def annotation_sha256(annotation: dict) -> str:
    return hashlib.sha256(encode_json(annotation).encode()).hexdigest()


def _stable_issue_value(value: object) -> object:
    if isinstance(value, dict):
        stable = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized_key = key.casefold().replace("-", "_")
            if (
                normalized_key in UNSTABLE_ISSUE_DETAIL_KEYS
                or normalized_key.endswith(
                    (
                        "_path",
                        "_uri",
                        "_url",
                        "_timestamp",
                        "_created_at",
                        "_updated_at",
                        "_started_at",
                        "_completed_at",
                    )
                )
            ):
                continue
            stable[key] = _stable_issue_value(child)
        return stable
    if isinstance(value, list):
        return [_stable_issue_value(child) for child in value]
    if isinstance(value, str) and (
        value.startswith(("/", "~/", "\\\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or "://" in value
        or ISO_TIMESTAMP_RE.fullmatch(value) is not None
    ):
        return "<unstable-string-redacted>"
    return value


def _canonical_issue(issue: dict, *, field: str) -> dict:
    issue = _require_object(issue, field=field)
    details = issue.get("details", {})
    if not isinstance(details, dict):
        raise GoldenEvaluationError(f"{field}.details must be an object")
    structural_locator = issue.get("structural_locator", {})
    if not isinstance(structural_locator, dict):
        raise GoldenEvaluationError(f"{field}.structural_locator must be an object")
    return {
        "stage": str(issue.get("stage", "")),
        "code": str(issue.get("code", "")),
        "severity": str(issue.get("severity", "")),
        "details": _stable_issue_value(details),
        "structural_locator": structural_locator,
    }


def projection_observation_sha256(
    units: list[dict],
    *,
    projection_issues: list[dict] | None = None,
) -> str:
    canonical_units = [
        {
            "ordinal": int(unit["ordinal"]),
            "unit_type": str(unit["unit_type"]),
            "content_sha256": _require_sha256(
                unit["content_sha256"],
                field="projection_observation.content_sha256",
            ),
            "derivation_method": str(unit["derivation_method"]),
            "structure_path": unit.get("structure_path", {}),
            "geometry": unit.get("geometry", {}),
            "confidence": unit.get("confidence"),
            "quality_flags": unit.get("quality_flags", []),
            "issues": [
                _canonical_issue(
                    issue,
                    field=f"projection_observation.units[{unit_index}].issues[{issue_index}]",
                )
                for issue_index, issue in enumerate(unit.get("issues", []))
            ],
        }
        for unit_index, unit in enumerate(units)
    ]
    canonical_observation = {
        "units": canonical_units,
        "projection_issues": [
            _canonical_issue(
                issue,
                field=f"projection_observation.projection_issues[{issue_index}]",
            )
            for issue_index, issue in enumerate(projection_issues or [])
        ],
    }
    return hashlib.sha256(encode_json(canonical_observation).encode()).hexdigest()


def _count_checks(name: str, actual: int, rule: dict) -> list[dict]:
    checks = []
    for comparator, expected in rule.items():
        if comparator == "exact":
            passed = actual == expected
        elif comparator == "minimum":
            passed = actual >= expected
        else:
            passed = actual <= expected
        checks.append(
            {
                "check": f"{name}.{comparator}",
                "passed": passed,
                "actual": actual,
                "expected": expected,
            }
        )
    return checks


def evaluate_projection_observation(annotation: dict, observation: dict) -> dict:
    annotation = validate_golden_annotation(annotation)
    checks = []

    for key in ("corpus_id", "document_id", "revision_sha256"):
        passed = observation.get(key) == annotation["subject"][key]
        checks.append(
            {
                "check": f"subject.{key}",
                "passed": passed,
                "actual": "match" if passed else "mismatch",
                "expected": "match",
            }
        )
    for key in ("adapter_id", "adapter_version", "config_hash", "completeness_state"):
        checks.append(
            {
                "check": f"projection.{key}",
                "passed": observation.get(key) == annotation["expected_projection"][key],
                "actual": observation.get(key),
                "expected": annotation["expected_projection"][key],
            }
        )

    criteria = annotation["criteria"]
    checks.extend(
        _count_checks("unit_count", int(observation.get("unit_count", 0)), criteria["unit_count"])
    )
    checks.append(
        {
            "check": "projection_observation_sha256",
            "passed": (
                observation.get("projection_observation_sha256")
                == criteria["projection_observation_sha256"]
            ),
            "actual": (
                "match"
                if observation.get("projection_observation_sha256")
                == criteria["projection_observation_sha256"]
                else "mismatch"
            ),
            "expected": "match",
        }
    )
    for category, observation_key in (
        ("unit_type_counts", "unit_type_counts"),
        ("derivation_method_counts", "derivation_method_counts"),
    ):
        actual_counts = observation.get(observation_key, {})
        for name, rule in criteria[category].items():
            checks.extend(
                _count_checks(
                    f"{category}.{name}",
                    int(actual_counts.get(name, 0)),
                    rule,
                )
            )
    for criterion, observation_key in (
        ("geometry_coverage_min", "geometry_coverage"),
        ("confidence_coverage_min", "confidence_coverage"),
    ):
        if criterion in criteria:
            actual = float(observation.get(observation_key, 0))
            expected = criteria[criterion]
            checks.append(
                {
                    "check": criterion,
                    "passed": actual >= expected,
                    "actual": actual,
                    "expected": expected,
                }
            )
    issue_codes = set(observation.get("issue_codes", []))
    for code in criteria["required_issue_codes"]:
        checks.append(
            {
                "check": f"required_issue_codes.{code}",
                "passed": code in issue_codes,
                "actual": code in issue_codes,
                "expected": True,
            }
        )
    for code in criteria["forbidden_issue_codes"]:
        checks.append(
            {
                "check": f"forbidden_issue_codes.{code}",
                "passed": code not in issue_codes,
                "actual": code in issue_codes,
                "expected": False,
            }
        )
    if criteria["current_source_observation_required"]:
        checks.append(
            {
                "check": "current_source_observation",
                "passed": observation.get("source_observation_current") is True,
                "actual": bool(observation.get("source_observation_current")),
                "expected": True,
            }
        )
    checks.append(
        {
            "check": "stored_unit_content_hashes",
            "passed": observation.get("unit_content_hashes_current") is True,
            "actual": bool(observation.get("unit_content_hashes_current")),
            "expected": True,
        }
    )

    failures = [check for check in checks if not check["passed"]]
    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "annotation_id": annotation["annotation_id"],
        "annotation_sha256": annotation_sha256(annotation),
        "evaluation": annotation["evaluation"],
        "passed": not failures,
        "check_count": len(checks),
        "failed_check_count": len(failures),
        "checks": checks,
        "claim_ceiling": GOLDEN_CLAIM_CEILING,
        "evaluated_dimensions": [
            "source_freshness",
            "projection_identity",
            "canonical_unit_observation",
            "unit_inventory",
            "issue_contract",
        ],
        "not_evaluated": [
            "semantic_correctness",
            "corpus_coverage",
            "cross_document_generalization",
        ],
    }
