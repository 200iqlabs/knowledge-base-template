"""Tests for graph.py — run with `python -m unittest tools/context-graph/test_graph.py`.

Aimed at the contracts no end-to-end run can pin down: what the status line is allowed to
claim, who wins when two builds publish at once, and the places where this tool and the
linter have to answer a shared question the same way. Those are exactly the parts that
break quietly — a wrong label reads like a right one, and a lost publish looks like a
graph that is merely a little behind.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph  # noqa: E402


def counts(**over):
    base = {"nodes": 10, "edges": 20, "orphans": 3, "dead": 0}
    base.update(over)
    return base


def payload(started, **over):
    blob = {"version": graph.CACHE_VERSION, "started_at": started,
            "counts": counts(), "built": "2026-01-01T00:00:00"}
    blob.update(over)
    return blob


class AgeLabelTest(unittest.TestCase):
    """The line may say when it was published. It may never say it matches the tree."""

    def test_units(self):
        self.assertEqual(graph.age_label(0), "published 0s ago")
        self.assertEqual(graph.age_label(59.9), "published 59s ago")
        self.assertEqual(graph.age_label(60), "published 1m ago")
        self.assertEqual(graph.age_label(3599), "published 59m ago")
        self.assertEqual(graph.age_label(3600), "published 1h ago")
        self.assertEqual(graph.age_label(86400 * 2), "published 2d ago")

    def test_never_claims_freshness(self):
        for seconds in (0, 5, 61, 4000, 200000):
            self.assertNotIn("fresh", graph.age_label(seconds))


class LineAnswerTest(unittest.TestCase):
    """What the one-line form prints, against a state directory it never has to build."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.state = os.path.join(self.root, "state")
        os.makedirs(self.state)
        self.config_path = os.path.join(self.root, "graph.yaml")
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write("scan_roots:\n  - context\n")
        self.config = {"state_dir": "state", "scan_roots": ["context"],
                       "thresholds": {"build_seconds": 1, "line_max_age_seconds": 3600}}
        self.write_state()
        # No detached build from a test: it would outlive the assertion, hold the
        # temporary directory open, and go looking for a knowledge base that is not there.
        # What the test wants to know is whether one was asked for.
        self.launched = []
        self.spawn, graph.start_background_build = graph.start_background_build, (
            lambda *args: self.launched.append(args))

    def tearDown(self):
        graph.start_background_build = self.spawn
        self.tmp.cleanup()

    def write_state(self, config_fp=None):
        """A published graph and a cache the current process would accept."""
        fingerprint = (config_fp if config_fp is not None
                       else graph.config_fingerprint(self.config, self.root,
                                                     self.config_path))
        with open(os.path.join(self.state, "graph.json"), "w", encoding="utf-8") as fh:
            json.dump(payload(time.time(), config=fingerprint), fh)
        with open(os.path.join(self.state, "cache.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": graph.CACHE_VERSION,
                       "relink": graph.relink_fingerprint(), "files": {}}, fh)

    def line(self):
        from io import StringIO
        held, sys.stdout = sys.stdout, StringIO()
        try:
            graph.line_answer(self.config, self.root, self.config_path)
            return sys.stdout.getvalue().strip()
        finally:
            sys.stdout = held

    def test_inside_the_window_reports_an_age(self):
        line = self.line()
        self.assertIn("published", line)
        self.assertNotIn("fresh", line)
        self.assertNotIn("rebuilding", line)
        self.assertEqual(self.launched, [])   # nothing to rebuild, nothing started

    def test_outside_the_window_says_rebuilding(self):
        self.config["thresholds"]["line_max_age_seconds"] = 0
        self.assertIn("rebuilding", self.line())
        self.assertEqual(len(self.launched), 1)

    def test_a_changed_config_is_cold_however_recent(self):
        """The counts were just published — and under a configuration that has changed."""
        self.write_state(config_fp="written-under-something-else")
        line = self.line()
        self.assertIn("rebuilding", line)
        self.assertNotIn("published", line)
        self.assertEqual(len(self.launched), 1)

    def test_numbers_survive_either_way(self):
        self.assertIn("10 nodes", self.line())


class PublishTest(unittest.TestCase):
    """A slower build must not lay its older view over a newer one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.path = os.path.join(self.state, "graph.json")

    def tearDown(self):
        self.tmp.cleanup()

    def published(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_later_start_wins_whatever_the_order(self):
        graph.publish(self.state, 200.0, payload(200.0, counts=counts(nodes=2)))
        graph.publish(self.state, 100.0, payload(100.0, counts=counts(nodes=1)))
        self.assertEqual(self.published()["started_at"], 200.0)

    def test_a_newer_build_still_replaces_an_older_one(self):
        graph.publish(self.state, 100.0, payload(100.0))
        graph.publish(self.state, 200.0, payload(200.0))
        self.assertEqual(self.published()["started_at"], 200.0)

    def test_the_claim_is_given_back(self):
        graph.publish(self.state, 100.0, payload(100.0))
        self.assertFalse(os.path.exists(
            graph.lock_path(self.state, graph.PUBLISH_LOCK_NAME)))

    def test_a_held_claim_does_not_lose_the_answer(self):
        """Waiting is bounded: the numbers go out even if the claim never comes free."""
        graph.PUBLISH_WAIT, held = 0.1, graph.PUBLISH_WAIT
        token = graph.take_lock(self.state, 300, graph.PUBLISH_LOCK_NAME)
        try:
            graph.publish(self.state, 100.0, payload(100.0))
            self.assertEqual(self.published()["started_at"], 100.0)
        finally:
            graph.PUBLISH_WAIT = held
            graph.drop_lock(self.state, token, graph.PUBLISH_LOCK_NAME)

    def test_two_locks_are_two_locks(self):
        """Publishing must not be blocked by the build claim, or a `map` could never publish."""
        build = graph.take_lock(self.state, 300)
        try:
            self.assertIsNotNone(
                graph.take_lock(self.state, 300, graph.PUBLISH_LOCK_NAME))
        finally:
            graph.drop_lock(self.state, build)


class ExemptTest(unittest.TestCase):
    """The orphan exemption, which the linter reads through this very function."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.rules = {"self_index_marker": "_index.md", "exclude_dirs": ["archive"],
                      "entity_scopes": ["context/projects"]}

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, rel):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").close()

    def test_a_marker_file_exempts_the_subtree(self):
        self.touch("context/projects/ALPHA/data/runs/_index.md")
        self.assertTrue(graph.exempt(self.root,
                                     "context/projects/ALPHA/data/runs/one.md", self.rules))

    def test_a_directory_named_like_the_marker_exempts_nothing(self):
        """`isfile`, not `exists` — the linter looks the marker up among file names."""
        os.makedirs(os.path.join(self.root,
                                 "context/projects/ALPHA/data/runs/_index.md"))
        self.assertFalse(graph.exempt(self.root,
                                      "context/projects/ALPHA/data/runs/one.md", self.rules))

    def test_a_scope_level_index_exempts_nothing(self):
        self.touch("context/projects/_index.md")
        self.assertFalse(graph.exempt(self.root,
                                      "context/projects/ALPHA/data/one.md", self.rules))

    def test_an_excluded_directory_is_exempt(self):
        self.assertTrue(graph.exempt(self.root,
                                     "context/projects/ALPHA/archive/one.md", self.rules))


class ConfigFingerprintTest(unittest.TestCase):
    """What the published counts were computed under, so a stale one can be spotted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.graph_config = os.path.join(self.root, "graph.yaml")
        with open(self.graph_config, "w", encoding="utf-8") as fh:
            fh.write("scan_roots:\n  - context\n")
        self.lint_config = os.path.join(self.root, "lint.yaml")
        with open(self.lint_config, "w", encoding="utf-8") as fh:
            fh.write("self_index_marker: _index.md\n")
        self.config = {"lint_config": "lint.yaml"}

    def tearDown(self):
        self.tmp.cleanup()

    def fingerprint(self):
        return graph.config_fingerprint(self.config, self.root, self.graph_config)

    def test_stable_while_nothing_changes(self):
        self.assertEqual(self.fingerprint(), self.fingerprint())

    def test_moves_when_the_graph_config_changes(self):
        before = self.fingerprint()
        with open(self.graph_config, "a", encoding="utf-8") as fh:
            fh.write("  - outputs\n")
        self.assertNotEqual(before, self.fingerprint())

    def test_moves_when_the_borrowed_lint_config_changes(self):
        """The exemption rule lives there, and it moves every orphan count."""
        before = self.fingerprint()
        with open(self.lint_config, "w", encoding="utf-8") as fh:
            fh.write("self_index_marker: index.md\n")
        self.assertNotEqual(before, self.fingerprint())


if __name__ == "__main__":
    unittest.main()
