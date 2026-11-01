# How the client and server reach each other: a unix socket where the platform has one,
# else a localhost TCP port. The TCP port and a random token are written to kokoro.port in
# KOKORO_HOME (private to the user), and every request must carry that token.
import json, os, secrets, socket, time

HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
SOCK = os.path.join(HOME, "kokoro.sock")
PORT_FILE = os.path.join(HOME, "kokoro.port")
USE_UNIX = hasattr(socket, "AF_UNIX") and os.name != "nt" and os.environ.get("KOKORO_TRANSPORT") != "tcp"


owned = {}  # path -> what it held when this server wrote it, so close() removes only its own
LOCK = os.path.join(HOME, "server.lock")
LOCK_WAIT = 10  # seconds a new server waits for one that is shutting down
_lock = None  # held open for the server's whole life


class NotRunning(Exception):
    """No server is listening."""


def try_lock(f):
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def single():
    """Holds the server lock, or SystemExit when another server keeps it. Only the holder touches
    the socket, so two servers started at once can never both run (each would speak every reply)."""
    global _lock
    os.makedirs(HOME, exist_ok=True)
    f = open(LOCK, "a+")
    deadline = time.time() + LOCK_WAIT
    while not try_lock(f):
        if time.time() > deadline:
            raise SystemExit(0)  # another server is running
        time.sleep(0.2)
    _lock = f


def retire_unlocked():
    """Asks a server that answers without holding the lock (a copy from before the lock, such as
    an old VS Code extension's) to quit, and waits for it: two servers would speak every reply."""
    try:
        send({"mode": "quit"})
    except NotRunning:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            connect()[0].close()
        except NotRunning:
            return
        time.sleep(0.2)


def listen():
    """The server's listening socket, or SystemExit when another server is running."""
    single()
    retire_unlocked()
    if USE_UNIX:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(SOCK)  # left by a server that died, or by the old one retired above
        except FileNotFoundError:
            pass
        s.bind(SOCK)
        token = ""
        owned[SOCK] = os.stat(SOCK).st_ino
    else:
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
