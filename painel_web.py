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
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agenda
import analise
import assistente
import backup
import urgencia
import buscas
import concorrentes
import copiloto
import custos_ia
import resumo
import documentos
import geo
import oportunidades
import pecas

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
          "busca_rodando": "Verificando agora. Se houver novidades, chegam no Telegram em instantes.",
          "doc_dados": "Dados da empresa salvos.",
          "doc_cnpj": "Dados preenchidos pela Receita e salvos. Confira e complete o CPF do representante (não vem da Receita).",
          "piso": "Piso salvo. O copiloto usa este valor na disputa.",
          "custos": "Preços da IA salvos. Os novos usos já são calculados com eles.",
          "resumo_enviado": "Resumo enviado agora no Telegram geral.",
          "prazo": "Prazo salvo. Você recebe aviso no Telegram 24 h e 2 h antes.",
          "agendado": "Prazo colocado na Agenda.",
          "cert_ok": "Certidão salva.",
          "cert_lida": "Certidão salva com os dados lidos do PDF – confira o tipo e a validade na lista.",
          "cert_sem_validade": "Certidão salva, mas SEM validade (não consegui ler do PDF). Clique em “editar” e informe a data.",
          "senha": "Senha do painel trocada. Os outros aparelhos foram desconectados.",
          "sessoes": "Todas as outras sessões foram encerradas.",
          "op_rodando": "Atualizando as oportunidades no PNCP. Leva 1 a 3 minutos; recarregue a página depois.",
          "op_cfg": "Perfil salvo. Atualizando as oportunidades com as novas regras (1 a 3 minutos)."}
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
HOST = CFG.get("PAINEL_HOST", "0.0.0.0")  # 127.0.0.1 = só pelo HTTPS (Caddy)
ARQ_SESSOES = PASTA / "sessoes.json"
TENTATIVAS = {}   # ip -> [horários de erro]


def _h(tok):
    return hashlib.sha256((tok or "").encode()).hexdigest()


def _sessoes():
    try:
        d = json.loads(ARQ_SESSOES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    agora = datetime.now().timestamp()
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v > agora and len(k) == 64}


def _gravar_sessoes(d):
    ARQ_SESSOES.write_text(json.dumps(d), encoding="utf-8")
    os.chmod(ARQ_SESSOES, 0o600)


def criar_sessao(dias):
    """O arquivo guarda só o 'hash' do código da sessão: quem ler o arquivo não consegue entrar."""
    tok = secrets.token_urlsafe(32)
    d = _sessoes()
    d[_h(tok)] = datetime.now().timestamp() + dias * 86400
    _gravar_sessoes(d)
    return tok


def sessao_valida(tok):
    return bool(tok) and _h(tok) in _sessoes()


def encerrar_sessao(tok):
    d = _sessoes()
    d.pop(_h(tok), None)
    _gravar_sessoes(d)


def encerrar_todas(menos=""):
    d = _sessoes()
    _gravar_sessoes({k: v for k, v in d.items() if menos and k == _h(menos)})


ARQ_ACESSOS = PASTA / "acessos_conhecidos.json"
FALHAS_GERAIS = []          # horários de senha errada (todos os IPs)
_ALERTAS_LOGIN = {}


def senha_fraca(sen):
    return len(sen) < 12 or not (re.search(r"[A-Za-z]", sen) and re.search(r"\d", sen))


def alerta_seguranca(chave, texto, minutos=60):
    """Manda aviso de segurança no Telegram (no máximo um por 'chave' a cada X minutos)."""
    agora = datetime.now().timestamp()
    if agora - _ALERTAS_LOGIN.get(chave, 0) < minutos * 60:
        return
    _ALERTAS_LOGIN[chave] = agora
    threading.Thread(target=telegram_geral, args=("🛡️ " + texto,), daemon=True).start()


def acesso_novo(ip, navegador):
    try:
        d = json.loads(ARQ_ACESSOS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    novo = ip not in d
    d[ip] = datetime.now().strftime("%d/%m/%Y %H:%M")
    ARQ_ACESSOS.write_text(json.dumps(dict(list(d.items())[-200:])), encoding="utf-8")
    os.chmod(ARQ_ACESSOS, 0o600)
    if novo:
        alerta_seguranca(f"novo|{ip}", f"<b>Novo acesso ao painel</b> de um endereço nunca usado antes.\nIP: {e(ip)}\n"
                                       f"Navegador: {e(navegador[:120])}\nSe não foi você, troque a senha do painel em "
                                       f"Configurações → Segurança e clique em “Encerrar todas as sessões”.", 0)


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


RE_COPIA_CHAT = re.compile(r"horario\W+autor\W+mensagem|mensagens\W+do\W+lote\W+(?:l\d+\W+)?hor", re.I)  # texto já normalizado


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
            if any(RE_COPIA_CHAT.search(urgencia.norm(x)) for x in (r[5], r[4], r[2])) or any(
                    "horarioautormensagem" in re.sub(r"[^a-z]", "", urgencia.norm(x)) or
                    re.sub(r"[^a-z]", "", urgencia.norm(x)).startswith("mensagensdolote") for x in (r[5], r[4])):
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
nav a{color:#cfe3d7;text-decoration:none;padding:16px 9px;font-size:13.5px;white-space:nowrap;border-bottom:3px solid transparent}
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
.cv-cab{display:flex;gap:12px;align-items:flex-start;background:#173f2c;color:#fff;padding:14px 18px;border-radius:12px 12px 0 0;font-size:13.5px}.cv-cab .sub{color:#cfe3d7}
.mc-emp{flex-basis:100%;width:100%;margin-top:4px;padding:8px 10px;border:1px solid var(--borda);border-radius:8px;background:var(--card);color:inherit;font-size:14px}
.cv-classif{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 16px;background:var(--nova);border-bottom:1px solid var(--borda)}
.cl-tit{font-weight:800;margin-right:4px}.cl-chip{border-radius:999px;padding:4px 12px;font-size:14px;border:1px solid var(--borda);background:var(--card);cursor:default}
.cl-chip.c-1{background:#2e7d32;color:#fff;border-color:#2e7d32}.cl-chip.c-23{background:#f9a825;color:#1b1b1b;border-color:#f9a825}
.cl-chip.c-n{background:var(--card)}.cl-chip.c-x{background:#c62828;color:#fff;border-color:#c62828}.cl-chip.c-0{opacity:.6}
.cl-ver{margin-left:auto;padding:6px 12px;font-size:13px}.cl-quando{font-size:12px}
.cv-doc{margin-left:auto;background:rgba(255,255,255,.15);color:#fff;border:1px solid rgba(255,255,255,.35);padding:6px 12px;font-size:13px;align-self:flex-start}
.cv-sit{background:rgba(255,255,255,.18);padding:1px 8px;border-radius:999px;font-weight:700;color:#fff}
.cv-num{font-size:18px;font-weight:700;margin-bottom:4px;display:flex;gap:8px;align-items:center}
.cv-filtro{display:flex;gap:10px;align-items:center;padding:12px 18px;border-bottom:1px solid var(--borda)}.cv-filtro select{min-width:0;width:100%}.cv-filtro{flex-wrap:wrap}.fm-l{flex:1 1 200px;display:flex;flex-direction:column;gap:4px;margin:0;font-size:12px;font-weight:700;color:var(--sub)}
@keyframes pulsar{0%,100%{box-shadow:0 0 0 0 rgba(229,57,53,.55)}50%{box-shadow:0 0 0 6px rgba(229,57,53,0)}}
@keyframes piscar{0%,100%{opacity:1}50%{opacity:.35}}
select.fm.tem-novas{border:2px solid #e53935;animation:pulsar 1.6s ease-in-out infinite}
.selo-nova{font-style:normal;background:#e53935;color:#fff;font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;margin-left:4px;animation:piscar 1.2s ease-in-out 6}
.faixa-novas{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px 18px;background:rgba(229,57,53,.08);border-bottom:1px solid var(--borda);font-size:13px}
.chip-nova{background:#e53935;color:#fff;border:0;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:700;cursor:pointer;animation:pulsar 1.6s ease-in-out infinite}
.tag-nova{background:#e53935;color:#fff;font-size:10px;font-weight:800;padding:1px 6px;border-radius:4px}
.cm.nova{box-shadow:inset 3px 0 0 #e53935}
select.fm.ativo{border-color:var(--verde);box-shadow:0 0 0 3px var(--verde2);color:var(--txt);font-weight:600}
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

ABAS = [("/", "Início"), ("/mensagens", "Mensagens"), ("/processos", "Processos"), ("/disputa", "🎯 Disputa"), ("/agenda", "Agenda"),
        ("/concorrentes", "Concorrentes"), ("/oportunidades", "Oportunidades"), ("/buscar", "Buscar"), ("/editais", "Editais"),
        ("/documentos", "Documentos"), ("/empresas", "Empresas"), ("/config", "⚙️")]


BOTAO_IA = ('<a href="/pergunte" class="fab-ia" title="Pergunte ao Licitatudo (IA)">💬<span>Pergunte</span></a>'
            '<style>.fab-ia{position:fixed;right:18px;bottom:18px;z-index:1500;background:#1b5e20;color:#fff;text-decoration:none;'
            'border-radius:999px;padding:12px 16px;font-weight:700;box-shadow:0 6px 18px rgba(0,0,0,.3);display:flex;gap:6px;align-items:center}'
            '.fab-ia:hover{background:#2e7d32}@media print{.fab-ia{display:none}}@media(max-width:700px){.fab-ia span{display:none}}</style>')


def pagina(titulo, corpo, aba="/", aviso="", erro=False, recarregar=False):
    nav = "".join(f'<a href="{u}" class="{"on" if u == aba else ""}" title="{"Configurações" if u == "/config" else n}">{n}</a>' for u, n in ABAS)
    msg = f'<div class="msg {"erro" if erro else ""}">{aviso}</div>' if aviso else ""
    if aba in ("/", "/config") and SENHA and senha_fraca(SENHA):
        msg += ('<div class="msg erro">🛡️ A senha do painel é fraca (menos de 12 caracteres ou sem letras e números). '
                '<a href="/config#seguranca">Troque em Configurações → Segurança</a>.</div>')
    meta = '<meta http-equiv="refresh" content="60">' if recarregar else ""
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(titulo)} – Licitatudo</title>
{TEMA_JS}<style>{ESTILO}</style></head><body><header><b>Licitatudo</b><nav>{nav}</nav><button class="tema" onclick="trocarTema()" title="Modo escuro">🌙</button><a class="sair" href="/sair">Sair</a></header><main>{msg}{corpo}</main>{'' if aba == "/pergunte" else BOTAO_IA}</body></html>"""


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
    _sts = [agenda.situacao_prazo(it) for it in agenda.prazos()["itens"].values()]
    n_prazo_urg = sum(1 for x in _sts if x in ("atrasado", "hoje"))
    n_prazo_sem = sum(1 for x in _sts if x == "semana")
    n_cert = sum(1 for c in agenda.certidoes()["itens"].values() if agenda.status_cert(c)[0] in ("vence", "vencida"))
    kpis = f"""<div class="kpis">
<a class="kpi" href="/consumo" style="text-decoration:none;color:inherit"><div class="v">{custos_ia.brl(custos_ia.resumo_mes(datetime.now().strftime("%Y-%m"))["total_brl"])}</div><div class="l">💳 IA neste mês</div></a>
<a class="kpi" href="/agenda" style="text-decoration:none;color:inherit{';border-color:#c62828' if n_prazo_urg else ''}"><div class="v">{n_prazo_urg}</div><div class="l">📅 prazos atrasados ou para hoje · {n_prazo_sem} na semana</div></a>
<a class="kpi" href="/certidoes" style="text-decoration:none;color:inherit{';border-color:#c62828' if n_cert else ''}"><div class="v">{n_cert}</div><div class="l">📑 certidões vencidas ou vencendo (15 dias)</div></a>
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


def situacao_proc(eid, processo, promotor):
    """Situação atual do processo como aparece na plataforma (ex.: HABILITAÇÃO, RECURSO, HOMOLOGADO)."""
    db = EMPRESAS / eid / "bll_monitor.db"
    if not db.exists():
        return ""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        r = con.execute("SELECT situacao, atualizada FROM processos WHERE processo=? AND promotor=?",
                        (processo, promotor)).fetchone()
        con.close()
        return (r[0] or "").strip() if r else ""
    except Exception:  # noqa: BLE001
        return ""


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


def marcar_lido_sub(chave_p, cat, sub):
    d = lidos()
    d[f"{chave_p}#{cat}#{sub}"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ARQ_LIDOS.write_text(json.dumps(d), encoding="utf-8")


def marcar_todos_lidos():
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    d = lidos()
    d["_todas"] = agora
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
            f'<button type="button" class="sec" title="Marca como lidas só as mensagens do lote/menu escolhido neste processo" '
            f'onclick="marcarLidoAtual()">✔ Marcar como lido</button>'
            f'<button type="button" class="sec" title="Marca como lidas as mensagens de TODOS os processos" '
            f'onclick="if(confirm(\'Marcar como lidas as mensagens de todos os processos?\'))marcarTodas()">✔✔ Tudo</button>'
            f'<button type="button" class="sec" title="Atualizar agora" onclick="location.reload()">⟳ Atualizar</button>'
            f'<span id="pesq-info" class="sub"></span></form>')


def categoria_msg(m):
    """('lote'|'dilig'|'evento', subgrupo) – separa chat dos lotes, diligências e eventos do processo."""
    esc = m.get("escopo") or ""
    n = urgencia.norm(f"{esc} {m.get('texto') or ''}")
    g = grupo_escopo(esc)
    eh_lote = g.startswith("L") and g[1:].isdigit()
    if "dilig" in urgencia.norm(esc):  # só o que veio da janela de diligências (chat que cita a palavra fica no lote)
        return "dilig", (g if eh_lote else "Processo")
    esc_n = urgencia.norm(esc)
    if "registro do lote" in esc_n:
        return "evento", "Registros do lote"
    if "andamento" in esc_n:
        return "evento", "Andamento do processo"
    if not urgencia.eh_chat(esc, m.get("autor") or "") or not eh_lote:
        return "evento", (g if not eh_lote else "Outros")
    return "lote", g


def _ordem_sub(x):
    return (not (x.startswith("L") and x[1:].isdigit()), int(x[1:]) if x[1:].isdigit() else 0, x)


def _rot_sub(x):
    return "Lote " + x[1:] if x.startswith("L") and x[1:].isdigit() else x


def html_conversa(g, cfg_chat, mapa=None):
    mapa = lidos() if mapa is None else mapa
    cp = chave_proc(g)
    todas = para_data(mapa.get("_todas", "")) if mapa.get("_todas") else datetime.min
    if not mapa.get("_inicio_sub"):   # a marcação por lote vale só para o que chegar depois de ligada
        mapa["_inicio_sub"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        d = lidos()
        d["_inicio_sub"] = mapa["_inicio_sub"]
        ARQ_LIDOS.write_text(json.dumps(d), encoding="utf-8")
    inicio = para_data(mapa["_inicio_sub"])
    cats = {"lote": {}, "dilig": {}, "evento": {}}
    novas = {"lote": {}, "dilig": {}, "evento": {}}
    info = []
    for m in g["msgs"]:
        c, sub = categoria_msg(m)
        cats[c][sub] = cats[c].get(sub, 0) + 1
        rec = mapa.get(f"{cp}#{c}#{sub}")
        lim = max(para_data(rec) if rec else datetime.min, todas, inicio)
        nova = para_data(m["visto"]) > lim
        if nova:
            novas[c][sub] = novas[c].get(sub, 0) + 1
        info.append((m, c, sub, nova))
    tot = {c: sum(v.values()) for c, v in cats.items()}
    tot_n = {c: sum(v.values()) for c, v in novas.items()}

    def menu(cat, rotulo, todos, padrao=False):
        # o número entre parênteses é o de NÃO LIDAS: zera quando você clica em "Marcar como lido"
        def op(x):
            n = novas[cat].get(x, 0)
            estilo = ' style="color:#c62828;font-weight:700"' if n else ""
            return (f'<option value="{e(x)}" data-novas="{n}" data-rot="{e(_rot_sub(x))}"{estilo}>'
                    f'{e(_rot_sub(x))} ({n}{" 🔴" if n else ""})</option>')
        ops = "".join(op(x) for x in sorted(cats[cat], key=_ordem_sub))
        nt = tot_n[cat]
        vazio = f'<option value="" data-rot="{rotulo}">{rotulo} ({nt}{" 🔴" if nt else ""})</option>'
        sel_todos = ' selected' if padrao else ''
        selo = f'<em class="selo-nova">NOVA MENSAGEM</em>' if nt else ""
        return (f'<label class="fm-l"><span title="O número entre parênteses é o de mensagens não lidas">{rotulo} {selo}</span>'
                f'<select class="fm{" ativo" if padrao else ""}{" tem-novas" if nt else ""}" data-cat="{cat}" '
                f'onchange="escolherMenu(this)">{vazio}<option value="todos" data-rot="{todos}"{sel_todos}>{todos} ({nt}{" 🔴" if nt else ""})</option>'
                f'{ops}</select></label>')

    chips = "".join(
        f'<button type="button" class="chip-nova" data-cat="{c}" data-sub="{e(x)}" onclick="irNovas(this)">'
        f'{ {"lote": "", "dilig": "Diligência · ", "evento": "Evento · "}[c] }{e(_rot_sub(x))} · {n} nova{"s" if n > 1 else ""}</button>'
        for c in ("lote", "dilig", "evento") for x, n in sorted(novas[c].items(), key=lambda t: _ordem_sub(t[0])))
    faixa = f'<div class="faixa-novas">🔔 <b>Novidades:</b> {chips}</div>' if chips else ""

    msgs = sorted(info, key=lambda x: (data_msg(x[0]), para_data(x[0]["visto"])), reverse=True)
    itens = ""
    for m, c, sub, nova in msgs:
        urg = urgencia.classificar(m["texto"], m["escopo"], m["autor"], chat_urgente=cfg_chat)
        tag_urg = f' <span class="tag erro">🚨 {e(urg)}</span>' if urg else ""
        oculto = "" if c == "lote" else ' style="display:none"'
        tag_nova = '<span class="tag-nova">NOVA</span>' if nova else ""
        itens += (f'<div class="cm{" urg" if urg else ""}{" nova" if nova else ""}" data-cat="{c}" data-sub="{e(sub)}"{oculto}>'
                  f'<div class="cm-cab"><b>{e(titulo_msg(m))}</b>{tag_nova}{tag_urg}<span class="sub cm-hora">{e(m["horario"] or m["visto"])}</span></div>'
                  f'<div class="cm-txt">{destacar(e(m["texto"]))}</div></div>')
    return (f'<div class="cv-cab" data-proc="{e(cp)}"><div><div class="cv-num">{e(g["processo"])} {selo_plat(g["plat"])}</div>'
            f'<div><b>Órgão:</b> {e(g["promotor"])}</div><div class="sub"><b>Empresa:</b> {e(g["nome"])} · '
            f'<b>Situação:</b> <span class="cv-sit">{e(situacao_proc(g["eid"], g["processo"], g["promotor"]) or "—")}</span>'
            f'{proximo_prazo_html(g)}'
            f'</div></div><a class="btn cv-doc" href="/documentos?{e(urllib.parse.urlencode({"emp": g["nome"].upper().strip(), "proc": g["processo"] + "||" + g["promotor"]}))}" '
            f'title="Gerar as declarações deste processo">📄 Declarações</a>'
            f'<a class="btn cv-doc" style="margin-left:6px" href="/pecas?{e(urllib.parse.urlencode({"emp": g["nome"].upper().strip(), "proc": g["processo"] + "||" + g["promotor"]}))}" '
            f'title="Rascunho de recurso, contrarrazões, resposta a diligência… com IA">⚖️ Peça (IA)</a></div>' + barra_classif(g) +
            f'<div class="cv-filtro">'
            + menu("lote", "Lotes (mensagens)", "Todos os lotes", padrao=True)
            + menu("dilig", "Diligências", "Todas as diligências")
            + menu("evento", "Processo (eventos)", "Todos os eventos")
            + '</div>' + faixa + barra_msgs() +
            f'<div class="cv-lista">{itens or "<div class=sub>Sem mensagens.</div>"}</div>')


def barra_classif(g):
    """Faixa bem visível com a nossa posição em cada lote (dados da classificação lida pelo monitor)."""
    linhas = classif(g["eid"], g["processo"], g["promotor"])
    if not linhas:
        return ""
    chips = ""
    for r in linhas:
        lote, fase, primeiro, melhor, pos, lance = nlote(r[2]), r[4] or "", r[5] or "", r[6] or "", r[8], r[9] or ""
        if pos == 1:
            cls, txt = "c-1", "🥇 1º"
        elif pos in (2, 3):
            cls, txt = "c-23", f"{pos}º"
        elif pos:
            cls, txt = "c-n", f"{pos}º"
        elif lance:
            cls, txt = "c-x", "❌ desclass."
        else:
            cls, txt = "c-0", "—"
        dica = f"Lote {lote}: {txt} · 1º: {primeiro} {melhor} · Fase: {fase}"
        chips += f'<span class="cl-chip {cls}" title="{e(dica)}"><b>L{e(lote)}</b> {e(txt)}</span>'
    url = "/classificacao?" + urllib.parse.urlencode({"empresa": g["eid"], "processo": g["processo"], "promotor": g["promotor"]})
    atual = max((r[10] or "" for r in linhas), default="")
    return (f'<div class="cv-classif"><span class="cl-tit">📊 Nossa classificação</span>{chips}'
            f'<a class="btn cl-ver" href="{e(url)}">Ver classificação completa</a>'
            f'<span class="sub cl-quando">lida em {e(atual)}</span></div>')


def proximo_prazo_html(g):
    """Próximo prazo aberto deste processo (Agenda), para mostrar no cabeçalho do chat."""
    agora = datetime.now()
    abertos = [it for it in agenda.prazos()["itens"].values() if it.get("status") == "aberto"
               and it.get("processo") == g["processo"] and it.get("promotor") == g["promotor"]
               and datetime.fromisoformat(it["quando"]) > agora - timedelta(hours=12)]
    if not abertos:
        return ""
    it = min(abertos, key=lambda x: x["quando"])
    q = datetime.fromisoformat(it["quando"])
    quando = q.strftime("%d/%m") + ("" if q.hour == 23 and q.minute == 59 else q.strftime(" %H:%M"))
    return (f' · <a href="/agenda" style="color:#ffd54f;font-weight:700" title="{e(it["titulo"])}">⏰ {e(it["titulo"].split(" – ")[0])} '
            f'até {quando} ({e(agenda.falta_txt(q, agora))})</a>')


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
    nomes_emp = sorted({g["nome"] for g in lista})
    menu_emp = ('<select class="mc-emp" onchange="filtrar();try{sessionStorage.setItem(\'emp\',this.value)}catch(e){}" title="Filtrar por empresa">'
                f'<option value="">🏢 Todas as empresas ({len(lista)})</option>' +
                "".join(f'<option value="{e(n)}">{e(n)} ({sum(1 for g in lista if g["nome"] == n)})</option>' for n in nomes_emp) +
                "</select>")
    sel = q.get("processo") and (q.get("eid", ""), q.get("processo", ""), q.get("promotor", ""))
    itens = ""
    for g in lista:
        um = g["ult_msg"] or {}
        urg = urgencia.classificar(um.get("texto", ""), um.get("escopo", ""), um.get("autor", ""), chat_urgente=cfg_chat)
        nl = nao_lido(g, mapa)
        quando = g["ultima"].strftime("%d/%m/%Y - %H:%M") if g["ultima"] != datetime.min else "—"
        url = "/mensagens/processo?" + urllib.parse.urlencode({"eid": g["eid"], "processo": g["processo"], "promotor": g["promotor"]})
        busca_txt = f'{g["processo"]} {g["promotor"]}'.lower()   # empresa tem filtro próprio (menu abaixo das plataformas)
        on = sel == (g["eid"], g["processo"], g["promotor"])
        itens += (f'<a class="it{" on" if on else ""}" href="#" data-url="{e(url)}" data-plat="{e(g["plat"])}" data-emp="{e(g["nome"])}" data-nl="{1 if nl else 0}" '
                  f'data-urg="{1 if urg else 0}" data-busca="{e(busca_txt)}" onclick="return abrir(this)">'
                  f'<div class="it-l1"><b>{e(g["processo"])}</b><span class="it-data">{e(quando)}'
                  f'<i class="ponto"{"" if nl else " hidden"}></i></span></div>'
                  f'<div class="it-org">{e(g["promotor"])}</div>'
                  f'<div class="it-l3">{selo_plat(g["plat"])}<span class="sub">{e(g["nome"])}</span>'
                  f'{"<span class=it-urg>🚨</span>" if urg else ""}</div></a>')
    direita = barra_msgs() + '<div class="cv-vazio">Selecione um processo à esquerda para ver as mensagens.</div>'
    if sel and sel in procs:
        direita = html_conversa(procs[sel], cfg_chat, mapa)
        marcar_lido(chave_proc(procs[sel]))
    corpo = f"""<div class="mc">
<aside class="mc-esq"><div class="mc-topo"><h2 style="margin:0">Monitorar chat</h2>
<div class="ferr">
<a class="btn sec" href="/mensagens?vista=lista" title="Ver todas as mensagens em tabela">☰</a></div></div>

<input class="mc-busca" placeholder="Buscar pregão ou órgão…" oninput="filtrar()">
<div class="abas">{abas}{menu_emp}</div>
<div class="mc-lista" id="lista">{itens or '<div class="sub" style="padding:14px">Nenhuma mensagem ainda.</div>'}</div></aside>
<section class="mc-dir"><div id="conversa">{direita}</div></section></div>
<script>
let filtroAba='todos';
function passaAba(a){{if(filtroAba==='naolidos')return a.dataset.nl==='1'; if(filtroAba==='urgentes')return a.dataset.urg==='1';
 if(filtroAba.startsWith('plat:'))return a.dataset.plat===filtroAba.slice(5); return true;}}
function filtrar(){{const t=document.querySelector('.mc-busca').value.toLowerCase();
 const its=[...document.querySelectorAll('#lista .it')]; const se=document.querySelector('.mc-emp');
 if(se){{  // o menu mostra só as empresas da plataforma/aba escolhida
  const cont={{}}; let tot=0; its.forEach(a=>{{if(passaAba(a)){{cont[a.dataset.emp]=(cont[a.dataset.emp]||0)+1; tot++;}}}});
  [...se.options].forEach(o=>{{if(!o.value){{o.textContent='🏢 Todas as empresas ('+tot+')'; return;}}
   const n=cont[o.value]||0; o.hidden=!n; o.disabled=!n; o.textContent=o.value+' ('+n+')';}});
  if(se.value&&!cont[se.value])se.value='';}}
 const em=se?se.value:'';
 its.forEach(a=>{{const ok=(!t||a.dataset.busca.includes(t))&&(!em||a.dataset.emp===em)&&passaAba(a);
  a.style.display=ok?'':'none';}});}}
function aba(b){{document.querySelectorAll('.aba').forEach(x=>x.classList.remove('on'));b.classList.add('on');filtroAba=b.dataset.f;filtrar();
 try{{sessionStorage.setItem('aba',filtroAba);}}catch(e){{}}}}
function abrir(a){{document.querySelectorAll('#lista .it').forEach(x=>x.classList.remove('on'));a.classList.add('on');
 const c=document.getElementById('conversa');c.innerHTML='<div class="cv-vazio">Carregando…</div>';
 fetch(a.dataset.url,{{credentials:'same-origin'}}).then(r=>r.text()).then(h=>{{c.innerHTML=h;a.dataset.nl='0';
  const p=a.querySelector('.ponto'); if(p)p.hidden=true;
  restaurarFiltro(c,a.dataset.url);
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
function filtroAtual(){{const s=[...document.querySelectorAll('#conversa select.fm')].find(x=>x.value!=='');
 return s?{{cat:s.dataset.cat,v:s.value}}:null;}}
function aplicarBusca(i){{const t=(i&&i.value||'').trim(); const f=filtroAtual();
 const re=padraoBusca(t);
 document.querySelectorAll('#conversa .cm').forEach(m=>{{if(m.dataset.t===undefined)m.dataset.t=m.textContent;
  const vis=!m.dataset.cat||(f?(m.dataset.cat===f.cat&&(f.v==='todos'||m.dataset.sub===f.v)):true); if((m.style.display!=='none')!==vis)m.style.display=vis?'':'none';
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
function escolherMenu(sel){{const todos=[...document.querySelectorAll('#conversa select.fm')];
 if(sel.value===''){{const l=todos.find(x=>x.dataset.cat==='lote'); if(l){{sel=l; l.value='todos';}}}}
 todos.forEach(x=>{{if(x!==sel)x.value=''; x.classList.toggle('ativo',x===sel);}});
 aplicarBusca(document.getElementById('pesq-txt'));
 const a=document.querySelector('#lista .it.on'); try{{if(a)sessionStorage.setItem('filtro|'+a.dataset.url,sel.dataset.cat+'|'+sel.value);}}catch(e){{}}}}
function marcarLidoAtual(){{  // marca como lido só o lote/menu escolhido neste processo
 const cab=document.querySelector('#conversa .cv-cab'); const sel=[...document.querySelectorAll('#conversa select.fm')].find(x=>x.value!=='');
 if(!cab||!sel)return;
 const subs=sel.value==='todos'?[...sel.options].filter(o=>o.value&&o.value!=='todos').map(o=>o.value):[sel.value];
 const lidas=subs.filter(v=>{{const o=[...sel.options].find(x=>x.value===v);return o&&+o.dataset.novas>0;}});
 if(!lidas.length){{avisoLido('Nada novo aqui.');return;}}
 const corpo='csrf={CSRF}&proc='+encodeURIComponent(cab.dataset.proc)+'&subs='+encodeURIComponent(lidas.map(v=>sel.dataset.cat+'|'+v).join('||'));
 fetch('/mensagens/lidas',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:corpo}}).then(r=>{{if(!r.ok)return;
  lidas.forEach(v=>{{const o=[...sel.options].find(x=>x.value===v); if(o){{o.dataset.novas=0;o.style.color='';o.style.fontWeight='';o.textContent=o.dataset.rot+' (0)';}}
   document.querySelectorAll('#conversa .chip-nova[data-cat="'+sel.dataset.cat+'"][data-sub="'+v+'"]').forEach(x=>x.remove());
   document.querySelectorAll('#conversa .cm.nova[data-cat="'+sel.dataset.cat+'"][data-sub="'+v+'"]').forEach(m=>{{m.classList.remove('nova');const t=m.querySelector('.tag-nova');if(t)t.remove();}});}});
  const resto=[...sel.options].filter(o=>o.value&&o.value!=='todos').reduce((a,o)=>a+(+o.dataset.novas||0),0);
  [...sel.options].filter(o=>!o.value||o.value==='todos').forEach(o=>{{o.textContent=o.dataset.rot+' ('+resto+(resto?' 🔴':'')+')';}});
  if(!resto){{sel.classList.remove('tem-novas');const sl=sel.parentElement.querySelector('.selo-nova');if(sl)sl.remove();}}
  const fx=document.querySelector('#conversa .faixa-novas'); if(fx&&!fx.querySelector('.chip-nova'))fx.remove();
  avisoLido('Marcado como lido.');}});}}
function avisoLido(t){{const i=document.getElementById('pesq-info'); if(i){{i.textContent=t; setTimeout(()=>{{i.textContent='';}},2500);}}}}
function irNovas(b){{const s=document.querySelector('#conversa select.fm[data-cat="'+b.dataset.cat+'"]'); if(!s)return; s.value=b.dataset.sub; escolherMenu(s);
 const n=document.querySelector('#conversa .cm.nova[data-cat="'+b.dataset.cat+'"][data-sub="'+b.dataset.sub+'"]'); if(n)n.scrollIntoView({{block:'center',behavior:'smooth'}});}}
function restaurarFiltro(c,url){{try{{const r=sessionStorage.getItem('filtro|'+url); if(!r)return; const [cat,v]=r.split('|');
 const s=c.querySelector('select.fm[data-cat="'+cat+'"]'); if(s&&[...s.options].some(o=>o.value===v)){{s.value=v; escolherMenu(s);}}}}catch(e){{}}}}
try{{const em=sessionStorage.getItem('emp'); const se=document.querySelector('.mc-emp');
 if(em&&se&&[...se.options].some(o=>o.value===em)){{se.value=em; filtrar();}}}}catch(e){{}}
try{{const f=sessionStorage.getItem('aba'); if(f){{const b=document.querySelector('.aba[data-f="'+f+'"]'); if(b)aba(b);}}}}catch(e){{}}
(function(){{const a=document.querySelector('#lista .it.on'); if(a){{a.scrollIntoView({{block:'center'}});
 restaurarFiltro(document.getElementById('conversa'),a.dataset.url);}}}})();
</script>"""
    return pagina("Mensagens", corpo, "/mensagens", recarregar=False)


def pg_proc_mensagens(q):
    procs = agrupar_processos()
    g = procs.get((q.get("eid", ""), q.get("processo", ""), q.get("promotor", "")))
    if not g:
        return '<div class="cv-vazio">Sem mensagens.</div>'
    h = html_conversa(g, CFG_ATUAL().get("CHAT_URGENTE", "1") != "0")
    marcar_lido(chave_proc(g))
    return h


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
    # Portal de Compras: o PDF não traz a fase; mostra a situação atual do processo
    sit = {}
    out = []
    for r in linhas:
        if (r[4] or "") == "Ranking (PDF)":
            k = (r[0], r[1])
            if k not in sit:
                sit[k] = situacao_proc(eid, r[0], r[1])
            r = r[:4] + (sit[k] or r[4],) + r[5:]
        out.append(r)
    return sorted(out, key=lambda r: num(r[2]))


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


# ------------------------------------------------------------------ declarações
def nomes_empresas():
    """{NOME: [ids...]} – a mesma empresa em plataformas diferentes vira um nome só."""
    out = {}
    for arq in sorted(EMPRESAS.glob("*.env")) if EMPRESAS.exists() else []:
        c = ler_env(arq)
        out.setdefault((c.get("EMPRESA_NOME") or arq.stem).upper().strip(), []).append(arq.stem)
    return out


def dados_doc(nome, ids):
    d = documentos.dados(nome)
    if not d["cnpj"]:
        for eid in ids:
            c = ler_env(EMPRESAS / f"{eid}.env").get("EMPRESA_CNPJ", "")
            if c:
                d["cnpj"] = documentos.fmt_cnpj(c)
                break
    return d


def processos_empresa(ids):
    out = []
    for eid in ids:
        db = EMPRESAS / eid / "bll_monitor.db"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            out += [(r[0], r[1], r[2] or "") for r in con.execute("SELECT processo, promotor, situacao FROM processos")]
            con.close()
        except Exception:  # noqa: BLE001
            pass
    vistos, res = set(), []
    for x in sorted(out, key=lambda x: x[0], reverse=True):
        if (x[0], x[1]) not in vistos:
            vistos.add((x[0], x[1]))
            res.append(x)
    return res


def ctx_doc(q):
    emps = nomes_empresas()
    nome = q.get("emp", "") if q.get("emp", "") in emps else (sorted(emps)[0] if emps else "")
    ctx = dados_doc(nome, emps.get(nome, [])) if nome else {k: "" for k in documentos.CAMPOS}
    proc, _, prom = q.get("proc", "").partition("||")
    ctx["processo"] = q.get("processo", "").strip() or proc
    ctx["orgao"] = q.get("orgao", "").strip() or prom
    ctx["modalidade"] = q.get("modalidade", "").strip() or "Pregão Eletrônico"
    ctx["cidade_data"] = q.get("cidade_data", "").strip() or ctx.get("cidade", "")
    ctx["data"] = q.get("data", "").strip() or documentos.data_extenso()
    return nome, ctx


def pg_documentos(q, aviso=""):
    emps = nomes_empresas()
    if not emps:
        return pagina("Declarações", "<h2>Declarações</h2><p>Cadastre uma empresa primeiro.</p>", "/documentos")
    nome, ctx = ctx_doc(q)
    procs = processos_empresa(emps[nome])
    falta = documentos.faltando(ctx)
    sel_emp = "".join(f'<option value="{e(n)}" {"selected" if n == nome else ""}>{e(n)}</option>' for n in sorted(emps))
    proc_q = q.get("proc", "")
    sel_proc = ('<option value="">— escolha um processo (ou preencha abaixo) —</option>' +
                "".join(f'<option value="{e(p + "||" + o)}" {"selected" if (p + "||" + o) == proc_q else ""}>'
                        f'{e(p)} – {e(o)}{" · " + e(sit) if sit else ""}</option>' for p, o, sit in procs))
    marcadas = set(q.get("ids", "").split(",")) if q.get("ids") else set(documentos.PADRAO)
    leitura = pecas.ler(q.get("leitura", "")) if re.fullmatch(r"[0-9a-f]{14}", q.get("leitura", "")) else None
    bloco_ia, extras_html = "", ""
    if leitura and leitura.get("status") == "gerando":
        bloco_ia = ('<div class="msg">🤖 Lendo o edital para descobrir quais declarações ele exige… (1 a 3 minutos – a página '
                    'atualiza sozinha)</div><script>setTimeout(()=>location.reload(),8000)</script>')
    elif leitura and leitura.get("status") == "erro":
        bloco_ia = f'<div class="msg erro">A IA não conseguiu ler o edital: {e(leitura.get("erro", ""))}</div>'
    elif leitura and leitura.get("status") == "pronto":
        r = leitura.get("resultado") or {}
        if not q.get("ids"):
            marcadas = set(r.get("exigidas") or []) | {f"x{i}" for i in range(len(r.get("especificas") or []))}
        bloco_ia = ('<div class="msg">🤖 <b>A IA leu o edital</b> e marcou abaixo as declarações que ele exige'
                    + (f' (veja: {e(r.get("onde"))})' if r.get("onde") else "") + '.'
                    + (f'<br>⚠️ {e(r.get("observacoes"))}' if r.get("observacoes") else "")
                    + f'<br><small>Confira no edital – a IA pode errar.</small> {custo_uso(leitura.get("custo"))}</div>')
        extras_html = "".join(
            f'<label class="dec"><input type="checkbox" name="d" value="x{i}" {"checked" if f"x{i}" in marcadas else ""}> '
            f'<span>📎 <b>Modelo do edital:</b> {e(x.get("titulo") or "Declaração")}</span></label>'
            for i, x in enumerate(r.get("especificas") or []))
    analisados = [a for a in analise.listar() if a.get("status") == "pronto"]
    sel_edital = ('<option value="">— escolha um edital já analisado em Editais (IA) —</option>' +
                  "".join(f'<option value="{e(a["chave"])}">{e((a.get("titulo") or a["chave"])[:110])}</option>' for a in analisados))
    form_ia = ESTILO_IA + f"""<details class="op-perfil" style="margin:10px 0;border:1px solid var(--borda);border-radius:10px;background:var(--card)" {"open" if not leitura else ""}>
<summary style="display:block;cursor:pointer;padding:10px 14px">🤖 <b>Ler o edital com IA</b> e marcar sozinho as declarações que ele exige
(inclusive os modelos próprios dos anexos)</summary>
<form method="post" action="/pecas/declaracoes" enctype="multipart/form-data" style="padding:0 14px 12px">{aviso_custo("declaracoes")}
<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="emp" value="{e(nome)}"><input type="hidden" name="proc" value="{e(proc_q)}">
<div class="duas"><div><label>Edital já analisado</label><select class="campo" name="edital">{sel_edital}</select></div>
<div><label>…ou envie o PDF do edital</label><input type="file" name="arquivo" accept=".pdf,.docx,.zip" multiple></div></div>
<div class="acoes"><button>🤖 Ler o edital</button></div></form></details>"""
    caixas = "".join(
        f'<label class="dec"><input type="checkbox" name="d" value="{i}" {"checked" if i in marcadas else ""}'
        f'{" disabled" if i == "meepp" and ctx.get("porte") not in ("ME", "EPP") else ""}> {e(t)}'
        f'{" <small>(só para ME/EPP – defina o porte abaixo)</small>" if i == "meepp" and ctx.get("porte") not in ("ME", "EPP") else ""}</label>'
        for i, t, _ in documentos.DECLARACOES)
    g = lambda k: e(ctx.get(k, ""))  # noqa: E731
    porte_sel = "".join(f'<option value="{k}" {"selected" if ctx.get("porte") == k else ""}>{r}</option>'
                        for k, r in (("", "Demais (não é ME/EPP)"), ("ME", "Microempresa (ME)"), ("EPP", "Empresa de Pequeno Porte (EPP)")))
    aviso_falta = (f'<div class="msg erro">Para gerar, preencha em “Dados da empresa”: <b>{e(", ".join(falta))}</b>.</div>'
                   if falta else "")
    corpo = f"""<style>.dec{{display:flex;gap:8px;align-items:flex-start;font-weight:400;margin:6px 0}}
.dec input{{margin-top:3px}}.duas{{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}}
@media(max-width:700px){{.duas{{grid-template-columns:1fr}}}}</style>
<h2>📄 Documentos</h2>
<p class="sub">Modelos padrão da Lei 14.133/2021 preenchidos com os dados da empresa e do processo. Saem prontos para assinar
(Word ou PDF). <b>Se o edital trouxer modelo próprio nos anexos, use o do edital</b> – e confira sempre antes de enviar.</p>
{abas_doc("dec")}{aviso_falta}{bloco_ia}{form_ia}
<div class="form" style="max-width:none">
<form method="get" action="/documentos/ver" target="_blank" id="fdoc">
<div class="duas"><div><label>Empresa</label>
<select class="campo" name="emp" onchange="location.href='/documentos?emp='+encodeURIComponent(this.value)">{sel_emp}</select></div>
<div><label>Processo <small>(lido pelo monitor)</small></label><select class="campo" name="proc">{sel_proc}</select></div>
<div><label>Órgão <small>(vazio = o do processo escolhido)</small></label><input class="campo" name="orgao" value="{e(q.get('orgao', ''))}" placeholder="ex.: MUNICÍPIO DE RUY BARBOSA"></div>
<div><label>Nº do processo/edital <small>(vazio = o do processo escolhido)</small></label><input class="campo" name="processo" value="{e(q.get('processo', ''))}" placeholder="ex.: 033/2026"></div>
<div><label>Modalidade</label><input class="campo" name="modalidade" value="{e(q.get('modalidade', 'Pregão Eletrônico'))}"></div>
<div><label>Local e data</label><div class="linha" style="gap:8px"><input class="campo" name="cidade_data" value="{g('cidade_data')}" placeholder="Cidade/UF">
<input class="campo" name="data" value="{g('data')}"></div></div></div>
<h3>Quais declarações</h3>
<div class="linha" style="gap:8px;margin-bottom:6px"><button type="button" class="sec" onclick="marcar(true)">Marcar todas</button>
<button type="button" class="sec" onclick="marcar(false)">Desmarcar todas</button></div>
{caixas}{extras_html}
<input type="hidden" name="ids" id="ids"><input type="hidden" name="leitura" value="{e(q.get('leitura', ''))}">
<label class="dec" style="margin-top:12px"><input type="checkbox" name="unificada" value="1"> <b>Juntar tudo numa “Declaração unificada”</b> (um documento só, com os itens a, b, c…)</label>
<div class="acoes"><button {"disabled" if falta else ""} onclick="return enviar('/documentos/ver')">👁 Visualizar / imprimir (PDF)</button>
<button class="sec" {"disabled" if falta else ""} onclick="return enviar('/documentos/docx')">⬇ Baixar Word (.docx)</button></div>
</form></div>
<h2 style="margin-top:22px">Dados da empresa <small class="sub">(usados em todas as declarações de {e(nome)})</small></h2>
<div class="form" style="max-width:none">
<form method="post" action="/documentos/dados"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="emp" value="{e(nome)}">
<div class="linha" style="gap:8px;align-items:flex-end"><div style="flex:1"><label>CNPJ</label>
<input class="campo" name="cnpj" value="{g('cnpj')}" placeholder="00.000.000/0000-00"></div>
<button formaction="/documentos/cnpj" class="sec">🔎 Preencher pelo CNPJ (Receita)</button></div>
<div class="duas"><div><label>Razão social</label><input class="campo" name="razao" value="{g('razao')}"></div>
<div><label>Porte</label><select class="campo" name="porte">{porte_sel}</select></div></div>
<label>Endereço completo da sede</label><input class="campo" name="endereco" value="{g('endereco')}" placeholder="Rua, nº, bairro, cidade/UF, CEP">
<div class="duas"><div><label>Cidade/UF da sede</label><input class="campo" name="cidade" value="{g('cidade')}" placeholder="Feira de Santana/BA"></div>
<div><label>Cargo do representante</label><input class="campo" name="rep_cargo" value="{g('rep_cargo')}" placeholder="Sócio-Administrador"></div>
<div><label>Nome do representante legal</label><input class="campo" name="rep_nome" value="{g('rep_nome')}"></div>
<div><label>CPF do representante</label><input class="campo" name="rep_cpf" value="{g('rep_cpf')}" placeholder="000.000.000-00"></div></div>
<p class="sub">O CPF não vem da Receita: preencha à mão. Os dados ficam só no seu servidor.</p>
<div class="acoes"><button>Salvar dados da empresa</button></div></form></div>
<script>
function marcar(v){{document.querySelectorAll('#fdoc input[name=d]').forEach(x=>{{if(!x.disabled)x.checked=v;}});}}
function enviar(acao){{const f=document.getElementById('fdoc');
 const ids=[...f.querySelectorAll('input[name=d]:checked')].map(x=>x.value);
 if(!ids.length){{alert('Marque pelo menos uma declaração.');return false;}}
 document.getElementById('ids').value=ids.join(','); f.querySelectorAll('input[name=d]').forEach(x=>x.disabled=true);
 f.action=acao; f.target=acao.endsWith('docx')?'_self':'_blank'; f.submit();
 setTimeout(()=>f.querySelectorAll('input[name=d]').forEach(x=>{{x.disabled=x.value==='meepp'&&{'true' if ctx.get('porte') not in ('ME', 'EPP') else 'false'};}}),300);
 return false;}}
</script>"""
    return pagina("Declarações", corpo, "/documentos", aviso)


def gerar_docs(q):
    nome, ctx = ctx_doc(q)
    ids = [x for x in q.get("ids", "").split(",") if x]
    extras = []
    if re.fullmatch(r"[0-9a-f]{14}", q.get("leitura", "")):
        esp = ((pecas.ler(q["leitura"]) or {}).get("resultado") or {}).get("especificas") or []
        extras = [(x.get("titulo", ""), x.get("texto", "")) for i, x in enumerate(esp) if f"x{i}" in ids]
    return ctx, documentos.montar(ids, ctx, q.get("unificada") == "1", extras)


def abas_doc(atual):
    return ('<div class="acoes" style="margin:6px 0 12px">'
            f'<a class="btn {"" if atual == "dec" else "sec"}" href="/documentos">📄 Declarações</a>'
            f'<a class="btn {"" if atual == "pec" else "sec"}" href="/pecas">⚖️ Peças com IA (recurso, contrarrazões…)</a>'
            f'<a class="btn {"" if atual == "cer" else "sec"}" href="/certidoes">📑 Certidões</a></div>')


def pg_pecas(q, aviso=""):
    emps = nomes_empresas()
    if not emps:
        return pagina("Peças", "<h2>Peças com IA</h2><p>Cadastre uma empresa primeiro.</p>", "/documentos")
    nome, ctx = ctx_doc(q)
    procs = processos_empresa(emps[nome])
    proc_q = q.get("proc", "")
    sel_emp = "".join(f'<option value="{e(n)}" {"selected" if n == nome else ""}>{e(n)}</option>' for n in sorted(emps))
    sel_proc = ('<option value="">— sem processo do monitor (preencha órgão e número) —</option>' +
                "".join(f'<option value="{e(p + "||" + o)}" {"selected" if (p + "||" + o) == proc_q else ""}>'
                        f'{e(p)} – {e(o)}{" · " + e(sit) if sit else ""}</option>' for p, o, sit in procs))
    tipo_q = q.get("tipo", "recurso")
    sel_tipo = "".join(f'<option value="{k}" {"selected" if k == tipo_q else ""}>{e(t)}</option>' for k, (t, _) in pecas.TIPOS.items())
    analisados = [a for a in analise.listar() if a.get("status") == "pronto"]
    sel_edital = ('<option value="">— nenhum (usar só o histórico e os pontos) —</option>' +
                  "".join(f'<option value="{e(a["chave"])}">{e((a.get("titulo") or a["chave"])[:110])}</option>' for a in analisados))
    falta = [x for x in documentos.faltando(ctx) if x in ("razão social", "CNPJ")]
    linhas = "".join(
        f'<tr><td><a href="/pecas/{e(x["id"])}">{e(pecas.TIPOS.get(x.get("tipo"), (x.get("tipo"), ""))[0])}</a>'
        f'<div class="sub">{e(x.get("empresa", ""))} · {e(x.get("titulo", ""))}</div></td>'
        f'<td>{ {"pronto": "✅ Pronta", "gerando": "⏳ Gerando", "erro": "❌ Erro"}.get(x.get("status"), "")}</td>'
        f'<td class="ocultar">{e(x.get("fim") or x.get("inicio") or "")}</td></tr>'
        for x in pecas.listar() if x.get("tipo") != "declaracoes")
    corpo = f"""<style>.duas{{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}}@media(max-width:700px){{.duas{{grid-template-columns:1fr}}}}</style>
{ESTILO_IA}<h2>📄 Documentos</h2>{abas_doc("pec")}{aviso_custo("peca")}
<p class="sub">A IA escreve um <b>rascunho</b> usando o edital, o histórico que o monitor registrou no processo (chat, registros
da sessão, classificação, diligências) e os pontos que você indicar. <b>Revise sempre</b> (ou peça ao advogado) antes de
protocolar – a IA marca com [CONFERIR] o que não encontrou. O sistema não envia nada à plataforma.</p>
{f'<div class="msg erro">Antes, preencha os dados da empresa (razão social e CNPJ) em <a href="/documentos?emp={e(urllib.parse.quote(nome))}">Declarações → Dados da empresa</a>.</div>' if falta else ''}
<div class="form" style="max-width:none">
<form method="post" action="/pecas/gerar" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{CSRF}">
<div class="duas"><div><label>Tipo de peça</label><select class="campo" name="tipo">{sel_tipo}</select></div>
<div><label>Empresa</label><select class="campo" name="emp" onchange="location.href='/pecas?emp='+encodeURIComponent(this.value)">{sel_emp}</select></div>
<div><label>Processo <small>(o histórico dele vai para a IA)</small></label><select class="campo" name="proc">{sel_proc}</select></div>
<div><label>Edital <small>(já analisado em Editais (IA))</small></label><select class="campo" name="edital">{sel_edital}</select></div>
<div><label>Órgão <small>(vazio = o do processo)</small></label><input class="campo" name="orgao" placeholder="ex.: MUNICÍPIO DE RUY BARBOSA"></div>
<div><label>Nº do processo/edital <small>(vazio = o do processo)</small></label><input class="campo" name="processo" placeholder="ex.: 033/2026"></div></div>
<label>Documentos de apoio <small>(opcional: recurso do concorrente, ata, pedido de diligência, planilha de custos, o PDF do edital… até 18 MB)</small></label>
<input type="file" name="arquivo" multiple accept=".pdf,.docx,.zip,.txt">
<label>O que você quer defender ou pedir <small>(quanto mais claro, melhor a peça)</small></label>
<textarea class="campo" name="pontos" rows="5" style="max-width:100%" placeholder="ex.: A LUCINEIDE (1ª no L3) apresentou atestado que não comprova a quantidade mínima do item 9.4.2; pedir a inabilitação dela e a convocação da VC (2ª)."></textarea>
<div class="acoes"><button {"disabled" if falta else ""}>🤖 Gerar rascunho</button></div></form></div>
<h2 style="margin-top:22px">Peças geradas</h2>
<table><tr><th>Peça</th><th>Situação</th><th class="ocultar">Quando</th></tr>{linhas or '<tr><td colspan="3">Nenhuma ainda.</td></tr>'}</table>
<p class="sub">IA: Google Gemini (a mesma chave da análise de editais).</p>"""
    return pagina("Peças com IA", corpo, "/documentos", aviso)


def pg_peca(pid):
    d = pecas.ler(pid)
    if not d:
        return pagina("Peça", "<h2>Peça não encontrada</h2>", "/documentos")
    titulo = pecas.TIPOS.get(d.get("tipo"), (d.get("tipo"), ""))[0]
    topo = f'<h2>⚖️ {e(titulo)}</h2><p class="sub">{e(d.get("empresa", ""))} · {e(d.get("titulo", ""))}</p>{abas_doc("pec")}'
    if d.get("status") == "gerando":
        return pagina("Gerando", topo + f'<div class="msg">🤖 Escrevendo o rascunho desde {e(d.get("inicio", ""))}… (1 a 3 minutos)</div>'
                      '<script>setTimeout(()=>location.reload(),8000)</script>', "/documentos")
    if d.get("status") == "erro":
        return pagina("Erro", topo + f'<div class="msg erro">{e(d.get("erro", ""))}</div><div class="acoes">'
                      + botao(f"/pecas/{pid}/refazer", "Tentar de novo") + '<a class="btn sec" href="/pecas">Voltar</a></div>', "/documentos")
    curta = d.get("tipo") == "intencao"
    notas = analise.md_html(d.get("notas", "")) if d.get("notas") else ""
    corpo = topo + f"""
{ESTILO_IA}<div class="msg">✅ Rascunho pronto ({e(d.get("fim", ""))} · {e(d.get("modelo", ""))}) {custo_uso(d.get("custo"))}. <b>Revise tudo</b> – procure por <b>[CONFERIR]</b>.
Arquivos lidos: {e(", ".join(d.get("arquivos") or []) or "—")}</div>
{f'<details open class="form" style="max-width:none;margin-bottom:12px"><summary><b>📝 Notas para revisão</b> (não vão no documento)</summary>{notas}</details>' if notas else ''}
<div class="form" style="max-width:none"><form method="post" action="/pecas/{pid}/salvar"><input type="hidden" name="csrf" value="{CSRF}">
<label>Texto da peça <small>(pode editar aqui; “# ” = título, “## ” = seção)</small>
{'<span id="cont" class="sub"></span>' if curta else ''}</label>
<textarea class="campo" name="md" id="md" rows="{8 if curta else 30}" style="max-width:100%;font-family:Georgia,serif;font-size:14px;line-height:1.5">{e(d.get("md", ""))}</textarea>
<div class="acoes"><button>💾 Salvar alterações</button>
{'<button type="button" class="sec" onclick="navigator.clipboard.writeText(document.getElementById(&quot;md&quot;).value);this.textContent=&quot;Copiado!&quot;">📋 Copiar para o chat</button>' if curta else
 f'<a class="btn sec" target="_blank" href="/pecas/{pid}/ver">👁 Visualizar / imprimir (PDF)</a><a class="btn sec" href="/pecas/{pid}/docx">⬇ Baixar Word</a>'}
</div></form>
<div class="acoes">{botao(f"/pecas/{pid}/refazer", "🔄 Gerar de novo", "sec", "Gerar um novo rascunho? O texto atual será substituído.")}
<a class="btn sec" href="/pecas">Voltar</a></div></div>
{'<script>const t=document.getElementById("md"),c=document.getElementById("cont");function n(){{c.textContent=" – "+t.value.length+" caracteres";}}t.oninput=n;n();</script>' if curta else ''}"""
    return pagina(titulo, corpo, "/documentos")


def pg_peca_ver(pid):
    d = pecas.ler(pid) or {}
    blocos = pecas.blocos_md(d.get("md", ""))
    titulo = pecas.TIPOS.get(d.get("tipo"), ("Peça", ""))[0]
    corpo = documentos.html([(titulo, blocos)], {})
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titulo)}</title><style>
body{{background:#e9edf0;margin:0;font-family:Arial,Helvetica,sans-serif;color:#111}}
.barra{{position:sticky;top:0;background:#1b3a2a;color:#fff;padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.barra button,.barra a{{background:#fff;color:#1b3a2a;border:0;border-radius:6px;padding:8px 14px;font-weight:700;cursor:pointer;text-decoration:none;font-size:14px}}
.folha{{background:#fff;width:210mm;min-height:297mm;margin:16px auto;padding:30mm 20mm 20mm 30mm;box-sizing:border-box;
 box-shadow:0 2px 10px rgba(0,0,0,.15);font-size:12pt;line-height:1.6;outline:none}}
.folha h2{{text-align:center;font-size:13pt;margin:10px 0 18px}}.folha h3{{font-size:12pt;margin:16px 0 6px}}
.folha .corpo{{text-align:justify;text-indent:12.5mm;margin:0 0 8px}}.folha .item{{text-align:justify;margin:4px 0 4px 8mm}}
@media print{{body{{background:#fff}}.barra{{display:none}}.folha{{margin:0;box-shadow:none;width:auto;min-height:0;padding:0}}
 @page{{size:A4;margin:30mm 20mm 20mm 30mm}}}}
@media(max-width:820px){{.folha{{width:auto;margin:10px;padding:18px}}}}
</style></head><body><div class="barra"><button onclick="window.print()">🖨 Imprimir / Salvar em PDF</button>
<a href="/pecas/{e(pid)}/docx">⬇ Baixar Word</a><span style="font-size:13px">Rascunho – revise antes de protocolar.</span></div>
{corpo}</body></html>"""


def pg_documentos_ver(q):
    ctx, docs = gerar_docs(q)
    falta = documentos.faltando(ctx)
    if falta or not docs:
        return pagina("Declarações", f'<h2>Não deu para gerar</h2><p>Faltam: {e(", ".join(falta) or "declarações marcadas")}.</p>'
                      '<p><a class="btn" href="/documentos">Voltar</a></p>', "/documentos")
    url_docx = "/documentos/docx?" + urllib.parse.urlencode(q)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Declarações – {e(ctx.get('razao', ''))}</title><style>
body{{background:#e9edf0;margin:0;font-family:Arial,Helvetica,sans-serif;color:#111}}
.barra{{position:sticky;top:0;background:#1b3a2a;color:#fff;padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;z-index:5}}
.barra button,.barra a{{background:#fff;color:#1b3a2a;border:0;border-radius:6px;padding:8px 14px;font-weight:700;cursor:pointer;text-decoration:none;font-size:14px}}
.barra span{{font-size:13px;opacity:.9}}
.folha{{background:#fff;width:210mm;min-height:297mm;margin:16px auto;padding:30mm 20mm 20mm 30mm;box-sizing:border-box;
 box-shadow:0 2px 10px rgba(0,0,0,.15);font-size:11pt;line-height:1.6;outline:none}}
.folha h2{{text-align:center;font-size:12pt;margin:18px 0 22px}}.folha .ref{{margin:0 0 12px}}
.folha .corpo{{text-align:justify;text-indent:12.5mm}}.folha .item{{text-align:justify;margin:6px 0}}
.folha .data{{text-align:right;margin-top:28px}}.folha .ass{{text-align:center;margin-top:40px}}
@media print{{body{{background:#fff}}.barra{{display:none}}.folha{{margin:0;box-shadow:none;page-break-after:always;width:auto;min-height:0;padding:0}}
 @page{{size:A4;margin:30mm 20mm 20mm 30mm}}}}
@media(max-width:820px){{.folha{{width:auto;margin:10px;padding:18px}}}}
</style></head><body>
<div class="barra"><button onclick="window.print()">🖨 Imprimir / Salvar em PDF</button><a href="{e(url_docx)}">⬇ Baixar Word</a>
<span>Dica: clique no texto para corrigir algo antes de imprimir. No Chrome, escolha “Salvar como PDF”.</span></div>
{documentos.html(docs, ctx)}</body></html>"""


# ------------------------------------------------------------------ agenda de prazos
COR_PRAZO = {"atrasado": "#c62828", "hoje": "#e65100", "semana": "#f9a825", "depois": "#2e7d32", "feito": "#90a4ae"}


def link_chat(it):
    if not it.get("eid") or not it.get("processo"):
        return ""
    return "/mensagens?" + urllib.parse.urlencode({"eid": it["eid"], "processo": it["processo"], "promotor": it.get("promotor", "")})


def pg_agenda(q, aviso=""):
    d = agenda.prazos()
    agora = datetime.now()
    emp_f = q.get("emp", "")
    itens = [(pid, it) for pid, it in d["itens"].items()
             if not emp_f or emp_f in [x.strip().upper() for x in it.get("empresa", "").split(",")]]
    grupos = {"atrasado": [], "hoje": [], "semana": [], "depois": [], "feito": []}
    for pid, it in itens:
        grupos[agenda.situacao_prazo(it, agora)].append((pid, it))
    for g in grupos.values():
        g.sort(key=lambda x: x[1]["quando"])
    grupos["feito"] = sorted(grupos["feito"], key=lambda x: x[1]["quando"], reverse=True)[:30]
    nomes = sorted(nomes_empresas())

    def cartao(pid, it):
        st = agenda.situacao_prazo(it, agora)
        qd = datetime.fromisoformat(it["quando"])
        dia_todo = qd.hour == 23 and qd.minute == 59
        quando = qd.strftime("%d/%m/%Y") + ("" if dia_todo else qd.strftime(" %H:%M"))
        semana = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"][qd.weekday()]
        lc = link_chat(it)
        hora_v = "" if dia_todo else qd.strftime("%H:%M")
        acoes = botao(f"/agenda/{pid}/feito", "✓ Concluído") if it.get("status") == "aberto" else botao(f"/agenda/{pid}/reabrir", "↺ Reabrir", "sec")
        acoes += (f'<details class="ag-ed"><summary class="btn sec">✎ Mudar data</summary><form method="post" action="/agenda/{pid}/data" class="linha" style="gap:6px;margin-top:6px">'
                  f'<input type="hidden" name="csrf" value="{CSRF}"><input class="campo" type="date" name="data" value="{qd:%Y-%m-%d}" required>'
                  f'<input class="campo" type="time" name="hora" value="{hora_v}"><button>Salvar</button></form></details>')
        acoes += botao(f"/agenda/{pid}/excluir", "🗑", "sec", "Apagar este prazo?")
        falta = e(agenda.falta_txt(qd, agora)) if st != "feito" else "concluído"
        tags = ('<span class="tag aviso">estimado</span>' if it.get("estimado") else "") + \
               ('<span class="tag parado">detectado no chat</span>' if it.get("origem") == "auto" else "")
        orgao = " · " + e(it.get("promotor", "")) if it.get("promotor") else ""
        chat = f' · <a href="{e(lc)}">abrir o chat do processo</a>' if lc else ""
        obs = (f'<details class="sub" style="margin-top:4px"><summary>trecho / observação</summary><div class="txt">{e(it.get("obs", ""))}</div></details>'
               if it.get("obs") else "")
        return (f'<div class="ag" style="border-left-color:{COR_PRAZO[st]}"><div class="ag-q"><b>{e(quando)}</b> <span class="sub">({semana})</span>'
                f'<span class="tag" style="background:{COR_PRAZO[st]};color:#fff">{falta}</span>{tags}</div>'
                f'<div class="nome">{e(it.get("titulo", ""))}</div><div class="sub">{e(it.get("empresa", ""))}{orgao}{chat}</div>{obs}'
                f'<div class="acoes" style="margin-top:6px">{acoes}</div></div>')

    blocos = ""
    for k, rot in (("atrasado", "❗ Atrasados"), ("hoje", "🔴 Hoje"), ("semana", "🟠 Próximos 7 dias"), ("depois", "🟢 Depois"),
                   ("feito", "✅ Concluídos (últimos 30)")):
        if grupos[k] or k in ("hoje", "semana"):
            lista = "".join(cartao(pid, it) for pid, it in grupos[k]) or '<p class="sub">Nada aqui.</p>'
            aberto = "" if k == "feito" else "open"
            blocos += f'<details {aberto} class="ag-grupo"><summary><h3 style="display:inline">{rot} ({len(grupos[k])})</h3></summary>{lista}</details>'
    sel_emp = ('<select class="campo" style="max-width:260px" onchange="location.href=\'/agenda?emp=\'+encodeURIComponent(this.value)">'
               '<option value="">Todas as empresas</option>' +
               "".join(f'<option value="{e(n)}" {"selected" if n == emp_f else ""}>{e(n)}</option>' for n in nomes) + "</select>")
    opc_emp = "".join(f'<option value="{e(n)}">{e(n)}</option>' for n in nomes)
    corpo = f"""<style>.ag{{background:var(--card);border:1px solid var(--borda);border-left:6px solid;border-radius:10px;padding:10px 14px;margin:8px 0}}
.ag-q{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.ag-grupo summary{{cursor:pointer;margin:14px 0 4px}}
.ag-ed{{display:inline-block}}.ag-ed summary{{list-style:none;display:inline-block}}.ag .acoes form{{display:inline}}
.kp-ag{{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}}.kp-ag div{{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:8px 14px}}
.kp-ag b{{font-size:22px;display:block}}</style>
<h2>📅 Agenda de prazos</h2>
<p class="sub">Prazos detectados sozinhos nas mensagens do pregoeiro, diligências e registros (ex.: “prazo de 2 horas”, “reabertura
amanhã às 10h”, “5 dias úteis para regularização”) e os que você cadastrar. Avisos no Telegram: quando aparece, 24 h antes,
2 h antes (também no ntfy) e quando vence. Dias úteis contam feriados nacionais – <b>confira feriados municipais</b>.</p>
<div class="kp-ag"><div><b style="color:{COR_PRAZO['atrasado']}">{len(grupos['atrasado'])}</b>atrasados</div>
<div><b style="color:{COR_PRAZO['hoje']}">{len(grupos['hoje'])}</b>hoje</div>
<div><b style="color:#b8860b">{len(grupos['semana'])}</b>próximos 7 dias</div><div><b>{len(grupos['depois'])}</b>depois</div></div>
<div class="acoes">{sel_emp}<input class="campo ag-busca" type="search" placeholder="🔍 Pesquisar prazo, processo, órgão, empresa…"
 oninput="buscarPrazo(this.value)" style="flex:1;min-width:240px"><span class="sub" id="ag-cont"></span></div>
<script>
function semAcento(t){{return (t||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();}}
function buscarPrazo(v){{const t=semAcento(v).trim(); let total=0;
 document.querySelectorAll('.ag-grupo').forEach(g=>{{let n=0;
  g.querySelectorAll('.ag').forEach(c=>{{if(c.dataset.t===undefined)c.dataset.t=semAcento(c.textContent);
   const ok=!t||t.split(/\\s+/).every(p=>c.dataset.t.includes(p)); c.style.display=ok?'':'none'; if(ok)n++;}});
  const h=g.querySelector('summary h3'); if(h){{if(h.dataset.r===undefined)h.dataset.r=h.textContent.replace(/\\s*\\(\\d+\\)\\s*$/,'');
   h.textContent=h.dataset.r+' ('+n+')';}} if(t&&n)g.open=true; total+=n;}});
 document.getElementById('ag-cont').textContent=t?total+' encontrado(s)':'';
 try{{sessionStorage.setItem('agBusca',v);}}catch(e){{}}}}
document.addEventListener('DOMContentLoaded',()=>{{try{{const v=sessionStorage.getItem('agBusca');
 if(v){{const i=document.querySelector('.ag-busca'); i.value=v; buscarPrazo(v);}}}}catch(e){{}}}});
</script>
<details class="form" style="max-width:none;margin:10px 0"><summary><b>➕ Novo prazo</b></summary>
<form method="post" action="/agenda/novo"><input type="hidden" name="csrf" value="{CSRF}">
<label>O quê</label><input class="campo" name="titulo" required placeholder="ex.: Enviar proposta ajustada – PE 033/2026">
<div class="linha" style="gap:8px;flex-wrap:wrap"><div><label>Data</label><input class="campo" type="date" name="data" required></div>
<div><label>Hora <small>(vazio = fim do dia)</small></label><input class="campo" type="time" name="hora"></div>
<div style="flex:1;min-width:200px"><label>Empresa</label><select class="campo" name="empresa"><option value="">—</option>{opc_emp}</select></div></div>
<label>Processo / órgão <small>(opcional)</small></label><input class="campo" name="processo" placeholder="ex.: 033/2026 – Município de Ruy Barbosa">
<label>Observação</label><textarea class="campo" name="obs" rows="2" style="max-width:100%"></textarea>
<div class="acoes"><button>Salvar prazo</button></div></form></details>
{blocos}"""
    return pagina("Agenda", corpo, "/agenda", aviso, recarregar=True)


# ------------------------------------------------------------------ certidões
COR_CERT = {"ok": ("#2e7d32", "✅ válida"), "vence": ("#ef6c00", "🟠 vence em breve"), "vencida": ("#c62828", "❌ vencida"),
            "sem": ("#90a4ae", "sem validade")}
ESSENCIAIS = ("federal", "fgts", "trabalhista", "estadual", "municipal", "falencia")


def _dt_br(iso):
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return "—"


def pg_certidoes(q, aviso=""):
    emps = sorted(nomes_empresas())
    dados = agenda.certidoes()["itens"]
    emp_f = q.get("emp", "")
    hoje = date.today()
    blocos = ""
    links = {k: u for k, _, u in agenda.TIPOS_CERT if u}
    opc_tipo = "".join(f'<option value="{k}">{e(n)}</option>' for k, n, _ in agenda.TIPOS_CERT)
    for nome in [n for n in emps if not emp_f or n == emp_f]:
        minhas = sorted([(cid, c) for cid, c in dados.items() if c.get("empresa") == nome],
                        key=lambda x: (x[1].get("validade") or "9999"))
        linhas = ""
        for cid, c in minhas:
            st, dias = agenda.status_cert(c, hoje)
            cor, rot = COR_CERT[st]
            prazo = "" if dias is None else f"{-dias} dia(s) atrás" if dias < 0 else "hoje" if dias == 0 else f"{dias} dia(s)"
            arq = f'<a href="/certidoes/{cid}/arquivo" target="_blank">📄 PDF</a>' if agenda.arquivo_cert(cid) else '<span class="sub">sem PDF</span>'
            emitir = f' · <a href="{e(links[c.get("tipo")])}" target="_blank" rel="noopener">emitir nova</a>' if c.get("tipo") in links else ""
            editar = (f'<details><summary class="sub" style="cursor:pointer">✎ editar / trocar PDF</summary>'
                      f'<form method="post" action="/certidoes/salvar" enctype="multipart/form-data" class="linha" style="gap:6px;flex-wrap:wrap;margin-top:6px">'
                      f'<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="cid" value="{cid}"><input type="hidden" name="empresa" value="{e(nome)}">'
                      f'<input type="hidden" name="tipo" value="{e(c.get("tipo", ""))}">'
                      f'<input class="campo" name="numero" value="{e(c.get("numero", ""))}" placeholder="nº" style="max-width:160px">'
                      f'<label class="sub">emissão <input class="campo" type="date" name="emissao" value="{e(c.get("emissao", ""))}"></label>'
                      f'<label class="sub">validade <input class="campo" type="date" name="validade" value="{e(c.get("validade", ""))}"></label>'
                      f'<input type="file" name="arquivo" accept=".pdf"><button>Salvar</button></form></details>')
            obs = f'<div class="sub">{e(c.get("obs", ""))}</div>' if c.get("obs") else ""
            linhas += (f'<tr><td><b>{e(agenda.NOME_CERT.get(c.get("tipo"), c.get("tipo", "")))}</b>{obs}{editar}</td>'
                       f'<td class="ocultar">{e(c.get("numero", "") or "—")}<div class="sub">emitida {_dt_br(c.get("emissao"))}</div></td>'
                       f'<td><b>{_dt_br(c.get("validade"))}</b></td><td><span class="tag" style="background:{cor};color:#fff">{rot}</span>'
                       f'<div class="sub">{prazo}</div></td><td>{arq}{emitir}</td>'
                       f'<td>{botao(f"/certidoes/{cid}/excluir", "🗑", "sec", "Apagar esta certidão?")}</td></tr>')
        tem = {c.get("tipo") for _, c in minhas if agenda.status_cert(c, hoje)[0] != "vencida"}
        faltam = [k for k in ESSENCIAIS if k not in tem]
        aviso_falta = ("<p class='sub'>Sem certidão válida cadastrada: " +
                       ", ".join(f'<b>{e(agenda.NOME_CERT[k])}</b>' + (f' (<a href="{e(links[k])}" target="_blank" rel="noopener">emitir</a>)' if k in links else "")
                                 for k in faltam) + "</p>") if faltam else ""
        n_venc = sum(1 for _, c in minhas if agenda.status_cert(c, hoje)[0] in ("vence", "vencida"))
        atencao = f'<span class="tag erro">{n_venc} precisa(m) de atenção</span>' if n_venc else ""
        vazio = '<tr><td colspan="6">Nenhuma certidão cadastrada.</td></tr>'
        blocos += (f'<div class="form" style="max-width:none;margin-bottom:16px">'
                   f'<div class="linha" style="justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px"><h3 style="margin:0">{e(nome)} {atencao}</h3>'
                   f'<a class="btn sec" href="/certidoes/pacote?emp={e(urllib.parse.quote(nome))}">⬇ Pacote de habilitação (ZIP com as válidas)</a></div>'
                   f'{aviso_falta}<table><tr><th>Certidão</th><th class="ocultar">Número</th><th>Validade</th><th>Situação</th><th>Arquivo</th><th></th></tr>'
                   f'{linhas or vazio}</table></div>')
    opc_emp = "".join(f'<option value="{e(n)}" {"selected" if n == emp_f else ""}>{e(n)}</option>' for n in emps)
    sel_emp = ('<select class="campo" style="max-width:260px" onchange="location.href=\'/certidoes?emp=\'+encodeURIComponent(this.value)">'
               '<option value="">Todas as empresas</option>' +
               "".join(f'<option value="{e(n)}" {"selected" if n == emp_f else ""}>{e(n)}</option>' for n in emps) + "</select>")
    corpo = f"""<h2>📄 Documentos</h2>{abas_doc("cer")}
<p class="sub">Guarde as certidões de cada empresa com a validade. O sistema avisa no Telegram 15, 7, 3 e 1 dia antes de vencer
e quando vence. Ao enviar o PDF, ele tenta ler sozinho o tipo, o número e as datas – confira depois de salvar.
As certidões são emitidas por você nos sites oficiais (eles têm captcha).</p>
<div class="acoes">{sel_emp}</div>
<details class="form" style="max-width:none;margin:10px 0" open><summary><b>➕ Adicionar certidão</b></summary>
<form method="post" action="/certidoes/salvar" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{CSRF}">
<div class="linha" style="gap:8px;flex-wrap:wrap"><div style="flex:1;min-width:220px"><label>Empresa</label><select class="campo" name="empresa">{opc_emp}</select></div>
<div style="flex:1;min-width:220px"><label>PDF da certidão <small>(lê as datas sozinho)</small></label><input type="file" name="arquivo" accept=".pdf"></div></div>
<div class="linha" style="gap:8px;flex-wrap:wrap"><div style="flex:1;min-width:200px"><label>Tipo <small>(vazio = descobrir pelo PDF)</small></label>
<select class="campo" name="tipo"><option value="">— descobrir pelo PDF —</option>{opc_tipo}</select></div>
<div><label>Número</label><input class="campo" name="numero"></div>
<div><label>Emissão</label><input class="campo" type="date" name="emissao"></div>
<div><label>Validade <small>(vazio = ler do PDF)</small></label><input class="campo" type="date" name="validade"></div></div>
<label>Observação</label><input class="campo" name="obs" placeholder="ex.: certidão municipal de Feira de Santana">
<div class="acoes"><button>Salvar certidão</button></div></form></details>
{blocos}"""
    return pagina("Certidões", corpo, "/documentos", aviso)


CAT_DOC = {"declaracao": "Declaração", "proposta": "Proposta/técnico", "outra": "Outro"}


def html_habilitacao(ch, q):
    """'Posso participar?': documentos exigidos (lidos pela IA) x certidões cadastradas da empresa."""
    jobs = [x for x in pecas.listar(200) if x.get("tipo") == "habilitacao" and x.get("edital") == ch]
    job = jobs[0] if jobs else None
    cab = ESTILO_IA + '<h2 id="hab" style="margin-top:24px">✅ Posso participar?</h2>' + aviso_custo("habilitacao")
    botao_ler = (f'<form method="post" action="/analise/{ch}/habilitacao" style="display:inline"><input type="hidden" name="csrf" value="{CSRF}">'
                 f'<button {"class=sec" if job else ""}>🤖 {"Ler de novo" if job else "Ver os documentos exigidos e comparar com as certidões"}</button></form>')
    if not job:
        return cab + f'<p class="sub">A IA lista os documentos de habilitação do edital e o sistema confere com as certidões cadastradas de cada empresa.</p>{botao_ler}'
    if job.get("status") == "gerando":
        return cab + '<div class="msg">🤖 Lendo os documentos exigidos… (1 a 3 minutos)</div><script>setTimeout(()=>location.reload(),8000)</script>'
    if job.get("status") == "erro":
        return cab + f'<div class="msg erro">{e(job.get("erro", ""))}</div>{botao_ler}'
    r = job.get("resultado") or {}
    emps = sorted(nomes_empresas())
    emp = q.get("emp", "") if q.get("emp", "") in emps else (emps[0] if emps else "")
    try:
        sessao = datetime.fromisoformat(r.get("data_sessao")) if r.get("data_sessao") else None
    except ValueError:
        sessao = None
    ref = (sessao.date() if sessao else date.today())
    certs = [c for c in agenda.certidoes()["itens"].values() if c.get("empresa") == emp]
    links = {k: u for k, _, u in agenda.TIPOS_CERT if u}
    linhas, faltas, atencao = "", [], []
    for doc in r.get("documentos", []):
        cat = doc.get("categoria", "outra")
        if cat in agenda.NOME_CERT and cat != "outra":
            do_tipo = [c for c in certs if c.get("tipo") == cat]
            validas = sorted([c for c in do_tipo if c.get("validade")], key=lambda c: c["validade"], reverse=True)
            if do_tipo and not validas:   # documento sem validade (atestado, contrato social…): basta estar cadastrado
                st = "✅ cadastrado"
            elif not validas:
                st = '❌ não cadastrada' + (f' · <a href="{e(links[cat])}" target="_blank" rel="noopener">emitir</a>' if cat in links else "")
                faltas.append(doc["nome"])
            elif date.fromisoformat(validas[0]["validade"]) < ref:
                st = f'🟠 vence {date.fromisoformat(validas[0]["validade"]):%d/%m} – antes da sessão' if sessao else "🟠 vencida"
                atencao.append(doc["nome"])
            else:
                st = f'✅ válida até {date.fromisoformat(validas[0]["validade"]):%d/%m/%Y}'
        elif cat == "declaracao":
            st = f'📄 <a href="/documentos?emp={e(urllib.parse.quote(emp))}">gerar em Declarações</a>'
        else:
            st = "⚪ conferir"
        linhas += (f'<tr><td>{e(doc.get("nome", ""))}<div class="sub">{e(doc.get("obs", ""))}</div></td>'
                   f'<td class="ocultar">{e(doc.get("item", ""))}</td><td class="ocultar">{e(agenda.NOME_CERT.get(cat) or CAT_DOC.get(cat, cat))}</td><td>{st}</td></tr>')
    if faltas or atencao:
        semaforo = (f'<div class="msg erro">🔴 <b>{e(emp)}</b> ainda não está pronta: '
                    + (f'faltam {len(faltas)} documento(s) ({e(", ".join(faltas[:4]))})' if faltas else "")
                    + ("; " if faltas and atencao else "") + (f'{len(atencao)} vence(m) antes da sessão' if atencao else "") + '.</div>')
    else:
        semaforo = (f'<div class="msg">🟢 As certidões exigidas de <b>{e(emp)}</b> estão cadastradas e válidas'
                    f'{" na data da sessão" if sessao else ""}. Confira os itens “⚪ conferir” e as declarações.</div>')
    sel = "".join(f'<option value="{e(n)}" {"selected" if n == emp else ""}>{e(n)}</option>' for n in emps)
    agendar = ""
    if sessao:
        agendar = (f'<form method="post" action="/agenda/novo" style="display:inline"><input type="hidden" name="csrf" value="{CSRF}">'
                   f'<input type="hidden" name="titulo" value="{e(("🗓️ Sessão – " + (job.get("titulo") or "")).strip(" –")[:150])}">'
                   f'<input type="hidden" name="data" value="{sessao:%Y-%m-%d}"><input type="hidden" name="hora" value="{sessao:%H:%M}">'
                   f'<input type="hidden" name="voltar" value="/agenda?ok=agendado"><button class="sec">📅 Agendar a sessão ({sessao:%d/%m %H:%M})</button></form>')
    return (cab + f'<div class="acoes"><select class="campo" style="max-width:280px" onchange="location.href=\'/analise/{ch}?emp=\'+encodeURIComponent(this.value)+\'#hab\'">{sel}</select>'
            f'{agendar}{botao_ler}</div>{semaforo}'
            + (f'<div class="msg">⚠️ {e(r.get("observacoes"))}</div>' if r.get("observacoes") else "")
            + f'<table><tr><th>Documento exigido</th><th class="ocultar">Item</th><th class="ocultar">Tipo</th><th>{e(emp)}</th></tr>'
            f'{linhas or "<tr><td colspan=4>A IA não encontrou a lista de documentos.</td></tr>"}</table>'
            f'<p class="sub">Lido em {e(job.get("fim", ""))} {custo_uso(job.get("custo"))}. A IA pode errar – confira no edital. Cadastre/atualize as certidões em '
            f'<a href="/certidoes?emp={e(urllib.parse.quote(emp))}">Documentos → Certidões</a>.</p>')


# ------------------------------------------------------------------ concorrentes
def _pct(v):
    return "—" if v is None else (f"+{v:.1f}%" if v > 0 else f"{v:.1f}%").replace(".", ",")


def _rs(v):
    return buscas.moeda(v) if v else "—"


def pg_concorrentes(q):
    emp = q.get("emp", "")
    dados = concorrentes.analisar(emp)
    r, lista = dados["resumo"], dados["concorrentes"]
    sel = ('<select class="campo" style="max-width:280px" onchange="location.href=\'/concorrentes?emp=\'+encodeURIComponent(this.value)">'
           '<option value="">Todas as nossas empresas</option>' +
           "".join(f'<option value="{e(n)}" {"selected" if n == emp else ""}>{e(n)}</option>' for n in dados["empresas"]) + "</select>")
    alvo = q.get("c", "")
    if alvo:
        c = next((x for x in lista if x["chave"] == alvo), None)
        if not c:
            return pagina("Concorrente", "<h2>Concorrente não encontrado</h2>", "/concorrentes")
        det = "".join(
            f'<tr><td>{e(d["processo"])}<div class="sub">{e(d["orgao"][:60])}</div></td><td>L{e(str(int(d["lote"])) if str(d["lote"]).isdigit() else d["lote"])}'
            f'<div class="sub">{e(d["desc"][:70])}</div></td>'
            f'<td><b>{e(str(d["pos"]) + "º" if d["pos"] else "❌")}</b> {_rs(d["valor"])}{"<div class=sub>marca " + e(d["marca"]) + "</div>" if d.get("marca") else ""}</td>'
            f'<td>{e(str(d["nossa_pos"]) + "º" if d["nossa_pos"] else "❌")} {_rs(d["nosso"])}<div class="sub">{e(d["empresa"])}</div></td>'
            f'<td>{_pct((d["valor"] - d["nosso"]) / d["nosso"] * 100 if d.get("valor") and d.get("nosso") else None)}</td>'
            f'<td class="ocultar">{e(d["quando"][:10])}</td></tr>' for d in c["det"])
        corpo = (f'<h2>🥊 {e(c["nome"])}</h2><p><a class="btn sec" href="/concorrentes?emp={e(urllib.parse.quote(emp))}">← Voltar</a></p>'
                 f'<div class="kpis"><div class="kpi"><div class="v">{c["lotes"]}</div><div class="l">lotes contra nós</div></div>'
                 f'<div class="kpi"><div class="v">{c["ganhou_de_nos"]}</div><div class="l">ficou à nossa frente</div></div>'
                 f'<div class="kpi"><div class="v">{c["perdeu_para_nos"]}</div><div class="l">ficou atrás de nós</div></div>'
                 f'<div class="kpi"><div class="v">{c["vitorias"]}</div><div class="l">vezes em 1º lugar</div></div>'
                 f'<div class="kpi"><div class="v">{_pct(c["dif_media"])}</div><div class="l">preço médio dele vs. o nosso</div></div>'
                 f'<div class="kpi"><div class="v">{c["desclass"]}</div><div class="l">vezes desclassificado</div></div></div>'
                 f'<table><tr><th>Processo</th><th>Lote/item</th><th>Ele</th><th>Nós</th><th>Diferença</th><th class="ocultar">Lido em</th></tr>{det}</table>'
                 '<p class="sub">Diferença = quanto o preço dele ficou acima (+) ou abaixo (−) do nosso no mesmo lote/item.</p>')
        return pagina("Concorrente", corpo, "/concorrentes")
    taxa = (r["ganhos"] / r["lotes"] * 100) if r["lotes"] else 0
    linhas = "".join(
        f'<tr><td><a href="/concorrentes?{e(urllib.parse.urlencode({"emp": emp, "c": c["chave"]}))}"><b>{e(c["nome"])}</b></a>'
        f'<div class="sub">{c["orgaos"]} órgão(s) · último encontro {e(c["ultimo"][8:10] + "/" + c["ultimo"][5:7] + "/" + c["ultimo"][:4] if c["ultimo"] else "—")}</div></td>'
        f'<td>{c["lotes"]}</td><td><b style="color:#c62828">{c["ganhou_de_nos"]}</b></td><td style="color:#2e7d32">{c["perdeu_para_nos"]}</td>'
        f'<td class="ocultar">{c["vitorias"]}</td><td>{_pct(c["dif_media"])}</td><td class="ocultar">{c["desclass"]}</td></tr>'
        for c in lista[:150])
    perdas = "".join(
        f'<tr><td>{e(p["processo"])}<div class="sub">{e(p["orgao"][:50])}</div></td><td>L{e(str(int(p["lote"])) if str(p["lote"]).isdigit() else p["lote"])} '
        f'<span class="sub">{e(p["desc"][:50])}</span></td><td>{e(str(p["nossa_pos"]) + "º" if p["nossa_pos"] else "❌")} {_rs(p["nosso"])}</td>'
        f'<td>{e(p["lider"][:40])}{" <span class=tag>nossa</span>" if p["propria"] else ""} {_rs(p["valor_lider"])}</td><td><b>{_pct(p["gap"])}</b></td></tr>'
        for p in dados["perdas"][:40] if p["gap"] > 0)
    plats = " · ".join(f"{k}: {v[1]}/{v[0]}" for k, v in sorted(r["por_plat"].items()))
    corpo = f"""<h2>🥊 Inteligência de concorrentes</h2>
<p class="sub">Feito com as classificações que o monitor já leu (BLL, BNC, Portal de Compras). Mostra quem disputa com vocês,
quem fica à frente e por quanto. Quanto mais processos lidos, mais confiável.</p>
<div class="acoes">{sel}</div>
<div class="kpis"><div class="kpi"><div class="v">{r["lotes"]}</div><div class="l">lotes/itens disputados</div></div>
<div class="kpi"><div class="v">{r["ganhos"]} <small>({taxa:.0f}%)</small></div><div class="l">em 1º lugar · {e(plats) or "—"}</div></div>
<div class="kpi"><div class="v">{r["segundos"]}</div><div class="l">em 2º lugar</div></div>
<div class="kpi"><div class="v">{_pct(r["gap_medio"])}</div><div class="l">quanto ficamos acima do 1º, em média, quando não ganhamos</div></div>
<div class="kpi"><div class="v">{r["perto"]}</div><div class="l">perdidos por até 3% (dava para ganhar)</div></div>
<div class="kpi"><div class="v">{r["desclass"]}</div><div class="l">vezes desclassificados/inabilitados</div></div></div>
<h3>Quem mais disputa com vocês</h3>
<table><tr><th>Concorrente</th><th>Lotes juntos</th><th>Ficou à frente</th><th>Ficou atrás</th><th class="ocultar">1º lugares</th>
<th>Preço dele vs. nosso</th><th class="ocultar">Desclass.</th></tr>{linhas or '<tr><td colspan="7">Ainda não há classificações lidas.</td></tr>'}</table>
<h3 style="margin-top:22px">Onde perdemos por menos</h3>
<p class="sub">Lotes em que não ficamos em 1º, do menor para o maior “gap” – bons candidatos a revisar preço/margem.</p>
<table><tr><th>Processo</th><th>Lote/item</th><th>Nós</th><th>1º colocado</th><th>Ficamos acima</th></tr>{perdas or '<tr><td colspan="5">Nada ainda.</td></tr>'}</table>"""
    return pagina("Concorrentes", corpo, "/concorrentes")


# ------------------------------------------------------------------ copiloto da disputa
def _num(v):
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def _sparkline(vals, larg=160, alt=34):
    vals = [v for v in vals if v]
    if len(vals) < 2:
        return ""
    mi, ma = min(vals), max(vals)
    faixa = (ma - mi) or 1
    pts = " ".join(f"{i * larg / (len(vals) - 1):.1f},{alt - 3 - (v - mi) / faixa * (alt - 6):.1f}" for i, v in enumerate(vals))
    return (f'<svg width="{larg}" height="{alt}" viewBox="0 0 {larg} {alt}" aria-label="evolução do melhor lance">'
            f'<polyline points="{pts}" fill="none" stroke="#1565c0" stroke-width="2"/></svg>')


def disputas_ao_vivo():
    """Lotes em disputa lidos pelo copiloto nos últimos 10 min, de todas as empresas BLL/BNC."""
    out = []
    limite = datetime.now() - timedelta(minutes=10)
    for nome, ids in nomes_empresas().items():
        for eid in ids:
            db = EMPRESAS / eid / "bll_monitor.db"
            if not db.exists():
                continue
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                rows = con.execute("SELECT chave, processo, promotor, lote, descricao, fase, primeiro, melhor, nos_primeiro, lido_em FROM ao_vivo").fetchall()
                for r in rows:
                    lido = para_data(r[9])
                    if lido < limite:
                        continue
                    hist = [h[0] for h in con.execute("SELECT melhor FROM ao_vivo_hist WHERE chave=? ORDER BY rowid DESC LIMIT 40", (r[0],))][::-1]
                    out.append({"empresa": nome, "eid": eid, "chave": r[0], "processo": r[1], "promotor": r[2], "lote": r[3],
                                "descricao": r[4], "fase": r[5], "primeiro": r[6], "melhor": r[7], "somos": bool(r[8]),
                                "lido": lido, "hist": hist})
                con.close()
            except Exception:  # noqa: BLE001  (tabela ainda não existe: copiloto nunca rodou)
                pass
    return sorted(out, key=lambda x: (x["somos"], x["processo"], x["lote"]))


def form_piso(emp, proc, prom, lote, atual, compacto=False):
    k = copiloto.chave_piso(emp, proc, prom, lote)
    p = copiloto.pisos().get(k) or {}
    v = lambda x: "" if p.get(x) in (None, "") else f'{p[x]:.2f}'.replace(".", ",")  # noqa: E731
    return (f'<form method="post" action="/disputa/piso" class="linha piso" style="gap:6px;flex-wrap:wrap;align-items:flex-end">'
            f'<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="k" value="{e(k)}"><input type="hidden" name="voltar" value="{e(atual)}">'
            f'<label class="sub">custo (R$)<input class="campo" name="custo" value="{v("custo")}" inputmode="decimal" style="width:120px"></label>'
            f'<label class="sub">margem mín. %<input class="campo" name="margem" value="{v("margem")}" inputmode="decimal" style="width:80px"></label>'
            f'<label class="sub">ou piso direto (R$)<input class="campo" name="piso" value="{v("piso") if not p.get("custo") else ""}" inputmode="decimal" style="width:120px"></label>'
            f'<button class="sec">Salvar piso</button>'
            + (f'<span class="sub">piso atual: <b>R$ {copiloto._br(p["piso"])}</b></span>' if p.get("piso") else "") + '</form>')


def pg_disputa(q, aviso=""):
    vivos = disputas_ao_vivo()
    agora = datetime.now()
    cards = ""
    for d in vivos:
        piso = (copiloto.pisos().get(copiloto.chave_piso(d["empresa"], d["processo"], d["promotor"], d["lote"])) or {}).get("piso")
        melhor = _num(d["melhor"])
        txt, folga = copiloto.analise_piso(melhor, piso, d["somos"])
        cor = "#2e7d32" if d["somos"] else ("#ef6c00" if folga is not None and folga > 0 else "#c62828" if folga is not None else "#607d8b")
        idade = int((agora - d["lido"]).total_seconds())
        lote = str(int(d["lote"])) if str(d["lote"]).isdigit() else d["lote"]
        chat = "/mensagens?" + urllib.parse.urlencode({"eid": d["eid"], "processo": d["processo"], "promotor": d["promotor"]})
        cards += f"""<div class="dv" style="border-left-color:{cor}">
<div class="dv-cab"><b>{e(d["processo"])} · L{e(lote)}</b> <span class="sub">{e(d["descricao"][:70])}</span>
<span class="tag" style="background:{cor};color:#fff">{"🟢 VOCÊ EM 1º" if d["somos"] else "🥊 fora do 1º"}</span>
<span class="sub" style="margin-left:auto">{e(d["empresa"])} · {e(d["fase"])} · lido há {idade}s</span></div>
<div class="dv-num"><div><small>1º colocado</small><b>{e(d["primeiro"] or "—")}</b></div><div><small>melhor lance</small><b>R$ {e(d["melhor"] or "—")}</b></div>
<div><small>seu piso</small><b>{"R$ " + copiloto._br(piso) if piso else "—"}</b></div><div>{_sparkline(d["hist"])}</div></div>
<div class="dv-txt" style="color:{cor}">{e(txt)}</div>
<details><summary class="sub" style="cursor:pointer">definir/alterar piso</summary>{form_piso(d["empresa"], d["processo"], d["promotor"], d["lote"], "/disputa")}</details>
<div class="sub"><a href="{e(chat)}">abrir o chat do processo</a> · {e(d["promotor"][:70])}</div></div>"""
    # preparar pisos antes da sessão
    emps = nomes_empresas()
    opcoes, linhas_prep = [], ""
    for nome, ids in sorted(emps.items()):
        for eid in ids:
            if ler_env(EMPRESAS / f"{eid}.env").get("PLATAFORMA", "bll").lower() not in ("bll", "bnc"):
                continue
            for proc, prom, sit in processos_empresa([eid]):
                opcoes.append((f"{nome}||{eid}||{proc}||{prom}", f"{nome} · {proc} – {prom[:50]}{' · ' + sit if sit else ''}"))
    sel_q = q.get("prep", "")
    sel = ('<option value="">— escolha o processo —</option>' +
           "".join(f'<option value="{e(v)}" {"selected" if v == sel_q else ""}>{e(t)}</option>' for v, t in opcoes))
    if sel_q.count("||") == 3:
        nome, eid, proc, prom = sel_q.split("||")
        volta = "/disputa?" + urllib.parse.urlencode({"prep": sel_q})
        for r in classif(eid, proc, prom):
            lote = nlote(r[2])
            linhas_prep += (f'<tr><td><b>L{e(lote)}</b><div class="sub">{e((r[3] or "")[:80])}</div></td><td class="ocultar">{e(r[4] or "")}</td>'
                            f'<td>{e(r[6] or "—")}<div class="sub">nosso: {e(r[9] or "—")}</div></td>'
                            f'<td>{form_piso(nome, proc, prom, r[2], volta)}</td></tr>')
        if not linhas_prep:
            linhas_prep = '<tr><td colspan="4">O monitor ainda não leu os lotes deste processo.</td></tr>'
    corpo = f"""<style>.dv{{background:var(--card);border:1px solid var(--borda);border-left:7px solid;border-radius:12px;padding:12px 16px;margin:10px 0}}
.dv-cab{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.dv-num{{display:flex;gap:22px;flex-wrap:wrap;align-items:center;margin:8px 0}}
.dv-num small{{display:block;color:var(--sub);font-size:12px}}.dv-num b{{font-size:20px}}.dv-txt{{font-weight:700;margin:4px 0 6px}}
.piso label{{display:flex;flex-direction:column;font-weight:400}}</style>
<h2>🎯 Disputa ao vivo (copiloto)</h2>
<p class="sub">Durante a fase de lances (BLL/BNC), o monitor relê a sala a cada ~25 s e avisa no Telegram/ntfy quando vocês são
superados, quando voltam ao 1º e se ainda dá para cobrir sem passar do <b>piso</b> (custo + margem mínima). <b>Os lances
continuam sendo dados por vocês</b> – o sistema só lê. Esta página atualiza sozinha.</p>
<div id="ao-vivo">{cards or '<div class="msg">Nenhuma disputa ao vivo agora. Quando um lote entrar em disputa, ele aparece aqui (e chega aviso no Telegram).</div>'}</div>
<h2 style="margin-top:26px">Preparar os pisos antes da sessão</h2>
<p class="sub">Informe o <b>custo</b> do lote/item e a <b>margem mínima</b> (o sistema calcula o piso) ou digite o piso direto.
O copiloto usa o piso para dizer se ainda dá para cobrir o concorrente.</p>
<div class="form" style="max-width:none"><select class="campo" onchange="location.href='/disputa?prep='+encodeURIComponent(this.value)">{sel}</select>
{f'<table style="margin-top:10px"><tr><th>Lote</th><th class="ocultar">Fase</th><th>Melhor lance</th><th>Piso</th></tr>{linhas_prep}</table>' if sel_q else ''}</div>
<script>setInterval(()=>{{const a=document.activeElement; if(a&&['INPUT','SELECT','TEXTAREA'].includes(a.tagName))return;
 if(document.querySelector('details[open] .piso'))return;
 fetch(location.pathname+'?parcial=1',{{credentials:'same-origin'}}).then(r=>r.ok?r.text():null).then(h=>{{if(h!==null)document.getElementById('ao-vivo').innerHTML=h;}});}},10000);</script>"""
    if q.get("parcial") == "1":
        return cards or '<div class="msg">Nenhuma disputa ao vivo agora.</div>'
    return pagina("Disputa ao vivo", corpo, "/disputa", aviso)


# ------------------------------------------------------------------ pergunte ao Licitatudo (IA)
SUGESTOES = ["Quais processos estão em habilitação ou recurso agora?", "Em quais lotes estamos em 1º lugar?",
             "Quais prazos vencem esta semana?", "Quais certidões estão vencidas ou vencendo?",
             "Quem é o concorrente que mais ganha da gente e por quanto?", "Resuma o que aconteceu hoje nos processos",
             "Quais oportunidades de encaixe alto vencem nos próximos 5 dias?", "O que o pregoeiro pediu no 033/2026?"]


def pg_pergunte():
    hist = assistente.conversa()
    bolhas = "".join(
        (f'<div class="bl voce"><div>{e(m["texto"])}</div><small>{e(m.get("quando", ""))}</small></div>' if m["papel"] == "voce" else
         f'<div class="bl ia"><div class="analise">{analise.md_html(m["texto"])}</div><small>{e(m.get("quando", ""))} · {e(m.get("modelo", ""))} {custo_uso(m.get("custo"))}</small></div>')
        for m in hist)
    chips = "".join(f'<button type="button" class="sec chip-s" onclick="usar(this)">{e(t)}</button>' for t in SUGESTOES)
    corpo = f"""<style>.conv{{max-width:900px;margin:0 auto}}.bls{{display:flex;flex-direction:column;gap:10px;margin:12px 0 130px}}
.bl{{max-width:85%;border-radius:14px;padding:10px 14px;line-height:1.5}}.bl small{{display:block;color:var(--sub);font-size:11px;margin-top:4px}}
.bl.voce{{align-self:flex-end;background:#1b5e20;color:#fff}}.bl.voce small{{color:#cfe3d7}}
.bl.ia{{align-self:flex-start;background:var(--card);border:1px solid var(--borda)}}.bl.ia table.md{{font-size:13px}}
.entrada{{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--borda);padding:10px 16px;z-index:20}}
.entrada form{{max-width:900px;margin:0 auto;display:flex;gap:8px}}.entrada textarea{{flex:1;resize:none;min-height:46px;max-height:160px}}
.chips-s{{display:flex;flex-wrap:wrap;gap:6px}}.chip-s{{font-size:13px;padding:6px 10px;border-radius:999px}}
.pensando{{color:var(--sub);font-style:italic}}</style>
<div class="conv"><div class="linha" style="justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
<h2 style="margin:0">💬 Pergunte ao Licitatudo</h2>
<form method="post" action="/pergunte/limpar"><input type="hidden" name="csrf" value="{CSRF}"><button class="sec">🗑 Nova conversa</button></form></div>
<p class="sub">A IA responde com os dados do sistema: processos, situação, classificação, mensagens do chat, prazos, certidões,
concorrentes, oportunidades e editais analisados. Ela pode errar – confira o que for decisivo. Os dados vão para o Google
Gemini para gerar a resposta (sem senhas e sem CPF).</p>{ESTILO_IA}{aviso_custo("pergunta")}
{'' if hist else f'<div class="chips-s">{chips}</div>'}
<div class="bls" id="bls">{bolhas}</div></div>
<div class="entrada"><form onsubmit="return enviar(event)"><textarea class="campo" id="perg" rows="1"
 placeholder="Pergunte algo… ex.: em quais lotes do 033/2026 estamos em 1º?" onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();enviar(event);}}"></textarea>
<button id="bt">Enviar</button></form></div>
<script>
const bls=document.getElementById('bls');
function rolar(){{window.scrollTo(0,document.body.scrollHeight);}}
function usar(b){{document.getElementById('perg').value=b.textContent;enviar(new Event('submit'));}}
function bolha(cls,html){{const d=document.createElement('div');d.className='bl '+cls;d.innerHTML=html;bls.appendChild(d);rolar();return d;}}
function esc(t){{return t.replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);}}
let ocupado=false;
function enviar(ev){{if(ev&&ev.preventDefault)ev.preventDefault(); if(ocupado)return false;
 const t=document.getElementById('perg'); const q=t.value.trim(); if(!q)return false;
 ocupado=true; document.getElementById('bt').disabled=true; t.value='';
 const ch=document.querySelector('.chips-s'); if(ch)ch.remove();
 bolha('voce','<div>'+esc(q)+'</div>'); const p=bolha('ia','<div class="pensando">🤖 Pensando… (pode levar até 1 minuto)</div>');
 fetch('/pergunte/enviar',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
  body:'csrf={CSRF}&pergunta='+encodeURIComponent(q)}}).then(r=>r.json()).then(j=>{{
   p.innerHTML=j.ok?'<div class="analise">'+j.html+'</div><small>'+esc(j.modelo||'')+' '+(j.custo||'')+'</small>':'<div class="msg erro">'+esc(j.erro||'Erro')+'</div>';
   rolar();}}).catch(()=>{{p.innerHTML='<div class="msg erro">Não consegui falar com o servidor.</div>';}})
  .finally(()=>{{ocupado=false;document.getElementById('bt').disabled=false;t.focus();}});
 return false;}}
rolar(); document.getElementById('perg').focus();
</script>"""
    return pagina("Pergunte", corpo, "/pergunte")


# ------------------------------------------------------------------ custos da IA (comunicação ao licitante)
def milhar(n):
    return f"{int(n or 0):,}".replace(",", ".")


def aviso_custo(recurso):
    """Aviso antes de usar um recurso pago: custo estimado por uso."""
    base, cliente, n = custos_ia.estimativa(recurso)
    p = custos_ia.precos()
    nome = custos_ia.RECURSOS.get(recurso, (recurso, 0))[0]
    valor = custos_ia.brl(cliente if p["cobrar"] and p["acrescimo"] else base)
    return (f'<div class="aviso-ia">💳 <b>Recurso com IA paga por uso</b> – {e(nome)}: custo estimado de <b>~{valor}</b> por uso'
            f'{f" (média dos últimos {n} usos)" if n else " (estimativa)"}. <a href="/consumo">ver consumo e valores</a></div>')


def custo_uso(reg):
    """'Custo deste uso' depois que a IA respondeu."""
    if not reg:
        return ""
    p = custos_ia.precos()
    valor = reg.get("cliente") if p["cobrar"] and p["acrescimo"] else reg.get("brl")
    return (f'<span class="custo-ia" title="{milhar(reg.get("entrada", 0))} tokens de entrada + {milhar(reg.get("saida", 0))} de saída · '
            f'{e(reg.get("modelo", ""))} · US$ {reg.get("usd", 0):.4f}">💳 custo deste uso: <b>{custos_ia.brl(valor or 0)}</b></span>')


ESTILO_IA = ('<style>.aviso-ia{background:#fff8e1;border:1px solid #ffe082;color:#5d4300;border-radius:10px;padding:8px 12px;'
             'margin:8px 0;font-size:13.5px}:root[data-theme=dark] .aviso-ia{background:#3a3418;border-color:#6b5b1b;color:#f3e3a3}'
             '@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .aviso-ia{background:#3a3418;border-color:#6b5b1b;color:#f3e3a3}}'
             '.custo-ia{font-size:12px;color:var(--sub);margin-left:6px}</style>')


def pg_consumo(q, aviso=""):
    mes = q.get("mes", "") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        mes = datetime.now().strftime("%Y-%m")
    r = custos_ia.resumo_mes(mes)
    p = custos_ia.precos()
    CL = "cliente"
    repassa = p["cobrar"] and p["acrescimo"] > 0
    meses = sorted({u["quando"][:7] for u in custos_ia.usos()} | {datetime.now().strftime("%Y-%m")}, reverse=True)
    sel = ('<select class="campo" style="max-width:180px" onchange="location.href=\'/consumo?mes=\'+this.value">' +
           "".join(f'<option value="{m}" {"selected" if m == mes else ""}>{m[5:]}/{m[:4]}</option>' for m in meses) + "</select>")
    linhas_rec = "".join(
        f'<tr><td>{e(custos_ia.RECURSOS.get(k, (k, 0))[0])}</td><td>{v["n"]}</td><td>{custos_ia.brl(v["brl"] / v["n"] if v["n"] else 0)}</td>'
        f'<td><b>{custos_ia.brl(v["brl"])}</b></td>{f"<td>{custos_ia.brl(v[CL])}</td>" if repassa else ""}'
        f'<td class="ocultar">{milhar(v["tokens"])}</td></tr>' for k, v in sorted(r["por_recurso"].items()))
    linhas_emp = "".join(
        f'<tr><td>{e(k)}</td><td>{v["n"]}</td><td><b>{custos_ia.brl(v["brl"])}</b></td>{f"<td>{custos_ia.brl(v[CL])}</td>" if repassa else ""}</tr>'
        for k, v in sorted(r["por_empresa"].items(), key=lambda t: -t[1]["brl"]))
    ult = "".join(
        f'<tr><td>{e(u["quando"][8:10] + "/" + u["quando"][5:7] + " " + u["quando"][11:16])}</td>'
        f'<td>{e(custos_ia.RECURSOS.get(u.get("recurso"), (u.get("recurso"), 0))[0])}<div class="sub">{e(u.get("ref", ""))}</div></td>'
        f'<td class="ocultar">{e(u.get("empresa") or "—")}</td><td class="ocultar">{milhar(u.get("entrada", 0) + u.get("saida", 0))}</td>'
        f'<td>{custos_ia.brl(u.get("brl", 0))}</td>{f"<td>{custos_ia.brl(u.get(CL, 0))}</td>" if repassa else ""}</tr>'
        for u in reversed(r["usos"][-150:]))
    col_cli = "<th>Valor ao licitante</th>" if repassa else ""
    # tabela de valores por uso (para o comunicado)
    tab_valores = []
    for k, (nome, _) in custos_ia.RECURSOS.items():
        base, cli, n = custos_ia.estimativa(k)
        tab_valores.append((nome, cli if repassa else base, n))
    comunicado = comunicado_texto(tab_valores)
    corpo = f"""{ESTILO_IA}<h2>💳 Consumo de IA</h2>
<p class="sub">Os recursos com inteligência artificial usam o Google Gemini, que cobra por uso (por tokens – pedaços de texto lidos e
escritos). O sistema registra o que o Google informa em cada resposta e calcula o custo com os preços e a cotação definidos em ⚙️.
Os demais recursos (monitor, alertas, agenda, certidões, concorrentes, copiloto, oportunidades, mapa, resumo diário) <b>não têm custo de IA</b>.</p>
<div class="acoes">{sel}<a class="btn sec" href="/consumo.csv?mes={mes}">⬇ Exportar CSV</a><a class="btn sec" href="/config#custos-ia">⚙️ Preços e repasse</a></div>
<div class="kpis"><div class="kpi"><div class="v">{len(r["usos"])}</div><div class="l">usos de IA no mês</div></div>
<div class="kpi"><div class="v">{custos_ia.brl(r["total_brl"])}</div><div class="l">custo no mês (US$ {r["total_usd"]:.2f} · cotação {p["cotacao"]:.2f})</div></div>
{f'<div class="kpi"><div class="v">{custos_ia.brl(r["total_cliente"])}</div><div class="l">valor ao licitante (+{p["acrescimo"]:g}%)</div></div>' if repassa else ''}</div>
<h3>Por recurso</h3><table><tr><th>Recurso</th><th>Usos</th><th>Custo médio por uso</th><th>Total</th>{col_cli}<th class="ocultar">Tokens</th></tr>
{linhas_rec or '<tr><td colspan="6">Nenhum uso neste mês.</td></tr>'}</table>
<h3>Por empresa</h3><table><tr><th>Empresa</th><th>Usos</th><th>Total</th>{col_cli}</tr>{linhas_emp or '<tr><td colspan="4">—</td></tr>'}</table>
<p class="sub">Análises de edital, “Posso participar?” e perguntas não são ligadas a uma empresa específica e aparecem como “(sem empresa)”.</p>
<h3>Últimos usos</h3><table><tr><th>Quando</th><th>Recurso</th><th class="ocultar">Empresa</th><th class="ocultar">Tokens</th><th>Custo</th>{col_cli}</tr>
{ult or '<tr><td colspan="6">—</td></tr>'}</table>
<h2 style="margin-top:26px" id="comunicado">📣 Comunicado aos licitantes</h2>
<p class="sub">Texto pronto para informar quem usa a ferramenta sobre o custo dos recursos com IA. Os valores por uso saem do consumo
real (média dos últimos usos) e dos preços/repasse em ⚙️. Edite à vontade antes de enviar.</p>
<div class="form" style="max-width:none"><textarea class="campo" id="com" rows="22" style="max-width:100%;font-family:Georgia,serif;font-size:14px">{e(comunicado)}</textarea>
<div class="acoes"><button type="button" onclick="navigator.clipboard.writeText(document.getElementById('com').value);this.textContent='Copiado!'">📋 Copiar</button>
<a class="btn sec" href="/consumo/comunicado" target="_blank">🖨 Versão para imprimir/PDF</a></div></div>"""
    return pagina("Consumo de IA", corpo, "/config", aviso)


def comunicado_texto(tab_valores):
    p = custos_ia.precos()
    linhas = "\n".join(f"  • {nome.split(' ', 1)[1] if ' ' in nome else nome}: aprox. {custos_ia.brl(v)} por uso" for nome, v, _ in tab_valores)
    return f"""Assunto: Uso dos recursos de Inteligência Artificial no Licitatudo – custos por uso

Prezado(a) licitante,

Informamos que alguns recursos do Licitatudo utilizam Inteligência Artificial (Google Gemini), serviço cobrado
por uso. Por isso, cada utilização desses recursos gera um custo, calculado pela quantidade de texto lida e
escrita pela IA (tokens).

Recursos com IA e custo estimado por uso:
{linhas}

Os valores acima são médias: editais maiores ou perguntas que envolvem muitos processos custam mais; consultas
simples custam menos. O custo exato de cada uso é mostrado na tela logo após a resposta e fica registrado no
relatório mensal de consumo.

Recursos SEM custo de IA (incluídos): monitoramento das plataformas e alertas (Telegram/celular), mensagens do
chat, classificação dos lotes, agenda de prazos, controle de certidões, declarações padrão, inteligência de
concorrentes, copiloto da disputa ao vivo, oportunidades do PNCP com mapa e resumo diário.

Forma de cobrança: o consumo de IA é apurado mensalmente e {"repassado conforme o uso" if p["cobrar"] else "informado para acompanhamento"}.
Os valores consideram a cotação de US$ 1 = R$ {p["cotacao"]:.2f} e podem mudar se o Google alterar seus preços
(há reajuste anunciado para janeiro de 2027).

Sobre os dados: para gerar as respostas, o conteúdo necessário (edital, histórico do processo ou dados do
sistema) é enviado ao Google Gemini. Senhas e CPF não são enviados.

Ficamos à disposição para esclarecimentos.

Atenciosamente,
"""


def pg_comunicado_imprimir():
    tab = []
    p = custos_ia.precos()
    for k, (nome, _) in custos_ia.RECURSOS.items():
        base, cli, n = custos_ia.estimativa(k)
        tab.append((nome, cli if p["cobrar"] and p["acrescimo"] else base, n))
    txt = comunicado_texto(tab)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><title>Comunicado – custos de IA</title>
<style>body{{font-family:Georgia,serif;max-width:720px;margin:30px auto;padding:0 20px;line-height:1.6;color:#111}}
pre{{white-space:pre-wrap;font-family:inherit;font-size:15px}}button{{padding:8px 14px}}@media print{{button{{display:none}}}}</style></head>
<body><button onclick="window.print()">🖨 Imprimir / Salvar em PDF</button><pre>{e(txt)}</pre></body></html>"""


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
<label>Modelo <small>(vazio = automático: usa o modelo “flash” mais novo que a sua chave tiver)</small></label><input class="campo" name="GEMINI_MODELO" value="{g('GEMINI_MODELO')}" placeholder="automático">
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
<h2 style="margin-top:22px" id="custos-ia">💳 Custos da IA</h2><div class="form">
<form method="post" action="/config/ia_custos"><input type="hidden" name="csrf" value="{CSRF}">
<p class="sub">Preços do Google Gemini em US$ por 1 milhão de tokens (confira em ai.google.dev/pricing – há reajuste anunciado para jan/2027).
Usados para calcular o custo de cada uso e o comunicado aos licitantes. <a href="/consumo">Ver consumo</a></p>
<div class="linha" style="gap:8px;flex-wrap:wrap">
<div><label>Flash – entrada</label><input class="campo" name="IA_PRECO_ENTRADA" value="{g('IA_PRECO_ENTRADA', '0.75')}" style="width:110px"></div>
<div><label>Flash – saída</label><input class="campo" name="IA_PRECO_SAIDA" value="{g('IA_PRECO_SAIDA', '3.75')}" style="width:110px"></div>
<div><label>Flash-Lite – entrada</label><input class="campo" name="IA_PRECO_LITE_ENTRADA" value="{g('IA_PRECO_LITE_ENTRADA', '0.30')}" style="width:110px"></div>
<div><label>Flash-Lite – saída</label><input class="campo" name="IA_PRECO_LITE_SAIDA" value="{g('IA_PRECO_LITE_SAIDA', '2.50')}" style="width:110px"></div>
<div><label>Cotação US$ → R$</label><input class="campo" name="IA_COTACAO_USD" value="{g('IA_COTACAO_USD', '5.50')}" style="width:110px"></div>
<div><label>Acréscimo no repasse %</label><input class="campo" name="IA_ACRESCIMO_PCT" value="{g('IA_ACRESCIMO_PCT', '0')}" style="width:110px"></div></div>
<label style="font-weight:400;display:flex;gap:6px;align-items:center;margin-top:8px"><input type="checkbox" name="IA_COBRAR" value="1" {"checked" if c.get("IA_COBRAR", "1") != "0" else ""}>
O custo da IA é repassado ao licitante (mostrar o “valor ao licitante”, com o acréscimo, nos avisos e no comunicado)</label>
<div class="acoes"><button>Salvar</button></div></form></div>
<h2 style="margin-top:22px">☀️ Resumo diário no Telegram</h2><div class="form">
<form method="post" action="/config/resumo"><input type="hidden" name="csrf" value="{CSRF}">
<label style="font-weight:400;display:flex;gap:6px;align-items:center"><input type="checkbox" name="RESUMO_ATIVO" value="1" {"checked" if c.get("RESUMO_ATIVO", "1") != "0" else ""}>
Mandar todo dia um resumo: prazos de hoje/amanhã, certidões vencendo, o que mudou nos processos e oportunidades novas</label>
<label>Hora do resumo <small>(0 a 23)</small></label><input class="campo" type="number" min="0" max="23" name="RESUMO_HORA" value="{g('RESUMO_HORA', '7')}" style="max-width:120px">
<div class="acoes"><button>Salvar</button><button class="sec" name="acao" value="agora">📨 Enviar um resumo agora</button></div></form></div>
<h2 style="margin-top:22px" id="seguranca">🛡️ Segurança</h2><div class="form">
<p class="sub">Acesso ao painel: {"<b>HTTPS (cadeado) ativo</b>" if HOST == "127.0.0.1" else "<b style='color:var(--erro)'>sem HTTPS</b> – rode <code>bash seguranca.sh</code> no servidor"} ·
sessões abertas: <b>{len(_sessoes())}</b> · senha {"<b style='color:var(--erro)'>fraca</b>" if senha_fraca(SENHA) else "<b>forte</b>"}</p>
<form method="post" action="/config/senha"><input type="hidden" name="csrf" value="{CSRF}">
<h3>Trocar a senha do painel</h3>
<label>Senha atual</label><input class="campo" type="password" name="atual" autocomplete="current-password" required>
<label>Nova senha <small>(12+ caracteres, com letras e números)</small></label><input class="campo" type="password" name="nova" autocomplete="new-password" minlength="12" required>
<label>Repita a nova senha</label><input class="campo" type="password" name="nova2" autocomplete="new-password" minlength="12" required>
<div class="acoes"><button>Trocar senha</button></div></form>
<form method="post" action="/config/sessoes" onsubmit="return confirm('Desconectar todos os outros aparelhos?')"><input type="hidden" name="csrf" value="{CSRF}">
<p class="sub" style="margin-top:14px">Trocar a senha já desconecta os outros aparelhos. Use o botão abaixo se suspeitar que alguém entrou.</p>
<div class="acoes"><button class="perigo">Encerrar todas as outras sessões</button></div></form></div>
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
        itens, total, erro = buscas.buscar_tudo(f)
        if erro:
            corpo += f'<div class="msg erro">{e(erro)}</div>'
        cards = ""
        for n_card, it in enumerate(itens):
            achados = "".join(f'<li><b>Item {e(str(x.get("numeroItem")))}</b>: {e((x.get("descricao") or "")[:180])}'
                              f' <span class="sub">({e(str(x.get("quantidade") or ""))} {e(x.get("unidadeMedida") or "")})</span></li>'
                              for x in it.get("_itens_achados", []))
            cnpj, ano, seq = buscas.ref(it)
            origem = it.get("link_sistema_origem") or ""
            cards += f"""<div class="card{' lote-oculto' if n_card >= LOTE_CARDS else ''}" data-lid="{e(str(it.get('id') or ''))}"><div class="sub">{e(it.get('modalidade_licitacao_nome') or '')} · {e(it.get('title') or '')}
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
{f'<a class="btn sec" target="_blank" rel="noopener" href="{e(origem)}">Sistema de origem</a>' if origem.startswith("http") else ''}
{botao_agendar(it.get('data_fim_vigencia'), f"{it.get('title') or ''} – {it.get('municipio_nome') or ''}/{it.get('uf') or ''}", it.get('orgao_nome') or '', buscas.link_pncp(it))}</div></div>"""
        base = urllib.parse.urlencode([(k, v) for k, vs in qs.items() if k != "pagina" for v in vs])
        nav = mais_cards(len(itens))
        salvar = f"""<form method="post" action="/buscas/salvar" class="linha" style="margin-top:10px">
<input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="filtros" value="{e(json.dumps(f, ensure_ascii=False))}">
<input name="nome" placeholder="Nome da busca (ex.: Limpeza SP)" value="{e(f['q'][:40])}">
<button>🔔 Salvar busca e avisar novas no Telegram</button></form>"""
        corpo += (f'<h3>{len(itens)} resultado(s)'
                  f'{" (o PNCP indica cerca de " + str(total) + ")" if total > len(itens) else ""}'
                  f'{" – mostrando as 3.000 mais recentes" if len(itens) >= 3000 else ""}</h3>' + salvar +
                  mapa_busca(itens, qs) +
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


# ------------------------------------------------------------------ oportunidades
ROT_NIVEL = {"alto": ("🎯 Encaixe alto", "#2e7d32"), "medio": ("Encaixe médio", "#e3a14b"), "baixo": ("Encaixe baixo", "#8a958f")}


RAIOS = (50, 100, 150, 200, 300, 500, 800)


def sedes_empresas(nomes, cfg):
    """{nome: {"cidade": 'Cidade/UF', "pt": (lat, lon) | None}} – manual (ajustes) ou pelo CNPJ cadastrado."""
    manuais = cfg.get("bases") or {}
    cnpjs = {}
    for arq in sorted(EMPRESAS.glob("*.env")) if EMPRESAS.exists() else []:
        c = ler_env(arq)
        n = (c.get("EMPRESA_NOME") or arq.stem).upper()
        if c.get("EMPRESA_CNPJ") and n not in cnpjs:
            cnpjs[n] = c["EMPRESA_CNPJ"]
    cache = geo._sedes()
    faltam = []
    out = {}
    for n in nomes:
        cid = (manuais.get(n) or "").strip()
        if not cid and cnpjs.get(n):
            num = re.sub(r"\D", "", cnpjs[n])
            if num in cache:
                cid = cache[num]
            else:
                faltam.append(cnpjs[n])
        c, uf = geo.separar(cid)
        out[n] = {"cidade": cid, "pt": geo.coord(c, uf) if c else None}
    if faltam:  # consulta o CNPJ em segundo plano; aparece na próxima vez que abrir a página
        threading.Thread(target=lambda: [geo.sede_por_cnpj(x) for x in faltam], daemon=True).start()
    return out


def html_mapa(lista, sedes, base, raio, link, rot_nivel, titulo="Mapa das oportunidades"):
    """Mapa do Brasil (Leaflet) com um pino por cidade e a quantidade de licitações."""
    if not geo.cidades():
        return (f'<div class="mapa-box"><div class="mapa-cab"><b>🗺️ {e(titulo)}</b>'
                '<span class="sub">baixando a lista de cidades do Brasil (só na 1ª vez)… recarregue em 1 minuto.</span></div></div>')
    grupos, sem_local = {}, 0
    ordem = {"alto": 3, "medio": 2, "baixo": 1, "busca": 0}
    hoje = datetime.now().strftime("%Y-%m-%d")
    for r in lista:
        if not r.get("_pt"):
            sem_local += 1
            continue
        k = geo.chave(r.get("municipio"), r.get("uf"))
        g = grupos.setdefault(k, {"cidade": r.get("municipio") or "", "uf": r.get("uf") or "", "lat": r["_pt"][0],
                                  "lon": r["_pt"][1], "n": 0, "nivel": r.get("nivel") or "baixo", "novas": 0, "km": r.get("_km"), "itens": []})
        g["n"] += 1
        g["novas"] += r.get("achada_em", "")[:10] == hoje
        if ordem.get(r.get("nivel"), 0) > ordem.get(g["nivel"], 0):
            g["nivel"] = r.get("nivel")
        g["itens"].append(r)
    pinos = []
    for g in grupos.values():
        g["itens"].sort(key=lambda r: -r.get("pontos", 0))
        pinos.append({"c": g["cidade"], "uf": g["uf"], "lat": g["lat"], "lon": g["lon"], "n": g["n"], "nv": g["nivel"],
                      "novas": g["novas"], "km": round(g["km"]) if g["km"] is not None else None,
                      "url": link(cidade=f'{g["cidade"]}/{g["uf"]}', pagina=""),
                      "it": [{"o": r.get("orgao", "")[:70], "t": (r.get("objeto") or r.get("titulo") or "")[:110],
                              "v": buscas.moeda(r.get("valor")), "l": r.get("link", ""), "nv": r.get("nivel"),
                              "f": buscas.data_br(r.get("fim")), "id": str(r.get("id") or "")} for r in g["itens"]]})
    bases = [{"n": n, "c": s["cidade"], "lat": s["pt"][0], "lon": s["pt"][1], "on": n == base}
             for n, s in sedes.items() if s.get("pt")]
    dados = json.dumps({"pinos": pinos, "bases": bases, "raio": raio, "base": base}, ensure_ascii=False).replace("</", "<\\/")
    resumo = (f'{len(pinos)} cidade(s) · {sum(p["n"] for p in pinos)} licitação(ões)'
              + (f' · {sem_local} sem localização' if sem_local else ""))
    return f"""<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
<style>
.mapa-box{{background:var(--card);border:1px solid var(--borda);border-radius:12px;overflow:hidden;margin:12px 0}}
.mapa-cab{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--borda)}}
.mapa-bts{{margin-left:auto;display:flex;gap:6px}}.mapa-bts button{{padding:6px 12px;font-size:13px}}
#mapa{{height:440px;background:#dfe7ec}}
.mapa-box.min #mapa{{display:none}}.mapa-box.min .mapa-cab{{border-bottom:0}}
.mapa-box.max{{position:fixed;inset:0;z-index:2000;border-radius:0;margin:0;display:flex;flex-direction:column}}
.mapa-box.max #mapa{{flex:1;height:auto}}
.pino{{position:relative;width:38px;height:38px}}
.pino .gota{{position:absolute;inset:0;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 4px 10px rgba(0,0,0,.35);
 border:2px solid #fff;background:linear-gradient(135deg,#43a047,#1b5e20)}}
.pino.medio .gota{{background:linear-gradient(135deg,#ffb300,#e65100)}}.pino.baixo .gota{{background:linear-gradient(135deg,#90a4ae,#455a64)}}.pino.busca .gota{{background:linear-gradient(135deg,#42a5f5,#0d47a1)}}
.pino b{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font:800 13px system-ui;text-shadow:0 1px 2px rgba(0,0,0,.4)}}
.pino.grande{{width:48px;height:48px}}.pino.grande b{{font-size:15px}}
.pino .nova{{position:absolute;top:-6px;right:-6px;background:#e53935;color:#fff;border-radius:999px;font:700 10px system-ui;padding:2px 5px;
 border:2px solid #fff;animation:pulsar 1.6s infinite}}
.sede{{font-size:26px;filter:drop-shadow(0 2px 3px rgba(0,0,0,.4))}}
.grupo{{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font:800 14px system-ui;
 background:radial-gradient(circle at 30% 30%,#66bb6a,#1b5e20);border:3px solid rgba(255,255,255,.9);box-shadow:0 0 0 6px rgba(46,125,50,.25),0 4px 12px rgba(0,0,0,.35)}}
.grupo.g2{{width:54px;height:54px;font-size:15px}}.grupo.g3{{width:64px;height:64px;font-size:16px}}
.grupo small{{display:block;font-size:9px;font-weight:600;opacity:.9;text-align:center;line-height:1}}
.mapa-escuro .leaflet-tile-pane{{filter:invert(1) hue-rotate(180deg) brightness(.9) contrast(.9)}}
.pop h4{{margin:0 0 4px}}.pop ul{{margin:6px 0;padding-left:16px;max-height:220px;overflow:auto}}.pop li{{margin-bottom:6px;font-size:12px}}
.pop .ponto{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}}
.pop-it{{cursor:pointer;border-radius:6px;padding:4px;margin-left:-4px;list-style:none}}.pop-it:hover{{background:rgba(21,101,192,.08)}}
.pop ul{{padding-left:4px}}.pop-bts{{display:flex;gap:6px;margin-top:4px;flex-wrap:wrap}}
.pop-bt{{background:#1b5e20;color:#fff!important;text-decoration:none;border-radius:6px;padding:3px 8px;font-size:12px;font-weight:600}}
.pop-bt.sec{{background:#e8eef0;color:#1b3a2a!important}}
.card.destaque{{animation:destaque 2.4s ease-out}}
@keyframes destaque{{0%,40%{{box-shadow:0 0 0 4px #1565c0,0 0 24px rgba(21,101,192,.6)}}100%{{box-shadow:none}}}}
</style>
<div class="mapa-box" id="mapaBox"><div class="mapa-cab"><b>🗺️ {e(titulo)}</b><span class="sub">{e(resumo)}</span>
<div class="mapa-bts"><button type="button" class="sec" id="btMin" onclick="mapaMin()">— Minimizar</button>
<button type="button" class="sec" id="btMax" onclick="mapaMax()">⤢ Maximizar</button></div></div><div id="mapa"></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
(function(){{
const D={dados};
const box=document.getElementById('mapaBox');
let mapa=null;
const esc=t=>String(t||'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const cor={{alto:'#2e7d32',medio:'#ef6c00',baixo:'#607d8b',busca:'#1565c0'}};
function criar(){{
 if(mapa||typeof L==='undefined')return;
 mapa=L.map('mapa',{{zoomControl:true,scrollWheelZoom:true,minZoom:3,maxBounds:[[-40,-85],[10,-25]]}}).setView([-14.5,-51],4);
 const escuro=document.documentElement.dataset.theme==='dark'||(!document.documentElement.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);
 if(escuro)box.classList.add('mapa-escuro');
 L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
   {{maxZoom:18,referrerPolicy:'strict-origin-when-cross-origin',attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'}}).addTo(mapa);
 // agrupa pinos próximos (somando as licitações) para não amontoar quando o mapa está afastado
 const camada=L.markerClusterGroup?L.markerClusterGroup({{maxClusterRadius:45,showCoverageOnHover:false,
   iconCreateFunction:c=>{{const t=c.getAllChildMarkers().reduce((a,m)=>a+(m.options.qtd||0),0);
    const k=t>=100?' g3':t>=20?' g2':'';
    return L.divIcon({{className:'',iconSize:null,html:'<div class="grupo'+k+'"><div>'+t+'<small>licit.</small></div></div>'}});}}}}):L.layerGroup();
 camada.addTo(mapa);
 const noBrasil=(la,lo)=>la>-35&&la<7&&lo>-75&&lo<-28;
 const pts=[];
 D.pinos.forEach(p=>{{
  const g=p.n>=10?' grande':'';
  const ic=L.divIcon({{className:'',iconSize:g?[48,48]:[38,38],iconAnchor:g?[24,48]:[19,38],popupAnchor:[0,-40],
   html:'<div class="pino '+p.nv+g+'"><div class="gota"></div><b>'+p.n+'</b>'+(p.novas?'<span class="nova">'+p.novas+' nova'+(p.novas>1?'s':'')+'</span>':'')+'</div>'}});
  const lis=p.it.map(i=>'<li class="pop-it" data-lid="'+esc(i.id)+'" data-link="'+esc(i.l)+'" title="Clique para ver a licitação">'+
    '<span class="ponto" style="background:'+(cor[i.nv]||'#999')+'"></span><b>'+esc(i.o)+'</b><br>'+esc(i.t)+
    '<br>'+esc(i.v)+(i.f?' · até '+esc(i.f):'')+
    '<div class="pop-bts"><a href="#" class="pop-bt" data-ver="'+esc(i.id)+'">👁 Ver licitação</a>'+
    (i.l?'<a class="pop-bt sec" href="'+esc(i.l)+'" target="_blank" rel="noopener">📄 Edital (PNCP)</a>':'')+'</div></li>').join('');
  if(!noBrasil(p.lat,p.lon))return;
  L.marker([p.lat,p.lon],{{icon:ic,qtd:p.n,title:p.c+'/'+p.uf+': '+p.n}}).addTo(camada).bindPopup(
   '<div class="pop"><h4>'+esc(p.c)+'/'+esc(p.uf)+'</h4><div>'+p.n+' licitação(ões) aberta(s)'+(p.km!=null?' · '+p.km+' km':'')+'</div><ul>'+lis+'</ul>'+
   '<a href="'+esc(p.url)+'">Ver só esta cidade na lista →</a></div>',{{maxWidth:320}});
  pts.push([p.lat,p.lon]);}});
 D.bases.forEach(b=>{{
  L.marker([b.lat,b.lon],{{icon:L.divIcon({{className:'',html:'<div class="sede">🏠</div>',iconSize:[28,28],iconAnchor:[14,24]}}),
    title:b.n,zIndexOffset:1000}}).addTo(mapa).bindPopup('<b>'+esc(b.n)+'</b><br>Sede: '+esc(b.c));
  if(b.on&&D.raio){{L.circle([b.lat,b.lon],{{radius:D.raio*1000,color:'#1565c0',weight:2,fillOpacity:.06}}).addTo(mapa); pts.push([b.lat,b.lon]);}}}});
 const ajustar=()=>{{mapa.invalidateSize(); if(pts.length)mapa.fitBounds(pts,{{padding:[40,40],maxZoom:9}});}};
 setTimeout(ajustar,60);
}}
// clique numa licitação do pino: vai até o card dela na lista (e destaca); se não estiver nesta página, abre o edital
window.verLicitacao=function(id,lnk){{
 const c=[...document.querySelectorAll('[data-lid]')].find(x=>x.classList.contains('card')&&x.dataset.lid===id);
 if(c){{ c.classList.remove('lote-oculto'); if(box.classList.contains('max'))mapaMax(); if(mapa)mapa.closePopup();
  c.scrollIntoView({{behavior:'smooth',block:'center'}}); c.classList.remove('destaque'); void c.offsetWidth; c.classList.add('destaque'); return;}}
 if(lnk)window.open(lnk,'_blank','noopener');}};
document.addEventListener('click',ev=>{{
 const v=ev.target.closest('[data-ver]'); if(v){{ev.preventDefault(); const li=v.closest('.pop-it'); verLicitacao(v.dataset.ver, li&&li.dataset.link); return;}}
 if(ev.target.closest('a'))return;
 const li=ev.target.closest('.pop-it'); if(li)verLicitacao(li.dataset.lid, li.dataset.link);}});
window.mapaMax=function(){{box.classList.remove('min');const m=box.classList.toggle('max');
 document.getElementById('btMax').textContent=m?'✕ Fechar tela cheia':'⤢ Maximizar';
 document.getElementById('btMin').textContent='— Minimizar';
 document.body.style.overflow=m?'hidden':''; criar(); setTimeout(()=>mapa&&mapa.invalidateSize(),150);}};
window.mapaMin=function(){{if(box.classList.contains('max'))mapaMax();const m=box.classList.toggle('min');
 document.getElementById('btMin').textContent=m?'＋ Mostrar mapa':'— Minimizar';
 try{{localStorage.setItem('mapaMin',m?'1':'');}}catch(e){{}}
 if(!m){{criar(); setTimeout(()=>mapa&&mapa.invalidateSize(),150);}}}};
document.addEventListener('keydown',ev=>{{if(ev.key==='Escape'&&box.classList.contains('max'))mapaMax();}});
let min=false; try{{min=localStorage.getItem('mapaMin')==='1';}}catch(e){{}}
if(min){{box.classList.add('min');document.getElementById('btMin').textContent='＋ Mostrar mapa';}} else criar();
}})();
</script>"""


def pg_oportunidades(q, aviso=""):
    d = oportunidades.ler()
    cfg = d["cfg"]
    filtro = q.get("f", "boas")
    emp_f = q.get("emp", "")
    uf_f = q.get("uf", "").upper()
    uf_f = uf_f if uf_f in buscas.UFS else ""
    hoje = datetime.now().strftime("%Y-%m-%d")
    todas = [r for r in d["itens"].values() if not r.get("descartado")]
    ufs_cont = {}
    for r in todas:
        if r.get("uf") and (not emp_f or emp_f in r.get("empresas", [])):
            ufs_cont[r["uf"]] = ufs_cont.get(r["uf"], 0) + 1
    if uf_f:   # os contadores dos botões passam a valer só para o estado escolhido
        todas = [r for r in todas if r.get("uf") == uf_f]
    # raio a partir da cidade-sede de uma empresa
    nomes_pf = sorted(d.get("perfis", {}))
    sedes = sedes_empresas(nomes_pf, cfg)
    raio = int(re.sub(r"\D", "", q.get("raio", "")) or 0)
    raio = raio if raio in RAIOS else 0
    base = q.get("base", "") or emp_f
    if base not in sedes:  # sem escolha: usa a 1ª empresa que tem cidade-sede conhecida (a mesma que o menu mostra)
        base = next((n for n in nomes_pf if sedes[n].get("pt")), nomes_pf[0] if nomes_pf else "")
    ponto_base = sedes.get(base, {}).get("pt") if base else None
    for r in d["itens"].values():
        r["_pt"] = geo.coord(r.get("municipio"), r.get("uf"))
        r["_km"] = geo.km(ponto_base, r["_pt"]) if ponto_base else None
    if raio and ponto_base:
        todas = [r for r in todas if r["_km"] is not None and r["_km"] <= raio]
    cidade_f = q.get("cidade", "")
    if cidade_f:
        cid, cuf = geo.separar(cidade_f)
        todas = [r for r in todas if geo.chave(r.get("municipio"), r.get("uf")) == geo.chave(cid, cuf)]
    descartadas = [r for r in d["itens"].values() if r.get("descartado") and (not uf_f or r.get("uf") == uf_f)
                   and (not (raio and ponto_base) or (r.get("_km") is not None and r["_km"] <= raio))]
    lista = descartadas if filtro == "descartadas" else todas
    if filtro == "novas":
        lista = [r for r in lista if r.get("achada_em", "")[:10] == hoje]
    elif filtro == "alto":
        lista = [r for r in lista if r.get("nivel") == "alto"]
    elif filtro == "boas":
        lista = [r for r in lista if r.get("nivel") in ("alto", "medio")]
    if emp_f:
        lista = [r for r in lista if emp_f in r.get("empresas", [])]
    lista.sort(key=lambda r: (r.get("achada_em", "")[:10] == hoje, r.get("pontos", 0), r.get("publicada", "")), reverse=True)
    if raio and ponto_base:
        lista.sort(key=lambda r: r.get("_km") or 0)
    trecho = lista   # todas; a tela mostra de 60 em 60 ("Mostrar mais")

    def link(**k):
        base = {"f": filtro, "emp": emp_f, "uf": uf_f, "raio": str(raio or ""), "base": q.get("base", ""), "cidade": cidade_f}
        base.update(k)
        return "/oportunidades?" + urllib.parse.urlencode({a: b for a, b in base.items() if b})

    n_novas = sum(1 for r in todas if r.get("achada_em", "")[:10] == hoje)
    n_alto = sum(1 for r in todas if r.get("nivel") == "alto")
    n_boas = sum(1 for r in todas if r.get("nivel") in ("alto", "medio"))
    abas = "".join(
        f'<a class="btn {"" if filtro == k else "sec"}" href="{link(f=k, pagina="")}">{rot} ({n})</a>'
        for k, rot, n in (("boas", "Alto + médio", n_boas), ("alto", "🎯 Encaixe alto", n_alto), ("novas", "🆕 Novas hoje", n_novas),
                          ("todas", "Todas", len(todas)), ("descartadas", "Descartadas", len(descartadas))))
    nomes = sorted(d.get("perfis", {}))
    sel_emp = ""
    if len(nomes) > 1:
        sel_emp = ('<select class="campo" style="max-width:260px" onchange="location.href=this.value">'
                   f'<option value="{link(emp="", pagina="")}">Todas as empresas</option>' +
                   "".join(f'<option value="{link(emp=n, pagina="")}" {"selected" if n == emp_f else ""}>{e(n)}</option>' for n in nomes) +
                   "</select>")
    sel_uf = ('<select class="campo" style="max-width:200px" onchange="location.href=this.value" title="Filtrar por estado">'
              f'<option value="{link(uf="", pagina="")}">📍 Todos os estados</option>' +
              "".join(f'<option value="{link(uf=u, pagina="")}" {"selected" if u == uf_f else ""}>{u} ({n})</option>'
                      for u, n in sorted(ufs_cont.items())) + "</select>")
    sel_base = ('<select class="campo" style="max-width:300px" onchange="location.href=this.value" title="Raio a partir da cidade-sede de qual empresa">' +
                "".join(f'<option value="{link(base=n, pagina="")}" {"selected" if n == base else ""}>🏠 {e(n)} – '
                        f'{e(sedes[n]["cidade"] or "sem cidade (configure abaixo)")}</option>' for n in nomes_pf) + "</select>")
    sel_raio = ('<select class="campo" style="max-width:170px" onchange="location.href=this.value" title="Distância máxima">' +
                f'<option value="{link(raio="", pagina="")}">⭕ Sem limite de km</option>' +
                "".join(f'<option value="{link(raio=str(k), pagina="")}" {"selected" if k == raio else ""}>⭕ até {k} km</option>'
                        for k in RAIOS) + "</select>")
    aviso_raio = ""
    if raio and not ponto_base:
        cid = sedes.get(base, {}).get("cidade", "")
        aviso_raio = ('<div class="msg erro">' + (f'Não achei a cidade “{e(cid)}” de {e(base)} no mapa. Confira o nome em '
                      if cid else f'{e(base)} ainda não tem cidade-sede. Preencha em ') +
                      '⚙️ Perfil e ajustes → Cidade-sede (formato Cidade/UF).</div>')
    chip_cidade = (f'<a class="btn sec" href="{link(cidade="", pagina="")}" title="Tirar o filtro de cidade">📍 {e(cidade_f)} ✕</a>'
                   if cidade_f else "")
    mapa = html_mapa(lista, sedes, base, raio, link, ROT_NIVEL)
    cards = ""
    for n_card, r in enumerate(trecho):
        rot, cor = ROT_NIVEL.get(r.get("nivel"), ROT_NIVEL["baixo"])
        nova = r.get("achada_em", "")[:10] == hoje
        acao_desc = (botao(f"/oportunidades/{r['id']}/voltar", "Restaurar", "sec") if r.get("descartado")
                     else botao(f"/oportunidades/{r['id']}/descartar", "Descartar", "sec"))
        origem = r.get("origem") or ""
        cards += f"""<div class="card{' lote-oculto' if n_card >= LOTE_CARDS else ''}" data-lid="{e(str(r.get('id') or ''))}" style="border-left-color:{cor}">
<div class="sub"><b style="color:{cor}">{rot}</b> · {r.get('pontos', 0)} pts {'<span class="tag-nova">NOVA</span>' if nova else ''}
· {e(r.get('modalidade') or '')} · {e(r.get('titulo') or '')}</div>
<div class="nome" style="margin:4px 0">{e(r.get('orgao') or '')}</div>
<div class="sub"><b>{e(r.get('municipio') or '')}/{e(r.get('uf') or '')}</b> · publicada {e(buscas.data_br(r.get('publicada')))}
{f" · 📍 <b>{round(r['_km'])} km</b> de {e(base)}" if r.get('_km') is not None else ''}</div>
<p style="margin:8px 0;font-size:14px">{e(r.get('objeto') or '')}</p>
<div class="sub">Combina com: <b>{e(', '.join(r.get('termos') or []))}</b>{(' · ' + e(r['motivo'])) if r.get('motivo') else ''}</div>
<div class="sub">Perfil de: {e(', '.join(r.get('empresas') or []))}</div>
<div class="sub" style="margin-top:4px">Valor estimado: <b>{e(buscas.moeda(r.get('valor')))}</b>
{f" · Propostas até: <b>{e(buscas.data_br(r.get('fim')))}</b>" if r.get('fim') else ''}</div>
<div class="acoes"><a class="btn" target="_blank" rel="noopener" href="{e(r.get('link') or '')}">Ver edital (PNCP)</a>
<a class="btn sec" href="/buscar/itens?cnpj={e(str(r.get('cnpj')))}&ano={e(str(r.get('ano')))}&seq={e(str(r.get('seq')))}">Itens</a>
{botao_analisar(r.get('cnpj'), r.get('ano'), r.get('seq'), f"{r.get('orgao')} – {r.get('titulo')} – {r.get('municipio')}/{r.get('uf')}")}
{f'<a class="btn sec" target="_blank" rel="noopener" href="{e(origem)}">Sistema de origem</a>' if origem.startswith("http") else ''}
{botao_agendar(r.get('fim'), f"{r.get('titulo') or ''} – {r.get('municipio') or ''}/{r.get('uf') or ''}", r.get('orgao') or '', r.get('link') or '')}
{acao_desc}</div></div>"""
    nav = mais_cards(len(trecho))
    # perfil
    blocos = ""
    for nome, pf in d.get("perfis", {}).items():
        cats = "".join(f'<span class="chip-perfil">{e(c)} <small>{n}</small></span>' for c, n in pf.get("cats", {}).items())
        prods = "".join(f'<span class="chip-perfil">{e(c)} <small>{n}</small></span>' for c, n in pf.get("produtos", {}).items())
        blocos += (f'<div class="card"><div class="nome">{e(nome)}</div><div class="sub">{pf.get("processos", 0)} processo(s) analisados · '
                   f'{pf.get("orgaos", 0)} órgão(s)</div>'
                   f'<div class="sub" style="margin-top:8px">Categorias que a empresa disputa:</div><div class="chips-perfil">{cats or "—"}</div>'
                   f'<div class="sub" style="margin-top:8px">Produtos mais frequentes (nº de processos):</div><div class="chips-perfil">{prods or "—"}</div>'
                   f'<div class="sub" style="margin-top:8px">Municípios: {e(", ".join(pf.get("municipios", [])) or "—")}</div></div>')
    ufs = "".join(f'<label class="uf"><input type="checkbox" name="uf" value="{u}" {"checked" if u in cfg["ufs"] else ""}>{u}</label>'
                  for u in buscas.UFS)
    auto = ", ".join(d.get("ufs_auto") or []) or "nenhum descoberto ainda"
    estado = (f'Última atualização: <b>{e(d.get("ultima") or "ainda não rodou")}</b> · atualiza sozinho a cada 3 horas (6h às 22h)'
              + (' · <b>atualizando agora…</b>' if oportunidades.RODANDO.is_set() else ""))
    corpo = f"""<style>.chips-perfil{{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}}
.chip-perfil{{background:var(--bg);border:1px solid var(--borda);border-radius:999px;padding:3px 10px;font-size:13px}}
.chip-perfil small{{color:var(--sub);margin-left:3px}}</style>
<h2>Oportunidades para você</h2>
<p class="sub">Licitações <b>abertas</b> no PNCP que combinam com o que cada empresa já disputou (produtos dos lotes, categorias e
órgãos lidos pelo monitor). {estado}</p>
{f'<div class="msg erro">{e(d["erro"])}</div>' if d.get("erro") else ''}
<div class="acoes">{abas}{sel_emp}{sel_uf}{botao("/oportunidades/atualizar", "⟳ Atualizar agora", "sec")}</div>
<div class="acoes" style="margin-top:0">{sel_base}{sel_raio}{chip_cidade}</div>{aviso_raio}
{mapa}
<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr));margin-top:12px">{cards or '<p>Nada por aqui ainda. Clique em “Atualizar agora” (leva 1 a 3 minutos).</p>'}</div>
{nav}
<details class="op-perfil" style="margin-top:24px;border:1px solid var(--borda);border-radius:10px;background:var(--card)">
<summary style="display:block;cursor:pointer;padding:12px 14px">⚙️ <b>Perfil e ajustes</b> – o que o sistema aprendeu e como afinar</summary>
<div style="padding:12px">
<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))">{blocos or '<p class="sub">O perfil aparece depois da primeira atualização.</p>'}</div>
<form method="post" action="/oportunidades/config" class="form" style="max-width:none;margin-top:14px">
<input type="hidden" name="csrf" value="{CSRF}">
<label>Acrescentar termos <small>(separe por vírgula – ex.: kit merenda, papel toalha interfolha)</small></label>
<input class="campo" name="extra" value="{e(cfg.get('extra', ''))}">
<label>Ignorar estes termos do perfil <small>(produtos ou categorias aprendidos que não interessam)</small></label>
<input class="campo" name="desligados" value="{e(', '.join(cfg.get('desligados') or []))}">
<label>Excluir licitações que tenham <small>(ex.: obra, serviço de engenharia, locação)</small></label>
<input class="campo" name="excluir" value="{e(cfg.get('excluir', ''))}">
<label>Estados <small>(nenhum marcado = os descobertos pelos órgãos onde já disputou: {e(auto)})</small></label>
<div class="ufs">{ufs}</div>
<label style="margin-top:10px">Cidade-sede de cada empresa <small>(para o raio em km · vazio = descobre pelo CNPJ cadastrado · formato Cidade/UF)</small></label>
{"".join(f'<div class="linha" style="gap:8px;align-items:center;margin-top:4px"><input type="hidden" name="sede_nome" value="{e(n)}">'
         f'<span style="min-width:200px"><b>{e(n)}</b></span><input class="campo" style="max-width:280px" name="sede" '
         f'value="{e((cfg.get("bases") or {}).get(n, ""))}" placeholder="{e(sedes[n]["cidade"] or "ex.: Feira de Santana/BA")}"></div>'
         for n in nomes_pf)}
<label style="margin-top:10px"><input type="checkbox" name="alertar" value="1" {"checked" if cfg.get("alertar") else ""}>
Avisar no Telegram geral as novas com encaixe alto</label>
<div class="acoes"><button>Salvar e atualizar</button></div></form></div></details>
<p class="sub" style="margin-top:18px">Fonte: PNCP – Portal Nacional de Contratações Públicas. Só leitura.</p>"""
    return pagina("Oportunidades", corpo, "/oportunidades", aviso)


LOTE_CARDS = 60


def mais_cards(total):
    """Botão 'Mostrar mais' (todas as licitações já vêm na página; a tela revela de 60 em 60)."""
    if total <= LOTE_CARDS:
        return ""
    return (f'<div class="acoes" id="mais-wrap"><button type="button" class="sec" id="mais-bt" onclick="maisCards()">'
            f'⬇ Mostrar mais {LOTE_CARDS} (faltam {total - LOTE_CARDS})</button>'
            f'<button type="button" class="sec" onclick="maisCards(true)">Mostrar todas ({total})</button></div>'
            '<style>.card.lote-oculto{display:none}</style><script>'
            'function maisCards(tudo){const h=[...document.querySelectorAll(".card.lote-oculto")];'
            f'h.slice(0,tudo?h.length:{LOTE_CARDS}).forEach(c=>c.classList.remove("lote-oculto"));'
            'const r=document.querySelectorAll(".card.lote-oculto").length;'
            'if(!r)document.getElementById("mais-wrap").remove();'
            f'else document.getElementById("mais-bt").textContent="⬇ Mostrar mais "+Math.min({LOTE_CARDS},r)+" (faltam "+r+")";}}'
            '</script>')


def botao_agendar(fim_iso, titulo, orgao, link):
    """📅 coloca na Agenda o prazo de envio de proposta de uma licitação (PNCP)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T?(\d{2}:\d{2})?", fim_iso or "")
    if not m:
        return ""
    return (f'<form method="post" action="/agenda/novo" style="display:inline"><input type="hidden" name="csrf" value="{CSRF}">'
            f'<input type="hidden" name="titulo" value="{e(("📝 Enviar proposta – " + titulo)[:150])}">'
            f'<input type="hidden" name="data" value="{m.group(1)}"><input type="hidden" name="hora" value="{m.group(2) or ""}">'
            f'<input type="hidden" name="processo" value="{e(orgao[:150])}"><input type="hidden" name="obs" value="{e(link)}">'
            f'<input type="hidden" name="voltar" value="/agenda?ok=agendado">'
            f'<button class="sec" title="Colocar o prazo de envio da proposta na Agenda">📅 Agendar</button></form>')


def mapa_busca(itens, qs):
    """Mapa com os resultados desta página da busca (um pino por cidade, azul)."""
    lista = []
    for it in itens:
        pt = geo.coord(it.get("municipio_nome"), it.get("uf"))
        lista.append({"id": str(it.get("id") or ""), "municipio": it.get("municipio_nome") or "", "uf": it.get("uf") or "", "_pt": pt, "_km": None,
                      "nivel": "busca", "pontos": 0, "achada_em": "", "orgao": it.get("orgao_nome") or "",
                      "objeto": it.get("description") or "", "titulo": it.get("title") or "",
                      "valor": it.get("valor_total_estimado"), "link": buscas.link_pncp(it), "fim": it.get("data_fim_vigencia") or ""})
    try:
        nomes = sorted(oportunidades.ler().get("perfis", {}))
        sedes = sedes_empresas(nomes, oportunidades.ler()["cfg"])
    except Exception:  # noqa: BLE001
        sedes = {}

    def link(cidade="", pagina="", **_):
        cid, uf = geo.separar(cidade)
        base = [(k, v) for k, vs in qs.items() if k not in ("pagina", "municipio", "uf") for v in vs]
        base += [("municipio", cid), ("uf", uf)]
        return "/buscar?" + urllib.parse.urlencode(base)
    return html_mapa(lista, sedes, "", 0, link, ROT_NIVEL, "Mapa dos resultados")


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


def pg_analise(ch, q=None):
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
             f'<p class="sub">Gerado em {e(d.get("fim", ""))} por {e(d.get("modelo", ""))} {custo_uso(d.get("custo"))} · Arquivos lidos: {e(arqs) or "—"}<br>'
             '⚠️ A IA pode errar ou deixar algo de fora. Confira sempre no edital antes de enviar documentos e proposta.</p>'
             + html_habilitacao(ch, q or {}) + form_refazer(ch, d))
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
{ESTILO_IA}{aviso_custo("analise")}<div class="form"><h3 style="margin-top:0">Analisar um edital que você tem em mãos</h3>
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
    server_version = "Licitatudo"
    sys_version = ""
    timeout = 40   # derruba conexões presas (ataque de conexões lentas)

    def ip(self):
        """IP real do visitante (atrás do HTTPS o pedido chega do próprio servidor)."""
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1"):
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            return xff or ip
        return ip

    def https(self):
        return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-Proto") == "https"

    def cabecalhos_seguranca(self):
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; "
                         "style-src 'self' 'unsafe-inline' https://unpkg.com; "
                         "img-src 'self' data: https://unpkg.com https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
                         "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; "
                         "base-uri 'none'; object-src 'none'")
        if self.https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def cookie(self, nome):
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == nome:
                return v
        return ""

    def autorizado(self):
        if SENHA and sessao_valida(self.cookie("lt_sessao")):
            return True
        if self.command == "GET" and not self.path.startswith(("/mensagens/processo", "/mensagens/busca")):
            return self.redirecionar("/login?destino=" + urllib.parse.quote(self.path)) and False
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.cabecalhos_seguranca()
        self.end_headers()
        self.wfile.write("Sessão expirada. Recarregue a página e entre de novo.".encode())
        return False

    def login(self):
        ip = self.ip()
        agora = datetime.now().timestamp()
        TENTATIVAS[ip] = [t for t in TENTATIVAS.get(ip, []) if agora - t < 600]
        FALHAS_GERAIS[:] = [t for t in FALHAS_GERAIS if agora - t < 900]
        tam = int(self.headers.get("Content-Length", "0") or 0)
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(min(tam, 5000)).decode("utf-8"), keep_blank_values=True).items()}
        destino = f.get("destino", "/")
        if not destino.startswith("/") or destino.startswith("//") or destino.startswith("/login"):
            destino = "/"
        if len(TENTATIVAS[ip]) >= 5:
            return self.enviar(pg_login("Muitas tentativas. Aguarde 10 minutos e tente de novo.", destino), 429)
        if len(FALHAS_GERAIS) >= 20:   # ataque vindo de vários endereços: tranca o login por 15 min
            alerta_seguranca("geral", "<b>Login do painel trancado por 15 minutos</b>: mais de 20 senhas erradas "
                                      "vindas de vários endereços. Pode ser uma tentativa de invasão.")
            return self.enviar(pg_login("Login temporariamente bloqueado por segurança. Tente em 15 minutos.", destino), 429)
        usu_ok = hmac.compare_digest(f.get("usuario", "").strip().encode(), USUARIO.encode())
        sen_ok = hmac.compare_digest(f.get("senha", "").encode(), SENHA.encode())
        ok = bool(SENHA) and usu_ok and sen_ok
        if not ok:
            TENTATIVAS[ip].append(agora)
            FALHAS_GERAIS.append(agora)
            time.sleep(1)   # deixa o "chute" de senhas lento
            if len(TENTATIVAS[ip]) >= 5:
                alerta_seguranca(f"ip|{ip}", f"<b>Tentativas de senha no painel</b>: 5 senhas erradas seguidas do IP "
                                             f"{e(ip)}. Esse endereço ficou bloqueado por 10 minutos.")
            return self.enviar(pg_login("Usuário ou senha incorretos.", destino), 401)
        TENTATIVAS.pop(ip, None)
        acesso_novo(ip, self.headers.get("User-Agent", ""))
        dias = 30 if f.get("lembrar") else 1
        tok = criar_sessao(dias)
        self.send_response(303)
        validade = f"; Max-Age={dias * 86400}" if f.get("lembrar") else ""
        seguro = "; Secure" if self.https() else ""
        self.send_header("Set-Cookie", f"lt_sessao={tok}; Path=/; HttpOnly; SameSite=Lax{seguro}{validade}")
        self.send_header("Location", destino)
        self.cabecalhos_seguranca()
        self.end_headers()

    def enviar(self, corpo, codigo=200):
        dados = corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(dados)))
        self.cabecalhos_seguranca()
        self.end_headers()
        self.wfile.write(dados)

    def baixar(self, dados, nome, tipo, na_tela=False):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Disposition", f'{"inline" if na_tela else "attachment"}; filename="{nome}"')
        self.send_header("Content-Length", str(len(dados)))
        self.cabecalhos_seguranca()
        self.end_headers()
        self.wfile.write(dados)

    def redirecionar(self, url):
        if not url.startswith("/") or url.startswith("//"):
            url = "/"
        self.send_response(303)
        self.send_header("Location", url)
        self.cabecalhos_seguranca()
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
            if p == "/documentos":
                return self.enviar(pg_documentos(q, AVISOS.get(q.get("ok", ""), "")))
            if p == "/consumo":
                return self.enviar(pg_consumo(q, AVISOS.get(q.get("ok", ""), "")))
            if p == "/consumo/comunicado":
                return self.enviar(pg_comunicado_imprimir())
            if p == "/consumo.csv":
                mes = q.get("mes", "")
                linhas = ["quando;recurso;empresa;referencia;modelo;tokens_entrada;tokens_saida;custo_usd;custo_brl;valor_licitante"]
                for u in custos_ia.usos(mes if re.fullmatch(r"\d{4}-\d{2}", mes) else ""):
                    linhas.append(";".join(str(u.get(k, "")).replace(";", ",") for k in
                                           ("quando", "recurso", "empresa", "ref", "modelo", "entrada", "saida", "usd", "brl", "cliente")))
                return self.baixar(("\n".join(linhas)).encode("utf-8-sig"), f"consumo_ia_{mes or 'tudo'}.csv", "text/csv; charset=utf-8")
            if p == "/pergunte":
                return self.enviar(pg_pergunte())
            if p == "/disputa":
                return self.enviar(pg_disputa(q, AVISOS.get(q.get("ok", ""), "")))
            if p == "/concorrentes":
                return self.enviar(pg_concorrentes(q))
            if p == "/agenda":
                return self.enviar(pg_agenda(q, AVISOS.get(q.get("ok", ""), "")))
            if p == "/certidoes":
                return self.enviar(pg_certidoes(q, AVISOS.get(q.get("ok", ""), "")))
            if p == "/certidoes/pacote":
                dados, n = agenda.pacote_zip(q.get("emp", ""))
                if not n:
                    return self.enviar(pg_certidoes(q, "Nenhuma certidão válida com PDF para esta empresa."))
                nome_arq = re.sub(r"[^\w.-]+", "_", f"Certidoes_{q.get('emp', '')}_{date.today():%Y-%m-%d}") + ".zip"
                return self.baixar(dados, nome_arq, "application/zip")
            m = re.fullmatch(r"/certidoes/([0-9a-f]{12})/arquivo", p)
            if m:
                arq = agenda.arquivo_cert(m.group(1))
                if not arq:
                    return self.redirecionar("/certidoes")
                return self.baixar(arq.read_bytes(), re.sub(r"[^\w.-]+", "_", arq.name),
                                   "application/pdf" if arq.suffix.lower() == ".pdf" else "application/octet-stream", True)
            if p == "/pecas":
                return self.enviar(pg_pecas(q, AVISOS.get(q.get("ok", ""), "")))
            m = re.fullmatch(r"/pecas/([0-9a-f]{14})(/ver|/docx)?", p)
            if m:
                if m.group(2) == "/ver":
                    return self.enviar(pg_peca_ver(m.group(1)))
                if m.group(2) == "/docx":
                    d = pecas.ler(m.group(1)) or {}
                    dados = documentos.docx([("peça", pecas.blocos_md(d.get("md", "")))], {})
                    nome_arq = re.sub(r"[^\w.-]+", "_", f"{d.get('tipo', 'peca')}_{d.get('titulo', '')[:40]}").strip("_") + ".docx"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                    self.send_header("Content-Disposition", f'attachment; filename="{nome_arq}"')
                    self.send_header("Content-Length", str(len(dados)))
                    self.cabecalhos_seguranca()
                    self.end_headers()
                    self.wfile.write(dados)
                    return
                return self.enviar(pg_peca(m.group(1)))
            if p == "/documentos/ver":
                return self.enviar(pg_documentos_ver(q))
            if p == "/documentos/docx":
                ctx, docs = gerar_docs(q)
                if documentos.faltando(ctx) or not docs:
                    return self.redirecionar("/documentos")
                dados = documentos.docx(docs, ctx)
                nome_arq = re.sub(r"[^\w.-]+", "_", f"Declaracoes_{ctx.get('processo') or 'processo'}_{ctx.get('razao', '')[:30]}").strip("_") + ".docx"
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self.send_header("Content-Disposition", f'attachment; filename="{nome_arq}"')
                self.send_header("Content-Length", str(len(dados)))
                self.cabecalhos_seguranca()
                self.end_headers()
                self.wfile.write(dados)
                return
            if p == "/oportunidades":
                return self.enviar(pg_oportunidades(q, AVISOS.get(q.get("ok", ""), "")))
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
                return self.enviar(pg_analise(m.group(1), q))
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
            print("Erro no painel:", self.path.split("?")[0], repr(ex), flush=True)
            self.enviar(pagina("Erro", "<h2>Algo deu errado</h2><p>O detalhe ficou registrado no servidor "
                                       "(journalctl -u painel-bll).</p>"), 500)

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
        if self.path.rstrip("/") == "/certidoes/salvar":
            if tam > 15 * 1024 * 1024:
                return self.enviar(pg_certidoes({}, "Arquivo grande demais (máx. 15 MB)."), 413)
            campos, arqs = ler_multipart(self, tam)
            if not hmac.compare_digest(campos.get("csrf", ""), CSRF):
                return self.enviar(pagina("Erro", "<h2>Formulário expirado</h2><p>Recarregue a página e tente de novo.</p>"), 403)
            if campos.get("empresa", "") not in nomes_empresas():
                return self.redirecionar("/certidoes")
            arq = next(((n, d) for n, d in arqs if d), None)
            lido = agenda.ler_pdf_certidao(arq[1]) if arq else {}
            cid = campos.get("cid", "") if re.fullmatch(r"[0-9a-f]{12}", campos.get("cid", "")) else ""
            dados_c = {"empresa": campos["empresa"], "tipo": campos.get("tipo") or lido.get("tipo") or "outra",
                       "numero": campos.get("numero") or lido.get("numero", ""),
                       "emissao": campos.get("emissao") or lido.get("emissao", ""),
                       "validade": campos.get("validade") or lido.get("validade", "")}
            if campos.get("obs") or not cid:
                dados_c["obs"] = campos.get("obs", "")
            agenda.salvar_cert(cid, dados_c, arq)
            ok = "cert_sem_validade" if not dados_c["validade"] else "cert_lida" if lido else "cert_ok"
            return self.redirecionar("/certidoes?" + urllib.parse.urlencode({"emp": campos["empresa"], "ok": ok}))
        if self.path.rstrip("/") in ("/pecas/gerar", "/pecas/declaracoes"):
            if tam > 40 * 1024 * 1024:
                return self.enviar(pagina("Erro", "<h2>Arquivos grandes demais (máx. 40 MB).</h2>", "/documentos"), 413)
            campos, arqs = ler_multipart(self, tam)
            if not hmac.compare_digest(campos.get("csrf", ""), CSRF):
                return self.enviar(pagina("Erro", "<h2>Formulário expirado</h2><p>Recarregue a página e tente de novo.</p>"), 403)
            emps = nomes_empresas()
            nome, ctx = ctx_doc(campos)
            proc, _, prom = campos.get("proc", "").partition("||")
            edital = campos.get("edital", "") if re.fullmatch(r"[\w-]{3,60}", campos.get("edital", "")) else ""
            arqs = [(n, d) for n, d in arqs if d][:8]
            pid = pecas.novo_id(nome, proc, campos.get("tipo", ""))
            declaracoes = self.path.rstrip("/") == "/pecas/declaracoes"
            pedido = {"tipo": "declaracoes" if declaracoes else campos.get("tipo", ""), "empresa": nome, "ctx": ctx,
                      "ids_empresa": emps.get(nome, []), "proc": proc, "prom": prom, "edital": edital,
                      "pontos": campos.get("pontos", "").strip()[:4000], "anexos": arqs,
                      "titulo": f"{ctx.get('processo') or ''} – {ctx.get('orgao') or ''}".strip(" –")}
            if not declaracoes and pedido["tipo"] not in pecas.TIPOS:
                return self.redirecionar("/pecas")
            if declaracoes and not (edital or arqs):
                return self.enviar(pg_documentos({"emp": nome, "proc": campos.get("proc", "")},
                                                 "Escolha um edital já analisado ou envie o PDF do edital."))
            pasta = pecas.DIR / pid
            if arqs:   # guarda os anexos para poder "Gerar de novo"
                pasta.mkdir(parents=True, exist_ok=True)
                for n, d in arqs:
                    (pasta / re.sub(r"[^\w.\- ]", "_", n)[:120]).write_bytes(d)
            pecas.iniciar(pid, CFG_ATUAL(), pedido)
            if declaracoes:
                return self.redirecionar("/documentos?" + urllib.parse.urlencode({"emp": nome, "proc": campos.get("proc", ""), "leitura": pid}))
            return self.redirecionar(f"/pecas/{pid}")
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
        multi = urllib.parse.parse_qs(self.rfile.read(min(tam, 100000)).decode("utf-8"), keep_blank_values=True)
        f = {k: v[0] for k, v in multi.items()}
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
                if f.get("proc"):
                    for cat_sub in f.get("subs", "").split("||"):
                        cat, _, sub = cat_sub.partition("|")
                        if cat in ("lote", "dilig", "evento") and sub:
                            marcar_lido_sub(f["proc"], cat, sub)
                else:
                    marcar_todos_lidos()
                return self.enviar("ok")
            if p == "/editais/ia":
                if f.get("acao") == "padrao":
                    analise.salvar_config_ia("", f.get("empresa", ""))
                else:
                    analise.salvar_config_ia(f.get("instrucoes", ""), f.get("empresa", ""))
                return self.redirecionar("/editais?ok=ia")
            m = re.fullmatch(r"/analise/([\w-]{3,60})/habilitacao", p)
            if m and analise.ler(m.group(1)):
                ch = m.group(1)
                d_an = analise.ler(ch) or {}
                pid = pecas.novo_id("hab", ch)
                pecas.iniciar(pid, CFG_ATUAL(), {"tipo": "habilitacao", "edital": ch, "ctx": {}, "empresa": "",
                                                 "titulo": d_an.get("titulo", ""), "anexos": []})
                return self.redirecionar(f"/analise/{ch}#hab")
            if p == "/config/ia_custos":
                v = {}
                for k in ("IA_PRECO_ENTRADA", "IA_PRECO_SAIDA", "IA_PRECO_LITE_ENTRADA", "IA_PRECO_LITE_SAIDA", "IA_COTACAO_USD", "IA_ACRESCIMO_PCT"):
                    x = f.get(k, "").strip().replace(",", ".")
                    if re.fullmatch(r"\d{1,6}(\.\d{1,6})?", x):
                        v[k] = x
                v["IA_COBRAR"] = "1" if f.get("IA_COBRAR") else "0"
                gravar_env(CONFIG, v)
                return self.redirecionar("/consumo?ok=custos")
            if p == "/pergunte/enviar":
                perg = f.get("pergunta", "").strip()[:2000]
                try:
                    if not perg:
                        raise RuntimeError("Escreva uma pergunta.")
                    md, modelo, custo = assistente.perguntar(CFG_ATUAL(), perg)
                    res = {"ok": True, "html": analise.md_html(md), "modelo": modelo, "custo": custo_uso(custo)}
                except Exception as ex:  # noqa: BLE001
                    res = {"ok": False, "erro": str(ex)[:400]}
                dados = json.dumps(res, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(dados)))
                self.cabecalhos_seguranca()
                self.end_headers()
                self.wfile.write(dados)
                return
            if p == "/pergunte/limpar":
                assistente.limpar_conversa()
                return self.redirecionar("/pergunte")
            if p == "/disputa/piso":
                k = f.get("k", "")
                if k.count("|") == 3:
                    custo, margem, piso = _num(f.get("custo")), _num(f.get("margem")), _num(f.get("piso"))
                    copiloto.salvar_piso(k, custo, margem if margem is not None else 0, None if custo is not None else piso)
                volta = f.get("voltar", "/disputa")
                return self.redirecionar((volta if volta.startswith("/") and not volta.startswith("//") else "/disputa")
                                         + ("&" if "?" in volta else "?") + "ok=piso")
            if p == "/config/resumo":
                gravar_env(CONFIG, {"RESUMO_ATIVO": "1" if f.get("RESUMO_ATIVO") else "0",
                                    "RESUMO_HORA": re.sub(r"\D", "", f.get("RESUMO_HORA", "7"))[:2] or "7"})
                if f.get("acao") == "agora":
                    threading.Thread(target=lambda: resumo.enviar_partes(telegram_geral, resumo.montar()), daemon=True).start()
                    return self.redirecionar("/config?ok=resumo_enviado")
                return self.redirecionar("/config?ok=config")
            if p == "/agenda/novo":
                try:
                    dt = datetime.strptime(f.get("data", "")[:10] + " " + (f.get("hora") or "23:59")[:5], "%Y-%m-%d %H:%M")
                except ValueError:
                    return self.enviar(pg_agenda({}, "Data ou hora inválida."))
                agenda.adicionar(f.get("titulo", "").strip() or "Prazo", dt, f.get("empresa", ""),
                                 promotor=f.get("processo", "").strip(), obs=f.get("obs", "").strip())
                volta = f.get("voltar", "")
                return self.redirecionar(volta if volta.startswith("/") and not volta.startswith("//") else "/agenda?ok=prazo")
            m = re.fullmatch(r"/agenda/([0-9a-f]{12})/(feito|reabrir|excluir|data)", p)
            if m:
                pid, acao = m.groups()
                if acao == "feito":
                    agenda.alterar(pid, status="feito")
                elif acao == "reabrir":
                    agenda.alterar(pid, status="aberto")
                elif acao == "excluir":
                    agenda.excluir(pid)
                else:
                    try:
                        dt = datetime.strptime(f.get("data", "")[:10] + " " + (f.get("hora") or "23:59")[:5], "%Y-%m-%d %H:%M")
                        agenda.alterar(pid, quando=dt.strftime("%Y-%m-%dT%H:%M"), estimado=False, status="aberto")
                    except ValueError:
                        pass
                return self.redirecionar("/agenda")
            m = re.fullmatch(r"/certidoes/([0-9a-f]{12})/excluir", p)
            if m:
                agenda.excluir_cert(m.group(1))
                return self.redirecionar("/certidoes?ok=feito")
            m = re.fullmatch(r"/pecas/([0-9a-f]{14})/(salvar|refazer)", p)
            if m:
                pid = m.group(1)
                d = pecas.ler(pid)
                if not d:
                    return self.redirecionar("/pecas")
                if m.group(2) == "salvar":
                    d["md"] = f.get("md", "")[:200000]
                    pecas.gravar(pid, d)
                    return self.redirecionar(f"/pecas/{pid}?ok=feito")
                pasta = pecas.DIR / pid
                anexos = [(x.name, x.read_bytes()) for x in sorted(pasta.glob("*"))] if pasta.is_dir() else []
                pedido = {k: d.get(k) for k in ("tipo", "empresa", "ctx", "ids_empresa", "proc", "prom", "edital", "pontos", "titulo")}
                pedido["anexos"] = anexos
                pecas.iniciar(pid, CFG_ATUAL(), pedido)
                return self.redirecionar(f"/pecas/{pid}")
            if p == "/documentos/dados":
                nome = f.get("emp", "")
                if nome in nomes_empresas():
                    documentos.salvar_dados(nome, f)
                return self.redirecionar("/documentos?" + urllib.parse.urlencode({"emp": nome, "ok": "doc_dados"}))
            if p == "/documentos/cnpj":
                nome = f.get("emp", "")
                if nome not in nomes_empresas():
                    return self.redirecionar("/documentos")
                try:
                    novo = documentos.consultar_cnpj(f.get("cnpj", ""))
                except Exception as ex:  # noqa: BLE001
                    return self.enviar(pg_documentos({"emp": nome}, e(f"Não consegui consultar o CNPJ: {ex}")))
                atual = documentos.dados(nome)
                for k in ("rep_cpf",):           # o CPF não vem da Receita: mantém o digitado
                    novo[k] = atual.get(k, "")
                if atual.get("rep_nome") and not novo.get("rep_nome"):
                    novo["rep_nome"], novo["rep_cargo"] = atual["rep_nome"], atual.get("rep_cargo", "")
                documentos.salvar_dados(nome, novo)
                return self.redirecionar("/documentos?" + urllib.parse.urlencode({"emp": nome, "ok": "doc_cnpj"}))
            if p == "/oportunidades/atualizar":
                threading.Thread(target=oportunidades.atualizar, args=(telegram_geral,), daemon=True).start()
                return self.redirecionar("/oportunidades?ok=op_rodando")
            if p == "/oportunidades/config":
                oportunidades.salvar_cfg(f.get("extra", ""), f.get("excluir", ""), multi.get("uf", []),
                                         f.get("alertar") == "1", f.get("desligados", "").split(","))
                oportunidades.salvar_bases(dict(zip(multi.get("sede_nome", []), multi.get("sede", []))))
                threading.Thread(target=oportunidades.atualizar, args=(telegram_geral,), daemon=True).start()
                return self.redirecionar("/oportunidades?ok=op_cfg")
            m = re.fullmatch(r"/oportunidades/([\w.-]+)/(descartar|voltar)", p)
            if m:
                oportunidades.descartar(m.group(1), m.group(2) == "voltar")
                return self.redirecionar(self.headers.get("Referer") or "/oportunidades")
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
            if p == "/config/senha":
                global SENHA
                if not hmac.compare_digest(f.get("atual", "").encode(), SENHA.encode()):
                    time.sleep(1)
                    return self.enviar(pg_config("Senha atual incorreta."))
                nova = f.get("nova", "")
                if nova != f.get("nova2", ""):
                    return self.enviar(pg_config("As duas senhas novas não são iguais."))
                if senha_fraca(nova):
                    return self.enviar(pg_config("A nova senha precisa ter 12 ou mais caracteres, com letras e números."))
                if "\n" in nova or "\r" in nova:
                    return self.enviar(pg_config("Senha inválida."))
                gravar_env(CONFIG, {"PAINEL_SENHA": nova})
                SENHA = nova
                encerrar_todas(menos=self.cookie("lt_sessao"))
                alerta_seguranca("senha", f"<b>Senha do painel trocada</b> (IP {e(self.ip())}). Os outros aparelhos foram desconectados.", 0)
                return self.redirecionar("/config?ok=senha")
            if p == "/config/sessoes":
                encerrar_todas(menos=self.cookie("lt_sessao"))
                return self.redirecionar("/config?ok=sessoes")
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
            print("Erro no painel:", self.path.split("?")[0], repr(ex), flush=True)
            self.enviar(pagina("Erro", "<h2>Algo deu errado</h2><p>O detalhe ficou registrado no servidor "
                                       "(journalctl -u painel-bll).</p>"), 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Painel em http://{HOST}:{PORTA}")
    threading.Thread(target=buscas.ciclo, args=(telegram_geral, minutos_busca), daemon=True).start()
    threading.Thread(target=vigiar, daemon=True).start()
    threading.Thread(target=backup.ciclo, args=(CFG_ATUAL, telegram_geral), daemon=True).start()
    threading.Thread(target=oportunidades.ciclo, args=(telegram_geral,), daemon=True).start()
    threading.Thread(target=agenda.ciclo, args=(telegram_geral, CFG_ATUAL), daemon=True).start()
    threading.Thread(target=resumo.ciclo, args=(telegram_geral, CFG_ATUAL), daemon=True).start()
    ThreadingHTTPServer((HOST, PORTA), Painel).serve_forever()
