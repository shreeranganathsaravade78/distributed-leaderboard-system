import json

# Command strings
CMD_REGISTER   = "REGISTER"
CMD_LOGIN      = "LOGIN"
CMD_UPDATE     = "UPDATE"
CMD_GET_TOP    = "GET_TOP"
CMD_GET_PLAYER = "GET_PLAYER"
CMD_QUIT       = "QUIT"

# Response status
STATUS_OK    = "ok"
STATUS_ERROR = "error"

def encode_message(data: dict) -> str:
    """Convert dict to JSON + newline."""
    return json.dumps(data) + "\n"

def decode_message(line: str) -> dict:
    """Parse JSON from a string (without newline)."""
    return json.loads(line)
