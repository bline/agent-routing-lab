"""Tests for agent_routing_lab.taskprofile. Run: python -m unittest discover -s tests

All fixtures are synthetic and built in a temporary directory: a small git repository, packets in
each supported format, authority documents and a dirty working tree.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from agent_routing_lab import taskprofile as tp  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, text=True, check=True).stdout.strip()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        cls.repo = os.path.join(t, "repo")
        os.makedirs(cls.repo)
        git(cls.repo, "init", "-q", "-b", "main")
        write(os.path.join(cls.repo, "AGENTS.md"), "# Agents\nRules.\n")
        write(os.path.join(cls.repo, "docs/guide.md"), "# Guide\n")
        write(os.path.join(cls.repo, "rust/crates/alpha/src/lib.rs"), "pub fn alpha() {}\n")
        write(os.path.join(cls.repo, "rust/crates/alpha/tests/alpha.rs"), "#[test] fn t() {}\n")
        write(os.path.join(cls.repo, "rust/crates/beta/src/lib.rs"), "pub fn beta() {}\n")
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "fixture")
        cls.commit = git(cls.repo, "rev-parse", "HEAD")

        p = os.path.join(t, "packets")
        cls.contract = os.path.join(p, "contract-v1.json")
        write(cls.contract, json.dumps({
            "objective": "Add an export to alpha without changing beta.",
            "scope": ["rust/crates/alpha/**", "docs/guide.md"],
            "acceptance": ["export is explicit", "existing output unchanged", "missing data is reported"],
            "exclusions": ["no network calls", "no schema migration"],
            "oracle_seed": {"description": "fixed input and expected output"},
            "review_schedule": "independent review after gates pass",
            "authority": "the owner authorizes this change in an isolated clone",
            "models": {"builder": "model-a", "reviewer": "model-b"},
        }, indent=1))
        cls.builder = os.path.join(p, "builder-packet-v1.json")
        write(cls.builder, json.dumps({
            "role": "builder", "repository": cls.repo,
            "read_set": ["AGENTS.md and doctrine", "rust/crates/alpha/src/lib.rs signatures first"],
            "gates": ["cargo test -p alpha", "cargo fmt --check", "cargo clippy -p alpha -- -D warnings", "git diff --check"],
        }, indent=1))
        docs = {k: os.path.join(t, "authority", f"{k}.md") for k in ("authority", "proposal", "clarification")}
        for k, path in docs.items():
            write(path, f"# {k}\nText for {k}.\n")
        cls.shared = os.path.join(p, "shared-contract.json")
        write(cls.shared, json.dumps({
            **{k: {"path": path, "sha256": tp.sha256_file(path)} for k, path in docs.items()},
            "base_commit": cls.commit, "allowed": "repair only",
            "disallowed": ["paid calls", "commit or push"], "deferred": ["live replay"],
            "builder": {"model": "model-a", "reasoning_effort": "high"},
        }, indent=1))
        cls.authority_doc = docs["authority"]
        cls.markdown = os.path.join(p, "packet.md")
        write(cls.markdown, "# Change alpha and beta\nTouch rust/crates/alpha/src/lib.rs and rust/crates/beta/src/lib.rs.\n"
                            f"Prepared against {cls.commit}. See https://example.com/docs/a.html for background.\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def adapt(self, adapter, packets, task_id="t", stage="B", role="builder"):
        return tp.ADAPTERS[adapter](tp.empty_profile(task_id, stage, role, adapter), packets)

    def profile(self, sidecar):
        a = tp.compute(sidecar, self.repo)
        b = tp.compute(json.loads(json.dumps(sidecar)), self.repo)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), "compute must be deterministic")
        self.assertEqual(tp.validate(a), [], "profile must validate")
        return a

    def contract_sidecar(self):
        return self.adapt("work-engine-contract-v1", [self.contract, self.builder], task_id="contract")


class ContractTests(Fixture):
    def test_declarations_and_coverage(self):
        p = self.profile(self.contract_sidecar())
        c = p["computed"]
        self.assertEqual(c["obligations"]["acceptance"], 3)
        self.assertIsNone(c["obligations"]["recovery"], "an undeclared kind is null, not 0")
        self.assertEqual(c["verification"]["by_kind"],
                         {"fixture-oracle": 1, "format": 2, "independent-review": 1, "lint": 1, "test": 1})
        self.assertEqual(c["write_set"]["crates"], ["alpha"])
        self.assertEqual(c["snapshot"]["kind"], "none")
        self.assertIn(("input_snapshot.git", "unknown"), {(e["field"], e["status"]) for e in p["coverage"]})
        self.assertIn("contract-v1.models", p["provenance"]["ignored_treatment_fields"])

    def test_git_snapshot_resolves_scope(self):
        s = self.contract_sidecar()
        s["input_snapshot"]["git"] = tp.F("declared", {"commit": self.commit}, "test")
        c = self.profile(s)["computed"]
        self.assertEqual(c["snapshot"]["kind"], "git")
        self.assertEqual(c["write_set"]["existing_files"], 3)  # alpha src, alpha tests, docs/guide.md
        self.assertEqual(c["read_set"]["resolved_files"], 2)
        self.assertFalse(c["write_set"]["cross_crate"])
        s["declared"]["expected_write_set"] = tp.F("declared", ["rust/crates/alpha/**", "rust/crates/beta/src/lib.rs"])
        self.assertTrue(self.profile(s)["computed"]["write_set"]["cross_crate"])

    def test_unknown_commit_is_a_resolution_failure(self):
        s = self.contract_sidecar()
        s["input_snapshot"]["git"] = tp.F("declared", {"commit": "0" * 40})
        p = tp.compute(s, self.repo)
        self.assertIn(("input_snapshot.git", "resolution_failure"), {(e["field"], e["status"]) for e in p["coverage"]})
        self.assertIsNone(p["computed"]["write_set"]["existing_files"])

    def test_reordering_does_not_change_structure(self):
        base = self.profile(self.contract_sidecar())
        d = tp.read_json(self.contract)
        d["acceptance"], d["exclusions"], d["scope"] = d["acceptance"][::-1], d["exclusions"][::-1], d["scope"][::-1]
        path = os.path.join(self.tmp.name, "packets", "reordered.json")
        write(path, json.dumps(d, indent=4))
        other = self.profile(self.adapt("work-engine-contract-v1", [path, self.builder], task_id="contract"))
        for group in ("obligations", "verification", "write_set", "read_set", "lists", "snapshot"):
            self.assertEqual(other["computed"][group], base["computed"][group], group)
        self.assertNotEqual(other["computed"]["task_revision"]["id"], base["computed"]["task_revision"]["id"],
                            "different packet bytes are a different task revision")


class SharedContractTests(Fixture):
    def test_authority_documents_verify(self):
        c = self.profile(self.adapt("work-engine-shared-contract", [self.shared]))["computed"]
        self.assertEqual(c["snapshot"]["kind"], "git")
        self.assertEqual((c["authority"]["verified"], c["authority"]["mismatch"]), (3, 0))
        self.assertEqual(c["lists"]["exclusions"], 3)

    def test_changed_authority_document_is_reported(self):
        s = self.adapt("work-engine-shared-contract", [self.shared])
        original = tp.read_text(self.authority_doc)
        try:
            write(self.authority_doc, original + "edited\n")
            p = tp.compute(s, self.repo)
        finally:
            write(self.authority_doc, original)
        self.assertEqual(p["computed"]["authority"]["mismatch"], 1)
        self.assertIsNone(p["computed"]["authority"]["bytes"])
        self.assertIn(("input_snapshot.authority_refs", "resolution_failure"), {(e["field"], e["status"]) for e in p["coverage"]})

    def test_treatment_fields_are_ignored(self):
        s = self.adapt("work-engine-shared-contract", [self.shared])
        self.assertIn("shared-contract.builder", s["provenance"]["ignored_treatment_fields"])


class MarkdownTests(Fixture):
    def test_everything_declared_is_unknown(self):
        p = self.profile(self.adapt("markdown", [self.markdown]))
        unknown = [n for n, f in tp.walk_fields(p["declared"], "declared.") if f["status"] == "unknown"]
        self.assertEqual(len(unknown), len(tp.DECLARED_KEYS))
        self.assertEqual(p["computed"]["mentions"]["crates"], ["alpha", "beta"])
        self.assertEqual(p["computed"]["mentions"]["commits"], 1, "a commit in prose is a mention, not a baseline")
        self.assertEqual(p["computed"]["snapshot"]["kind"], "none")

    def test_urls_are_not_repository_paths(self):
        self.assertIsNone(tp.first_path("see https://example.com/docs/a.html"))
        self.assertEqual(tp.first_path("edit rust/crates/alpha/src/lib.rs now"), "rust/crates/alpha/src/lib.rs")


class ManifestTests(Fixture):
    def test_dirty_tree_manifest(self):
        lib = os.path.join(self.repo, "rust/crates/alpha/src/lib.rs")
        original = tp.read_text(lib)
        out = os.path.join(self.tmp.name, "manifest")
        try:
            write(lib, original + "pub fn uncommitted() {}\n")
            m = tp.write_manifest(self.repo, ["rust/crates/alpha/**"], out)
            again = tp.write_manifest(self.repo, ["rust/crates/alpha/**"], out + "-2")
        finally:
            write(lib, original)
        self.assertEqual(m["sha256"], again["sha256"], "same bytes give the same manifest")
        s = self.adapt("markdown", [self.markdown])
        s["input_snapshot"]["manifest"] = tp.F("declared", m, "test")
        snap = self.profile(s)["computed"]["snapshot"]
        self.assertEqual((snap["kind"], snap["files"], snap["copies_verified"]), ("manifest", 2, 2))
        self.assertEqual(snap["dirty_paths"], 1)

    def test_tampered_copy_is_unresolved(self):
        out = os.path.join(self.tmp.name, "manifest-tamper")
        m = tp.write_manifest(self.repo, ["rust/crates/beta/**"], out)
        write(os.path.join(m["copies_dir"], "rust/crates/beta/src/lib.rs"), "changed\n")
        s = self.adapt("markdown", [self.markdown])
        s["input_snapshot"]["manifest"] = tp.F("declared", m)
        p = tp.compute(s, self.repo)
        self.assertEqual(p["computed"]["snapshot"]["copies_verified"], 0)
        self.assertIsNone(p["computed"]["snapshot"]["bytes"])
        self.assertIn(("input_snapshot.manifest", "unresolved"), {(e["field"], e["status"]) for e in p["coverage"]})


class ValidatorTests(Fixture):
    def test_stage_a_rejects_plan_content(self):
        s = self.adapt("work-engine-contract-v1", [self.contract], stage="A", role="supervisor")
        errs = tp.validate(tp.compute(s, self.repo))
        self.assertTrue(any("stage A may not declare" in e for e in errs), errs)

    def test_missing_coverage_entry_and_value_on_unknown(self):
        p = tp.compute(self.contract_sidecar(), self.repo)
        bad = copy.deepcopy(p)
        bad["coverage"] = [e for e in bad["coverage"] if e["field"] != "declared.interfaces"]
        self.assertIn("declared.interfaces: unknown but has no coverage entry", tp.validate(bad))
        bad = copy.deepcopy(p)
        bad["declared"]["obligations"]["recovery"] = {"status": "unknown", "value": 0}
        self.assertTrue(any("'value' must be present exactly" in e for e in tp.validate(bad)))

    def test_production_requires_provenance(self):
        p = tp.compute(self.contract_sidecar(), self.repo)
        self.assertTrue(any(e.startswith("production:") for e in tp.validate(p, production=True)))

    def test_task_revision_is_stable(self):
        a = tp.compute(self.contract_sidecar(), self.repo)["computed"]["task_revision"]["id"]
        b = tp.compute(self.contract_sidecar(), self.repo)["computed"]["task_revision"]["id"]
        self.assertEqual(a, b)
        self.assertRegex(a, r"^contract@[0-9a-f]{16}$")

    def test_template_validates_for_production(self):
        t = tp.read_json(os.path.join(ROOT, "docs", "sidecar-template.json"))
        self.assertEqual(tp.validate(t, production=True), [])


class IdentityTests(Fixture):
    def rev(self, sidecar):
        return tp.compute(sidecar, self.repo)["computed"]["task_revision"]["id"]

    def test_revision_depends_on_snapshot_and_role(self):
        base = self.adapt("work-engine-shared-contract", [self.shared])
        other = copy.deepcopy(base)
        other["input_snapshot"]["git"] = tp.F("declared", {"commit": "f" * 40})
        self.assertNotEqual(self.rev(base), self.rev(other), "same packet at another commit is another revision")
        reviewer = copy.deepcopy(base)
        reviewer["identity"]["target_role"] = "reviewer"
        self.assertNotEqual(self.rev(base), self.rev(reviewer), "builder and reviewer profiles are separate revisions")

    def test_revision_defined_without_source_documents(self):
        s = self.contract_sidecar()
        s["provenance"]["source_documents"] = []
        self.assertRegex(self.rev(s), r"^contract@[0-9a-f]{16}$")


class TypeValidationTests(Fixture):
    def errs(self, mutate):
        s = self.contract_sidecar()
        mutate(s)
        return tp.validate(tp.compute(s, self.repo))

    def test_string_where_list_belongs(self):
        e = self.errs(lambda s: s["declared"]["obligations"].__setitem__("acceptance", {"status": "declared", "value": "must pass"}))
        self.assertTrue(any(x.startswith("declared.obligations.acceptance: must be a list") for x in e), e)

    def test_malformed_commit_and_hash(self):
        e = self.errs(lambda s: s["input_snapshot"].__setitem__("git", {"status": "declared", "value": {"commit": "HEAD"}}))
        self.assertTrue(any(x.startswith("input_snapshot.git") for x in e), e)
        e = self.errs(lambda s: s["input_snapshot"].__setitem__("authority_refs", {"status": "declared", "value": [{"kind": "contract", "path": "/x", "sha256": "abc"}]}))
        self.assertTrue(any(x.startswith("input_snapshot.authority_refs") for x in e), e)

    def test_bad_read_set_item_and_task_id(self):
        e = self.errs(lambda s: s["declared"].__setitem__("read_set", {"status": "declared", "value": ["a plain string"]}))
        self.assertTrue(any(x.startswith("declared.read_set") for x in e), e)
        e = self.errs(lambda s: s["identity"].__setitem__("task_id", "has space/and slash"))
        self.assertTrue(any(x.startswith("identity.task_id") for x in e), e)


if __name__ == "__main__":
    unittest.main()
