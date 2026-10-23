"""Coordenadas das cidades brasileiras (base pública do IBGE compilada em kelvins/municipios-brasileiros)
e cidade-sede de cada empresa (pelo CNPJ, na BrasilAPI). Tudo fica guardado no servidor depois da 1ª vez."""
import csv
import io
import json
import math
import re
import threading
import urllib.request
from pathlib import Path

import buscas

PASTA = Path(__file__).resolve().parent
ARQ_CIDADES = PASTA / "municipios.csv"
ARQ_SEDES = PASTA / "sedes_empresas.json"
FONTES = ["https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv",
          "https://cdn.jsdelivr.net/gh/kelvins/municipios-brasileiros@main/csv/municipios.csv"]
UF_COD = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI",
          "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
          "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
_CIDADES = {}
_TRAVA = threading.Lock()
_BAIXANDO = threading.Event()


def chave(cidade, uf):
    return re.sub(r"[^a-z0-9]", "", buscas.norm(cidade)) + "|" + (uf or "").upper().strip()


def _ler_csv(texto):
    out = {}
    for r in csv.DictReader(io.StringIO(texto)):
        try:
            uf = UF_COD.get(str(r.get("codigo_uf", "")).strip()) or (r.get("uf") or "").upper()
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, ValueError, TypeError):
            continue
        if uf and r.get("nome"):
            out[chave(r["nome"], uf)] = (round(lat, 5), round(lon, 5), r["nome"].strip())
    return out


def _baixar():
    for url in FONTES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Licitatudo/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8")
            if len(_ler_csv(txt)) > 5000:
                ARQ_CIDADES.write_text(txt, encoding="utf-8")
                return True
        except Exception as ex:  # noqa: BLE001
            print("geo: não baixei", url, ex, flush=True)
    return False


def cidades():
    """{chave: (lat, lon, nome)} – vazio enquanto a lista ainda não foi baixada."""
    global _CIDADES
    if _CIDADES:
        return _CIDADES
    with _TRAVA:
        if not _CIDADES and ARQ_CIDADES.exists():
            _CIDADES = _ler_csv(ARQ_CIDADES.read_text(encoding="utf-8"))
    if not _CIDADES and not _BAIXANDO.is_set():
        _BAIXANDO.set()

        def tarefa():
            try:
                if _baixar():
                    cidades()
            finally:
                _BAIXANDO.clear()
        threading.Thread(target=tarefa, daemon=True).start()
    return _CIDADES


def coord(cidade, uf):
    c = cidades().get(chave(cidade, uf))
    return (c[0], c[1]) if c else None


def km(a, b):
    """Distância em linha reta (km) entre (lat, lon) a e b."""
    if not a or not b:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ sede das empresas
def _sedes():
    try:
        return json.loads(ARQ_SEDES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def sede_por_cnpj(cnpj):
    """'Cidade/UF' da empresa pelo CNPJ (BrasilAPI, dados públicos da Receita). Guarda a resposta."""
    cnpj = re.sub(r"\D", "", cnpj or "")
    if len(cnpj) != 14:
        return ""
    d = _sedes()
    if cnpj in d:
        return d[cnpj]
    sede = ""
    try:
        req = urllib.request.Request(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", headers={"User-Agent": "Licitatudo/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
        if j.get("municipio") and j.get("uf"):
            sede = f"{j['municipio'].title()}/{j['uf'].upper()}"
    except Exception as ex:  # noqa: BLE001
        print("geo: CNPJ não consultado", ex, flush=True)
        return ""
    d[cnpj] = sede
    ARQ_SEDES.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return sede


def separar(cidade_uf):
    """'Ruy Barbosa/BA' ou 'Ruy Barbosa - BA' -> ('Ruy Barbosa', 'BA')."""
    m = re.match(r"^\s*(.+?)\s*[/\-,]\s*([A-Za-z]{2})\s*$", cidade_uf or "")
    return (m.group(1), m.group(2).upper()) if m else ("", "")
