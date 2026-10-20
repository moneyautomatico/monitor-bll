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
import re
import sqlite3
import sys
import time
import urllib.parse
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
def _ler_env(arq, cfg):
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")


def carregar_config(empresa=None):
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
        "CHAMADA_USUARIO": "",        # @seu_usuario do Telegram para receber LIGAÇÃO (CallMeBot)
        "CHAMADA_IDIOMA": "pt-BR-Standard-A",
        "NTFY_TOPICO": "",            # alarme no celular pelo app ntfy (prioridade máxima)
        # ---- várias empresas (arquivo empresas/<id>.env sobrepõe o config.env)
        "EMPRESA_ID": "",
        "EMPRESA_NOME": "",
        "ENVIAR_PARA": "geral",       # geral | proprio | ambos
        "EMPRESA_TELEGRAM_TOKEN": "",
        "EMPRESA_TELEGRAM_CHAT_ID": "",
        "EMPRESA_NTFY_TOPICO": "",
        "MAX_NAVEGADORES": "3",       # quantas empresas podem usar o navegador ao mesmo tempo
        "PLATAFORMA": "bll",          # bll | licitanet
        "EMPRESA_CNPJ": "",           # Licitanet: empresa a escolher após o login
        "LICITANET_FUNCAO": "",       # Licitanet: tipo de funcionário (vazio = primeira opção)
        "LICITANET_MAX_LOTES": "80",
    }
    _ler_env(PASTA / "config.env", cfg)
    if empresa:
        arq = PASTA / "empresas" / f"{empresa}.env"
        if not arq.exists():
            raise SystemExit(f"Empresa '{empresa}' não encontrada em {arq}")
        _ler_env(arq, cfg)
        cfg["EMPRESA_ID"] = empresa
        cfg["EMPRESA_NOME"] = cfg["EMPRESA_NOME"] or empresa
    for k in cfg:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


CFG = carregar_config()


BASES_BLL = {"bll": "https://bllcompras.com", "bnc": "https://bnccompras.com"}  # mesmo sistema


def usar_empresa(empresa):
    """Aponta configuração e arquivos (banco, login, painel) para a pasta da empresa."""
    global CFG, PERFIL, BANCO, PAINEL, DIAG, BASE, URL_PROPOSTAS, URL_INICIO
    CFG = carregar_config(empresa)
    BASE = BASES_BLL.get(CFG.get("PLATAFORMA", "bll").lower(), BASE)
    URL_PROPOSTAS = f"{BASE}/Proposal/ProposalSearch"
    URL_INICIO = f"{BASE}/Participant"
    dados = PASTA / "empresas" / empresa
    dados.mkdir(parents=True, exist_ok=True)
    PERFIL, BANCO, PAINEL, DIAG = (dados / "perfil_navegador", dados / "bll_monitor.db",
                                   dados / "painel.html", dados / "diagnostico")


def nome_empresa():
    n = CFG.get("EMPRESA_NOME", "")
    return f"🏢 <b>{html.escape(n)}</b>\n" if n else ""


def log(msg):
    print(f"[{datetime.now():%d/%m %H:%M:%S}] {msg}", flush=True)


# ----------------------------------------------------------------- telegram
def destinos_telegram():
    geral = (CFG["TELEGRAM_TOKEN"], CFG["TELEGRAM_CHAT_ID"])
    proprio = (CFG.get("EMPRESA_TELEGRAM_TOKEN") or CFG["TELEGRAM_TOKEN"], CFG.get("EMPRESA_TELEGRAM_CHAT_ID", ""))
    modo = (CFG.get("ENVIAR_PARA") or "geral").lower() if CFG.get("EMPRESA_ID") else "geral"
    lista = []
    if modo in ("geral", "ambos"):
        lista.append(geral)
    if modo in ("proprio", "próprio", "ambos"):
        lista.append(proprio)
    vistos, out = set(), []
    for t, c in lista:
        if t and c and (t, c) not in vistos:
            vistos.add((t, c)); out.append((t, c))
    return out


def telegram(texto):
    texto = nome_empresa() + texto
    destinos = destinos_telegram()
    if not destinos:
        log("Telegram não configurado; alerta só no painel.")
        return False
    ok = False
    for tok, chat in destinos:
        ok = _telegram_um(tok, chat, texto) or ok
    return ok


def _telegram_um(tok, chat, texto):
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


def ligar(texto):
    """Faz o Telegram TOCAR como ligação (serviço CallMeBot), falando o texto."""
    usuario = CFG.get("CHAMADA_USUARIO", "").strip()
    if not usuario:
        return False
    try:
        r = requests.get("http://api.callmebot.com/start.php", timeout=90, params={
            "user": usuario, "text": texto[:250], "lang": CFG.get("CHAMADA_IDIOMA", "pt-BR-Standard-A"),
            "rpt": "2", "cc": "no"})
        import re as _re
        corpo = _re.sub(r"(?is)<(script|style|head).*?</\1>", " ", r.text)
        corpo = _re.sub(r"<[^>]+>", " ", corpo)
        corpo = " ".join(html.unescape(corpo).split())
        log(f"Ligação para {usuario}: HTTP {r.status_code} - {corpo[:300]}")
        return r.ok
    except requests.RequestException as e:
        log(f"Falha na ligação: {e}")
        return False


def alarme(titulo, texto, prioridade="5"):
    """Notificação no app ntfy. Prioridade 5 = máxima (toca alto); 3 = normal."""
    modo = (CFG.get("ENVIAR_PARA") or "geral").lower() if CFG.get("EMPRESA_ID") else "geral"
    topicos = []
    if modo in ("geral", "ambos"):
        topicos.append(CFG.get("NTFY_TOPICO", "").strip())
    if modo in ("proprio", "próprio", "ambos"):
        topicos.append(CFG.get("EMPRESA_NTFY_TOPICO", "").strip())
    topicos = [t for i, t in enumerate(topicos) if t and t not in topicos[:i]]
    if CFG.get("EMPRESA_NOME"):
        titulo = f"{CFG['EMPRESA_NOME']} – {titulo}"
    ok = False
    for topico in topicos:
        try:
            r = requests.post(f"https://ntfy.sh/{topico}", data=texto[:3000].encode("utf-8"), timeout=20,
                              params={"title": titulo, "priority": prioridade,
                                      "tags": "rotating_light" if prioridade == "5" else "bell"})
            log(f"Alarme ntfy ({topico}): HTTP {r.status_code}")
            ok = r.ok or ok
        except requests.RequestException as e:
            log(f"Falha no alarme ntfy: {e}")
    return ok


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
        processo TEXT, promotor TEXT, situacao TEXT, disputa TEXT,
        atualizada TEXT, url TEXT, visto_em TEXT, PRIMARY KEY(processo, promotor))""")
    db.execute("""CREATE TABLE IF NOT EXISTS estado(chave TEXT PRIMARY KEY, valor TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS classificacao(
        chave TEXT PRIMARY KEY, processo TEXT, promotor TEXT, lote TEXT, descricao TEXT, fase TEXT,
        primeiro TEXT, melhor TEXT, ranking TEXT, nossa_pos INTEGER, nosso_lance TEXT, lido_em REAL, atualizado TEXT)""")
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
      (m.getClientRects().length > 0) && /perfi/i.test(m.innerText));
  if (!modal) return false;
  const alvo = perfil.toUpperCase();
  // pega a linha mais interna (a janela pode ter tabelas dentro de tabelas)
  const linha = [...modal.querySelectorAll('tr')].find(tr =>
      !tr.querySelector('tr') && tr.querySelector('td') && tr.innerText.toUpperCase().includes(alvo));
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
    ir(page, f"{BASE}/Home/Login")
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
    ir(page, URL_INICIO)
    return "ok" if logado(page) else "falhou"


def salvar_diag(page, nome):
    DIAG.mkdir(exist_ok=True)
    (DIAG / f"{nome}.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(DIAG / f"{nome}.png"), full_page=True)
    log(f"Diagnóstico salvo: {nome}")


def trocar_perfil(page):
    """Abre 'Perfis' (como no menu do usuário) e escolhe o perfil configurado."""
    perfil = CFG.get("BLL_PERFIL", "OPERADOR")
    ir(page, URL_INICIO)
    page.wait_for_timeout(1000)
    if not page.evaluate("() => typeof showProfiles === 'function' && (showProfiles(), true)"):
        log("Não achei a opção 'Perfis' para trocar de perfil.")
        return False
    for _ in range(10):
        page.wait_for_timeout(800)
        if page.evaluate(JS_ESCOLHER_PERFIL, perfil):
            page.wait_for_timeout(5000)
            log(f"Perfil trocado para {perfil}. URL: {page.url}")
            return True
    abertas = page.evaluate("""() => [...document.querySelectorAll('.modal')]
        .filter(m => m.getClientRects().length > 0).map(m => m.innerText.replace(/\\s+/g,' ').slice(0,200)).join(' | ')""")
    log(f"A janela de perfis não apareceu. Janelas na tela: {abertas or 'nenhuma'}")
    return False


JS_LINHAS_PROCESSOS = """
() => {
  const out = [];
  for (const t of document.querySelectorAll('table')) {
    const cabRow = [...t.querySelectorAll('thead tr')].pop();
    if (!cabRow) continue;
    const cab = [...cabRow.children].map(x => x.innerText.trim().toUpperCase());
    if (!cab.includes('PROCESSO') || !cab.includes('SITUAÇÃO')) continue;
    for (const tr of t.querySelectorAll('tbody tr')) {
      if (tr.querySelector('input:not([type=button]), select')) continue;   // linha de filtro
      const link = [...tr.querySelectorAll('a[href]')].find(a => a.href.includes('BatchList'));
      if (!link) continue;                                                  // só processos de verdade
      const tds = [...tr.children];
      const cel = {};
      cab.forEach((nome, i) => { if (nome && tds[i]) cel[nome] = tds[i].innerText.trim(); });
      out.push({cel, url: link.href});
    }
  }
  return out;
}
"""


def ler_processos(page, diag=False):
    """Lista em Processos > Propostas, com o link da Disputa de cada um."""
    ir(page, URL_PROPOSTAS)
    if "errorview" in page.url.lower():  # perfil errado (ex.: Representante Legal)
        log("BLL recusou a página de processos para este perfil; trocando de perfil...")
        if not trocar_perfil(page):
            # plano B: sai da sessão e entra de novo, escolhendo o perfil certo no login
            log("Refazendo o login para escolher o perfil certo...")
            page.context.clear_cookies()
            log(f"Login automático: {login_automatico(page)}")
        ir(page, URL_PROPOSTAS)
    if not logado(page):
        return None
    page.wait_for_timeout(1500)
    if diag:
        salvar_diag(page, "01_propostas")
    procs = []
    vistos = set()
    for r in page.evaluate(JS_LINHAS_PROCESSOS):
        cel = r["cel"]
        proc = cel.get("PROCESSO", "").strip()
        if not proc or not r["url"]:
            continue
        chave = (proc, cel.get("PROMOTOR", "").strip())
        if chave in vistos:
            continue
        vistos.add(chave)
        procs.append({
            "processo": proc,
            "promotor": chave[1],
            "situacao": cel.get("SITUAÇÃO", "").strip(),
            "disputa": cel.get("DISPUTA", "").strip(),
            "atualizada": cel.get("ATUALIZADA EM", "").strip(),
            "url": r["url"],
        })
    return procs


def ler_notificacoes(page):
    """Quadro 'Notificações' da página inicial do participante."""
    ir(page, URL_INICIO)
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
    info = page.evaluate(JS_ITENS_CHAT)
    if not info:  # só clica no botão 💬 se o menu ainda não estiver aberto (clicar de novo o fecharia)
        if not page.evaluate(JS_ABRIR_CHAT):
            return None
        page.wait_for_timeout(900)
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
    aberta = False
    for _ in range(24):  # espera até 12 s a janela de mensagens abrir
        page.wait_for_timeout(500)
        if page.evaluate("""() => [...document.querySelectorAll('.modal')].some(m =>
              m.getClientRects().length > 0 && /mensage/i.test(m.innerText) && m.querySelector('table'))"""):
            aberta = True
            break
    if not aberta:
        log(f"Janela de mensagens de {rotulo} não abriu.")
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


# ----------------------------------------------------------------- classificação dos lotes (só leitura)
JS_ABA_TODOS = """
() => {
  const b = [...document.querySelectorAll('button, a, li')].find(x => x.getClientRects().length > 0 &&
      (x.innerText || '').trim().toUpperCase() === 'TODOS');
  if (!b) return false;
  b.click(); return true;
}
"""

JS_LOTES = """
() => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim().toUpperCase();
  for (const t of document.querySelectorAll('table')) {
    if (!t.getClientRects().length) continue;
    const cab = [...t.querySelectorAll('thead th, tr:first-child th')].map(x => norm(x.innerText));
    const iL = cab.findIndex(c => c.startsWith('LOTE')), iC = cab.findIndex(c => c.includes('COLOCADO'));
    if (iL < 0 || iC < 0) continue;
    const iD = cab.findIndex(c => c.startsWith('DESCRI')), iF = cab.findIndex(c => c === 'FASE' || c.startsWith('FASE'));
    const iM = cab.findIndex(c => c.includes('MELHOR'));
    const linhas = [];
    [...t.querySelectorAll('tbody tr')].forEach((tr, idx) => {
      const td = [...tr.querySelectorAll('td')];
      if (td.length < cab.length - 1) return;
      const v = i => i >= 0 && td[i] ? td[i].innerText.trim() : '';
      if (!v(iL)) return;
      linhas.push({idx, lote: v(iL), descricao: v(iD), fase: v(iF), primeiro: v(iC), melhor: v(iM),
                   botoes: tr.querySelectorAll('button, a').length});
    });
    if (linhas.length) return linhas;
  }
  return [];
}
"""

JS_ABRIR_REGISTRO = """
(lote) => {
  for (const t of document.querySelectorAll('table')) {
    if (!t.getClientRects().length) continue;
    const cab = [...t.querySelectorAll('thead th, tr:first-child th')].map(x => (x.innerText || '').trim().toUpperCase());
    const iL = cab.findIndex(c => c.startsWith('LOTE'));
    if (iL < 0 || !cab.some(c => c.includes('COLOCADO'))) continue;
    for (const tr of t.querySelectorAll('tbody tr')) {
      const td = [...tr.querySelectorAll('td')];
      if (!td[iL] || td[iL].innerText.trim() !== lote) continue;
      const bs = [...tr.querySelectorAll('button, a')].filter(b => b.getClientRects().length);
      if (!bs.length) return false;
      bs[bs.length - 1].click();   // último botão da linha = "Registros da sessão do lote"
      return true;
    }
  }
  return false;
}
"""

FASES_VIVAS = ("DISPUTA", "RANDÔMICO", "RANDOMICO", "FECHADO", "DESEMPATE", "ABERTO")
RE_PART = re.compile(r"^(.*?)\s*\(PARTICIPANTE\s*(\d+)\)\s*$", re.I)


def valor_br(txt):
    t = re.sub(r"[^\d,.-]", "", txt or "")
    if not t:
        return None
    try:
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def moeda_br(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v is not None else "—"


def _norm(t):
    import unicodedata
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper().split())


def montar_ranking(linhas, melhor_tabela):
    """linhas = [[horário, movimento, autor, descrição], ...] do 'Registros da sessão do lote'."""
    lances, excluidos = {}, set()
    for c in linhas:
        if len(c) < 4:
            continue
        mov, autor, desc = c[1].upper(), c[2], c[3]
        m = RE_PART.match(autor.strip())
        if not m:
            continue
        nome, num = m.group(1).strip(), m.group(2)
        if mov.strip() == "LANCE":
            v = valor_br(desc)
            if v is not None:
                lances.setdefault(num, {"nome": nome, "num": num, "lances": []})["lances"].append(v)
        elif re.search(r"DESCLASSIF|INABILIT|RECUSAD|EXCLU", _norm(mov + " " + desc)):
            excluidos.add(num)
        elif re.search(r"(RE)?CLASSIFICAD|HABILITAD|REVERT|RETORN", _norm(mov + " " + desc)):
            excluidos.discard(num)
    if not lances:
        return []
    todos = [v for x in lances.values() for v in x["lances"]]
    melhor = valor_br(melhor_tabela)
    maior_ganha = melhor is not None and abs(melhor - max(todos)) < 0.005 and abs(melhor - min(todos)) >= 0.005
    for x in lances.values():
        x["valor"] = max(x["lances"]) if maior_ganha else min(x["lances"])
        x["excluido"] = x["num"] in excluidos
    ordem = sorted(lances.values(), key=lambda x: (x["excluido"], -x["valor"] if maior_ganha else x["valor"]))
    pos = 0
    out = []
    for x in ordem:
        if not x["excluido"]:
            pos += 1
        out.append({"pos": pos if not x["excluido"] else None, "nome": x["nome"], "num": x["num"],
                    "valor": x["valor"], "excluido": x["excluido"], "qtd": len(x["lances"])})
    return out


def eh_nossa(nome):
    alvo = _norm(CFG.get("CLASSIFICACAO_NOME") or CFG.get("EMPRESA_NOME", ""))
    return bool(alvo) and alvo in _norm(nome)


def ler_classificacao(db, page, p, primeira_vez, diag=False):
    """Lê a classificação de cada lote do processo (já com a sala de disputa aberta). Retorna alertas novos."""
    if page.evaluate(JS_ABA_TODOS):
        page.wait_for_timeout(1800)
    lotes = avaliar(page, JS_LOTES) or []
    if diag:
        (DIAG / f"04_lotes_{p['processo'].replace('/', '-')}.json").write_text(
            json.dumps(lotes, ensure_ascii=False, indent=1), encoding="utf-8")
    novas, agora = [], datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    for lt in lotes:
        chave = hash_msg(p["processo"], p["promotor"], lt["lote"])
        ant = db.execute("SELECT fase, primeiro, melhor, nossa_pos, lido_em, ranking FROM classificacao WHERE chave=?",
                         (chave,)).fetchone()
        viva = any(f in lt["fase"].upper() for f in FASES_VIVAS)
        mudou = not ant or (ant[0], ant[1], ant[2]) != (lt["fase"], lt["primeiro"], lt["melhor"])
        velho = not ant or time.time() - (ant[4] or 0) > (120 if viva else 6 * 3600)
        if "CANCELAD" in lt["fase"].upper() or not (mudou or velho or diag):
            continue
        if not page.evaluate(JS_ABRIR_REGISTRO, lt["lote"]):
            continue
        dados = None
        for _ in range(20):
            page.wait_for_timeout(500)
            if page.evaluate("""() => [...document.querySelectorAll('.modal')].some(m =>
                  m.getClientRects().length > 0 && /registros da sess/i.test(m.innerText) && m.querySelector('table'))"""):
                page.wait_for_timeout(800)
                dados = page.evaluate(JS_LER_MODAL)
                break
        if not page.evaluate(JS_FECHAR_MODAL):
            page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if not dados:
            log(f"{p['processo']} lote {lt['lote']}: registros da sessão não abriram.")
            continue
        if diag:
            (DIAG / f"05_registros_{p['processo'].replace('/', '-')}_lote{lt['lote']}.json").write_text(
                json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        linhas = []
        for t in dados.get("tabelas", []):
            if any("MOVIMENTO" in c for c in t["cab"]):
                linhas += t["linhas"]
        ranking = montar_ranking(linhas, lt["melhor"])
        nossa = next((r for r in ranking if eh_nossa(r["nome"])), None)
        pos = nossa["pos"] if nossa else None
        db.execute("INSERT OR REPLACE INTO classificacao VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (chave, p["processo"], p["promotor"], lt["lote"], lt["descricao"], lt["fase"], lt["primeiro"],
                    lt["melhor"], json.dumps(ranking, ensure_ascii=False), pos,
                    moeda_br(nossa["valor"]) if nossa else "", time.time(), agora))
        db.commit()
        pos_ant = ant[3] if ant else None
        if ant and not primeira_vez and nossa and pos != pos_ant:
            lider = next((r for r in ranking if r["pos"] == 1), None)
            if pos is None:
                txt = f"L{lt['lote']}: sua empresa aparece como DESCLASSIFICADA/INABILITADA."
            elif pos_ant is None:
                txt = f"L{lt['lote']}: sua empresa entrou na classificação em {pos}º lugar ({moeda_br(nossa['valor'])})."
            else:
                seta = "subiu" if pos < pos_ant else "caiu"
                txt = f"L{lt['lote']}: você {seta} de {pos_ant}º para {pos}º ({moeda_br(nossa['valor'])})."
            if lider and pos != 1:
                txt += f"\n1º: {lider['nome']} – {moeda_br(lider['valor'])}"
            txt += f"\nFase: {lt['fase']}"
            mid = hash_msg(p["processo"], p["promotor"], "Classificação", agora, txt)
            db.execute("INSERT OR IGNORE INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, p["processo"], p["promotor"], "Classificação", agora, plataforma_nome(), txt, agora, 0))
            novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "📊 Classificação",
                          "horario": agora, "autor": plataforma_nome(), "texto": txt, "id": mid})
        time.sleep(random.uniform(0.8, 1.8))
    db.commit()
    return novas


# ----------------------------------------------------------------- ciclo
def processar(db, page, primeira_vez, diag=False):
    procs = ler_processos(page, diag)
    if procs is None:
        return "deslogado"
    ignorar = {s.strip().upper() for s in CFG["SITUACOES_IGNORADAS"].split(",") if s.strip()}
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novas = []

    for p in procs:
        antiga = db.execute("SELECT situacao FROM processos WHERE processo=? AND promotor=?",
                            (p["processo"], p["promotor"])).fetchone()
        valida = lambda x: bool(x) and not any(ch.isdigit() for ch in x)   # nome de fase, sem números
        if (antiga and antiga[0] != p["situacao"] and valida(antiga[0]) and valida(p["situacao"])
                and not primeira_vez):
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
        ir(page, p["url"])
        if not logado(page):
            return "deslogado"
        page.wait_for_timeout(1500)
        if diag:
            salvar_diag(page, f"02_disputa_{p['processo'].replace('/', '-')}")
        info = abrir_menu_chat(page)
        if not info:
            log(f"{p['processo']}: botão de mensagens não encontrado (rode --diagnostico).")
        for item in (info or {}).get("itens", []):
            rot, qtd = item["rotulo"], item["qtd"]
            chave = f'{p["processo"]}|{p["promotor"]}'
            ant = db.execute("SELECT qtd FROM contadores WHERE processo=? AND escopo=?",
                             (chave, rot)).fetchone()
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
                mid = hash_msg(p["processo"], p["promotor"], m["escopo"], m["horario"], m["autor"], m["texto"])
                if db.execute("SELECT 1 FROM mensagens WHERE id=?", (mid,)).fetchone():
                    continue
                db.execute("INSERT INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                           (mid, p["processo"], p["promotor"], m["escopo"], m["horario"],
                            m["autor"], m["texto"], agora, 0 if not primeira_vez else 1))
                if not primeira_vez:
                    novas.append({**m, "processo": p["processo"], "promotor": p["promotor"], "id": mid})
            db.execute("INSERT OR REPLACE INTO contadores VALUES(?,?,?)", (chave, rot, qtd))
            db.commit()
            time.sleep(random.uniform(1.0, 2.5))  # ritmo humano
        try:
            page.keyboard.press("Escape")  # fecha o menu de mensagens, se aberto
            novas += ler_classificacao(db, page, p, primeira_vez, diag)
        except Exception as ex:  # a classificação nunca pode atrapalhar as mensagens
            log(f"{p['processo']}: não consegui ler a classificação ({ex}).")

    # histórico: classificação final dos processos já encerrados (lida uma única vez, poucos por ciclo)
    lidos_hist = 0
    for p in procs:
        if lidos_hist >= int(CFG.get("HISTORICO_POR_CICLO", "8") or 8):
            break
        if p["situacao"].upper() not in ignorar or not p["url"] or "CANCELAD" in p["situacao"].upper():
            continue
        k = "hist_" + hash_msg(p["processo"], p["promotor"])
        if estado(db, k):
            continue
        try:
            ir(page, p["url"])
            if not logado(page):
                return "deslogado"
            page.wait_for_timeout(1500)
            ler_classificacao(db, page, p, True, diag)  # True = só registra, sem alertas
            log(f"{p['processo']}: classificação final (histórico) registrada.")
        except Exception as ex:
            log(f"{p['processo']}: não consegui ler o histórico ({ex}).")
        estado(db, k, datetime.now().strftime("%d/%m/%Y %H:%M"))
        lidos_hist += 1

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

    return notificar(db, novas, primeira_vez, len(ativos), agora)


def plataforma_nome():
    return {"licitanet": "Licitanet", "pcp": "Portal de Compras Públicas", "bnc": "BNC"}.get(CFG.get("PLATAFORMA", "bll").lower(), "BLL")


def notificar(db, novas, primeira_vez, n_ativos, agora):
    """Envia os alertas das mensagens novas (Telegram, ntfy, ligação) e o aviso de início."""
    import urgencia
    plat = plataforma_nome()
    e = html.escape
    so_urgente = CFG.get("ALARME_SO_URGENTE", "1") != "0"
    for m in novas:
        m["urgente"] = urgencia.classificar(m.get("texto", ""), m.get("escopo", ""), m.get("autor", ""))
    urgentes = [m for m in novas if m["urgente"]]
    if novas:  # um alarme (e uma ligação) por ciclo, não um por mensagem
        fortes = urgentes if so_urgente else novas
        if fortes:
            procs = sorted({m["processo"] for m in fortes if m["processo"]})
            motivos = sorted({m["urgente"] for m in fortes if m["urgente"]})
            ligar(f"Atenção. {CFG.get('EMPRESA_NOME','')}. {'Urgente: ' + ', '.join(motivos) if motivos else 'Nova mensagem'} "
                  f"na {plat}. Processo {', '.join(procs)}. Veja no Telegram.")
            resumo = "\n\n".join(f"🚨 {m['urgente'] or 'Nova'} · {m['processo']} · {m['autor']}\n{m['texto'][:300]}" for m in fortes[:5])
            alarme(f"URGENTE {plat}: {', '.join(motivos) or 'nova mensagem'} – {', '.join(procs)}", resumo, "5")
        comuns = [m for m in novas if not m["urgente"]] if so_urgente else []
        if comuns:
            procs = sorted({m["processo"] for m in comuns if m["processo"]})
            resumo = "\n\n".join(f"{m['processo']} · {m['escopo']} · {m['autor']}\n{m['texto'][:300]}" for m in comuns[:5])
            alarme(f"{plat}: {len(comuns)} nova(s) – {', '.join(procs)}", resumo, "3")
    for m in novas:
        topo = f"🚨 <b>URGENTE – {e(m['urgente'])}</b>\n" if m["urgente"] else ""
        txt = (f"{topo}🔔 <b>{plat} – Processo {e(m['processo'])}</b>\n"
               f"{e(m['promotor'])}\n"
               f"📌 {e(m['escopo'])} · {e(m['autor'])} · {e(m['horario'])}\n\n"
               f"{e(m['texto'])}")
        if telegram(txt) and m.get("id"):
            db.execute("UPDATE mensagens SET alertado=1 WHERE id=?", (m["id"],))
        time.sleep(0.5)
    db.commit()
    if primeira_vez:
        total = db.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
        telegram(f"✅ <b>Monitor {plat} ativo</b>\n{n_ativos} processo(s) em andamento vigiados, "
                 f"{total} mensagens já existentes registradas.\n"
                 f"A partir de agora você recebe aqui só as novas.")
        estado(db, "inicializado", agora)
    log(f"{len(novas)} mensagem(ns) nova(s).")
    return "ok"


# ================================================================= LICITANET
LN_LOGIN = "https://licita-sso.licitanet.com.br/login"
LN_PORTAL = "https://portal.licitanet.com.br"

JS_LN_PROCESSOS = """
() => {
  const out = [];
  for (const t of document.querySelectorAll('table')) {
    const cab = [...(t.querySelector('thead tr') || t.querySelector('tr') || {children: []}).children]
                  .map(x => x.innerText.trim().toUpperCase());
    if (!cab.includes('PROCESSO') || !cab.includes('STATUS')) continue;
    t.querySelectorAll('tbody tr').forEach((tr, idx) => {
      const tds = [...tr.children];
      if (tds.length < 3) return;
      const cel = {};
      cab.forEach((n, i) => { if (n && tds[i]) cel[n] = tds[i].innerText.replace(/\\s+/g, ' ').trim(); });
      const a = [...tr.querySelectorAll('a[href]')].find(x => /sala-disputa\\/\\d+/.test(x.href));
      out.push({cel, url: a ? a.href : '', idx});
    });
  }
  return out;
}
"""

JS_LN_ABRIR_ACOES = """
(idx) => {
  const linhas = [...document.querySelectorAll('table tbody tr')].filter(tr => tr.children.length >= 3);
  const tr = linhas[idx]; if (!tr) return false;
  const b = [...tr.querySelectorAll('button, a, i, span')].filter(x => x.getClientRects().length > 0).pop();
  if (!b) return false; b.click(); return true;
}
"""

JS_LN_LINK_SALA = """
() => {
  const a = [...document.querySelectorAll('a, button, li')].find(x =>
      x.getClientRects().length > 0 && /sala de disputa/i.test(x.innerText || ''));
  if (!a) return null;
  if (a.href && /sala-disputa/.test(a.href)) return {href: a.href};
  a.click(); return {clicado: true};
}
"""

JS_LN_LOTES = """
() => [...document.querySelectorAll('input[name="item_lote"]')].map(r => ({
  valor: r.value,
  rotulo: ((r.closest('label') || r.parentElement).innerText || ('Lote ' + r.value)).trim()
}))
"""

JS_LN_CLICAR_LOTE = """
(v) => {
  const r = [...document.querySelectorAll('input[name="item_lote"]')].find(x => x.value === v);
  if (!r) return false;
  (r.closest('label') || r).click();
  return true;
}
"""

JS_LN_MENSAGENS = """
() => [...document.querySelectorAll('.chat-list .chat-content, [id^="notificationMensagem"]')]
  .filter((el, i, arr) => arr.indexOf(el) === i)
  .map(el => {
    const cab = (el.querySelector('h5, h6, .text-muted') || {innerText: ''}).innerText.trim();
    const msg = (el.querySelector('.message') || el).innerText.replace(/\\s+\\n/g, '\\n').trim();
    const id = (el.id || '').replace('notificationMensagem', '');
    return {id, cab, msg};
  })
  .filter(m => m.msg)
"""

JS_LN_TITULO_CHAT = "() => ((document.querySelector('#title-chat') || {}).innerText || '').trim()"


def ln_logado(page):
    u = page.url.lower()
    return u.startswith(LN_PORTAL) and "login" not in u


def login_licitanet(page):
    """Usuário/senha no SSO → cartão 'PLATAFORMA LICITANET' → escolhe a empresa (CNPJ) → Logar."""
    usuario, senha = CFG.get("BLL_EMAIL", ""), CFG.get("BLL_SENHA", "")
    if not usuario or not senha:
        return "sem_credenciais"
    ir(page, LN_LOGIN)
    page.wait_for_timeout(2000)
    if "login" in page.url.lower():
        campo = page.locator("input[type=text]:visible, input[type=email]:visible, input:not([type]):visible").first
        campo.click()
        campo.fill("")
        campo.type(usuario, delay=random.randint(50, 120))
        pw = page.locator("input[type=password]:visible").first
        pw.click()
        pw.type(senha, delay=random.randint(50, 120))
        page.wait_for_timeout(400)
        page.get_by_role("button", name=re.compile("entrar", re.I)).first.click()
        page.wait_for_timeout(5000)
        if page.evaluate(JS_CAPTCHA_VISIVEL):
            return "captcha"
        if "login" in page.url.lower():
            log("Licitanet: login não concluído (usuário/senha?).")
            salvar_diag(page, "ln_login_falhou")
            return "falhou"
    # cartão da plataforma de fornecedores
    cartao = page.get_by_text(re.compile(r"PLATAFORMA LICITANET", re.I)).first
    try:
        cartao.click(timeout=10000)
    except Exception:
        log("Licitanet: cartão 'PLATAFORMA LICITANET' não encontrado.")
        salvar_diag(page, "ln_modulos")
        return "falhou"
    page.wait_for_timeout(2500)
    # janela 'Escolha uma opção': marca a empresa pelo CNPJ
    cnpj = re.sub(r"\D", "", CFG.get("EMPRESA_CNPJ", ""))
    ok = page.evaluate("""(cnpj) => {
      const radios = [...document.querySelectorAll('input[type=radio]')].filter(r => r.getClientRects().length > 0 || r.offsetParent);
      for (const r of radios) {
        const bloco = r.closest('div, li, label, tr') ;
        let el = r, txt = '';
        for (let i = 0; i < 5 && el; i++) { txt = (el.innerText || ''); if (/\\d{2}\\.\\d{3}\\.\\d{3}/.test(txt)) break; el = el.parentElement; }
        if (!cnpj || txt.replace(/\\D/g, '').includes(cnpj)) { r.click(); return true; }
      }
      return radios.length === 0 ? 'sem_radio' : false;
    }""", cnpj)
    if ok is False:
        log(f"Licitanet: empresa com CNPJ {CFG.get('EMPRESA_CNPJ','')} não encontrada na janela de escolha.")
        salvar_diag(page, "ln_escolha_empresa")
        return "falhou"
    page.wait_for_timeout(1200)
    # tipo de funcionário: primeira opção válida (ou a que contém LICITANET_FUNCAO)
    funcao = CFG.get("LICITANET_FUNCAO", "")
    page.evaluate("""(funcao) => {
      for (const s of document.querySelectorAll('select')) {
        if (!(s.getClientRects().length > 0)) continue;
        const ops = [...s.options].filter(o => o.value && !/selecione/i.test(o.text));
        const alvo = ops.find(o => funcao && o.text.toUpperCase().includes(funcao.toUpperCase())) || ops[0];
        if (alvo) { s.value = alvo.value; s.dispatchEvent(new Event('change', {bubbles: true})); }
      }
    }""", funcao)
    page.wait_for_timeout(800)
    try:
        page.get_by_role("button", name=re.compile(r"^\s*logar\s*$", re.I)).first.click(timeout=8000)
    except Exception:
        log("Licitanet: botão 'Logar' não encontrado.")
        salvar_diag(page, "ln_logar")
        return "falhou"
    for _ in range(40):
        page.wait_for_timeout(1000)
        if ln_logado(page) and "home" in page.url.lower():
            break
    if not ln_logado(page):
        salvar_diag(page, "ln_portal")
        return "falhou"
    page.wait_for_timeout(2500)
    estado_url = page.url
    CFG["_LN_HOME"] = estado_url
    log(f"Licitanet: login ok ({estado_url}).")
    return "ok"


JS_LN_ESTADO_HOME = """
() => {
  if (document.querySelector('input[type=password]')) return 'login';
  const t = document.body ? document.body.innerText : '';
  const tabela = [...document.querySelectorAll('table')].some(tb => /PROCESSO/i.test(tb.innerText) && /STATUS/i.test(tb.innerText));
  if (tabela || /Acompanhe e participe dos processos/i.test(t)) return 'ok';
  if (/sess[aã]o expirada|fa[cç]a login|n[aã]o autenticado|unauthorized/i.test(t)) return 'login';
  return 'carregando';
}
"""


def ln_ler_processos(page, diag=False):
    ir(page, CFG.get("_LN_HOME") or f"{LN_PORTAL}/home-avulso")
    est = "carregando"
    for _ in range(15):
        page.wait_for_timeout(1000)
        if not ln_logado(page):
            return None
        est = page.evaluate(JS_LN_ESTADO_HOME)
        if est != "carregando":
            break
    if est != "ok":
        log(f"Licitanet: lista de processos não apareceu ({est}). URL: {page.url} | título: {page.title()}")
        salvar_diag(page, "ln_sem_lista")
        return None  # trata como sessão caída: faz login de novo
    if diag:
        salvar_diag(page, "ln_01_processos")
    linhas = page.evaluate(JS_LN_PROCESSOS)
    procs = []
    for r in linhas:
        c = r["cel"]
        proc = re.sub(r"\s*-\s*Modo.*$", "", c.get("PROCESSO", "")).strip()
        if not proc:
            continue
        info = c.get("INFORMAÇÕES", "")
        m = re.search(r"Início:\s*([\d/]+\s*-\s*[\dh:]+)", info)
        procs.append({"processo": proc, "promotor": c.get("ORGAO COMPRADOR") or c.get("ÓRGÃO COMPRADOR") or c.get("ORGÃO COMPRADOR", ""),
                      "situacao": c.get("STATUS", ""), "disputa": m.group(1) if m else "",
                      "atualizada": "", "url": r["url"], "idx": r["idx"]})
    return procs


def ln_url_sala(page, p):
    """Descobre o endereço da sala de disputa abrindo o menu de ações (⋮) da linha."""
    if p["url"]:
        return p["url"]
    if not page.evaluate(JS_LN_ABRIR_ACOES, p["idx"]):
        return ""
    page.wait_for_timeout(800)
    r = page.evaluate(JS_LN_LINK_SALA)
    if not r:
        page.keyboard.press("Escape")
        return ""
    if r.get("href"):
        page.keyboard.press("Escape")
        return r["href"]
    for _ in range(20):
        page.wait_for_timeout(500)
        if "sala-disputa" in page.url:
            return page.url
    return ""


def processar_licitanet(db, page, primeira_vez, diag=False):
    procs = ln_ler_processos(page, diag)
    if procs is None:
        return "deslogado"
    ignorar = {s.strip().upper() for s in CFG["SITUACOES_IGNORADAS"].split(",") if s.strip()}
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novas = []
    valida = lambda x: bool(x) and not any(ch.isdigit() for ch in x)  # noqa: E731
    for p in procs:
        antiga = db.execute("SELECT situacao, url FROM processos WHERE processo=? AND promotor=?",
                            (p["processo"], p["promotor"])).fetchone()
        if antiga and not p["url"] and antiga[1]:
            p["url"] = antiga[1]
        if (antiga and antiga[0] != p["situacao"] and valida(antiga[0]) and valida(p["situacao"])
                and not primeira_vez):
            novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "Mudança de situação",
                          "horario": agora, "autor": "Licitanet", "texto": f"{antiga[0]} → {p['situacao']}", "id": None})
    ativos = [p for p in procs if p["situacao"].upper() not in ignorar]
    log(f"Licitanet: {len(procs)} processos na lista, {len(ativos)} em andamento.")
    if not procs:
        salvar_diag(page, "ln_lista_vazia")

    for p in ativos:
        if not p["url"]:
            ir(page, CFG.get("_LN_HOME") or f"{LN_PORTAL}/home-avulso")
            page.wait_for_timeout(2000)
            p["url"] = ln_url_sala(page, p)
        if not p["url"]:
            log(f"{p['processo']}: não achei a sala de disputa (rode --diagnostico).")
            continue
        ir(page, p["url"])
        page.wait_for_timeout(2500)
        if not ln_logado(page):
            return "deslogado"
        if diag:
            salvar_diag(page, f"ln_02_sala_{p['processo'].replace('/', '-').replace(' ', '')}")
        vistas = {}
        lotes = page.evaluate(JS_LN_LOTES) or [{"valor": "", "rotulo": ""}]
        maxl = int(CFG.get("LICITANET_MAX_LOTES", "80") or 80)
        for lote in lotes[:maxl]:
            if lote["valor"]:
                page.evaluate(JS_LN_CLICAR_LOTE, lote["valor"])
                page.wait_for_timeout(1300)
            escopo = page.evaluate(JS_LN_TITULO_CHAT) or lote["rotulo"] or "Chat"
            for m in page.evaluate(JS_LN_MENSAGENS):
                vistas.setdefault(m["id"] or hash_msg(m["cab"], m["msg"]), (escopo, m))
        # mensagens gerais (caixa "Mensagem Geral")
        if page.evaluate("() => { const c = document.querySelector('#checkBoxChatGeral'); if (!c) return false; if (!c.checked) c.click(); return true; }"):
            page.wait_for_timeout(1500)
            for m in page.evaluate(JS_LN_MENSAGENS):
                vistas.setdefault(m["id"] or hash_msg(m["cab"], m["msg"]), ("Mensagem geral", m))
        for chave, (escopo, m) in vistas.items():
            autor, horario = m["cab"], ""
            mm = re.match(r"(.*?)\s*-\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}(?::\d{2})?)\s*$", m["cab"])
            if mm:
                autor, horario = mm.group(1).strip(), mm.group(2)
            mid = hash_msg("licitanet", p["processo"], p["promotor"], str(chave))
            if db.execute("SELECT 1 FROM mensagens WHERE id=?", (mid,)).fetchone():
                continue
            db.execute("INSERT INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, p["processo"], p["promotor"], escopo.title(), horario, autor or "—",
                        m["msg"], agora, 0 if not primeira_vez else 1))
            if not primeira_vez:
                novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": escopo.title(),
                              "horario": horario, "autor": autor or "—", "texto": m["msg"], "id": mid})
        db.commit()
        time.sleep(random.uniform(1.0, 2.5))

    for p in procs:
        db.execute("INSERT OR REPLACE INTO processos VALUES(?,?,?,?,?,?,?)",
                   (p["processo"], p["promotor"], p["situacao"], p["disputa"], p["atualizada"], p["url"], agora))
    db.commit()
    if primeira_vez and not procs:
        log("Licitanet: nenhum processo lido ainda; aguardando para registrar o histórico.")
        return "ok"
    return notificar(db, novas, primeira_vez, len(ativos), agora)


def ir(page, url, tentativas=2):
    """Abre a página sem depender de a rede ficar 100% parada (alguns sites nunca param)."""
    for i in range(tentativas):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception as e:
            if i == tentativas - 1 or "Timeout" not in str(e):
                raise
            log(f"Página demorou para abrir ({url[:60]}…); tentando de novo.")
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass  # rede nunca parou: segue com o que já carregou


def avaliar(page, js, arg=None, tentativas=4):
    """page.evaluate tolerante a recarregamentos/redirecionamentos da página."""
    for i in range(tentativas):
        try:
            return page.evaluate(js) if arg is None else page.evaluate(js, arg)
        except Exception as e:
            if "context was destroyed" not in str(e) and "navigat" not in str(e).lower() or i == tentativas - 1:
                raise
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)


# ================================================================= PORTAL DE COMPRAS PÚBLICAS
PCP_BASE = "https://operacao.portaldecompraspublicas.com.br"
PCP_LISTAS = ["/4/SuasPropostas/", "/4/SeusPregoes/"]

JS_PCP_LOGADO = """
() => {
  if (/iam\\.secure/.test(location.host)) return 'login';
  if (document.querySelector('input[type=password]')) return 'login';
  const t = document.body ? document.body.innerText : '';
  return /Voc[eê] est[aá] logado como/i.test(t) ? 'ok' : 'nao';
}
"""

JS_PCP_LISTA = """
() => {
  const out = [];
  for (const t of document.querySelectorAll('table')) {
    const cabRow = t.querySelector('thead tr') || t.querySelector('tr');
    if (!cabRow) continue;
    const cab = [...cabRow.children].map(x => x.innerText.trim().toUpperCase());
    if (!cab.includes('NÚMERO') && !cab.includes('NUMERO')) continue;
    if (!cab.some(c => c.startsWith('SITUA'))) continue;
    for (const tr of t.querySelectorAll('tbody tr')) {
      const tds = [...tr.children]; if (tds.length < 4) continue;
      const cel = {}; let sit = '';
      cab.forEach((n, i) => {
        if (!tds[i]) return;
        cel[n] = tds[i].innerText.replace(/\\s+/g, ' ').trim();
        if (n.startsWith('SITUA')) {
          const el = tds[i].querySelector('[title], img[alt]');
          sit = el ? (el.getAttribute('title') || el.getAttribute('alt') || '') : cel[n];
        }
      });
      const links = [...tr.querySelectorAll('a[href]')].map(a => a.href);
      const comChave = links.find(h => /ttCD_CHAVE=\\d+/.test(h)) || '';
      const m = comChave.match(/ttCD_CHAVE=(\\d+)/);
      const onclick = [...tr.querySelectorAll('[onclick]')].map(x => x.getAttribute('onclick')).join(' ');
      const m2 = onclick.match(/(\\d{5,})/);
      const titulos = [...tr.querySelectorAll('[title]')].map(x => x.getAttribute('title')).join(' | ');
      out.push({cel, sit, chave: m ? m[1] : (m2 ? m2[1] : ''), titulos});
    }
  }
  return out;
}
"""

JS_PCP_DOCS = """
() => [...document.querySelectorAll('#tabTableSorter tbody tr, table tbody tr')].map(tr => {
  const tds = [...tr.children]; if (tds.length < 3) return null;
  const a0 = tds[0].querySelector('a');
  const nome = (a0 && a0.getAttribute('title')) || tds[0].innerText.trim();
  const tipo = (tds[1].querySelector('a') && tds[1].querySelector('a').getAttribute('title')) || tds[1].innerText.trim();
  const quando = tds[2].innerText.trim();
  const dl = tr.querySelector('a[href*="Download"]');
  const id = dl ? ((dl.href.match(/ttCD_CHAVE=(\\d+)/) || [])[1] || dl.href) : (nome + '|' + quando);
  return {nome, tipo, quando, id, url: dl ? dl.href : ''};
}).filter(x => x && x.nome && /\\d{2}\\/\\d{2}\\/\\d{4}/.test(x.quando))
"""


def pcp_estado(page):
    try:
        return avaliar(page, JS_PCP_LOGADO)
    except Exception:
        return "nao"


def login_pcp(page):
    usuario, senha = CFG.get("BLL_EMAIL", ""), CFG.get("BLL_SENHA", "")
    if not usuario or not senha:
        return "sem_credenciais"
    ir(page, f"{PCP_BASE}/")
    page.wait_for_timeout(2500)
    if pcp_estado(page) == "ok":
        return "ok"
    if pcp_estado(page) != "login":
        ir(page, f"{PCP_BASE}/4/NaoAssinante/DashBoard/")
        page.wait_for_timeout(2000)
    campo = page.locator("#username, input[name=username], input[type=text]:visible, input[type=email]:visible").first
    try:
        campo.click(timeout=10000)
    except Exception:
        salvar_diag(page, "pcp_login")
        return "falhou"
    campo.fill("")
    campo.type(usuario, delay=random.randint(50, 120))
    pw = page.locator("#password, input[type=password]:visible").first
    pw.click()
    pw.type(senha, delay=random.randint(50, 120))
    page.wait_for_timeout(400)
    btn = page.locator("#kc-login, button[type=submit], input[type=submit]").first
    try:
        btn.click(timeout=5000)
    except Exception:
        page.get_by_role("button", name=re.compile("entrar", re.I)).first.click()
    for _ in range(30):
        page.wait_for_timeout(1000)
        if avaliar(page, JS_CAPTCHA_VISIVEL):
            return "captcha"
        if pcp_estado(page) == "ok":
            log("Portal de Compras Públicas: login ok.")
            return "ok"
    erro = page.evaluate("() => ((document.querySelector('#input-error, .alert-error, .kc-feedback-text, .alert') || {}).innerText || '').trim()")
    log(f"Portal de Compras Públicas: login não concluído. {erro[:200]}")
    salvar_diag(page, "pcp_login_falhou")
    return "falhou"


def pcp_ler_processos(page, diag=False):
    procs, vistos = [], set()
    for caminho in PCP_LISTAS:
        ir(page, PCP_BASE + caminho)
        page.wait_for_timeout(1500)
        if pcp_estado(page) != "ok":
            return None
        try:
            page.get_by_role("button", name=re.compile(r"^(Rejeitar|Aceitar)$", re.I)).first.click(timeout=1500)
        except Exception:
            pass
        if diag:
            salvar_diag(page, "pcp_lista_" + caminho.strip("/").split("/")[-1])
        for r in avaliar(page, JS_PCP_LISTA):
            c = r["cel"]
            num = c.get("NÚMERO") or c.get("NUMERO", "")
            chave = r["chave"] or num
            if not num or chave in vistos:
                continue
            vistos.add(chave)
            procs.append({"processo": num, "promotor": c.get("UNIDADE COMPRADORA", ""),
                          "situacao": (r["sit"] or "").strip(), "disputa": c.get("ABERTURA", ""),
                          "atualizada": "", "url": f"{PCP_BASE}/Atas/?sIA=View&ttCD_CHAVE={r['chave']}&iframe=true" if r["chave"] else "",
                          "chave": r["chave"], "objeto": c.get("OBJETO", "")})
    return procs


PCP_SITE = "https://www.portaldecompraspublicas.com.br"
JS_ACHAR_PUBLICO = r"""
(chave) => {
  let html = document.documentElement.outerHTML.replace(/&amp;/g, '&');
  try { html = decodeURIComponent(html); } catch (e) { html = html.replace(/%2F/gi, '/').replace(/%3A/gi, ':'); }
  const re = new RegExp(String.raw`https?://(?:www\.)?portaldecompraspublicas\.com\.br/processos/[^\s"'<>&?#]*-` + chave + '(?![0-9])', 'g');
  const m = html.match(re);
  if (m) return m[0];
  const a = [...document.querySelectorAll('a[href*="/processos/"]')].map(x => x.href).find(h => new RegExp('-' + chave + '/?$').test(h));
  return a || '';
}
"""


def pcp_url_publica(db, page, p):
    """Descobre (e guarda) o endereço da página pública do processo."""
    k = f"pcp_url_{p['chave']}"
    url = estado(db, k)
    if url:
        return url
    # 1) página do processo na área do fornecedor (link/QR Code apontam para a página pública)
    for caminho in (f"/4/Pregoes/DadosPregao/?sIA=Edit&ttCD_CHAVE={p['chave']}", p.get("url") or ""):
        if not caminho:
            continue
        try:
            ir(page, caminho if caminho.startswith("http") else PCP_BASE + caminho)
            page.wait_for_timeout(1500)
            url = avaliar(page, JS_ACHAR_PUBLICO, p["chave"])
        except Exception:
            url = ""
        if url:
            break
    # 2) busca pública pelo número do processo
    if not url:
        for q in (f"processo={urllib.parse.quote(p['processo'])}", f"objeto={urllib.parse.quote(p.get('objeto', '')[:40])}"):
            try:
                ir(page, f"{PCP_SITE}/processos?filtroEspecial=0&pagina=1&{q}")
                page.wait_for_timeout(5000)
                url = avaliar(page, JS_ACHAR_PUBLICO, p["chave"])
            except Exception:
                url = ""
            if url:
                break
    if url and url.startswith("/"):
        url = PCP_SITE + url
    if url:
        estado(db, k, url)
        log(f"PCP {p['processo']}: página pública encontrada ({url}).")
    else:
        log(f"PCP {p['processo']}: não achei a página pública do processo (chave {p['chave']}).")
    return url


def pcp_baixar_ranking(page, url, chave):
    """Abre a página pública do processo (logado) e baixa o PDF 'Ranking nos Itens'. Só leitura."""
    ir(page, url)
    page.wait_for_timeout(8000)
    for rot in ("Rejeitar", "Aceitar"):
        try:
            page.get_by_text(rot, exact=True).first.click(timeout=2000)
            break
        except Exception:
            pass
    for _ in range(2):  # fecha janelas de propaganda ("Aviso de licitações")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    card = page.locator(".step-card", has_text="Ranking nos Itens").first
    if not card.count():
        log(f"PCP {chave}: sem 'Ranking nos Itens' na página pública ainda ({page.url}).")
        return None
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    botao = card.locator("button").first
    achados = []
    padrao = re.compile(r"https://arquivos\.portaldecompraspublicas\.com\.br/v1/download/[0-9a-fA-F]+")

    def segurar(route):  # não deixa o navegador baixar: só anota o endereço do arquivo
        achados.append(route.request.url)
        route.abort()

    def resposta(r):
        if "arquivo/download" in r.url:
            try:
                achados.extend(padrao.findall(r.text()))
            except Exception:
                pass

    ctx = page.context
    ctx.route("https://arquivos.portaldecompraspublicas.com.br/**", segurar)
    page.on("response", resposta)
    dados, erro = None, ""
    try:
        for tentativa in range(4):  # o site às vezes ainda não está pronto para o clique
            if tentativa == 3:  # última chance: recarrega a página e espera mais
                ir(page, url)
                page.wait_for_timeout(10000)
                card = page.locator(".step-card", has_text="Ranking nos Itens").first
                botao = card.locator("button").first
            try:
                page.mouse.move(300 + 50 * tentativa, 400)
                botao.scroll_into_view_if_needed()
                botao.click(timeout=10000)
            except Exception as ex:
                erro = str(ex)[:120]
            for _ in range(40):  # até 20 s esperando o endereço do arquivo
                if achados:
                    break
                page.wait_for_timeout(500)
            if achados:
                break
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(2000)
    finally:
        try:
            ctx.unroute("https://arquivos.portaldecompraspublicas.com.br/**", segurar)
            page.remove_listener("response", resposta)
        except Exception:
            pass
    url = next((u for u in achados if padrao.match(u)), None)
    if not url:
        log(f"PCP {chave}: o site não gerou o arquivo do ranking ({erro or 'sem resposta ao clique'}).")
        return None
    dados = None
    try:  # baixa com a mesma sessão do navegador, sem abrir o PDF
        r = ctx.request.get(url, timeout=90000)
        dados = r.body() if r.ok else None
    except Exception as ex:
        erro = str(ex)[:120]
    if not dados or dados[:4] != b"%PDF":
        try:
            r = requests.get(url, timeout=90)
            dados = r.content if r.ok else None
        except Exception as ex:
            log(f"PCP {chave}: falha ao baixar o ranking ({ex} / {erro}).")
            return None
    return dados if dados[:4] == b"%PDF" else None


def pcp_classificacao(db, page, p, primeira_vez):
    """Baixa e grava a classificação de cada item do processo; devolve alertas de mudança de posição."""
    import pcp_ranking
    url = pcp_url_publica(db, page, p)
    if not url:
        return None
    aba = page.context.new_page()  # aba própria: se algo der errado, não afeta a janela principal
    try:
        dados = pcp_baixar_ranking(aba, url, p["chave"])
    finally:
        try:
            aba.close()
        except Exception:
            pass
    if not dados:
        return None
    itens = pcp_ranking.classificar(pcp_ranking.ranking_pdf(dados),
                                    CFG.get("CLASSIFICACAO_NOME") or CFG.get("EMPRESA_NOME", ""), CFG.get("EMPRESA_CNPJ", ""))
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novas = []
    for n, it in itens.items():
        chave = f"pcp|{p['processo']}|{p['promotor']}|{n}"
        ant = db.execute("SELECT nossa_pos, nosso_lance FROM classificacao WHERE chave=?", (chave,)).fetchone()
        rk = [{"pos": x["pos"], "nome": x["nome"], "num": x["cnpj"], "valor": x["valor"] or 0, "excluido": x["excluido"],
               "qtd": "", "marca": x.get("marca", ""), "tipo": x.get("tipo", "")} for x in it["ranking"]]
        nossa = next((x for x in it["ranking"] if x["nossa"]), None)
        lider = next((x for x in it["ranking"] if x["pos"] == 1), None)
        pos = nossa["pos"] if nossa else None
        db.execute("INSERT OR REPLACE INTO classificacao VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (chave, p["processo"], p["promotor"], n, it["descricao"][:200], "Ranking (PDF)",
                    lider["nome"] if lider else "", moeda_br(lider["valor"]) if lider else "",
                    json.dumps(rk, ensure_ascii=False), pos, moeda_br(nossa["valor"]) if nossa else "", time.time(), agora))
        if ant and not primeira_vez and nossa and (pos != ant[0] or moeda_br(nossa["valor"]) != ant[1]):
            if pos is None:
                txt = f"L{int(n) if str(n).isdigit() else n}: sua empresa aparece como DESCLASSIFICADA/INABILITADA/REJEITADA."
            elif ant[0] is None:
                txt = f"L{int(n) if str(n).isdigit() else n}: sua empresa está em {pos}º lugar ({moeda_br(nossa['valor'])})."
            elif pos != ant[0]:
                txt = f"L{int(n) if str(n).isdigit() else n}: você {'subiu' if pos < ant[0] else 'caiu'} de {ant[0]}º para {pos}º ({moeda_br(nossa['valor'])})."
            else:
                txt = f"L{int(n) if str(n).isdigit() else n}: seu valor mudou para {moeda_br(nossa['valor'])} – continua em {pos}º."
            if lider and pos != 1:
                txt += f"\n1º: {lider['nome']} – {moeda_br(lider['valor'])}"
            txt += f"\n{it['descricao'][:90]}"
            mid = hash_msg("pcp-classif", p["chave"], n, agora, txt)
            db.execute("INSERT OR IGNORE INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, p["processo"], p["promotor"], "Classificação", agora, "Portal", txt, agora, 0))
            novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "📊 Classificação",
                          "horario": agora, "autor": "Portal", "texto": txt, "id": mid})
    db.commit()
    log(f"PCP {p['processo']}: classificação de {len(itens)} item(ns) lida.")
    return novas


def processar_pcp(db, page, primeira_vez, diag=False):
    procs = pcp_ler_processos(page, diag)
    if procs is None:
        return "deslogado"
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    novas = []
    for p in procs:
        antiga = db.execute("SELECT situacao FROM processos WHERE processo=? AND promotor=?",
                            (p["processo"], p["promotor"])).fetchone()
        if antiga and antiga[0] and p["situacao"] and antiga[0] != p["situacao"] and not primeira_vez:
            novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "Mudança de situação",
                          "horario": agora, "autor": "Portal", "texto": f"{antiga[0]} → {p['situacao']}", "id": None})
        db.execute("INSERT OR REPLACE INTO processos VALUES(?,?,?,?,?,?,?)",
                   (p["processo"], p["promotor"], p["situacao"], p["disputa"], p["atualizada"], p["url"], agora))
    db.commit()
    log(f"Portal de Compras Públicas: {len(procs)} processo(s) acompanhados.")
    if not procs:
        salvar_diag(page, "pcp_lista_vazia")
        if primeira_vez:
            return "ok"

    for p in procs:
        if not p["url"]:
            continue
        ir(page, p["url"])
        page.wait_for_timeout(1500)
        if pcp_estado(page) == "login":
            return "deslogado"
        if diag:
            salvar_diag(page, f"pcp_docs_{p['processo'].replace('/', '-')}")
        for d in avaliar(page, JS_PCP_DOCS):
            mid = hash_msg("pcp", p["chave"], str(d["id"]))
            if db.execute("SELECT 1 FROM mensagens WHERE id=?", (mid,)).fetchone():
                continue
            texto = f"📄 Novo documento: {d['tipo']}\n{d['nome']}"
            db.execute("INSERT INTO mensagens VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, p["processo"], p["promotor"], "Documento", d["quando"], d["tipo"], texto,
                        agora, 0 if not primeira_vez else 1))
            if not primeira_vez:
                novas.append({"processo": p["processo"], "promotor": p["promotor"], "escopo": "Documento",
                              "horario": d["quando"], "autor": d["tipo"], "texto": texto, "id": mid})
        db.commit()
        time.sleep(random.uniform(1.0, 2.5))

    # classificação por item (PDF "Ranking nos Itens" da página pública) – a cada 30 min por processo
    for p in procs:
        if not p.get("chave"):
            continue
        k = f"pcp_ranking_{p['chave']}"
        ultimo = float(estado(db, k) or 0)
        if time.time() - ultimo < 30 * 60 and not diag:
            continue
        encerrado = re.search(r"finaliz|homolog|encerrad|fracass|desert|revog|anulad|cancel", (p.get("situacao") or "").lower())
        ja_lido = db.execute("SELECT 1 FROM classificacao WHERE processo=? AND promotor=? LIMIT 1",
                             (p["processo"], p["promotor"])).fetchone()
        if encerrado and ja_lido and not diag:  # processo encerrado: a classificação final já está guardada
            continue
        r = None
        try:
            r = pcp_classificacao(db, page, p, primeira_vez)
        except Exception as ex:  # nunca atrapalha mensagens/documentos
            log(f"PCP {p['processo']}: erro ao ler classificação ({ex}).")
        novas += r or []
        # deu certo: próxima leitura em 30 min; falhou: tenta de novo em 10 min
        estado(db, k, str(time.time() if r is not None else time.time() - 20 * 60))
    return notificar(db, novas, primeira_vez, len(procs), agora)


# ----------------------------------------------------------------- painel
def gerar_painel(db, status):
    e = html.escape
    try:  # o painel web central lê estes dados
        estado(db, "status", status)
        estado(db, "ultima_verificacao", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    except Exception:
        pass
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
<title>Licitatudo {e(CFG.get("EMPRESA_NOME",""))}</title><style>
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
</style></head><body><header><h1>Licitatudo – Painel de alertas{(" – " + e(CFG.get("EMPRESA_NOME",""))) if CFG.get("EMPRESA_NOME") else ""}</h1>
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
        str(PERFIL), headless=not visivel, locale="pt-BR", accept_downloads=True,
        viewport={"width": 1366, "height": 800})


def modo_login():
    with sync_playwright() as pw:
        ctx = contexto(pw, True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/Home/Login")
        print("\nEntre na BLL na janela que abriu (usuário, senha e, se pedir, o perfil de PARTICIPANTE).")
        input("Quando estiver vendo a tela de Notificações/Processos, volte aqui e tecle ENTER... ")
        ir(page, URL_PROPOSTAS)
        print("Login salvo." if logado(page) else "Ainda parece deslogado. Tente de novo.")
        ctx.close()


def dentro_do_horario():
    h = datetime.now().hour
    return int(CFG["HORARIO_INICIO"]) <= h < int(CFG["HORARIO_FIM"])


class VagaNavegador:
    """Limita quantas empresas abrem o navegador ao mesmo tempo (economiza memória do servidor)."""
    def __init__(self):
        import fcntl
        self.fcntl, self.f = fcntl, None
        self.max = max(1, int(CFG.get("MAX_NAVEGADORES", "3") or 3))
        (PASTA / "locks").mkdir(exist_ok=True)

    def __enter__(self):
        avisou = False
        while True:
            for i in range(self.max):
                f = open(PASTA / "locks" / f"vaga{i}.lock", "w")
                try:
                    self.fcntl.flock(f, self.fcntl.LOCK_EX | self.fcntl.LOCK_NB)
                    self.f = f
                    return self
                except OSError:
                    f.close()
            if not avisou:
                log("Aguardando vaga de navegador (outras empresas verificando)...")
                avisou = True
            time.sleep(3)

    def __exit__(self, *a):
        if self.f:
            self.fcntl.flock(self.f, self.fcntl.LOCK_UN)
            self.f.close()


def rodar(uma_vez=False, visivel=False, diag=False):
    db = abrir_banco()
    intervalo = max(1.0, float(CFG["INTERVALO_MINUTOS"].replace(",", ".")))  # mínimo 1 minuto
    avisou_logout = False
    falhas_login = 0
    erros_seguidos = 0
    gerar_painel(db, "Iniciando")
    if CFG.get("PLATAFORMA", "bll").lower() == "licitanet":
        fn_processar, fn_login = processar_licitanet, login_licitanet
    elif CFG.get("PLATAFORMA", "bll").lower() == "pcp":
        fn_processar, fn_login = processar_pcp, login_pcp
    else:
        fn_processar, fn_login = processar, login_automatico
    if CFG.get("EMPRESA_ID"):
        time.sleep(random.uniform(0, 20))  # espalha as empresas para não verificarem todas juntas
    while True:
      with VagaNavegador(), sync_playwright() as pw:
        inicio_ciclo = time.time()
        ctx = contexto(pw, visivel)
        ctx.set_default_navigation_timeout(60000)  # sites lentos: espera até 60 s antes de desistir
        ctx.set_default_timeout(45000)
        arq_cookies = PERFIL.parent / "cookies_sessao.json"
        try:  # recupera a sessão (cookies de sessão somem quando o navegador fecha)
            if arq_cookies.exists():
                ctx.add_cookies(json.loads(arq_cookies.read_text(encoding="utf-8")))
        except Exception as e:
            log(f"Não consegui restaurar a sessão: {e}")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if True:
            if dentro_do_horario():
                try:
                    primeira = estado(db, "inicializado") is None
                    r = fn_processar(db, page, primeira, diag)
                    # sessão caiu: tenta entrar sozinho (no máximo 2 vezes seguidas, para não bloquear a conta)
                    if r == "deslogado" and falhas_login < 2 and CFG.get("BLL_EMAIL"):
                        res = fn_login(page)
                        log(f"Login automático: {res}")
                        if res == "ok":
                            falhas_login = 0
                            r = fn_processar(db, page, primeira, diag)
                        elif res == "captcha":
                            falhas_login = 99  # não insiste com captcha
                            telegram(f"🧩 <b>Monitor {plataforma_nome()}</b>: a {plataforma_nome()} pediu captcha no login. "
                                     "Faça o login manualmente para o monitor voltar a funcionar.")
                        else:
                            falhas_login += 1
                    if r == "deslogado":
                        log(f"Sessão da {plataforma_nome()} expirou ou não foi possível entrar.")
                        if not avisou_logout:
                            telegram(f"⚠️ <b>Monitor {plataforma_nome()}</b>: não consegui entrar na plataforma "
                                     "(sessão expirada e login automático sem sucesso). "
                                     "Confira usuário e senha da empresa no painel (aba Empresas).")
                            avisou_logout = True
                        gerar_painel(db, "⚠️ Sessão expirada – faça login de novo")
                    else:
                        avisou_logout = False
                        falhas_login = 0
                        erros_seguidos = 0
                        gerar_painel(db, "Monitor ativo")
                except Exception as e:  # nunca derrubar o loop
                    log(f"Erro no ciclo: {e!r}")
                    erros_seguidos += 1
                    if "Timeout" in str(e) and erros_seguidos < 3:
                        gerar_painel(db, "⏳ Site da plataforma lento no último ciclo – tentando de novo")
                    else:
                        gerar_painel(db, f"Erro no último ciclo: {e}")
            else:
                gerar_painel(db, "Fora do horário de monitoramento")
        try:
            arq_cookies.write_text(json.dumps(ctx.cookies()), encoding="utf-8")
            os.chmod(arq_cookies, 0o600)
        except Exception:
            pass
        ctx.close()
        estado(db, "duracao_ciclo", str(int(time.time() - inicio_ciclo)))
      if uma_vez:
          break
      base = intervalo * 60
      espera = int(base + random.uniform(-0.15, 0.15) * base)  # variação de ±15% para não parecer robô
      log(f"Próxima verificação em {espera} s.")
      time.sleep(espera)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Monitor de mensagens BLL Compras")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--teste", action="store_true")
    ap.add_argument("--diagnostico", action="store_true")
    ap.add_argument("--ligar", action="store_true", help="testa a ligação pelo Telegram")
    ap.add_argument("--alarme", action="store_true", help="testa o alarme do app ntfy")
    ap.add_argument("--empresa", help="id da empresa (arquivo empresas/<id>.env)")
    a = ap.parse_args()
    if a.empresa:
        usar_empresa(a.empresa)
    if a.login:
        modo_login()
    elif a.telegram:
        descobrir_chat_id()
    elif a.ligar:
        print("OK" if ligar("Teste do monitor BLL. A ligação está funcionando.") else "Ligação não configurada/falhou.")
    elif a.alarme:
        telegram("🔔 Teste de alerta do monitor BLL.")
        print("OK" if alarme("Teste BLL", "Alarme do monitor BLL funcionando.") else "Falhou - veja a mensagem acima.")
    elif a.diagnostico:
        rodar(uma_vez=True, visivel=True, diag=True)
    elif a.teste:
        rodar(uma_vez=True, visivel=True)
    else:
        rodar()
