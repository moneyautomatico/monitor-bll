#!/usr/bin/env bash
# Gerencia várias empresas no Monitor BLL.
#   bash empresas.sh adicionar        -> cadastra uma empresa (pergunta tudo)
#   bash empresas.sh listar           -> mostra empresas e se estão rodando
#   bash empresas.sh destino <id>     -> muda para onde vão os alertas da empresa
#   bash empresas.sh senha <id>       -> troca e-mail/senha da BLL da empresa
#   bash empresas.sh log <id>         -> mostra o que o monitor da empresa está fazendo
#   bash empresas.sh reiniciar <id>   -> reinicia o monitor da empresa
#   bash empresas.sh remover <id>     -> para e remove a empresa
#   bash empresas.sh migrar           -> converte a instalação de 1 empresa para este formato
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
mkdir -p empresas
chmod 700 empresas

garantir_servico() {
  if [ ! -f /etc/systemd/system/monitor-bll@.service ]; then
    tee /etc/systemd/system/monitor-bll@.service >/dev/null <<EOF
[Unit]
Description=Monitor BLL Compras - empresa %i
After=network-online.target
Wants=network-online.target

[Service]
User=$(whoami)
WorkingDirectory=${DIR}
Environment=TZ=America/Sao_Paulo
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1366x800x24" ${DIR}/venv/bin/python ${DIR}/monitor_bll.py --empresa %i
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
  fi
}

gerar_id() {  # "Multifornece Soluções LTDA" -> multifornece-solucoes-ltda
  echo "$1" | iconv -f utf-8 -t ascii//TRANSLIT 2>/dev/null | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-30
}

valor() { grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2- || true; }

perguntar_destino() {  # escreve ENVIAR_PARA e, se preciso, o Telegram/ntfy próprios em $1
  local ARQ="$1"
  echo
  echo "Para onde vão os alertas desta empresa?"
  echo "  1) Telegram geral (o mesmo de todas as empresas)"
  echo "  2) Telegram próprio desta empresa"
  echo "  3) Os dois"
  read -rp "Escolha [1]: " OP; OP="${OP:-1}"
  case "$OP" in 2) MODO=proprio ;; 3) MODO=ambos ;; *) MODO=geral ;; esac
  sed -i '/^ENVIAR_PARA=/d;/^EMPRESA_TELEGRAM_TOKEN=/d;/^EMPRESA_TELEGRAM_CHAT_ID=/d;/^EMPRESA_NTFY_TOPICO=/d' "$ARQ"
  echo "ENVIAR_PARA=$MODO" >> "$ARQ"
  if [ "$MODO" != geral ]; then
    echo
    echo "Telegram próprio: pode ser um bot novo (@BotFather) ou o mesmo bot geral."
    read -rp "Token do bot (ENTER = usar o mesmo bot geral): " TK
    TOKEN_USADO="${TK:-$(valor config.env TELEGRAM_TOKEN)}"
    [ -n "$TK" ] && echo "EMPRESA_TELEGRAM_TOKEN=$TK" >> "$ARQ"
    echo "Agora a pessoa (ou grupo) que vai receber deve mandar \"oi\" para esse bot."
    echo "Para um GRUPO: adicione o bot ao grupo e mande uma mensagem no grupo."
    read -rp "Quando tiver mandado, tecle ENTER... " _
    LISTA="$(curl -s "https://api.telegram.org/bot${TOKEN_USADO}/getUpdates" | ./venv/bin/python -c '
import sys, json
vistos = {}
for u in json.load(sys.stdin).get("result", []):
    m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
    c = m.get("chat") or {}
    if c.get("id"):
        vistos[c["id"]] = c.get("title") or ((c.get("first_name") or "") + " " + (c.get("last_name") or "")).strip()
for i, n in vistos.items():
    print(f"{i}\t{n}")
')"
    if [ -n "$LISTA" ]; then
      echo "Conversas encontradas:"; echo "$LISTA" | nl -w2 -s') '
      read -rp "Número da conversa que vai receber os alertas: " N
      CID="$(echo "$LISTA" | sed -n "${N}p" | cut -f1)"
    else
      read -rp "Não achei conversas. Digite o chat_id manualmente: " CID
    fi
    echo "EMPRESA_TELEGRAM_CHAT_ID=$CID" >> "$ARQ"
    read -rp "Tópico ntfy próprio para alarme no celular (ENTER = nenhum): " NT
    [ -n "$NT" ] && echo "EMPRESA_NTFY_TOPICO=$NT" >> "$ARQ"
  fi
}

adicionar() {
  garantir_servico
  read -rp "Nome da empresa (como vai aparecer nos alertas): " NOME
  ID="$(gerar_id "$NOME")"
  read -rp "Identificador curto [$ID]: " X; ID="$(gerar_id "${X:-$ID}")"
  ARQ="empresas/$ID.env"
  [ -f "$ARQ" ] && { echo "Já existe uma empresa com id '$ID'."; exit 1; }
  read -rp "E-mail de login da BLL desta empresa: " EMAIL
  read -rsp "Senha da BLL (6 números, não aparece): " SENHA; echo
  read -rp "Parte do nome do perfil a escolher após o login (ex.: OPERADOR EMPRESA X): " PERFIL
  umask 077
  cat > "$ARQ" <<EOF
EMPRESA_NOME=$NOME
BLL_EMAIL=$EMAIL
BLL_SENHA=$SENHA
BLL_PERFIL=${PERFIL:-OPERADOR}
EOF
  chmod 600 "$ARQ"
  perguntar_destino "$ARQ"
  systemctl enable --now "monitor-bll@$ID"
  echo
  echo "Empresa '$NOME' cadastrada (id: $ID) e monitor iniciado."
  echo "Em alguns minutos chega o \"Monitor BLL ativo\" com o nome dela."
  echo "Acompanhar: bash empresas.sh log $ID"
}

listar() {
  printf "%-28s %-10s %-9s %s\n" "ID" "SITUAÇÃO" "ALERTAS" "NOME"
  for f in empresas/*.env; do
    [ -e "$f" ] || { echo "(nenhuma empresa cadastrada)"; return; }
    ID="$(basename "$f" .env)"
    ST="$(systemctl is-active "monitor-bll@$ID" 2>/dev/null || true)"
    printf "%-28s %-10s %-9s %s\n" "$ID" "$ST" "$(valor "$f" ENVIAR_PARA)" "$(valor "$f" EMPRESA_NOME)"
  done
  echo
  echo "Memória livre: $(free -h | awk '/Mem:/{print $7}')   Navegadores simultâneos: $(M=$(valor config.env MAX_NAVEGADORES); echo ${M:-3})"
}

exigir_id() { [ -n "${1:-}" ] && [ -f "empresas/$1.env" ] || { echo "Informe um id válido. Use: bash empresas.sh listar"; exit 1; }; }

case "${1:-}" in
  adicionar) adicionar ;;
  listar|"") listar ;;
  destino) exigir_id "${2:-}"; perguntar_destino "empresas/$2.env"; systemctl restart "monitor-bll@$2"; echo "OK" ;;
  senha)
    exigir_id "${2:-}"
    read -rp "Novo e-mail da BLL (ENTER = manter): " EMAIL
    read -rsp "Nova senha (6 números): " SENHA; echo
    [ -n "$EMAIL" ] && sed -i "s/^BLL_EMAIL=.*/BLL_EMAIL=$EMAIL/" "empresas/$2.env"
    sed -i "s/^BLL_SENHA=.*/BLL_SENHA=$SENHA/" "empresas/$2.env"
    rm -rf "empresas/$2/perfil_navegador"
    systemctl restart "monitor-bll@$2"; echo "OK" ;;
  log) exigir_id "${2:-}"; journalctl -u "monitor-bll@$2" -n "${3:-30}" --no-pager ;;
  reiniciar) exigir_id "${2:-}"; systemctl restart "monitor-bll@$2"; echo "OK" ;;
  remover)
    exigir_id "${2:-}"
    read -rp "Remover a empresa '$2' e todo o histórico dela? (s/N) " R
    [ "$R" = s ] || [ "$R" = S ] || exit 0
    systemctl disable --now "monitor-bll@$2" || true
    rm -rf "empresas/$2" "empresas/$2.env"; echo "Removida." ;;
  migrar)
    garantir_servico
    EMAIL="$(valor config.env BLL_EMAIL)"
    [ -n "$EMAIL" ] || { echo "Não há empresa configurada no config.env para migrar."; exit 1; }
    read -rp "Nome da empresa atual [MULTIFORNECE]: " NOME; NOME="${NOME:-MULTIFORNECE}"
    ID="$(gerar_id "$NOME")"
    umask 077
    cat > "empresas/$ID.env" <<EOF
EMPRESA_NOME=$NOME
BLL_EMAIL=$EMAIL
BLL_SENHA=$(valor config.env BLL_SENHA)
BLL_PERFIL=$(valor config.env BLL_PERFIL)
ENVIAR_PARA=geral
EOF
    chmod 600 "empresas/$ID.env"
    systemctl disable --now monitor-bll.service 2>/dev/null || true
    mkdir -p "empresas/$ID"
    [ -f bll_monitor.db ] && mv bll_monitor.db "empresas/$ID/"
    [ -d perfil_navegador ] && mv perfil_navegador "empresas/$ID/"
    [ -f painel.html ] && mv painel.html "empresas/$ID/"
    sed -i '/^BLL_EMAIL=/d;/^BLL_SENHA=/d' config.env
    grep -q '^MAX_NAVEGADORES=' config.env || echo "MAX_NAVEGADORES=3" >> config.env
    systemctl enable --now "monitor-bll@$ID"
    systemctl restart painel-bll
    echo "Pronto: '$NOME' agora roda como empresa '$ID'. Histórico e login mantidos."
    echo "Para cadastrar outra: bash empresas.sh adicionar" ;;
  *) sed -n '2,10p' "$0" ;;
esac
