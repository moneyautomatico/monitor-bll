"""Pacote de habilitação: junta capa/sumário, proposta, declarações, certidões e outros PDFs num PDF só, com
marcadores (índice lateral do leitor de PDF) e "Página X de Y" contínuo em todas as páginas.
O PDF é gerado no servidor com o próprio Chromium do Playwright (o mesmo dos monitores) e unido com pypdf."""
import base64
import html as h
import io
import threading

TRAVA = threading.Lock()   # um PDF por vez (o Chromium pesa)


def _pdf_do_html(conteudo_html, rodape="", fundo=True):
    from playwright.sync_api import sync_playwright
    rodape_html = ('<div style="width:100%;font-family:Arial;font-size:8px;color:#555;text-align:center;'
                   f'padding:0 20mm 0 30mm;box-sizing:border-box">{h.escape(rodape)}</div>') if rodape else "<span></span>"
    with TRAVA, sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        try:
            pg = nav.new_page()
            pg.set_content(conteudo_html, wait_until="load")
            return pg.pdf(format="A4", print_background=fundo, prefer_css_page_size=True, display_header_footer=bool(rodape),
                          header_template="<span></span>", footer_template=rodape_html)
        finally:
            nav.close()


def pdf_do_html(conteudo_html, rodape=""):
    """HTML das folhas (documentos.html + CSS) -> bytes de PDF A4."""
    return _pdf_do_html(conteudo_html, rodape)


def pdf_da_imagem(dados, ext):
    uri = f"data:image/{'png' if ext == 'png' else 'jpeg'};base64," + base64.b64encode(dados).decode()
    return _pdf_do_html('<html><head><style>@page{size:A4;margin:12mm}body{margin:0}img{max-width:100%;max-height:270mm;'
                        f'display:block;margin:0 auto}}</style></head><body><img src="{uri}"></body></html>')


def _numeracao(n):
    paginas = "".join(f'<div class="p"><div class="n">Página {i} de {n}</div></div>' for i in range(1, n + 1))
    return _pdf_do_html("<html><head><style>@page{size:A4;margin:0}html,body{margin:0;background:transparent}"
                        ".p{width:210mm;height:297mm;position:relative;page-break-after:always;overflow:hidden}"
                        ".p:last-child{page-break-after:auto}.n{position:absolute;right:12mm;bottom:7mm;font:8pt Arial;color:#444}"
                        f"</style></head><body>{paginas}</body></html>", fundo=False)


def montar(secoes, numerar=True):
    """secoes = [(titulo_do_marcador, bytes_pdf)] na ordem. Devolve (bytes, [avisos])."""
    from pypdf import PdfReader, PdfWriter, Transformation
    avisos, partes = [], []
    for titulo, dados in secoes:
        try:
            r = PdfReader(io.BytesIO(dados))
            if r.is_encrypted:
                r.decrypt("")
            partes.append((titulo, r))
        except Exception as ex:  # noqa: BLE001
            avisos.append(f"“{titulo}” não pôde ser incluído (PDF protegido ou com defeito: {str(ex)[:80]})")
    total = sum(len(r.pages) for _, r in partes)
    sobre = PdfReader(io.BytesIO(_numeracao(total))) if numerar and total else None
    w = PdfWriter()
    marcadores, idx = [], 0
    for titulo, r in partes:
        marcadores.append((titulo, idx))
        for pg in r.pages:
            if sobre is not None:
                o = sobre.pages[idx]
                try:
                    largura, altura = float(pg.mediabox.width), float(pg.mediabox.height)
                    if int(pg.get("/Rotate", 0) or 0) % 180:
                        largura, altura = altura, largura
                    pg.merge_transformed_page(o, Transformation().scale(largura / float(o.mediabox.width),
                                                                        altura / float(o.mediabox.height)))
                except Exception:  # noqa: BLE001
                    pass
            w.add_page(pg)
            idx += 1
    for titulo, pagina in marcadores:
        w.add_outline_item(titulo, pagina)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue(), avisos
