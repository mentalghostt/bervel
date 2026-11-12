"""Check documentation integrity. This is not an application test suite."""
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
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


def documentation_files(root):
    files = []
    excluded = {".git", "node_modules", ".venv", "__pycache__", "local", "outputs", "dist", "build", "vendor"}
    history = {"AGENTS.md", "CLAUDE.md", "SESSION_LOG.md", "LESSONS.md"}
    for directory, children, names in os.walk(root):
        children[:] = [name for name in children if name not in excluded and not name.endswith('.egg-info')]
        files.extend(Path(directory) / name for name in names if name.endswith(".md") and name not in history)
    return files


def main():
    requests = load_csv("docs/discovery/requests.csv", ["id", "kind", "priority", "ask", "status"])
    request_ids = {row["id"] for row in requests}
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
    markdown_files = documentation_files(PROJECT_ROOT)
    link_count = 0
    for file in markdown_files:
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
                fail(str(file.relative_to(PROJECT_ROOT)) + ": broken local link " + target)
    result = {
        "ok": not errors,
        "counts": {"requests": len(requests), "source_checksums": len(sources),
                   "markdown_files": len(markdown_files), "local_links": link_count},
        "scope": "Current documentation links, original request consistency and source integrity",
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
