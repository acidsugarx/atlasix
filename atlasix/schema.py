"""Fact-model schema: SQLite DDL + sqlite-vec virtual table."""
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY,
  parse_status TEXT NOT NULL DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  line INTEGER,
  data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities(kind);
CREATE INDEX IF NOT EXISTS idx_entities_path ON entities(path);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE TABLE IF NOT EXISTS edges(
  src_id INTEGER NOT NULL REFERENCES entities(id),
  dst_id INTEGER REFERENCES entities(id),
  rel TEXT NOT NULL,
  raw_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE TABLE IF NOT EXISTS text_units(
  id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(id),
  path TEXT NOT NULL,
  line INTEGER,
  normalized_hash TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  ref_table TEXT NOT NULL,
  ref_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  vec BLOB
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # multilingual, 384-dim, local ONNX
VEC_DIM = 384
VEC_DDL = f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{VEC_DIM}]);"


def connect(db_path, *, for_write: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL)
    if for_write:
        load_vec(conn)
    return conn


def load_vec(conn: sqlite3.Connection) -> bool:
    """Try to load sqlite-vec extension and create virtual table. Returns success."""
    global VEC_OK
    try:
        try:
            conn.enable_load_extension(True)
        except AttributeError:
            pass  # sqlite built without extension support
        import sqlite_vec  # type: ignore

        sqlite_vec.load(conn)
        conn.execute(VEC_DDL)
        VEC_OK = True
    except Exception:
        VEC_OK = False
    return VEC_OK


VEC_OK = False


def rebuild(db_path):
    """Fresh database: drop everything, recreate."""
    import pathlib

    p = pathlib.Path(db_path)
    if p.exists():
        p.unlink()
    for suffix in ("-wal", "-shm"):
        side = p.with_name(p.name + suffix)
        if side.exists():
            side.unlink()
    conn = connect(db_path, for_write=True)
    return conn
