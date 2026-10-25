"""Trava do teste grátis: cada empresa (CNPJ) e cada login de plataforma só pode fazer o teste uma vez,
mesmo que a pessoa crie outra conta. Guarda apenas um resumo (hash) do CNPJ/login – não o dado em si."""
import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "testes_empresas.json"
TRAVA = threading.Lock()


def _ler():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def _h(valor):
    return hashlib.sha256(("licitatudo-teste|" + valor).encode()).hexdigest()[:32]


def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def chaves(cnpj="", plataforma="", login_plataforma=""):
    """[(tipo, hash)] que identificam a empresa."""
    out = []
    c = so_digitos(cnpj)
    if len(c) == 14:
        out.append(("cnpj", _h("cnpj:" + c)))
    lp = (login_plataforma or "").strip().lower()
    if lp:
        out.append(("plataforma", _h(f"plat:{(plataforma or 'bll').lower()}|{lp}")))
    return out


def conflito(lista, login):
    """Registro de outra conta que já fez o teste com algum destes dados (ou None)."""
    d = _ler()
    for tipo, hh in lista:
        r = d.get(hh)
        if r and r.get("login") != login:
            return {**r, "tipo": tipo}
    return None


def registrar(lista, login, empresa=""):
    with TRAVA:
        d = _ler()
        for tipo, hh in lista:
            d.setdefault(hh, {"login": login, "tipo": tipo, "empresa": (empresa or "")[:80],
                              "quando": datetime.now().strftime("%d/%m/%Y %H:%M")})
        _gravar(d)


def liberar(cnpj="", plataforma="", login_plataforma="", login=""):
    """Admin: permite um novo teste. Por CNPJ, por login de plataforma ou tudo de uma conta. Devolve quantos saíram."""
    alvo = {hh for _, hh in chaves(cnpj, plataforma, login_plataforma)}
    with TRAVA:
        d = _ler()
        fora = [k for k, v in d.items() if k in alvo or (login and v.get("login") == login)]
        for k in fora:
            d.pop(k)
        _gravar(d)
    return len(fora)


def total():
    return len({v.get("login") for v in _ler().values()})
