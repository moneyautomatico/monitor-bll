"""Histórico dos avisos mandados (Telegram) – o sininho 🔔 do painel mostra para quem perdeu a mensagem.
Guarda só o texto do aviso, para quem foi (usuário/empresas) e quando. Nada de tokens ou senhas."""
import json
import re
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "avisos.jsonl"
TRAVA = threading.Lock()
MAXIMO = 3000


def registrar(texto, empresas=(), usuario=""):
    reg = {"quando": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "empresas": [x for x in empresas if x][:10],
           "usuario": usuario or "", "texto": str(texto)[:3000]}
    with TRAVA:
        with ARQ.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        ARQ.chmod(0o600)
        try:
            linhas = ARQ.read_text(encoding="utf-8").splitlines()
            if len(linhas) > MAXIMO * 1.2:
                ARQ.write_text("\n".join(linhas[-MAXIMO:]) + "\n", encoding="utf-8")
        except OSError:
            pass


def ultimos(n=600):
    try:
        linhas = ARQ.read_text(encoding="utf-8").splitlines()[-n:]
    except FileNotFoundError:
        return []
    out = []
    for ln in linhas:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out[::-1]


def texto_simples(html_txt):
    t = re.sub(r"<br\s*/?>", "\n", html_txt or "")
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&")
