"""Check documentation integrity. This is not an application test suite."""
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
errors = []


def fail(message):
    errors.append(message)


def local_path(relative):
    path = (ROOT / relative).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError("Path leaves project: " + relative)
    return path


def load_csv(relative, required):
    path = local_path(relative)
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            fail(relative + ": missing columns " + repr(sorted(missing)))
        rows = list(reader)
    ids = [row.get("id") for row in rows]
    if not rows or len(set(ids)) != len(ids) or not all(ids):
        fail(relative + ": empty or duplicate IDs")
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            fail(relative + ": malformed row " + repr(row.get("id")))
    return rows


def refs(rows, field, allowed, label):
    for row in rows:
        for ref in filter(None, row.get(field, "").split(";")):
            if ref not in allowed:
                fail(label + "/" + row["id"] + ": invalid " + field + " " + ref)


def check_dag(rows, label):
    graph = {row["id"]: list(filter(None, row.get("depends_on", "").split(";"))) for row in rows}
    done, active = set(), set()

    def visit(node):
        if node in active:
            fail(label + ": dependency cycle at " + node)
            return
        if node in done:
            return
        active.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        active.remove(node)
        done.add(node)
    for node in graph:
        visit(node)


def main():
    requests = load_csv("docs/discovery/requests.csv", ["id", "kind", "priority", "ask", "status"])
    functions = load_csv("docs/planning/function-catalog.csv", [
        "id", "module", "function", "input", "output", "invariant", "acceptance",
        "depends_on", "optional_depends_on", "request_refs"
    ])
    backlog = load_csv("docs/planning/backlog.csv", ["id", "function_refs", "request_refs", "depends_on", "acceptance"])
    assumptions = load_csv("docs/discovery/assumptions.csv", ["id", "request_refs", "decision_refs"])
    risks = load_csv("docs/discovery/risks.csv", ["id", "request_refs", "function_refs"])
    scenarios = load_csv("docs/quality/test-scenarios.csv", ["id", "function_refs", "expected", "status"])
    request_ids = {row["id"] for row in requests}
    function_ids = {row["id"] for row in functions}
    backlog_ids = {row["id"] for row in backlog}
    decision_text = (ROOT / "docs/architecture/09-open-decisions.md").read_text(encoding="utf-8")
    decision_ids = set(re.findall(r"\bDEC-\d{2}\b", decision_text))
    for rows, name in [(functions, "functions"), (backlog, "backlog"), (assumptions, "assumptions"), (risks, "risks")]:
        refs(rows, "request_refs", request_ids, name)
    for rows, name in [(backlog, "backlog"), (risks, "risks"), (scenarios, "scenarios")]:
        refs(rows, "function_refs", function_ids, name)
    refs(functions, "depends_on", function_ids, "functions")
    refs(functions, "optional_depends_on", function_ids, "functions")
    refs(backlog, "depends_on", backlog_ids, "backlog")
    refs(assumptions, "decision_refs", decision_ids, "assumptions")
    check_dag(functions, "functions")
    check_dag(backlog, "backlog")
    names = [(row["module"], row["function"]) for row in functions]
    if len(set(names)) != len(names):
        fail("Duplicate function within module")
    covered = {ref for row in backlog for ref in row["function_refs"].split(";")}
    if function_ids - covered:
        fail("Functions without a work package: " + ", ".join(sorted(function_ids - covered)))
    structured = json.loads((ROOT / "docs/discovery/requests.json").read_text(encoding="utf-8"))
    structured_items = {item["id"]: item for item in structured["items"]}
    if set(structured_items) != request_ids:
        fail("Request IDs differ between JSON and CSV")
    for row in requests:
        for key, value in structured_items.get(row["id"], {}).items():
            if row.get(key) != value:
                fail("Request CSV differs from audited JSON: " + row["id"] + "/" + key)
        if row["kind"] not in {"N", "D", "P"} or row["priority"] not in {"A", "B", "C"}:
            fail("Unknown request classification " + row["id"])
    sources = json.loads((ROOT / "docs/sources/manifest.json").read_text(encoding="utf-8"))
    for item in sources:
        target = local_path(item["path"])
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            fail("Source checksum mismatch: " + item["path"])
    markdown_files = list(ROOT.rglob("*.md"))
    link_count = 0
    for file in markdown_files:
        if ".git" in file.relative_to(ROOT).parts:
            continue
        content = file.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", content):
            target = target.strip().strip("<>")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            link_count += 1
            resolved = (file.parent / target).resolve()
            if not resolved.exists():
                fail(str(file.relative_to(ROOT)) + ": broken local link " + target)
    result = {
        "ok": not errors,
        "counts": {"functions": len(functions), "work_packages": len(backlog), "requests": len(requests),
                   "assumptions": len(assumptions), "risks": len(risks), "scenarios": len(scenarios),
                   "source_checksums": len(sources), "markdown_files": len(markdown_files), "local_links": link_count},
        "scope": "Documentation references, registry structure and source integrity only",
        "errors": errors
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, csv.Error) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}, ensure_ascii=False))
        sys.exit(1)
