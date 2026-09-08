"""Integrity helpers for the external LLM review handoff.

The mobile executor produces the evidence and the row-level review queue.
Codex or OpenCode only returns semantic verdicts.  This module gives all
three artifacts stable bindings so a stale reviewer output, changed execution
record, or replaced screenshot cannot be silently merged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT_SCHEMA_VERSION = "1.1"
SUPPORTED_AGENTS = {"codex": "Codex", "opencode": "OpenCode"}


class ReviewContractError(ValueError):
    """Raised when a queue/review artifact does not match its source run."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document_sha256(document: Mapping[str, Any] | list[Any]) -> str:
    return sha256_value(document)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def run_id_for(document: Mapping[str, Any], run_dir: str | Path) -> str:
    manifest = document.get("execution_manifest")
    if isinstance(manifest, Mapping) and _text(manifest.get("run_id")):
        return _text(manifest["run_id"])
    if _text(document.get("run_id")):
        return _text(document["run_id"])
    # Direct library callers without a runtime manifest still get a stable
    # binding for that run directory.  Real executions always inject run_id.
    root = str(Path(run_dir).expanduser().resolve()).replace("\\", "/")
    return f"legacy-{hashlib.sha256(root.encode('utf-8')).hexdigest()[:16]}"


def _evidence_path(value: Any, root: Path) -> Path | None:
    if isinstance(value, Mapping):
        value = value.get("path") or value.get("file")
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def evidence_fingerprint(value: Any, root: str | Path) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    path = _evidence_path(value, base)
    if path is None:
        return {"path": "", "exists": False, "size": None, "sha256": None}
    item: dict[str, Any] = {
        "path": str(path).replace("\\", "/"),
        "exists": path.is_file(),
        "size": None,
        "sha256": None,
    }
    if path.is_file():
        data = path.read_bytes()
        item["size"] = len(data)
        item["sha256"] = sha256_bytes(data)
    return item


def evidence_values(record: Mapping[str, Any]) -> list[Any]:
    return list(record.get("evidence") or record.get("evidence_paths") or [])


def evidence_fingerprints(record: Mapping[str, Any], root: str | Path) -> list[dict[str, Any]]:
    return [evidence_fingerprint(value, root) for value in evidence_values(record)]


def evidence_manifest(records: list[Mapping[str, Any]], root: str | Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in records:
        case_id = _text(record.get("case_id")) or f"{record.get('sheet', '')}!{record.get('row', '')}"
        result.append(
            {
                "case_id": case_id,
                "fingerprints": evidence_fingerprints(record, root),
            }
        )
    return result


def queue_payload_without_binding(queue: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(queue)
    binding = payload.pop("queue_binding", None)
    if isinstance(binding, Mapping):
        # Keep the run/evidence/source bindings inside the digest.  Only the
        # self-referential digest and its derived short id are excluded.
        binding_payload = dict(binding)
        binding_payload.pop("queue_sha256", None)
        binding_payload.pop("queue_id", None)
        payload["queue_binding"] = binding_payload
    return payload


def queue_sha256(queue: Mapping[str, Any]) -> str:
    return sha256_value(queue_payload_without_binding(queue))


def make_queue_binding(
    document: Mapping[str, Any],
    *,
    run_dir: str | Path,
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    execution_digest = document_sha256(document)
    return {
        "run_id": run_id_for(document, root),
        "execution_document_sha256": execution_digest,
        "evidence_manifest_sha256": sha256_value(evidence_items),
        "run_dir": str(root).replace("\\", "/"),
    }


def normalize_agent(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("agent") or value.get("provider")
    normalized = _text(value).casefold()
    if normalized not in SUPPORTED_AGENTS:
        raise ReviewContractError("reviewer agent 必须是 Codex 或 OpenCode")
    return SUPPORTED_AGENTS[normalized]


def validate_agent_metadata(review_document: Mapping[str, Any]) -> dict[str, str]:
    agent = review_document.get("agent")
    name = normalize_agent(agent)
    model = ""
    prompt_version = ""
    if isinstance(agent, Mapping):
        model = _text(agent.get("model"))
        prompt_version = _text(agent.get("prompt_version"))
    model = model or _text(review_document.get("model"))
    prompt_version = prompt_version or _text(review_document.get("prompt_version"))
    if not model:
        raise ReviewContractError("llm_reviews.json 缺少 agent.model")
    if not prompt_version:
        raise ReviewContractError("llm_reviews.json 缺少 agent.prompt_version")
    return {"name": name, "model": model, "prompt_version": prompt_version}


def validate_queue_integrity(
    document: Mapping[str, Any],
    queue: Mapping[str, Any],
    *,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate that the queue is intact and still describes ``document``."""

    binding = queue.get("queue_binding")
    if not isinstance(binding, Mapping):
        raise ReviewContractError("llm_review_queue.json 缺少 queue_binding")
    expected_queue_hash = queue_sha256(queue)
    if _text(binding.get("queue_sha256")) != expected_queue_hash:
        raise ReviewContractError("llm_review_queue.json 内容摘要不匹配，队列可能已被替换或修改")
    if _text(binding.get("queue_id")) != f"queue-{expected_queue_hash[:16]}":
        raise ReviewContractError("llm_review_queue.json 的 queue_id 与队列摘要不匹配")
    actual_digest = document_sha256(document)
    if _text(binding.get("execution_document_sha256")) != actual_digest:
        raise ReviewContractError("执行记录摘要不匹配，execution_records 可能已被替换或修改")

    root = Path(_text(queue.get("run_dir")) or _text(binding.get("run_dir")) or ".").expanduser().resolve()
    manifest = document.get("execution_manifest")
    if input_path is not None and (
        not isinstance(manifest, Mapping) or not _text(manifest.get("run_id"))
    ):
        raise ReviewContractError("执行记录缺少本次运行的 execution_manifest.run_id")
    expected_run_id = run_id_for(document, root)
    if _text(binding.get("run_id")) != expected_run_id:
        raise ReviewContractError("运行 run_id 不匹配，复核队列不属于本次执行")

    records = [item for item in document.get("cases", []) if isinstance(item, Mapping)]
    expected_evidence = evidence_manifest(records, root)
    if _text(binding.get("evidence_manifest_sha256")) != sha256_value(expected_evidence):
        raise ReviewContractError("截图证据摘要不匹配，截图路径或内容可能已被替换")

    queue_cases = [item for item in queue.get("cases", []) if isinstance(item, Mapping)]
    queue_evidence = [
        {"case_id": item.get("case_id"), "fingerprints": item.get("evidence_fingerprints") or []}
        for item in queue_cases
    ]
    if queue_evidence != expected_evidence:
        raise ReviewContractError("队列中的截图指纹与当前执行记录不一致")

    if input_path is not None:
        path = Path(input_path).expanduser().resolve()
        expected_file_hash = binding.get("execution_source_file_sha256")
        if not expected_file_hash:
            raise ReviewContractError("当前复核队列缺少执行记录源文件摘要")
        if not path.is_file() or sha256_bytes(path.read_bytes()) != expected_file_hash:
            raise ReviewContractError("执行记录源文件摘要不匹配，输入文件可能已被替换")

    return dict(binding)


def validate_review_binding(
    review_document: Mapping[str, Any],
    queue_binding: Mapping[str, Any],
) -> dict[str, str]:
    metadata = validate_agent_metadata(review_document)
    binding = review_document.get("review_binding")
    if not isinstance(binding, Mapping):
        raise ReviewContractError("llm_reviews.json 缺少 review_binding")
    for field in (
        "run_id",
        "queue_id",
        "queue_sha256",
        "execution_document_sha256",
        "evidence_manifest_sha256",
    ):
        if _text(binding.get(field)) != _text(queue_binding.get(field)):
            raise ReviewContractError(f"llm_reviews.json 的 {field} 与当前队列不匹配")
    return metadata
