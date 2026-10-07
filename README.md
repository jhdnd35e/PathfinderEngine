# PathfinderEngine

PathfinderEngine is a Pathfinder Second Edition counterpart to `DnDControllerV1`.

The project is being built in phases. Phase 0 establishes a reproducible published-rules
library sourced from the public Archives of Nethys search index. Later phases can layer
rules execution, character state, encounters, simulation, campaign state, generation,
and narrative/controller services on top of that immutable reference layer.

## Phase 0: Archives of Nethys reference data

The ingestion pipeline:

1. Reads the public Archives of Nethys Elasticsearch index at
   `https://elasticsearch.aonprd.com/aon`.
2. Takes a complete point-in-time snapshot when the endpoint supports PIT, with a
   `search_after` fallback if PIT is unavailable.
3. Preserves every returned `_source` object losslessly.
4. Wraps each entry in a consistent Pathfinder 2e envelope.
5. Records provenance, hashes, category, source, level, rarity, traits, URL, release
   date, and Legacy/Remaster relationships when AoN exposes them.
6. Reconstructs legacy-name aliases for remastered entries from AoN's
   `legacy_id`/`remaster_id` relationships.
7. Writes bounded JSONL shards under `data/source/definitions/<category>/`.
8. Builds a compact local index and catalog with integrity hashes.
9. Verifies the snapshot before it is accepted.

The generated reference data is deliberately separate from executable mechanics.
A source entry being indexed does **not** mean PathfinderEngine has implemented that
rule yet.

## Run a sync

Python 3.11+; standard library only:

```bash
python scripts/sync_aon.py
python scripts/verify_source_data.py data/source
python -m unittest discover -s tests -p 'test_*.py'
```

The GitHub Actions workflow in `.github/workflows/sync-aon.yml` can also refresh the
snapshot. It is designed to make a bounded number of requests to the AoN search
backend rather than crawling thousands of HTML pages.

## Data layout

```text
data/source/
  catalog.json
  index/
    entries.jsonl
    categories.json
    remaster-map.json
  definitions/
    <category>/
      0000.jsonl
      0001.jsonl
      ...
```

Each definition uses `schema/source-definition-v1.schema.json`. The complete AoN
record remains in `fields`, while common PF2e identity and provenance fields are
lifted into the envelope for stable querying.

## Remaster policy

PathfinderEngine does not delete Legacy material. Records are tagged as:

- `legacy`: AoN says the entry has a `remaster_id` replacement.
- `remastered`: AoN says the entry has one or more `legacy_id` predecessors.
- `single`: no replacement relationship is exposed.

Future rules resolution should prefer non-superseded material by default while still
allowing Legacy content to be enabled explicitly.

## Rights and attribution

Archives of Nethys and Pathfinder content are not owned by this project. See
[ATTRIBUTION.md](ATTRIBUTION.md) before redistributing generated data.

This repository is not published, endorsed, or specifically approved by Paizo Inc.
