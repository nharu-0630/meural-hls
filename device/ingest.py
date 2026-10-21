import http.server,socketserver,threading,queue,os,re,time
R="/dev/shm/hls"
BUFS={}; LIV=[]; PLX=[b"#EXTM3U"]; LOCK=threading.Lock()
def prune(n):
    dead=[k for k in BUFS if k<n-14]
    for k in dead: BUFS.pop(k,None)
class H(http.server.SimpleHTTPRequestHandler):
    def _ok(self,b):
        self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers()
        try: self.wfile.write(b)
        except OSError: pass
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0")); body=self.rfile.read(n) if n else b""
        p=self.path.split("?")[0]
        if p=="/seg":
            m=re.search(r"n=(-?\d+)",self.path)
            if m:
                k=int(m.group(1))
                with LOCK:
                    BUFS[k]=body; prune(k)
                    for q in list(LIV): q.put(body)
                self._ok(b"ok"); return
            self.send_response(400); self.end_headers()
        elif p=="/pl":
            with LOCK: PLX[0]=body
            self._ok(b"ok")
        else:
            self.send_response(404); self.end_headers()
    def _drip(self,d):
        # セグメントの実時間 (2秒) に合わせて送出: ビットレートに自動同期し、
        # 到着間隔 (~2s) と送出所要時間が一致するため無データ孔が生じない
        for i in range(0,len(d),1024):
            self.wfile.write(d[i:i+1024]); time.sleep(2.0*1024/max(1,len(d)))
    def do_GET(self):
        p=self.path.split("?")[0]
        if p=="/live.m3u8":
            with LOCK: d=PLX[0][:]
            self._ok(d)
        else:
            m=re.match(r"^/s(-?\d+)\.ts$",p)
            if m:
                with LOCK: d=BUFS.get(int(m.group(1)))
                if d is None: self.send_response(404); self.end_headers(); return
                self._ok(d)
            elif p=="/live.ts":
                q=queue.Queue()
                with LOCK:
                    LIV.append(q); snap=[BUFS[k] for k in sorted(BUFS)[-3:]]
                self.send_response(200); self.send_header("Content-Type","video/mp2t"); self.end_headers()
                try:
                    for d in snap: self._drip(d)
                    while True: self._drip(q.get())
                except OSError: pass
                with LOCK:
                    try: LIV.remove(q)
                    except ValueError: pass
            else:
                try: http.server.SimpleHTTPRequestHandler.do_GET(self)
                except OSError: pass
class S(socketserver.ThreadingTCPServer): allow_reuse_address=True; daemon_threads=True
S(("",8484),H).serve_forever()
