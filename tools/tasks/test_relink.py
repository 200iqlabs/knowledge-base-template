"""Tests for relink.py — run with `python -m unittest tools/tasks/test_relink.py`."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relink  # noqa: E402

TASK = "context/projects/ALPHA/tasks/fix-export.md"
ARCHIVED = "context/projects/ALPHA/tasks/_archive/fix-export.md"


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=root, check=True, capture_output=True)


class RelinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        git(self.root, "init", "-q")
        self.write(TASK, "Notes: [notes](../data/notes.md)\n")
        self.write("context/projects/ALPHA/tasks/sibling.md",
                   "See [the export task](fix-export.md#done).\n")
        self.write("context/projects/ALPHA/data/notes.md",
                   "Task: [`EX-1`](../tasks/fix-export.md)\n"
                   "Example: `[x](../tasks/fix-export.md)`\n"
                   "```\n[y](../tasks/fix-export.md)\n```\n"
                   "Web: [site](https://example.com/tasks/fix-export.md), [top](#top)\n")
        self.write("context/projects/BETA/status.md",
                   "| 🟢 | [EX-1](../ALPHA/tasks/fix-export.md) | 2026-01-02 |\n")
        self.write("context/projects/ALPHA/communication/2026-01-01-email-client.md",
                   "[sent](../tasks/fix-export.md)\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        os.makedirs(os.path.join(self.root, os.path.dirname(ARCHIVED)))
        git(self.root, "mv", TASK, ARCHIVED)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()

    def by_place(self):
        return {(l.path, l.line): l for l in relink.scan(self.root)}

    def test_classifies_both_halves_of_an_archive_move(self):
        links = self.by_place()
        self.assertEqual(links[("context/projects/ALPHA/data/notes.md", 1)].fix,
                         "../tasks/_archive/fix-export.md")
        self.assertEqual(links[("context/projects/ALPHA/tasks/sibling.md", 1)].fix,
                         "_archive/fix-export.md#done")
        self.assertEqual(links[("context/projects/BETA/status.md", 1)].fix,
                         "../ALPHA/tasks/_archive/fix-export.md")
        moved = links[(ARCHIVED, 1)]
        self.assertEqual((moved.kind, moved.fix), ("moved", "../../data/notes.md"))

    def test_code_urls_anchors_and_sealed_material_are_left_alone(self):
        places = set(self.by_place())
        self.assertNotIn(("context/projects/ALPHA/data/notes.md", 2), places)
        self.assertNotIn(("context/projects/ALPHA/data/notes.md", 4), places)
        self.assertNotIn(("context/projects/ALPHA/data/notes.md", 6), places)
        self.assertFalse(any("communication" in p for p, _ in places))

    def test_apply_repairs_and_a_second_run_finds_nothing(self):
        relink.apply(self.root, relink.scan(self.root), own=[])
        self.assertIn("(../tasks/_archive/fix-export.md)",
                      self.read("context/projects/ALPHA/data/notes.md"))
        self.assertIn("(../../data/notes.md)", self.read(ARCHIVED))
        self.assertIn("(../tasks/fix-export.md)", self.read(
            "context/projects/ALPHA/communication/2026-01-01-email-client.md"))
        self.assertEqual(relink.scan(self.root), [])

    def test_somebody_elses_uncommitted_file_is_skipped_until_claimed(self):
        status = "context/projects/BETA/status.md"
        self.write(status, self.read(status) + "| 🔴 | waiting on the client | |\n")
        links = relink.scan(self.root)
        relink.apply(self.root, links, own=[])
        skipped = [l for l in links if l.path == status]
        self.assertTrue(skipped and all(l.skipped for l in skipped))
        self.assertIn("(../ALPHA/tasks/fix-export.md)", self.read(status))
        relink.apply(self.root, relink.scan(self.root), own=["context/projects/BETA"])
        self.assertIn("(../ALPHA/tasks/_archive/fix-export.md)", self.read(status))

    def test_file_being_moved_is_the_callers_own_even_with_edits(self):
        self.write(ARCHIVED, self.read(ARCHIVED) + "closed: 2026-01-02\n")
        relink.apply(self.root, relink.scan(self.root), own=[])
        self.assertIn("(../../data/notes.md)", self.read(ARCHIVED))

    def test_task_moved_back_out_of_the_archive(self):
        self.write("context/projects/ALPHA/tasks/live.md", "live\n")
        self.write("context/projects/ALPHA/data/back.md",
                   "[live](../tasks/_archive/live.md)\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "more")
        link = self.by_place()[("context/projects/ALPHA/data/back.md", 1)]
        self.assertEqual((link.kind, link.fix), ("unarchived", "../tasks/live.md"))

    def test_dead_link_gets_a_lead_but_no_repair(self):
        self.write("context/projects/ALPHA/tasks/sibling.md",
                   "[board](../BETA/status.md)\n[gone](../data/never-existed.md)\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "dead")
        links = self.by_place()
        wrong_depth = links[("context/projects/ALPHA/tasks/sibling.md", 1)]
        self.assertEqual(wrong_depth.kind, "dead")
        self.assertIn("../../BETA/status.md", wrong_depth.hint)
        self.assertIn("no tracked file",
                      links[("context/projects/ALPHA/tasks/sibling.md", 2)].hint)
        relink.apply(self.root, list(links.values()), own=[])
        self.assertIn("(../BETA/status.md)",
                      self.read("context/projects/ALPHA/tasks/sibling.md"))

    def test_site_paths_ignored_files_and_footnotes_are_not_dead(self):
        self.write(".gitignore", "**/runs/*.json\n")
        self.write("context/projects/ALPHA/data/runs/r1.md",
                   "[quiz](/tools/label-quiz) · [raw](response.json)\n"
                   "[^1]: a footnote, not a link definition\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "runs")
        self.assertFalse([l for l in relink.scan(self.root) if "runs/" in l.path])

    def test_retarget_carries_out_a_decision_and_checks_it(self):
        self.write("context/projects/ALPHA/data/_index.md", "index\n")
        self.write("context/projects/ALPHA/data/prompts/p1.md",
                   "[i](_index.md) [n](../old-notes.md) [x](gone.md)\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "prompts")
        links = relink.scan(self.root, ["context/projects/ALPHA/data/prompts"])
        relink.retarget(self.root, links, [
            ("_index.md", "../_index.md"),                               # relative
            ("../old-notes.md", "context/projects/ALPHA/data/notes.md"),  # root path
            ("gone.md", "../still-gone.md"),                             # refused
        ])
        relink.apply(self.root, links, own=[], kinds=("retargeted",))
        text = self.read("context/projects/ALPHA/data/prompts/p1.md")
        self.assertIn("[i](../_index.md)", text)
        self.assertIn("[n](../notes.md)", text)
        self.assertIn("[x](gone.md)", text)
        refused = [l for l in links if l.target == "gone.md"][0]
        self.assertEqual(refused.kind, "dead")
        self.assertIn("exists neither", refused.skipped)


if __name__ == "__main__":
    unittest.main()
