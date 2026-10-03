"""Minimal local SMTP sink for seeding runs.

The app never sends real email while this is the configured SMTP server: every
message is appended to a log file instead of leaving the machine. It speaks just
enough SMTP for `smtplib.SMTP(...)` (no STARTTLS, no AUTH), which is what the
seed run configures via SMTP_USE_TLS=false and an empty SMTP_USER.

Usage:
    python seed/mock_smtp.py --host 127.0.0.1 --port 2525 --log .logs/mock_smtp.log
"""
import argparse
import datetime
import socketserver
import sys
import threading


class _State(threading.local):
    def __init__(self):
        self.in_data = False
        self.buffer = []
        self.mail_from = ""
        self.rcpt = ""


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        state = _State()
        self._reply(b"220 localhost Schoolers mock SMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if state.in_data:
                if line.strip() == b".":
                    state.in_data = False
                    self._store(state.mail_from, state.rcpt, b"".join(state.buffer))
                    state.buffer = []
                    self._reply(b"250 2.0.0 Ok: queued")
                else:
                    state.buffer.append(line)
                continue
            command = line.decode("utf-8", "replace").strip()
            verb = command.upper()
            if verb.startswith("EHLO"):
                for part in (b"250-localhost", b"250-8BITMIME", b"250 SIZE 10485760"):
                    self.wfile.write(part + b"\r\n")
            elif verb.startswith("HELO"):
                self._reply(b"250 localhost")
            elif verb.startswith("MAIL FROM"):
                state.mail_from = command
                self._reply(b"250 2.1.0 Ok")
            elif verb.startswith("RCPT TO"):
                state.rcpt = command
                self._reply(b"250 2.1.5 Ok")
            elif verb == "DATA":
                state.in_data = True
                self._reply(b"354 End data with <CR><LF>.<CR><LF>")
            elif verb == "RSET":
                state.__init__()
                self._reply(b"250 2.0.0 Ok")
            elif verb == "NOOP":
                self._reply(b"250 2.0.0 Ok")
            elif verb == "QUIT":
                self._reply(b"221 2.0.0 Bye")
                break
            else:
                self._reply(b"250 2.0.0 Ok")

    def _reply(self, payload: bytes):
        self.wfile.write(payload + b"\r\n")

    def _store(self, mail_from: str, rcpt: str, body: bytes):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(self.server.log_path, "ab") as fh:
            fh.write(b"\n===== MESSAGE =====\n")
            fh.write(f"{stamp} {mail_from} -> {rcpt}\n".encode())
            fh.write(body)
        sys.stdout.write(f"[mock-smtp] captured message {mail_from} -> {rcpt}\n")
        sys.stdout.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, handler, log_path):
        super().__init__(addr, handler)
        self.log_path = log_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--log", default=".logs/mock_smtp.log")
    args = ap.parse_args()
    open(args.log, "ab").close()
    server = Server((args.host, args.port), Handler, args.log)
    print(f"[mock-smtp] listening on {args.host}:{args.port}, logging to {args.log}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
