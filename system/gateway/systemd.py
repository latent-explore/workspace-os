"""Tell systemd we are alive (sd_notify), without any dependency."""
import os
import socket


def notify(state: str) -> None:
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return  # not running under systemd
    if path.startswith("@"):
        path = "\0" + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(path)
        sock.sendall(state.encode())
