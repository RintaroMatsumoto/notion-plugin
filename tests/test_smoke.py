"""
Smoke tests for notion-plugin skills.

Every skill is exercised once through its CLI `main(argv)` with a
`MockTransport`-backed client. No real network; no NOTION_TOKEN needed.
The tests assert that each skill exits with code 0 and emits valid JSON
where expected.

Run:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Make plugin imports resolvable regardless of CWD.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))


def _load_main(module_path: str):
    spec = importlib.util.spec_from_file_location(module_path.replace("/", "."), ROOT / module_path)
    module = importlib.util.module_from_spec(spec)
    # Inject the _lib path into sys.path so "from _lib.notion_client..." works.
    sys.path.insert(0, str((ROOT / module_path).parent.parent))
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class SchemaSetupSmoke(unittest.TestCase):
    def test_mock_create(self) -> None:
        module = _load_main("skills/notion-schema-setup/schema_setup.py")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = module.main([
                "--title", "Test DB",
                "--columns", "title,status,tags,notes,rating",
                "--mock",
            ])
        self.assertEqual(code, 0, msg=buf_err.getvalue())
        payload = json.loads(buf_out.getvalue())
        self.assertEqual(payload["status"], "created")
        types = {c["name"]: c["type"] for c in payload["schema_preview"]}
        self.assertEqual(types["title"], "title")
        self.assertEqual(types["status"], "status")
        self.assertEqual(types["tags"], "multi_select")
        self.assertEqual(types["notes"], "rich_text")
        self.assertEqual(types["rating"], "number")

    def test_dry_run(self) -> None:
        module = _load_main("skills/notion-schema-setup/schema_setup.py")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = module.main([
                "--title", "Preview",
                "--columns", "foo,bar",
                "--dry-run",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf_out.getvalue())["status"], "dry-run")

    def test_promotes_first_column_to_title_when_no_title_column(self) -> None:
        module = _load_main("skills/notion-schema-setup/schema_setup.py")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = module.main([
                "--title", "X",
                "--columns", "foo,bar,baz",
                "--mock",
            ])
        self.assertEqual(code, 0)
        preview = json.loads(buf_out.getvalue())["schema_preview"]
        self.assertEqual(preview[0]["type"], "title")


class BulkEditSmoke(unittest.TestCase):
    def test_mock_dry_run(self) -> None:
        module = _load_main("skills/notion-bulk-edit/bulk_edit.py")
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            plan.write_text(json.dumps({
                "set_properties": {"Status": {"status": {"name": "Done"}}},
                "append_multi_select": {"Tags": ["2026-Q2"]},
            }), encoding="utf-8")
            output = Path(tmp) / "diff.jsonl"
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = module.main([
                    "--database", "demo-db",
                    "--plan", str(plan),
                    "--mock",
                    "--output", str(output),
                ])
            self.assertEqual(code, 0, msg=buf_err.getvalue())
            lines = output.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreater(len(lines), 0)
            for line in lines:
                rec = json.loads(line)
                self.assertIn("page_id", rec)
                self.assertIn("diff", rec)
                # dry-run: never applied
                self.assertFalse(rec["applied"])


class SyncSmoke(unittest.TestCase):
    def test_pull_both_directions(self) -> None:
        module = _load_main("skills/notion-sync/sync.py")
        with tempfile.TemporaryDirectory() as tmp:
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = module.main([
                    "--database", "demo-db",
                    "--dir", tmp,
                    "--mock",
                    "--direction", "both",
                ])
            self.assertEqual(code, 0, msg=buf_err.getvalue())
            md_files = list(Path(tmp).glob("*.md"))
            self.assertGreaterEqual(len(md_files), 1)
            self.assertTrue((Path(tmp) / ".notion-sync" / "state.json").exists())
            # sanity: body contains seed text
            content = md_files[0].read_text(encoding="utf-8")
            self.assertIn("notion_page_id:", content)
            self.assertIn("Seed body", content)


class CrossReferenceSmoke(unittest.TestCase):
    def test_proposes_relations(self) -> None:
        module = _load_main("skills/notion-cross-reference/cross_reference.py")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = module.main([
                "--database", "demo-db",
                "--relation-property", "Related",
                "--threshold", "0.3",
                "--top-k", "3",
                "--mock",
            ])
        self.assertEqual(code, 0, msg=buf_err.getvalue())
        lines = [line for line in buf_out.getvalue().splitlines() if line.strip()]
        self.assertGreater(len(lines), 0)
        # At least one page should find a similar neighbor in the seed data.
        any_proposal = any(json.loads(line)["proposed"] for line in lines)
        self.assertTrue(any_proposal, "expected at least one proposal in the seed data")


class TemplateInstantiateSmoke(unittest.TestCase):
    def test_single_instantiation(self) -> None:
        module = _load_main("skills/notion-template-instantiate/template_instantiate.py")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = module.main([
                "--template", "tmpl-1",
                "--parent-database", "demo-db",
                "--payload", json.dumps({
                    "title": "2026-04-20 Standup",
                    "date": "2026-04-20",
                    "attendees": "Alice, Bob",
                }),
                "--mock",
            ])
        self.assertEqual(code, 0, msg=buf_err.getvalue())
        report = json.loads(buf_out.getvalue())
        self.assertEqual(len(report["created"]), 1)
        self.assertEqual(report["created"][0]["title"], "2026-04-20 Standup")
        self.assertGreater(report["created"][0]["blocks"], 0)

    def test_batch_instantiation_reports_unknown_placeholders(self) -> None:
        module = _load_main("skills/notion-template-instantiate/template_instantiate.py")
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "hires.jsonl"
            jsonl.write_text(
                "\n".join([
                    json.dumps({"title": "Alice", "date": "2026-05-01"}),  # missing 'attendees'
                    json.dumps({"title": "Bob", "date": "2026-05-08"}),
                ]),
                encoding="utf-8",
            )
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = module.main([
                    "--template", "tmpl-1",
                    "--parent-database", "demo-db",
                    "--payload-file", str(jsonl),
                    "--mock",
                ])
            self.assertEqual(code, 0)
            report = json.loads(buf_out.getvalue())
            self.assertEqual(len(report["created"]), 2)
            self.assertTrue(any("attendees" in w for w in report["warnings"]))


class NotionClientUnit(unittest.TestCase):
    """Sanity checks on the shared client + helpers."""

    def test_rich_text_payload_chunks_at_2000_chars(self) -> None:
        sys.path.insert(0, str(ROOT / "skills"))
        from _lib.notion_client import rich_text_payload
        text = "a" * 4500
        rt = rich_text_payload(text)
        self.assertEqual(len(rt), 3)  # 2000 + 2000 + 500
        self.assertEqual(len(rt[0]["text"]["content"]), 2000)
        self.assertEqual(len(rt[2]["text"]["content"]), 500)

    def test_mock_transport_records_calls(self) -> None:
        sys.path.insert(0, str(ROOT / "skills"))
        from _lib.notion_client import NotionClient, MockTransport
        transport = MockTransport()
        client = NotionClient("mock-token", transport=transport)
        client.create_database("parent-page", "Test", {"Name": {"title": {}}})
        methods = [c[0] for c in transport.calls]
        self.assertIn("POST", methods)
        paths = [c[1] for c in transport.calls]
        self.assertIn("/v1/databases", paths)


if __name__ == "__main__":
    unittest.main()
