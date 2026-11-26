"""Build and serve the local reconciliation UI and its Python workspace API."""

import argparse
import fcntl
import json
import mimetypes
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "src"))

from reconcile_agent.business.errors import AdapterError
from reconcile_agent.tools.errors import ToolError
from reconcile_agent.web_workspace import WebWorkspace, WorkspaceError


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, workspace, dist):
        self.workspace = workspace
        self.dist = dist.resolve()
        super().__init__(address, RequestHandler)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class RequestHandler(BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")

    def _guard(self):
        host = self.headers.get("Host")
        if host not in self.server.hosts:
            raise WorkspaceError(403, "ORIGIN_DENIED")
        if self.command == "POST":
            if (self.headers.get("Origin") != f"http://{host}" or self.headers.get("X-Workspace") != "local-mvp"):
                raise WorkspaceError(403, "ORIGIN_DENIED")
            if self.headers.get_content_type() != "application/json":
                raise WorkspaceError(415, "INVALID_REQUEST")

    def _dispatch(self):
        self._guard()
        path = urlsplit(self.path).path
        read = self.command in ("GET", "HEAD")
        if read and path == "/api/workspace":
            self._json(200, {"scenarios": self.server.workspace.list_cases(), "model": self.server.workspace.model})
            return
        case_route = re.fullmatch(r"/api/cases/([a-z_]+)(/actions)?", path)
        if case_route and read and case_route[2] is None:
            self._json(200, self.server.workspace.case(case_route[1]))
        elif case_route and not read and case_route[2]:
            raw_size = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[1-9][0-9]{0,3}", raw_size) or int(raw_size) > 2048:
                raise WorkspaceError(400, "INVALID_REQUEST")
            args = json.loads(self.rfile.read(int(raw_size)))
            if (type(args) is not dict or set(args) != {"action", "revision"} or type(args["action"]) is not str
                or type(args["revision"]) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", args["revision"]) is None):
                raise WorkspaceError(400, "INVALID_REQUEST")
            self._json(200, self.server.workspace.act(case_route[1], args["action"], args["revision"]))
        elif read and (path == "/" or re.fullmatch(r"/assets/[A-Za-z0-9_.-]+", path)):
            file = self.server.dist / ("index.html" if path == "/" else path.lstrip("/"))
            if not file.is_file() or not file.resolve().is_relative_to(self.server.dist):
                raise WorkspaceError(404, "NOT_FOUND")
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            self._send(200, file.read_bytes(), content_type)
        else:
            raise WorkspaceError(404, "NOT_FOUND")

    def do_GET(self):
        try:
            self.connection.settimeout(12)
            self._dispatch()
        except WorkspaceError as exc:
            self._json(exc.status, {"error": exc.code})
        except (AdapterError, ToolError) as exc:
            self._json(409, {"error": exc.code})
        except (ValueError, UnicodeError):
            self._json(400, {"error": "INVALID_REQUEST"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.log_error("workspace failure: %s", type(exc).__name__)
            self._json(500, {"error": "LOCAL_RUNTIME_ERROR"})

    do_POST = do_GET
    do_HEAD = do_GET


def build_web():
    dist = WEB / "dist"
    inputs = [WEB / "index.html", *WEB.glob("*.js"), *WEB.glob("*.json"),
              *(path for path in (WEB / "src").rglob("*") if path.is_file())]
    if (dist / "index.html").is_file() and (dist / "index.html").stat().st_mtime >= max(p.stat().st_mtime for p in inputs):
        return dist
    if not (WEB / "node_modules" / ".bin" / "vite").exists():
        print("Preparing locked web dependencies...", flush=True)
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=WEB, check=True)
    subprocess.run(["npm", "run", "build"], cwd=WEB, check=True)
    return dist


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "generated_data" / "web-mvp")
    parser.add_argument("--model", help="Optional model already installed in local Ollama.")
    args = parser.parse_args()

    lock_fd = None
    try:
        dist = build_web()
        root = args.data_dir.absolute()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink():
            raise OSError("Choose a physical workspace directory for --data-dir.")
        lock_fd = os.open(root / ".workspace.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError("This web workspace is already served. Open its URL or use a different --data-dir.") from exc
        workspace = WebWorkspace(root, ROOT / "scripts" / "mvp.py", model=args.model)
        with WorkspaceServer(("127.0.0.1", args.port), workspace, dist) as server:
            print(f"Financial Reconciliation MVP · http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, subprocess.CalledProcessError, AdapterError, WorkspaceError) as exc:
        print(f"Web startup failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
