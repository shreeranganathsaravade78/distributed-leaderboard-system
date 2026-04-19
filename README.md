# 🏆 Distributed Leaderboard Server

A high-performance, TLS-secured leaderboard server supporting **500+ concurrent clients** with full concurrency safety, max-score conflict resolution, and consistent read-your-writes guarantees backed by SQLite WAL.

---

## ✨ Features

| Feature | Details |
|---|---|
| 🔒 **TLS Encryption** | All client-server traffic is TLS-encrypted (self-signed cert included) |
| 👤 **Auth** | bcrypt password hashing; registration + login flow |
| 🏅 **Dense Ranking** | Tied scores share the same rank (1, 1, 2 — not 1, 1, 3) |
| ⚡ **High Concurrency** | `ThreadPoolExecutor` with up to 500 workers; WAL journal mode for non-blocking reads |
| 🔁 **Max-Score Resolution** | `MAX(score, ?)` — a lower re-submission never overwrites a career high |
| 🧵 **Read-Your-Writes** | Per-thread SQLite connections ensure every write is immediately visible to the writer |
| ⏱️ **Idle Timeout** | Clients disconnected after 600 s of inactivity |
| 📊 **Stress Test** | 500-client concurrent benchmark with latency percentiles and success-rate verdict |

---

## 📁 Project Structure

```
leaderboard_project/
├── server.py               # Main server — ThreadPoolExecutor, TLS, command dispatch
├── client.py               # Interactive CLI client
├── leaderboard_engine.py   # Thread-safe rank cache with atomic dirty-flag
├── user_manager.py         # SQLite persistence, bcrypt auth, max-score logic
├── protocol.py             # Shared command/status constants + encode/decode helpers
├── perf_test_500.py        # 500-client concurrent stress test
└── certs/
    ├── server.crt          # Self-signed TLS certificate
    └── server.key          # TLS private key
```

---

## 🚀 Quick Start

### Prerequisites

```bash
pip install bcrypt
```

> Python **3.10+** required.

### 1 — Generate TLS certificates (first time only)

```bash
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key \
    -out certs/server.crt -days 365 -nodes \
    -subj "/CN=leaderboard"
```
or
```bash
cd certs
openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt -days 365 -nodes
```

### 2 — Start the server

```bash
python server.py
```

Listens on `0.0.0.0:8888` by default. Edit `HOST`, `PORT`, `CERTFILE`, `KEYFILE` at the top of `server.py` if needed.

### 3 — Connect with the client

```bash
python client.py
```

Edit `SERVER_HOST` in `client.py` to point to the server's IP address.

---

## 🖥️ Client Menu

```
╔══════════════════════════════╗
║   Distributed Leaderboard    ║
╠══════════════════════════════╣
║  1. Register                 ║
║  2. Login                    ║
║  3. Submit Score             ║
║  4. View Leaderboard         ║
║  5. Lookup Player            ║
║  6. Quit                     ║
╚══════════════════════════════╝
```

Password input is hidden via `getpass` — nothing is echoed to the terminal.

---

## 📡 Protocol

All messages are newline-delimited JSON over TLS TCP.

### Commands (client → server)

| Command | Required fields | Auth required |
|---|---|:---:|
| `REGISTER` | `username`, `password` | ✗ |
| `LOGIN` | `username`, `password` | ✗ |
| `UPDATE` | `score` | ✓ |
| `GET_TOP` | `n` (default 10, max 100) | ✗ |
| `GET_PLAYER` | `username` | ✗ |
| `QUIT` | — | ✗ |

### Response envelope

```json
{
  "status":  "ok" | "error",
  "message": "Human-readable string",
  "data":    { ... }
}
```

---

## 🗄️ Architecture

```
Client (TLS) ──► ClientHandler (thread)
                      │
                      ├─► UserManager       — SQLite (WAL, per-thread conn, bcrypt)
                      └─► LeaderboardEngine — dirty-flag cache, dense ranking
```

### Concurrency design

- **`ThreadPoolExecutor(max_workers=500)`** — one thread per connected client; bounded to prevent runaway resource use.
- **`PRAGMA journal_mode=WAL`** — readers never block writers; writers never block readers.
- **`PRAGMA busy_timeout=3000`** — contended threads retry for up to 3 s instead of raising immediately.
- **`threading.local()` connections** — each worker thread owns a persistent connection, providing read-your-writes consistency.
- **Atomic dirty-flag** — the leaderboard cache's `_dirty = True` is set *inside* the same `threading.Lock` as the DB write, so no reader can observe a stale cache after a successful update.

---

## ⚙️ Configuration Reference

### `server.py`

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8888` | TCP port |
| `CERTFILE` | `certs/server.crt` | TLS certificate path |
| `KEYFILE` | `certs/server.key` | TLS private key path |
| `IDLE_TIMEOUT` | `600` | Seconds before idle client is dropped |
| `MAX_WORKERS` | `500` | Thread pool ceiling |

### `client.py`

| Variable | Default | Description |
|---|---|---|
| `SERVER_HOST` | `192.168.56.1` | Server IP address |
| `SERVER_PORT` | `8888` | Server port |
| `CA_CERT` | `certs/server.crt` | CA certificate for verification |

---

## 🧪 Stress Test — 500 Concurrent Clients

```bash
# Raise file-descriptor limit first (Linux / macOS)
ulimit -n 2048

python perf_test_500.py --host 192.168.56.1 --port 8888
```

**Optional flags:**

```
--clients 500    Number of concurrent clients (default: 500)
--workers 100    Parallel threads for Phase 1 registration (default: 100)
```

### What the test does

| Phase | Action |
|---|---|
| **Phase 1** | Registers + logs in all N users in parallel (with exponential back-off retry) |
| **Phase 2** | All N clients fire simultaneously via `threading.Barrier` — submit score + read back rank |
| **Phase 3** | Prints throughput, latency percentiles (p50/p95/p99/max), rank distribution, and a Pass/Fail verdict |

### Sample output

```
=======================================================
 500-Client Stress Test — Results
=======================================================
  Total clients    : 500
  Successful       : 498
  Failed           : 2
  Wall-clock time  : 4.83 s

  Throughput       : 103.1 req/s
  Latency avg      : 312.4 ms
  Latency p50      : 287.1 ms
  Latency p95      : 621.3 ms
  Latency p99      : 814.7 ms
  Latency max      : 923.1 ms

  Rank range seen  : 1 – 498
  Unique ranks     : 492

  Success rate     : 99.6%
  Verdict          : ✓ PASS  (threshold: 95%)
=======================================================
```

---

## 🐛 Known Issues & Limitations

- **Self-signed certificate** — clients must load `certs/server.crt` as the trusted CA. Replace with a CA-signed cert for production.
- **Single-node SQLite** — suitable for hundreds of concurrent users; for multi-server deployments consider PostgreSQL with connection pooling.
- **No session tokens** — authentication state lives in the server-side `ClientHandler` object for the lifetime of the TCP connection.

---

## 📜 Changelog

### v2 — Concurrency & Correctness Fixes

**Fix 1 — Max-score conflict resolution** (`user_manager.py`)  
`UPDATE users SET score=MAX(score, ?)` — a lower re-submission can never erase a career high. Previously, scores were unconditionally overwritten.

**Fix 2 — Atomic dirty-flag** (`leaderboard_engine.py`)  
`self._dirty = True` is now set *inside* `self.lock`, atomically with the DB write. Previously it was set after the lock released, allowing a narrow window where readers could serve a stale cache.

**Fix 3 — WAL journal mode + busy timeout** (`user_manager.py`)  
`PRAGMA journal_mode=WAL` eliminates read/write lock contention. `PRAGMA busy_timeout=3000` prevents immediate `OperationalError` under write pressure.

**Fix 4 — Per-thread connections** (`user_manager.py`)  
`threading.local()` gives each worker thread its own persistent SQLite connection, guaranteeing read-your-writes consistency. Previously a new connection was opened on every call.

---

## 📄 License

MIT
