"""Investiga a página pública de um processo do Portal de Compras Públicas (somente leitura).
Uso: xvfb-run -a venv/bin/python descobrir_pcp.py <url_da_pagina_publica> ["Ranking nos Itens"]
Clica no documento indicado e mostra as respostas JSON, o endereço aberto e o texto do arquivo.
"""
import io
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

if len(sys.argv) < 2 or not sys.argv[1].startswith("http"):
    print(__doc__)
    sys.exit(1)
args = sys.argv[1:]
EMPRESA = ""
if "--empresa" in args:
    i = args.index("--empresa")
    EMPRESA = args[i + 1]
    del args[i:i + 2]
URL = args[0]
ALVO = args[1] if len(args) > 1 else "Ranking nos Itens"
jsons = []


def texto_pdf(dados):
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages)
    except Exception as ex:  # noqa: BLE001
        return f"(não consegui ler o PDF: {ex})"


with sync_playwright() as p:
    nav = p.chromium.launch(headless=False)  # janela na tela virtual (xvfb), como o monitor
    ctx = nav.new_context(accept_downloads=True, locale="pt-BR", viewport={"width": 1366, "height": 800})
    if EMPRESA:
        arq = Path(__file__).resolve().parent / "empresas" / EMPRESA / "cookies_sessao.json"
        cks = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else []
        ctx.add_cookies(cks)
        print(f"(sessão da empresa {EMPRESA} carregada: {len(cks)} cookies)")
    pg = ctx.new_page()
    # teste: a página abre só com o número interno no fim do endereço?
    m = re.search(r"-(\d{5,})/?$", URL)
    if m:
        curto = f"https://www.portaldecompraspublicas.com.br/processos/x/x/x-{m.group(1)}"
        try:
            pg.goto(curto, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(6000)
            corpo = pg.inner_text("body")
            achou = re.search(r"\d{1,4}/\d{4}", corpo)
            print(f"=== TESTE ENDEREÇO CURTO: {pg.url}\n    título: {pg.title()} | nº visto: {achou.group(0) if achou else '-'}"
                  f" | tem 'Ranking nos Itens': {'Ranking nos Itens' in corpo}")
        except Exception as ex:  # noqa: BLE001
            print("=== TESTE ENDEREÇO CURTO falhou:", ex)

    def resp(r):
        tipo = r.headers.get("content-type", "")
        if "json" in tipo or "pdf" in tipo:
            jsons.append((r.status, r.url, tipo))

    ctx.on("response", resp)
    erros = []
    pg.on("pageerror", lambda ex: erros.append(str(ex)[:150]))
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    try:
        pg.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    print("=== RESPOSTAS JSON/PDF AO CARREGAR ===")
    for st, u, t in jsons:
        print(f"[{st}] {t[:30]} {u}")
    jsons.clear()

    for rot in ("Rejeitar", "Aceitar"):
        try:
            pg.get_by_text(rot, exact=True).first.click(timeout=4000)
            print(f"(aviso de cookies fechado: {rot})")
            break
        except Exception:  # noqa: BLE001
            pass
    pg.wait_for_timeout(8000)  # espera o site ficar interativo
    pg.mouse.move(400, 400)
    pg.mouse.wheel(0, 600)
    pg.wait_for_timeout(2000)
    print("Angular ativo:", pg.evaluate("() => !!(window.ng || document.querySelector('[ng-version]'))"),
          "| erros de script:", erros[:3])
    for _ in range(3):  # fecha janelas como "Aviso de licitações"
        pg.keyboard.press("Escape")
        pg.evaluate("""() => document.querySelectorAll('.modal .close, .modal [aria-label*=lose], .cdk-overlay-backdrop, [class*=close-button]')
                          .forEach(b => { try { b.click(); } catch (e) {} })""")
        pg.wait_for_timeout(700)
    pedidos = []
    ctx.on("request", lambda r: pedidos.append(f"{r.method} {r.resource_type} {r.url}")
           if r.resource_type not in ("image", "font", "stylesheet", "media") else None)
    ctx.on("response", lambda r: print("   resposta:", r.status, r.url[:200], (r.text()[:300] if "json" in r.headers.get("content-type", "") else ""))
           if "portaldecompraspublicas" in r.url and r.request.resource_type in ("xhr", "fetch") else None)
    card = pg.locator(".step-card", has_text=ALVO).first
    el = card.locator("button").first if card.count() else pg.get_by_text(ALVO, exact=False).first
    print(f"\n=== ELEMENTO '{ALVO}' ===")
    try:
        print(el.evaluate("e => (e.closest('.step-card') || e).outerHTML.slice(0, 600)"))
    except Exception as ex:  # noqa: BLE001
        print("não encontrado:", ex)
        nav.close()
        sys.exit(0)

    conteudo, onde = None, ""
    try:
        with pg.expect_download(timeout=30000) as baixa:
            el.scroll_into_view_if_needed()
            el.click(timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    pg.wait_for_timeout(3000)
    for outra in ctx.pages[1:]:
        try:
            outra.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        onde = outra.url
        print("\n=== NOVA ABA:", onde)
        try:
            print(outra.inner_text("body")[:6000])
        except Exception as ex:  # noqa: BLE001
            print("(sem texto)", ex)
    try:
        d = baixa.value  # noqa: F821
        caminho = d.path()
        dados = open(caminho, "rb").read()
        print("\n=== DOWNLOAD:", d.suggested_filename, len(dados), "bytes")
        print(texto_pdf(dados)[:8000] if dados[:4] == b"%PDF" else dados[:3000].decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        pass
    print("\n=== PEDIDOS FEITOS APÓS O CLIQUE ===")
    for x in pedidos[:40]:
        print(x[:300])
    print("\n=== RESPOSTAS JSON/PDF APÓS O CLIQUE ===")
    for st, u, t in jsons:
        print(f"[{st}] {t[:30]} {u}")
    try:
        modal = pg.locator(".modal:visible, [role=dialog]:visible").first
        print("\n=== JANELA NA PÁGINA ===")
        print(modal.inner_text(timeout=3000)[:6000])
    except Exception:  # noqa: BLE001
        pass
    nav.close()
