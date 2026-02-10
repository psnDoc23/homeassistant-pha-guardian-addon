# server.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import json
import os
import urllib.request

logging.basicConfig(level=logging.INFO, format='[Guardian] %(message)s')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            logging.info("Health check received")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        elif self.path == "/issues":
            logging.info("Issues requested")

            # Temporary static data — replace later with real Guardian logic
            issues = {
                "issues": [
                    {"id": 1, "title": "Example issue", "severity": "low"},
                    {"id": 2, "title": "Another issue", "severity": "medium"}
                ]
            }

            payload = json.dumps(issues).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            
        elif self.path == "/supervisor-test":
            logging.info("Supervisor test requested")
        
        
            token = os.environ.get("SUPERVISOR_TOKEN")
            if not token:
                logging.error("Supervisor token not found")
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
                logging.error(f"Supervisor API error: {e}")
                self.send_response(500)
                self.end_headers()
        else:
            logging.info(f"Unknown path requested: {self.path}")
            self.send_response(404)
            self.end_headers()

def run():
    logging.info("Starting Guardian server on port 8099")
    server = HTTPServer(("", 8099), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()

