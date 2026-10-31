# How the client and server reach each other: a unix socket where the platform has one,
# else a localhost TCP port. The TCP port and a random token are written to kokoro.port in
# KOKORO_HOME (private to the user), and every request must carry that token.
import json, os, secrets, socket

HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
SOCK = os.path.join(HOME, "kokoro.sock")
PORT_FILE = os.path.join(HOME, "kokoro.port")
USE_UNIX = hasattr(socket, "AF_UNIX") and os.name != "nt" and os.environ.get("KOKORO_TRANSPORT") != "tcp"


owned = {}  # path -> what it held when this server wrote it, so close() removes only its own


class NotRunning(Exception):
    """No server is listening."""


def listen():
    """The server's listening socket, or SystemExit when another server already listens."""
    if USE_UNIX:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(SOCK)
        except OSError:
            try:
                connect()[0].close()
                raise SystemExit(0)  # another server is already running
            except NotRunning:
                os.unlink(SOCK)  # stale socket from a dead server
                s.bind(SOCK)
        token = ""
        owned[SOCK] = os.stat(SOCK).st_ino
    else:
        try:
            connect()[0].close()
            raise SystemExit(0)
        except NotRunning:
            pass
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        token = secrets.token_hex(16)
        tmp = PORT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"port": s.getsockname()[1], "token": token}, f)
        os.replace(tmp, PORT_FILE)
        owned[PORT_FILE] = os.stat(PORT_FILE).st_ino
    s.listen(8)
    s.settimeout(15)
    return s, token


def close(s):
    """Closes the socket and removes the files this server made, never a newer server's."""
    s.close()
    for path, inode in owned.items():
        try:
            if os.stat(path).st_ino == inode:
                os.unlink(path)
        except FileNotFoundError:
            pass


def connect():
    """(connection to the running server, token to send), or NotRunning."""
    try:
        if USE_UNIX:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCK)
            return s, ""
        with open(PORT_FILE) as f:
            info = json.load(f)
        return socket.create_connection(("127.0.0.1", int(info["port"])), timeout=2), info["token"]
    except (OSError, ValueError, KeyError) as err:
        raise NotRunning() from err


def send(request):
    """Sends one JSON request; NotRunning when no server listens."""
    s, token = connect()
    try:
        s.sendall(json.dumps({**request, "token": token} if token else request).encode())
    finally:
        s.close()
