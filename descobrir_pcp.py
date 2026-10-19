"""Investiga a página pública de um processo do Portal de Compras Públicas (somente leitura).
Uso: xvfb-run -a venv/bin/python descobrir_pcp.py <url_da_pagina_publica> ["Ranking nos Itens"]
Clica no documento indicado e mostra as respostas JSON, o endereço aberto e o texto do arquivo.
"""
import io
import sys

from playwright.sync_api import sync_playwright

if len(sys.argv) < 2 or not sys.argv[1].startswith("http"):
    print(__doc__)
    sys.exit(1)
URL = sys.argv[1]
ALVO = sys.argv[2] if len(sys.argv) > 2 else "Ranking nos Itens"
jsons = []


def texto_pdf(dados):
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages)
    except Exception as ex:  # noqa: BLE001
        return f"(não consegui ler o PDF: {ex})"


with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    ctx = nav.new_context(accept_downloads=True)
    pg = ctx.new_page()

    def resp(r):
        tipo = r.headers.get("content-type", "")
        if "json" in tipo or "pdf" in tipo:
            jsons.append((r.status, r.url, tipo))

    ctx.on("response", resp)
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    try:
        pg.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    print("=== RESPOSTAS JSON/PDF AO CARREGAR ===")
    for st, u, t in jsons:
        print(f"[{st}] {t[:30]} {u}")
    jsons.clear()

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
        with ctx.expect_page(timeout=8000) as nova:
            with pg.expect_download(timeout=8000) as baixa:
                el.click()
    except Exception:  # noqa: BLE001
        pass
    pg.wait_for_timeout(5000)
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
