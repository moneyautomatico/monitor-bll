"""Investiga a consulta pública de um pregão no Compras.gov.br (somente leitura).
Uso: xvfb-run -a venv/bin/python descobrir_compras.py <numero_da_compra>
  numero_da_compra = UASG(6) + modalidade(2, pregão=05) + número(5) + ano(4)
  ex.: UASG 158381, Pregão 90005/2024 -> 15838105900052024
"""
import json
import sys

from playwright.sync_api import sync_playwright

compra = sys.argv[1] if len(sys.argv) > 1 else ""
if not compra.isdigit():
    print(__doc__)
    sys.exit(1)

URL = ("https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/"
       f"acompanhamento-compra?compra={compra}")
vistos = []

with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    pg = nav.new_page()

    def resposta(r):
        tipo = r.headers.get("content-type", "")
        if "json" in tipo:
            try:
                corpo = r.text()
            except Exception:
                corpo = ""
            vistos.append((r.status, r.url, corpo[:400].replace("\n", " ")))

    pg.on("response", resposta)
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(5000)
    # tenta abrir abas/botões de mensagens só para ler (não envia nada)
    for rotulo in ["Mensagens", "Chat", "mensagens"]:
        try:
            pg.get_by_text(rotulo, exact=False).first.click(timeout=3000)
            pg.wait_for_timeout(4000)
            break
        except Exception:
            pass

    print("=== TÍTULO:", pg.title())
    print("=== TEXTO DA PÁGINA (início) ===")
    print(pg.inner_text("body")[:1500])
    print("\n=== RESPOSTAS JSON ===")
    for st, url, corpo in vistos:
        print(f"[{st}] {url}\n     {corpo}\n")
    nav.close()
