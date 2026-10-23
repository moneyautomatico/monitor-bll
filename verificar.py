"""Verifica se o acompanhamento de cada empresa está funcionando. Não mostra senhas, tokens nem conteúdo de mensagens.
Uso: cd ~/monitor-bll && venv/bin/python verificar.py
"""
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EMP = PASTA / "empresas"


def env(arq):
    d = {}
    for linha in arq.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            k, v = linha.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def sh(*c):
    try:
        return subprocess.run(c, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as ex:  # noqa: BLE001
        return f"erro: {ex}"


def data(txt):
    for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime((txt or "")[:19 if f.endswith("%S") else 16], f)
        except ValueError:
            pass
    return None


geral = env(PASTA / "config.env") if (PASTA / "config.env").exists() else {}
intervalo = geral.get("INTERVALO_MINUTOS", "") or "10"
print(f"Intervalo configurado: {intervalo} min · horário {geral.get('HORARIO_INICIO', '0') or '0'}h–{geral.get('HORARIO_FIM', '24') or '24'}h")
print(f"Painel: {sh('systemctl', 'is-active', 'painel-bll')}\n")
agora = datetime.now()
problemas = 0
for arq in sorted(EMP.glob("*.env")):
    eid, c = arq.stem, env(arq)
    nome = c.get("EMPRESA_NOME") or eid
    plat = (c.get("PLATAFORMA") or "bll").upper()
    hab = sh("systemctl", "is-enabled", f"monitor-bll@{eid}")
    ativo = sh("systemctl", "is-active", f"monitor-bll@{eid}")
    linha = [f"■ {nome} ({plat}) [{eid}] · dono: {c.get('EMPRESA_DONO') or '—'}"]
    if hab != "enabled":
        linha.append("   ⏸  pausada (de propósito)")
        print("\n".join(linha) + "\n")
        continue
    st, ult, dur, n24, nproc, nlote = "", "", "", 0, 0, 0
    db = EMP / eid / "bll_monitor.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            est = dict(con.execute("SELECT chave, valor FROM estado").fetchall())
            st, ult, dur = est.get("status", ""), est.get("ultima_verificacao", ""), est.get("duracao_ciclo", "")
            nproc = con.execute("SELECT COUNT(*) FROM processos").fetchone()[0]
            ontem = agora - timedelta(hours=24)
            n24 = sum(1 for (v,) in con.execute("SELECT visto_em FROM mensagens ORDER BY rowid DESC LIMIT 3000")
                      if (data(v) or datetime.min) >= ontem)
            try:
                nlote = con.execute("SELECT COUNT(*) FROM classificacao").fetchone()[0]
            except sqlite3.OperationalError:
                pass
            con.close()
        except Exception as ex:  # noqa: BLE001
            st = f"erro ao ler o banco: {ex}"
    du = data(ult)
    minutos = int((agora - du).total_seconds() / 60) if du else None
    # só conta o que aconteceu desde que o monitor ligou desta vez (erros antigos, já resolvidos, não contam)
    desde = sh("systemctl", "show", "-p", "ActiveEnterTimestamp", "--value", f"monitor-bll@{eid}") or "-60min"
    erros = sh("bash", "-c", f"journalctl -u monitor-bll@{eid} --since '{desde}' --no-pager | grep -cE 'Traceback|Error|ERRO|Erro'")
    reinicios = sh("bash", "-c", f"journalctl -u monitor-bll@{eid} --since '-60min' --no-pager | grep -c 'Scheduled restart'")
    rodando_desde = sh("bash", "-c", f"date -d '{desde}' '+%d/%m %H:%M'") if desde != "-60min" else "?"
    tg = "sim" if (c.get("EMPRESA_TELEGRAM_CHAT_ID") and (c.get("TELEGRAM_DESTINO") or c.get("ENVIAR_PARA")) in ("proprio", "ambos")) \
        or ((c.get("TELEGRAM_DESTINO") or c.get("ENVIAR_PARA") or "geral") in ("geral", "ambos") and geral.get("TELEGRAM_CHAT_ID")) else "NÃO"
    nt = "sim" if c.get("EMPRESA_NTFY_TOPICO") or geral.get("NTFY_TOPICO") else "NÃO"
    ok = ativo == "active" and minutos is not None and minutos <= max(30, int(float(intervalo)) * 3) and "Erro" not in st \
        and (erros or "0") == "0"
    problemas += 0 if ok else 1
    linha.append(f"   {'✅' if ok else '❌'} serviço: {ativo} · última verificação: {ult or 'nunca'}"
                 f"{f' (há {minutos} min)' if minutos is not None else ''} · ciclo: {dur or '?'} s")
    linha.append(f"   situação: {st[:120] or '—'}")
    linha.append(f"   processos: {nproc} · lotes com classificação: {nlote} · mensagens novas em 24 h: {n24}")
    linha.append(f"   rodando desde {rodando_desde}: {erros or 0} erro(s) no log · reinícios na última hora: {reinicios or 0} · "
                 f"alertas: Telegram {tg} · ntfy {nt}")
    print("\n".join(linha) + "\n")
print("RESULTADO:", "tudo funcionando ✅" if not problemas else f"{problemas} empresa(s) com problema ❌ – mande este resultado no chat")
