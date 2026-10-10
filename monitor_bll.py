"""
Monitor BLL Compras -> Telegram + painel local
------------------------------------------------
Lê (SOMENTE LEITURA) as mensagens do pregoeiro e do processo, lote a lote,
nos processos em andamento da sua conta na BLL, a cada N minutos.
Mensagens novas viram alerta no Telegram e aparecem no painel (painel.html).

Uso:
  python monitor_bll.py --login      # 1ª vez: abre o navegador para você entrar na BLL
  python monitor_bll.py --telegram   # descobre o seu chat_id (mande "oi" ao bot antes)
  python monitor_bll.py --teste      # roda um ciclo só, com o navegador visível
  python monitor_bll.py              # roda continuamente
  python monitor_bll.py --diagnostico  # salva o HTML das telas em ./diagnostico

O programa NUNCA digita, envia mensagens, lances ou documentos.
"""

import argparse
import hashlib
import html
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://bllcompras.com"
URL_PROPOSTAS = f"{BASE}/Proposal/ProposalSearch"
URL_INICIO = f"{BASE}/Participant"
PASTA = Path(__file__).resolve().parent
PERFIL = PASTA / "perfil_navegador"   # guarda o login (cookies) da BLL
BANCO = PASTA / "bll_monitor.db"
PAINEL = PASTA / "painel.html"
DIAG = PASTA / "diagnostico"


# ----------------------------------------------------------------- configuração
def carregar_config():
    cfg = {
        "TELEGRAM_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "INTERVALO_MINUTOS": "10",
        "SITUACOES_IGNORADAS": "HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO",
        "HORARIO_INICIO": "0",
        "HORARIO_FIM": "24",
        "BLL_EMAIL": "",
        "BLL_SENHA": "",
        "BLL_PERFIL": "OPERADOR MULTIFORNECE",
        "NAVEGADOR_VISIVEL": "0",
    }
    arq = PASTA / "config.env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in cfg:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


CFG = carregar_config()


def log(msg):
    print(f"[{datetime.now():%d/%m %H:%M:%S}] {msg}", flush=True)


# ----------------------------------------------------------------- telegram
def telegram(texto):
    tok, chat = CFG["TELEGRAM_TOKEN"], CFG["TELEGRAM_CHAT_ID"]
    if not tok or not chat:
        log("Telegram não configurado; alerta só no painel.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=20,
        )
        if not r.ok:
            log(f"Telegram recusou: {r.text[:200]}")
        return r.ok
    except requests.RequestException as e:
        log(f"Falha ao enviar Telegram: {e}")
        return False


def descobrir_chat_id():
    tok = CFG["TELEGRAM_TOKEN"]
    if not tok:
        print("Coloque o TELEGRAM_TOKEN no arquivo config.env primeiro.")
        return
    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=20).json()
    chats = {}
    for u in r.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        c = m.get("chat") or {}
        if c.get("id"):
            chats[c["id"]] = c.get("first_name") or c.get("title") or ""
    if not chats:
        print("Nenhuma conversa encontrada. Abra o seu bot no Telegram, mande 'oi' e rode de novo.")
        return
    for cid, nome in chats.items():
        print(f"chat_id = {cid}   ({nome})")
    print("\nCopie o número para TELEGRAM_CHAT_ID no config.env.")


# ----------------------------------------------------------------- banco
def abrir_banco():
    db = sqlite3.connect(BANCO)
    db.execute("""CREATE TABLE IF NOT EXISTS mensagens(
        id TEXT PRIMARY KEY, processo TEXT, promotor TEXT, escopo TEXT,
        horario TEXT, autor TEXT, texto TEXT, visto_em TEXT, alertado INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS contadores(
        processo TEXT, escopo TEXT, qtd INTEGER, PRIMARY KEY(processo, escopo))""")
    db.execute("""CREATE TABLE IF NOT EXISTS processos(
        processo TEXT PRIMARY KEY, promotor TEXT, situacao TEXT, disputa TEXT,
        atualizada TEXT, url TEXT, visto_em TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS estado(chave TEXT PRIMARY KEY, valor TEXT)""")
    return db


def estado(db, chave, valor=None):
    if valor is None:
        r = db.execute("SELECT valor FROM estado WHERE chave=?", (chave,)).fetchone()
        return r[0] if r else None
    db.execute("INSERT OR REPLACE INTO estado VALUES(?,?)", (chave, valor))
    db.commit()


def hash_msg(*partes):
    return hashlib.sha1("|".join(p or "" for p in partes).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- leitura da BLL
JS_TABELAS = """
(raiz) => {
  const r = raiz || document;
  return [...r.querySelectorAll('table')].filter(t => (t.getClientRects().length > 0)).map(t => {
    const cab = [...t.querySelectorAll('thead th, tr:first-child th')].map(x => x.innerText.trim());
    const linhas = [...t.querySelectorAll('tbody tr')].map(tr => {
      const tds = [...tr.querySelectorAll('td')];
      const links = [...tr.querySelectorAll('a[href]')].map(a => ({
        href: a.href, titulo: (a.getAttribute('title') || a.getAttribute('data-original-title') || a.innerText || '').trim()}));
      return {cel: tds.map(td => td.innerText.trim()), links};
    }).filter(l => l.cel.length > 1);
    return {cab, linhas};
  });
}
"""


JS_LOGADO = """
() => {
  if (document.querySelector('#Email')) return false;           // tela de login
  const h = document.body ? document.body.innerHTML : '';
  return /Desconectar|logout|LogOff/i.test(h);                   // menu do usuário logado
}
"""


def logado(page):
    u = page.url.lower()
    if "login" in u or "dataresult" in u:
        return False
    try:
        return bool(page.evaluate(JS_LOGADO))
    except Exception:
        return False


JS_DIGITO = """
(d) => {
  const bs = [...document.querySelectorAll('button, input[type=button], a.btn')].filter(b => (b.getClientRects().length > 0));
  const b = bs.find(x => {
    const t = (x.innerText || x.value || '').trim();
    return /^\\d\\s*ou\\s*\\d$/i.test(t) && t.replace(/\\D/g, '').includes(d);
  });
  if (!b) return false;
  b.click(); return true;
}
"""

JS_ESCOLHER_PERFIL = """
(perfil) => {
  const modal = [...document.querySelectorAll('.modal')].find(m =>
      (m.getClientRects().length > 0) && /escolha de perfil/i.test(m.innerText));
  if (!modal) return false;
  const alvo = perfil.toUpperCase();
  const linha = [...modal.querySelectorAll('tbody tr, tr')].find(tr =>
      tr.querySelector('td') && tr.innerText.toUpperCase().includes(alvo));
  const botao = linha && linha.querySelector('button, a, input[type=button]');
  if (!botao) return false;
  botao.click(); return true;
}
"""

JS_CAPTCHA_VISIVEL = """
() => [...document.querySelectorAll('iframe')].some(f =>
   /recaptcha\\/.*bframe|hcaptcha/i.test(f.src || '') && (f.getClientRects().length > 0) &&
   f.getBoundingClientRect().height > 100)
"""


JS_FECHAR_AVISO = """
() => {
  const abertas = [...document.querySelectorAll('.modal')].filter(m => m.getClientRects().length > 0);
  for (const m of abertas) {
    if (/escolha de perfil/i.test(m.innerText)) continue;   // essa é tratada depois do login
    const b = m.querySelector('#btnClose, .close, [data-dismiss="modal"], [data-bs-dismiss="modal"]')
           || [...m.querySelectorAll('button')].find(x => /fechar|ok|entendi|continuar/i.test(x.innerText));
    const texto = m.innerText.replace(/\\s+/g, ' ').trim().slice(0, 300);
    if (b) { b.click(); return texto || '(aviso sem texto)'; }
  }
  return null;
}
"""


def fechar_avisos(page):
    """Fecha avisos (janelas com botão 'Fechar') que a BLL mostra na tela de login."""
    for _ in range(6):
        texto = page.evaluate(JS_FECHAR_AVISO)
        if not texto:
            return
        log(f"Aviso da BLL fechado: {texto}")
        page.wait_for_timeout(1200)


def login_automatico(page):
    """Entra com e-mail e senha do config.env. Retorna 'ok', 'captcha' ou 'falhou'.
    Não tenta resolver captcha: se aparecer um desafio, desiste e avisa."""
    email, senha = CFG.get("BLL_EMAIL", ""), CFG.get("BLL_SENHA", "")
    if not email or not senha:
        return "sem_credenciais"
    if len(senha) != 6 or not senha.isdigit():
        log("Login: BLL_SENHA deve ter 6 números (o teclado da BLL aceita 6 dígitos).")
        return "falhou"
    page.goto(f"{BASE}/Home/Login", wait_until="networkidle")
    page.wait_for_timeout(2500)  # dá tempo do reCAPTCHA invisível carregar
    fechar_avisos(page)
    campo = page.locator("#Email")
    campo.click()
    campo.type(email, delay=random.randint(60, 140))
    page.wait_for_timeout(500)
    for d in senha:
        if not page.evaluate(JS_DIGITO, d):
            log("Login: botão do teclado virtual não encontrado.")
            return "falhou"
        page.wait_for_timeout(random.randint(250, 600))
    caixa = page.locator("#RememberMe")
    if caixa.count() and not caixa.is_checked():
        caixa.check()
    page.wait_for_timeout(400)
    fechar_avisos(page)
    page.get_by_role("button", name="Entrar").first.click()
    # o site valida o reCAPTCHA invisível e redireciona sozinho se der certo
    escolheu = False
    for _ in range(25):
        page.wait_for_timeout(1000)
        if "login" not in page.url.lower():
            break
        if page.evaluate(JS_CAPTCHA_VISIVEL):
            return "captcha"
        # janela "Escolha de perfil": escolhe o perfil de operador da empresa
        if not escolheu and page.evaluate(JS_ESCOLHER_PERFIL, CFG.get("BLL_PERFIL", "OPERADOR")):
            escolheu = True
            log("Perfil escolhido.")
    if "login" in page.url.lower():
        erro = page.evaluate("""() => [...document.querySelectorAll('.modal')]
              .filter(m => m.getClientRects().length > 0).map(m => m.innerText).join(' | ')""")
        log(f"Login não concluído. Janela na tela: {erro.strip()[:300]}")
        salvar_diag(page, "login_falhou")
        return "falhou"
    page.goto(URL_INICIO, wait_until="networkidle")
    return "ok" if logado(page) else "falhou"


def salvar_diag(page, nome):
    DIAG.mkdir(exist_ok=True)
    (DIAG / f"{nome}.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(DIAG / f"{nome}.png"), full_page=True)
    log(f"Diagnóstico salvo: {nome}")


def ler_processos(page, diag=False):
    """Lista em Processos > Propostas, com o link da Disputa de cada um."""
    page.goto(URL_PROPOSTAS, wait_until="networkidle")
    if not logado(page):
        return None
    page.wait_for_timeout(1500)
    if diag:
        salvar_diag(page, "01_propostas")
    procs = []
    for t in page.evaluate(JS_TABELAS):
        cab = [c.upper() for c in t["cab"]]
        if "PROCESSO" not in cab or "SITUAÇÃO" not in cab:
            continue
        for l in t["linhas"]:
            cel = dict(zip(cab, l["cel"])) if len(l["cel"]) == len(cab) else None
            if cel is None:  # há colunas de botões sem título; alinha pela direita do texto
                textos = [c for c in l["cel"]]
                cel = {}
                for i, nome in enumerate(cab):
                    if nome and i < len(textos):
                        cel[nome] = textos[i]
            disputa = next((a["href"] for a in l["links"] if "BatchList" in a["href"]), None)
            procs.append({
                "processo": cel.get("PROCESSO", ""),
                "promotor": cel.get("PROMOTOR", ""),
                "situacao": cel.get("SITUAÇÃO", ""),
                "disputa": cel.get("DISPUTA", ""),
                "atualizada": cel.get("ATUALIZADA EM", ""),
                "url": disputa,
            })
    return procs


def ler_notificacoes(page):
    """Quadro 'Notificações' da página inicial do participante."""
    page.goto(URL_INICIO, wait_until="networkidle")
    if not logado(page):
        return None
    page.wait_for_timeout(1000)
    out = []
    for t in page.evaluate(JS_TABELAS):
        cab = [c.upper() for c in t["cab"]]
        if "MENSAGEM" in cab and "CIDADE" in cab:
            for l in t["linhas"]:
                d = dict(zip(cab, l["cel"]))
                if d.get("MENSAGEM"):
                    out.append(d)
    return out


JS_ABRIR_CHAT = """
() => {
  const cands = [...document.querySelectorAll('button, a')].filter(b =>
      (b.getClientRects().length > 0) && b.querySelector('[class*="comment"]') &&
      (b.className.includes('dropdown-toggle') || b.getAttribute('data-toggle') === 'dropdown'));
  if (!cands.length) return false;
  cands[0].click();
  return true;
}
"""

JS_ITENS_CHAT = """
() => {
  const menu = [...document.querySelectorAll('.dropdown-menu')].find(m => (m.getClientRects().length > 0));
  if (!menu) return null;
  const itens = [];
  menu.querySelectorAll('button, a').forEach((b, i) => {
    if (!b.querySelector('[class*="comment"]')) return;
    const linha = b.closest('li, tr, div') || b.parentElement;
    let rotulo = (linha.innerText || '').replace(b.innerText, '').trim().split('\\n')[0].trim();
    itens.push({rotulo, qtd: parseInt((b.innerText || '0').trim()) || 0});
  });
  const mais = [...menu.querySelectorAll('button, a')].some(b => /carregar mais/i.test(b.innerText));
  return {itens, mais};
}
"""

JS_CLICAR_MAIS = """
() => {
  const menu = [...document.querySelectorAll('.dropdown-menu')].find(m => (m.getClientRects().length > 0));
  const b = menu && [...menu.querySelectorAll('button, a')].find(x => /carregar mais/i.test(x.innerText));
  if (b) { b.click(); return true; } return false;
}
"""

JS_CLICAR_ITEM = """
(rotulo) => {
  const menu = [...document.querySelectorAll('.dropdown-menu')].find(m => (m.getClientRects().length > 0));
  if (!menu) return false;
  for (const b of menu.querySelectorAll('button, a')) {
    if (!b.querySelector('[class*="comment"]')) continue;
    const linha = b.closest('li, tr, div') || b.parentElement;
    const r = (linha.innerText || '').replace(b.innerText, '').trim().split('\\n')[0].trim();
    if (r === rotulo) { b.click(); return true; }
  }
  return false;
}
"""

JS_LER_MODAL = """
() => {
  const modal = [...document.querySelectorAll('.modal')].find(m => (m.getClientRects().length > 0) || m.classList.contains('in') || m.classList.contains('show'));
  if (!modal) return null;
  const titulo = (modal.querySelector('.modal-title, .modal-header') || {}).innerText || '';
  const tabelas = [...modal.querySelectorAll('table')].map(t => {
    const cab = [...t.querySelectorAll('th')].map(x => x.innerText.trim().toUpperCase());
    const linhas = [...t.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim())).filter(c => c.length > 1);
    return {cab, linhas};
  });
  return {titulo: titulo.trim(), tabelas};
}
"""

JS_FECHAR_MODAL = """
() => {
  const modal = [...document.querySelectorAll('.modal')].find(m => (m.getClientRects().length > 0));
  const x = modal && modal.querySelector('.close, [data-dismiss="modal"], [data-bs-dismiss="modal"]');
  if (x) { x.click(); return true; } return false;
}
"""


def abrir_menu_chat(page, carregar_tudo=True):
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    if not page.evaluate(JS_ABRIR_CHAT):
        return None
    page.wait_for_timeout(700)
    info = page.evaluate(JS_ITENS_CHAT)
    tentativas = 0
    while carregar_tudo and info and info["mais"] and tentativas < 20:
        antes = len(info["itens"])
        page.evaluate(JS_CLICAR_MAIS)
        page.wait_for_timeout(900)
        info = page.evaluate(JS_ITENS_CHAT)
        tentativas += 1
        if info and len(info["itens"]) == antes:
            break
    return info


def ler_chat_item(page, rotulo):
    info = abrir_menu_chat(page)
    if not info or not any(i["rotulo"] == rotulo for i in info["itens"]):
        return None
    page.evaluate(JS_CLICAR_ITEM, rotulo)
    try:
        page.wait_for_selector(".modal table", state="visible", timeout=10000)
    except PWTimeout:
        return None
    page.wait_for_timeout(1200)
    dados = page.evaluate(JS_LER_MODAL)
    if not page.evaluate(JS_FECHAR_MODAL):
        page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    return dados


def extrair_mensagens(dados, rotulo):
    """Separa 'Mensagens do lote' (com Autor) e 'Mensagens do processo'."""
    out = []
    for t in dados.get("tabelas", []):
        cab = t["cab"]
        if "MENSAGEM" not in cab:
            continue
        i_h = cab.index("HORÁRIO") if "HORÁRIO" in cab else 0
        i_m = cab.index("MENSAGEM")
        i_a = cab.index("AUTOR") if "AUTOR" in cab else None
        escopo = rotulo.title() if i_a is not None else "Processo (eventos)"
        for c in t["linhas"]:
            if len(c) <= i_m:
                continue
            out.append({
                "escopo": escopo,
                "horario": c[i_h] if i_h < len(c) else "",
                "autor": c[i_a] if i_a is not None and i_a < len(c) else "SISTEMA",
                "texto": c[i_m],
            })
    return out


# ----------------------------------------------------------------- ciclo
def processar(db, page, primeira_vez, diag=False):
    procs = ler_processos(page, diag)
    if procs is None:
        return "deslogado"
    ignorar = {s.strip().upper() for s in CFG["SITUACOES_IGNORADAS"].split(",") if s.strip()}
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novas = []

    for p in procs:
        antiga = db.execute("SELECT situacao FROM processos WHERE processo=?", (p["processo"],)).fetchone()
        if antiga and antiga[0] != p["situacao"] and not primeira_vez:
            novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "Mudança de situação",
                          "horario": p["atualizada"], "autor": "BLL",
                          "texto": f"{antiga[0]} → {p['situacao']}", "id": None})
        db.execute("INSERT OR REPLACE INTO processos VALUES(?,?,?,?,?,?,?)",
                   (p["processo"], p["promotor"], p["situacao"], p["disputa"],
                    p["atualizada"], p["url"], agora))
    db.commit()

    if not procs:
        log(f"Lista de processos vazia. URL: {page.url} | título: {page.title()}")
        salvar_diag(page, "lista_vazia")
    ativos = [p for p in procs if p["situacao"].upper() not in ignorar and p["url"]]
    log(f"{len(procs)} processos na lista, {len(ativos)} em andamento.")

    for p in ativos:
        page.goto(p["url"], wait_until="networkidle")
        if not logado(page):
            return "deslogado"
        page.wait_for_timeout(1500)
        if diag:
            salvar_diag(page, f"02_disputa_{p['processo'].replace('/', '-')}")
        info = abrir_menu_chat(page)
        if not info:
            log(f"{p['processo']}: botão de mensagens não encontrado (rode --diagnostico).")
            continue
        for item in info["itens"]:
            rot, qtd = item["rotulo"], item["qtd"]
            ant = db.execute("SELECT qtd FROM contadores WHERE processo=? AND escopo=?",
                             (p["processo"], rot)).fetchone()
            # abre o lote se o contador mudou, se nunca foi lido, ou no 1º lote com msgs (traz os eventos do processo)
            if ant is not None and ant[0] == qtd and not primeira_vez:
                continue
            if qtd == 0 and ant is not None:
                continue
            dados = ler_chat_item(page, rot)
            if dados is None:
                log(f"{p['processo']} {rot}: não consegui abrir as mensagens.")
                continue
            if diag:
                (DIAG / f"03_modal_{p['processo'].replace('/', '-')}_{rot}.json").write_text(
                    json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
            for m in extrair_mensagens(dados, rot):
                mid = hash_msg(p["processo"], m["escopo"], m["horario"], m["autor"], m["texto"])
                if db.execute("SELECT 1 FROM mensagens WHERE id=?", (mid,)).fetchone():
                    continue
                db.execute("INSERT INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                           (mid, p["processo"], p["promotor"], m["escopo"], m["horario"],
                            m["autor"], m["texto"], agora, 0 if not primeira_vez else 1))
                if not primeira_vez:
                    novas.append({**m, "processo": p["processo"], "promotor": p["promotor"], "id": mid})
            db.execute("INSERT OR REPLACE INTO contadores VALUES(?,?,?)", (p["processo"], rot, qtd))
            db.commit()
            time.sleep(random.uniform(1.0, 2.5))  # ritmo humano

    # quadro de notificações da página inicial
    notifs = ler_notificacoes(page) or []
    for n in notifs:
        mid = hash_msg(n.get("PROCESSO"), "Notificação", n.get("DATA"), "", n.get("MENSAGEM"))
        if db.execute("SELECT 1 FROM mensagens WHERE id=?", (mid,)).fetchone():
            continue
        db.execute("INSERT INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                   (mid, n.get("PROCESSO", ""), n.get("CIDADE", ""), "Notificação",
                    n.get("DATA", ""), "BLL", n.get("MENSAGEM", ""), agora, 0 if not primeira_vez else 1))
        if not primeira_vez:
            novas.append({"processo": n.get("PROCESSO", ""), "promotor": n.get("CIDADE", ""),
                          "escopo": "Notificação", "horario": n.get("DATA", ""), "autor": "BLL",
                          "texto": n.get("MENSAGEM", ""), "id": mid})
    db.commit()

    for m in novas:
        e = html.escape
        txt = (f"🔔 <b>BLL – Processo {e(m['processo'])}</b>\n"
               f"{e(m['promotor'])}\n"
               f"📌 {e(m['escopo'])} · {e(m['autor'])} · {e(m['horario'])}\n\n"
               f"{e(m['texto'])}")
        if telegram(txt) and m["id"]:
            db.execute("UPDATE mensagens SET alertado=1 WHERE id=?", (m["id"],))
        time.sleep(0.5)
    db.commit()

    if primeira_vez:
        total = db.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
        telegram(f"✅ <b>Monitor BLL ativo</b>\n{len(ativos)} processo(s) em andamento vigiados, "
                 f"{total} mensagens já existentes registradas.\n"
                 f"A partir de agora você recebe aqui só as novas.")
        estado(db, "inicializado", agora)
    log(f"{len(novas)} mensagem(ns) nova(s).")
    return "ok"


# ----------------------------------------------------------------- painel
def gerar_painel(db, status):
    e = html.escape
    procs = db.execute("SELECT processo, promotor, situacao, disputa, atualizada FROM processos "
                       "ORDER BY processo DESC").fetchall()
    msgs = db.execute("SELECT processo, escopo, horario, autor, texto, visto_em FROM mensagens "
                      "ORDER BY visto_em DESC, rowid DESC LIMIT 400").fetchall()
    ignorar = {s.strip().upper() for s in CFG["SITUACOES_IGNORADAS"].split(",")}
    hoje = datetime.now().strftime("%d/%m/%Y")

    cards = ""
    for pr, prom, sit, disp, atu in procs:
        ativo = sit.upper() not in ignorar
        n = sum(1 for m in msgs if m[0] == pr)
        cards += (f'<div class="card {"ativo" if ativo else "inativo"}"><div class="num">{e(pr)}</div>'
                  f'<div class="sit">{e(sit)}</div><div class="det">{e(prom)}</div>'
                  f'<div class="det">Disputa: {e(disp)} · Atualizada: {e(atu)}</div>'
                  f'<div class="det">{n} mensagens registradas</div></div>')

    linhas = ""
    for pr, esc, hor, aut, txt, visto in msgs:
        nova = visto.startswith(hoje)
        linhas += (f'<tr class="{"nova" if nova else ""}" data-p="{e(pr)}"><td>{e(pr)}</td><td>{e(esc)}</td>'
                   f'<td>{e(hor)}</td><td>{e(aut)}</td><td class="txt">{e(txt)}</td></tr>')

    opcoes = "".join(f'<option>{e(p[0])}</option>' for p in procs)
    PAINEL.write_text(f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel BLL</title><style>
:root{{--bg:#f4f6f4;--card:#fff;--txt:#1d2a22;--sub:#5d6b62;--verde:#1f5c3f;--nova:#fff6d6;--borda:#dde3de}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121714;--card:#1b221e;--txt:#e7ece9;--sub:#9aa8a0;--verde:#5fb88a;--nova:#3a3418;--borda:#2b342f}}}}
body{{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}}
header{{background:var(--verde);color:#fff;padding:16px 20px}}h1{{margin:0;font-size:20px}}
.st{{font-size:13px;opacity:.9;margin-top:4px}}main{{padding:16px 20px;max-width:1300px;margin:auto}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-bottom:18px}}
.card{{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:12px}}
.card.ativo{{border-left:5px solid var(--verde)}}.card.inativo{{opacity:.6}}
.num{{font-weight:700;font-size:17px}}.sit{{font-size:12px;font-weight:600;color:var(--verde);margin:2px 0 6px}}
.det{{font-size:12px;color:var(--sub)}}select,input{{padding:7px;border-radius:6px;border:1px solid var(--borda);background:var(--card);color:var(--txt)}}
.filtros{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;font-size:13px}}
th{{background:var(--verde);color:#fff;text-align:left;padding:8px}}td{{padding:8px;border-top:1px solid var(--borda);vertical-align:top}}
tr.nova td{{background:var(--nova)}}.txt{{white-space:pre-wrap}}
@media (max-width:700px){{td:nth-child(2),th:nth-child(2){{display:none}}}}
</style></head><body><header><h1>Painel de alertas – BLL Compras</h1>
<div class="st">{e(status)} · atualizado em {datetime.now():%d/%m/%Y %H:%M:%S} · esta página se recarrega a cada minuto</div></header>
<main><div class="cards">{cards or "<p>Nenhum processo lido ainda.</p>"}</div>
<div class="filtros"><select id="fp"><option value="">Todos os processos</option>{opcoes}</select>
<input id="fb" placeholder="Buscar no texto…"></div>
<table><thead><tr><th>Processo</th><th>Origem</th><th>Horário</th><th>Autor</th><th>Mensagem</th></tr></thead>
<tbody id="tb">{linhas or '<tr><td colspan="5">Sem mensagens ainda.</td></tr>'}</tbody></table>
<p class="det">Linhas destacadas = registradas hoje.</p></main>
<script>
const fp=document.getElementById('fp'),fb=document.getElementById('fb');
function f(){{const p=fp.value,b=fb.value.toLowerCase();document.querySelectorAll('#tb tr').forEach(tr=>{{
tr.style.display=((!p||tr.dataset.p===p)&&(!b||tr.innerText.toLowerCase().includes(b)))?'':'none';}});}}
fp.onchange=f;fb.oninput=f;
</script></body></html>""", encoding="utf-8")


# ----------------------------------------------------------------- execução
def contexto(pw, visivel):
    visivel = visivel or CFG.get("NAVEGADOR_VISIVEL") == "1"  # no servidor roda numa tela virtual (xvfb)
    return pw.chromium.launch_persistent_context(
        str(PERFIL), headless=not visivel, locale="pt-BR",
        viewport={"width": 1366, "height": 800})


def modo_login():
    with sync_playwright() as pw:
        ctx = contexto(pw, True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/Home/Login")
        print("\nEntre na BLL na janela que abriu (usuário, senha e, se pedir, o perfil de PARTICIPANTE).")
        input("Quando estiver vendo a tela de Notificações/Processos, volte aqui e tecle ENTER... ")
        page.goto(URL_PROPOSTAS, wait_until="networkidle")
        print("Login salvo." if logado(page) else "Ainda parece deslogado. Tente de novo.")
        ctx.close()


def dentro_do_horario():
    h = datetime.now().hour
    return int(CFG["HORARIO_INICIO"]) <= h < int(CFG["HORARIO_FIM"])


def rodar(uma_vez=False, visivel=False, diag=False):
    db = abrir_banco()
    intervalo = max(1.0, float(CFG["INTERVALO_MINUTOS"].replace(",", ".")))  # mínimo 1 minuto
    avisou_logout = False
    falhas_login = 0
    gerar_painel(db, "Iniciando")
    with sync_playwright() as pw:
        ctx = contexto(pw, visivel)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        while True:
            if dentro_do_horario():
                try:
                    primeira = estado(db, "inicializado") is None
                    r = processar(db, page, primeira, diag)
                    # sessão caiu: tenta entrar sozinho (no máximo 2 vezes seguidas, para não bloquear a conta)
                    if r == "deslogado" and falhas_login < 2 and CFG.get("BLL_EMAIL"):
                        res = login_automatico(page)
                        log(f"Login automático: {res}")
                        if res == "ok":
                            falhas_login = 0
                            r = processar(db, page, primeira, diag)
                        elif res == "captcha":
                            falhas_login = 99  # não insiste com captcha
                            telegram("🧩 <b>Monitor BLL</b>: a BLL pediu captcha no login. "
                                     "Faça o login manualmente para o monitor voltar a funcionar.")
                        else:
                            falhas_login += 1
                    if r == "deslogado":
                        log("Sessão da BLL expirou.")
                        if not avisou_logout:
                            telegram("⚠️ <b>Monitor BLL</b>: a sessão expirou e o login automático não funcionou. "
                                     "Confira e-mail/senha no config.env do servidor ou me peça ajuda.")
                            avisou_logout = True
                        gerar_painel(db, "⚠️ Sessão expirada – faça login de novo")
                    else:
                        avisou_logout = False
                        falhas_login = 0
                        gerar_painel(db, "Monitor ativo")
                except Exception as e:  # nunca derrubar o loop
                    log(f"Erro no ciclo: {e!r}")
                    gerar_painel(db, f"Erro no último ciclo: {e}")
            else:
                gerar_painel(db, "Fora do horário de monitoramento")
            if uma_vez:
                break
            base = intervalo * 60
            espera = int(base + random.uniform(-0.15, 0.15) * base)  # variação de ±15% para não parecer robô
            log(f"Próxima verificação em {espera} s.")
            time.sleep(espera)
        ctx.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Monitor de mensagens BLL Compras")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--teste", action="store_true")
    ap.add_argument("--diagnostico", action="store_true")
    a = ap.parse_args()
    if a.login:
        modo_login()
    elif a.telegram:
        descobrir_chat_id()
    elif a.diagnostico:
        rodar(uma_vez=True, visivel=True, diag=True)
    elif a.teste:
        rodar(uma_vez=True, visivel=True)
    else:
        rodar()
