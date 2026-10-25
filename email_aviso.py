"""Avisos por e-mail (alternativa ao Telegram): usa o servidor SMTP que o administrador cadastra no painel."""
import html as h
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


def configurado(cfg):
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_REMETENTE"))


def corpo_html(texto_tg):
    """Texto no formato do Telegram (HTML simples + quebras de linha) -> e-mail HTML legível."""
    t = re.sub(r"</?(?!b>|/b>|i>|/i>)[^>]*>", "", texto_tg or "")   # só mantém <b> e <i>
    t = t.replace("\n", "<br>")
    return (f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#1d2b24;line-height:1.5">{t}'
            '<hr style="border:0;border-top:1px solid #dfe5e1;margin:18px 0"><small style="color:#5d6b63">Licitatudo – aviso automático. '
            'Ajuste o que recebe em ⚙️ Minhas configurações.</small></div>')


def enviar(cfg, para, assunto, texto_tg, anexos=()):
    if not configurado(cfg) or not para:
        return False
    msg = EmailMessage()
    msg["Subject"] = assunto[:180]
    msg["From"] = formataddr(("Licitatudo", cfg["SMTP_REMETENTE"]))
    msg["To"] = para
    msg["Message-ID"] = make_msgid(domain=(cfg["SMTP_REMETENTE"].split("@")[-1] or "licitatudo"))
    simples = h.unescape(re.sub(r"<[^>]+>", "", texto_tg or ""))
    msg.set_content(simples)
    msg.add_alternative(corpo_html(texto_tg), subtype="html")
    for nome, dados, mime in anexos:
        principal, _, sub = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub or "octet-stream", filename=nome)
    porta = int(cfg.get("SMTP_PORTA") or 587)
    ctx = ssl.create_default_context()
    if porta == 465:
        srv = smtplib.SMTP_SSL(cfg["SMTP_HOST"], porta, context=ctx, timeout=30)
    else:
        srv = smtplib.SMTP(cfg["SMTP_HOST"], porta, timeout=30)
        srv.starttls(context=ctx)
    try:
        if cfg.get("SMTP_USUARIO"):
            srv.login(cfg["SMTP_USUARIO"], cfg.get("SMTP_SENHA", ""))
        srv.send_message(msg)
    finally:
        try:
            srv.quit()
        except Exception:  # noqa: BLE001
            pass
    return True
