"""Painel central do Monitor BLL (http://IP:8080, protegido por usuário e senha).

Abas: Visão geral · Mensagens · Processos · Empresas · Configurações.
Permite cadastrar/editar/pausar/remover empresas e ajustar os alertas sem usar o terminal.
"""
import base64
import getpass
import hmac
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMPRESAS = PASTA / "empresas"
CONFIG = PASTA / "config.env"
PY = PASTA / "venv" / "bin" / "python"
CSRF = secrets.token_hex(16)
AVISOS = {"migrado": "Convertido! Agora você pode cadastrar outras empresas.",
          "cadastrada": "Empresa cadastrada. Em alguns minutos chega o “Monitor BLL ativo” com o nome dela.",
          "atualizada": "Empresa atualizada.", "feito": "Feito.",
          "config": "Configurações salvas e monitores reiniciados."}
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


def mensagens(filtro_emp="", busca="", limite=300):
    out = []
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
                             "ORDER BY rowid DESC LIMIT ?", (limite,)):
            if busca and busca.lower() not in " ".join(str(x) for x in r).lower():
                continue
            out.append({"emp": emp["nome"], "eid": emp["id"], "processo": r[0], "promotor": r[1], "escopo": r[2],
                        "horario": r[3], "autor": r[4], "texto": r[5], "visto": r[6], "nova": r[6] != prim})
        con.close()
    out.sort(key=lambda m: (para_data(m["visto"]), para_data(m["horario"])), reverse=True)
    return out[:limite]


def CFG_ATUAL():
    return ler_env(CONFIG)


# ------------------------------------------------------------------ HTML
ESTILO = """
:root{--bg:#f3f5f4;--card:#fff;--txt:#17241c;--sub:#5f6d65;--verde:#1f5c3f;--verde2:#e6f0ea;--aviso:#a85d00;
--avisobg:#fff4e0;--erro:#b3261e;--errobg:#fde8e6;--borda:#dfe5e1;--nova:#fff7d6}
@media (prefers-color-scheme:dark){:root{--bg:#101512;--card:#1a211d;--txt:#e5ebe7;--sub:#97a59d;--verde:#5fb88a;
--verde2:#1f3328;--aviso:#e3a14b;--avisobg:#3a2b12;--erro:#ff8a80;--errobg:#3b1715;--borda:#2a332e;--nova:#3a3418}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}
header{background:#173f2c;color:#fff;padding:0 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
header b{font-size:17px;padding:14px 0}nav{display:flex;gap:2px;flex-wrap:wrap}
nav a{color:#cfe3d7;text-decoration:none;padding:16px 12px;font-size:14px;border-bottom:3px solid transparent}
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
@media (max-width:700px){.ocultar{display:none}nav a{padding:12px 8px}}
"""

ABAS = [("/", "Visão geral"), ("/mensagens", "Mensagens"), ("/processos", "Processos"),
        ("/empresas", "Empresas"), ("/config", "Configurações")]


def pagina(titulo, corpo, aba="/", aviso="", erro=False, recarregar=False):
    nav = "".join(f'<a href="{u}" class="{"on" if u == aba else ""}">{n}</a>' for u, n in ABAS)
    msg = f'<div class="msg {"erro" if erro else ""}">{aviso}</div>' if aviso else ""
    meta = '<meta http-equiv="refresh" content="60">' if recarregar else ""
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(titulo)} – Monitor BLL</title>
<style>{ESTILO}</style></head><body><header><b>Monitor BLL</b><nav>{nav}</nav></header><main>{msg}{corpo}</main></body></html>"""


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
    cards = ""
    for x in emps:
        cls, txt = tag_status(x)
        cards += f"""<div class="card {cls}"><div class="nome">{e(x['nome'])}</div>
<div style="margin:6px 0"><span class="tag {cls}">{e(txt)}</span></div>
<div class="sub">{x['andamento']} processo(s) em andamento · {x['novas_hoje']} nova(s) hoje</div>
<div class="sub">Última verificação: {e(x['ultima'] or '—')}{f" · ciclo {x['duracao']} s" if x['duracao'] else ""}</div>
<div class="acoes"><a class="btn sec" href="/mensagens?empresa={e(x['id'])}">Mensagens</a>
<a class="btn sec" href="/processos?empresa={e(x['id'])}">Processos</a>
<a class="btn sec" href="/empresas/{e(x['id'])}">Editar</a></div></div>"""
    if capacidade and len(ativos) > capacidade:
        aviso = (f"⚠️ Há mais empresas ativas ({len(ativos)}) do que o servidor consegue verificar a cada "
                 f"{intervalo:g} min (~{capacidade}). As verificações ficam mais espaçadas. Aumente o intervalo, "
                 "os navegadores simultâneos (se houver memória) ou o plano do servidor.")
    else:
        aviso = ""
    corpo = (kpis + (f'<div class="msg erro">{aviso}</div>' if aviso else "") +
             f'<h2>Empresas</h2><div class="grid">{cards or "<p>Nenhuma empresa. <a href=/empresas/nova>Cadastrar</a></p>"}</div>')
    return pagina("Visão geral", corpo, "/", recarregar=True)


def pg_mensagens(q):
    emp, busca, so_novas = q.get("empresa", ""), q.get("q", ""), q.get("novas", "")
    emps = empresas()
    ops = "".join(f'<option value="{e(x["id"])}" {"selected" if x["id"] == emp else ""}>{e(x["nome"])}</option>'
                  for x in emps)
    msgs = mensagens(emp, busca)
    if so_novas:
        msgs = [m for m in msgs if m["nova"]]
    linhas = "".join(
        f'<tr class="{"nova" if m["nova"] and m["visto"].startswith(datetime.now().strftime("%d/%m/%Y")) else ""}">'
        f'<td>{e(m["emp"])}</td><td><b>{e(m["processo"])}</b><div class="sub ocultar">{e(m["promotor"])}</div></td>'
        f'<td class="ocultar">{e(m["escopo"])}</td><td>{e(m["horario"])}</td><td class="ocultar">{e(m["autor"])}</td>'
        f'<td class="txt">{e(m["texto"])}</td></tr>' for m in msgs)
    corpo = f"""<h2>Mensagens</h2><form class="linha" method="get">
<select name="empresa"><option value="">Todas as empresas</option>{ops}</select>
<input name="q" value="{e(busca)}" placeholder="Buscar texto, processo, autor…">
<label style="margin:0;display:flex;align-items:center;gap:6px;font-weight:400"><input type="checkbox" name="novas" value="1" {"checked" if so_novas else ""}> só novas</label>
<button>Filtrar</button></form>
<table><tr><th>Empresa</th><th>Processo</th><th class="ocultar">Origem</th><th>Horário</th><th class="ocultar">Autor</th><th>Mensagem</th></tr>
{linhas or '<tr><td colspan="6">Nenhuma mensagem.</td></tr>'}</table>
<p class="sub">Destaque amarelo = chegou hoje depois do cadastro. Mostrando até 300.</p>"""
    return pagina("Mensagens", corpo, "/mensagens", recarregar=True)


def pg_processos(q):
    emp, filtro = q.get("empresa", ""), q.get("situacao", "")
    emps = empresas()
    ign = {s.strip().upper() for s in CFG_ATUAL().get("SITUACOES_IGNORADAS", "HOMOLOGADO").split(",")}
    linhas, sits = "", set()
    for x in emps:
        if emp and x["id"] != emp:
            continue
        for p in sorted(x["procs"], key=lambda p: para_data(p[3] or ""), reverse=True):
            sits.add(p[2])
            if filtro == "andamento" and (p[2] or "").upper() in ign:
                continue
            if filtro and filtro != "andamento" and p[2] != filtro:
                continue
            cls = "parado" if (p[2] or "").upper() in ign else ""
            linhas += (f'<tr><td>{e(x["nome"])}</td><td><b>{e(p[0])}</b></td><td>{e(p[1])}</td>'
                       f'<td><span class="tag {cls}">{e(p[2])}</span></td><td class="ocultar">{e(p[3])}</td>'
                       f'<td class="ocultar">{e(p[4])}</td></tr>')
    ops = "".join(f'<option value="{e(x["id"])}" {"selected" if x["id"] == emp else ""}>{e(x["nome"])}</option>'
                  for x in emps)
    sops = "".join(f'<option {"selected" if s == filtro else ""}>{e(s)}</option>' for s in sorted(sits) if s)
    corpo = f"""<h2>Processos</h2><form class="linha" method="get">
<select name="empresa"><option value="">Todas as empresas</option>{ops}</select>
<select name="situacao"><option value="">Todas as situações</option>
<option value="andamento" {"selected" if filtro == "andamento" else ""}>Só em andamento</option>{sops}</select>
<button>Filtrar</button></form>
<table><tr><th>Empresa</th><th>Processo</th><th>Órgão</th><th>Situação</th><th class="ocultar">Disputa</th><th class="ocultar">Atualizado na BLL</th></tr>
{linhas or '<tr><td colspan="6">Nenhum processo.</td></tr>'}</table>"""
    return pagina("Processos", corpo, "/processos")


def pg_empresas(aviso="", erro=False):
    linhas = ""
    for x in empresas():
        cls, txt = tag_status(x)
        pausar = (botao(f"/empresas/{x['id']}/pausar", "Pausar", "sec") if x["ativo"]
                  else botao(f"/empresas/{x['id']}/retomar", "Retomar"))
        linhas += f"""<tr><td><b>{e(x['nome'])}</b><div class="sub">{e(x['cfg'].get('BLL_EMAIL', ''))}</div></td>
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
    sel = lambda v: "selected" if modo == v else ""  # noqa: E731
    acao = f"/empresas/{eid}" if eid else "/empresas/nova"
    remover = (f'<h3>Zona de perigo</h3>' +
               botao(f"/empresas/{eid}/remover", "Remover empresa e histórico", "perigo",
                     "Remover esta empresa e todo o histórico dela?")) if eid else ""
    corpo = f"""<h2>{"Editar empresa" if eid else "Cadastrar empresa"}</h2><div class="form">
<form method="post" action="{acao}"><input type="hidden" name="csrf" value="{CSRF}">
<label>Nome da empresa <small>(aparece nos alertas)</small></label><input class="campo" name="nome" value="{g('EMPRESA_NOME')}" required>
<label>E-mail de login da BLL</label><input class="campo" type="email" name="email" value="{g('BLL_EMAIL')}" required>
<label>Senha da BLL <small>{"(deixe em branco para manter)" if eid else "(6 números)"}</small></label>
<input class="campo" type="password" name="senha" inputmode="numeric" pattern="[0-9]{{6}}" {"" if eid else "required"} autocomplete="new-password">
<label>Perfil a escolher após o login <small>(parte do nome, ex.: OPERADOR EMPRESA X)</small></label>
<input class="campo" name="perfil" value="{g('BLL_PERFIL', 'OPERADOR')}">
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
<div class="acoes"><button>Salvar</button><a class="btn sec" href="/empresas">Cancelar</a></div></form>{remover}</div>"""
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
<label>Tópico ntfy geral</label><input class="campo" name="NTFY_TOPICO" value="{g('NTFY_TOPICO')}">
<label>Ligação pelo Telegram (CallMeBot) <small>@usuario, ou vazio para desligar</small></label>
<input class="campo" name="CHAMADA_USUARIO" value="{g('CHAMADA_USUARIO')}">
<h3>Ritmo</h3>
<label>Intervalo entre verificações (minutos)</label><input class="campo" type="number" step="0.5" min="1" name="INTERVALO_MINUTOS" value="{g('INTERVALO_MINUTOS', '2')}">
<label>Navegadores simultâneos <small>(≈ 1 por GB de memória livre)</small></label><input class="campo" type="number" min="1" max="12" name="MAX_NAVEGADORES" value="{g('MAX_NAVEGADORES', '3')}">
<label>Horário de funcionamento <small>(0 a 24)</small></label>
<input type="number" min="0" max="23" name="HORARIO_INICIO" value="{g('HORARIO_INICIO', '0')}"> até
<input type="number" min="1" max="24" name="HORARIO_FIM" value="{g('HORARIO_FIM', '24')}">
<label>Situações ignoradas <small>(separadas por vírgula)</small></label>
<input class="campo" name="SITUACOES_IGNORADAS" value="{g('SITUACOES_IGNORADAS', 'HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO')}">
<div class="acoes"><button>Salvar e reiniciar monitores</button></div></form></div>
<h2 style="margin-top:22px">Descobrir conversas do Telegram</h2><div class="form">
<p class="sub">Mande uma mensagem ao bot (ou num grupo onde ele está) e clique abaixo para ver o chat_id.</p>
<form method="post" action="/telegram"><input type="hidden" name="csrf" value="{CSRF}">
<label>Token <small>(vazio = bot geral)</small></label><input class="campo" type="password" name="token" autocomplete="off">
<div class="acoes"><button>Descobrir conversas</button></div></form></div>"""
    return pagina("Configurações", corpo, "/config", aviso)


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
    if senha and not re.fullmatch(r"\d{6}", senha):
        return "A senha da BLL deve ter 6 números."
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
         "EMPRESA_TELEGRAM_CHAT_ID": f.get("tg_chat", "").strip(), "EMPRESA_NTFY_TOPICO": f.get("ntfy", "").strip()}
    if senha:
        v["BLL_SENHA"] = senha
    if f.get("tg_token", "").strip():
        v["EMPRESA_TELEGRAM_TOKEN"] = f["tg_token"].strip()
    gravar_env(arq, v)
    login_mudou = senha or antigo.get("BLL_EMAIL") != v["BLL_EMAIL"] or antigo.get("BLL_PERFIL") != v["BLL_PERFIL"]
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
    def autorizado(self):
        if SENHA and hmac.compare_digest(self.headers.get("Authorization", ""), ESPERADO):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Painel BLL"')
        self.end_headers()
        return False

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
        if not self.autorizado():
            return
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        p = url.path.rstrip("/") or "/"
        try:
            if p == "/":
                return self.enviar(pg_visao())
            if p == "/mensagens":
                return self.enviar(pg_mensagens(q))
            if p == "/processos":
                return self.enviar(pg_processos(q))
            if p == "/empresas":
                return self.enviar(pg_empresas(AVISOS.get(q.get("ok", ""), "")))
            if p == "/empresas/nova":
                return self.enviar(form_empresa())
            if p == "/config":
                return self.enviar(pg_config(AVISOS.get(q.get("ok", ""), "")))
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
        if not self.autorizado():
            return
        tam = int(self.headers.get("Content-Length", "0") or 0)
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
                v = {k: f.get(k, "").strip() for k in ("TELEGRAM_CHAT_ID", "NTFY_TOPICO", "CHAMADA_USUARIO",
                                                        "INTERVALO_MINUTOS", "MAX_NAVEGADORES", "HORARIO_INICIO",
                                                        "HORARIO_FIM", "SITUACOES_IGNORADAS")}
                if f.get("TELEGRAM_TOKEN", "").strip():
                    v["TELEGRAM_TOKEN"] = f["TELEGRAM_TOKEN"].strip()
                gravar_env(CONFIG, v)
                for x in empresas():
                    if x["ativo"]:
                        sh("systemctl", "restart", f"monitor-bll@{x['id']}")
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
    ThreadingHTTPServer(("0.0.0.0", PORTA), Painel).serve_forever()
