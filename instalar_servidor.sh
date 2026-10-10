#!/usr/bin/env bash
# Instala o Monitor BLL num servidor Ubuntu (Oracle Cloud Always Free).
# Uso:  bash instalar_servidor.sh
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
USUARIO_SO="$(whoami)"

echo "==> 1/6 Instalando pacotes do sistema (alguns minutos)..."
sudo apt-get update -y -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip xvfb iptables-persistent curl

echo "==> 2/6 Criando ambiente Python e baixando o navegador..."
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q playwright requests
sudo ./venv/bin/python -m playwright install-deps chromium
./venv/bin/python -m playwright install chromium

echo
echo "==> 3/6 Configuração (o que você digitar fica só neste servidor)"
read -rp "Token do bot do Telegram (do @BotFather): " TG_TOKEN
echo "Agora abra o seu bot no Telegram e mande \"oi\". Depois tecle ENTER aqui."
read -r _
CHAT_ID="$(curl -s "https://api.telegram.org/bot${TG_TOKEN}/getUpdates" | ./venv/bin/python -c \
 'import sys,json; r=json.load(sys.stdin).get("result",[]); ids=[(u.get("message") or {}).get("chat",{}).get("id") for u in r]; ids=[i for i in ids if i]; print(ids[-1] if ids else "")')"
if [ -z "$CHAT_ID" ]; then
  read -rp "Não achei a conversa. Mande \"oi\" ao bot de novo e digite o chat_id (ou ENTER para deixar em branco): " CHAT_ID
else
  echo "chat_id encontrado: $CHAT_ID"
fi
read -rp "E-mail de login da BLL: " BLL_EMAIL
read -rsp "Senha da BLL (6 números, não aparece na tela): " BLL_SENHA; echo
read -rp "Perfil a escolher após o login [OPERADOR MULTIFORNECE]: " BLL_PERFIL
BLL_PERFIL="${BLL_PERFIL:-OPERADOR MULTIFORNECE}"
read -rsp "Crie uma senha para abrir o painel na internet: " PAINEL_SENHA; echo

cat > config.env <<EOF
TELEGRAM_TOKEN=${TG_TOKEN}
TELEGRAM_CHAT_ID=${CHAT_ID}
INTERVALO_MINUTOS=10
SITUACOES_IGNORADAS=HOMOLOGADO,CANCELADO,REVOGADO,ANULADO,FRACASSADO,DESERTO
HORARIO_INICIO=0
HORARIO_FIM=24
BLL_EMAIL=${BLL_EMAIL}
BLL_SENHA=${BLL_SENHA}
BLL_PERFIL=${BLL_PERFIL}
NAVEGADOR_VISIVEL=1
PAINEL_USUARIO=bll
PAINEL_SENHA=${PAINEL_SENHA}
PAINEL_PORTA=8080
EOF
chmod 600 config.env

echo "==> 4/6 Criando os serviços (ligam sozinhos e reiniciam se cair)..."
sudo tee /etc/systemd/system/monitor-bll.service >/dev/null <<EOF
[Unit]
Description=Monitor BLL Compras
After=network-online.target
Wants=network-online.target

[Service]
User=${USUARIO_SO}
WorkingDirectory=${DIR}
Environment=TZ=America/Sao_Paulo
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1366x800x24" ${DIR}/venv/bin/python ${DIR}/monitor_bll.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/painel-bll.service >/dev/null <<EOF
[Unit]
Description=Painel web do Monitor BLL
After=network-online.target

[Service]
User=${USUARIO_SO}
WorkingDirectory=${DIR}
ExecStart=${DIR}/venv/bin/python ${DIR}/painel_web.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "==> 5/6 Liberando a porta 8080 no firewall interno do servidor..."
sudo iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT 1 -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "==> 6/6 Iniciando..."
sudo timedatectl set-timezone America/Sao_Paulo || true
sudo systemctl daemon-reload
sudo systemctl enable --now painel-bll.service monitor-bll.service

IP="$(curl -s https://ifconfig.me || echo SEU_IP)"
echo
echo "======================================================"
echo " Pronto! O monitor está rodando."
echo " Painel:  http://${IP}:8080   (usuário: bll)"
echo " Em alguns minutos chega no Telegram: \"Monitor BLL ativo\"."
echo
echo " Ver o que ele está fazendo:  sudo journalctl -u monitor-bll -f"
echo " Reiniciar:                   sudo systemctl restart monitor-bll"
echo "======================================================"
