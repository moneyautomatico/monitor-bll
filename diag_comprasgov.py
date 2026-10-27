"""Diagnóstico do Compras.gov.br: mostra a situação da conexão e a ESTRUTURA das telas/respostas que o site usou
enquanto você navegava pela tela "Conectar" (só endereços e nomes de campos – sem valores, sem senhas, sem cookies).
Uso: venv/bin/python diag_comprasgov.py <id_da_empresa>     (sem id: lista as empresas do Compras.gov.br)
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMP = PASTA / "empresas"


def env(arq):
    d = {}
    for ln in arq.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip().strip('"')
    return d


def chaves(c, n=0):
    if isinstance(c, dict):
        return "{" + ", ".join(k + (":" + chaves(v, n + 1) if isinstance(v, (dict, list)) and n < 2 else "") for k, v in c.items()) + "}"
    if isinstance(c, list) and c:
        return "[" + chaves(c[0], n + 1) + "]"
    return ""


cg = [a.stem for a in sorted(EMP.glob("*.env")) if env(a).get("PLATAFORMA", "").lower() == "comprasgov"]
if len(sys.argv) < 2:
    print("Empresas do Compras.gov.br:", ", ".join(cg) or "nenhuma")
    sys.exit(0)
eid = sys.argv[1]
d = EMP / eid
st = json.loads((d / "comprasgov.json").read_text(encoding="utf-8")) if (d / "comprasgov.json").exists() else {}
print("== CONEXÃO ==")
for k in ("conectado_em", "conectado_por", "inicio_titulo", "ultima_ok", "no_ar_ha", "aviso_caiu", "duracao_sessao"):
    if st.get(k):
        print(f"  {k}: {st[k]}")
print("\n== TELAS SALVAS (🧭) ==")
for ln in (d / "comprasgov_telas.jsonl").read_text(encoding="utf-8").splitlines() if (d / "comprasgov_telas.jsonl").exists() else []:
    t = json.loads(ln)
    print(f"\n[{t.get('em')}] {t.get('url')}  «{t.get('titulo', '')[:60]}»")
    for k in ("titulos", "botoes", "abas_menu"):
        if t.get(k):
            print(f"  {k}: " + " | ".join(t[k][:30]))
    for tb in t.get("tabelas", []):
        print(f"  tabela: {' | '.join(tb.get('cab', []))}  ({tb.get('linhas')} linhas)")
    if t.get("classes_lista"):
        print("  classes: " + " ".join(t["classes_lista"][:25]))
print("\n== RESPOSTAS DO SITE (endereço → campos) ==")
vistos = OrderedDict()
for ln in (d / "comprasgov_estrutura.jsonl").read_text(encoding="utf-8").splitlines() if (d / "comprasgov_estrutura.jsonl").exists() else []:
    r = json.loads(ln)
    k = f"{r['metodo']} {r['url']}"
    vistos[k] = (r["status"], chaves(r.get("campos")), vistos.get(k, (0, "", 0))[2] + 1)
for k, (stt, cps, n) in vistos.items():
    print(f"- {k}  [{stt}] ×{n}\n    {cps[:400]}")
