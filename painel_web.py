"""Serve o(s) painel(is) pela internet, protegido por usuário e senha.

- Uma empresa (modo antigo): mostra painel.html.
- Várias empresas: a página inicial lista as empresas; /e/<id> mostra o painel de cada uma.
"""
import base64
import hmac
import html
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"


def ler_env(arq):
    cfg = {}
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


CFG = ler_env(PASTA / "config.env")
USUARIO = CFG.get("PAINEL_USUARIO", "bll")
SENHA = CFG.get("PAINEL_SENHA", "")
PORTA = int(CFG.get("PAINEL_PORTA", "8080"))
ESPERADO = "Basic " + base64.b64encode(f"{USUARIO}:{SENHA}".encode()).decode()


def lista_empresas():
    if not EMPRESAS.exists():
        return []
    out = []
    for arq in sorted(EMPRESAS.glob("*.env")):
        eid = arq.stem
        cfg = ler_env(arq)
        painel = EMPRESAS / eid / "painel.html"
        status, atualizado = "Aguardando primeira verificação", ""
        if painel.exists():
            txt = painel.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'<div class="st">(.*?) · atualizado em (.*?) ·', txt)
            if m:
                status, atualizado = html.unescape(m.group(1)), m.group(2)
        out.append({"id": eid, "nome": cfg.get("EMPRESA_NOME", eid), "status": status, "atualizado": atualizado})
    return out


def pagina_inicial(emps):
    e = html.escape
    cards = ""
    for x in emps:
        alerta = "ok" if x["status"].startswith("Monitor ativo") else "aviso"
        cards += (f'<a class="card {alerta}" href="/e/{e(x["id"])}"><div class="nome">{e(x["nome"])}</div>'
                  f'<div class="st">{e(x["status"])}</div><div class="at">{e(x["atualizado"])}</div></a>')
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel BLL – Empresas</title><style>
:root{{--bg:#f4f6f4;--card:#fff;--txt:#1d2a22;--sub:#5d6b62;--verde:#1f5c3f;--aviso:#b26b00;--borda:#dde3de}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121714;--card:#1b221e;--txt:#e7ece9;--sub:#9aa8a0;--verde:#5fb88a;--aviso:#e0a040;--borda:#2b342f}}}}
body{{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}}
header{{background:#1f5c3f;color:#fff;padding:16px 20px}}h1{{margin:0;font-size:20px}}
main{{padding:16px 20px;max-width:1100px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
.card{{display:block;text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--borda);
border-left:6px solid var(--verde);border-radius:10px;padding:14px}}.card.aviso{{border-left-color:var(--aviso)}}
.nome{{font-weight:700;font-size:17px}}.st{{font-size:13px;margin-top:6px}}.at{{font-size:12px;color:var(--sub);margin-top:4px}}
</style></head><body><header><h1>Painel BLL – {len(emps)} empresa(s)</h1>
<div style="font-size:13px;opacity:.9">atualizado em {datetime.now():%d/%m/%Y %H:%M:%S}</div></header>
<main><div class="grid">{cards or "<p>Nenhuma empresa cadastrada.</p>"}</div></main></body></html>"""


class Painel(BaseHTTPRequestHandler):
    def responder(self, corpo, codigo=200):
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo.encode("utf-8") if isinstance(corpo, str) else corpo)

    def do_GET(self):
        if not SENHA or not hmac.compare_digest(self.headers.get("Authorization", ""), ESPERADO):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Painel BLL"')
            self.end_headers()
            return
        emps = lista_empresas()
        caminho = self.path.split("?")[0]
        m = re.fullmatch(r"/e/([A-Za-z0-9_-]+)/?", caminho)
        if m:
            arq = EMPRESAS / m.group(1) / "painel.html"
            if not arq.exists():
                return self.responder("Painel desta empresa ainda não foi gerado.", 404)
            corpo = arq.read_text(encoding="utf-8")
            voltar = '<a href="/" style="position:fixed;right:14px;bottom:14px;background:#1f5c3f;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none;font-family:sans-serif">← Empresas</a>'
            return self.responder(corpo.replace("</body>", voltar + "</body>"))
        if emps:
            return self.responder(pagina_inicial(emps))
        arq = PASTA / "painel.html"
        return self.responder(arq.read_bytes() if arq.exists() else "Painel ainda não gerado.")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Painel em http://0.0.0.0:{PORTA}")
    ThreadingHTTPServer(("0.0.0.0", PORTA), Painel).serve_forever()
