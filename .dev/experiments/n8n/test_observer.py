"""Check the experiment's measurement tool, not the application or n8n."""
import tempfile
import unittest
from pathlib import Path

from observer import ObserverStore


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name) / "observer.sqlite"
        self.store = ObserverStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_publication_counts_two_attempts_and_one_effect(self):
        payload = {"key": "run-1:revision-1", "run_id": "run-1", "revision": "1"}
        self.assertEqual(self.store.publish(payload), {"created": True})
        self.assertEqual(self.store.publish(payload), {"created": False})
        state = self.store.snapshot("run-1")
        self.assertEqual(len(state["attempts"]), 2)
        self.assertEqual(len(state["effects"]), 1)

    def test_same_key_with_changed_revision_is_a_conflict(self):
        payload = {"key": "run-1:revision-1", "run_id": "run-1", "revision": "1"}
        self.store.publish(payload)
        with self.assertRaises(ValueError):
            self.store.publish({**payload, "revision": "2"})
        state = self.store.snapshot("run-1")
        self.assertEqual(len(state["attempts"]), 2)
        self.assertEqual(state["effects"][0]["revision"], "1")

    def test_reopening_preserves_effect_and_recorded_workflow_version(self):
        self.store.record({"run_id": "run-2", "phase": "waiting", "version": "1"})
        self.store.publish({"key": "run-2:1", "run_id": "run-2", "revision": "1"})
        state = ObserverStore(self.path).snapshot("run-2")
        self.assertEqual([event["version"] for event in state["events"]], ["1"])
        self.assertEqual(len(state["effects"]), 1)


if __name__ == "__main__":
    unittest.main()
