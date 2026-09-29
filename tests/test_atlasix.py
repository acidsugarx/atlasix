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


def run(*args, cwd, home=None):
    import os

    env = dict(os.environ)
    env.pop("ATLASIX_HOME", None)
    env["PYTHONIOENCODING"] = "utf-8"  # windows cp1251 console
    if home:
        env["ATLASIX_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "atlasix", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", env=env,
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


def test_empty_pack_central_by_default(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    home = tmp_path / "atlasix-home"
    assert run("init", "--no-agents-md", cwd=d, home=home).returncode == 0
    assert not (d / ".atlas").exists()  # no repo pollution
    r = run("build", "--no-vectors", cwd=d, home=home)
    assert r.returncode == 0
    repos = list((home / "repos").iterdir())
    assert len(repos) == 1
    conn = sqlite3.connect(repos[0] / "index.db")
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1


def test_init_local_flag(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    home = tmp_path / "atlasix-home"
    assert run("init", "--local", cwd=d, home=home).returncode == 0
    assert (d / ".atlas" / "pack.yaml").exists()
    assert not (home / "repos").exists()
    # once .atlas exists, all commands use it
    r = run("build", "--no-vectors", cwd=d, home=home)
    assert r.returncode == 0
    assert (d / ".atlas" / "index.db").exists()


def test_profile(workdir):
    run("build", "--no-vectors", cwd=workdir)
    out = run("profile", cwd=workdir).stdout
    assert "== entity kinds ==" in out


def test_config_roundtrip(monkeypatch, tmp_path):
    from atlasix import config

    home = tmp_path / "atlasix-home"
    monkeypatch.setattr(config, "ATLASIX_HOME", home)
    monkeypatch.setattr(config, "SETTINGS_PATH", home / "settings.yaml")
    monkeypatch.setattr(config, "KEY_PATH", home / "secret.key")
    monkeypatch.setattr(config, "CACHE_DIR", home / "cache")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACEHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("ATLASIX_HF_TOKEN", raising=False)

    config.set_value("hf_token", "hf-secret-123")
    stored = config.load_settings()["hf_token"]
    assert stored.startswith("enc:")
    assert "hf-secret-123" not in stored
    assert config.get_effective("hf_token") == "hf-secret-123"
    assert config.get_effective("model") == ""  # default → schema.MODEL at call time
    # tamper detection
    import pytest as _pytest

    config.KEY_PATH.write_bytes(b"\x00" * 32)
    with _pytest.raises(ValueError):
        config.get_effective("hf_token")


def test_pack_import(tmp_path):
    import shutil

    from atlasix import packs

    d = tmp_path / "repo"
    shutil.copytree(FIXTURE, d)
    atlas = d / ".atlas"
    (atlas / "rules").mkdir(parents=True)
    (atlas / "pack.yaml").write_text(PACK, encoding="utf-8")  # custom pack → guard

    r = run("pack", "import", "gitlab-ci", cwd=d)
    assert r.returncode == 3  # refuses to overwrite custom pack
    written = packs.install("gitlab-ci", atlas, force=True)
    assert any("pack.yaml" in w for w in written)
    # imported pack is valid and lintable
    assert run("build", "--no-vectors", cwd=d).returncode == 0
    r = run("lint", cwd=d)
    assert r.returncode == 0
    assert "job-duplication" in r.stdout


def test_repos_list_prune(tmp_path):
    import shutil as _sh

    home = tmp_path / "home"
    d = tmp_path / "repoA"
    d.mkdir()
    (d / "a.txt").write_text("hi")
    assert run("init", cwd=d, home=home).returncode == 0
    gone = tmp_path / "repoB"
    gone.mkdir()
    (gone / "b.txt").write_text("hi")
    assert run("init", cwd=gone, home=home).returncode == 0
    _sh.rmtree(gone)

    out = run("repos", "list", cwd=d, home=home).stdout
    assert "ORPHANED" in out and "ok" in out
    r = run("repos", "prune", cwd=d, home=home)
    assert "pruned" in r.stdout
    assert len(list((home / "repos").iterdir())) == 1
    # live state dir untouched
    assert run("build", "--no-vectors", cwd=d, home=home).returncode == 0


def test_pack_import_from_dir(tmp_path):
    import shutil as _sh

    from atlasix import packs

    src = tmp_path / "corp" / "mycorp"
    _sh.copytree(packs.PACKS_DIR / "gitlab-ci", src)
    d = tmp_path / "repo"
    _sh.copytree(FIXTURE, d)
    home = tmp_path / "home"
    assert run("init", cwd=d, home=home).returncode == 0
    r = run("pack", "import", "mycorp", "--from", str(tmp_path / "corp"), cwd=d, home=home)
    assert r.returncode == 0, r.stderr
    assert run("build", "--no-vectors", cwd=d, home=home).returncode == 0


def test_init_embeds_agents_md(tmp_path):
    import shutil as _sh

    d = tmp_path / "repo"
    _sh.copytree(FIXTURE, d)
    home = tmp_path / "home"
    (d / "AGENTS.md").write_text("# existing\n", encoding="utf-8")
    assert run("init", cwd=d, home=home).returncode == 0
    body = (d / "AGENTS.md").read_text(encoding="utf-8")
    assert body.startswith("# existing")
    assert "atlasix:begin" in body and "atlasix lint" in body
    # idempotent: no section duplication
    run("init", cwd=d, home=home)
    assert (d / "AGENTS.md").read_text(encoding="utf-8").count("atlasix:begin") == 1


def test_mcp_server_tools(tmp_path):
    import shutil as _sh

    d = tmp_path / "repo"
    _sh.copytree(FIXTURE, d)
    home = tmp_path / "home"
    run("init", cwd=d, home=home)
    run("pack", "import", "gitlab-ci", "--force", cwd=d, home=home)
    run("build", "--no-vectors", cwd=d, home=home)

    import os

    from atlasix import mcp_server

    old_home, old_cwd = os.environ.get("ATLASIX_HOME"), os.getcwd()
    os.environ["ATLASIX_HOME"] = str(home)
    os.chdir(d)
    try:
        out = mcp_server._run("who-uses", ".build-template")
    finally:
        os.chdir(old_cwd)
        if old_home:
            os.environ["ATLASIX_HOME"] = old_home
        else:
            os.environ.pop("ATLASIX_HOME", None)
    assert "job:build-dev" in out
