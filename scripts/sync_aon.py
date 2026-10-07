#!/usr/bin/env python3
"""Fetch and normalize the public Archives of Nethys Pathfinder 2e search index.

The generated library is reference data only. It does not claim that any indexed
rule has executable mechanics in PathfinderEngine.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

VERSION = 1
DEFAULT_ROOT = "https://elasticsearch.aonprd.com"
DEFAULT_INDEX = "aon"
USER_AGENT = "PathfinderEngine AoN index sync (github.com/jhdnd35e/PathfinderEngine)"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


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


def slug(value: Any, fallback: str = "entry") -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or fallback


def search_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(text.replace("’", "'").replace("–", "-").replace("—", "-").split())


def list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v)]
    return [str(value)]


def make_reference(category: str, aon_id: str) -> str:
    kind = slug(category, "uncategorized")
    identity = slug(aon_id, "entry")[:72]
    suffix = hashlib.sha1(f"{category}\0{aon_id}".encode("utf-8")).hexdigest()[:12]
    return f"aon:{kind}:{identity}-{suffix}"


def request_json(
    method: str,
    url: str,
    payload: Any | None = None,
    *,
    attempts: int = 5,
) -> Any:
    body = None if payload is None else canonical(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE or attempt + 1 >= attempts:
                detail = exc.read().decode("utf-8", "replace")[:2000]
                raise RuntimeError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise RuntimeError(f"{method} {url} failed: {exc}") from exc

        time.sleep(min(16, 2**attempt))

    raise RuntimeError(f"{method} {url} failed: {last_error}")


def open_pit(root: str, index: str, keep_alive: str) -> str | None:
    url = f"{root.rstrip('/')}/{urllib.parse.quote(index, safe='')}/_pit?keep_alive={urllib.parse.quote(keep_alive)}"
    try:
        response = request_json("POST", url)
    except RuntimeError as exc:
        print(f"PIT unavailable; using stable id search_after fallback: {exc}")
        return None
    pit_id = response.get("id") if isinstance(response, dict) else None
    return pit_id if isinstance(pit_id, str) and pit_id else None


def close_pit(root: str, pit_id: str) -> None:
    try:
        request_json("DELETE", f"{root.rstrip('/')}/_pit", {"id": pit_id}, attempts=2)
    except Exception as exc:
        print(f"Warning: failed to close point-in-time snapshot: {exc}")


def fetch_all(
    root: str,
    index: str,
    page_size: int,
    keep_alive: str,
    delay_ms: int,
) -> tuple[list[dict[str, Any]], str]:
    pit_id = open_pit(root, index, keep_alive)
    method = "pit" if pit_id else "search_after"
    endpoint = (
        f"{root.rstrip('/')}/_search"
        if pit_id
        else f"{root.rstrip('/')}/{urllib.parse.quote(index, safe='')}/_search"
    )

    results: list[dict[str, Any]] = []
    search_after: list[Any] | None = None
    expected_total: int | None = None

    try:
        while True:
            body: dict[str, Any] = {
                "size": page_size,
                "query": {"match_all": {}},
                "track_total_hits": True if expected_total is None else False,
            }
            if pit_id:
                body["pit"] = {"id": pit_id, "keep_alive": keep_alive}
                body["sort"] = [{"_shard_doc": "asc"}]
            else:
                body["sort"] = [{"id.keyword": "asc"}]

            if search_after is not None:
                body["search_after"] = search_after

            page = request_json("POST", endpoint, body)
            if not isinstance(page, dict) or "hits" not in page:
                raise RuntimeError("AoN Elasticsearch response does not contain hits")

            hits_wrapper = page.get("hits") or {}
            hits = hits_wrapper.get("hits") or []
            if expected_total is None:
                total = hits_wrapper.get("total")
                if isinstance(total, dict):
                    expected_total = int(total.get("value", 0))
                elif isinstance(total, int):
                    expected_total = total

            if not hits:
                break

            for hit in hits:
                if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
                    raise RuntimeError("Encountered Elasticsearch hit without an object _source")
                results.append(hit)

            last_sort = hits[-1].get("sort")
            if not isinstance(last_sort, list) or not last_sort:
                raise RuntimeError("Elasticsearch page is missing search_after sort values")
            search_after = last_sort

            if len(results) % 5000 < page_size:
                total_text = f"/{expected_total}" if expected_total is not None else ""
                print(f"  fetched {len(results)}{total_text} entries")

            if len(hits) < page_size:
                break
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
    finally:
        if pit_id:
            close_pit(root, pit_id)

    if expected_total is not None and len(results) != expected_total:
        raise RuntimeError(
            f"Snapshot count mismatch: fetched {len(results)} entries, Elasticsearch reported {expected_total}"
        )
    return results, method


def normalize_hits(
    hits: Iterable[dict[str, Any]],
    fetched_at: str,
    retrieved_from: str,
) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    by_aon_id: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)

    for hit in hits:
        source = hit["_source"]
        issues: list[str] = []

        raw_aon_id = source.get("id")
        if raw_aon_id is None or str(raw_aon_id) == "":
            raw_aon_id = hit.get("_id")
            issues.append("missing_upstream_id_used_elasticsearch_hit_id")
        aon_id = str(raw_aon_id)

        raw_category = source.get("category")
        if raw_category is None or str(raw_category).strip() == "":
            category = "uncategorized"
            issues.append("missing_upstream_category")
        else:
            category = str(raw_category)

        raw_name = source.get("name")
        if raw_name is None or str(raw_name).strip() == "":
            name = aon_id
            issues.append("missing_upstream_name_used_id")
        else:
            name = str(raw_name).strip()

        legacy_ids = list_of_strings(source.get("legacy_id"))
        remaster_ids = list_of_strings(source.get("remaster_id"))
        edition = "legacy" if remaster_ids else "remastered" if legacy_ids else "single"

        url = source.get("url")
        if url is not None and not isinstance(url, str):
            issues.append("non_string_upstream_url")
            url = None

        record = {
            "schema_version": VERSION,
            "ruleset": "pathfinder-2e",
            "reference": make_reference(category, aon_id),
            "source_id": "archives-of-nethys",
            "aon_id": aon_id,
            "category": category,
            "kind": slug(category, "uncategorized"),
            "name": name,
            "search_key": search_key(name),
            "aliases": [],
            "edition": edition,
            "fields": source,
            "relations": {
                "legacy_ids": legacy_ids,
                "remaster_ids": remaster_ids,
                "replaces": [],
                "superseded_by": [],
                "unresolved_legacy_ids": [],
                "unresolved_remaster_ids": [],
            },
            "provenance": {
                "retrieved_from": retrieved_from,
                "source_index": "aon",
                "elasticsearch_hit_id": str(hit.get("_id", "")),
                "source_record_sha256": sha256_value(source),
                "fetched_at": fetched_at,
                "url": url,
                "primary_source": source.get("primary_source"),
                "release_date": source.get("release_date"),
            },
            "coverage": {
                "normalization": "lossless_structural",
                "mechanics_implementation": "not_implemented",
                "issues": issues,
            },
        }
        seeds.append(record)
        by_aon_id[aon_id].append(record)

    duplicate_ids = {key for key, rows in by_aon_id.items() if len(rows) != 1}
    for aon_id in duplicate_ids:
        for record in by_aon_id[aon_id]:
            record["coverage"]["issues"].append("duplicate_aon_id")

    unique_by_id = {
        aon_id: rows[0]
        for aon_id, rows in by_aon_id.items()
        if len(rows) == 1
    }

    for record in seeds:
        rel = record["relations"]
        aliases: set[str] = set()

        for legacy_id in rel["legacy_ids"]:
            target = unique_by_id.get(legacy_id)
            if target is None:
                rel["unresolved_legacy_ids"].append(legacy_id)
                continue
            rel["replaces"].append(target["reference"])
            if target["name"] != record["name"]:
                aliases.add(target["name"])

        for remaster_id in rel["remaster_ids"]:
            target = unique_by_id.get(remaster_id)
            if target is None:
                rel["unresolved_remaster_ids"].append(remaster_id)
                continue
            rel["superseded_by"].append(target["reference"])

        rel["replaces"] = sorted(set(rel["replaces"]))
        rel["superseded_by"] = sorted(set(rel["superseded_by"]))
        rel["unresolved_legacy_ids"] = sorted(set(rel["unresolved_legacy_ids"]))
        rel["unresolved_remaster_ids"] = sorted(set(rel["unresolved_remaster_ids"]))
        record["aliases"] = sorted(aliases, key=search_key)

    references = [r["reference"] for r in seeds]
    if len(references) != len(set(references)):
        raise RuntimeError("Generated reference collision")
    return seeds


def shard_rows(directory: Path, rows: list[dict[str, Any]], max_bytes: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    chunk: list[str] = []
    size = 0
    part = 0

    def flush() -> None:
        nonlocal chunk, size, part
        if not chunk:
            return
        path = directory / f"{part:04d}.jsonl"
        path.write_text("".join(chunk), encoding="utf-8")
        files.append(path)
        part += 1
        chunk = []
        size = 0

    for row in rows:
        line = canonical(row) + "\n"
        n = len(line.encode("utf-8"))
        if chunk and size + n > max_bytes:
            flush()
        chunk.append(line)
        size += n
    flush()
    return files


def compact_index_row(record: dict[str, Any]) -> dict[str, Any]:
    fields = record["fields"]
    traits = fields.get("trait")
    if traits is None:
        traits = fields.get("trait_raw")
    return {
        "reference": record["reference"],
        "aon_id": record["aon_id"],
        "name": record["name"],
        "aliases": record["aliases"],
        "category": record["category"],
        "kind": record["kind"],
        "edition": record["edition"],
        "level": fields.get("level"),
        "rarity": fields.get("rarity"),
        "traits": traits,
        "type": fields.get("type"),
        "primary_source": fields.get("primary_source"),
        "source": fields.get("source"),
        "url": fields.get("url"),
        "exclude_from_search": fields.get("exclude_from_search", False),
    }


def file_digest(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_readme(root: Path) -> None:
    (root / "README.md").write_text(
        """# Generated Archives of Nethys Pathfinder 2e reference library

This directory is generated by `scripts/sync_aon.py`.

- `definitions/<category>/*.jsonl` contains the complete normalized records.
- `index/entries.jsonl` is the compact retrieval index.
- `index/categories.json` contains category counts.
- `index/remaster-map.json` contains resolved and unresolved Legacy/Remaster links.
- `catalog.json` records snapshot metadata and integrity hashes.

Every definition preserves the complete upstream Elasticsearch `_source` object in
`fields`. Do not hand-edit generated files; update the ingestion code and rebuild.
""",
        encoding="utf-8",
    )


def build_snapshot(
    records: list[dict[str, Any]],
    out: Path,
    *,
    endpoint: str,
    index: str,
    fetched_at: str,
    pagination: str,
    max_shard_bytes: int,
) -> None:
    out_parent = out.parent
    out_parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{out.name}.building-", dir=out_parent))

    try:
        write_readme(temp)
        by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for record in records:
            by_kind[record["kind"]].append(record)

        for kind, rows in sorted(by_kind.items()):
            rows.sort(key=lambda r: (r["search_key"], r["aon_id"], r["reference"]))
            shard_rows(temp / "definitions" / kind, rows, max_shard_bytes)

        index_dir = temp / "index"
        index_dir.mkdir(parents=True, exist_ok=True)

        compact_rows = sorted(
            (compact_index_row(record) for record in records),
            key=lambda r: (search_key(r["name"]), r["aon_id"], r["reference"]),
        )
        (index_dir / "entries.jsonl").write_text(
            "".join(canonical(row) + "\n" for row in compact_rows),
            encoding="utf-8",
        )

        category_counts = collections.Counter(record["category"] for record in records)
        kind_counts = collections.Counter(record["kind"] for record in records)
        categories = {
            "total_entries": len(records),
            "categories": [
                {"category": key, "kind": slug(key, "uncategorized"), "count": value}
                for key, value in sorted(category_counts.items(), key=lambda item: (search_key(item[0]), item[0]))
            ],
            "kinds": [
                {"kind": key, "count": value}
                for key, value in sorted(kind_counts.items())
            ],
        }
        (index_dir / "categories.json").write_text(
            json.dumps(categories, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        remaster_rows = []
        for record in sorted(records, key=lambda r: r["reference"]):
            rel = record["relations"]
            if rel["legacy_ids"] or rel["remaster_ids"]:
                remaster_rows.append(
                    {
                        "reference": record["reference"],
                        "aon_id": record["aon_id"],
                        "name": record["name"],
                        "edition": record["edition"],
                        **rel,
                    }
                )
        (index_dir / "remaster-map.json").write_text(
            json.dumps(remaster_rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        assets = [
            path for path in temp.rglob("*")
            if path.is_file() and path.name != "catalog.json"
        ]
        file_entries = []
        for path in sorted(assets):
            digest = file_digest(path)
            digest["path"] = str(path.relative_to(temp))
            file_entries.append(digest)

        unresolved_legacy = sum(len(r["relations"]["unresolved_legacy_ids"]) for r in records)
        unresolved_remaster = sum(len(r["relations"]["unresolved_remaster_ids"]) for r in records)
        catalog = {
            "schema_version": VERSION,
            "ruleset": "pathfinder-2e",
            "source_id": "archives-of-nethys",
            "source_endpoint": endpoint,
            "source_index": index,
            "fetched_at": fetched_at,
            "pagination": pagination,
            "entry_count": len(records),
            "category_count": len(category_counts),
            "kind_count": len(kind_counts),
            "legacy_count": sum(r["edition"] == "legacy" for r in records),
            "remastered_count": sum(r["edition"] == "remastered" for r in records),
            "single_count": sum(r["edition"] == "single" for r in records),
            "unresolved_legacy_links": unresolved_legacy,
            "unresolved_remaster_links": unresolved_remaster,
            "files": file_entries,
        }
        (temp / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        backup = out.with_name(out.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        if out.exists():
            out.rename(backup)
        temp.rename(out)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("AON_ES_ROOT", DEFAULT_ROOT))
    parser.add_argument("--index", default=os.environ.get("AON_ES_INDEX", DEFAULT_INDEX))
    parser.add_argument("--output", type=Path, default=Path("data/source"))
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-shard-bytes", type=int, default=4_000_000)
    parser.add_argument("--keep-alive", default="5m")
    parser.add_argument("--delay-ms", type=int, default=150)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not (1 <= args.page_size <= 5000):
        raise SystemExit("--page-size must be between 1 and 5000")
    if args.max_shard_bytes < 100_000:
        raise SystemExit("--max-shard-bytes is too small")

    fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    root = args.root.rstrip("/")
    retrieved_from = f"{root}/{args.index}"

    print(f"Fetching Pathfinder 2e index from {retrieved_from}")
    hits, pagination = fetch_all(
        root,
        args.index,
        args.page_size,
        args.keep_alive,
        args.delay_ms,
    )
    print(f"Normalizing {len(hits)} entries")
    records = normalize_hits(hits, fetched_at, retrieved_from)

    print(f"Writing {args.output}")
    build_snapshot(
        records,
        args.output,
        endpoint=root,
        index=args.index,
        fetched_at=fetched_at,
        pagination=pagination,
        max_shard_bytes=args.max_shard_bytes,
    )
    print(f"Done: {len(records)} entries across {len(set(r['kind'] for r in records))} kinds")


if __name__ == "__main__":
    main()
