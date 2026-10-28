#!/usr/bin/env python3
"""task-profile v0: deterministic task profiles recorded before a role is selected.

Commands
  adapt <adapter> --task-id ID --stage {A,B} --target-role ROLE --packet PATH [--packet PATH] -o SIDECAR
  compute SIDECAR --repo REPO -o PROFILE
  validate PROFILE [--production]
  manifest --root ROOT --path PATTERN [--path PATTERN] --out-dir DIR

A sidecar is normally written by the coordinator at dispatch. `adapt` exists for historical
packets in formats that carry some structure.

Rules this file enforces
- Every declaration is a field {"status": declared|none|not_applicable|unknown, "value"?, "basis"?}.
  "value" exists only when status is "declared". Unknown is never written as zero.
- Stage A (before the supervisor is chosen) may declare only identity and the objective. Stage B
  (before the builder or reviewer is chosen) may declare everything. A feature used to choose a role
  must exist before that role acts.
- Treatment fields found in a packet (assigned models, efforts) are ignored and listed, never profiled.
- compute reads only the sidecar, files whose sha256 it verifies, and git objects at a declared
  commit. It never reads the current working tree as snapshot data. Same inputs give the same bytes.
Stdlib only.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA_ID = "task-profile/v0"
EXTRACTOR = {"name": "taskprofile.py", "version": "0.2.1"}
STATUSES = ("declared", "none", "not_applicable", "unknown")
WORK_TYPES = ("implementation", "planning", "investigation", "qualification", "review",
              "authority-preparation", "documentation", "other")
AUTHORITY_STATES = ("approved", "proposed-not-approved", "preparation-only", "other")
ROLES = {"A": ("supervisor",), "B": ("builder", "reviewer")}
OBLIGATION_KINDS = ("acceptance", "state", "persistence", "recovery", "compatibility")
VERIFY_KINDS = ("build", "test", "lint", "format", "fixture-oracle", "independent-review", "manual", "other")
EXECUTABLE = {"build", "test", "lint", "format", "fixture-oracle"}
JUDGMENT = {"independent-review", "manual"}
AUTHORITY_KINDS = ("authority", "contract", "proposal", "clarification", "plan", "guidance", "other")
DECLARED_KEYS = ("objective", "read_set", "expected_write_set", "interfaces", "exclusions", "verification",
                 "uncertainties", "dependencies") + tuple(f"obligations.{k}" for k in OBLIGATION_KINDS)
STAGE_A_ALLOWED = {"objective"}
REQUIRED_FIELDS = (("identity", "parent_task_id"), ("identity", "work_type"), ("identity", "authority_state"),
                   ("input_snapshot", "git"), ("input_snapshot", "manifest"), ("input_snapshot", "authority_refs"),
                   *(("declared", k) for k in ("objective", "read_set", "expected_write_set", "interfaces", "exclusions",
                                               "verification", "uncertainties", "dependencies")),
                   *(("declared", "obligations", k) for k in OBLIGATION_KINDS),
                   ("provenance", "declared_by"))

URL = re.compile(r"\w+://\S+")
REPO_PATH = re.compile(r"(?<![\w./-])((?:[\w.*-]+/)+[\w.*-]+|[\w-]+\.(?:md|rs|toml|json|py|txt|ya?ml|sh))(?![\w/])")
CRATE = re.compile(r"(?<![\w/-])rust/crates/([A-Za-z0-9_-]+)")
COMMIT = re.compile(r"\b[0-9a-f]{40}\b")
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LIST_OF_STR = ("declared.expected_write_set", "declared.interfaces", "declared.exclusions", "declared.uncertainties",
               "declared.dependencies") + tuple(f"declared.obligations.{k}" for k in OBLIGATION_KINDS)


def F(status, value=None, basis=None):
    f = {"status": status}
    if status == "declared":
        f["value"] = value
    if basis:
        f["basis"] = basis
    return f


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def dump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def source_doc(path):
    return {"path": os.path.abspath(path), "sha256": sha256_file(path), "bytes": os.path.getsize(path)}


def first_path(text):
    m = REPO_PATH.search(URL.sub(" ", text))
    return m.group(1) if m else None


def classify_gate(cmd):
    c = cmd.lower()
    if re.search(r"\bcargo (test|nextest)\b|\bpytest\b|\bgo test\b|\bnpm test\b", c):
        return "test"
    if re.search(r"\bclippy\b|\blint\b|\beslint\b|\bruff\b", c):
        return "lint"
    if re.search(r"\bfmt\b|\bformat\b|git diff --check", c):
        return "format"
    if re.search(r"\bcargo (build|check)\b|\bmake\b", c):
        return "build"
    return "other"


def empty_profile(task_id, stage, role, adapter):
    unk = "not stated in a machine-readable form by this packet format"
    return {
        "schema": SCHEMA_ID,
        "stage": stage,
        "identity": {"task_id": task_id, "target_role": role, "parent_task_id": F("unknown", basis=unk),
                     "work_type": F("unknown", basis=unk), "authority_state": F("unknown", basis=unk)},
        "input_snapshot": {"git": F("unknown", basis=unk), "manifest": F("unknown", basis=unk),
                           "authority_refs": F("unknown", basis=unk)},
        "declared": {"objective": F("unknown", basis=unk), "read_set": F("unknown", basis=unk),
                     "expected_write_set": F("unknown", basis=unk), "interfaces": F("unknown", basis=unk),
                     "exclusions": F("unknown", basis=unk), "verification": F("unknown", basis=unk),
                     "uncertainties": F("unknown", basis=unk), "dependencies": F("unknown", basis=unk),
                     "obligations": {k: F("unknown", basis=unk) for k in OBLIGATION_KINDS}},
        "provenance": {"adapter": {"name": adapter, "version": EXTRACTOR["version"]},
                       "declared_by": F("unknown", basis="packet does not record who wrote it"),
                       "ignored_treatment_fields": [], "source_documents": []},
    }


# ---------------------------------------------------------------- adapters

def adapt_contract_v1(p, packets):
    """Work Engine contract-v1 JSON, optionally with its builder-packet-v1 JSON."""
    contract = builder = None
    for path in packets:
        d = read_json(path)
        p["provenance"]["source_documents"].append(source_doc(path))
        if "acceptance" in d:
            contract = d
        elif d.get("role"):
            builder = d
    if contract is None:
        raise SystemExit("work-engine-contract-v1 adapter needs a contract with an 'acceptance' list")
    dec = p["declared"]
    if contract.get("objective"):
        dec["objective"] = F("declared", contract["objective"], "contract-v1.objective")
    if isinstance(contract.get("scope"), list):
        dec["expected_write_set"] = F("declared", sorted(contract["scope"]), "contract-v1.scope")
    if isinstance(contract.get("acceptance"), list):
        dec["obligations"]["acceptance"] = F("declared", list(contract["acceptance"]), "contract-v1.acceptance")
    if isinstance(contract.get("exclusions"), list):
        dec["exclusions"] = F("declared", list(contract["exclusions"]), "contract-v1.exclusions")
    checks = []
    if builder and isinstance(builder.get("gates"), list):
        checks += [{"kind": classify_gate(g), "text": g} for g in builder["gates"]]
    if contract.get("oracle_seed"):
        checks.append({"kind": "fixture-oracle", "text": "contract-v1.oracle_seed"})
    if contract.get("review_schedule") or contract.get("review"):
        checks.append({"kind": "independent-review", "text": "contract-v1 review schedule"})
    if checks:
        dec["verification"] = F("declared", checks, "builder-packet-v1.gates (kind by command), contract oracle and review")
    if builder and isinstance(builder.get("read_set"), list):
        dec["read_set"] = F("declared", [{"text": t, "path": first_path(t)} for t in builder["read_set"]],
                            "builder-packet-v1.read_set; path = first repository path token in each item")
    if builder and builder.get("repository"):
        p["input_snapshot"]["git"] = F("unknown", basis=f"packet names a repository ({builder['repository']}) but no commit")
    if isinstance(contract.get("authority"), str):
        p["input_snapshot"]["authority_refs"] = F("unknown", basis="contract-v1.authority is a prose statement, not file references")
        p["identity"]["authority_state"] = F("unknown", basis="contract-v1.authority is prose; not classified")
    for key in ("models",):
        if key in contract:
            p["provenance"]["ignored_treatment_fields"].append(f"contract-v1.{key}")
    if isinstance(contract.get("review"), dict):
        p["provenance"]["ignored_treatment_fields"] += [f"contract-v1.review.{k}" for k in ("builder", "reviewer") if k in contract["review"]]
    return p


def adapt_shared_contract(p, packets):
    """Work Engine shared-contract JSON: base_commit, hashed authority documents, disallowed and deferred lists."""
    path = packets[0]
    s = read_json(path)
    p["provenance"]["source_documents"].append(source_doc(path))
    if s.get("base_commit"):
        p["input_snapshot"]["git"] = F("declared", {"commit": s["base_commit"]}, "shared-contract.base_commit")
    refs = [{"kind": k, "path": s[k]["path"], "sha256": s[k]["sha256"]}
            for k in ("authority", "proposal", "clarification") if isinstance(s.get(k), dict)]
    if refs:
        p["input_snapshot"]["authority_refs"] = F("declared", refs, "shared-contract authority/proposal/clarification")
        p["declared"]["objective"] = F("unknown", basis="the objective lives in the referenced authority documents")
    excl = list(s.get("disallowed") or []) + list(s.get("deferred") or [])
    if excl:
        p["declared"]["exclusions"] = F("declared", excl, "shared-contract.disallowed + shared-contract.deferred")
    for k in ("builder", "reviewer", "root_collector"):
        if k in s:
            p["provenance"]["ignored_treatment_fields"].append(f"shared-contract.{k}")
    return p


def adapt_markdown(p, packets):
    """Markdown packet: no machine-readable declarations. Only the document itself is recorded."""
    for path in packets:
        p["provenance"]["source_documents"].append(source_doc(path))
    basis = "Markdown packet: no machine-readable declarations; a coordinator must declare this field"
    for k, v in p["declared"].items():
        if k == "obligations":
            for ok in v:
                v[ok] = F("unknown", basis=basis)
        else:
            p["declared"][k] = F("unknown", basis=basis)
    return p


ADAPTERS = {"work-engine-contract-v1": adapt_contract_v1, "work-engine-shared-contract": adapt_shared_contract,
            "markdown": adapt_markdown}


# ---------------------------------------------------------------- compute

def glob_regex(pat):
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out + r"\Z")


def resolve(pattern, listing):
    """Files in the snapshot matched by a path or glob; a plain path also matches a directory prefix."""
    if any(ch in pattern for ch in "*?"):
        rx = glob_regex(pattern)
        return sorted(f for f in listing if rx.match(f))
    if pattern in listing:
        return [pattern]
    prefix = pattern.rstrip("/") + "/"
    return sorted(f for f in listing if f.startswith(prefix))


def git_listing(repo, commit, coverage):
    t = subprocess.run(["git", "-C", repo, "cat-file", "-t", commit], capture_output=True, text=True)
    if t.returncode != 0 or t.stdout.strip() != "commit":
        coverage.append({"field": "input_snapshot.git", "status": "resolution_failure",
                         "detail": f"commit {commit} not found in {repo}"})
        return None
    out = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "-l", commit], capture_output=True, text=True, check=True).stdout
    listing = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        size = meta.split()[3]
        listing[path] = int(size) if size.isdigit() else 0
    return listing


def manifest_listing(m, coverage):
    """{relative path: bytes} for a manifest whose copies verify; None if it cannot be verified."""
    path, root, copies = m["path"], m["root"], m.get("copies_dir")
    if not os.path.isfile(path) or sha256_file(path) != m["sha256"]:
        coverage.append({"field": "input_snapshot.manifest", "status": "resolution_failure",
                         "detail": "manifest missing or sha256 differs from the declared value"})
        return None, {}
    doc = read_json(path)
    listing, verified, facts = {}, 0, {}
    for abs_path, digest in sorted(doc.get("files", {}).items()):
        rel = os.path.relpath(abs_path, root)
        size = None
        if copies:
            cand = os.path.join(copies, rel)
            if os.path.isfile(cand) and sha256_file(cand) == digest:
                size, verified = os.path.getsize(cand), verified + 1
        listing[rel] = size
    facts["files"] = len(listing)
    facts["copies_verified"] = verified
    porcelain = (doc.get("provenance") or {}).get("manifest_path_git_status_porcelain")
    facts["dirty_paths"] = len(porcelain) if isinstance(porcelain, list) else None
    if verified < len(listing):
        coverage.append({"field": "input_snapshot.manifest", "status": "unresolved",
                         "detail": f"{len(listing) - verified} of {len(listing)} files have no verified copy; their bytes are unknown"})
    return listing, facts


def structure_errors(p):
    """Every required field must exist and carry one of the four statuses."""
    errs = []
    for path in REQUIRED_FIELDS:
        node = p
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        name = ".".join(path)
        if not isinstance(node, dict):
            errs.append(f"{name}: required field missing")
        elif node.get("status") not in STATUSES:
            errs.append(f"{name}: status {node.get('status')!r} not in {STATUSES}")
    if not isinstance(p.get("identity", {}).get("task_id"), str):
        errs.append("identity.task_id: required string missing")
    if not isinstance(p.get("provenance", {}).get("source_documents"), list):
        errs.append("provenance.source_documents: required list missing")
    snap = p.get("input_snapshot", {})
    if all(isinstance(snap.get(k), dict) and snap[k].get("status") == "declared" for k in ("git", "manifest")):
        errs.append("input_snapshot: git and manifest are both declared; declare exactly one snapshot")
    return errs


def walk_fields(obj, prefix=""):
    for k, v in obj.items():
        here = f"{prefix}{k}"
        if isinstance(v, dict) and "status" in v and v.get("status") in STATUSES:
            yield here, v
        elif isinstance(v, dict):
            yield from walk_fields(v, here + ".")


def count(field):
    """len(value) if declared, 0 if explicitly none, None otherwise."""
    if field["status"] == "declared":
        return len(field["value"])
    if field["status"] == "none":
        return 0
    return None


def compute(sidecar, repo):
    bad = [e for e in structure_errors(sidecar) if "both declared" not in e]
    if bad:
        raise ValueError("malformed sidecar: " + "; ".join(bad))
    p = json.loads(json.dumps(sidecar))
    coverage, computed = [], {}
    for name, f in list(walk_fields(p["identity"], "identity.")) + list(walk_fields(p["input_snapshot"], "input_snapshot.")) \
            + list(walk_fields(p["declared"], "declared.")) + list(walk_fields(p["provenance"], "provenance.")):
        if f["status"] == "unknown":
            coverage.append({"field": name, "status": "unknown", "detail": f.get("basis", "not declared")})

    # packet text (text-mention features: weak, reported separately)
    texts, nbytes, verified_docs = [], 0, True
    for d in p["provenance"]["source_documents"]:
        if not os.path.isfile(d["path"]) or sha256_file(d["path"]) != d["sha256"]:
            verified_docs = False
            coverage.append({"field": "provenance.source_documents", "status": "resolution_failure", "detail": d["path"]})
            continue
        texts.append(read_text(d["path"]))
        nbytes += d["bytes"]
    text = URL.sub(" ", "\n".join(texts))
    computed["packet"] = {"kind": "text", "bytes": nbytes if verified_docs else None,
                          "words": len(text.split()) if verified_docs else None}
    computed["mentions"] = {"kind": "text_mention",
                            "crates": sorted(set(CRATE.findall(text))),
                            "repo_paths": len(set(REPO_PATH.findall(text))),
                            "commits": len(set(COMMIT.findall(text)))}

    # snapshot
    snap = {"kind": "none", "files": None, "bytes": None, "dirty_paths": None}
    listing = None
    git, man = p["input_snapshot"]["git"], p["input_snapshot"]["manifest"]
    if git["status"] == "declared" and man["status"] == "declared":
        snap = {"kind": "conflict", "files": None, "bytes": None, "dirty_paths": None}
        coverage.append({"field": "input_snapshot", "status": "resolution_failure",
                         "detail": "git and manifest are both declared; no snapshot used"})
    elif git["status"] == "declared":
        listing = git_listing(repo, git["value"]["commit"], coverage)
        if listing is not None:
            snap = {"kind": "git", "files": len(listing), "bytes": sum(listing.values()), "dirty_paths": 0}
    elif man["status"] == "declared" and check_value("input_snapshot.manifest", man["value"]):
        # A malformed manifest is recorded as a resolution failure; validate reports the error.
        coverage.append({"field": "input_snapshot.manifest", "status": "resolution_failure",
                         "detail": "; ".join(check_value("input_snapshot.manifest", man["value"]))})
    elif man["status"] == "declared":
        listing, facts = manifest_listing(man["value"], coverage)
        if listing is not None:
            sizes = [s for s in listing.values() if s is not None]
            snap = {"kind": "manifest", "files": facts["files"], "copies_verified": facts["copies_verified"],
                    "bytes": sum(sizes) if len(sizes) == len(listing) else None, "dirty_paths": facts["dirty_paths"]}
    else:
        coverage.append({"field": "computed.snapshot", "status": "unresolved",
                         "detail": "no git commit or manifest declared; repository measurements are not computed"})
    snap["kind_label"] = "computed"
    computed["snapshot"] = snap

    # read set
    rs = p["declared"]["read_set"]
    r = {"kind": "declaration_dependent", "declared_items": count(rs), "items_without_path": None,
         "resolved_files": None, "resolved_bytes": None, "unresolved_items": None}
    if rs["status"] == "declared":
        items = [it if isinstance(it, dict) else {"text": it, "path": it} for it in rs["value"]]
        r["items_without_path"] = sum(1 for it in items if not it.get("path"))
        if listing is not None:
            files, unresolved = set(), 0
            for it in items:
                hit = resolve(it["path"], listing) if it.get("path") else []
                unresolved += not hit
                files.update(hit)
            r["resolved_files"] = len(files)
            sizes = [listing[f] for f in files]
            r["resolved_bytes"] = sum(sizes) if all(s is not None for s in sizes) else None
            r["unresolved_items"] = unresolved
    computed["read_set"] = r

    # write set
    ws = p["declared"]["expected_write_set"]
    w = {"kind": "declaration_dependent", "declared_items": count(ws), "crates": None, "cross_crate": None,
         "existing_files": None, "unmatched_items": None}
    if ws["status"] == "declared":
        crates = sorted({m for item in ws["value"] for m in CRATE.findall(item)})
        w["crates"], w["cross_crate"] = crates, len(crates) > 1
        if listing is not None:
            hits = [resolve(item, listing) for item in ws["value"]]
            w["existing_files"] = len({f for h in hits for f in h})
            w["unmatched_items"] = sum(1 for h in hits if not h)
    computed["write_set"] = w

    # authority documents
    ar = p["input_snapshot"]["authority_refs"]
    a = {"kind": "computed", "count": count(ar), "verified": None, "mismatch": None, "unavailable": None, "bytes": None}
    if ar["status"] == "declared":
        ver = mis = una = byt = 0
        for ref in ar["value"]:
            if not os.path.isfile(ref["path"]):
                una += 1
            elif ref.get("sha256") and sha256_file(ref["path"]) == ref["sha256"]:
                ver, byt = ver + 1, byt + os.path.getsize(ref["path"])
            else:
                mis += 1
        a.update(verified=ver, mismatch=mis, unavailable=una, bytes=byt if una == mis == 0 else None)
        if una or mis:
            coverage.append({"field": "input_snapshot.authority_refs", "status": "resolution_failure",
                             "detail": f"{mis} hash mismatches, {una} unavailable"})
    computed["authority"] = a

    # obligations, verification and other declared lists
    ob = {k: count(p["declared"]["obligations"][k]) for k in OBLIGATION_KINDS}
    known = [v for v in ob.values() if v is not None]
    computed["obligations"] = {"kind": "declaration_dependent", **ob,
                               "total_declared_kinds": sum(known) if known else None,
                               "kinds_declared": sorted(k for k, v in ob.items() if v is not None)}
    vf = p["declared"]["verification"]
    v = {"kind": "declaration_dependent", "count": count(vf), "by_kind": None, "executable": None, "judgment": None}
    if vf["status"] == "declared":
        by = {}
        for c in vf["value"]:
            by[c["kind"]] = by.get(c["kind"], 0) + 1
        v.update(by_kind=dict(sorted(by.items())), executable=sum(n for k, n in by.items() if k in EXECUTABLE),
                 judgment=sum(n for k, n in by.items() if k in JUDGMENT))
    computed["verification"] = v
    computed["lists"] = {"kind": "declaration_dependent",
                         **{k: count(p["declared"][k]) for k in ("exclusions", "interfaces", "uncertainties", "dependencies")}}

    # context burden: what the role must read before writing
    parts = {"packet_bytes": computed["packet"]["bytes"], "authority_bytes": a["bytes"], "read_set_bytes": r["resolved_bytes"]}
    read_gaps = (r["unresolved_items"] or 0) + (r["items_without_path"] or 0) if rs["status"] == "declared" else 0
    computed["context_burden"] = {"kind": "computed", "bytes_known": sum(x for x in parts.values() if x is not None),
                                  "components": parts, "unresolved_read_items": read_gaps,
                                  "complete": all(x is not None for x in parts.values()) and read_gaps == 0}

    # task revision: identifies what the profile is about, so profile and assignment records can be joined.
    # Same packet bytes against a different snapshot, or for a different role, is a different revision.
    snap_identity = None
    if git["status"] == "declared":
        snap_identity = {"git": git["value"].get("commit")}
    elif man["status"] == "declared":
        snap_identity = {"manifest": man["value"].get("sha256") if isinstance(man["value"], dict) else None}
    basis = {"schema": p.get("schema"), "task_id": p["identity"]["task_id"], "stage": p.get("stage"),
             "target_role": p["identity"].get("target_role"),
             "source_documents": sorted(d["sha256"] for d in p["provenance"]["source_documents"]),
             "snapshot": snap_identity}
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    computed["task_revision"] = {"kind": "computed", "id": f'{p["identity"]["task_id"]}@{digest}',
                                 "basis": "schema, task_id, stage, target_role, source document sha256s, snapshot identity"}

    p["computed"] = computed
    p["coverage"] = sorted(coverage, key=lambda c: (c["field"], c["status"], c["detail"]))
    p["provenance"]["extractor"] = dict(EXTRACTOR)
    return p


# ---------------------------------------------------------------- manifest

def write_manifest(root, patterns, out_dir):
    """Capture working-tree bytes (which may be uncommitted) as hashed copies plus a manifest.

    No timestamps are written, so the same bytes give the same manifest hash.
    """
    root = os.path.abspath(root)
    out_abs = os.path.abspath(out_dir)
    files, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and os.path.abspath(os.path.join(dirpath, d)) != out_abs
                       and not os.path.isfile(os.path.join(dirpath, d, "baseline-manifest.json"))]  # earlier snapshots
        for f in filenames:
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                skipped.append(os.path.relpath(full, root))
            else:
                files.append(os.path.relpath(full, root))
    listing = {f: None for f in files}
    chosen = sorted({f for pat in patterns for f in resolve(pat, listing)})
    if not chosen:
        raise SystemExit("manifest: no files matched")
    copies = os.path.join(out_dir, "baseline")
    entries = {}
    for rel in chosen:
        src = os.path.join(root, rel)
        dst = os.path.join(copies, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dst, "wb") as fh:
            fh.write(data)
        entries[src] = hashlib.sha256(data).hexdigest()
    head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-C", root, "status", "--porcelain", "--"] + chosen, capture_output=True, text=True)
    doc = {"meaning": "working-tree bytes at capture; may include uncommitted changes",
           "files": entries,
           "skipped_symlinks": sorted(skipped),
           "provenance": {"git_head_at_capture": head.stdout.strip() if head.returncode == 0 else None,
                          "manifest_path_git_status_porcelain": status.stdout.splitlines() if status.returncode == 0 else None}}
    path = os.path.join(out_dir, "baseline-manifest.json")
    dump(doc, path)
    return {"path": os.path.abspath(path), "sha256": sha256_file(path), "root": root, "copies_dir": os.path.abspath(copies)}


# ---------------------------------------------------------------- validate

PROVENANCE_KEYS = ("role", "model", "effort", "harness", "guidance_sha256")


def _is_str(x):
    return isinstance(x, str) and x.strip() != ""


def check_value(name, v):
    """Type errors in a declared value; [] when it is well formed."""
    bad = lambda what: [f"{name}: {what}"]
    if name in LIST_OF_STR:
        return [] if isinstance(v, list) and all(_is_str(x) for x in v) else bad("must be a list of non-empty strings")
    if name in ("declared.objective", "identity.parent_task_id"):
        return [] if _is_str(v) else bad("must be a non-empty string")
    if name == "identity.work_type":
        return [] if v in WORK_TYPES else bad(f"{v!r} not in {WORK_TYPES}")
    if name == "identity.authority_state":
        return [] if v in AUTHORITY_STATES else bad(f"{v!r} not in {AUTHORITY_STATES}")
    if name == "declared.read_set":
        ok = isinstance(v, list) and all(isinstance(x, dict) and _is_str(x.get("text")) and (x.get("path") is None or _is_str(x.get("path"))) for x in v)
        return [] if ok else bad("must be a list of {text: string, path: string or null}")
    if name == "declared.verification":
        ok = isinstance(v, list) and all(isinstance(x, dict) and x.get("kind") in VERIFY_KINDS and _is_str(x.get("text")) for x in v)
        return [] if ok else bad(f"must be a list of {{kind: one of {VERIFY_KINDS}, text: string}}")
    if name == "input_snapshot.authority_refs":
        ok = isinstance(v, list) and all(isinstance(x, dict) and x.get("kind") in AUTHORITY_KINDS and _is_str(x.get("path"))
                                         and isinstance(x.get("sha256"), str) and HEX64.match(x["sha256"]) for x in v)
        return [] if ok else bad(f"must be a list of {{kind: one of {AUTHORITY_KINDS}, path, sha256: 64 hex}}")
    if name == "input_snapshot.git":
        return [] if isinstance(v, dict) and isinstance(v.get("commit"), str) and HEX40.match(v["commit"]) else bad("must be {commit: 40 lowercase hex}")
    if name == "input_snapshot.manifest":
        ok = (isinstance(v, dict) and _is_str(v.get("path")) and _is_str(v.get("root")) and isinstance(v.get("sha256"), str)
              and HEX64.match(v["sha256"]) and (v.get("copies_dir") is None or _is_str(v.get("copies_dir"))))
        return [] if ok else bad("must be {path, sha256: 64 hex, root, copies_dir?}")
    if name == "provenance.declared_by":
        return [] if isinstance(v, dict) and all(isinstance(x, str) for x in v.values()) else bad("must be an object of strings")
    return []


def validate(p, production=False):
    """Structural and cross-field checks. production=True also requires full declarer provenance."""
    errs = structure_errors(p)
    if any("required" in e for e in errs):
        return errs
    if p.get("schema") != SCHEMA_ID:
        errs.append(f"schema must be {SCHEMA_ID}")
    stage = p.get("stage")
    if stage not in ROLES:
        return errs + ["stage must be A or B"]
    if p["identity"].get("target_role") not in ROLES[stage]:
        errs.append(f"stage {stage} target_role must be one of {ROLES[stage]}")
    for name, f in list(walk_fields(p["identity"], "identity.")) + list(walk_fields(p["input_snapshot"], "input_snapshot.")) \
            + list(walk_fields(p["declared"], "declared.")) + list(walk_fields(p["provenance"], "provenance.")):
        if f["status"] not in STATUSES:
            errs.append(f"{name}: bad status {f['status']}")
        if (f["status"] == "declared") != ("value" in f):
            errs.append(f"{name}: 'value' must be present exactly when status is declared")
    if not isinstance(p["identity"].get("task_id"), str) or not TASK_ID.match(p["identity"]["task_id"]):
        errs.append("identity.task_id: must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    for name, f in list(walk_fields(p["identity"], "identity.")) + list(walk_fields(p["input_snapshot"], "input_snapshot.")) \
            + list(walk_fields(p["declared"], "declared.")) + list(walk_fields(p["provenance"], "provenance.")):
        if f.get("status") == "declared" and "value" in f:
            errs += check_value(name, f["value"])
    for d in p["provenance"].get("source_documents", []):
        if not (isinstance(d, dict) and _is_str(d.get("path")) and isinstance(d.get("sha256"), str) and HEX64.match(d["sha256"])
                and isinstance(d.get("bytes"), int)):
            errs.append("provenance.source_documents: each entry must be {path, sha256: 64 hex, bytes: int}")
    if stage == "A":
        for name, f in walk_fields(p["declared"], ""):
            if f["status"] != "unknown" and name not in STAGE_A_ALLOWED:
                errs.append(f"stage A may not declare declared.{name}: it would come from the supervisor's own plan")
    if production:
        db = p["provenance"].get("declared_by", {})
        if db.get("status") != "declared":
            errs.append("production: provenance.declared_by must be declared")
        else:
            missing = [k for k in PROVENANCE_KEYS if not db["value"].get(k)]
            if missing:
                errs.append(f"production: provenance.declared_by is missing {missing}")
        if not p["provenance"].get("source_documents"):
            errs.append("production: provenance.source_documents must list the frozen packet")
    if "coverage" in p:
        listed = {c["field"] for c in p["coverage"] if c["status"] == "unknown"}
        for name, f in list(walk_fields(p["declared"], "declared.")) + list(walk_fields(p["input_snapshot"], "input_snapshot.")):
            if f["status"] == "unknown" and name not in listed:
                errs.append(f"{name}: unknown but has no coverage entry")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adapt")
    a.add_argument("adapter", choices=sorted(ADAPTERS))
    a.add_argument("--task-id", required=True)
    a.add_argument("--stage", choices=("A", "B"), default="B")
    a.add_argument("--target-role", default="builder")
    a.add_argument("--packet", action="append", required=True)
    a.add_argument("-o", "--out", required=True)
    c = sub.add_parser("compute")
    c.add_argument("sidecar")
    c.add_argument("--repo", required=True)
    c.add_argument("-o", "--out", required=True)
    v = sub.add_parser("validate")
    v.add_argument("profile")
    v.add_argument("--production", action="store_true", help="also require full declarer provenance")
    m = sub.add_parser("manifest")
    m.add_argument("--root", required=True)
    m.add_argument("--path", action="append", required=True)
    m.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    if args.cmd == "adapt":
        p = ADAPTERS[args.adapter](empty_profile(args.task_id, args.stage, args.target_role, args.adapter), args.packet)
        dump(p, args.out)
    elif args.cmd == "compute":
        try:
            dump(compute(read_json(args.sidecar), args.repo), args.out)
        except ValueError as e:
            print(e, file=sys.stderr)
            sys.exit(2)
    elif args.cmd == "manifest":
        print(json.dumps(write_manifest(args.root, args.path, args.out_dir), indent=1, sort_keys=True))
    else:
        errs = validate(read_json(args.profile), production=args.production)
        print("\n".join(errs) if errs else "valid")
        sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
