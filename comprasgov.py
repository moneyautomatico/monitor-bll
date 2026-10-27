"""Compras.gov.br – conexão assistida (o licitante entra e resolve o captcha) e leitura com a sessão dele.

Regras:
- O sistema NUNCA resolve captcha nem guarda a senha do gov.br. Quem entra é sempre o licitante, pela tela
  "Conectar Compras.gov.br" do painel (um navegador do servidor que ele controla pela imagem).
- Depois que ele entra, a sessão (cookies) fica no perfil do navegador da empresa e o monitor só LÊ com ela.
- Enquanto o licitante navega na conexão, guardamos só a ESTRUTURA das respostas do site (endereço e nomes
  dos campos, sem valores) para montar a leitura do chat. Senhas e o que é digitado não são registrados.
"""
import fcntl
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
LARG, ALT = 1366, 800
INICIO = "https://www.gov.br/compras/pt-br"
OCIOSO_MAX = 15 * 60          # fecha a conexão depois de 15 min sem uso
MAX_CONEXOES = 2              # conexões abertas ao mesmo tempo no servidor (memória)
HOSTS_OK = re.compile(r"(^|\.)(gov\.br|serpro\.gov\.br|comprasnet\.gov\.br)$", re.I)
HOSTS_LOGIN = re.compile(r"(^|\.)(acesso\.gov\.br|sso\.acesso\.gov\.br)$", re.I)
ORIGENS_CERT = ("https://certificado.sso.acesso.gov.br", "https://sso.acesso.gov.br")
TECLAS = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End",
          "PageUp", "PageDown", "Shift+Tab"}


def pasta(eid):
    p = EMPRESAS / eid
    p.mkdir(parents=True, exist_ok=True)
    return p


def arq_estado(eid):
    return pasta(eid) / "comprasgov.json"


def ler_estado(eid):
    try:
        return json.loads(arq_estado(eid).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def gravar_estado(eid, **kw):
    d = ler_estado(eid)
    d.update(kw)
    a = arq_estado(eid)
    a.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(a, 0o600)
    return d


def host_ok(url):
    try:
        h = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return urlsplit(url).scheme == "https" and bool(HOSTS_OK.search(h))


def tela_de_login(url):
    """True quando o endereço é a tela de entrada do gov.br (sessão caiu ou ainda não entrou)."""
    try:
        h = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return bool(HOSTS_LOGIN.search(h)) or "login" in url.lower()


# ------------------------------------------------------------------ trava do perfil (painel x monitor)
class TravaPerfil:
    """O perfil do navegador não pode ser aberto por dois processos ao mesmo tempo."""
    def __init__(self, eid):
        self.arq = pasta(eid) / "perfil.lock"
        self.f = None

    def pegar(self):
        self.f = open(self.arq, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False

    def soltar(self):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
            self.f = None


# ------------------------------------------------------------------ estrutura das respostas (diagnóstico, sem valores)
RE_NUM = re.compile(r"/\d{3,}(?=/|$)")
RE_TOKEN = re.compile(r"(token|captcha|senha|password|code|state|nonce|session|jwt|auth)", re.I)


def esqueleto(v, prof=0):
    """Só os nomes dos campos e os tipos – nunca os valores."""
    if prof > 4:
        return "…"
    if isinstance(v, dict):
        return {k: esqueleto(x, prof + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return [esqueleto(v[0], prof + 1), f"×{len(v)}"] if v else []
    return type(v).__name__


def caminho_limpo(url):
    s = urlsplit(url)
    chaves = sorted({k.split("=")[0] for k in s.query.split("&") if k and not RE_TOKEN.search(k.split("=")[0])})
    return f"{s.hostname}{RE_NUM.sub('/{n}', s.path)}" + (("?" + "&".join(chaves)) if chaves else "")


def registrar_navegacao(eid, evento, url, erro=""):
    """Caminho do login (só endereço sem parâmetros) – para diagnosticar o certificado / a entrada."""
    try:
        reg = {"em": datetime.now().strftime("%d/%m %H:%M:%S"), "evento": evento, "url": caminho_limpo(url), "erro": str(erro or "")[:200]}
        a = pasta(eid) / "comprasgov_navegacao.jsonl"
        if a.exists() and a.stat().st_size > 1_000_000:
            a.replace(a.with_suffix(".jsonl.1"))
        with open(a, "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        os.chmod(a, 0o600)
    except Exception:  # noqa: BLE001
        pass


def registrar_resposta(eid, resp):
    try:
        url = resp.url
        h = urlsplit(url).hostname or ""
        if not HOSTS_OK.search(h) or HOSTS_LOGIN.search(h):
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            corpo = esqueleto(resp.json())
        except Exception:  # noqa: BLE001
            corpo = "?"
        reg = {"em": datetime.now().strftime("%d/%m %H:%M:%S"), "metodo": resp.request.method, "status": resp.status,
               "url": caminho_limpo(url), "campos": corpo}
        a = pasta(eid) / "comprasgov_estrutura.jsonl"
        if a.exists() and a.stat().st_size > 3_000_000:
            a.replace(a.with_suffix(".jsonl.1"))
        with open(a, "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        os.chmod(a, 0o600)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ tela virtual (o captcha passa melhor com navegador "visível")
def iniciar_xvfb():
    if not shutil.which("Xvfb"):
        return None, None
    for n in range(91, 120):
        if not Path(f"/tmp/.X{n}-lock").exists():
            pr = subprocess.Popen(["Xvfb", f":{n}", "-screen", "0", f"{LARG}x{ALT}x24", "-nolisten", "tcp"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.8)
            if pr.poll() is None:
                return pr, f":{n}"
    return None, None


# ------------------------------------------------------------------ a conexão (um navegador por empresa, numa thread própria)
class Conexao:
    def __init__(self, eid, login, cert=None):
        self.eid, self.login = eid, login
        self._cert = cert          # (bytes do .pfx, senha) – só na memória, até o navegador abrir
        self.cert_info = cert[2] if cert and len(cert) > 2 else ""   # só o nome do titular e a validade (para mostrar)
        self.q = queue.Queue()
        self.uso = time.time()
        self.erro = ""
        self.url = ""
        self.titulo = ""
        self.vivo = True
        self.pronto = threading.Event()
        self.trava = TravaPerfil(eid)
        self.t = threading.Thread(target=self._rodar, daemon=True)
        self.t.start()

    # chamado pelas requisições do painel
    def pedir(self, cmd, prazo=25, **kw):
        if not self.vivo:
            raise RuntimeError(self.erro or "A conexão foi fechada.")
        self.uso = time.time()
        r = queue.Queue(maxsize=1)
        self.q.put((cmd, kw, r))
        ok, v = r.get(timeout=prazo)
        if not ok:
            raise RuntimeError(v)
        return v

    def _rodar(self):
        from playwright.sync_api import sync_playwright
        xvfb = None
        try:
            if not self.trava.pegar():
                raise RuntimeError("O monitor desta empresa está usando o navegador agora. Tente de novo em 1 minuto.")
            xvfb, tela = iniciar_xvfb()
            amb = dict(os.environ, DISPLAY=tela) if tela else None
            with sync_playwright() as pw:
                extra = {}
                if self._cert:   # certificado A1: o navegador apresenta ao gov.br quando ele pedir (o licitante escolheu usar)
                    extra["client_certificates"] = [{"origin": o, "pfx": self._cert[0], "passphrase": self._cert[1]}
                                                    for o in ORIGENS_CERT]
                self._cert = None
                ctx = pw.chromium.launch_persistent_context(
                    str(pasta(self.eid) / "perfil_navegador"), headless=not tela, env=amb, locale="pt-BR",
                    viewport={"width": LARG, "height": ALT}, accept_downloads=False, **extra)
                extra.clear()
                ctx.set_default_timeout(20000)
                cook = pasta(self.eid) / "cookies_sessao.json"
                try:
                    if cook.exists():
                        ctx.add_cookies(json.loads(cook.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    pass
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                def ligar(p_):
                    p_.on("framenavigated", lambda fr: registrar_navegacao(self.eid, "abriu", fr.url) if fr.parent_frame is None else None)
                ligar(pg)
                ctx.on("page", ligar)
                ctx.on("response", lambda r: registrar_resposta(self.eid, r))
                ctx.on("requestfailed", lambda rq: registrar_navegacao(self.eid, "falhou", rq.url, rq.failure)
                       if HOSTS_LOGIN.search(urlsplit(rq.url).hostname or "") else None)
                ctx.on("page", lambda p: setattr(self, "_pg_nova", p))
                self._pg_nova = None
                inicio = ler_estado(self.eid).get("inicio") or INICIO
                try:
                    pg.goto(inicio if host_ok(inicio) else INICIO, wait_until="domcontentloaded", timeout=45000)
                except Exception:  # noqa: BLE001
                    pass
                self.pronto.set()
                while self.vivo:
                    if self._pg_nova is not None:   # link que abre em outra aba: passa a mostrar a aba nova
                        pg, self._pg_nova = self._pg_nova, None
                        try:
                            pg.set_viewport_size({"width": LARG, "height": ALT})
                        except Exception:  # noqa: BLE001
                            pass
                    if time.time() - self.uso > OCIOSO_MAX:
                        break
                    try:
                        cmd, kw, r = self.q.get(timeout=1)
                    except queue.Empty:
                        continue
                    try:
                        if cmd == "fechar":
                            r.put((True, self._salvar(ctx, pg)))
                            break
                        r.put((True, self._executar(ctx, pg, cmd, kw)))
                    except Exception as ex:  # noqa: BLE001
                        r.put((False, str(ex)[:300]))
                    try:
                        self.url, self.titulo = pg.url, pg.title()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self._salvar(ctx, pg)
                except Exception:  # noqa: BLE001
                    pass
                ctx.close()
        except Exception as ex:  # noqa: BLE001
            self.erro = str(ex)[:300]
        finally:
            self.vivo = False
            self.pronto.set()
            self.trava.soltar()
            if xvfb:
                xvfb.terminate()
            while not self.q.empty():
                try:
                    _, _, r = self.q.get_nowait()
                    r.put((False, self.erro or "A conexão foi fechada."))
                except Exception:  # noqa: BLE001
                    break

    def _executar(self, ctx, pg, cmd, kw):
        if cmd == "tela":
            return pg.screenshot(type="jpeg", quality=62)
        if cmd == "clique":
            x, y = max(0, min(LARG, int(kw["x"]))), max(0, min(ALT, int(kw["y"])))
            pg.mouse.click(x, y)
            pg.wait_for_timeout(250)
        elif cmd == "texto":
            pg.keyboard.type(kw["texto"][:200], delay=35)
        elif cmd == "tecla":
            if kw["tecla"] not in TECLAS:
                raise RuntimeError("tecla não permitida")
            pg.keyboard.press(kw["tecla"])
            pg.wait_for_timeout(200)
        elif cmd == "rolar":
            pg.mouse.wheel(0, max(-2000, min(2000, int(kw["dy"]))))
            pg.wait_for_timeout(200)
        elif cmd == "ir":
            url = kw["url"].strip()
            if not url.startswith("http"):
                url = "https://" + url
            if not host_ok(url):
                raise RuntimeError("Só endereços do gov.br / Compras.gov.br podem ser abertos aqui.")
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        elif cmd == "voltar":
            pg.go_back(wait_until="domcontentloaded", timeout=30000)
        elif cmd == "recarregar":
            pg.reload(wait_until="domcontentloaded", timeout=45000)
        elif cmd == "marcar_inicio":   # "esta é a minha página inicial de fornecedor" – o monitor volta nela a cada ciclo
            if tela_de_login(pg.url) or not host_ok(pg.url):
                raise RuntimeError("Entre primeiro no Compras.gov.br (esta ainda é a tela de login).")
            gravar_estado(self.eid, inicio=pg.url, inicio_titulo=pg.title()[:120])
        elif cmd == "salvar_tela":     # estrutura da tela (para montar a leitura): textos de botões/abas e tabelas, sem os valores digitados
            salvar_estrutura_tela(self.eid, pg)
        return {"url": pg.url, "titulo": pg.title()}

    def _salvar(self, ctx, pg):
        cook = pasta(self.eid) / "cookies_sessao.json"
        cook.write_text(json.dumps(ctx.cookies()), encoding="utf-8")
        os.chmod(cook, 0o600)
        logado = host_ok(pg.url) and not tela_de_login(pg.url)
        if logado:
            gravar_estado(self.eid, conectado_em=datetime.now().strftime("%d/%m/%Y %H:%M"), conectado_ts=time.time(),
                          conectado_por=self.login, ultima_url=pg.url, aviso_caiu="")
        return {"logado": logado, "url": pg.url}


JS_ESTRUTURA = r"""() => {
 const t = s => (s||'').replace(/\s+/g,' ').trim().slice(0,80);
 const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
 const pega = (sel, n) => [...document.querySelectorAll(sel)].filter(vis).slice(0, n);
 return {
  url: location.href.split('?')[0], titulo: document.title,
  botoes: pega('button,a[role=button],[role=tab],.p-tabview-nav a,.nav-link', 80).map(b => t(b.innerText || b.getAttribute('aria-label'))).filter(Boolean),
  abas_menu: pega('nav a,[role=menuitem]', 60).map(a => t(a.innerText)).filter(Boolean),
  titulos: pega('h1,h2,h3,h4,legend,.titulo,.p-panel-title,.card-title', 40).map(h => t(h.innerText)).filter(Boolean),
  tabelas: pega('table', 10).map(tb => ({cab: [...tb.querySelectorAll('th')].slice(0, 15).map(th => t(th.innerText)),
     linhas: tb.querySelectorAll('tbody tr').length})),
  classes_lista: [...new Set(pega('[class*=mensag],[class*=chat],[class*=item],[class*=lote]', 200).map(e => e.className.toString().split(' ').slice(0,3).join('.')))].slice(0, 40)
 };
}"""


def salvar_estrutura_tela(eid, pg):
    reg = pg.evaluate(JS_ESTRUTURA)
    reg["url"] = caminho_limpo(reg.get("url", ""))
    reg["em"] = datetime.now().strftime("%d/%m %H:%M:%S")
    a = pasta(eid) / "comprasgov_telas.jsonl"
    with open(a, "a", encoding="utf-8") as f:
        f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    os.chmod(a, 0o600)


# ------------------------------------------------------------------ gerência das conexões (no processo do painel)
_CONEXOES = {}
_TRAVA = threading.Lock()


def conferir_certificado(pfx, senha):
    """Abre o .pfx só para conferir a senha e mostrar de quem é e até quando vale. Nada é gravado."""
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
    except Exception:  # noqa: BLE001
        return None, ""
    try:
        chave, cert, _ = pkcs12.load_key_and_certificates(pfx, senha.encode("utf-8"))
    except ValueError:
        return False, "A senha do certificado não confere (ou o arquivo não é um certificado .pfx/.p12)."
    if not cert or not chave:
        return False, "O arquivo não tem o certificado com a chave privada."
    nome = next((a.value for a in cert.subject if a.oid.dotted_string == "2.5.4.3"), "")
    try:
        vale = cert.not_valid_after_utc
    except AttributeError:
        vale = cert.not_valid_after
    if vale.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
        return False, f"Este certificado venceu em {vale:%d/%m/%Y}."
    return True, f"{nome} – válido até {vale:%d/%m/%Y}"


def abrir(eid, login, cert=None):
    with _TRAVA:
        c = _CONEXOES.get(eid)
        if c and c.vivo:
            return c
        vivas = [k for k, x in _CONEXOES.items() if x.vivo]
        if len(vivas) >= MAX_CONEXOES:
            raise RuntimeError("Há outras conexões abertas no servidor agora. Tente de novo em alguns minutos.")
        c = Conexao(eid, login, cert)
        _CONEXOES[eid] = c
    c.pronto.wait(60)
    if not c.vivo:
        raise RuntimeError(c.erro or "Não consegui abrir o navegador.")
    return c


def atual(eid):
    c = _CONEXOES.get(eid)
    return c if c and c.vivo else None


def fechar(eid):
    c = atual(eid)
    if not c:
        return {"logado": False}
    try:
        return c.pedir("fechar", prazo=30)
    finally:
        c.vivo = False


def resumo_estado(eid):
    """Para a página Empresas: conectado / caiu / nunca conectou."""
    d = ler_estado(eid)
    if atual(eid):
        return "🟡 conexão aberta agora" + (f" · 🔐 certificado carregado: {atual(eid).cert_info}" if atual(eid).cert_info else " · sem certificado")
    if d.get("aviso_caiu"):
        return f"🔴 sessão caiu em {d['aviso_caiu']} – reconecte"
    if d.get("conectado_em"):
        return f"🟢 conectado em {d['conectado_em']}"
    return "⚪ ainda não conectado"
