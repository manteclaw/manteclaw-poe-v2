"""Mock SurplusLLM API for local smoke tests only. NOT shipped in the image.

- Bearer valid-test-key -> SSE stream spelling out "4" for 2+2.
- Any other key -> HTTP 401 {"detail":"Invalid API Key"} (real OpenAI-compatible shape).
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer valid-test-key":
            body = json.dumps({"detail": "Invalid API Key"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        chunks = ["2 + 2 = ", "4", "."]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for text in chunks:
            payload = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": text}}],
                }
            )
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18099), Handler).serve_forever()
