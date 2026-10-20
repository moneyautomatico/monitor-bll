"""Backup diário do Licitatudo.

- Cópia local em backups/ (guarda os últimos 7 dias).
- Cópia fora do servidor: o arquivo vai CRIPTOGRAFADO (AES-256, senha definida no painel) para o Telegram geral.
Inclui: cadastros das empresas e configurações, bancos de dados (mensagens, processos, classificação),
buscas salvas, análises da IA e instruções. Não inclui o perfil do navegador (é recriado no próximo login).

Restaurar (no servidor):
  openssl enc -d -aes-256-cbc -pbkdf2 -in licitatudo-AAAA-MM-DD.tar.gz.enc -out restaurar.tar.gz
  tar xzf restaurar.tar.gz -C ~/monitor-bll && systemctl restart painel-bll 'monitor-bll@*'
"""
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
DIR = PASTA / "backups"
GUARDAR = 7
ARQUIVOS_SOLTOS = ["config.env", "buscas_salvas.json", "buscas_vistos.json", "analise_instrucoes.txt",
                   "analise_empresa.txt", "urgente_palavras.txt", "vigia_estado.json"]


def _copia_sqlite(origem, destino):
    """Cópia consistente mesmo com o monitor gravando."""
    src = sqlite3.connect(f"file:{origem}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destino)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def gerar():
    """Cria backups/licitatudo-AAAA-MM-DD_HHMM.tar.gz e devolve o caminho."""
    DIR.mkdir(exist_ok=True)
    os.chmod(DIR, 0o700)
    nome = f"licitatudo-{datetime.now():%Y-%m-%d_%H%M}.tar.gz"
    destino = DIR / nome
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(destino, "w:gz") as tar:
        for n in ARQUIVOS_SOLTOS:
            if (PASTA / n).exists():
                tar.add(PASTA / n, arcname=n)
        emp = PASTA / "empresas"
        if emp.exists():
            for env in emp.glob("*.env"):
                tar.add(env, arcname=f"empresas/{env.name}")
            for db in emp.glob("*/bll_monitor.db"):
                copia = Path(tmp) / f"{db.parent.name}.db"
                try:
                    _copia_sqlite(db, copia)
                    tar.add(copia, arcname=f"empresas/{db.parent.name}/bll_monitor.db")
                except Exception as ex:  # noqa: BLE001
                    print("backup: não copiei", db, ex, flush=True)
            for ck in emp.glob("*/cookies_sessao.json"):
                tar.add(ck, arcname=f"empresas/{ck.parent.name}/{ck.name}")
        an = PASTA / "analises"
        if an.exists():
            for j in an.glob("*.json"):
                tar.add(j, arcname=f"analises/{j.name}")
    os.chmod(destino, 0o600)
    limpar()
    return destino


def limpar():
    antigos = sorted(DIR.glob("licitatudo-*.tar.gz"))
    for a in antigos[:-GUARDAR]:
        a.unlink(missing_ok=True)
    for a in DIR.glob("*.enc"):
        a.unlink(missing_ok=True)


def listar():
    if not DIR.exists():
        return []
    return [(a.name, a.stat().st_size, datetime.fromtimestamp(a.stat().st_mtime))
            for a in sorted(DIR.glob("licitatudo-*.tar.gz"), reverse=True)]


def criptografar(arq, senha):
    saida = arq.with_name(arq.name + ".enc")
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-in", str(arq), "-out", str(saida),
                        "-pass", "env:LT_SENHA"], env={**os.environ, "LT_SENHA": senha},
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "openssl falhou")
    return saida


def enviar_telegram(token, chat, arq, legenda):
    limite = 49 * 1024 * 1024
    dados = arq.read_bytes()
    if len(dados) > limite:
        raise RuntimeError(f"arquivo com {len(dados) // 1024 // 1024} MB passa do limite de 50 MB do Telegram")
    fronteira = uuid.uuid4().hex
    corpo = io.BytesIO()
    for k, v in (("chat_id", chat), ("caption", legenda)):
        corpo.write(f"--{fronteira}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    corpo.write(f"--{fronteira}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{arq.name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n".encode())
    corpo.write(dados)
    corpo.write(f"\r\n--{fronteira}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", corpo.getvalue(),
                                 {"Content-Type": f"multipart/form-data; boundary={fronteira}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.load(r)
    if not resp.get("ok"):
        raise RuntimeError(str(resp)[:200])


def executar(cfg, avisar):
    """Gera o backup e manda a cópia criptografada para o Telegram. Devolve texto do resultado."""
    arq = gerar()
    tam = arq.stat().st_size / 1024 / 1024
    senha = cfg.get("BACKUP_SENHA", "")
    tok, chat = cfg.get("TELEGRAM_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")
    if not senha:
        msg = (f"Backup local criado ({arq.name}, {tam:.1f} MB). Cópia fora do servidor NÃO enviada: "
               "defina a senha do backup em Configurações.")
        avisar("💾 " + msg)
        return msg
    if not (tok and chat):
        return f"Backup local criado ({arq.name}). Telegram geral não configurado para a cópia externa."
    enc = criptografar(arq, senha)
    try:
        enviar_telegram(tok, chat, enc, f"💾 Backup Licitatudo {datetime.now():%d/%m/%Y %H:%M} "
                                        f"({tam:.1f} MB, criptografado com a senha do backup)")
    finally:
        enc.unlink(missing_ok=True)
    return f"Backup criado ({arq.name}, {tam:.1f} MB) e cópia criptografada enviada ao Telegram."


def ciclo(cfg_func, avisar, hora=3):
    """Roda todo dia na hora indicada (padrão 03:00)."""
    feito = None
    time.sleep(120)
    while True:
        agora = datetime.now()
        try:
            h = int(cfg_func().get("BACKUP_HORA", str(hora)) or hora)
        except ValueError:
            h = hora
        if agora.hour == h and feito != agora.date():
            try:
                print(executar(cfg_func(), avisar), flush=True)
            except Exception as ex:  # noqa: BLE001
                avisar(f"⚠️ <b>Backup falhou</b>: {ex}")
            feito = agora.date()
        time.sleep(300)
