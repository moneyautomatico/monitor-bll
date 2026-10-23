"""Conserta o config.env depois do formulário de Configurações ter sido enviado incompleto (23/10/2026).
Recupera do backup local mais recente que ainda estava bom: ritmo, horários, alarme, palavras urgentes, ntfy geral.
NÃO mexe no Telegram (o bot passou a ser do usuário) nem em senhas/chaves.

Uso: cd ~/monitor-bll && venv/bin/python reparar_config.py && systemctl restart painel-bll 'monitor-bll@*'
"""
import io
import tarfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent
CONFIG = PASTA / "config.env"
URG = PASTA / "urgente_palavras.txt"
CHAVES = ["INTERVALO_MINUTOS", "MAX_NAVEGADORES", "HORARIO_INICIO", "HORARIO_FIM", "BUSCA_INTERVALO_MINUTOS", "VIGIA_MINUTOS",
          "SITUACOES_IGNORADAS", "CHAMADA_USUARIO", "NTFY_TOPICO", "GEMINI_MODELO", "ALARME_SO_URGENTE", "CHAT_URGENTE"]


def ler(txt):
    d = {}
    for linha in txt.splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            k, v = linha.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def gravar(valores):
    linhas = CONFIG.read_text(encoding="utf-8").splitlines()
    feitas, out = set(), []
    for linha in linhas:
        k = linha.split("=", 1)[0].strip() if "=" in linha and not linha.strip().startswith("#") else None
        if k in valores:
            out.append(f"{k}={valores[k]}")
            feitas.add(k)
        else:
            out.append(linha)
    out += [f"{k}={v}" for k, v in valores.items() if k not in feitas]
    CONFIG.write_text("\n".join(out) + "\n", encoding="utf-8")
    CONFIG.chmod(0o600)


atual = ler(CONFIG.read_text(encoding="utf-8"))
backups = sorted((PASTA / "backups").glob("licitatudo-*.tar.gz"), reverse=True)
escolhido = None
for b in backups:
    try:
        with tarfile.open(b) as t:
            cfg = ler(t.extractfile("config.env").read().decode("utf-8"))
            try:
                urg = t.extractfile("urgente_palavras.txt").read().decode("utf-8")
            except KeyError:
                urg = ""
    except Exception as ex:  # noqa: BLE001
        print("ignorando", b.name, ex)
        continue
    if cfg.get("INTERVALO_MINUTOS", "x").strip():   # ainda não tinha o problema
        escolhido = (b, cfg, urg)
        break

if not escolhido:
    print("Nenhum backup bom encontrado. O monitor já usa os valores padrão quando o campo está em branco;")
    print("confira e salve de novo em Administração → Configurações.")
    raise SystemExit(0)

b, cfg, urg = escolhido
print("Usando o backup:", b.name)
novos = {}
for k in CHAVES:
    if k in ("ALARME_SO_URGENTE", "CHAT_URGENTE"):
        if cfg.get(k, "1") != atual.get(k, "1"):
            novos[k] = cfg.get(k, "1")
    elif not atual.get(k, "").strip() and cfg.get(k, "").strip():
        novos[k] = cfg[k]
if novos:
    gravar(novos)
    for k, v in novos.items():
        print(f"  {k} restaurado")
else:
    print("  config.env já estava certo.")
if (not URG.exists() or not URG.read_text(encoding="utf-8").strip()) and urg.strip():
    URG.write_text(urg, encoding="utf-8")
    print("  palavras urgentes restauradas")
print("Pronto. Agora: systemctl restart painel-bll 'monitor-bll@*'")
