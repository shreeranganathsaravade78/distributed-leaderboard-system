import socket
import ssl
import json
import getpass   # hides password input (req #13)
from protocol import *

# ------------------------------------------------------------------
# Configuration  — change SERVER_HOST to the server machine's IP
# ------------------------------------------------------------------
SERVER_HOST = "192.168.56.1"
SERVER_PORT = 8888
CA_CERT     = "certs/server.crt"   # self-signed CA cert for verification


# ------------------------------------------------------------------
# Network helpers
# ------------------------------------------------------------------
def connect():
    """Create TLS-wrapped socket and connect to server."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    # Use CERT_REQUIRED + load_verify_locations for production (req: TLS verified)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(CA_CERT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ssl_sock = context.wrap_socket(sock, server_hostname=SERVER_HOST)
    ssl_sock.connect((SERVER_HOST, SERVER_PORT))
    return ssl_sock


def send_command(sock, cmd_dict: dict):
    sock.sendall(encode_message(cmd_dict).encode())


def recv_response(sock) -> dict | None:
    """Read one newline-terminated JSON response."""
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if b"\n" in data:
            line, _ = data.split(b"\n", 1)
            return json.loads(line.decode())


# ------------------------------------------------------------------
# Menu (no Ping option — req #5 / #9)
# ------------------------------------------------------------------
def display_menu(authenticated: bool, username: str = ""):
    who = f"  Logged in as: {username}" if authenticated else "  Not logged in"
    print(f"""
╔══════════════════════════════╗
║   Distributed Leaderboard    ║
╠══════════════════════════════╣
║  1. Register                 ║
║  2. Login                    ║
║  3. Submit Score             ║
║  4. View Leaderboard         ║
║  5. Lookup Player            ║
║  6. Quit                     ║
╠══════════════════════════════╣
║ {who:<28} ║
╚══════════════════════════════╝""")





# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    try:
        sock = connect()
        print(f"[+] Connected to {SERVER_HOST}:{SERVER_PORT}")
    except Exception as e:
        print(f"[!] Connection failed: {e}")
        return

    authenticated = False
    current_user  = ""

    while True:
        display_menu(authenticated, current_user)
        choice = input("Choice: ").strip()

        # ── 1. Register ──────────────────────────────────────────────
        if choice == "1":
            username = input("  Username: ").strip()
            password = getpass.getpass("  Password: ")   # hidden (req #13)
            if not username or not password:
                print("  Username and password cannot be empty.")
                continue
            send_command(sock, {"cmd": CMD_REGISTER, "username": username, "password": password})
            resp = recv_response(sock)
            print(f"  [Server] {resp['message']}")

        # ── 2. Login ─────────────────────────────────────────────────
        elif choice == "2":
            username = input("  Username: ").strip()
            password = getpass.getpass("  Password: ")   # hidden (req #13)
            send_command(sock, {"cmd": CMD_LOGIN, "username": username, "password": password})
            resp = recv_response(sock)
            print(f"  [Server] {resp['message']}")
            if resp["status"] == STATUS_OK:
                authenticated = True
                current_user  = username

        # ── 3. Submit Score ───────────────────────────────────────────
        elif choice == "3":
            if not authenticated:
                print("  Please login first.")
                continue
            raw = input("  Score: ").strip()
            try:
                score = int(raw)
            except ValueError:
                print("  Please enter a whole number.")
                continue
            send_command(sock, {"cmd": CMD_UPDATE, "score": score})
            resp = recv_response(sock)
            if resp is None:
                print("[!] Server closed connection.")
                break
            if resp["status"] == STATUS_OK:
                updated = resp.get("data", {}).get("score", score)
                print(f"  ✓ Score recorded: {updated}")
            else:
                print(f"  ✗ {resp['message']}")

        # ── 4. View Leaderboard ───────────────────────────────────────
        elif choice == "4":
            raw_n = input("  Top N players (default 10): ").strip()
            n = int(raw_n) if raw_n.isdigit() else 10
            send_command(sock, {"cmd": CMD_GET_TOP, "n": n})
            resp = recv_response(sock)
            if resp["status"] == STATUS_OK:
                rows = resp.get("data", [])
                if not rows:
                    print("  Leaderboard is empty.")
                else:
                    # Header
                    print(f"\n  {'Rank':<5} {'Player':<20} {'Score':>8}  {'Last Update'}")
                    print("  " + "─" * 58)
                    for p in rows:
                        # Req #8: show timestamp / last-update time
                        ts = p.get("last_update", "—")
                        print(f"  {p['rank']:<5} {p['username']:<20} {p['score']:>8}  {ts}")
            else:
                print(f"  [Server] {resp['message']}")

        # ── 5. Lookup Player ──────────────────────────────────────────
        elif choice == "5":
            username = input("  Username: ").strip()
            send_command(sock, {"cmd": CMD_GET_PLAYER, "username": username})
            resp = recv_response(sock)
            if resp["status"] == STATUS_OK:
                d = resp["data"]
                print(f"\n  Player   : {d['username']}")
                print(f"  Score    : {d['score']}")
                print(f"  Rank     : {d['rank']}")
                print(f"  Updated  : {d['last_update']}")
            else:
                print(f"  [Server] {resp['message']}")

        # ── 6. Quit ───────────────────────────────────────────────────
        elif choice == "6":
            send_command(sock, {"cmd": CMD_QUIT})
            recv_response(sock)
            break

        else:
            # Req #4: no repeated error spam — just one clean line
            print("  Invalid choice. Enter 1–6.")

    sock.close()
    print("\n[+] Disconnected. Goodbye!")


if __name__ == "__main__":
    main()