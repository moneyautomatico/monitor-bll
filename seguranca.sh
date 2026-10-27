#!/usr/bin/env bash
# Blindagem do servidor do Expert Licitante. Pode rodar mais de uma vez sem problema.
#   bash seguranca.sh                 -> usa o endereço grátis <ip>.sslip.io para o HTTPS
#   bash seguranca.sh meu.dominio.com www.meu.dominio.com -> usa o seu domínio (aponte o DNS para o IP do servidor antes);
#                                     só o domínio atende (www redireciona para ele; o endereço sslip.io é desligado)
# O que faz:
#   1. Permissões: arquivos com senhas/tokens só o administrador lê.
#   2. Atualizações de segurança automáticas do Ubuntu.
#   3. fail2ban: bloqueia quem erra a senha do SSH várias vezes.
#   4. Aviso no Telegram a cada login no servidor por SSH.
#   5. HTTPS (cadeado) no painel com Caddy + certificado grátis; a porta 8080 deixa de ficar aberta na internet.
#   6. Mostra portas abertas que não deveriam estar.
set -uo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
[ "$(id -u)" = "0" ] || { echo "Rode como root."; exit 1; }
ok()  { echo "  ✔ $*"; }
av()  { echo "  ⚠ $*"; }
cfg() { grep -E "^$1=" "$DIR/config.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
set_cfg() {  # set_cfg CHAVE VALOR
  if grep -qE "^$1=" config.env; then sed -i "s|^$1=.*|$1=$2|" config.env; else echo "$1=$2" >> config.env; fi
}
PORTA="$(cfg PAINEL_PORTA)"; PORTA="${PORTA:-8080}"
export DEBIAN_FRONTEND=noninteractive

echo "==> 1/6 Permissões dos arquivos sensíveis"
chmod 600 config.env empresas/*.env sessoes.json acessos_conhecidos.json buscas_salvas.json buscas_vistos.json \
  oportunidades.json vigia_estado.json 2>/dev/null
chmod 700 empresas empresas/*/ backups analises 2>/dev/null
find empresas -name 'cookies_sessao.json' -exec chmod 600 {} + 2>/dev/null
find empresas -name 'bll_monitor.db' -exec chmod 600 {} + 2>/dev/null
chmod 700 "$DIR"
ok "senhas, tokens, bancos e cópias só podem ser lidos pelo administrador"

echo "==> 2/6 Atualizações de segurança automáticas"
apt-get update -q >/dev/null 2>&1
apt-get install -y -q unattended-upgrades >/dev/null 2>&1
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
ok "o Ubuntu instala sozinho as correções de segurança todo dia"

echo "==> 3/6 fail2ban (bloqueio de quem tenta adivinhar a senha do SSH)"
apt-get install -y -q fail2ban >/dev/null 2>&1
SSH_PORTA="$(sshd -T 2>/dev/null | awk '/^port /{print $2}' | head -1)"; SSH_PORTA="${SSH_PORTA:-22}"
cat > /etc/fail2ban/jail.d/licitatudo.conf <<EOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
port = ${SSH_PORTA}
backend = systemd
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 && systemctl restart fail2ban
sleep 2
if fail2ban-client status sshd >/dev/null 2>&1; then
  ok "ativo na porta SSH ${SSH_PORTA}: 5 senhas erradas em 10 min = 1 hora bloqueado"
  echo "     (se você mesmo for bloqueado: fail2ban-client set sshd unbanip SEU_IP)"
else
  av "fail2ban não iniciou – veja: journalctl -u fail2ban -n 30"
fi

echo "==> 4/6 Aviso no Telegram a cada login no servidor"
cat > /usr/local/bin/licitatudo-alerta-ssh <<EOF
#!/bin/sh
# Chamado pelo PAM quando alguém entra por SSH. Nunca bloqueia o login.
[ "\$PAM_TYPE" = "open_session" ] || exit 0
TOK="\$(grep -E '^TELEGRAM_TOKEN=' ${DIR}/config.env | cut -d= -f2-)"
CHAT="\$(grep -E '^TELEGRAM_CHAT_ID=' ${DIR}/config.env | cut -d= -f2-)"
[ -n "\$TOK" ] && [ -n "\$CHAT" ] || exit 0
TXT="🛡️ Login no SERVIDOR do Expert Licitante: usuário \$PAM_USER, de \${PAM_RHOST:-local}, em \$(date '+%d/%m/%Y %H:%M'). Se não foi você, troque a senha do servidor na Hostinger."
( timeout 10 curl -s -o /dev/null "https://api.telegram.org/bot\$TOK/sendMessage" \
    --data-urlencode "chat_id=\$CHAT" --data-urlencode "text=\$TXT" & ) >/dev/null 2>&1
exit 0
EOF
chmod 700 /usr/local/bin/licitatudo-alerta-ssh
if ! grep -q licitatudo-alerta-ssh /etc/pam.d/sshd; then
  echo "session optional pam_exec.so quiet /usr/local/bin/licitatudo-alerta-ssh" >> /etc/pam.d/sshd
fi
ok "cada entrada por SSH manda um aviso no Telegram geral"

echo "==> 5/6 HTTPS (cadeado) no painel"
IP="$(curl -s -4 --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
SSLIP="$(echo "$IP" | tr . -).sslip.io"
DOMINIO="${1:-$SSLIP}"
# com domínio próprio: só ele atende (os outros endereços informados, ex. www, redirecionam para ele; o sslip.io deixa de existir)
REDIRS=""
for extra in "${@:2}"; do
  REDIRS="${REDIRS}
${extra} {
	redir https://${DOMINIO}{uri} permanent
}"
done
if ! command -v caddy >/dev/null; then
  apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null 2>&1
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q >/dev/null 2>&1
  apt-get install -y -q caddy >/dev/null 2>&1
fi
for p in 80 443; do
  iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport $p -j ACCEPT
done
cat > /etc/caddy/Caddyfile <<EOF
${DOMINIO} {
	encode gzip
	header -Server
	reverse_proxy 127.0.0.1:${PORTA}
}${REDIRS}
EOF
systemctl enable caddy >/dev/null 2>&1; systemctl restart caddy
echo "  aguardando o certificado de ${DOMINIO} (até 2 minutos)..."
HTTPS_OK=""
for _ in $(seq 1 24); do
  sleep 5
  cod="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${DOMINIO}/login" || true)"
  if [ "$cod" = "200" ]; then HTTPS_OK=1; break; fi
done
if [ -n "$HTTPS_OK" ]; then
  set_cfg PAINEL_HOST 127.0.0.1
  set_cfg PAINEL_URL "https://${DOMINIO}"
  chmod 600 config.env
  systemctl restart painel-bll
  # a 8080 deixa de aceitar conexões de fora (o painel só responde ao Caddy)
  while iptables -C INPUT -p tcp --dport "$PORTA" -j ACCEPT 2>/dev/null; do iptables -D INPUT -p tcp --dport "$PORTA" -j ACCEPT; done
  iptables -C INPUT -p tcp --dport "$PORTA" ! -i lo -j DROP 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$PORTA" ! -i lo -j DROP
  ok "painel com cadeado em: https://${DOMINIO}"
  ok "a porta ${PORTA} foi fechada para a internet (o endereço antigo http://${IP}:${PORTA} para de funcionar)"
  systemctl restart 'monitor-bll@*' 2>/dev/null || true
else
  av "o certificado não saiu. O painel continua no endereço antigo (http://${IP}:${PORTA})."
  av "confira se as portas 80 e 443 estão liberadas no firewall da Hostinger (hPanel → VPS → Firewall)"
  av "e rode de novo: bash seguranca.sh   – detalhes: journalctl -u caddy -n 40"
fi
netfilter-persistent save >/dev/null 2>&1 || (apt-get install -y -q iptables-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1)

echo "==> 6/6 Portas abertas para a internet"
ESPERADAS=" ${SSH_PORTA} 80 443 "
EXTRAS=""
while read -r porta; do
  case "$ESPERADAS" in *" $porta "*) ;; *) EXTRAS="$EXTRAS $porta";; esac
done < <(ss -tlnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un)
if [ -n "$HTTPS_OK" ]; then EXTRAS="$(echo "$EXTRAS" | tr ' ' '\n' | grep -vx "$PORTA" | tr '\n' ' ')"; fi
if [ -n "$(echo "$EXTRAS" | tr -d ' ')" ]; then
  av "outras portas escutando de fora:$EXTRAS – me mande esta lista para conferir se são necessárias"
else
  ok "só SSH (${SSH_PORTA}), 80 e 443 abertas"
fi

echo
echo "================= RESUMO ================="
[ -n "$HTTPS_OK" ] && echo " Painel: https://${DOMINIO}" || echo " Painel: http://${IP}:${PORTA} (sem HTTPS ainda)"
echo " Próximos passos manuais (recomendados):"
echo "  - Painel → 👤 Minha conta: troque a senha do painel por uma de 12+ caracteres."
echo "  - Hostinger: use uma senha forte para o root do servidor e ative a verificação em 2 etapas da conta."
echo "=========================================="
