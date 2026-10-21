import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .domain import canonical
from .errors import FlowError, redact


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workflows.db"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  workflow_id TEXT NOT NULL, time REAL NOT NULL, actor TEXT NOT NULL,
                  reason TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, doc, actor):
        with self.connection() as db:
            db.execute("INSERT INTO workflows VALUES (?,?)", (doc["id"], canonical(doc)))
            self._event(db, doc, actor, "创建工作流")
        return doc

    def get(self, ident):
        with self.connection() as db:
            row = db.execute("SELECT data FROM workflows WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise FlowError("WORKFLOW_NOT_FOUND", "工作流不存在")
        return json.loads(row[0])

    def mutate(self, ident, actor, reason, fn):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM workflows WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise FlowError("WORKFLOW_NOT_FOUND", "工作流不存在")
            doc = json.loads(row[0])
            result = fn(doc)
            doc["updated_at"] = time.time()
            db.execute("UPDATE workflows SET data=? WHERE id=?", (canonical(doc), ident))
            self._event(db, doc, actor, reason)
        return result if result is not None else doc

    def _event(self, db, doc, actor, reason):
        db.execute("INSERT INTO events(workflow_id,time,actor,reason,state,revision) VALUES (?,?,?,?,?,?)",
                   (doc["id"], time.time(), actor, redact(reason), doc["state"], doc["revision"]))

    def events(self, ident):
        self.get(ident)
        with self.connection() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE workflow_id=? ORDER BY seq", (ident,))]
