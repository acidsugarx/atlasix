import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "mini-repo"
PACK = """version: 1
entity_types:
  job:
    files: ["**/*.yml"]
    extractor: yaml_jobs
    fields: [stage, script]
  env_var:
    files: ["lib/**/README.md"]
    extractor: regex
    pattern: '\\$\\{(\\w+)\\}'
references:
  extends:
    from: {entity: job, field: extends}
    resolve: same_doc_dict
  includes:
    from: {entity: file, field: raw_regex, pattern: '(\\S+\\.yml)'}
    resolve: repo_path
text_units:
  unit: {entity: job, field: script}
duplicates:
  min_lines: 2
  threshold: 0.85
"""
RULES = {
    "dup.yaml": (
        "id: duplicated-jobs\n"
        "severity: warn\n"
        "match: {kind: job}\n"
        'where: "index.in_duplicate_cluster(fact.id)"\n'
        'message: "{kind} {name} in duplicate cluster"\n'
    ),
    "evil.yaml": (
        "id: evil\nseverity: warn\nmatch: {kind: job}\n"
        "where: \"__import__('os').system('true')\"\nmessage: x\n"
    ),
}


def run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "atlasix", *args], cwd=cwd, capture_output=True, text=True
    )


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / "repo"
    shutil.copytree(FIXTURE, d)
    (d / ".atlas" / "rules").mkdir(parents=True)
    (d / ".atlas" / "pack.yaml").write_text(PACK, encoding="utf-8")
    return d


def test_build_and_who_uses(workdir):
    assert run("build", "--no-vectors", cwd=workdir).returncode == 0
    out = run("who-uses", ".build-template", cwd=workdir).stdout
    assert "job:build-dev" in out and "job:build-test" in out


def test_duplicates(workdir):
    run("build", "--no-vectors", cwd=workdir)
    out = run("duplicates", cwd=workdir).stdout
    assert "cluster:1" in out
    assert "job:build-dev" in out and "job:build-test" in out


def test_unresolved_ref_edge(workdir):
    # break the extends target
    p = workdir / "pipelines" / "app" / ".gitlab-ci.yml"
    p.write_text(p.read_text().replace("extends: .build-template", "extends: .missing"), encoding="utf-8")
    run("build", "--no-vectors", cwd=workdir)
    conn = sqlite3.connect(workdir / ".atlas" / "index.db")
    n = conn.execute("SELECT count(*) FROM edges WHERE dst_id IS NULL AND rel='extends'").fetchone()[0]
    assert n == 2  # build-dev + build-test now unresolved


def test_lint_warn_and_whitelist(workdir):
    run("build", "--no-vectors", cwd=workdir)
    (workdir / ".atlas" / "rules" / "dup.yaml").write_text(RULES["dup.yaml"])
    (workdir / ".atlas" / "rules" / "evil.yaml").write_text(RULES["evil.yaml"])
    r = run("lint", cwd=workdir)
    assert r.returncode == 2  # evil rule rejected by AST whitelist
    assert "__import__" in r.stderr
    (workdir / ".atlas" / "rules" / "evil.yaml").unlink()
    r = run("lint", cwd=workdir)
    assert r.returncode == 0
    assert "duplicated-jobs" in r.stdout
    assert "build-dev" in r.stdout


def test_search_bm25(workdir):
    run("build", "--no-vectors", cwd=workdir)
    out = run("search", "docker build", "--top", "3", cwd=workdir).stdout
    assert "pipelines/app/.gitlab-ci.yml" in out


def test_graph_cycles_no_hang(workdir):
    run("build", "--no-vectors", cwd=workdir)
    r = run("graph", "--cycles", cwd=workdir)
    assert r.returncode == 0


def test_empty_pack(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    assert run("init", cwd=d).returncode == 0
    r = run("build", "--no-vectors", cwd=d)
    assert r.returncode == 0
    conn = sqlite3.connect(d / ".atlas" / "index.db")
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1


def test_profile(workdir):
    run("build", "--no-vectors", cwd=workdir)
    out = run("profile", cwd=workdir).stdout
    assert "== entity kinds ==" in out
