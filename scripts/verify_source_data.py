#!/usr/bin/env python3
"""Verify a generated PathfinderEngine Archives of Nethys source snapshot."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED = {
    "schema_version",
    "ruleset",
    "reference",
    "source_id",
    "aon_id",
    "category",
    "kind",
    "name",
    "search_key",
    "aliases",
    "edition",
    "fields",
    "relations",
    "provenance",
    "coverage",
}


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def verify_record(record: dict[str, Any], path: Path, line_no: int) -> None:
    missing = REQUIRED - record.keys()
    extra = record.keys() - REQUIRED
    if missing:
        raise ValueError(f"{path}:{line_no}: missing keys {sorted(missing)}")
    if extra:
        raise ValueError(f"{path}:{line_no}: unexpected keys {sorted(extra)}")
    if record["schema_version"] != 1:
        raise ValueError(f"{path}:{line_no}: unsupported schema_version")
    if record["ruleset"] != "pathfinder-2e":
        raise ValueError(f"{path}:{line_no}: wrong ruleset")
    if record["source_id"] != "archives-of-nethys":
        raise ValueError(f"{path}:{line_no}: wrong source_id")
    if record["edition"] not in {"legacy", "remastered", "single"}:
        raise ValueError(f"{path}:{line_no}: invalid edition")
    if not isinstance(record["fields"], dict):
        raise ValueError(f"{path}:{line_no}: fields must be an object")
    if not isinstance(record["aliases"], list):
        raise ValueError(f"{path}:{line_no}: aliases must be an array")
    if len(record["aliases"]) != len(set(record["aliases"])):
        raise ValueError(f"{path}:{line_no}: duplicate aliases")

    provenance = record["provenance"]
    expected_hash = sha256_value(record["fields"])
    if provenance.get("source_record_sha256") != expected_hash:
        raise ValueError(f"{path}:{line_no}: source record hash mismatch")

    relations = record["relations"]
    for key in (
        "legacy_ids",
        "remaster_ids",
        "replaces",
        "superseded_by",
        "unresolved_legacy_ids",
        "unresolved_remaster_ids",
    ):
        if not isinstance(relations.get(key), list):
            raise ValueError(f"{path}:{line_no}: relations.{key} must be an array")


def verify(root: Path) -> dict[str, Any]:
    catalog_path = root / "catalog.json"
    if not catalog_path.exists():
        raise ValueError(f"missing {catalog_path}")
    catalog = load_json(catalog_path)

    if catalog.get("schema_version") != 1:
        raise ValueError("catalog schema_version must be 1")
    if catalog.get("ruleset") != "pathfinder-2e":
        raise ValueError("catalog ruleset must be pathfinder-2e")
    if catalog.get("source_id") != "archives-of-nethys":
        raise ValueError("catalog source_id must be archives-of-nethys")

    listed = {item["path"]: item for item in catalog.get("files", [])}
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != "catalog.json"
    }
    if actual != set(listed):
        missing = sorted(set(listed) - actual)
        unlisted = sorted(actual - set(listed))
        raise ValueError(f"asset inventory mismatch; missing={missing[:10]} unlisted={unlisted[:10]}")

    for rel, meta in listed.items():
        path = root / rel
        size = path.stat().st_size
        if size != meta.get("bytes"):
            raise ValueError(f"{rel}: byte count mismatch")
        if sha256_file(path) != meta.get("sha256"):
            raise ValueError(f"{rel}: sha256 mismatch")

    definition_files = sorted((root / "definitions").glob("*/*.jsonl"))
    if not definition_files:
        raise ValueError("no definition shards found")

    references: set[str] = set()
    aon_ids: collections.Counter[str] = collections.Counter()
    categories: collections.Counter[str] = collections.Counter()
    kinds: collections.Counter[str] = collections.Counter()
    edition_counts: collections.Counter[str] = collections.Counter()
    relation_targets: list[tuple[str, str]] = []
    count = 0

    for path in definition_files:
        expected_kind = path.parent.name
        for line_no, record in iter_jsonl(path):
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_no}: record must be an object")
            verify_record(record, path, line_no)
            if record["kind"] != expected_kind:
                raise ValueError(f"{path}:{line_no}: kind does not match shard directory")
            if record["reference"] in references:
                raise ValueError(f"{path}:{line_no}: duplicate reference {record['reference']}")
            references.add(record["reference"])
            aon_ids[record["aon_id"]] += 1
            categories[record["category"]] += 1
            kinds[record["kind"]] += 1
            edition_counts[record["edition"]] += 1
            count += 1
            for key in ("replaces", "superseded_by"):
                relation_targets.extend((record["reference"], target) for target in record["relations"][key])

    for origin, target in relation_targets:
        if target not in references:
            raise ValueError(f"{origin}: relation target does not exist: {target}")

    if count != catalog.get("entry_count"):
        raise ValueError(f"entry_count mismatch: definitions={count} catalog={catalog.get('entry_count')}")
    if len(categories) != catalog.get("category_count"):
        raise ValueError("category_count mismatch")
    if len(kinds) != catalog.get("kind_count"):
        raise ValueError("kind_count mismatch")

    expected_editions = {
        "legacy": catalog.get("legacy_count"),
        "remastered": catalog.get("remastered_count"),
        "single": catalog.get("single_count"),
    }
    for key, expected in expected_editions.items():
        if edition_counts[key] != expected:
            raise ValueError(f"{key}_count mismatch")

    index_entries = root / "index" / "entries.jsonl"
    compact_count = sum(1 for _ in iter_jsonl(index_entries))
    if compact_count != count:
        raise ValueError(f"compact index count mismatch: {compact_count} != {count}")

    category_index = load_json(root / "index" / "categories.json")
    if category_index.get("total_entries") != count:
        raise ValueError("category index total_entries mismatch")
    indexed_categories = {
        item["category"]: item["count"]
        for item in category_index.get("categories", [])
    }
    if indexed_categories != dict(categories):
        raise ValueError("category index counts do not match definitions")

    duplicates = sorted(key for key, value in aon_ids.items() if value > 1)
    return {
        "entries": count,
        "categories": len(categories),
        "kinds": len(kinds),
        "duplicate_aon_ids": len(duplicates),
        "legacy": edition_counts["legacy"],
        "remastered": edition_counts["remastered"],
        "single": edition_counts["single"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data/source"))
    args = parser.parse_args()
    summary = verify(args.root)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
