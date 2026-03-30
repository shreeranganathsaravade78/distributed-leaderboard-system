import threading
import time
import sqlite3
import bcrypt

DB_PATH = "leaderboard.db"


class UserManager:
    """
    Thread-safe user store with:
      - Per-thread SQLite connections (threading.local) for read-your-writes guarantee
      - WAL journal mode for concurrent reads alongside writes
      - busy_timeout so contended threads retry instead of failing immediately
      - Max-score conflict resolution: a lower submission never overwrites a high score
    """

    def __init__(self):
        self.lock = threading.Lock()
        self._local = threading.local()   # per-thread connection cache
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """
        Return a per-thread SQLite connection.
        Each thread gets its own connection so reads always reflect that
        thread's own writes (read-your-writes guarantee).
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL: readers don't block writers, writers don't block readers
            conn.execute("PRAGMA journal_mode=WAL")
            # Retry up to 3 s under write contention before raising OperationalError
            conn.execute("PRAGMA busy_timeout=3000")
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    # Database bootstrap
    # ------------------------------------------------------------------
    def _init_db(self):
        with self.lock:
            conn = self._conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username      TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    score         INTEGER NOT NULL DEFAULT 0,
                    timestamp     INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------
    def _hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register(self, username: str, password: str):
        """Register a new user. Returns (success, message)."""
        if not username or not password:
            return False, "Username and password required"
        if len(username) > 32 or len(password) > 128:
            return False, "Username/password too long"
        with self.lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT 1 FROM users WHERE username=?", (username,)
            ).fetchone()
            if row:
                return False, "Username already exists"
            ph = self._hash_password(password)
            conn.execute(
                "INSERT INTO users(username, password_hash, score, timestamp) VALUES(?,?,0,0)",
                (username, ph)
            )
            conn.commit()
        return True, "Registration successful"

    def login(self, username: str, password: str):
        """Authenticate user. Returns (success, message)."""
        with self.lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
        if not row:
            return False, "User not found"
        if not self._verify_password(password, row["password_hash"]):
            return False, "Incorrect password"
        return True, "Login successful"

    def update_score(self, username: str, new_score: int, timestamp: int):
        """
        Update score using max-score conflict resolution.

        The stored score only increases — submitting a lower score never
        overwrites a previously achieved high score.  The server-generated
        timestamp is still recorded so last-update display stays accurate.

        Returns (success, message, stored_score).
        """
        with self.lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT score FROM users WHERE username=?", (username,)
            ).fetchone()
            if not row:
                return False, "User not found", None

            # MAX(score, ?) is a single atomic SQL expression — no TOCTOU race
            conn.execute(
                "UPDATE users SET score=MAX(score, ?), timestamp=? WHERE username=?",
                (new_score, timestamp, username)
            )
            conn.commit()

            # Read back the value that was actually stored
            stored_row = conn.execute(
                "SELECT score FROM users WHERE username=?", (username,)
            ).fetchone()
            stored = stored_row["score"]

        return True, "Score updated", stored

    def get_player(self, username: str):
        """Return player data dict or None."""
        with self.lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT username, score, timestamp FROM users WHERE username=?",
                (username,)
            ).fetchone()
        if row:
            return dict(row)
        return None

    def get_all_players(self):
        """Return list of (username, score, timestamp) for all users."""
        with self.lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT username, score, timestamp FROM users"
            ).fetchall()
        return [(r["username"], r["score"], r["timestamp"]) for r in rows]
