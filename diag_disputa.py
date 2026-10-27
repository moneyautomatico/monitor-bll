"""Diagnóstico da Disputa ao vivo (copiloto) – mostra só processo, lote, fase e horários (sem senhas nem valores).
Uso: venv/bin/python diag_disputa.py
"""
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMP = PASTA / "empresas"


def env(arq):
    out = {}
    for linha in arq.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.startswith("#"):
            k, v = linha.split("=", 1)
            out[k.strip()] = v.strip()
    return out


for arq in sorted(EMP.glob("*.env")):
    c = env(arq)
    eid = arq.stem
    print(f"\n=== {eid} · plataforma={c.get('PLATAFORMA', 'bll')} · copiloto={c.get('COPILOTO', '1')} · intervalo={c.get('INTERVALO_MINUTOS', 'padrão')} min")
    db = EMP / eid / "bll_monitor.db"
    if not db.exists():
        print("  (sem banco)")
        continue
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT processo, lote, fase, lido_em FROM classificacao ORDER BY lido_em DESC LIMIT 12").fetchall()
        print("  últimas classificações lidas (processo | lote | fase | lida em):")
        for p, l, f, t in rows:
            quando = datetime.fromtimestamp(t).strftime("%d/%m %H:%M") if t else "-"
            print(f"   {p} | L{l} | {f} | {quando}")
    except sqlite3.Error as ex:
        print("  classificação:", ex)
    try:
        n = con.execute("SELECT COUNT(*), MAX(lido_em) FROM ao_vivo").fetchone()
        print(f"  ao_vivo: {n[0]} lote(s), última leitura {n[1]}")
    except sqlite3.Error:
        print("  ao_vivo: tabela ainda não criada (o copiloto nunca rodou nesta empresa)")
    try:
        pr = con.execute("SELECT COUNT(*), SUM(url != '') FROM processos").fetchone()
        print(f"  processos: {pr[0]} (com link: {pr[1]})")
    except sqlite3.Error:
        pass
    log = subprocess.run(["journalctl", "-u", f"monitor-bll@{eid}", "--since", "-6h", "--no-pager", "-o", "cat"],
                         capture_output=True, text=True).stdout
    linhas = [x for x in log.splitlines() if "opiloto" in x or "Classifica" in x or "Erro" in x][-8:]
    print("  log (últimas 6 h):" + ("".join("\n   " + x[:160] for x in linhas) if linhas else " nada sobre copiloto/classificação"))
print(f"\n(agora: {time.strftime('%d/%m %H:%M')})")
