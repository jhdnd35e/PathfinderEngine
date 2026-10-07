import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


sync = load_module("sync_aon", "scripts/sync_aon.py")
verify_mod = load_module("verify_source_data", "scripts/verify_source_data.py")


class SyncAonTests(unittest.TestCase):
    def sample_hits(self):
        return [
            {
                "_id": "es-old",
                "_source": {
                    "id": "legacy-1",
                    "name": "Power Attack",
                    "category": "feat",
                    "level": 1,
                    "trait": ["fighter"],
                    "primary_source": "Core Rulebook",
                    "url": "/Feats.aspx?ID=359",
                    "remaster_id": ["remaster-1"],
                    "search_markdown": "Legacy rules text",
                },
            },
            {
                "_id": "es-new",
                "_source": {
                    "id": "remaster-1",
                    "name": "Vicious Swing",
                    "category": "feat",
                    "level": 1,
                    "trait": ["fighter"],
                    "primary_source": "Player Core",
                    "url": "/Feats.aspx?ID=4775",
                    "legacy_id": ["legacy-1"],
                    "search_markdown": "Remastered rules text",
                },
            },
        ]

    def test_reference_is_stable_and_schema_safe(self):
        a = sync.make_reference("Class Feature", "ABC / 123")
        b = sync.make_reference("Class Feature", "ABC / 123")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^aon:[a-z0-9-]+:[a-z0-9-]+$")

    def test_remaster_links_and_aliases(self):
        records = sync.normalize_hits(
            self.sample_hits(),
            "2026-10-07T22:00:00+00:00",
            "https://elasticsearch.aonprd.com/aon",
        )
        by_id = {record["aon_id"]: record for record in records}
        old = by_id["legacy-1"]
        new = by_id["remaster-1"]

        self.assertEqual(old["edition"], "legacy")
        self.assertEqual(new["edition"], "remastered")
        self.assertEqual(old["relations"]["superseded_by"], [new["reference"]])
        self.assertEqual(new["relations"]["replaces"], [old["reference"]])
        self.assertEqual(new["aliases"], ["Power Attack"])
        self.assertEqual(old["fields"], self.sample_hits()[0]["_source"])

    def test_snapshot_round_trip_verifies(self):
        records = sync.normalize_hits(
            self.sample_hits(),
            "2026-10-07T22:00:00+00:00",
            "https://elasticsearch.aonprd.com/aon",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "source"
            sync.build_snapshot(
                records,
                out,
                endpoint="https://elasticsearch.aonprd.com",
                index="aon",
                fetched_at="2026-10-07T22:00:00+00:00",
                pagination="test",
                max_shard_bytes=100_000,
            )
            summary = verify_mod.verify(out)
            self.assertEqual(summary["entries"], 2)
            self.assertEqual(summary["categories"], 1)
            self.assertEqual(summary["legacy"], 1)
            self.assertEqual(summary["remastered"], 1)


if __name__ == "__main__":
    unittest.main()
