"""
perf_test_500.py — 500 Concurrent Client Stress Test
=====================================================
Before running:
    ulimit -n 2048          # raise open-file-descriptor limit (Linux/Mac)
    python perf_test_500.py --host 192.168.56.1

What this does:
  - Registers + logs in 500 unique users (perf500_user_0 … perf500_user_499)
  - Fires all 500 clients simultaneously using a Barrier
  - Each client submits one score, reads back its rank, then disconnects
  - Reports: success rate, latency percentiles, throughput, error breakdown
"""

import socket
import ssl
import json
import time
import threading
import argparse
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_HOST     = "192.168.56.1"
DEFAULT_PORT     = 8888
CA_CERT          = "certs/server.crt"
USER_PREFIX      = "perf500_user_"
PASSWORD         = "Test@1234"
NUM_CLIENTS      = 500
CONNECT_TIMEOUT  = 10    # seconds to wait for TLS handshake
OP_TIMEOUT       = 15    # seconds for each send/recv
RAMP_BATCH       = 50    # connect N clients at a time before firing them all
RAMP_DELAY       = 0.05  # seconds between batches during connection ramp-up


# ── Low-level helpers ─────────────────────────────────────────────────────────
def make_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(CA_CERT)
    return ctx


def connect(host, port, ctx):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    ssl_sock = ctx.wrap_socket(sock, server_hostname=host)
    ssl_sock.connect((host, port))
    ssl_sock.settimeout(OP_TIMEOUT)
    return ssl_sock


def transact(sock, cmd_dict):
    payload = json.dumps(cmd_dict) + "\n"
    sock.sendall(payload.encode())
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if b"\n" in data:
            line, _ = data.split(b"\n", 1)
            return json.loads(line.decode())


# ── Phase 1 — Register + login all users in PARALLEL ─────────────────────────
def setup_users(host, port, n=NUM_CLIENTS, max_workers=50):
    print(f"\n[Phase 1] Registering + logging in {n} users …")
    ctx = make_ssl_context()
    socks  = []
    errors = []
    lock   = threading.Lock()
    connected_count = [0]  # mutable counter for progress tracking
    t0 = time.perf_counter()

    def register_and_login(i):
        uname  = USER_PREFIX + str(i)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                s = connect(host, port, ctx)
                transact(s, {"cmd": "REGISTER", "username": uname, "password": PASSWORD})
                r = transact(s, {"cmd": "LOGIN",    "username": uname, "password": PASSWORD})
                if r and r["status"] == "ok":
                    with lock:
                        socks.append((i, s))
                        connected_count[0] += 1
                        if connected_count[0] % 50 == 0:
                            print(f"  {connected_count[0]}/{n} connected …")
                else:
                    with lock:
                        errors.append(f"user {i}: login failed — {r}")
                    s.close()
                return   # success — exit retry loop
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(0.2 * (attempt + 1))  # back-off: 0.2s, 0.4s, 0.6s …
                else:
                    with lock:
                        errors.append(f"user {i}: {e}")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(register_and_login, i) for i in range(n)]
        for f in as_completed(futures):
            pass

    elapsed = time.perf_counter() - t0
    print(f"  Done in {elapsed:.1f}s  |  connected={len(socks)}  errors={len(errors)}")
    for e in errors[:10]:
        print(f"    [ERR] {e}")
    if len(errors) > 10:
        print(f"    … and {len(errors)-10} more errors")
    return socks


# ── Phase 2 — Fire all clients simultaneously ─────────────────────────────────
def stress_test(socks):
    n = len(socks)
    print(f"\n[Phase 2] Firing {n} clients simultaneously …")

    results = []
    errors  = []
    lock    = threading.Lock()
    barrier = threading.Barrier(n)   # all threads wait here then fire together

    def worker(idx, sock):
        score = idx * 7 + 42          # deterministic unique score per user
        try:
            barrier.wait(timeout=30)  # synchronise — all clients fire at once
            t0 = time.perf_counter()

            # Submit score
            r1 = transact(sock, {"cmd": "UPDATE", "score": score})
            if not r1 or r1["status"] != "ok":
                raise RuntimeError(f"UPDATE failed: {r1}")

            # Read rank back immediately (read-your-writes check)
            uname = USER_PREFIX + str(idx)
            r2 = transact(sock, {"cmd": "GET_PLAYER", "username": uname})

            elapsed_ms = (time.perf_counter() - t0) * 1000
            with lock:
                results.append({
                    "idx":        idx,
                    "ok":         True,
                    "latency_ms": elapsed_ms,
                    "rank":       r2["data"]["rank"] if r2 and r2["status"] == "ok" else None,
                })
        except Exception as e:
            with lock:
                errors.append(f"user {idx}: {e}")
                results.append({"idx": idx, "ok": False, "latency_ms": None, "rank": None})
        finally:
            try:
                sock.close()
            except Exception:
                pass

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(worker, idx, sock) for idx, sock in socks]
        for f in as_completed(futures):
            pass   # results collected via shared list
    total_s = time.perf_counter() - t_start

    return results, errors, total_s


# ── Phase 3 — Report ──────────────────────────────────────────────────────────
def report(results, errors, total_s):
    print(f"\n{'='*55}")
    print(" 500-Client Stress Test — Results")
    print(f"{'='*55}")

    ok_results = [r for r in results if r["ok"]]
    fail_count = len(results) - len(ok_results)

    print(f"  Total clients    : {len(results)}")
    print(f"  Successful       : {len(ok_results)}")
    print(f"  Failed           : {fail_count}")
    print(f"  Error count      : {len(errors)}")
    print(f"  Wall-clock time  : {total_s:.2f} s")

    if ok_results:
        rps = len(ok_results) / total_s
        lats = sorted(r["latency_ms"] for r in ok_results)
        p50  = lats[len(lats)//2]
        p95  = lats[int(len(lats)*0.95)]
        p99  = lats[int(len(lats)*0.99)]
        avg  = statistics.mean(lats)
        mx   = lats[-1]
        print(f"\n  Throughput       : {rps:.1f} req/s")
        print(f"  Latency avg      : {avg:.1f} ms")
        print(f"  Latency p50      : {p50:.1f} ms")
        print(f"  Latency p95      : {p95:.1f} ms")
        print(f"  Latency p99      : {p99:.1f} ms")
        print(f"  Latency max      : {mx:.1f} ms")

    # Rank distribution sanity check
    ranks = [r["rank"] for r in ok_results if r["rank"] is not None]
    if ranks:
        print(f"\n  Rank range seen  : {min(ranks)} – {max(ranks)}")
        print(f"  Unique ranks     : {len(set(ranks))}")

    if errors:
        print(f"\n  First 10 errors:")
        for e in errors[:10]:
            print(f"    [ERR] {e}")
        if len(errors) > 10:
            print(f"    … and {len(errors)-10} more")

    success_pct = 100 * len(ok_results) / max(len(results), 1)
    print(f"\n  Success rate     : {success_pct:.1f}%")
    verdict = "✓ PASS" if success_pct >= 95 else "✗ FAIL"
    print(f"  Verdict          : {verdict}  (threshold: 95%)")
    print(f"{'='*55}\n")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    global NUM_CLIENTS

    p = argparse.ArgumentParser(description="500-client concurrent stress test")
    p.add_argument("--host",    default=DEFAULT_HOST)
    p.add_argument("--port",    type=int, default=DEFAULT_PORT)
    p.add_argument("--clients", type=int, default=NUM_CLIENTS,
                   help="Number of concurrent clients (default 500)")
    p.add_argument("--workers", type=int, default=100,
                   help="Parallel threads for Phase 1 registration (default 100)")
    args = p.parse_args()

    NUM_CLIENTS = args.clients

    print("=" * 55)
    print(f" 500-Client Stress Test  →  {args.host}:{args.port}")
    print("=" * 55)
    print("\n Pre-flight checklist:")
    print("   ✓ Run:  ulimit -n 2048   (before starting this script)")
    print("   ✓ Server MAX_WORKERS must be >= this client count")
    print()

    socks = setup_users(args.host, args.port, args.clients, args.workers)
    if not socks:
        print("[!] No connections established — aborting.")
        return

    results, errors, total_s = stress_test(socks)
    report(results, errors, total_s)


if __name__ == "__main__":
    main()