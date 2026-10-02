"""Live view of the SpaceBall sensors in the browser: python live_view.py, then open http://localhost:8765

Streams raw axis values and button state via Server-Sent Events (~30 updates/s).
Can run while Fusion uses the ball: Windows delivers HID reports to every open handle.
"""
import http.server
import json
import os
import threading
import time

import hidreader

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
state = {'axes': [0] * 6, 'buttons': 0, 'connected': False, 'rate': 0}
lock = threading.Lock()


def reader():
    dev = None
    count, t0, last = 0, time.time(), 0.0
    while True:
        try:
            if dev is None:
                dev = hidreader.SpaceBall()
                with lock:
                    state['connected'] = True
            r = dev.read(50)
            now = time.time()
            with lock:
                if r:
                    last = now
                    count += 1
                    rid = r[0]
                    vals = [int.from_bytes(r[1 + 2 * i:3 + 2 * i], 'little', signed=True) for i in range(3)]
                    if rid == 1:
                        state['axes'][0:3] = vals
                    elif rid == 2:
                        state['axes'][3:6] = vals
                    elif rid == 3:
                        state['buttons'] = int.from_bytes(r[1:3], 'little')
                if now - last > 0.08:  # ball released -> device goes silent
                    state['axes'] = [0] * 6
                if now - t0 >= 1:
                    state['rate'] = count
                    count, t0 = 0, now
        except Exception:
            if dev:
                dev.close()
            dev = None
            with lock:
                state['connected'] = False
            time.sleep(1)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                while True:
                    with lock:
                        msg = json.dumps(state)
                    self.wfile.write(('data: %s\n\n' % msg).encode())
                    self.wfile.flush()
                    time.sleep(1 / 30)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        if self.path == '/config':  # button assignment from the add-in's config.json
            try:
                with open(os.path.join(HERE, 'config.json'), 'rb') as f:
                    body = f.read()
            except OSError:
                body = b'{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
            return
        with open(os.path.join(HERE, 'live_view.html'), 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    threading.Thread(target=reader, daemon=True).start()
    print('Live-Ansicht: http://localhost:%d' % PORT, flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
