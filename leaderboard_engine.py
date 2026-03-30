import threading
import time


class LeaderboardEngine:
    """
    Thread-safe leaderboard with:
      - Dirty-flag sorted cache (rebuild only on change)
      - Tied scores receive the SAME rank (dense / standard competition ranking)
      - Atomic dirty-flag: set inside the same lock as the DB write so no
        reader can ever observe a stale cache after a successful update
      - Max-score conflict resolution delegated to UserManager
    """

    def __init__(self, user_manager):
        self.user_manager = user_manager
        self.lock = threading.Lock()
        self._cached = []   # list of (username, score, timestamp)
        self._dirty  = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _rebuild_cache(self):
        """Sort all players: score DESC, then username ASC for tie-breaking display."""
        players = self.user_manager.get_all_players()
        players.sort(key=lambda x: (-x[1], x[0]))
        self._cached = players
        self._dirty  = False

    def _assign_ranks(self, entries):
        """
        Return list of dicts with dense ranking:
          Same score → same rank.
          e.g. scores [100, 100, 80] → ranks [1, 1, 2]
        """
        result     = []
        rank       = 0
        prev_score = None
        position   = 0          # actual position counter (1-based)
        for username, score, ts in entries:
            position += 1
            if score != prev_score:
                rank       = position
                prev_score = score
            result.append({
                "username":    username,
                "score":       score,
                "rank":        rank,
                "last_update": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(ts)
                ) if ts else "—",
            })
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_top_n(self, n: int):
        """Return top-N players as list of ranked dicts."""
        with self.lock:
            if self._dirty:
                self._rebuild_cache()
            subset = self._cached[:n]
        return self._assign_ranks(subset)

    def update_score(self, username: str, new_score: int):
        """
        Concurrent-safe score update.

        The dirty flag is set INSIDE self.lock — atomically with the DB
        write — so any reader acquiring self.lock afterwards is guaranteed
        to see _dirty=True and will rebuild the cache before serving data.

        Returns (success, message, stored_score).
        """
        timestamp = int(time.time())
        with self.lock:
            success, msg, stored = self.user_manager.update_score(
                username, new_score, timestamp
            )
            if success:
                self._dirty = True          # atomic: inside the same lock
        return success, msg, stored

    def get_player_rank_and_score(self, username: str):
        """Return (rank, score, last_update_str) or (None, None, None)."""
        with self.lock:
            if self._dirty:
                self._rebuild_cache()
            cache = self._cached[:]

        ranked = self._assign_ranks(cache)
        for entry in ranked:
            if entry["username"] == username:
                return entry["rank"], entry["score"], entry["last_update"]
        return None, None, None

    def get_performance_stats(self):
        """Return basic stats dict for the performance-check script."""
        with self.lock:
            if self._dirty:
                self._rebuild_cache()
            total = len(self._cached)
            top   = self._cached[0] if self._cached else None
        return {
            "total_players": total,
            "top_player":    top[0] if top else None,
            "top_score":     top[1] if top else None,
        }
