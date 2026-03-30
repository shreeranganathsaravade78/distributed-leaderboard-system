import socket
import ssl
import threading
import json
import time
from concurrent.futures import ThreadPoolExecutor
from protocol import *
from user_manager import UserManager
from leaderboard_engine import LeaderboardEngine

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
HOST         = "0.0.0.0"
PORT         = 8888
CERTFILE     = "certs/server.crt"
KEYFILE      = "certs/server.key"
IDLE_TIMEOUT = 600         # seconds — client disconnected after 60 s idle (req #10)
MAX_WORKERS  = 500          # bounded thread pool prevents resource exhaustion


# ------------------------------------------------------------------
# Per-client handler
# ------------------------------------------------------------------
class ClientHandler:
    def __init__(self, client_sock, addr, user_manager, leaderboard):
        self.sock        = client_sock
        self.addr        = addr
        self.um          = user_manager
        self.lb          = leaderboard
        self.current_user = None
        self.running      = True

    def run(self):
        print(f"[+] Connection from {self.addr}")
        self.sock.settimeout(IDLE_TIMEOUT)
        try:
            rfile = self.sock.makefile("r", encoding="utf-8")
            while self.running:
                try:
                    line = rfile.readline()
                    if not line:
                        break
                    self._handle(line.strip())
                except socket.timeout:
                    self._send(STATUS_ERROR, "Idle timeout — connection closed")
                    break
                except Exception as e:
                    print(f"[!] {self.addr}: {e}")
                    break
        finally:
            try:
                self.sock.close()
            except Exception:
                pass
            print(f"[-] {self.addr} disconnected")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _handle(self, line: str):
        try:
            msg = decode_message(line)
        except json.JSONDecodeError:
            self._send(STATUS_ERROR, "Invalid JSON")
            return

        cmd = msg.get("cmd")
        dispatch = {
            CMD_REGISTER:   self._register,
            CMD_LOGIN:      self._login,
            CMD_UPDATE:     self._update,
            CMD_GET_TOP:    self._get_top,
            CMD_GET_PLAYER: self._get_player,
            CMD_QUIT:       self._quit,
        }
        handler = dispatch.get(cmd)
        if handler:
            handler(msg)
        else:
            self._send(STATUS_ERROR, f"Unknown command: {cmd}")

    # ------------------------------------------------------------------
    # Response helper
    # ------------------------------------------------------------------
    def _send(self, status: str, message: str, data=None):
        resp = {"status": status, "message": message}
        if data is not None:
            resp["data"] = data
        try:
            self.sock.sendall(encode_message(resp).encode())
        except Exception as e:
            print(f"[!] Send failed to {self.addr}: {e}")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    def _register(self, msg):
        success, text = self.um.register(
            msg.get("username", ""), msg.get("password", "")
        )
        self._send(STATUS_OK if success else STATUS_ERROR, text)

    def _login(self, msg):
        success, text = self.um.login(
            msg.get("username", ""), msg.get("password", "")
        )
        if success:
            self.current_user = msg.get("username")
        self._send(STATUS_OK if success else STATUS_ERROR, text)

    def _update(self, msg):
        if not self.current_user:
            self._send(STATUS_ERROR, "Login required")
            return
        try:
            new_score = int(msg.get("score"))
        except (TypeError, ValueError):
            self._send(STATUS_ERROR, "Invalid score")
            return
        # Server generates timestamp — client never controls it (req #6, conflict fix)
        success, text, updated = self.lb.update_score(self.current_user, new_score)
        data = {"score": updated} if updated is not None else None
        self._send(STATUS_OK if success else STATUS_ERROR, text, data)

    def _get_top(self, msg):
        try:
            n = int(msg.get("n", 10))
            n = max(1, min(n, 100))
        except ValueError:
            self._send(STATUS_ERROR, "Invalid number")
            return
        top  = self.lb.get_top_n(n)
        self._send(STATUS_OK, f"Top {len(top)} players", top)

    def _get_player(self, msg):
        username = msg.get("username", "").strip()
        if not username:
            self._send(STATUS_ERROR, "Username required")
            return
        player = self.um.get_player(username)
        if not player:
            self._send(STATUS_ERROR, "Player not found")
            return
        rank, score, last_update = self.lb.get_player_rank_and_score(username)
        data = {
            "username":    username,
            "score":       player["score"],
            "rank":        rank,
            "last_update": last_update,
        }
        self._send(STATUS_OK, "Player found", data)

    def _quit(self, msg):
        self._send(STATUS_OK, "Goodbye")
        self.running = False


# ------------------------------------------------------------------
# Main server loop
# ------------------------------------------------------------------
def main():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERTFILE, KEYFILE)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(10)
    print(f"[*] Server listening on {HOST}:{PORT}  (max {MAX_WORKERS} clients)")

    um = UserManager()
    lb = LeaderboardEngine(um)

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    try:
        while True:
            client_sock, addr = sock.accept()
            ssl_client = context.wrap_socket(client_sock, server_side=True)
            handler = ClientHandler(ssl_client, addr, um, lb)
            executor.submit(handler.run)
    except KeyboardInterrupt:
        print("\n[!] Shutting down.")
    finally:
        executor.shutdown(wait=False)
        sock.close()


if __name__ == "__main__":
    main()
