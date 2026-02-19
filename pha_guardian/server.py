# server.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import urllib.request

from logging_config import setup_logging
logger = setup_logging()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            logger.info("Health check received")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        elif self.path == "/issues":
            logger.info("Issues requested")

            issues = {
                "issues": [
                    {"id": 1, "title": "Example issue", "severity": "low"},
                    {"id": 2, "title": "Another issue", "severity": "medium"},
                    ({"id": 3, "title": "Yikes!!! that doesn't look good!", "severity": "high"})
                ]
            }

            payload = json.dumps(issues).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        elif self.path == "/supervisor-test":
            logger.info("Supervisor test requested")

            token = os.environ.get("SUPERVISOR_TOKEN")
            if not token:
                logger.error("Supervisor token not found")
                self.send_response(500)
                self.end_headers()
                return

            req = urllib.request.Request(
                "http://supervisor/info",
                headers={"Authorization": f"Bearer {token}"}
            )

            try:
                with urllib.request.urlopen(req) as response:
                    data = response.read()
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(data)
            except Exception as e:
                logger.error(f"Supervisor API error: {e}")
                self.send_response(500)
                self.end_headers()

        else:
            logger.info(f"Unknown path requested: {self.path}")
            self.send_response(404)
            self.end_headers()

def run():
    logger.info("Starting Guardian server on port 8099")
    server = HTTPServer(("", 8099), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()
