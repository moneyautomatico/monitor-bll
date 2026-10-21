"""Resume as telas salvas pelo --diagnostico (botões, ícones e títulos) para mandar no chat sem dados sensíveis.
Uso: venv/bin/python diag_resumo.py <empresa> [trecho_do_nome]   ex.: diag_resumo.py vc-comercial-4 017
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class Botoes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pilha, self.out, self.titulos, self.em_tit = [], [], [], None

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag in ("button", "a"):
            self.pilha.append({"tag": tag, "cls": a.get("class", ""), "txt": "", "icones": [],
                               "extra": a.get("data-toggle") or a.get("title") or ""})
        elif tag in ("i", "span", "svg") and self.pilha and re.search(r"fa-|icon|glyph", a.get("class", "")):
            self.pilha[-1]["icones"].append(a.get("class", ""))
        if tag in ("h1", "h2", "h3", "h4", "h5", "legend") or "modal-title" in a.get("class", ""):
            self.em_tit = ""

    def handle_endtag(self, tag):
        if tag in ("button", "a") and self.pilha:
            b = self.pilha.pop()
            txt = " ".join(b["txt"].split())[:60]
            if txt or b["icones"]:
                self.out.append(f'{b["tag"]:6} | {txt:60} | ícone: {" ".join(b["icones"])[:50]} | {b["extra"][:30]}')
        if tag in ("h1", "h2", "h3", "h4", "h5", "legend") and self.em_tit is not None:
            t = " ".join(self.em_tit.split())
            if t:
                self.titulos.append(t[:100])
            self.em_tit = None

    def handle_data(self, d):
        if self.pilha:
            self.pilha[-1]["txt"] += d
        if self.em_tit is not None:
            self.em_tit += d


if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)
pasta = Path(__file__).resolve().parent / "empresas" / sys.argv[1] / "diagnostico"
filtro = sys.argv[2] if len(sys.argv) > 2 else ""
for arq in sorted(pasta.glob("*.html")):
    if filtro and filtro not in arq.name:
        continue
    p = Botoes()
    p.feed(arq.read_text(encoding="utf-8", errors="ignore"))
    print(f"\n===== {arq.name}\nTítulos: {' / '.join(dict.fromkeys(p.titulos))[:400]}")
    vistos = set()
    for linha in p.out:
        if linha not in vistos and not re.search(r"sair|logout|senha", linha, re.I):
            vistos.add(linha)
            print(linha)
