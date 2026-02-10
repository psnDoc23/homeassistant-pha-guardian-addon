# server.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging

logging.basicConfig(level=logging.INFO, format='[Guardian] %(message)s')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            logging.info("Health check received")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
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
