"""Serve o painel (painel.html) pela internet, protegido por usuário e senha."""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
CFG = {}
for linha in (PASTA / "config.env").read_text(encoding="utf-8").splitlines():
    linha = linha.strip()
    if linha and not linha.startswith("#") and "=" in linha:
        k, v = linha.split("=", 1)
        CFG[k.strip()] = v.strip()

USUARIO = CFG.get("PAINEL_USUARIO", "bll")
SENHA = CFG.get("PAINEL_SENHA", "")
PORTA = int(CFG.get("PAINEL_PORTA", "8080"))
ESPERADO = "Basic " + base64.b64encode(f"{USUARIO}:{SENHA}".encode()).decode()


class Painel(BaseHTTPRequestHandler):
    def do_GET(self):
        if not SENHA or not hmac.compare_digest(self.headers.get("Authorization", ""), ESPERADO):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Painel BLL"')
            self.end_headers()
            return
        arq = PASTA / "painel.html"
        corpo = arq.read_bytes() if arq.exists() else "Painel ainda não gerado.".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Painel em http://0.0.0.0:{PORTA}")
    ThreadingHTTPServer(("0.0.0.0", PORTA), Painel).serve_forever()
