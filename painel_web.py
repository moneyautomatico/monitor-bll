"""Painel central do Monitor BLL (http://IP:8080, protegido por usuário e senha).

Abas: Visão geral · Mensagens · Processos · Buscar · Empresas · Configurações.
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
import threading
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import analise
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
            out.append({"emp": f'{emp["nome"]} · {emp["plat"]}', "eid": emp["id"], "processo": r[0], "promotor": r[1], "escopo": r[2],
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
.bgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:0 16px}
.ufs{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}.uf{display:flex;gap:3px;align-items:center;font-weight:400;margin:0;padding:4px 8px;border:1px solid var(--borda);border-radius:6px}
.md li{margin:4px 0}.md li.chk{list-style:none;margin-left:-22px}.md li.chk input{margin-right:6px}
.md li.chk label{font-weight:400;display:inline;margin:0;font-size:inherit}
@media print{header,.acoes,.msg{display:none}.analise{border:0}}
.analise{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:6px 20px 16px;max-width:900px;line-height:1.5}
.analise h3{border-bottom:1px solid var(--borda);padding-bottom:4px;margin-top:22px}.analise h4{margin:14px 0 4px;font-size:14px}
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
<style>{ESTILO}</style></head><body><header><b>Licitatudo</b><nav>{nav}</nav></header><main>{msg}{corpo}</main></body></html>"""


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


def pg_mensagens(q):
    emp, busca, so_novas = q.get("empresa", ""), q.get("q", ""), q.get("novas", "")
    emps = empresas()
    ops = "".join(f'<option value="{e(x["id"])}" {"selected" if x["id"] == emp else ""}>{e(x["nome"])} · {e(x["plat"])}</option>'
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
        elif r[7] and r[7] != "[]":
            partes.append(f'<span class="tag parado">L{e(r[2])}: –</span>')
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
<label>Tópico ntfy geral</label><input class="campo" name="NTFY_TOPICO" value="{g('NTFY_TOPICO')}">
<label>Ligação pelo Telegram (CallMeBot) <small>@usuario, ou vazio para desligar</small></label>
<input class="campo" name="CHAMADA_USUARIO" value="{g('CHAMADA_USUARIO')}">
<h3>Inteligência artificial (análise de editais)</h3>
<p class="sub">Chave gratuita do Google Gemini: entre em <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>, faça login com uma conta Google e clique em “Create API key”.</p>
<label>Chave do Gemini <small>({"configurada; " if c.get("GEMINI_API_KEY") else ""}deixe vazio para manter)</small></label>
<input class="campo" type="password" name="GEMINI_API_KEY" autocomplete="off">
<label>Modelo <small>(vazio = gemini-2.5-flash)</small></label><input class="campo" name="GEMINI_MODELO" value="{g('GEMINI_MODELO')}" placeholder="gemini-2.5-flash">
<h3>Ritmo</h3>
<label>Intervalo entre verificações (minutos)</label><input class="campo" type="number" step="0.5" min="1" name="INTERVALO_MINUTOS" value="{g('INTERVALO_MINUTOS', '2')}">
<label>Navegadores simultâneos <small>(≈ 1 por GB de memória livre)</small></label><input class="campo" type="number" min="1" max="12" name="MAX_NAVEGADORES" value="{g('MAX_NAVEGADORES', '3')}">
<label>Horário de funcionamento <small>(0 a 24)</small></label>
<input type="number" min="0" max="23" name="HORARIO_INICIO" value="{g('HORARIO_INICIO', '0')}"> até
<input type="number" min="1" max="24" name="HORARIO_FIM" value="{g('HORARIO_FIM', '24')}">
<label>Buscas salvas: verificar a cada (minutos)</label><input class="campo" type="number" min="10" name="BUSCA_INTERVALO_MINUTOS" value="{g('BUSCA_INTERVALO_MINUTOS', '30')}">
<label>Situações ignoradas <small>(separadas por vírgula)</small></label>
<input class="campo" name="SITUACOES_IGNORADAS" value="{g('SITUACOES_IGNORADAS', 'HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO')}">
<div class="acoes"><button>Salvar e reiniciar monitores</button></div></form></div>
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
    def autorizado(self):
        if SENHA and hmac.compare_digest(self.headers.get("Authorization", ""), ESPERADO):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Licitatudo"')
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
            if p == "/buscar":
                return self.enviar(pg_buscar(urllib.parse.parse_qs(url.query), AVISOS.get(q.get("ok", ""), "")))
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
                v = {k: f.get(k, "").strip() for k in ("TELEGRAM_CHAT_ID", "NTFY_TOPICO", "CHAMADA_USUARIO",
                                                        "INTERVALO_MINUTOS", "MAX_NAVEGADORES", "HORARIO_INICIO",
                                                        "HORARIO_FIM", "SITUACOES_IGNORADAS", "BUSCA_INTERVALO_MINUTOS")}
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
    ThreadingHTTPServer(("0.0.0.0", PORTA), Painel).serve_forever()
