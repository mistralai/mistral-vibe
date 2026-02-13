from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vibe.core.memory.compression import (
    compact_dumps,
    compress_text,
    decompress_text,
    is_near_duplicate,
    simhash,
)
from vibe.core.memory.models import (
    ContextMemory,
    FieldMeta,
    Observation,
    Seed,
    UserField,
    UserState,
)

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS user_states (
    user_id TEXT PRIMARY KEY,
    seed_json TEXT NOT NULL DEFAULT '{}',
    fields_json TEXT NOT NULL DEFAULT '[]',
    observations_json TEXT DEFAULT '[]',
    accumulated_importance REAL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_memories (
    context_key TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sensory_json TEXT DEFAULT '[]',
    short_term_json TEXT DEFAULT '[]',
    long_term TEXT DEFAULT '',
    version INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    consolidation_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, context_key)
);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    observations_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consolidations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    context_key TEXT NOT NULL,
    input_sensory_count INTEGER,
    old_long_term TEXT,
    new_long_term TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE INDEX IF NOT EXISTS idx_reflections_user_created ON reflections(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_consolidations_user_context ON consolidations(user_id, context_key, created_at);
CREATE INDEX IF NOT EXISTS idx_context_memories_updated ON context_memories(updated_at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    """SQLite storage backend for the memory system."""

    def __init__(
        self,
        db_path: Path,
        compress: bool = True,
        dedup_sensory: bool = False,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(db_path), check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=200")
        self._conn.row_factory = sqlite3.Row
        self._compress = compress
        self._dedup_sensory = dedup_sensory
        self._simhash_cache: dict[tuple[str, str], list[int]] = {}
        self._init_schema()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA_SQL)

        with self._conn:
            row = self._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            current_version = row["version"] if row else 0
            has_version_row = row is not None

        if current_version < _SCHEMA_VERSION:
            self._migrate(current_version)
            with self._conn:
                if has_version_row:
                    self._conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (_SCHEMA_VERSION,),
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (_SCHEMA_VERSION,),
                    )

    def _migrate(self, from_version: int) -> None:
        """Run incremental migrations."""
        assert self._conn is not None
        if from_version < 2:
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_reflections_user_created ON reflections(user_id, created_at);\n"
                "CREATE INDEX IF NOT EXISTS idx_consolidations_user_context ON consolidations(user_id, context_key, created_at);\n"
                "CREATE INDEX IF NOT EXISTS idx_context_memories_updated ON context_memories(updated_at);\n"
            )

    # -- User State --

    def get_or_create_user_state(self, user_id: str) -> UserState:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM user_states WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is not None:
                return self._row_to_user_state(row)

            now = _now_iso()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO user_states (user_id, seed_json, fields_json, observations_json, accumulated_importance, updated_at) "
                    "VALUES (?, '{}', '[]', '[]', 0, ?)",
                    (user_id, now),
                )
            return UserState(
                user_id=user_id,
                updated_at=datetime.fromisoformat(now),
            )

    def update_user_state(self, state: UserState) -> None:
        with self._lock:
            assert self._conn is not None
            seed_json = state.seed.model_dump_json()
            fields_json = compact_dumps(
                [f.model_dump(mode="json") for f in state.fields]
            )
            with self._conn:
                self._conn.execute(
                    "UPDATE user_states SET seed_json=?, fields_json=?, accumulated_importance=?, updated_at=? "
                    "WHERE user_id=?",
                    (
                        seed_json,
                        fields_json,
                        state.accumulated_importance,
                        _now_iso(),
                        state.user_id,
                    ),
                )

    @staticmethod
    def _row_to_user_state(row: sqlite3.Row) -> UserState:
        seed_data = json.loads(row["seed_json"])
        fields_data = json.loads(row["fields_json"])
        fields = []
        for f in fields_data:
            meta = FieldMeta(**f["meta"]) if "meta" in f else FieldMeta()
            fields.append(UserField(key=f["key"], value=f["value"], meta=meta))
        return UserState(
            user_id=row["user_id"],
            seed=Seed(**seed_data) if seed_data else Seed(),
            fields=fields,
            accumulated_importance=row["accumulated_importance"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # -- Observations --

    def add_observation(self, obs: Observation) -> int:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT observations_json FROM user_states WHERE user_id = ?",
                (obs.user_id,),
            ).fetchone()
            if row is None:
                return 0

            if obs.created_at is None:
                obs.created_at = datetime.now(UTC)

            observations = json.loads(row["observations_json"])
            observations.append(obs.model_dump(mode="json", exclude={"id"}))
            with self._conn:
                self._conn.execute(
                    "UPDATE user_states SET observations_json=? WHERE user_id=?",
                    (compact_dumps(observations), obs.user_id),
                )
            return len(observations)

    def get_pending_observations(
        self, user_id: str, limit: int = 20
    ) -> list[Observation]:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT observations_json FROM user_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return []

            observations = json.loads(row["observations_json"])
            observations.sort(key=lambda o: o.get("importance", 0), reverse=True)
            return [Observation(**o) for o in observations[:limit]]

    def clear_observations(
        self, user_id: str, processed: list[Observation] | None = None
    ) -> int:
        """Remove pending observations.

        When processed is None, clears all observations for the user.
        Otherwise removes the top-N observations by the same importance ordering
        used by get_pending_observations (N=len(processed)).
        Returns the number of removed observations.
        """
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT observations_json FROM user_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return 0

            observations = json.loads(row["observations_json"])
            if not observations:
                return 0

            if processed is None:
                removed = len(observations)
                updated = []
            else:
                remove_count = min(len(processed), len(observations))
                ranked_indices = sorted(
                    enumerate(observations),
                    key=lambda item: item[1].get("importance", 0),
                    reverse=True,
                )
                to_remove_indices = {
                    index for index, _ in ranked_indices[:remove_count]
                }
                updated = [
                    obs
                    for index, obs in enumerate(observations)
                    if index not in to_remove_indices
                ]
                removed = len(observations) - len(updated)

            if removed == 0:
                return 0

            with self._conn:
                self._conn.execute(
                    "UPDATE user_states SET observations_json=? WHERE user_id=?",
                    (compact_dumps(updated), user_id),
                )
            return removed

    # -- Context Memory --

    def get_or_create_context_memory(
        self, context_key: str, user_id: str
    ) -> ContextMemory:
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM context_memories WHERE user_id=? AND context_key=?",
                (user_id, context_key),
            ).fetchone()
            if row is not None:
                return self._row_to_context_memory(row)

            now = _now_iso()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO context_memories (context_key, user_id, sensory_json, short_term_json, long_term, version, updated_at, consolidation_count) "
                    "VALUES (?, ?, '[]', '[]', '', 0, ?, 0)",
                    (context_key, user_id, now),
                )
            return ContextMemory(
                context_key=context_key,
                user_id=user_id,
                updated_at=datetime.fromisoformat(now),
            )

    def update_context_memory(self, ctx_mem: ContextMemory) -> bool:
        with self._lock:
            assert self._conn is not None
            lt_value = (
                compress_text(ctx_mem.long_term)
                if self._compress
                else ctx_mem.long_term
            )
            with self._conn:
                result = self._conn.execute(
                    "UPDATE context_memories SET sensory_json=?, short_term_json=?, long_term=?, "
                    "version=version+1, updated_at=?, consolidation_count=? "
                    "WHERE user_id=? AND context_key=? AND version=?",
                    (
                        compact_dumps(ctx_mem.sensory),
                        compact_dumps(ctx_mem.short_term),
                        lt_value,
                        _now_iso(),
                        ctx_mem.consolidation_count,
                        ctx_mem.user_id,
                        ctx_mem.context_key,
                        ctx_mem.version,
                    ),
                )
                if result.rowcount == 0:
                    logger.warning(
                        "Optimistic lock failure on context_memory %s (version %d)",
                        ctx_mem.context_key,
                        ctx_mem.version,
                    )
                    return False
            return True

    def add_sensory(
        self, context_key: str, user_id: str, content: str, cap: int = 50
    ) -> bool:
        with self._lock:
            assert self._conn is not None
            ctx_mem = self.get_or_create_context_memory(context_key, user_id)
            if ctx_mem.sensory and ctx_mem.sensory[-1] == content:
                return False
            if self._dedup_sensory:
                cache_key = (context_key, user_id)
                hashes = self._simhash_cache.get(cache_key, [])
                if hashes and is_near_duplicate(content, hashes[-20:], threshold=3):
                    return False
                hashes.append(simhash(content))
                self._simhash_cache[cache_key] = hashes
            ctx_mem.sensory.append(content)
            if len(ctx_mem.sensory) > cap:
                ctx_mem.sensory = ctx_mem.sensory[-cap:]
            with self._conn:
                self._conn.execute(
                    "UPDATE context_memories SET sensory_json=?, version=version+1, updated_at=? "
                    "WHERE user_id=? AND context_key=?",
                    (compact_dumps(ctx_mem.sensory), _now_iso(), user_id, context_key),
                )
            return True

    @staticmethod
    def _row_to_context_memory(row: sqlite3.Row) -> ContextMemory:
        return ContextMemory(
            context_key=row["context_key"],
            user_id=row["user_id"],
            sensory=json.loads(row["sensory_json"]),
            short_term=json.loads(row["short_term_json"]),
            long_term=decompress_text(row["long_term"]),
            version=row["version"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            consolidation_count=row["consolidation_count"],
        )

    # -- Pruning / Forgetting --

    def prune_audit_logs(self, retention_days: int) -> tuple[int, int]:
        """Delete reflections and consolidations older than retention_days."""
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self._lock:
            assert self._conn is not None
            with self._conn:
                r = self._conn.execute(
                    "DELETE FROM reflections WHERE created_at < ?", (cutoff,)
                )
                c = self._conn.execute(
                    "DELETE FROM consolidations WHERE created_at < ?", (cutoff,)
                )
                return r.rowcount, c.rowcount

    def get_stale_contexts(self, stale_days: int) -> list[tuple[str, str]]:
        """Return stale contexts that still contain volatile data."""
        cutoff = (datetime.now(UTC) - timedelta(days=stale_days)).isoformat()
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT context_key, user_id FROM context_memories "
                "WHERE updated_at < ? "
                "AND (sensory_json != '[]' OR short_term_json != '[]')",
                (cutoff,),
            ).fetchall()
            return [(r["context_key"], r["user_id"]) for r in rows]

    def prune_context_volatile(self, context_key: str, user_id: str) -> int:
        """Clear sensory and short_term for a stale context, keep long_term."""
        with self._lock:
            assert self._conn is not None
            with self._conn:
                result = self._conn.execute(
                    "UPDATE context_memories SET sensory_json='[]', short_term_json='[]', updated_at=? "
                    "WHERE context_key=? AND user_id=? "
                    "AND (sensory_json != '[]' OR short_term_json != '[]')",
                    (_now_iso(), context_key, user_id),
                )
                return result.rowcount

    def run_maintenance(self) -> None:
        """Run PRAGMA optimize for query planner statistics."""
        with self._lock:
            assert self._conn is not None
            self._conn.execute("PRAGMA optimize")

    # -- Audit Logging --

    def log_reflection(
        self, user_id: str, input_summary: str, output_changes: str
    ) -> None:
        with self._lock:
            assert self._conn is not None
            with self._conn:
                self._conn.execute(
                    "INSERT INTO reflections (user_id, observations_json, result_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, input_summary, output_changes, _now_iso()),
                )

    def log_consolidation(
        self,
        context_key: str,
        user_id: str,
        input_sensory_count: int,
        old_long_term: str,
        new_long_term: str,
    ) -> None:
        with self._lock:
            assert self._conn is not None
            with self._conn:
                self._conn.execute(
                    "INSERT INTO consolidations (user_id, context_key, input_sensory_count, old_long_term, new_long_term, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        context_key,
                        input_sensory_count,
                        old_long_term,
                        new_long_term,
                        _now_iso(),
                    ),
                )

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
