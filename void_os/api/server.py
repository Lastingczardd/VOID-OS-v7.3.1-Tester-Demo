"""Authenticated, rate-limited localhost HTTP API."""
import json, threading, time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ..kernel.security import constant_time_token_ok, get_or_create_api_token
from ..kernel.witness import log


def start_api(app, host='127.0.0.1', port=8765):
    if app.cfg.get('security',{}).get('local_only',True) and host not in {'127.0.0.1','localhost','::1'}:
        raise PermissionError('Security policy permits localhost API binding only.')
    token=get_or_create_api_token(); windows=defaultdict(deque); lock=threading.Lock()
    max_body=int(app.cfg.get('security',{}).get('max_api_body_bytes',262144))
    rpm=int(app.cfg.get('security',{}).get('api_requests_per_minute',30))
    require_token=bool(app.cfg.get('security',{}).get('require_api_token',True))
    class Handler(BaseHTTPRequestHandler):
        server_version='VOIDOS/6.2'; sys_version=''
        def _headers(self):
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'")
        def _send_json(self,obj,code=200):
            raw=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self._headers(); self.end_headers(); self.wfile.write(raw)
        def _allowed(self):
            now=time.time(); key=self.client_address[0]
            with lock:
                q=windows[key]
                while q and q[0] < now-60: q.popleft()
                if len(q)>=rpm: return False
                q.append(now); return True
        def _auth(self):
            if not self._allowed(): self._send_json({'error':'rate limit exceeded'},429); return False
            if self.path=='/health' or not require_token: return True
            raw=self.headers.get('Authorization','')
            supplied=raw[7:].strip() if raw.lower().startswith('bearer ') else self.headers.get('X-VOID-Token')
            if not constant_time_token_ok(supplied,token):
                log('api_auth_failed',{'client':self.client_address[0],'path':self.path}); self._send_json({'error':'unauthorized'},401); return False
            return True
        def do_GET(self):
            if not self._auth(): return
            if self.path=='/health': self._send_json({'ok':True,'version':app.cfg.get('version','6.2')})
            elif self.path=='/agents': self._send_json({'agents':list(app.agents.agents)})
            elif self.path=='/workflows': self._send_json({'workflows':app.workflows.list()})
            elif self.path=='/system': self._send_json({'models':app.cfg.get('models'),'active_project':app.cfg.get('active_project'),'ollama_available':app.router.is_available()})
            else: self._send_json({'error':'not found'},404)
        def do_POST(self):
            if not self._auth(): return
            try:
                raw_len=self.headers.get('Content-Length')
                if raw_len is None: return self._send_json({'error':'content-length required'},411)
                length=int(raw_len)
                if length<0 or length>max_body: return self._send_json({'error':'request body too large'},413)
                raw=self.rfile.read(length)
                data=json.loads(raw or b'{}')
                if not isinstance(data,dict): return self._send_json({'error':'JSON object required'},400)
                if self.path=='/chat': result={'response':app.router.generate(str(data.get('prompt','')))}
                elif self.path=='/agent/run': result={'response':app.agents.run(str(data['agent']),str(data.get('goal','')))}
                elif self.path=='/workflow/run': result=app.workflows.run(str(data['workflow']),str(data.get('input','')))
                else: return self._send_json({'error':'not found'},404)
                self._send_json(result)
            except (KeyError,ValueError,TypeError,json.JSONDecodeError) as e: self._send_json({'error':'invalid request'},400)
            except Exception as e:
                log('api_request_failed',{'path':self.path,'error':str(e)}); self._send_json({'error':'internal error'},500)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer((host,port),Handler); threading.Thread(target=server.serve_forever,daemon=True).start(); return server
