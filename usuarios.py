"""Usuários do painel: login próprio para cada pessoa, papéis (admin / operador / leitura), empresas que cada um
pode ver, permissão para usar IA (que tem custo) e registro de auditoria (quem fez o quê).
Senhas guardadas só como hash PBKDF2-SHA256 (nunca em texto)."""
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / "usuarios.json"
ARQ_AUDIT = PASTA / "auditoria.jsonl"
TRAVA = threading.Lock()
ITERACOES = 240_000
PAPEIS = {"admin": "Administrador", "operador": "Operador", "leitura": "Somente leitura"}
DESCR_PAPEL = {
    "admin": "tudo, inclusive empresas, configurações, usuários, consumo de IA e auditoria",
    "operador": "trabalha no dia a dia (mensagens, disputa, agenda, documentos, oportunidades); sem configurações e sem usuários",
    "leitura": "só consulta; não altera nada e não usa IA",
}


# ------------------------------------------------------------------ senhas
def hash_senha(senha):
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), ITERACOES).hex()
    return f"pbkdf2_sha256${ITERACOES}${sal}${h}"


def confere(senha, guardado):
    try:
        _, it, sal, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:  # noqa: BLE001
        return False


def senha_fraca(sen):
    """Recomendação (aviso): 12+ caracteres com letras e números."""
    return len(sen) < 12 or not (re.search(r"[A-Za-z]", sen) and re.search(r"\d", sen))


def senha_invalida(sen):
    """Mínimo obrigatório: 8 caracteres, com letras e números."""
    return len(sen) < 8 or not (re.search(r"[A-Za-z]", sen) and re.search(r"\d", sen))


MSG_SENHA = "A senha precisa ter pelo menos 8 caracteres, com letras e números (recomendado: 12 ou mais)."


# ------------------------------------------------------------------ armazenamento
def carregar():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _salvar(d):
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ARQ)


def obter(login):
    u = carregar().get((login or "").lower())
    return dict(u, login=login.lower()) if u else None


def login_valido(login):
    return bool(re.fullmatch(r"[a-z0-9._-]{3,32}", login or ""))


def criar(login, nome, senha, papel="operador", empresas=(), ia=True, por="", trocar=False):
    login = (login or "").strip().lower()
    if not login_valido(login):
        raise ValueError("Login: 3 a 32 caracteres, só letras minúsculas, números, ponto, hífen ou _.")
    if papel not in PAPEIS:
        raise ValueError("Papel inválido.")
    if senha_invalida(senha):
        raise ValueError(MSG_SENHA)
    with TRAVA:
        d = carregar()
        if login in d:
            raise ValueError("Já existe um usuário com esse login.")
        d[login] = {"nome": (nome or login).strip()[:80], "hash": hash_senha(senha), "papel": papel,
                    "empresas": sorted({x.upper().strip() for x in empresas if x.strip()}), "ia": bool(ia), "ativo": True,
                    "criado": datetime.now().strftime("%d/%m/%Y %H:%M"), "criado_por": por, "ultimo_acesso": "",
                    "trocar_senha": bool(trocar), "senha_fraca": senha_fraca(senha)}
        _salvar(d)


def atualizar(login, por="", **campos):
    with TRAVA:
        d = carregar()
        u = d.get(login)
        if not u:
            raise ValueError("Usuário não encontrado.")
        novo_papel = campos.get("papel", u["papel"])
        if u["papel"] == "admin" and (novo_papel != "admin" or campos.get("ativo") is False):
            admins = [k for k, v in d.items() if v["papel"] == "admin" and v.get("ativo", True)]
            if admins == [login]:
                raise ValueError("Este é o único administrador ativo – crie outro admin antes.")
        if "papel" in campos and campos["papel"] not in PAPEIS:
            raise ValueError("Papel inválido.")
        if "empresas" in campos:
            campos["empresas"] = sorted({x.upper().strip() for x in campos["empresas"] if x.strip()})
        u.update({k: v for k, v in campos.items() if k in ("nome", "papel", "empresas", "ia", "ativo", "trocar_senha")})
        _salvar(d)


def trocar_senha(login, nova, obrigar_troca=False):
    if senha_invalida(nova):
        raise ValueError(MSG_SENHA)
    with TRAVA:
        d = carregar()
        if login not in d:
            raise ValueError("Usuário não encontrado.")
        d[login]["hash"] = hash_senha(nova)
        d[login]["senha_fraca"] = senha_fraca(nova)
        d[login]["trocar_senha"] = bool(obrigar_troca)
        _salvar(d)


def excluir(login):
    with TRAVA:
        d = carregar()
        u = d.get(login)
        if not u:
            return
        if u["papel"] == "admin" and [k for k, v in d.items() if v["papel"] == "admin"] == [login]:
            raise ValueError("Não dá para excluir o único administrador.")
        del d[login]
        _salvar(d)


def autenticar(login, senha):
    """Devolve o usuário (dict) ou None. Faz o mesmo trabalho mesmo se o login não existir (sem 'vazar' quem existe)."""
    u = obter((login or "").strip().lower())
    if not u:
        confere(senha, f"pbkdf2_sha256${ITERACOES}$00$00")
        return None
    if not confere(senha, u.get("hash", "")) or not u.get("ativo", True):
        return None
    return u


def marcar_acesso(login, ip):
    with TRAVA:
        d = carregar()
        if login in d:
            d[login]["ultimo_acesso"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            d[login]["ultimo_ip"] = ip
            _salvar(d)


def migrar_de_senha_unica(usuario, senha):
    """1ª vez: cria o administrador com o usuário/senha que o painel já usava (config.env)."""
    if carregar() or not senha:
        return False
    login = (usuario or "admin").strip().lower()
    if not login_valido(login):
        login = "admin"
    with TRAVA:
        _salvar({login: {"nome": "Administrador", "hash": hash_senha(senha), "papel": "admin", "empresas": [], "ia": True,
                         "ativo": True, "criado": datetime.now().strftime("%d/%m/%Y %H:%M"), "criado_por": "migração",
                         "ultimo_acesso": "", "senha_fraca": senha_fraca(senha)}})
    return True


# ------------------------------------------------------------------ auditoria
def auditar(login, ip, acao, detalhe=""):
    reg = {"quando": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "usuario": login or "-", "ip": ip or "",
           "acao": acao[:80], "detalhe": str(detalhe)[:300]}
    with TRAVA:
        with ARQ_AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        ARQ_AUDIT.chmod(0o600)


def auditoria(n=300, filtro=""):
    try:
        linhas = ARQ_AUDIT.read_text(encoding="utf-8").splitlines()[-5000:]
    except FileNotFoundError:
        return []
    out = []
    for linha in reversed(linhas):
        try:
            r = json.loads(linha)
        except ValueError:
            continue
        if filtro and filtro.lower() not in json.dumps(r, ensure_ascii=False).lower():
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ configurações de cada usuário
def cfg(login):
    return dict((carregar().get(login) or {}).get("cfg") or {})


def salvar_cfg(login, campos):
    with TRAVA:
        d = carregar()
        if login in d:
            c = d[login].setdefault("cfg", {})
            for k, v in campos.items():
                if v is None:
                    c.pop(k, None)
                else:
                    c[k] = v
            _salvar(d)

# ------------------------------------------------------------------ recuperação pela linha de comando
if __name__ == "__main__":
    import getpass
    import sys
    if len(sys.argv) == 3 and sys.argv[1] in ("--novo-admin", "--nova-senha"):
        lg = sys.argv[2].strip().lower()
        s1 = getpass.getpass("Senha (mínimo 8 caracteres, com letras e números): ")
        if s1 != getpass.getpass("Repita a senha: "):
            sys.exit("As senhas não conferem.")
        try:
            if sys.argv[1] == "--nova-senha" or obter(lg):
                trocar_senha(lg, s1)
                atualizar(lg, ativo=True)
                print(f"Senha de '{lg}' redefinida.")
            else:
                criar(lg, "Administrador", s1, "admin", por="linha de comando")
                print(f"Administrador '{lg}' criado.")
            auditar(lg, "servidor", "senha/admin pela linha de comando")
        except ValueError as ex:
            sys.exit(str(ex))
    else:
        print("Uso: venv/bin/python usuarios.py --novo-admin LOGIN   (ou --nova-senha LOGIN)")

