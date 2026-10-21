"""Painel central do Monitor BLL (http://IP:8080, protegido por usuário e senha).

Abas: Visão geral · Mensagens · Processos · Buscar · Empresas · Configurações.
Permite cadastrar/editar/pausar/remover empresas e ajustar os alertas sem usar o terminal.
"""
import base64
import getpass
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import analise
import backup
import urgencia
import buscas

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
CONFIG = PASTA / "config.env"
PY = PASTA / "venv" / "bin" / "python"
CSRF = secrets.token_hex(16)
AVISOS = {"migrado": "Convertido! Agora você pode cadastrar outras empresas.",
          "cadastrada": "Empresa cadastrada. Em alguns minutos chega o “Monitor BLL ativo” com o nome dela.",
          "atualizada": "Empresa atualizada.", "feito": "Feito.",
          "config": "Configurações salvas e monitores reiniciados.",
          "ia": "Instruções da IA salvas. Valem para as próximas análises.",
          "busca_salva": "Busca salva. Vou mandar no Telegram um resumo agora e, depois, só as licitações novas.",
          "busca_rodando": "Verificando agora. Se houver novidades, chegam no Telegram em instantes."}
e = html.escape


# ------------------------------------------------------------------ arquivos .env
def ler_env(arq):
    cfg = {}
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def gravar_env(arq, valores, remover=()):
    """Atualiza chaves no arquivo, mantendo comentários e o resto."""
    linhas = arq.read_text(encoding="utf-8").splitlines() if arq.exists() else []
    feitas, out = set(), []
    for linha in linhas:
        k = linha.split("=", 1)[0].strip() if "=" in linha and not linha.strip().startswith("#") else None
        if k in remover:
            continue
        if k in valores:
            out.append(f"{k}={valores[k]}")
            feitas.add(k)
        else:
            out.append(linha)
    out += [f"{k}={v}" for k, v in valores.items() if k not in feitas]
    arq.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(arq, 0o600)


CFG = ler_env(CONFIG)
USUARIO = CFG.get("PAINEL_USUARIO", "bll")
SENHA = CFG.get("PAINEL_SENHA", "")
PORTA = int(CFG.get("PAINEL_PORTA", "8080"))
ESPERADO = "Basic " + base64.b64encode(f"{USUARIO}:{SENHA}".encode()).decode()
ARQ_SESSOES = PASTA / "sessoes.json"
TENTATIVAS = {}   # ip -> [horários de erro]


def _sessoes():
    try:
        d = json.loads(ARQ_SESSOES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    agora = datetime.now().timestamp()
    return {k: v for k, v in d.items() if v > agora}


def criar_sessao(dias):
    tok = secrets.token_urlsafe(32)
    d = _sessoes()
    d[tok] = datetime.now().timestamp() + dias * 86400
    ARQ_SESSOES.write_text(json.dumps(d), encoding="utf-8")
    os.chmod(ARQ_SESSOES, 0o600)
    return tok


def sessao_valida(tok):
    return bool(tok) and tok in _sessoes()


def encerrar_sessao(tok):
    d = _sessoes()
    d.pop(tok, None)
    ARQ_SESSOES.write_text(json.dumps(d), encoding="utf-8")


def pg_login(erro="", destino="/"):
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Entrar – Licitatudo</title>{TEMA_JS}
<style>
*{{box-sizing:border-box}}html,body{{height:100%;margin:0}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#17241c;display:grid;grid-template-columns:1.1fr 1fr;background:#f3f5f4}}
.arte{{position:relative;overflow:hidden;background:radial-gradient(1200px 600px at -10% -10%,#2f8a5b 0,transparent 60%),
 radial-gradient(900px 500px at 110% 110%,#0e2a1d 0,transparent 55%),linear-gradient(135deg,#173f2c,#0f2b1f);color:#fff;
 display:flex;flex-direction:column;justify-content:space-between;padding:48px}}
.arte:after{{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),
 linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);background-size:44px 44px;mask-image:linear-gradient(180deg,#000,transparent)}}
.marca{{display:flex;align-items:center;gap:12px;font-weight:800;font-size:22px;letter-spacing:.3px;position:relative;z-index:1}}
.logo{{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#7fd3a5,#2f8a5b);display:grid;place-items:center;
 box-shadow:0 8px 24px rgba(0,0,0,.25)}}
.chamada{{position:relative;z-index:1;max-width:460px}}.chamada h1{{font-size:38px;line-height:1.12;margin:0 0 14px}}
.chamada p{{color:#cfe3d7;font-size:16px;line-height:1.5;margin:0}}
.chips{{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}}.chip{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);
 padding:7px 12px;border-radius:999px;font-size:13px;backdrop-filter:blur(4px)}}
.rodape{{position:relative;z-index:1;color:#9fbfae;font-size:12px}}
.lado{{display:grid;place-items:center;padding:32px}}
.cartao{{width:100%;max-width:380px}}
.cartao h2{{font-size:26px;margin:0 0 6px}}.cartao .sub{{color:#5f6d65;margin:0 0 26px}}
label{{display:block;font-size:13px;font-weight:600;margin:14px 0 6px}}
.campo{{position:relative}}.campo input{{width:100%;font:inherit;padding:13px 14px 13px 42px;border:1.5px solid #dfe5e1;border-radius:12px;
 background:#fff;outline:none;transition:.15s}}.campo input:focus{{border-color:#2f8a5b;box-shadow:0 0 0 4px rgba(47,138,91,.15)}}
.campo svg{{position:absolute;left:13px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:#8a9a91}}
.olho{{position:absolute;right:8px;top:50%;transform:translateY(-50%);border:0;background:none;cursor:pointer;color:#5f6d65;padding:6px;font-size:13px}}
.linha{{display:flex;justify-content:space-between;align-items:center;margin:16px 0 22px;font-size:13px;color:#5f6d65}}
.linha label{{display:flex;gap:8px;align-items:center;margin:0;font-weight:500}}input[type=checkbox]{{accent-color:#2f8a5b;width:16px;height:16px}}
button.entrar{{width:100%;font:inherit;font-weight:700;font-size:15px;color:#fff;border:0;border-radius:12px;padding:14px;cursor:pointer;
 background:linear-gradient(135deg,#2f8a5b,#1f5c3f);box-shadow:0 10px 24px rgba(31,92,63,.3);transition:.15s}}
button.entrar:hover{{transform:translateY(-1px);box-shadow:0 14px 28px rgba(31,92,63,.35)}}
.erro{{background:#fde8e6;color:#b3261e;border-radius:10px;padding:10px 12px;font-size:13.5px;margin-bottom:6px}}
.seg{{display:flex;gap:6px;align-items:center;justify-content:center;color:#8a9a91;font-size:12px;margin-top:22px}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]) body{{background:#101512;color:#e5ebe7}}
 :root:not([data-theme=light]) .campo input{{background:#1a211d;border-color:#2a332e;color:#e5ebe7}}
 :root:not([data-theme=light]) .cartao .sub,:root:not([data-theme=light]) .linha{{color:#97a59d}}}}
:root[data-theme=dark] body{{background:#101512;color:#e5ebe7}}:root[data-theme=dark] .campo input{{background:#1a211d;border-color:#2a332e;color:#e5ebe7}}
:root[data-theme=dark] .cartao .sub,:root[data-theme=dark] .linha{{color:#97a59d}}
.tema{{position:fixed;top:16px;right:16px;border:1px solid #dfe5e1;background:rgba(255,255,255,.7);border-radius:10px;padding:7px 11px;font-size:16px;cursor:pointer;z-index:5}}
:root[data-theme=dark] .tema{{background:#1a211d;border-color:#2a332e}}
@media (max-width:860px){{body{{grid-template-columns:1fr}}.arte{{padding:28px;min-height:auto}}.chamada h1{{font-size:26px}}.chips,.rodape{{display:none}}}}
</style></head><body>
<section class="arte"><div class="marca"><div class="logo"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg></div>Licitatudo</div>
<div class="chamada"><h1>Todas as suas licitações, num só lugar.</h1>
<p>Mensagens do pregoeiro, classificação dos lotes, recursos e diligências da BLL, BNC e Portal de Compras — com alerta na hora.</p>
<div class="chips"><span class="chip">🔔 Alertas em tempo real</span><span class="chip">📊 Classificação por lote</span><span class="chip">🤖 Análise de editais</span></div></div>
<div class="rodape">© {datetime.now().year} Licitatudo · acesso restrito</div></section>
<button type="button" class="tema" onclick="trocarTema()" title="Modo escuro">🌙</button>
<section class="lado"><form class="cartao" method="post" action="/login">
<h2>Bem-vindo de volta</h2><p class="sub">Entre para acessar o painel.</p>
{f'<div class="erro">{e(erro)}</div>' if erro else ''}
<input type="hidden" name="destino" value="{e(destino)}">
<label for="u">Usuário</label><div class="campo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>
<input id="u" name="usuario" autocomplete="username" required autofocus></div>
<label for="s">Senha</label><div class="campo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
<input id="s" name="senha" type="password" autocomplete="current-password" required>
<button type="button" class="olho" onclick="const i=document.getElementById('s');i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'Mostrar':'Ocultar'">Mostrar</button></div>
<div class="linha"><label><input type="checkbox" name="lembrar" value="1" checked> Manter conectado por 30 dias</label></div>
<button class="entrar">Entrar</button>
<div class="seg">🔒 Conexão protegida por senha · use apenas em dispositivos confiáveis</div>
</form></section></body></html>"""


# ------------------------------------------------------------------ sistema
def sh(*cmd, timeout=60):
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as ex:  # noqa: BLE001
        return str(ex)


def garantir_servico():
    alvo = Path("/etc/systemd/system/monitor-bll@.service")
    if alvo.exists():
        return
    alvo.write_text(f"""[Unit]
Description=Monitor BLL Compras - empresa %i
After=network-online.target
Wants=network-online.target

[Service]
User={getpass.getuser()}
WorkingDirectory={PASTA}
Environment=TZ=America/Sao_Paulo
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1366x800x24" {PY} {PASTA}/monitor_bll.py --empresa %i
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
""")
    sh("systemctl", "daemon-reload")


def ativo(eid):
    return sh("systemctl", "is-active", f"monitor-bll@{eid}") == "active"


def memoria():
    info = {}
    try:
        for l in Path("/proc/meminfo").read_text().splitlines():
            k, v = l.split(":", 1)
            info[k] = int(v.split()[0]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return info.get("MemTotal", 0), info.get("MemAvailable", 0)


def gerar_id(nome):
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30] or "empresa"


def data_msg(m):
    """Data/hora da mensagem na plataforma (ou, se não der para ler, quando o monitor a viu)."""
    x = re.search(r"(\d{2})/(\d{2})/(\d{4})\D*(\d{2}):(\d{2})(?::(\d{2}))?", m.get("horario") or "")
    if x:
        try:
            d, mo, a, h, mi, se = x.groups()
            return datetime(int(a), int(mo), int(d), int(h), int(mi), int(se or 0))
        except ValueError:
            pass
    x = re.search(r"(\d{2})/(\d{2})/(\d{4})", m.get("horario") or "")
    if x:
        try:
            return datetime(int(x.group(3)), int(x.group(2)), int(x.group(1)), 23, 59)
        except ValueError:
            pass
    return para_data(m.get("visto") or "")


def para_data(txt):
    for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(txt.strip(), f)
        except Exception:  # noqa: BLE001
            pass
    return datetime.min


# ------------------------------------------------------------------ dados
def empresas():
    out = []
    if not EMPRESAS.exists():
        return out
    ign = {s.strip().upper() for s in CFG_ATUAL().get("SITUACOES_IGNORADAS",
           "HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO").split(",")}
    for arq in sorted(EMPRESAS.glob("*.env")):
        eid = arq.stem
        cfg = ler_env(arq)
        d = {"id": eid, "nome": cfg.get("EMPRESA_NOME", eid), "cfg": cfg, "ativo": ativo(eid),
             "plat": {"licitanet": "Licitanet", "pcp": "Portal de Compras", "bnc": "BNC"}.get(cfg.get("PLATAFORMA", "bll").lower(), "BLL"),
             "status": "Aguardando primeira verificação", "ultima": "", "duracao": 0,
             "procs": [], "andamento": 0, "msgs": 0, "novas_hoje": 0}
        db = EMPRESAS / eid / "bll_monitor.db"
        if db.exists():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                est = dict(con.execute("SELECT chave, valor FROM estado").fetchall())
                d["status"] = est.get("status", d["status"])
                d["ultima"] = est.get("ultima_verificacao", "")
                d["duracao"] = int(est.get("duracao_ciclo", "0") or 0)
                d["procs"] = con.execute("SELECT processo, promotor, situacao, disputa, atualizada, visto_em "
                                         "FROM processos").fetchall()
                d["andamento"] = sum(1 for p in d["procs"] if (p[2] or "").upper() not in ign)
                d["msgs"] = con.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
                hoje = datetime.now().strftime("%d/%m/%Y")
                d["novas_hoje"] = con.execute(
                    "SELECT COUNT(*) FROM mensagens WHERE visto_em LIKE ? AND visto_em <> "
                    "(SELECT visto_em FROM mensagens ORDER BY rowid LIMIT 1)", (hoje + "%",)).fetchone()[0]
                con.close()
            except Exception as ex:  # noqa: BLE001
                d["status"] = f"Erro ao ler dados: {ex}"
        out.append(d)
    return out


_ACENTO_RE = {"a": "[aáàâãä]", "e": "[eéèêë]", "i": "[iíìîï]", "o": "[oóòôõö]", "u": "[uúùûü]", "c": "[cç]", "n": "[nñ]"}


def regex_busca(termo):
    """Busca tolerante: ignora acentos, maiúsculas e trata espaço/hífen/quebra de linha como iguais."""
    termo = unicodedata.normalize("NFKD", termo or "").encode("ascii", "ignore").decode().lower().strip()
    partes = []
    for ch in termo:
        if not ch.isalnum():
            partes.append(r"[\W_]+")
        else:
            partes.append(_ACENTO_RE.get(ch, re.escape(ch)))
    padrao = "".join(partes)
    padrao = re.sub(r"(\[\\W_\]\+)+", lambda m: r"[\W_]+", padrao).strip()
    return re.compile(padrao, re.I) if padrao else None


RE_COPIA_CHAT = re.compile(r"hor[aá]rio\W+autor\W+mensagem|mensagens\W+do\W+lote\W+hor", re.I)


def mensagens(filtro_emp="", busca="", limite=None):
    out = []
    pat_busca = regex_busca(busca) if busca else None
    for emp in empresas():
        if filtro_emp and emp["id"] != filtro_emp:
            continue
        db = EMPRESAS / emp["id"] / "bll_monitor.db"
        if not db.exists():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        prim = con.execute("SELECT visto_em FROM mensagens ORDER BY rowid LIMIT 1").fetchone()
        prim = prim[0] if prim else ""
        for r in con.execute("SELECT processo, promotor, escopo, horario, autor, texto, visto_em FROM mensagens "
                             "WHERE NOT (texto LIKE '%Limite 500 caracteres%' OR autor LIKE 'Você é o:%' "
                             "OR texto LIKE '%Horário Autor Mensagem%' OR texto LIKE '%Horario Autor Mensagem%') "
                             "ORDER BY rowid DESC LIMIT ?", (limite * 4 if limite else -1,)):
            if RE_COPIA_CHAT.search(r[5] or "") or RE_COPIA_CHAT.search(r[4] or ""):
                continue  # cópia inteira do chat lida por engano como evento
            if busca and not (pat_busca and pat_busca.search(" ".join(str(x) for x in r))):
                continue
            out.append({"emp": f'{emp["nome"]} · {emp["plat"]}', "nome": emp["nome"], "plat": emp["plat"], "eid": emp["id"], "processo": r[0], "promotor": r[1], "escopo": r[2],
                        "horario": r[3], "autor": r[4], "texto": r[5], "visto": r[6], "nova": r[6] != prim})
        con.close()
    out.sort(key=lambda m: (data_msg(m), para_data(m["visto"])), reverse=True)  # mais recentes primeiro
    return out[:limite] if limite else out


def paginacao(pag, total_pag, base):
    """Links 1 2 3 … com anterior/próxima."""
    if total_pag <= 1:
        return ""
    def link(n, txt=None):
        if n == pag:
            return f'<span class="btn">{n}</span>'
        return f'<a class="btn sec" href="{base}&pagina={n}">{txt or n}</a>'
    nums = sorted({1, total_pag, *range(max(1, pag - 3), min(total_pag, pag + 3) + 1)})
    partes, ant = [], 0
    for n in nums:
        if n - ant > 1:
            partes.append('<span class="sub" style="align-self:center">…</span>')
        partes.append(link(n))
        ant = n
    if pag > 1:
        partes.insert(0, link(pag - 1, "← Anterior"))
    if pag < total_pag:
        partes.append(link(pag + 1, "Próxima →"))
    return f'<div class="acoes" style="margin:12px 0">{"".join(partes)}</div>'


def CFG_ATUAL():
    return ler_env(CONFIG)


# ------------------------------------------------------------------ HTML
TEMA_JS = "<script>(function(){try{var t=localStorage.getItem('tema');if(t)document.documentElement.dataset.theme=t;}catch(e){}})();\nfunction trocarTema(){var r=document.documentElement,atual=r.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'),\nnovo=atual==='dark'?'light':'dark';r.dataset.theme=novo;try{localStorage.setItem('tema',novo);}catch(e){}\ndocument.querySelectorAll('.tema').forEach(function(b){b.textContent=novo==='dark'?'☀️':'🌙';b.title=novo==='dark'?'Modo claro':'Modo escuro';});}\ndocument.addEventListener('DOMContentLoaded',function(){var r=document.documentElement,d=(r.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'))==='dark';\ndocument.querySelectorAll('.tema').forEach(function(b){b.textContent=d?'☀️':'🌙';b.title=d?'Modo claro':'Modo escuro';});});</script>"
ESTILO = """
:root{--bg:#f3f5f4;--card:#fff;--txt:#17241c;--sub:#5f6d65;--verde:#1f5c3f;--verde2:#e6f0ea;--aviso:#a85d00;
--avisobg:#fff4e0;--erro:#b3261e;--errobg:#fde8e6;--borda:#dfe5e1;--nova:#fff7d6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#101512;--card:#1a211d;--txt:#e5ebe7;--sub:#97a59d;--verde:#5fb88a;
--verde2:#1f3328;--aviso:#e3a14b;--avisobg:#3a2b12;--erro:#ff8a80;--errobg:#3b1715;--borda:#2a332e;--nova:#3a3418;--achou:#3a3418;color-scheme:dark}}
:root[data-theme=dark]{--bg:#101512;--card:#1a211d;--txt:#e5ebe7;--sub:#97a59d;--verde:#5fb88a;
--verde2:#1f3328;--aviso:#e3a14b;--avisobg:#3a2b12;--erro:#ff8a80;--errobg:#3b1715;--borda:#2a332e;--nova:#3a3418;--achou:#3a3418;color-scheme:dark}
:root{--achou:#fffde7}
.tema{background:none;border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:8px;padding:5px 10px;font-size:15px;cursor:pointer;margin-left:auto}
.tema:hover{background:rgba(255,255,255,.1)}header .tema+.sair{margin-left:8px}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}
header{background:#173f2c;color:#fff;padding:0 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
header b{font-size:17px;padding:14px 0}nav{display:flex;gap:2px;flex-wrap:wrap}
nav a{color:#cfe3d7;text-decoration:none;padding:16px 12px;font-size:14px;border-bottom:3px solid transparent}
header .sair{color:#cfe3d7;text-decoration:none;font-size:13px;border:1px solid rgba(255,255,255,.25);padding:6px 12px;border-radius:8px}header .sair:hover{background:rgba(255,255,255,.1)}
nav a.on{color:#fff;border-bottom-color:#7fd3a5}main{max-width:1250px;margin:auto;padding:18px 16px 60px}
h2{font-size:19px;margin:6px 0 14px}h3{font-size:15px;margin:18px 0 8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:12px 14px}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{font-size:12px;color:var(--sub)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:14px;border-left:5px solid var(--verde)}
.card.aviso{border-left-color:var(--aviso)}.card.erro{border-left-color:var(--erro)}.card.parado{border-left-color:var(--sub);opacity:.75}
.nome{font-weight:700;font-size:16px}.sub{font-size:12px;color:var(--sub)}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;background:var(--verde2);color:var(--verde)}
.tag.aviso{background:var(--avisobg);color:var(--aviso)}.tag.erro{background:var(--errobg);color:var(--erro)}.tag.parado{background:var(--borda);color:var(--sub)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--borda);border-radius:10px;overflow:hidden;font-size:13px}
th{background:var(--verde2);color:var(--txt);text-align:left;padding:8px;font-size:12px}td{padding:8px;border-top:1px solid var(--borda);vertical-align:top}
tr.nova td{background:var(--nova)}.txt{white-space:pre-wrap;max-width:560px}
form.linha{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input,select,textarea{font:inherit;padding:8px 10px;border-radius:7px;border:1px solid var(--borda);background:var(--card);color:var(--txt)}
label{display:block;font-size:13px;font-weight:600;margin:12px 0 4px}label small{font-weight:400;color:var(--sub)}
.campo{width:100%;max-width:520px}.form{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:16px;max-width:640px}
button,.btn{font:inherit;font-size:13px;cursor:pointer;border:0;border-radius:7px;padding:8px 12px;background:var(--verde);color:#fff;text-decoration:none;display:inline-block}
button.sec,.btn.sec{background:var(--borda);color:var(--txt)}button.perigo{background:var(--erro)}
.acoes{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.acoes form{margin:0}
.msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;background:var(--verde2)}.msg.erro{background:var(--errobg);color:var(--erro)}
pre{background:#0f1411;color:#d5e3da;padding:12px;border-radius:8px;overflow:auto;font-size:12px;max-height:560px}
.barra{height:8px;background:var(--borda);border-radius:6px;overflow:hidden;margin-top:6px}.barra i{display:block;height:100%;background:var(--verde)}
.bgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:0 16px}
.ufs{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}.uf{display:flex;gap:3px;align-items:center;font-weight:400;margin:0;padding:4px 8px;border:1px solid var(--borda);border-radius:6px}
.md li{margin:4px 0}.md li.chk{list-style:none;margin-left:-22px}.md li.chk input{margin-right:6px}
.md li.chk label{font-weight:400;display:inline;margin:0;font-size:inherit}
@media print{header,.acoes,.msg{display:none}.analise{border:0}}
.analise{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:6px 20px 16px;max-width:900px;line-height:1.5}
.analise h3{border-bottom:1px solid var(--borda);padding-bottom:4px;margin-top:22px}.analise h4{margin:14px 0 4px;font-size:14px}
.mc{display:grid;grid-template-columns:380px 1fr;gap:14px;height:calc(100vh - 110px);min-height:480px}
.mc-esq{background:var(--card);border:1px solid var(--borda);border-radius:12px;display:flex;flex-direction:column;min-height:0}
.mc-topo{display:flex;justify-content:space-between;align-items:center;padding:14px 14px 8px}
.mc-busca{margin:0 14px 8px}.ferr{display:flex;gap:4px}.ferr button,.ferr .btn{padding:6px 9px;font-size:14px}
.pesq{display:flex;gap:6px;padding:10px 12px;border-bottom:1px solid var(--borda)}.pesq input{flex:1}
#conversa{display:flex;flex-direction:column;flex:1;min-height:0}.cv-cab{border-radius:0}.cv-busca{margin-left:auto;min-width:180px}a.res{display:block;color:var(--txt);text-decoration:none}a.res:hover{background:var(--bg)}.abas{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px 10px;border-bottom:1px solid var(--borda)}
.aba{background:var(--card);color:var(--verde);border:1px solid var(--verde);padding:5px 10px;border-radius:7px;font-size:12px}
.aba.on{background:var(--verde);color:#fff}
.mc-lista{overflow-y:auto;flex:1;min-height:0}
.it{display:block;padding:11px 14px;border-bottom:1px solid var(--borda);color:var(--txt);text-decoration:none}
.it:hover{background:var(--bg)}.it.on{background:var(--verde2);border-left:4px solid var(--verde)}
.it-l1{display:flex;justify-content:space-between;align-items:center;gap:8px}.it-data{font-size:12px;color:var(--sub);display:flex;align-items:center;gap:6px}
.ponto{width:9px;height:9px;border-radius:50%;background:#e53935;display:inline-block}.ponto[hidden]{display:none}
.it-org{font-size:12.5px;color:var(--sub);margin:2px 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.it-l3{display:flex;gap:6px;align-items:center}.it-urg{margin-left:auto}
.selo{color:#fff;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:4px;white-space:nowrap}
.mc-dir{background:var(--card);border:1px solid var(--borda);border-radius:12px;display:flex;flex-direction:column;min-height:0;min-width:0}
.cv-vazio{padding:40px;text-align:center;color:var(--sub)}
.cv-cab{background:#173f2c;color:#fff;padding:14px 18px;border-radius:12px 12px 0 0;font-size:13.5px}.cv-cab .sub{color:#cfe3d7}
.cv-num{font-size:18px;font-weight:700;margin-bottom:4px;display:flex;gap:8px;align-items:center}
.cv-filtro{display:flex;gap:10px;align-items:center;padding:12px 18px;border-bottom:1px solid var(--borda)}.cv-filtro select{min-width:200px}
.cv-lista{overflow-y:auto;flex:1;min-height:0;padding:6px 18px 18px}
.cm{padding:10px 0;border-bottom:1px solid var(--borda)}.cm.urg{border-left:4px solid var(--erro);padding-left:10px}
.cm-cab{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px;letter-spacing:.2px}.cm-hora{margin-left:auto}
.cm-txt{white-space:pre-wrap;font-size:14px;margin-top:4px}
mark{background:#ffb74d;color:#000;border-radius:3px;padding:0 2px}mark.achado.atual{background:#ff6d00;color:#fff;outline:3px solid #bf360c}.nav-busca{display:flex;gap:4px;align-items:center}.nav-busca button{padding:6px 10px}mark.achado{background:#ffeb3b;color:#000;font-weight:700;outline:2px solid #e65100;outline-offset:0;box-shadow:0 0 0 3px #ffe0b2}.cm.achou{background:var(--achou)}
.pesq{flex-wrap:wrap}#pesq-info{align-self:center}
@media (max-width:900px){.mc{grid-template-columns:1fr;height:auto}.mc-lista{max-height:55vh}.cv-lista{max-height:none}}
.pl{margin-bottom:16px;background:var(--card);border:1px solid var(--borda);border-radius:10px;overflow:hidden}
.pl-cab{background:#173f2c;color:#fff;font-weight:700;padding:10px 14px;letter-spacing:.3px}.pl-cab .sub{color:#cfe3d7}
.pr-cab,.pr summary{display:grid;grid-template-columns:110px minmax(180px,1.3fr) 2fr 150px;gap:12px;align-items:center;padding:8px 14px}
.pr-cab{background:var(--verde2);font-size:12px;font-weight:700}
.pr{border-top:1px solid var(--borda)}.pr summary{cursor:pointer;list-style:none}.pr summary::-webkit-details-marker{display:none}
.pr summary:hover{background:var(--bg)}.pr[open] summary{background:var(--nova)}
.pr-num{font-weight:700;font-size:15px}.pr-num:before{content:'▸ ';color:var(--sub)}.pr[open] .pr-num:before{content:'▾ '}
.pr-org{display:flex;flex-direction:column;min-width:0}.pr-org .sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pr-prev{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pr-data{display:flex;flex-direction:column;text-align:right}
.pr-corpo{padding:10px 14px 16px;border-top:1px dashed var(--borda)}.pr-ferr{display:flex;gap:8px;align-items:center;margin-bottom:10px}
.pr-corpo .ch-lista{max-height:520px;overflow-y:auto;padding:0}
@media (max-width:800px){.pr-cab{display:none}.pr summary{grid-template-columns:1fr auto}.pr-org{grid-column:1/-1;order:3}.pr-prev{grid-column:1/-1;order:4}}
.ch-barra{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.chat{display:grid;grid-template-columns:340px 1fr;gap:12px;height:calc(100vh - 170px);min-height:420px}
.ch-lado{background:var(--card);border:1px solid var(--borda);border-radius:10px;overflow-y:auto}
.ch-plat{position:sticky;top:0;background:var(--verde2);color:var(--verde);font-weight:700;font-size:12px;padding:8px 12px;text-transform:uppercase;letter-spacing:.5px;z-index:1}
.ch-item{display:block;padding:10px 12px;border-bottom:1px solid var(--borda);color:var(--txt);text-decoration:none}
.ch-item:hover{background:var(--bg)}.ch-item.on{background:var(--nova);border-left:4px solid var(--verde)}
.ch-l1{display:flex;justify-content:space-between;gap:8px}.ch-org{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-prev{font-size:12.5px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-conv{background:var(--card);border:1px solid var(--borda);border-radius:10px;display:flex;flex-direction:column;min-width:0}
.ch-topo{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid var(--borda)}
.ch-lista{overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.ch-msg{background:var(--verde2);border-radius:10px;padding:9px 12px;max-width:92%}
.ch-msg.sis{background:var(--bg);border:1px dashed var(--borda)}.ch-msg.urg{border-left:4px solid var(--erro)}
.ch-cab{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:4px;font-size:13px}.ch-hora{margin-left:auto}
.ch-msg .txt{max-width:none;font-size:14px}
@media (max-width:800px){.chat{grid-template-columns:1fr;height:auto}.ch-lado{max-height:260px}.ch-lista{max-height:none}}
@media (max-width:700px){.ocultar{display:none}nav a{padding:12px 8px}}
"""

ABAS = [("/", "Visão geral"), ("/mensagens", "Mensagens"), ("/processos", "Processos"),
        ("/buscar", "Buscar"), ("/editais", "Editais (IA)"), ("/empresas", "Empresas"), ("/config", "Configurações")]


def pagina(titulo, corpo, aba="/", aviso="", erro=False, recarregar=False):
    nav = "".join(f'<a href="{u}" class="{"on" if u == aba else ""}">{n}</a>' for u, n in ABAS)
    msg = f'<div class="msg {"erro" if erro else ""}">{aviso}</div>' if aviso else ""
    meta = '<meta http-equiv="refresh" content="60">' if recarregar else ""
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(titulo)} – Licitatudo</title>
{TEMA_JS}<style>{ESTILO}</style></head><body><header><b>Licitatudo</b><nav>{nav}</nav><button class="tema" onclick="trocarTema()" title="Modo escuro">🌙</button><a class="sair" href="/sair">Sair</a></header><main>{msg}{corpo}</main></body></html>"""


def tag_status(emp):
    if not emp["ativo"]:
        return "parado", "Pausado"
    st = emp["status"]
    if st.startswith("Monitor ativo"):
        return "", "Ativo"
    if "Erro" in st or "expirada" in st or "captcha" in st.lower():
        return "erro", st[:60]
    return "aviso", st[:60]


def botao(acao, texto, classe="", confirmar=""):
    js = f' onclick="return confirm({json.dumps(confirmar)})"' if confirmar else ""
    return (f'<form method="post" action="{acao}"><input type="hidden" name="csrf" value="{CSRF}">'
            f'<button class="{classe}"{js}>{texto}</button></form>')


# ------------------------------------------------------------------ páginas
def pg_visao():
    emps = empresas()
    cfg = CFG_ATUAL()
    if not emps and cfg.get("BLL_EMAIL"):
        return pagina("Visão geral", f"""<h2>Converter para várias empresas</h2><div class="form">
<p>Hoje o monitor está configurado para <b>uma</b> empresa. Converta para poder cadastrar outras aqui no painel.
O histórico e o login atuais são mantidos.</p>
<form method="post" action="/migrar"><input type="hidden" name="csrf" value="{CSRF}">
<label>Nome da empresa atual</label><input class="campo" name="nome" value="MULTIFORNECE" required>
<div class="acoes"><button>Converter</button></div></form></div>""")
    total_mem, livre = memoria()
    vagas = int(cfg.get("MAX_NAVEGADORES", "3") or 3)
    intervalo = float((cfg.get("INTERVALO_MINUTOS", "2") or "2").replace(",", "."))
    dur = [x["duracao"] for x in emps if x["duracao"]]
    media = sum(dur) / len(dur) if dur else 60
    capacidade = int(vagas * intervalo * 60 / max(media, 20))
    ativos = [x for x in emps if x["ativo"]]
    uso = min(100, int(100 * len(ativos) / max(capacidade, 1)))
    kpis = f"""<div class="kpis">
<div class="kpi"><div class="v">{len(ativos)}/{len(emps)}</div><div class="l">empresas ativas</div></div>
<div class="kpi"><div class="v">{sum(x['andamento'] for x in emps)}</div><div class="l">processos em andamento</div></div>
<div class="kpi"><div class="v">{sum(x['novas_hoje'] for x in emps)}</div><div class="l">mensagens novas hoje</div></div>
<div class="kpi"><div class="v">{len(ativos)} de ~{capacidade}</div><div class="l">capacidade no intervalo de {intervalo:g} min
<div class="barra"><i style="width:{uso}%"></i></div></div></div>
<div class="kpi"><div class="v">{livre} MB</div><div class="l">memória livre de {total_mem} MB · {vagas} navegador(es) simultâneos</div></div>
</div>"""
    cards, posicoes = "", []
    ign = {t.strip().upper() for t in cfg.get("SITUACOES_IGNORADAS", "HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO").split(",")}
    for x in emps:
        ativos_proc = {(p[0], p[1]) for p in x["procs"] if (p[2] or "").upper() not in ign}
        for r in classif(x["id"]):
            if (r[0], r[1]) in ativos_proc and "CANCELAD" not in (r[4] or "").upper():
                posicoes.append((x, r))
    for x in emps:
        cls, txt = tag_status(x)
        mine = [r for (y, r) in posicoes if y["id"] == x["id"] and r[8]]
        x["resumo_pos"] = (f'<div class="sub">📊 {sum(1 for r in mine if r[8] == 1)} lote(s) em 1º · '
                           f'{sum(1 for r in mine if r[8] > 1)} em outras posições</div>') if mine else ""
        cards += f"""<div class="card {cls}"><div class="nome">{e(x['nome'])} <span class="tag parado">{e(x['plat'])}</span></div>
<div style="margin:6px 0"><span class="tag {cls}">{e(txt)}</span></div>
<div class="sub">{x['andamento']} processo(s) em andamento · {x['novas_hoje']} nova(s) hoje</div>
{x['resumo_pos']}<div class="sub">Última verificação: {e(x['ultima'] or '—')}{f" · ciclo {x['duracao']} s" if x['duracao'] else ""}</div>
<div class="acoes"><a class="btn sec" href="/mensagens?empresa={e(x['id'])}">Mensagens</a>
<a class="btn sec" href="/processos?empresa={e(x['id'])}">Processos</a>
<a class="btn sec" href="/empresas/{e(x['id'])}">Editar</a></div></div>"""
    if capacidade and len(ativos) > capacidade:
        aviso = (f"⚠️ Há mais empresas ativas ({len(ativos)}) do que o servidor consegue verificar a cada "
                 f"{intervalo:g} min (~{capacidade}). As verificações ficam mais espaçadas. Aumente o intervalo, "
                 "os navegadores simultâneos (se houver memória) ou o plano do servidor.")
    else:
        aviso = ""
    tab_pos = ""
    if posicoes:
        def ordem(it):
            r = it[1]
            return (r[8] is None, r[8] or 99, it[0]["nome"], r[0], r[2])
        linhas = ""
        for x, r in sorted(posicoes, key=ordem):
            if r[8] == 1:
                pos = '<span class="tag">🥇 1º</span>'
            elif r[8]:
                pos = f'<span class="tag aviso">{r[8]}º</span>'
            elif r[9]:
                pos = '<span class="tag erro">desclassificada</span>'
            else:
                pos = '<span class="tag parado">sem lance</span>'
            try:
                rk = json.loads(r[7] or "[]")
                lider = next((z for z in rk if z.get("pos") == 1), None)
            except ValueError:
                lider = None
            lid = ("R$ " + f'{lider["valor"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".")) if lider else e(r[6])
            url = "/classificacao?" + urllib.parse.urlencode({"empresa": x["id"], "processo": r[0], "promotor": r[1]})
            linhas += (f'<tr><td>{pos}</td><td>{e(x["nome"])} <span class="sub">{e(x["plat"])}</span></td>'
                       f'<td><a href="{url}"><b>{e(r[0])}</b></a><div class="sub ocultar">{e(r[1])}</div></td>'
                       f'<td>L{e(nlote(r[2]))}</td><td class="ocultar">{e(r[4])}</td>'
                       f'<td>{e(r[9]) or "—"}</td><td class="ocultar">{lid}</td></tr>')
        tab_pos = ('<h2 style="margin-top:22px">Nossa posição nos lotes</h2>'
                   '<table><tr><th>Posição</th><th>Empresa</th><th>Processo</th><th>Lote</th><th class="ocultar">Fase</th>'
                   f'<th>Nosso lance</th><th class="ocultar">Lance do 1º</th></tr>{linhas}</table>'
                   '<p class="sub">Só processos em andamento. Clique no processo para ver a classificação completa.</p>')
    corpo = (kpis + (f'<div class="msg erro">{aviso}</div>' if aviso else "") + tab_pos +
             f'<h2>Empresas</h2><div class="grid">{cards or "<p>Nenhuma empresa. <a href=/empresas/nova>Cadastrar</a></p>"}</div>')
    return pagina("Visão geral", corpo, "/", recarregar=True)


def urg_tag(m):
    motivo = urgencia.classificar(m["texto"], m["escopo"], m["autor"], chat_urgente=CFG_ATUAL().get("CHAT_URGENTE", "1") != "0")
    return f'<span class="tag erro">🚨 {e(motivo)}</span><br>' if motivo else ""


def grupo_escopo(esc):
    """'Lote 1', 'LOTE 01', 'L1 – registro do lote' -> 'L1'; outros viram categorias."""
    m = re.search(r"(?i)\b(?:lote|l)\s*0*(\d+)\b", esc or "")
    if m:
        return f"L{m.group(1)}"
    n = unicodedata.normalize("NFKD", esc or "").encode("ascii", "ignore").decode().lower()
    for chave, nome in (("situac", "Mudança de situação"), ("document", "Documentos"), ("notifica", "Notificações"),
                        ("classifica", "Classificação"), ("dilig", "Diligências"), ("evento", "Processo (eventos)")):
        if chave in n:
            return nome
    return esc or "Outros"


def agrupar_processos(busca="", empresa=""):
    procs = {}
    for m in mensagens(empresa, busca):
        k = (m["eid"], m["processo"], m["promotor"])
        g = procs.setdefault(k, {"eid": m["eid"], "nome": m["nome"], "plat": m["plat"], "processo": m["processo"],
                                 "promotor": m["promotor"], "msgs": [], "ultima": datetime.min, "ult_msg": None})
        g["msgs"].append(m)
        d = data_msg(m)
        if d >= g["ultima"]:
            g["ultima"], g["ult_msg"] = d, m
    return procs


ARQ_LIDOS = PASTA / "mensagens_lidas.json"
CORES_PLAT = {"BLL": "#2e7d32", "BNC": "#1f3a93", "Portal de Compras": "#e8710a", "Licitanet": "#7b1fa2"}


def lidos():
    try:
        return json.loads(ARQ_LIDOS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def marcar_lido(chave):
    d = lidos()
    d[chave] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        ARQ_LIDOS.write_text(json.dumps(d), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def marcar_todos_lidos():
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    d = lidos()
    for g in agrupar_processos().values():
        d[chave_proc(g)] = agora
    ARQ_LIDOS.write_text(json.dumps(d), encoding="utf-8")


def pg_busca_msgs(q):
    """Resultados da pesquisa em todas as mensagens (para o painel da direita)."""
    termo = (q.get("q") or "").strip()
    if len(termo) < 2:
        return barra_msgs(termo) + '<div class="cv-vazio">Digite pelo menos 2 letras.</div>'
    achados = sorted(mensagens("", termo), key=lambda m: (data_msg(m), para_data(m["visto"])), reverse=True)
    itens = ""
    for m in achados[:300]:
        url = "/mensagens/processo?" + urllib.parse.urlencode({"eid": m["eid"], "processo": m["processo"], "promotor": m["promotor"]})
        txt = e(m["texto"])
        pat = regex_busca(termo)
        txt = e(pat.sub(lambda x: "\x00" + x.group(0) + "\x01", m["texto"])) if pat else txt
        txt = txt.replace("\x00", '<mark class="achado">').replace("\x01", "</mark>")
        itens += (f'<a class="cm res" href="#" data-url="{e(url)}" onclick="return abrirUrl(this.dataset.url)">'
                  f'<div class="cm-cab"><b>{e(m["processo"])}</b>{selo_plat(m["plat"])}<span class="sub">{e(m["promotor"][:40])} · '
                  f'{e(grupo_escopo(m["escopo"]))}</span><span class="sub cm-hora">{e(m["horario"] or m["visto"])}</span></div>'
                  f'<div class="cm-txt">{txt}</div></a>')
    return (f'<div class="cv-cab"><div class="cv-num">🔍 “{e(termo)}”</div><div class="sub">{len(achados)} mensagem(ns) encontrada(s)'
            f'{" – mostrando 300" if len(achados) > 300 else ""}. Clique para abrir o processo.</div></div>' + barra_msgs(termo) +
            f'<div class="cv-lista">{itens or "<div class=sub>Nada encontrado.</div>"}</div>')


def chave_proc(g):
    return f'{g["eid"]}|{g["processo"]}|{g["promotor"]}'


def nao_lido(g, mapa):
    vista = mapa.get(chave_proc(g))
    if not vista:
        return any(m["nova"] for m in g["msgs"])
    lim = para_data(vista)
    return any(para_data(m["visto"]) > lim for m in g["msgs"])


def selo_plat(plat):
    return f'<span class="selo" style="background:{CORES_PLAT.get(plat, "#5f6d65")}">{e(plat)}</span>'


_ACENTOS = {"a": "[aáàâã]", "e": "[eéê]", "i": "[ií]", "o": "[oóôõ]", "u": "[uúü]", "c": "[cç]"}


def destacar(txt_html):
    """Marca as palavras de urgência no texto (já escapado)."""
    for trecho, _ in urgencia.regras():
        if len(trecho) < 4:
            continue
        padrao = "".join(_ACENTOS.get(ch, re.escape(ch)) for ch in trecho)
        txt_html = re.sub(r"(?i)(?<![\w>])(" + padrao + r"\w*)", r"<mark>\1</mark>", txt_html)
    return txt_html


def titulo_msg(m):
    g = grupo_escopo(m["escopo"])
    if g == "Mudança de situação":
        return "TRANSIÇÃO DE FASE"
    if g == "Processo (eventos)":
        return "SESSÃO – PROCESSO"
    if g in ("Documentos", "Notificações", "Classificação", "Diligências"):
        return g.upper()
    if "registro" in (m["escopo"] or "").lower():
        return f"{g} – REGISTRO DO LOTE"
    return f"{(m['autor'] or 'MENSAGEM').upper()} · {g}"


def barra_msgs(valor=""):
    """Pesquisa + marcar todas como lidas, dentro do painel das mensagens."""
    return (f'<form class="pesq" onsubmit="return proximo(event)">'
            f'<input id="pesq-txt" value="{e(valor)}" placeholder="🔍 Localizar nas mensagens… (Enter = próxima)" '
            f'oninput="buscaConversa(this)" onkeydown="teclaBusca(event)">'
            f'<span class="nav-busca"><span id="pesq-pos" class="sub">0/0</span>'
            f'<button type="button" class="sec" title="Anterior (Shift+Enter)" onclick="irPara(-1)">▲</button>'
            f'<button type="button" class="sec" title="Próxima (Enter)" onclick="irPara(1)">▼</button></span>'
            f'<button type="button" class="sec" title="Marcar todas as mensagens como lidas" onclick="marcarTodas()">✔✔ Marcar todas como lidas</button>'
            f'<button type="button" class="sec" title="Atualizar agora" onclick="location.reload()">⟳ Atualizar</button>'
            f'<span id="pesq-info" class="sub"></span></form>')


def html_conversa(g, cfg_chat):
    grupos = sorted({grupo_escopo(m["escopo"]) for m in g["msgs"]},
                    key=lambda x: (not (x.startswith("L") and x[1:].isdigit()), int(x[1:]) if x[1:].isdigit() else 0, x))
    contagem = {x: sum(1 for m in g["msgs"] if grupo_escopo(m["escopo"]) == x) for x in grupos}
    ops = "".join(f'<option value="{e(x)}">{"Lote " + x[1:] if x.startswith("L") and x[1:].isdigit() else e(x)} ({contagem[x]})</option>'
                  for x in grupos)
    msgs = sorted(g["msgs"], key=lambda m: (data_msg(m), para_data(m["visto"])), reverse=True)
    itens = ""
    for m in msgs:
        urg = urgencia.classificar(m["texto"], m["escopo"], m["autor"], chat_urgente=cfg_chat)
        tag_urg = f' <span class="tag erro">🚨 {e(urg)}</span>' if urg else ""
        itens += (f'<div class="cm{" urg" if urg else ""}" data-lote="{e(grupo_escopo(m["escopo"]))}">'
                  f'<div class="cm-cab"><b>{e(titulo_msg(m))}</b>{tag_urg}<span class="sub cm-hora">{e(m["horario"] or m["visto"])}</span></div>'
                  f'<div class="cm-txt">{destacar(e(m["texto"]))}</div></div>')
    return (f'<div class="cv-cab"><div><div class="cv-num">{e(g["processo"])} {selo_plat(g["plat"])}</div>'
            f'<div><b>Órgão:</b> {e(g["promotor"])}</div><div class="sub"><b>Empresa:</b> {e(g["nome"])} · '
            f'{len(g["msgs"])} mensagem(ns)</div></div></div>'
            f'<div class="cv-filtro"><b>Mensagens do chat</b><select onchange="filtrarLote(this)">'
            f'<option value="">Todos os lotes ({len(g["msgs"])})</option>{ops}</select></div>' + barra_msgs() +
            f'<div class="cv-lista">{itens or "<div class=sub>Sem mensagens.</div>"}</div>')


def pg_chat(q):
    """Monitorar chat: lista de processos à esquerda (rolagem, abas por plataforma), conversa à direita."""
    procs = agrupar_processos()
    cfg_chat = CFG_ATUAL().get("CHAT_URGENTE", "1") != "0"
    mapa = lidos()
    lista = sorted(procs.values(), key=lambda x: x["ultima"], reverse=True)
    plats = [pl for pl in ("BLL", "BNC", "Portal de Compras", "Licitanet") if any(g["plat"] == pl for g in lista)]
    plats += sorted({g["plat"] for g in lista} - set(plats))
    abas = ('<button class="aba on" data-f="todos" onclick="aba(this)">Todos</button>'
            '<button class="aba" data-f="naolidos" onclick="aba(this)">Não lidos</button>'
            '<button class="aba" data-f="urgentes" onclick="aba(this)">Urgentes</button>' +
            "".join(f'<button class="aba" data-f="plat:{e(pl)}" onclick="aba(this)">{e(pl)}</button>' for pl in plats))
    sel = q.get("processo") and (q.get("eid", ""), q.get("processo", ""), q.get("promotor", ""))
    itens = ""
    for g in lista:
        um = g["ult_msg"] or {}
        urg = urgencia.classificar(um.get("texto", ""), um.get("escopo", ""), um.get("autor", ""), chat_urgente=cfg_chat)
        nl = nao_lido(g, mapa)
        quando = g["ultima"].strftime("%d/%m/%Y - %H:%M") if g["ultima"] != datetime.min else "—"
        url = "/mensagens/processo?" + urllib.parse.urlencode({"eid": g["eid"], "processo": g["processo"], "promotor": g["promotor"]})
        busca_txt = f'{g["processo"]} {g["promotor"]} {g["nome"]} {g["plat"]}'.lower()
        on = sel == (g["eid"], g["processo"], g["promotor"])
        itens += (f'<a class="it{" on" if on else ""}" href="#" data-url="{e(url)}" data-plat="{e(g["plat"])}" data-nl="{1 if nl else 0}" '
                  f'data-urg="{1 if urg else 0}" data-busca="{e(busca_txt)}" onclick="return abrir(this)">'
                  f'<div class="it-l1"><b>{e(g["processo"])}</b><span class="it-data">{e(quando)}'
                  f'<i class="ponto"{"" if nl else " hidden"}></i></span></div>'
                  f'<div class="it-org">{e(g["promotor"])}</div>'
                  f'<div class="it-l3">{selo_plat(g["plat"])}<span class="sub">{e(g["nome"])}</span>'
                  f'{"<span class=it-urg>🚨</span>" if urg else ""}</div></a>')
    direita = barra_msgs() + '<div class="cv-vazio">Selecione um processo à esquerda para ver as mensagens.</div>'
    if sel and sel in procs:
        direita = html_conversa(procs[sel], cfg_chat)
        marcar_lido(chave_proc(procs[sel]))
    corpo = f"""<div class="mc">
<aside class="mc-esq"><div class="mc-topo"><h2 style="margin:0">Monitorar chat</h2>
<div class="ferr">
<a class="btn sec" href="/mensagens?vista=lista" title="Ver todas as mensagens em tabela">☰</a></div></div>

<input class="mc-busca" placeholder="Buscar pregão, órgão ou empresa…" oninput="filtrar()">
<div class="abas">{abas}</div>
<div class="mc-lista" id="lista">{itens or '<div class="sub" style="padding:14px">Nenhuma mensagem ainda.</div>'}</div></aside>
<section class="mc-dir"><div id="conversa">{direita}</div></section></div>
<script>
let filtroAba='todos';
function filtrar(){{const t=document.querySelector('.mc-busca').value.toLowerCase();
 document.querySelectorAll('#lista .it').forEach(a=>{{let ok=!t||a.dataset.busca.includes(t);
  if(filtroAba==='naolidos')ok=ok&&a.dataset.nl==='1'; else if(filtroAba==='urgentes')ok=ok&&a.dataset.urg==='1';
  else if(filtroAba.startsWith('plat:'))ok=ok&&a.dataset.plat===filtroAba.slice(5);
  a.style.display=ok?'':'none';}});}}
function aba(b){{document.querySelectorAll('.aba').forEach(x=>x.classList.remove('on'));b.classList.add('on');filtroAba=b.dataset.f;filtrar();
 try{{sessionStorage.setItem('aba',filtroAba);}}catch(e){{}}}}
function abrir(a){{document.querySelectorAll('#lista .it').forEach(x=>x.classList.remove('on'));a.classList.add('on');
 const c=document.getElementById('conversa');c.innerHTML='<div class="cv-vazio">Carregando…</div>';
 fetch(a.dataset.url,{{credentials:'same-origin'}}).then(r=>r.text()).then(h=>{{c.innerHTML=h;a.dataset.nl='0';
  const p=a.querySelector('.ponto'); if(p)p.hidden=true;
  try{{const v=sessionStorage.getItem('lote|'+a.dataset.url); if(v){{const s=c.querySelector('select'); s.value=v; filtrarLote(s);}}}}catch(e){{}}
  history.replaceState(null,'','/mensagens?'+a.dataset.url.split('?')[1]);
  if(window.innerWidth<900)c.scrollIntoView({{behavior:'smooth'}});}}).catch(()=>{{c.innerHTML='<div class="cv-vazio">Não consegui carregar.</div>';}});
 return false;}}
function abrirUrl(u){{const a=[...document.querySelectorAll('#lista .it')].find(x=>x.dataset.url===u);
 if(a){{a.style.display='';return abrir(a);}} location.href='/mensagens?'+u.split('?')[1]; return false;}}
function marcarTodas(){{fetch('/mensagens/lidas',{{method:'POST',credentials:'same-origin',
  headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'csrf={CSRF}'}}).then(r=>{{if(r.ok){{
  document.querySelectorAll('#lista .it').forEach(a=>{{a.dataset.nl='0';const p=a.querySelector('.ponto');if(p)p.hidden=true;}});filtrar();}}}});}}
function pesquisar(){{const t=document.getElementById('pesq-txt').value.trim();const c=document.getElementById('conversa');
 document.querySelectorAll('#lista .it').forEach(x=>x.classList.remove('on'));
 c.innerHTML='<div class="cv-vazio">Pesquisando…</div>';
 fetch('/mensagens/busca?q='+encodeURIComponent(t),{{credentials:'same-origin'}}).then(r=>r.text()).then(h=>{{c.innerHTML=h;
  const mk=c.querySelector('mark.achado'); if(mk)mk.scrollIntoView({{block:'center'}});}});return false;}}
let tBusca=null;
function buscaConversa(i){{clearTimeout(tBusca);tBusca=setTimeout(()=>aplicarBusca(i),250);}}
function padraoBusca(t){{const sem=t.normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().trim(); if(!sem)return null;
 const ac={{a:'[aáàâãä]',e:'[eéèêë]',i:'[iíìîï]',o:'[oóòôõö]',u:'[uúùûü]',c:'[cç]',n:'[nñ]'}};
 let p=''; for(const ch of sem){{ if(!/[0-9a-z]/.test(ch)){{ if(!p.endsWith(')+'))p+='(?:[^0-9A-Za-zÀ-ÿ])+'; }}
  else p+=ac[ch]||ch; }}
 return new RegExp(p,'gi');}}
function grifar(el,t){{  // grifa o termo pesquisado dentro do texto da mensagem
 if(el.dataset.h===undefined)el.dataset.h=el.innerHTML; else if(el.dataset.g)el.innerHTML=el.dataset.h;
 el.dataset.g=''; if(!t)return;
 const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT); const nos=[]; while(w.nextNode())nos.push(w.currentNode);
 const re=padraoBusca(t); if(!re)return;
 nos.forEach(n=>{{const tx=n.nodeValue; re.lastIndex=0; let mt=re.exec(tx); if(!mt)return;
  const f=document.createDocumentFragment(); let ult=0;
  while(mt){{ if(!mt[0].length){{re.lastIndex++; mt=re.exec(tx); continue;}}
   f.appendChild(document.createTextNode(tx.slice(ult,mt.index))); const mk=document.createElement('mark'); mk.className='achado';
   mk.textContent=mt[0]; f.appendChild(mk); ult=mt.index+mt[0].length; mt=re.exec(tx);}}
  f.appendChild(document.createTextNode(tx.slice(ult))); n.parentNode.replaceChild(f,n);}});
 el.dataset.g='1';}}
// Localizar (como Ctrl+F): grifa tudo, ▲ ▼ navegam entre as ocorrências, sem esconder mensagens
let achados=[], atual=-1;
function aplicarBusca(i){{const t=(i&&i.value||'').trim();const s=document.querySelector('#conversa select');const v=s?s.value:'';
 const re=padraoBusca(t);
 document.querySelectorAll('#conversa .cm').forEach(m=>{{if(m.dataset.t===undefined)m.dataset.t=m.textContent;
  const vis=(!v||m.dataset.lote===v); if((m.style.display!=='none')!==vis)m.style.display=vis?'':'none';
  if(re)re.lastIndex=0; const tem=vis&&re&&re.test(m.dataset.t);
  const tx=m.querySelector('.cm-txt'); if(tx&&(tem||tx.dataset.g))grifar(tx, tem?t:'');
  m.classList.toggle('achou', !!tem);}});
 achados=[...document.querySelectorAll('#conversa mark.achado')].filter(x=>x.offsetParent!==null);
 atual=-1; if(achados.length)irPara(1); else posicao();}}
function posicao(){{const el=document.getElementById('pesq-pos'); if(el)el.textContent=(achados.length?(atual+1):0)+'/'+achados.length;}}
function irPara(d){{if(!achados.length){{aplicarBusca(document.getElementById('pesq-txt'));return;}}
 if(atual>=0&&achados[atual])achados[atual].classList.remove('atual');
 atual=(atual+d+achados.length)%achados.length; const mk=achados[atual]; mk.classList.add('atual');
 mk.scrollIntoView({{block:'center',behavior:'smooth'}}); posicao();}}
function teclaBusca(ev){{if(ev.key==='Enter'){{ev.preventDefault(); clearTimeout(tBusca);
  if(!achados.length)aplicarBusca(ev.target); else irPara(ev.shiftKey?-1:1);}}}}
function proximo(ev){{ev.preventDefault();return false;}}
// atualização automática só quando você não está digitando
setInterval(()=>{{const a=document.activeElement; const p=document.getElementById('pesq-txt');
 if(p&&p.value.trim())return;              // pesquisa ativa: não recarrega
 if(document.querySelector('#conversa a.res'))return;   // mostrando resultados da busca
 if(!a||!['INPUT','SELECT','TEXTAREA'].includes(a.tagName))location.reload();}},60000);
function filtrarLote(s){{aplicarBusca(document.getElementById('pesq-txt'));
 const a=document.querySelector('#lista .it.on'); try{{if(a)sessionStorage.setItem('lote|'+a.dataset.url,v);}}catch(e){{}}}}
try{{const f=sessionStorage.getItem('aba'); if(f){{const b=document.querySelector('.aba[data-f="'+f+'"]'); if(b)aba(b);}}}}catch(e){{}}
(function(){{const a=document.querySelector('#lista .it.on'); if(a){{a.scrollIntoView({{block:'center'}});
 try{{const v=sessionStorage.getItem('lote|'+a.dataset.url); const s=document.querySelector('#conversa select'); if(v&&s){{s.value=v;filtrarLote(s);}}}}catch(e){{}}}}}})();
</script>"""
    return pagina("Mensagens", corpo, "/mensagens", recarregar=False)


def pg_proc_mensagens(q):
    procs = agrupar_processos()
    g = procs.get((q.get("eid", ""), q.get("processo", ""), q.get("promotor", "")))
    if not g:
        return '<div class="cv-vazio">Sem mensagens.</div>'
    marcar_lido(chave_proc(g))
    return html_conversa(g, CFG_ATUAL().get("CHAT_URGENTE", "1") != "0")


def pg_mensagens(q):
    emp, busca, so_novas = q.get("empresa", ""), q.get("q", ""), q.get("novas", "")
    emps = empresas()
    ops = "".join(f'<option value="{e(x["id"])}" {"selected" if x["id"] == emp else ""}>{e(x["nome"])} · {e(x["plat"])}</option>'
                  for x in emps)
    msgs = mensagens(emp, busca)
    if so_novas:
        msgs = [m for m in msgs if m["nova"]]
    if q.get("urgentes"):
        msgs = [m for m in msgs if urgencia.classificar(m["texto"], m["escopo"], m["autor"], chat_urgente=CFG_ATUAL().get("CHAT_URGENTE", "1") != "0")]
    por_pag = 300
    total = len(msgs)
    total_pag = max(1, -(-total // por_pag))
    pag = min(max(1, int(re.sub(r"\D", "", q.get("pagina", "1")) or 1)), total_pag)
    msgs = msgs[(pag - 1) * por_pag: pag * por_pag]
    base = "/mensagens?" + urllib.parse.urlencode({k: v for k, v in q.items() if k != "pagina"})
    nav = paginacao(pag, total_pag, base)
    linhas = "".join(
        f'<tr class="{"nova" if m["nova"] and m["visto"].startswith(datetime.now().strftime("%d/%m/%Y")) else ""}">'
        f'<td>{e(m["emp"])}</td><td><b>{e(m["processo"])}</b><div class="sub ocultar">{e(m["promotor"])}</div></td>'
        f'<td class="ocultar">{e(m["escopo"])}</td><td>{e(m["horario"])}</td><td class="ocultar">{e(m["autor"])}</td>'
        f'<td class="txt">{urg_tag(m)}{e(m["texto"])}</td></tr>' for m in msgs)
    corpo = f"""<h2>Mensagens</h2><form class="linha" method="get">
<select name="empresa"><option value="">Todas as empresas</option>{ops}</select>
<input name="q" value="{e(busca)}" placeholder="Buscar texto, processo, autor…">
<label style="margin:0;display:flex;align-items:center;gap:6px;font-weight:400"><input type="checkbox" name="novas" value="1" {"checked" if so_novas else ""}> só novas</label>
<label style="margin:0;display:flex;align-items:center;gap:6px;font-weight:400"><input type="checkbox" name="urgentes" value="1" {"checked" if q.get("urgentes") else ""}> só urgentes</label>
<input type="hidden" name="vista" value="lista"><button>Filtrar</button><a class="btn sec" href="/mensagens">Ver como chat</a></form>
<p class="sub">{total} mensagem(ns) · página {pag} de {total_pag} · 300 por página · mais recentes primeiro</p>{nav}
<table><tr><th>Empresa</th><th>Processo</th><th class="ocultar">Origem</th><th>Horário</th><th class="ocultar">Autor</th><th>Mensagem</th></tr>
{linhas or '<tr><td colspan="6">Nenhuma mensagem.</td></tr>'}</table>{nav}
<p class="sub">Destaque amarelo = chegou hoje depois do cadastro.</p>"""
    return pagina("Mensagens", corpo, "/mensagens", recarregar=(pag == 1))


def classif(eid, processo=None, promotor=None):
    db = EMPRESAS / eid / "bll_monitor.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        sql = "SELECT processo, promotor, lote, descricao, fase, primeiro, melhor, ranking, nossa_pos, nosso_lance, atualizado FROM classificacao"
        args = ()
        if processo is not None:
            sql += " WHERE processo=? AND promotor=?"
            args = (processo, promotor)
        linhas = con.execute(sql, args).fetchall()
        con.close()
    except Exception:  # noqa: BLE001  (tabela ainda não existe)
        return []
    def num(x):
        try:
            return int(re.sub(r"\D", "", x) or 0)
        except ValueError:
            return 0
    return sorted(linhas, key=lambda r: num(r[2]))


def nlote(n):
    """'0001' -> '1' (lotes e itens sempre como L1, L2...)."""
    n = str(n or "").strip()
    return str(int(n)) if n.isdigit() else n


def resumo_classif(lotes):
    partes = []
    for r in lotes:
        if r[8]:
            partes.append(f'<span class="tag {"" if r[8] == 1 else "aviso"}">L{e(nlote(r[2]))}: {r[8]}º</span>')
        elif r[9]:
            partes.append(f'<span class="tag erro">L{e(nlote(r[2]))}: desclass.</span>')
    sem = sum(1 for r in lotes if not r[8] and not r[9])
    if sem and partes:
        partes.append(f'<span class="tag parado">+{sem} sem lance</span>')
    elif sem:
        partes.append(f'<span class="tag parado">{sem} lote(s) sem lance da empresa</span>')
    return " ".join(partes)


def salvar_ranking_pcp(eid, nome_arq, dados):
    """Grava no banco da empresa a classificação lida do PDF 'Ranking nos Itens' do Portal de Compras."""
    import pcp_ranking
    if dados[:4] != b"%PDF":
        return None, "O arquivo não é um PDF."
    try:
        itens = pcp_ranking.ranking_pdf(dados)
        num, orgao = pcp_ranking.cabecalho(dados)
    except Exception as ex:  # noqa: BLE001
        return None, f"Não consegui ler o PDF ({ex})."
    if not itens:
        return None, "Não encontrei itens com classificação neste PDF. É o “Ranking nos Itens”?"
    cfg = ler_env(EMPRESAS / f"{eid}.env")
    pcp_ranking.classificar(itens, cfg.get("CLASSIFICACAO_NOME") or cfg.get("EMPRESA_NOME", ""), cfg.get("EMPRESA_CNPJ", ""))
    (EMPRESAS / eid).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(EMPRESAS / eid / "bll_monitor.db", timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS classificacao(
        chave TEXT PRIMARY KEY, processo TEXT, promotor TEXT, lote TEXT, descricao TEXT, fase TEXT,
        primeiro TEXT, melhor TEXT, ranking TEXT, nossa_pos INTEGER, nosso_lance TEXT, lido_em REAL, atualizado TEXT)""")
    processo, promotor = num, orgao
    m = re.search(r"(\d{5,})", nome_arq or "")
    try:
        achado = None
        if m:
            achado = con.execute("SELECT processo, promotor FROM processos WHERE url LIKE ?", (f"%ttCD_CHAVE={m.group(1)}%",)).fetchone()
        if not achado and num:
            achado = con.execute("SELECT processo, promotor FROM processos WHERE processo=?", (num,)).fetchone()
        if achado:
            processo, promotor = achado
    except sqlite3.Error:
        pass
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    moeda = lambda v: ("R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")) if v is not None else ""  # noqa: E731
    for n, it in itens.items():
        rk = [{"pos": x["pos"], "nome": x["nome"], "num": x["cnpj"], "valor": x["valor"] or 0, "excluido": x["excluido"],
               "qtd": "", "marca": x.get("marca", ""), "tipo": x.get("tipo", "")} for x in it["ranking"]]
        nossa = next((x for x in it["ranking"] if x["nossa"]), None)
        lider = next((x for x in it["ranking"] if x["pos"] == 1), None)
        con.execute("INSERT OR REPLACE INTO classificacao VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"pcp|{processo}|{promotor}|{n}", processo, promotor, n, it["descricao"][:200], "Ranking (PDF)",
                     lider["nome"] if lider else "", moeda(lider["valor"]) if lider else "", json.dumps(rk, ensure_ascii=False),
                     nossa["pos"] if nossa else None, moeda(nossa["valor"]) if nossa else "", 0, agora))
    con.commit()
    con.close()
    return (processo, promotor), None


def eh_pcp(eid):
    return ler_env(EMPRESAS / f"{eid}.env").get("PLATAFORMA", "").lower() == "pcp"


def pg_classificacao(q):
    eid, proc, prom = q.get("empresa", ""), q.get("processo", ""), q.get("promotor", "")
    if not (EMPRESAS / f"{eid}.env").exists():
        return pagina("Classificação", "<h2>Empresa não encontrada</h2>", "/processos")
    cfg_emp = ler_env(EMPRESAS / f"{eid}.env")
    pcp = cfg_emp.get("PLATAFORMA", "").lower() == "pcp"
    nome = cfg_emp.get("EMPRESA_NOME", eid)
    nrm = lambda t: " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper().split())  # noqa: E731
    alvo = nrm(cfg_emp.get("CLASSIFICACAO_NOME") or nome)
    blocos = ""
    for r in classif(eid, proc, prom):
        try:
            rank = json.loads(r[7] or "[]")
        except ValueError:
            rank = []
        linhas = ""
        for x in rank:
            cnpj_emp = re.sub(r"\D", "", cfg_emp.get("EMPRESA_CNPJ", ""))
            nossa = (bool(alvo) and alvo in nrm(x.get("nome"))) or (bool(cnpj_emp) and re.sub(r"\D", "", str(x.get("num"))) == cnpj_emp)
            cls = "nova" if nossa else ""
            pos = f'{x["pos"]}º' if x.get("pos") else "❌"
            val = "R$ " + f'{x["valor"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".")
            linhas += (f'<tr class="{cls}"><td><b>{pos}</b></td><td>{e(x["nome"])}{" ⭐" if nossa else ""}'
                       f'<div class="sub">{"CNPJ" if "/" in str(x["num"]) else "Participante"} {e(x["num"])}'
                       f'{(" · marca " + e(x["marca"])) if x.get("marca") else ""}'
                       f'{" · desclassificada/inabilitada" if x.get("excluido") else ""}</div></td>'
                       f'<td>{val}</td><td class="ocultar">{x.get("qtd", "")}</td></tr>')
        dif = ""
        lider = next((z for z in rank if z.get("pos") == 1), None)
        minha = next((z for z in rank if alvo and alvo in nrm(z.get("nome"))), None)
        if lider and minha and r[8] and r[8] > 1 and lider.get("valor"):
            d = minha["valor"] - lider["valor"]
            dif = (f' · {"acima" if d >= 0 else "abaixo"} do 1º em R$ '
                   + f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                   + f' ({str(round(abs(d) / lider["valor"] * 100, 1)).replace(".", ",")}%)')
        sua = (f'<span class="tag {"" if r[8] == 1 else "aviso"}">Sua posição: {r[8]}º · {e(r[9])}{dif}</span>' if r[8] else
               f'<span class="tag erro">Sua empresa: desclassificada/inabilitada · {e(r[9])}</span>' if r[9] else
               '<span class="tag parado">Sua empresa não aparece nos lances deste lote</span>')
        blocos += (f'<h3>L{e(nlote(r[2]))} – {e(r[3][:120])} <span class="tag parado">{e(r[4])}</span></h3>'
                   f'<p class="sub">{sua} · 1º na plataforma: {e(r[5])} {e(r[6])} · lido em {e(r[10])}</p>'
                   f'<table><tr><th>Pos.</th><th>Empresa</th><th>Melhor lance</th><th class="ocultar">Lances</th></tr>'
                   f'{linhas or "<tr><td colspan=4>Sem lances registrados.</td></tr>"}</table>')
    corpo = (f'<h2>Classificação – {e(proc)}</h2><p class="sub">{e(nome)} · {e(prom)}</p>'
             f'<p><a class="btn sec" href="/processos?empresa={e(eid)}">← Processos</a></p>'
             + (blocos or '<div class="msg">Ainda não li a classificação deste processo. Ela aparece depois do próximo ciclo do monitor '
                '(só para processos que já tiveram disputa).</div>') +
             '<p class="sub">Calculada pelos “Registros da sessão do lote”: menor lance de cada empresa, '
             'descontando as marcadas como desclassificadas/inabilitadas. Confira sempre na plataforma.</p>')
    return pagina("Classificação", corpo, "/processos", recarregar=True)


def pg_processos(q):
    emp, filtro = q.get("empresa", ""), q.get("situacao", "")
    emps = empresas()
    ign = {s.strip().upper() for s in CFG_ATUAL().get("SITUACOES_IGNORADAS", "HOMOLOGADO").split(",")}
    linhas, sits = "", set()
    for x in emps:
        if emp and x["id"] != emp:
            continue
        cls_emp = {}
        for r in classif(x["id"]):
            cls_emp.setdefault((r[0], r[1]), []).append(r)
        for p in sorted(x["procs"], key=lambda p: para_data(p[3] or ""), reverse=True):
            sits.add(p[2])
            if filtro == "andamento" and (p[2] or "").upper() in ign:
                continue
            if filtro and filtro != "andamento" and p[2] != filtro:
                continue
            cls = "parado" if (p[2] or "").upper() in ign else ""
            linhas += (f'<tr><td>{e(x["nome"])}<div><span class="tag parado">{e(x["plat"])}</span></div></td><td><b>{e(p[0])}</b></td><td>{e(p[1])}</td>'
                       f'<td><span class="tag {cls}">{e(p[2])}</span></td><td class="ocultar">{e(p[3])}</td>'
                       f'<td class="ocultar">{e(p[4])}</td>'
                       f'<td>{resumo_classif(cls_emp.get((p[0], p[1]), []))} '
                       f'{"<a class=sub href=/classificacao?" + urllib.parse.urlencode({"empresa": x["id"], "processo": p[0], "promotor": p[1]}) + ">ver</a>" if cls_emp.get((p[0], p[1])) else ""}</td></tr>')
    ops = "".join(f'<option value="{e(x["id"])}" {"selected" if x["id"] == emp else ""}>{e(x["nome"])} · {e(x["plat"])}</option>'
                  for x in emps)
    sops = "".join(f'<option {"selected" if s == filtro else ""}>{e(s)}</option>' for s in sorted(sits) if s)
    corpo = f"""<h2>Processos</h2><form class="linha" method="get">
<select name="empresa"><option value="">Todas as empresas</option>{ops}</select>
<select name="situacao"><option value="">Todas as situações</option>
<option value="andamento" {"selected" if filtro == "andamento" else ""}>Só em andamento</option>{sops}</select>
<button>Filtrar</button></form>
<table><tr><th>Empresa</th><th>Processo</th><th>Órgão</th><th>Situação</th><th class="ocultar">Disputa</th><th class="ocultar">Atualizado na plataforma</th><th>Classificação</th></tr>
{linhas or '<tr><td colspan="7">Nenhum processo.</td></tr>'}</table>"""
    return pagina("Processos", corpo, "/processos")


def pg_empresas(aviso="", erro=False):
    linhas = ""
    for x in empresas():
        cls, txt = tag_status(x)
        pausar = (botao(f"/empresas/{x['id']}/pausar", "Pausar", "sec") if x["ativo"]
                  else botao(f"/empresas/{x['id']}/retomar", "Retomar"))
        linhas += f"""<tr><td><b>{e(x['nome'])}</b> <span class="tag parado">{e(x['plat'])}</span><div class="sub">{e(x['cfg'].get('BLL_EMAIL', ''))}</div></td>
<td><span class="tag {cls}">{e(txt)}</span></td><td class="ocultar">{e(x['cfg'].get('ENVIAR_PARA', 'geral'))}</td>
<td><div class="acoes" style="margin:0"><a class="btn sec" href="/empresas/{e(x['id'])}">Editar</a>{pausar}
{botao(f"/empresas/{x['id']}/testar", "Testar alerta", "sec")}<a class="btn sec" href="/empresas/{e(x['id'])}/log">Log</a></div></td></tr>"""
    corpo = f"""<h2>Empresas</h2><div class="acoes" style="margin-bottom:12px"><a class="btn" href="/empresas/nova">+ Cadastrar empresa</a></div>
<table><tr><th>Empresa</th><th>Situação</th><th class="ocultar">Alertas</th><th>Ações</th></tr>
{linhas or '<tr><td colspan="4">Nenhuma empresa cadastrada.</td></tr>'}</table>"""
    return pagina("Empresas", corpo, "/empresas", aviso, erro)


def form_empresa(eid=None):
    cfg = ler_env(EMPRESAS / f"{eid}.env") if eid else {}
    g = lambda k, d="": e(cfg.get(k, d))  # noqa: E731
    modo = cfg.get("ENVIAR_PARA", "geral")
    plat = cfg.get("PLATAFORMA", "bll").lower()
    sel = lambda v: "selected" if modo == v else ""  # noqa: E731
    acao = f"/empresas/{eid}" if eid else "/empresas/nova"
    remover = (f'<h3>Zona de perigo</h3>' +
               botao(f"/empresas/{eid}/remover", "Remover empresa e histórico", "perigo",
                     "Remover esta empresa e todo o histórico dela?")) if eid else ""
    corpo = f"""<h2>{"Editar empresa" if eid else "Cadastrar empresa"}</h2><div class="form">
<form method="post" action="{acao}"><input type="hidden" name="csrf" value="{CSRF}">
<label>Nome da empresa <small>(aparece nos alertas)</small></label><input class="campo" name="nome" value="{g('EMPRESA_NOME')}" required>
<label>Plataforma</label><select class="campo" name="plataforma" id="plat" onchange="trocaPlat()">
<option value="bll" {"selected" if plat == "bll" else ""}>BLL Compras</option>
<option value="licitanet" {"selected" if plat == "licitanet" else ""}>Licitanet</option>
<option value="pcp" {"selected" if plat == "pcp" else ""}>Portal de Compras Públicas</option>
<option value="bnc" {"selected" if plat == "bnc" else ""}>BNC Compras</option></select>
<label>Usuário / e-mail de login</label><input class="campo" name="email" value="{g('BLL_EMAIL')}" required>
<label>Senha <small>{"(deixe em branco para manter)" if eid else ""}<span class="so-bll"> BLL/BNC: 6 números</span></small></label>
<input class="campo" type="password" name="senha" {"" if eid else "required"} autocomplete="new-password">
<div class="so-bll"><label>Perfil a escolher após o login <small>(parte do nome, ex.: OPERADOR EMPRESA X)</small></label>
<input class="campo" name="perfil" value="{g('BLL_PERFIL', 'OPERADOR')}"></div>
<div class="so-ln"><label>CNPJ da empresa <small>(o mesmo login pode ter várias empresas; escolhemos esta)</small></label>
<input class="campo" name="cnpj" value="{g('EMPRESA_CNPJ')}" placeholder="00.000.000/0000-00">
<label>Tipo de funcionário <small>(opcional; vazio = primeira opção, ex.: Representante Legal)</small></label>
<input class="campo" name="funcao" value="{g('LICITANET_FUNCAO')}"></div>
<h3>Alertas</h3>
<label>Para onde vão os alertas</label><select class="campo" name="enviar_para">
<option value="geral" {sel('geral')}>Telegram geral (o mesmo de todas)</option>
<option value="proprio" {sel('proprio')}>Telegram próprio desta empresa</option>
<option value="ambos" {sel('ambos')}>Os dois</option></select>
<label>Chat do Telegram próprio <small>(chat_id; use “Descobrir conversas” em Configurações)</small></label>
<input class="campo" name="tg_chat" value="{g('EMPRESA_TELEGRAM_CHAT_ID')}">
<label>Token de bot próprio <small>(opcional; em branco = mesmo bot geral; {"já configurado, " if cfg.get("EMPRESA_TELEGRAM_TOKEN") else ""}deixe vazio para manter)</small></label>
<input class="campo" type="password" name="tg_token" autocomplete="off">
<label>Tópico ntfy próprio <small>(opcional, alarme no celular)</small></label>
<input class="campo" name="ntfy" value="{g('EMPRESA_NTFY_TOPICO')}">
<div class="acoes"><button>Salvar</button><a class="btn sec" href="/empresas">Cancelar</a></div></form>{remover}</div>
<script>function trocaPlat(){{const v=document.getElementById('plat').value,ln=v==='licitanet',bll=(v==='bll'||v==='bnc');
document.querySelectorAll('.so-bll').forEach(x=>x.style.display=bll?'':'none');
document.querySelectorAll('.so-ln').forEach(x=>x.style.display=ln?'':'none');}}trocaPlat();</script>"""
    return pagina("Empresa", corpo, "/empresas")


def pg_config(aviso=""):
    c = CFG_ATUAL()
    g = lambda k, d="": e(c.get(k, d))  # noqa: E731
    corpo = f"""<h2>Configurações gerais</h2><div class="form">
<form method="post" action="/config"><input type="hidden" name="csrf" value="{CSRF}">
<h3>Telegram geral</h3>
<label>Token do bot <small>({"configurado; " if c.get("TELEGRAM_TOKEN") else ""}deixe vazio para manter)</small></label>
<input class="campo" type="password" name="TELEGRAM_TOKEN" autocomplete="off">
<label>Chat ID</label><input class="campo" name="TELEGRAM_CHAT_ID" value="{g('TELEGRAM_CHAT_ID')}">
<h3>Alarme e ligação</h3>
<label style="font-weight:400;display:flex;gap:6px;align-items:center"><input type="checkbox" name="ALARME_SO_URGENTE" value="1" {"checked" if c.get("ALARME_SO_URGENTE", "1") != "0" else ""}>
<b>Alarme forte só para mensagens urgentes</b> <small>(as demais chegam como notificação normal)</small></label>
<label style="font-weight:400;display:flex;gap:6px;align-items:center"><input type="checkbox" name="CHAT_URGENTE" value="1" {"checked" if c.get("CHAT_URGENTE", "1") != "0" else ""}>
<b>Toda mensagem do chat (pregoeiro/participantes) é urgente</b> <small>(eventos automáticos do sistema continuam normais)</small></label>
<label>Palavras que tornam a mensagem URGENTE <small>(uma por linha: trecho | motivo · sem acento · vale início de palavra)</small></label>
<textarea class="campo" name="URGENTE_PALAVRAS" rows="10" style="font-family:ui-monospace,Consolas,monospace;font-size:12.5px">{e(urgencia.texto_atual())}</textarea>
<label>Tópico ntfy geral</label><input class="campo" name="NTFY_TOPICO" value="{g('NTFY_TOPICO')}">
<label>Ligação pelo Telegram (CallMeBot) <small>@usuario, ou vazio para desligar</small></label>
<input class="campo" name="CHAMADA_USUARIO" value="{g('CHAMADA_USUARIO')}">
<h3>Inteligência artificial (análise de editais)</h3>
<p class="sub">Chave gratuita do Google Gemini: entre em <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>, faça login com uma conta Google e clique em “Create API key”.</p>
<label>Chave do Gemini <small>({"configurada; " if c.get("GEMINI_API_KEY") else ""}deixe vazio para manter)</small></label>
<input class="campo" type="password" name="GEMINI_API_KEY" autocomplete="off">
<label>Modelo <small>(vazio = gemini-2.5-flash)</small></label><input class="campo" name="GEMINI_MODELO" value="{g('GEMINI_MODELO')}" placeholder="gemini-2.5-flash">
<h3>Ritmo</h3>
<label>Avisar se uma empresa ficar sem verificação por mais de (minutos)</label>
<input class="campo" type="number" min="5" name="VIGIA_MINUTOS" value="{g('VIGIA_MINUTOS', '15')}">
<label>Intervalo entre verificações (minutos)</label><input class="campo" type="number" step="0.5" min="1" name="INTERVALO_MINUTOS" value="{g('INTERVALO_MINUTOS', '2')}">
<label>Navegadores simultâneos <small>(≈ 1 por GB de memória livre)</small></label><input class="campo" type="number" min="1" max="12" name="MAX_NAVEGADORES" value="{g('MAX_NAVEGADORES', '3')}">
<label>Horário de funcionamento <small>(0 a 24)</small></label>
<input type="number" min="0" max="23" name="HORARIO_INICIO" value="{g('HORARIO_INICIO', '0')}"> até
<input type="number" min="1" max="24" name="HORARIO_FIM" value="{g('HORARIO_FIM', '24')}">
<label>Buscas salvas: verificar a cada (minutos)</label><input class="campo" type="number" min="10" name="BUSCA_INTERVALO_MINUTOS" value="{g('BUSCA_INTERVALO_MINUTOS', '30')}">
<label>Situações ignoradas <small>(separadas por vírgula)</small></label>
<input class="campo" name="SITUACOES_IGNORADAS" value="{g('SITUACOES_IGNORADAS', 'HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO')}">
<div class="acoes"><button>Salvar e reiniciar monitores</button></div></form></div>
<h2 style="margin-top:22px">Backup</h2><div class="form">
<p class="sub">Todo dia às {g('BACKUP_HORA', '3')}h o painel guarda uma cópia no servidor (últimos 7 dias) e manda outra,
<b>criptografada</b>, para o Telegram geral (fica fora do servidor). Inclui cadastros, senhas das plataformas,
mensagens, processos, classificações, buscas e análises.</p>
<form method="post" action="/config/backup"><input type="hidden" name="csrf" value="{CSRF}">
<label>Senha do backup <small>({"definida; " if c.get("BACKUP_SENHA") else "<b style='color:var(--erro)'>não definida</b> – sem ela a cópia não vai para o Telegram; "}deixe vazio para manter. Guarde-a: sem ela não dá para abrir o backup)</small></label>
<input class="campo" type="password" name="BACKUP_SENHA" autocomplete="new-password" minlength="8">
<label>Horário do backup diário (0–23)</label><input class="campo" type="number" min="0" max="23" name="BACKUP_HORA" value="{g('BACKUP_HORA', '3')}">
<div class="acoes"><button name="acao" value="salvar">Salvar</button><button class="sec" name="acao" value="agora">Salvar e fazer backup agora</button></div></form>
<h3>Cópias no servidor</h3>
<table><tr><th>Arquivo</th><th>Tamanho</th><th>Data</th></tr>{"".join(f'<tr><td><a href="/backup/{e(n)}">{e(n)}</a></td><td>{t / 1024 / 1024:.1f} MB</td><td>{d:%d/%m/%Y %H:%M}</td></tr>' for n, t, d in backup.listar()) or '<tr><td colspan="3">Nenhum ainda.</td></tr>'}</table>
<p class="sub">Para abrir a cópia do Telegram: <code>openssl enc -d -aes-256-cbc -pbkdf2 -in ARQUIVO.enc -out backup.tar.gz</code> (pede a senha).</p></div>
<h2 style="margin-top:22px">Descobrir conversas do Telegram</h2><div class="form">
<p class="sub">Mande uma mensagem ao bot (ou num grupo onde ele está) e clique abaixo para ver o chat_id.</p>
<form method="post" action="/telegram"><input type="hidden" name="csrf" value="{CSRF}">
<label>Token <small>(vazio = bot geral)</small></label><input class="campo" type="password" name="token" autocomplete="off">
<div class="acoes"><button>Descobrir conversas</button></div></form></div>"""
    return pagina("Configurações", corpo, "/config", aviso)


def filtros_busca(qs):
    """qs = parse_qs completo (listas)."""
    um = lambda k: (qs.get(k) or [""])[0].strip()  # noqa: E731
    ufs = [u for u in qs.get("uf", []) if u in buscas.UFS]
    return {"q": um("q"), "excluir": um("excluir"), "ufs": ufs, "municipio": um("municipio"),
            "modalidade": um("modalidade"), "status": um("status") if "status" in qs else "recebendo_proposta",
            "vmin": re.sub(r"[^\d.]", "", um("vmin").replace(".", "").replace(",", ".")),
            "vmax": re.sub(r"[^\d.]", "", um("vmax").replace(".", "").replace(",", ".")),
            "itens": um("itens"), "nome": um("nome")}


def form_busca(f):
    sel = lambda a, b: "selected" if str(a) == str(b) else ""  # noqa: E731
    ufs = "".join(f'<label class="uf"><input type="checkbox" name="uf" value="{u}" {"checked" if u in f["ufs"] else ""}>{u}</label>'
                  for u in buscas.UFS)
    mods = "".join(f'<option value="{v}" {sel(v, f["modalidade"])}>{n}</option>' for v, n in buscas.MODALIDADES)
    sits = "".join(f'<option value="{v}" {sel(v, f["status"])}>{n}</option>' for v, n in buscas.SITUACOES)
    return f"""<form method="get" action="/buscar" class="form" style="max-width:none">
<div class="bgrid">
<div><label>Palavras-chave <small>(vírgula = OU: <i>papel a4, toner</i>)</small></label>
<input class="campo" name="q" value="{e(f['q'])}" placeholder="ex.: material de limpeza, café"></div>
<div><label>Excluir se tiver <small>(vírgula separa)</small></label>
<input class="campo" name="excluir" value="{e(f['excluir'])}" placeholder="ex.: serviço, locação"></div>
<div><label>Município <small>(vírgula = OU)</small></label>
<input class="campo" name="municipio" value="{e(f['municipio'])}" placeholder="ex.: Campinas, Sorocaba"></div>
<div><label>Modalidade</label><select class="campo" name="modalidade"><option value="">Todas</option>{mods}</select></div>
<div><label>Situação</label><select class="campo" name="status">{sits}</select></div>
<div><label>Valor estimado <small>(R$)</small></label>
<input name="vmin" value="{e(f['vmin'])}" placeholder="mínimo" style="width:48%"> <input name="vmax" value="{e(f['vmax'])}" placeholder="máximo" style="width:48%"></div>
</div>
<details {"open" if f["ufs"] else ""}><summary style="cursor:pointer;margin-top:12px;font-weight:600;font-size:13px">Estados (UF) {("– " + ", ".join(f["ufs"])) if f["ufs"] else "– todos"}</summary>
<div class="ufs">{ufs}</div></details>
<label style="font-weight:400;display:flex;gap:6px;align-items:center"><input type="checkbox" name="itens" value="1" {"checked" if f["itens"] else ""}>
Procurar as palavras-chave também dentro dos <b>itens</b> <small>(mais lento)</small></label>
<div class="acoes"><button>Buscar</button><a class="btn sec" href="/buscar">Limpar</a></div></form>"""


def pg_buscar(qs, aviso=""):
    f = filtros_busca(qs)
    pag = max(1, int(re.sub(r"\D", "", (qs.get("pagina") or ["1"])[0]) or 1))
    corpo = "<h2>Buscar licitações</h2>" + form_busca(f)
    feita = any(k in qs for k in ("q", "uf", "municipio", "modalidade", "status"))
    if feita:
        itens, total, erro = buscas.buscar(f, pag)
        if erro:
            corpo += f'<div class="msg erro">{e(erro)}</div>'
        cards = ""
        for it in itens:
            achados = "".join(f'<li><b>Item {e(str(x.get("numeroItem")))}</b>: {e((x.get("descricao") or "")[:180])}'
                              f' <span class="sub">({e(str(x.get("quantidade") or ""))} {e(x.get("unidadeMedida") or "")})</span></li>'
                              for x in it.get("_itens_achados", []))
            cnpj, ano, seq = buscas.ref(it)
            origem = it.get("link_sistema_origem") or ""
            cards += f"""<div class="card"><div class="sub">{e(it.get('modalidade_licitacao_nome') or '')} · {e(it.get('title') or '')}
· publicada {e(buscas.data_br(it.get('data_publicacao_pncp')))}</div>
<div class="nome" style="margin:4px 0">{e(it.get('orgao_nome') or '')}</div>
<div class="sub"><b>{e(it.get('municipio_nome') or '')}/{e(it.get('uf') or '')}</b> · {e(it.get('unidade_nome') or '')}</div>
<p style="margin:8px 0;font-size:14px">{e(it.get('description') or '')}</p>
{f'<ul class="sub" style="margin:4px 0 8px">{achados}</ul>' if achados else ''}
<div class="sub">Valor estimado: <b>{e(buscas.moeda(it.get('valor_total_estimado')))}</b>
{f" · Propostas até: <b>{e(buscas.data_br(it.get('data_fim_vigencia')))}</b>" if it.get('data_fim_vigencia') else ''}</div>
<div class="acoes"><a class="btn" target="_blank" rel="noopener" href="{e(buscas.link_pncp(it))}">Ver edital (PNCP)</a>
<a class="btn sec" href="/buscar/itens?cnpj={e(str(cnpj))}&ano={e(str(ano))}&seq={e(str(seq))}">Itens</a>
{botao_analisar(cnpj, ano, seq, f"{it.get('orgao_nome') or ''} – {it.get('title') or ''} – {it.get('municipio_nome') or ''}/{it.get('uf') or ''}")}
{f'<a class="btn sec" target="_blank" rel="noopener" href="{e(origem)}">Sistema de origem</a>' if origem.startswith("http") else ''}</div></div>"""
        base = urllib.parse.urlencode([(k, v) for k, vs in qs.items() if k != "pagina" for v in vs])
        nav = '<div class="acoes">'
        if pag > 1:
            nav += f'<a class="btn sec" href="/buscar?{base}&pagina={pag - 1}">← Anterior</a>'
        nav += f'<span class="sub" style="align-self:center">Página {pag}</span>'
        if total > pag * 50:
            nav += f'<a class="btn sec" href="/buscar?{base}&pagina={pag + 1}">Próxima →</a>'
        nav += "</div>"
        salvar = f"""<form method="post" action="/buscas/salvar" class="linha" style="margin-top:10px">
<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="filtros" value="{e(json.dumps(f, ensure_ascii=False))}">
<input name="nome" placeholder="Nome da busca (ex.: Limpeza SP)" value="{e(f['q'][:40])}">
<button>🔔 Salvar busca e avisar novas no Telegram</button></form>"""
        corpo += (f'<h3>{len(itens)} resultado(s) nesta página'
                  f'{" (de cerca de " + str(total) + " no PNCP)" if total else ""}</h3>' + salvar +
                  f'<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))">'
                  f'{cards or "<p>Nada encontrado com esses filtros.</p>"}</div>' + nav)
    lista = buscas.salvas()
    if lista:
        linhas = ""
        for b in lista:
            resumo = " · ".join(x for x in [b.get("q") and f"“{b['q']}”", ", ".join(b.get("ufs") or []),
                                             b.get("municipio"), dict(buscas.MODALIDADES).get(b.get("modalidade", ""), ""),
                                             b.get("excluir") and f"sem: {b['excluir']}"] if x)
            linhas += (f'<tr><td><b>{e(b["nome"])}</b><div class="sub">{e(resumo or "todas")}</div></td>'
                       f'<td>{"🔔 Avisando" if b.get("alertar") else "⏸ Pausada"}</td>'
                       f'<td class="ocultar">{e(b.get("ultima") or "—")}</td><td>{b.get("novas_total", 0)}</td>'
                       f'<td><div class="acoes" style="margin:0">'
                       f'<a class="btn sec" href="/buscar?{urllib.parse.urlencode(busca_para_qs(b), doseq=True)}">Abrir</a>'
                       f'{botao("/buscas/" + b["id"] + "/rodar", "Verificar agora", "sec")}'
                       f'{botao("/buscas/" + b["id"] + ("/pausar" if b.get("alertar") else "/ligar"), "Pausar" if b.get("alertar") else "Ativar", "sec")}'
                       f'{botao("/buscas/" + b["id"] + "/remover", "Remover", "perigo", "Remover esta busca salva?")}</div></td></tr>')
        corpo += (f'<h2 style="margin-top:26px">Buscas salvas</h2><p class="sub">Verificadas a cada '
                  f'{int(minutos_busca())} min (só licitações abertas). Novas vão para o Telegram geral.</p>'
                  f'<table><tr><th>Busca</th><th>Alerta</th><th class="ocultar">Última verificação</th><th>Novas</th><th></th></tr>{linhas}</table>')
    corpo += '<p class="sub" style="margin-top:18px">Fonte: PNCP – Portal Nacional de Contratações Públicas (todas as plataformas e órgãos que seguem a Lei 14.133).</p>'
    return pagina("Buscar", corpo, "/buscar", aviso)


def busca_para_qs(b):
    return {"q": b.get("q", ""), "excluir": b.get("excluir", ""), "uf": b.get("ufs", []), "municipio": b.get("municipio", ""),
            "modalidade": b.get("modalidade", ""), "status": "recebendo_proposta", "vmin": b.get("vmin", ""),
            "vmax": b.get("vmax", ""), **({"itens": "1"} if b.get("itens") else {})}


def pg_itens(q):
    cnpj, ano, seq = (re.sub(r"\D", "", q.get(k, "")) for k in ("cnpj", "ano", "seq"))
    its = buscas.listar_itens(cnpj, ano, seq)
    linhas = "".join(f'<tr><td>{e(str(x.get("numeroItem")))}</td><td class="txt">{e(x.get("descricao") or "")}</td>'
                     f'<td>{e(str(x.get("quantidade") or ""))} {e(x.get("unidadeMedida") or "")}</td>'
                     f'<td>{e(buscas.moeda(x.get("valorUnitarioEstimado")))}</td>'
                     f'<td class="ocultar">{e(x.get("situacaoCompraItemNome") or "")}</td></tr>' for x in its)
    corpo = (f'<h2>Itens da licitação</h2><p><a class="btn sec" href="javascript:history.back()">← Voltar</a> '
             f'<a class="btn" target="_blank" rel="noopener" href="https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}">Ver edital (PNCP)</a></p>'
             f'<div style="margin-bottom:12px">{botao_analisar(cnpj, ano, seq, "")}</div>'
             f'<table><tr><th>Nº</th><th>Descrição</th><th>Qtd.</th><th>Valor unit. estimado</th><th class="ocultar">Situação</th></tr>'
             f'{linhas or "<tr><td colspan=5>Itens não disponíveis.</td></tr>"}</table>')
    return pagina("Itens", corpo, "/buscar")


def botao_analisar(cnpj, ano, seq, titulo):
    ch = analise.chave_pncp(cnpj, ano, seq)
    feito = analise.ler(ch)
    if feito and feito.get("status") in ("pronto", "analisando"):
        return f'<a class="btn" href="/analise/{ch}">🤖 Ver análise</a>'
    return (f'<form method="post" action="/analise/pncp" style="display:inline"><input type="hidden" name="csrf" value="{CSRF}">'
            f'<input type="hidden" name="cnpj" value="{e(str(cnpj))}"><input type="hidden" name="ano" value="{e(str(ano))}">'
            f'<input type="hidden" name="seq" value="{e(str(seq))}"><input type="hidden" name="titulo" value="{e(titulo)}">'
            f'<button>🤖 Analisar edital (IA)</button></form>')


def pg_analise(ch):
    d = analise.ler(ch)
    if not d:
        return pagina("Análise", "<h2>Análise não encontrada</h2>", "/editais")
    o = d.get("origem", {})
    edital = (f'<a class="btn sec" target="_blank" rel="noopener" href="https://pncp.gov.br/app/editais/{e(o.get("cnpj", ""))}/'
              f'{e(o.get("ano", ""))}/{e(o.get("seq", ""))}">Ver edital (PNCP)</a>') if o.get("tipo") == "pncp" else ""
    topo = f'<h2>🤖 Análise do edital</h2><p class="sub">{e(d.get("titulo") or "")}</p>'
    if d.get("status") == "analisando":
        corpo = topo + (f'<div class="msg">Analisando desde {e(d.get("inicio", ""))}… Baixando o edital e lendo com a IA. '
                        'Costuma levar de 30 segundos a 3 minutos. Esta página atualiza sozinha.</div>')
        return pagina("Analisando", corpo, "/editais").replace("<head>", '<head><meta http-equiv="refresh" content="8">', 1)
    if d.get("status") == "erro":
        corpo = topo + f'<div class="msg erro">{e(d.get("erro", ""))}</div><div class="acoes">' + \
            botao(f"/analise/{ch}/refazer", "Tentar de novo") + edital + '<a class="btn sec" href="/config">Configurações</a></div>'
        return pagina("Análise", corpo, "/editais")
    arqs = ", ".join(d.get("arquivos") or [])
    corpo = (topo + '<div class="acoes" style="margin-bottom:12px">' + edital +
             botao(f"/analise/{ch}/telegram", "📨 Enviar no Telegram", "sec") +
             '<button class="sec" onclick="window.print()">Imprimir / PDF</button></div>'
             + (f'<div class="msg">Pedido adicional usado: {e(d["extra"])}</div>' if d.get("extra") else "") +
             f'<div class="analise">{analise.md_html(d.get("md", ""))}</div>'
             f'<p class="sub">Gerado em {e(d.get("fim", ""))} por {e(d.get("modelo", ""))} · Arquivos lidos: {e(arqs) or "—"}<br>'
             '⚠️ A IA pode errar ou deixar algo de fora. Confira sempre no edital antes de enviar documentos e proposta.</p>'
             + form_refazer(ch, d))
    return pagina("Análise", corpo, "/editais")


def form_refazer(ch, d):
    return (f'<details class="form" style="margin-top:16px"><summary style="cursor:pointer;font-weight:600">'
            f'🔁 Analisar de novo / fazer uma pergunta sobre este edital</summary>'
            f'<form method="post" action="/analise/{ch}/refazer"><input type="hidden" name="csrf" value="{CSRF}">'
            f'<label>Pedido adicional <small>(opcional – ex.: “foque no lote 3”, “a empresa não tem balanço de 2025, isso impede?”, '
            f'“liste só os documentos”)</small></label><textarea class="campo" name="extra" rows="3" style="max-width:100%">'
            f'{e(d.get("extra", ""))}</textarea>'
            f'<p class="sub">Usa as instruções atuais da aba Editais (IA) + este pedido. Gasta uma consulta da IA.</p>'
            f'<div class="acoes"><button>Analisar de novo</button></div></form></details>')


def pg_editais(aviso="", erro=False):
    tem_chave = bool(CFG_ATUAL().get("GEMINI_API_KEY"))
    alerta = "" if tem_chave else ('<div class="msg erro">Falta a chave gratuita do Gemini. '
                                   'Cadastre em <a href="/config">Configurações → Inteligência artificial</a>.</div>')
    linhas = "".join(
        f'<tr><td><a href="/analise/{e(d["chave"])}"><b>{e(d.get("titulo") or d["chave"])}</b></a></td>'
        f'<td>{ {"pronto": "✅ Pronta", "analisando": "⏳ Analisando", "erro": "❌ Erro"}.get(d.get("status"), "") }</td>'
        f'<td class="ocultar">{e(d.get("fim") or d.get("inicio") or "")}</td></tr>' for d in analise.listar())
    corpo = f"""<h2>Editais (IA)</h2>{alerta}
<div class="form"><h3 style="margin-top:0">Analisar um edital que você tem em mãos</h3>
<p class="sub">Para editais de qualquer plataforma (BLL, BNC, Licitanet...). Envie o PDF do edital e, se quiser, o termo de referência (até 18 MB no total).
Para licitações do PNCP, use o botão “🤖 Analisar edital (IA)” na aba Buscar.</p>
<form method="post" action="/analise/upload" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{CSRF}">
<label>Nome para identificar</label><input class="campo" name="titulo" placeholder="ex.: Pregão 45/2026 – Prefeitura de X">
<label>Arquivos (PDF, DOCX ou ZIP)</label><input type="file" name="arquivo" multiple required accept=".pdf,.docx,.zip">
<label>Pedido adicional <small>(opcional – só para esta análise)</small></label>
<textarea class="campo" name="extra" rows="2" placeholder="ex.: foque nos itens de material de limpeza"></textarea>
<div class="acoes"><button>🤖 Analisar</button></div></form></div>
<h2 style="margin-top:22px">Como a IA deve analisar</h2>
<div class="form" style="max-width:900px"><form method="post" action="/editais/ia"><input type="hidden" name="csrf" value="{CSRF}">
<label>Sobre a empresa <small>(opcional – o que vende, onde atende, certidões/atestados que já tem. A IA usa para dizer o que falta)</small></label>
<textarea class="campo" name="empresa" rows="5" style="max-width:100%" placeholder="ex.: Distribuidora de material de limpeza e descartáveis, ME, atende SP e MG. Temos atestados de fornecimento de limpeza para 3 prefeituras. Não temos balanço de 2025 ainda.">{e(analise.perfil_empresa())}</textarea>
<label>Instruções para a IA <small>({"personalizadas" if analise.ARQ_INSTRUCOES.exists() and analise.ARQ_INSTRUCOES.read_text(encoding="utf-8").strip() else "padrão"} – edite à vontade: tire seções, acrescente perguntas, mude o formato)</small></label>
<textarea class="campo" name="instrucoes" rows="22" style="max-width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12.5px">{e(analise.instrucoes())}</textarea>
<p class="sub">Dicas: use “## Título” para seções e “- [ ]” para itens de checklist (viram caixinhas). Vale para as próximas análises.</p>
<div class="acoes"><button name="acao" value="salvar">Salvar instruções</button>
<button class="sec" name="acao" value="padrao" onclick="return confirm('Voltar as instruções para o padrão? (o texto sobre a empresa é mantido)')">Restaurar padrão</button></div></form></div>
<h2 style="margin-top:22px">Análises feitas</h2>
<table><tr><th>Edital</th><th>Situação</th><th class="ocultar">Quando</th></tr>{linhas or '<tr><td colspan="3">Nenhuma ainda.</td></tr>'}</table>
<p class="sub">IA gratuita: Google Gemini (faixa gratuita tem limite diário de consultas). Na faixa gratuita o Google pode usar o conteúdo
enviado para melhorar seus produtos; editais são públicos, mas evite enviar documentos internos da empresa.</p>"""
    return pagina("Editais (IA)", corpo, "/editais", aviso, erro)


def ler_multipart(handler, tam):
    """Retorna (campos, arquivos[(nome, bytes)]) de um formulário multipart."""
    from email.parser import BytesParser
    from email.policy import default
    bruto = handler.rfile.read(tam)
    msg = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + handler.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + bruto)
    campos, arqs = {}, []
    for parte in msg.iter_parts():
        nome = parte.get_param("name", header="content-disposition")
        arq = parte.get_filename()
        dados = parte.get_payload(decode=True) or b""
        if arq:
            if dados:
                arqs.append((Path(arq).name, dados))
        elif nome:
            campos[nome] = dados.decode("utf-8", "ignore")
    return campos, arqs


# ------------------------------------------------------------------ vigia (aviso de monitor parado)
ARQ_VIGIA = PASTA / "vigia_estado.json"


def ntfy_geral(titulo, texto, prioridade="4"):
    topico = CFG_ATUAL().get("NTFY_TOPICO", "").strip()
    if not topico:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topico}", texto.encode("utf-8"), method="POST",
                                     headers={"Title": titulo.encode("utf-8").decode("latin-1", "ignore"),
                                              "Priority": prioridade, "Tags": "warning"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:  # noqa: BLE001
        print("ntfy (vigia):", ex, flush=True)


def problema_monitor(x, limite):
    """Retorna a descrição do problema da empresa, ou '' se estiver tudo bem."""
    if not x["ativo"]:
        habilitado = sh("systemctl", "is-enabled", f"monitor-bll@{x['id']}") == "enabled"
        return "o serviço do monitor parou (caiu e não voltou)" if habilitado else ""  # pausado de propósito: ignora
    st = x["status"] or ""
    if st.startswith("Fora do horário"):
        return ""
    ult = para_data(x["ultima"]) if x["ultima"] else datetime.min
    minutos = (datetime.now() - ult).total_seconds() / 60 if ult != datetime.min else None
    if minutos is not None and minutos > limite:
        return f"sem verificar há {int(minutos)} min"
    if "Erro" in st or "expirada" in st or "captcha" in st.lower():
        return f"com problema: {st[:150]}"
    return ""


def vigiar():
    """A cada 3 min confere se cada empresa ativa está sendo verificada. Avisa uma vez e avisa quando voltar."""
    import time as _t
    _t.sleep(180)
    while True:
        try:
            limite = float(CFG_ATUAL().get("VIGIA_MINUTOS", "15") or 15)
            try:
                memo = json.loads(ARQ_VIGIA.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                memo = {}
            agora = datetime.now().timestamp()
            for x in empresas():
                prob = problema_monitor(x, limite)
                m = memo.get(x["id"], {})
                nome = f'{x["nome"]} ({x["plat"]})'
                if prob:
                    m.setdefault("desde", agora)
                    # erro de status só conta se persistir pelo tempo-limite (evita falso alarme de 1 ciclo)
                    if not m.get("avisado") and (agora - m["desde"] >= limite * 60 or "sem verificar" in prob
                                               or ("parou" in prob and agora - m["desde"] >= 300)):
                        telegram_geral(f"⚠️ <b>Monitor parado – {html.escape(nome)}</b>\n{html.escape(prob)}.\n"
                                       f"Última verificação: {html.escape(x['ultima'] or '—')}\n"
                                       "Veja o Log no painel (aba Empresas) ou use Reiniciar.")
                        ntfy_geral(f"Monitor parado: {nome}", prob)
                        m["avisado"] = True
                elif m:
                    if m.get("avisado"):
                        telegram_geral(f"✅ <b>Monitor voltou – {html.escape(nome)}</b>\nVerificação normal às {html.escape(x['ultima'])}.")
                    m = {}
                if m:
                    memo[x["id"]] = m
                else:
                    memo.pop(x["id"], None)
            ARQ_VIGIA.write_text(json.dumps(memo), encoding="utf-8")
        except Exception as ex:  # noqa: BLE001
            print("Erro no vigia:", ex, flush=True)
        _t.sleep(180)


def minutos_busca():
    try:
        return float(CFG_ATUAL().get("BUSCA_INTERVALO_MINUTOS", "30") or 30)
    except ValueError:
        return 30.0


def telegram_geral(texto):
    c = CFG_ATUAL()
    tok, chat = c.get("TELEGRAM_TOKEN"), c.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    dados = urllib.parse.urlencode({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", dados, timeout=20).read()
    except Exception as ex:  # noqa: BLE001
        print("Telegram (buscas):", ex, flush=True)


# ------------------------------------------------------------------ ações
def salvar_empresa(f, eid=None):
    nome = f.get("nome", "").strip()
    if not nome:
        return "Informe o nome."
    novo = eid is None
    if novo:
        eid = gerar_id(nome)
        n = 2
        while (EMPRESAS / f"{eid}.env").exists():
            eid = f"{gerar_id(nome)[:27]}-{n}"
            n += 1
    senha = f.get("senha", "").strip()
    plat = f.get("plataforma", "bll")
    if plat not in ("bll", "licitanet", "pcp", "bnc"):
        plat = "bll"
    if plat in ("bll", "bnc") and senha and not re.fullmatch(r"\d{6}", senha):
        return "A senha da BLL/BNC deve ter 6 números."
    if plat == "licitanet" and not re.sub(r"\D", "", f.get("cnpj", "")):
        return "Informe o CNPJ da empresa na Licitanet."
    if novo and not senha:
        return "Informe a senha da BLL."
    modo = f.get("enviar_para", "geral")
    if modo in ("proprio", "ambos") and not f.get("tg_chat", "").strip():
        return "Para Telegram próprio, informe o chat_id da conversa."
    EMPRESAS.mkdir(exist_ok=True)
    os.chmod(EMPRESAS, 0o700)
    arq = EMPRESAS / f"{eid}.env"
    antigo = ler_env(arq)
    v = {"EMPRESA_NOME": nome, "BLL_EMAIL": f.get("email", "").strip(),
         "BLL_PERFIL": f.get("perfil", "").strip() or "OPERADOR", "ENVIAR_PARA": modo,
         "EMPRESA_TELEGRAM_CHAT_ID": f.get("tg_chat", "").strip(), "EMPRESA_NTFY_TOPICO": f.get("ntfy", "").strip(),
         "PLATAFORMA": plat, "EMPRESA_CNPJ": f.get("cnpj", "").strip(), "LICITANET_FUNCAO": f.get("funcao", "").strip()}
    if senha:
        v["BLL_SENHA"] = senha
    if f.get("tg_token", "").strip():
        v["EMPRESA_TELEGRAM_TOKEN"] = f["tg_token"].strip()
    gravar_env(arq, v)
    login_mudou = (senha or antigo.get("BLL_EMAIL") != v["BLL_EMAIL"] or antigo.get("BLL_PERFIL") != v["BLL_PERFIL"]
                   or antigo.get("PLATAFORMA", "bll") != plat or antigo.get("EMPRESA_CNPJ", "") != v["EMPRESA_CNPJ"])
    if login_mudou and not novo:
        shutil.rmtree(EMPRESAS / eid / "perfil_navegador", ignore_errors=True)
    garantir_servico()
    sh("systemctl", "enable", f"monitor-bll@{eid}")
    sh("systemctl", "restart", f"monitor-bll@{eid}")
    return None


def migrar(nome):
    c = CFG_ATUAL()
    eid = gerar_id(nome)
    EMPRESAS.mkdir(exist_ok=True)
    os.chmod(EMPRESAS, 0o700)
    gravar_env(EMPRESAS / f"{eid}.env", {"EMPRESA_NOME": nome, "BLL_EMAIL": c.get("BLL_EMAIL", ""),
                                         "BLL_SENHA": c.get("BLL_SENHA", ""), "BLL_PERFIL": c.get("BLL_PERFIL", "OPERADOR"),
                                         "ENVIAR_PARA": "geral"})
    sh("systemctl", "disable", "--now", "monitor-bll.service")
    destino = EMPRESAS / eid
    destino.mkdir(exist_ok=True)
    for item in ("bll_monitor.db", "perfil_navegador", "painel.html"):
        if (PASTA / item).exists():
            shutil.move(str(PASTA / item), str(destino / item))
    extra = {} if c.get("MAX_NAVEGADORES") else {"MAX_NAVEGADORES": "3"}
    gravar_env(CONFIG, extra, remover=("BLL_EMAIL", "BLL_SENHA"))
    garantir_servico()
    sh("systemctl", "enable", "--now", f"monitor-bll@{eid}")


def descobrir(token):
    token = token or CFG_ATUAL().get("TELEGRAM_TOKEN", "")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
            dados = json.load(r)
    except Exception as ex:  # noqa: BLE001
        return f"<p>Não consegui consultar o Telegram: {e(str(ex))}</p>"
    chats = {}
    for u in dados.get("result", []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = m.get("chat") or {}
        if c.get("id"):
            chats[c["id"]] = c.get("title") or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    if not chats:
        return "<p>Nenhuma conversa recente. Mande uma mensagem ao bot e tente de novo.</p>"
    return "<table><tr><th>chat_id</th><th>Conversa</th></tr>" + "".join(
        f"<tr><td><b>{i}</b></td><td>{e(n)}</td></tr>" for i, n in chats.items()) + "</table>"


# ------------------------------------------------------------------ servidor
class Painel(BaseHTTPRequestHandler):
    def cookie(self, nome):
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == nome:
                return v
        return ""

    def autorizado(self):
        if SENHA and (sessao_valida(self.cookie("lt_sessao")) or
                      hmac.compare_digest(self.headers.get("Authorization", ""), ESPERADO)):
            return True
        if self.command == "GET" and not self.path.startswith(("/mensagens/processo", "/mensagens/busca")):
            return self.redirecionar("/login?destino=" + urllib.parse.quote(self.path)) and False
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Sessão expirada. Recarregue a página e entre de novo.".encode())
        return False

    def login(self):
        ip = self.client_address[0]
        agora = datetime.now().timestamp()
        TENTATIVAS[ip] = [t for t in TENTATIVAS.get(ip, []) if agora - t < 600]
        tam = int(self.headers.get("Content-Length", "0") or 0)
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(tam, 5000)).decode("utf-8"), keep_blank_values=True).items()}
        destino = f.get("destino", "/")
        if not destino.startswith("/") or destino.startswith("//") or destino.startswith("/login"):
            destino = "/"
        if len(TENTATIVAS[ip]) >= 5:
            return self.enviar(pg_login("Muitas tentativas. Aguarde 10 minutos e tente de novo.", destino), 429)
        ok = bool(SENHA) and hmac.compare_digest(f.get("usuario", "").strip(), USUARIO) and hmac.compare_digest(f.get("senha", ""), SENHA)
        if not ok:
            TENTATIVAS[ip].append(agora)
            return self.enviar(pg_login("Usuário ou senha incorretos.", destino), 401)
        TENTATIVAS.pop(ip, None)
        dias = 30 if f.get("lembrar") else 1
        tok = criar_sessao(dias)
        self.send_response(303)
        validade = f"; Max-Age={dias * 86400}" if f.get("lembrar") else ""
        self.send_header("Set-Cookie", f"lt_sessao={tok}; Path=/; HttpOnly; SameSite=Lax{validade}")
        self.send_header("Location", destino)
        self.end_headers()

    def enviar(self, corpo, codigo=200):
        dados = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(dados)

    def redirecionar(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def do_GET(self):
        caminho = urllib.parse.urlparse(self.path).path.rstrip("/")
        if caminho == "/login":
            q0 = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            return self.enviar(pg_login("", q0.get("destino", "/")))
        if caminho == "/sair":
            encerrar_sessao(self.cookie("lt_sessao"))
            self.send_response(303)
            self.send_header("Set-Cookie", "lt_sessao=; Path=/; Max-Age=0")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if not self.autorizado():
            return
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        p = url.path.rstrip("/") or "/"
        try:
            if p == "/":
                return self.enviar(pg_visao())
            if p == "/mensagens/busca":
                return self.enviar(pg_busca_msgs(q))
            if p == "/mensagens/processo":
                return self.enviar(pg_proc_mensagens(q))
            if p == "/mensagens":
                return self.enviar(pg_mensagens(q) if q.get("vista") == "lista" else pg_chat(q))
            if p == "/processos":
                return self.enviar(pg_processos(q))
            if p == "/empresas":
                return self.enviar(pg_empresas(AVISOS.get(q.get("ok", ""), "")))
            if p == "/empresas/nova":
                return self.enviar(form_empresa())
            if p == "/config":
                return self.enviar(pg_config(AVISOS.get(q.get("ok", ""), "")))
            if p == "/buscar":
                return self.enviar(pg_buscar(urllib.parse.parse_qs(url.query), AVISOS.get(q.get("ok", ""), "")))
            m = re.fullmatch(r"/backup/(licitatudo-[0-9_-]+\.tar\.gz)", p)
            if m and (backup.DIR / m.group(1)).exists():
                dados = (backup.DIR / m.group(1)).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", f'attachment; filename="{m.group(1)}"')
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)
                return
            if p == "/editais":
                return self.enviar(pg_editais(AVISOS.get(q.get("ok", ""), "")))
            m = re.fullmatch(r"/analise/([a-z0-9-]+)", p)
            if m:
                return self.enviar(pg_analise(m.group(1)))
            if p == "/classificacao":
                return self.enviar(pg_classificacao(q))
            if p == "/buscar/itens":
                return self.enviar(pg_itens(q))
            m = re.fullmatch(r"/empresas/([a-z0-9-]+)(/log)?", p)
            if m and (EMPRESAS / f"{m.group(1)}.env").exists():
                if m.group(2):
                    log = sh("journalctl", "-u", f"monitor-bll@{m.group(1)}", "-n", "120", "--no-pager")
                    return self.enviar(pagina("Log", f'<h2>Log – {e(m.group(1))}</h2><pre>{e(log)}</pre>', "/empresas"))
                return self.enviar(form_empresa(m.group(1)))
            m = re.fullmatch(r"/e/([a-z0-9-]+)", p)  # painel individual antigo
            if m and (EMPRESAS / m.group(1) / "painel.html").exists():
                return self.enviar((EMPRESAS / m.group(1) / "painel.html").read_text(encoding="utf-8"))
            self.enviar(pagina("Não encontrado", "<h2>Página não encontrada</h2>"), 404)
        except Exception as ex:  # noqa: BLE001
            self.enviar(pagina("Erro", f"<h2>Erro</h2><pre>{e(repr(ex))}</pre>"), 500)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path.rstrip("/") == "/login":
            return self.login()
        if not self.autorizado():
            return
        tam = int(self.headers.get("Content-Length", "0") or 0)
        if self.path.rstrip("/") == "/classificacao/upload":
            if tam > 20 * 1024 * 1024:
                return self.enviar(pagina("Erro", "<h2>Arquivo grande demais</h2>"), 413)
            campos, arqs = ler_multipart(self, tam)
            if not hmac.compare_digest(campos.get("csrf", ""), CSRF):
                return self.enviar(pagina("Erro", "<h2>Formulário expirado</h2><p>Recarregue a página e tente de novo.</p>"), 403)
            eid = campos.get("empresa", "")
            if not arqs or not (EMPRESAS / f"{eid}.env").exists():
                return self.enviar(pagina("Erro", "<h2>Escolha a empresa e o PDF.</h2>", "/processos"))
            chave, erro = salvar_ranking_pcp(eid, arqs[0][0], arqs[0][1])
            if erro:
                return self.enviar(pagina("Erro", f'<h2>Não deu certo</h2><div class="msg erro">{e(erro)}</div>'
                                          '<p><a class="btn sec" href="/processos">Voltar</a></p>', "/processos"))
            return self.redirecionar("/classificacao?" + urllib.parse.urlencode(
                {"empresa": eid, "processo": chave[0], "promotor": chave[1]}))
        if self.path.rstrip("/") == "/analise/upload":
            if tam > 40 * 1024 * 1024:
                return self.enviar(pg_editais("Arquivos grandes demais (máx. 40 MB).", True), 413)
            campos, arqs = ler_multipart(self, tam)
            if not hmac.compare_digest(campos.get("csrf", ""), CSRF):
                return self.enviar(pagina("Erro", "<h2>Formulário expirado</h2><p>Recarregue a página e tente de novo.</p>"), 403)
            if not arqs:
                return self.enviar(pg_editais("Escolha ao menos um arquivo.", True))
            ch = analise.chave_upload(arqs)
            titulo = campos.get("titulo", "").strip()[:150] or ", ".join(n for n, _ in arqs)[:150]
            pasta = analise.DIR / ch
            pasta.mkdir(parents=True, exist_ok=True)
            for nome, dados in arqs:
                (pasta / re.sub(r"[^\w.\- ]", "_", nome)[:120]).write_bytes(dados)
            analise.iniciar(ch, CFG_ATUAL(), {"tipo": "upload", "arquivos": arqs, "titulo": titulo,
                                              "extra": campos.get("extra", "").strip()[:2000]})
            return self.redirecionar(f"/analise/{ch}")
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(tam, 100000)).decode("utf-8"),
                                                        keep_blank_values=True).items()}
        if not hmac.compare_digest(f.get("csrf", ""), CSRF):
            return self.enviar(pagina("Erro", "<h2>Formulário expirado</h2><p>Recarregue a página e tente de novo.</p>"), 403)
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            if p == "/migrar":
                migrar(f.get("nome", "EMPRESA").strip() or "EMPRESA")
                return self.redirecionar("/empresas?ok=migrado")
            if p == "/empresas/nova":
                err = salvar_empresa(f)
                if err:
                    return self.enviar(pg_empresas(e(err), True))
                return self.redirecionar("/empresas?ok=cadastrada")
            if p == "/config":
                urgencia.ARQ.write_text(f.get("URGENTE_PALAVRAS", "").replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
                v = {k: f.get(k, "").strip() for k in ("TELEGRAM_CHAT_ID", "NTFY_TOPICO", "CHAMADA_USUARIO",
                                                        "INTERVALO_MINUTOS", "MAX_NAVEGADORES", "HORARIO_INICIO",
                                                        "HORARIO_FIM", "SITUACOES_IGNORADAS", "BUSCA_INTERVALO_MINUTOS", "VIGIA_MINUTOS")}
                v["ALARME_SO_URGENTE"] = "1" if f.get("ALARME_SO_URGENTE") else "0"
                v["CHAT_URGENTE"] = "1" if f.get("CHAT_URGENTE") else "0"
                if f.get("TELEGRAM_TOKEN", "").strip():
                    v["TELEGRAM_TOKEN"] = f["TELEGRAM_TOKEN"].strip()
                if f.get("GEMINI_API_KEY", "").strip():
                    v["GEMINI_API_KEY"] = f["GEMINI_API_KEY"].strip()
                v["GEMINI_MODELO"] = f.get("GEMINI_MODELO", "").strip()
                gravar_env(CONFIG, v)
                for x in empresas():
                    if x["ativo"]:
                        sh("systemctl", "restart", f"monitor-bll@{x['id']}")
                return self.redirecionar("/config?ok=config")
            if p == "/analise/pncp":
                cnpj, ano, seq = (re.sub(r"\D", "", f.get(k, "")) for k in ("cnpj", "ano", "seq"))
                ch = analise.chave_pncp(cnpj, ano, seq)
                d = analise.ler(ch)
                if not d or d.get("status") == "erro":
                    analise.iniciar(ch, CFG_ATUAL(), {"tipo": "pncp", "cnpj": cnpj, "ano": ano, "seq": seq,
                                                      "titulo": f.get("titulo", "").strip()[:200] or ch})
                return self.redirecionar(f"/analise/{ch}")
            m = re.fullmatch(r"/analise/([a-z0-9-]+)/(telegram|refazer)", p)
            if m:
                ch, acao = m.groups()
                d = analise.ler(ch)
                if not d:
                    return self.redirecionar("/editais")
                if acao == "telegram" and d.get("md"):
                    for parte in analise.md_telegram(d["md"], d.get("titulo", "")):
                        telegram_geral(parte)
                elif acao == "refazer":
                    o = dict(d.get("origem", {}), titulo=d.get("titulo", ""), extra=f.get("extra", d.get("extra", "")).strip()[:2000])
                    if o.get("tipo") != "pncp":
                        pasta = analise.DIR / ch
                        o["arquivos"] = [(x.name, x.read_bytes()) for x in sorted(pasta.glob("*")) if x.is_file()] if pasta.is_dir() else []
                        if not o["arquivos"]:
                            return self.enviar(pg_editais("Os arquivos desta análise não estão mais guardados. Envie de novo.", True))
                    analise.iniciar(ch, CFG_ATUAL(), o)
                return self.redirecionar(f"/analise/{ch}")
            if p == "/mensagens/lidas":
                marcar_todos_lidos()
                return self.enviar("ok")
            if p == "/editais/ia":
                if f.get("acao") == "padrao":
                    analise.salvar_config_ia("", f.get("empresa", ""))
                else:
                    analise.salvar_config_ia(f.get("instrucoes", ""), f.get("empresa", ""))
                return self.redirecionar("/editais?ok=ia")
            if p == "/buscas/salvar":
                try:
                    filtros = json.loads(f.get("filtros", "{}"))
                except ValueError:
                    filtros = {}
                filtros["nome"] = f.get("nome", "").strip()[:60]
                bid = buscas.salvar_busca(filtros)
                threading.Thread(target=buscas.verificar_salvas, args=(telegram_geral, bid), daemon=True).start()
                return self.redirecionar("/buscar?ok=busca_salva")
            m = re.fullmatch(r"/buscas/(b\d+)/(rodar|pausar|ligar|remover)", p)
            if m:
                if m.group(2) == "rodar":
                    threading.Thread(target=buscas.verificar_salvas, args=(telegram_geral, m.group(1)), daemon=True).start()
                    return self.redirecionar("/buscar?ok=busca_rodando")
                buscas.alterar_busca(m.group(1), m.group(2))
                return self.redirecionar("/buscar?ok=feito")
            if p == "/config/backup":
                v = {"BACKUP_HORA": re.sub(r"\D", "", f.get("BACKUP_HORA", "3")) or "3"}
                if f.get("BACKUP_SENHA", "").strip():
                    if len(f["BACKUP_SENHA"].strip()) < 8:
                        return self.enviar(pg_config("A senha do backup precisa ter pelo menos 8 caracteres."))
                    v["BACKUP_SENHA"] = f["BACKUP_SENHA"].strip()
                gravar_env(CONFIG, v)
                if f.get("acao") == "agora":
                    try:
                        res = backup.executar(CFG_ATUAL(), telegram_geral)
                    except Exception as ex:  # noqa: BLE001
                        res = f"Backup falhou: {ex}"
                    return self.enviar(pg_config(e(res)))
                return self.redirecionar("/config?ok=config")
            if p == "/telegram":
                return self.enviar(pagina("Telegram", "<h2>Conversas encontradas</h2>" + descobrir(f.get("token", "").strip()) +
                                          '<p><a class="btn sec" href="/config">Voltar</a></p>', "/config"))
            m = re.fullmatch(r"/empresas/([a-z0-9-]+)(?:/(pausar|retomar|testar|remover|reiniciar))?", p)
            if m and (EMPRESAS / f"{m.group(1)}.env").exists():
                eid, acao = m.group(1), m.group(2)
                svc = f"monitor-bll@{eid}"
                if acao is None:
                    err = salvar_empresa(f, eid)
                    return self.enviar(pg_empresas(e(err), True)) if err else self.redirecionar("/empresas?ok=atualizada")
                if acao == "pausar":
                    sh("systemctl", "disable", "--now", svc)
                elif acao == "retomar":
                    sh("systemctl", "enable", "--now", svc)
                elif acao == "reiniciar":
                    sh("systemctl", "restart", svc)
                elif acao == "remover":
                    sh("systemctl", "disable", "--now", svc)
                    shutil.rmtree(EMPRESAS / eid, ignore_errors=True)
                    (EMPRESAS / f"{eid}.env").unlink(missing_ok=True)
                elif acao == "testar":
                    saida = sh(str(PY), str(PASTA / "monitor_bll.py"), "--empresa", eid, "--alarme", timeout=90)
                    return self.enviar(pg_empresas(f"Teste enviado. Resultado: {e(saida[-300:])}"))
                return self.redirecionar("/empresas?ok=feito")
            self.enviar(pagina("Não encontrado", "<h2>Ação não encontrada</h2>"), 404)
        except Exception as ex:  # noqa: BLE001
            self.enviar(pagina("Erro", f"<h2>Erro</h2><pre>{e(repr(ex))}</pre>"), 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Painel em http://0.0.0.0:{PORTA}")
    threading.Thread(target=buscas.ciclo, args=(telegram_geral, minutos_busca), daemon=True).start()
    threading.Thread(target=vigiar, daemon=True).start()
    threading.Thread(target=backup.ciclo, args=(CFG_ATUAL, telegram_geral), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORTA), Painel).serve_forever()
