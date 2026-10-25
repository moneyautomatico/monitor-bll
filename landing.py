"""Página de vendas (pública) e cadastro do teste grátis."""
import html as h
import urllib.parse

e = h.escape

ICONES = {
    "radar": '<path d="M12 12 19 5"/><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>',
    "sino": '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "quadro": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "balanca": '<path d="M12 3v18"/><path d="M5 7h14"/><path d="m5 7-3 7a4 4 0 0 0 6 0Z"/><path d="m19 7-3 7a4 4 0 0 0 6 0Z"/>',
    "calc": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h8"/>',
    "pasta": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "lupa": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "grafico": '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>',
    "escudo": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
}


def ic(nome, cls="ic"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ICONES[nome]}</svg>')


RECURSOS = [
    ("radar", "Monitor 24 h nas plataformas", "Lê sozinho o chat do pregoeiro, as diligências, a situação e a classificação na BLL, BNC, Portal de Compras Públicas e Licitanet."),
    ("sino", "Alerta que toca no celular", "Mensagem do pregoeiro, convocação e prazo curto chegam no Telegram e tocam alto (mesmo no silencioso) pelo ntfy."),
    ("quadro", "Siga-me de cada licitação", "Quadro tipo kanban com o passo a passo – edital, documentos, proposta, envio, resultado, contrato – e a barra do que falta."),
    ("balanca", "Prazos legais calculados", "Impugnação, esclarecimento, recurso e contrarrazões em dias úteis (Lei 14.133), na agenda e com aviso 24 h e 2 h antes."),
    ("calc", "Proposta com preço certo", "Itens do edital pela IA ou pelo PNCP, cotação com fornecedores, formação de preço com impostos e margem e o lance mínimo da disputa."),
    ("pasta", "Habilitação em um PDF", "Declarações com papel timbrado, certidões com aviso de vencimento, atestados que atendem o edital e o pacote pronto para assinar."),
    ("lupa", "Editais e oportunidades", "Busca no PNCP, alerta de novas licitações do seu perfil, análise do edital com IA e aviso quando sai errata ou muda a data."),
    ("grafico", "Por que perdi? + pós-venda", "Diferença para o vencedor lote a lote, ficha do órgão (paga em dia?), contratos, atas de registro de preço e lucro real."),
]

FAQ = [
    ("O sistema dá lance ou envia documentos por mim?",
     "Não. O Licitatudo só lê as plataformas e prepara arquivos e rascunhos. Quem envia, assina e dá lance é sempre você – com tudo pronto e no prazo."),
    ("Preciso de cartão de crédito para testar?", "Não. O teste é grátis por {dias} dias e não pede cartão. No fim, você decide se quer continuar."),
    ("Quais plataformas ele acompanha?",
     "BLL, BNC, Portal de Compras Públicas e Licitanet para o monitor; PNCP para buscar editais e oportunidades. Você cadastra o login da sua empresa e ele começa a ler."),
    ("Meus dados ficam seguros?",
     "Cada conta vê só as próprias empresas – os dados não se cruzam entre clientes. O acesso é por HTTPS, as senhas do painel são guardadas com criptografia e toda ação fica registrada."),
    ("Funciona no celular?", "Sim. Os alertas chegam no Telegram e o painel pode ser instalado na tela inicial do celular como um aplicativo."),
    ("E depois dos {dias} dias?", "Seus dados continuam guardados. É só falar com a gente para escolher o plano e seguir de onde parou."),
]


def pagina(dias=7, whatsapp="", cadastro_aberto=True):
    wa = ("https://wa.me/" + "".join(c for c in whatsapp if c.isdigit()) + "?text=" +
          urllib.parse.quote("Olá! Vim pelo site do Licitatudo e quero saber mais.")) if whatsapp else ""
    cta = (f'<a class="btn-cta" href="/teste-gratis">Começar {dias} dias grátis</a>' if cadastro_aberto else
           (f'<a class="btn-cta" href="{e(wa)}" target="_blank" rel="noopener">Falar com a gente</a>' if wa else ""))
    recursos = "".join(f'<article class="rec">{ic(i)}<h3>{e(t)}</h3><p>{e(d)}</p></article>' for i, t, d in RECURSOS)
    faq = "".join(f'<details><summary>{e(p.format(dias=dias))}</summary><p>{e(r.format(dias=dias))}</p></details>' for p, r in FAQ)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Licitatudo – licitação no piloto automático</title>
<meta name="description" content="Monitor do pregoeiro 24 h, prazos legais, proposta com preço certo e habilitação em um PDF. Teste grátis por {dias} dias, sem cartão.">
<meta name="theme-color" content="#173f2c"><link rel="icon" href="/icone-192.png"><link rel="manifest" href="/manifest.webmanifest">
<style>
:root{{--verde:#1f5c3f;--verde2:#2f8a5b;--claro:#e9f4ee;--txt:#14211a;--sub:#55645b;--borda:#dde6e0;--bg:#f7faf8}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--txt);background:var(--bg);line-height:1.55}}
a{{color:inherit}}.wrap{{max-width:1120px;margin:auto;padding:0 20px}}
.topo{{position:sticky;top:0;z-index:20;background:rgba(247,250,248,.9);backdrop-filter:blur(8px);border-bottom:1px solid var(--borda)}}
.topo .wrap{{display:flex;align-items:center;gap:18px;height:62px}}.marca{{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;text-decoration:none}}
.marca i{{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#7fd3a5,var(--verde2));display:grid;place-items:center;color:#fff;font-style:normal;font-size:13px}}
.topo nav{{margin-left:auto;display:flex;gap:18px;align-items:center;font-size:14px}}.topo nav a{{text-decoration:none;color:var(--sub)}}
.topo nav a:hover{{color:var(--txt)}}.topo .entrar{{border:1px solid var(--borda);padding:7px 14px;border-radius:9px;color:var(--txt)}}
.btn-cta{{display:inline-block;background:linear-gradient(135deg,var(--verde2),var(--verde));color:#fff!important;text-decoration:none;font-weight:700;
padding:14px 24px;border-radius:12px;box-shadow:0 12px 28px rgba(31,92,63,.28);transition:.15s}}.btn-cta:hover{{transform:translateY(-1px)}}
.topo .btn-cta{{padding:8px 14px;font-size:14px;box-shadow:none;white-space:nowrap}}
.hero{{padding:72px 0 56px;background:radial-gradient(900px 420px at 85% 10%,#d5efe1 0,transparent 70%)}}
.hero .wrap{{display:grid;grid-template-columns:1.05fr 1fr;gap:48px;align-items:center}}
.selo{{display:inline-flex;gap:8px;align-items:center;background:var(--claro);color:var(--verde);border-radius:999px;padding:6px 12px;font-size:13px;font-weight:600}}
h1{{font-size:46px;line-height:1.08;margin:16px 0 18px;letter-spacing:-.5px}}h1 em{{font-style:normal;color:var(--verde2)}}
.hero p.lead{{font-size:18px;color:var(--sub);margin:0 0 26px;max-width:540px}}
.garantias{{display:flex;gap:18px;flex-wrap:wrap;margin-top:18px;font-size:13.5px;color:var(--sub)}}.garantias span{{display:flex;gap:6px;align-items:center}}
.garantias .ic{{width:16px;height:16px;color:var(--verde2)}}
.mock{{background:#fff;border:1px solid var(--borda);border-radius:18px;box-shadow:0 30px 60px rgba(20,33,26,.12);overflow:hidden}}
.mock .barra{{background:#173f2c;color:#fff;padding:10px 14px;font-size:13px;font-weight:700;display:flex;gap:14px}}.mock .barra span{{opacity:.7;font-weight:500}}
.mock .corpo{{padding:16px}}.mock h4{{margin:0 0 10px;font-size:15px}}.it{{display:flex;gap:10px;align-items:center;border:1px solid var(--borda);border-left:4px solid #c62828;
border-radius:10px;padding:9px 11px;margin-bottom:8px;font-size:13px}}.it.l{{border-left-color:#ef6c00}}.it.v{{border-left-color:var(--verde2)}}
.it b{{display:block}}.it small{{color:var(--sub)}}.it .bt{{margin-left:auto;background:var(--claro);color:var(--verde);border-radius:7px;padding:4px 9px;font-size:11.5px;font-weight:700;white-space:nowrap}}
.toast{{position:relative;margin:-26px 18px 0 auto;width:78%;background:#fff;border:1px solid var(--borda);border-radius:14px;padding:11px 13px;font-size:12.5px;
box-shadow:0 14px 30px rgba(20,33,26,.16);display:flex;gap:10px}}.toast .av{{width:32px;height:32px;border-radius:50%;background:#2aa1da;color:#fff;display:grid;place-items:center;flex:none}}
section{{padding:64px 0}}.titulo{{text-align:center;max-width:720px;margin:0 auto 38px}}.titulo h2{{font-size:32px;margin:0 0 10px;letter-spacing:-.3px}}.titulo p{{color:var(--sub);margin:0;font-size:16.5px}}
.dores{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.dor{{background:#fff;border:1px solid var(--borda);border-radius:14px;padding:20px}}
.dor b{{display:block;font-size:16px;margin-bottom:6px}}.dor p{{margin:0;color:var(--sub);font-size:14.5px}}
.recs{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.rec{{background:#fff;border:1px solid var(--borda);border-radius:14px;padding:20px}}
.rec .ic{{width:30px;height:30px;color:var(--verde2);background:var(--claro);border-radius:9px;padding:5px}}.rec h3{{font-size:16px;margin:12px 0 6px}}.rec p{{margin:0;font-size:14px;color:var(--sub)}}
.passos{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;counter-reset:p}}.passo{{background:#fff;border:1px solid var(--borda);border-radius:14px;padding:22px;position:relative}}
.passo:before{{counter-increment:p;content:counter(p);position:absolute;top:-14px;left:20px;width:30px;height:30px;border-radius:50%;background:var(--verde2);color:#fff;
display:grid;place-items:center;font-weight:800}}.passo b{{display:block;margin:6px 0}}.passo p{{margin:0;color:var(--sub);font-size:14.5px}}
.faixa{{background:#173f2c;color:#fff}}.faixa .titulo p{{color:#cfe3d7}}.seg{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
.seg div{{border:1px solid rgba(255,255,255,.15);border-radius:14px;padding:20px;background:rgba(255,255,255,.04)}}.seg b{{display:block;margin-bottom:6px}}.seg p{{margin:0;color:#cfe3d7;font-size:14.5px}}
.faq{{max-width:760px;margin:auto}}.faq details{{background:#fff;border:1px solid var(--borda);border-radius:12px;padding:14px 18px;margin-bottom:10px}}
.faq summary{{cursor:pointer;font-weight:700}}.faq p{{margin:10px 0 0;color:var(--sub)}}
.final{{text-align:center;background:radial-gradient(700px 300px at 50% 0,#d5efe1 0,transparent 70%)}}.final h2{{font-size:34px;margin:0 0 12px}}.final p{{color:var(--sub);margin:0 0 24px;font-size:17px}}
footer{{border-top:1px solid var(--borda);padding:26px 0;color:var(--sub);font-size:13px}}footer .wrap{{display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between}}
.wa{{position:fixed;right:18px;bottom:18px;background:#25d366;color:#fff;border-radius:999px;padding:12px 16px;font-weight:700;text-decoration:none;box-shadow:0 10px 24px rgba(0,0,0,.2);font-size:14px;z-index:30}}
@media(max-width:960px){{.hero .wrap{{grid-template-columns:1fr}}.recs{{grid-template-columns:1fr 1fr}}h1{{font-size:36px}}}}
@media(max-width:640px){{.dores,.passos,.seg,.recs{{grid-template-columns:1fr}}.topo nav a.lnk{{display:none}}h1{{font-size:31px}}.titulo h2{{font-size:26px}}section{{padding:48px 0}}.hero{{padding:44px 0}}}}
</style></head><body>
<header class="topo"><div class="wrap"><a class="marca" href="/"><i>LT</i>Licitatudo</a>
<nav><a class="lnk" href="#recursos">Recursos</a><a class="lnk" href="#como">Como funciona</a><a class="lnk" href="#faq">Dúvidas</a>
<a class="entrar" href="/login">Entrar</a>{cta.replace(f"Começar {dias} dias grátis", "Testar grátis")}</nav></div></header>

<div class="hero"><div class="wrap"><div>
<span class="selo">⚡ Para quem disputa pregão eletrônico</span>
<h1>Pare de perder licitação por <em>mensagem não vista</em> e prazo esquecido.</h1>
<p class="lead">O Licitatudo vigia o pregoeiro por você, calcula os prazos da lei, monta a proposta com o preço certo e deixa a habilitação pronta em um PDF.
Você só decide e envia.</p>
{cta}
<div class="garantias"><span>{ic("check")} {dias} dias grátis</span><span>{ic("check")} Sem cartão de crédito</span><span>{ic("check")} Pronto em 10 minutos</span></div>
</div>
<div><div class="mock"><div class="barra">Licitatudo <span>Hoje</span><span>Licitações</span><span>Disputa</span></div>
<div class="corpo"><h4>🏠 Hoje</h4>
<div class="it"><span>🏁</span><div><b>Disputa HOJE às 09:00 – Pregão 45/2026</b><small>Prefeitura · 86% pronto · falta: pacote</small></div><span class="bt">Abrir</span></div>
<div class="it"><span>⏰</span><div><b>Proposta ajustada em 2 horas</b><small>Pedido do pregoeiro no chat</small></div><span class="bt">Ver</span></div>
<div class="it l"><span>📑</span><div><b>CND Federal vence em 3 dias</b><small>Link de emissão + CNPJ copiado</small></div><span class="bt">Renovar</span></div>
<div class="it v"><span>💰</span><div><b>Lance mínimo do lote 3: R$ 13,30</b><small>Custo + impostos + margem mínima</small></div><span class="bt">Piso</span></div>
</div></div>
<div class="toast"><div class="av">✈</div><div><b>🚨 Mensagem do pregoeiro</b><br>“Convoco a empresa para enviar a proposta ajustada em até 2 horas.”</div></div></div>
</div></div>

<section><div class="wrap"><div class="titulo"><h2>Licitação não perdoa descuido</h2><p>Quem disputa vários pregões ao mesmo tempo conhece estas dores.</p></div>
<div class="dores"><div class="dor"><b>😰 O pregoeiro chamou e ninguém viu</b><p>Convocação com prazo de 2 horas enquanto você estava em outra sala. Inabilitado por silêncio.</p></div>
<div class="dor"><b>📅 O prazo venceu no meio da correria</b><p>Recurso, contrarrazões, certidão vencida na véspera… cada processo com uma data diferente.</p></div>
<div class="dor"><b>💸 Ganhou, mas no prejuízo</b><p>Lance no impulso sem saber o custo real com impostos e frete – ou perdeu por centavos sem saber por quê.</p></div></div>
</div></section>

<section id="recursos" style="background:#fff;border-top:1px solid var(--borda);border-bottom:1px solid var(--borda)"><div class="wrap">
<div class="titulo"><h2>Tudo o que um licitante precisa, num lugar só</h2><p>Do edital ao pagamento – com o sistema avisando o que fazer e quando.</p></div>
<div class="recs">{recursos}</div></div></section>

<section id="como"><div class="wrap"><div class="titulo"><h2>Comece hoje, em 3 passos</h2><p>Sem instalação. Funciona no computador e no celular.</p></div>
<div class="passos"><div class="passo"><b>Crie a sua conta grátis</b><p>{dias} dias para usar tudo, sem cartão. Leva 1 minuto.</p></div>
<div class="passo"><b>Cadastre a empresa e ligue o Telegram</b><p>O assistente mostra cada passo. O monitor começa a ler as plataformas na hora.</p></div>
<div class="passo"><b>Siga o “Hoje”</b><p>Todo dia, a lista do que precisa de ação: disputas, prazos, certidões, propostas e pagamentos.</p></div></div>
<p style="text-align:center;margin-top:30px">{cta}</p></div></section>

<section class="faixa"><div class="wrap"><div class="titulo"><h2>Seguro e transparente</h2><p>Feito para quem não pode errar.</p></div>
<div class="seg"><div><b>🔒 Só leitura nas plataformas</b><p>O sistema nunca envia mensagem, lance ou documento no seu lugar. Você mantém o controle.</p></div>
<div><b>🧱 Seus dados só seus</b><p>Cada conta vê apenas as próprias empresas. Acesso por HTTPS e registro de todas as ações.</p></div>
<div><b>⚖️ Baseado na Lei 14.133</b><p>Prazos de impugnação, esclarecimento e recurso em dias úteis, com os artigos citados.</p></div></div></div></section>

<section id="faq"><div class="wrap"><div class="titulo"><h2>Dúvidas frequentes</h2></div><div class="faq">{faq}</div></div></section>

<section class="final"><div class="wrap"><h2>Teste {dias} dias grátis. Sem cartão.</h2>
<p>Configure em 10 minutos e veja amanhã cedo, no Telegram, o resumo do seu dia de licitações.</p>{cta}</div></section>

<footer><div class="wrap"><span>© Licitatudo</span><span><a href="/login">Entrar</a>{(' · <a href="' + e(wa) + '" target="_blank" rel="noopener">WhatsApp</a>') if wa else ""}</span></div></footer>
{f'<a class="wa" href="{e(wa)}" target="_blank" rel="noopener">💬 Dúvidas? WhatsApp</a>' if wa else ""}
</body></html>"""


def pagina_cadastro(dias, token, erro="", campos=None, whatsapp=""):
    c = campos or {}
    v = lambda k: e(c.get(k, ""))  # noqa: E731
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teste grátis – Licitatudo</title><meta name="theme-color" content="#173f2c"><link rel="icon" href="/icone-192.png">
<style>*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f7faf8;color:#14211a;
display:grid;place-items:center;min-height:100vh;padding:24px}}.cx{{width:100%;max-width:440px;background:#fff;border:1px solid #dde6e0;border-radius:18px;padding:28px;
box-shadow:0 24px 50px rgba(20,33,26,.1)}}a{{color:#1f5c3f}}h1{{font-size:24px;margin:0 0 6px}}p.s{{color:#55645b;margin:0 0 18px;font-size:14.5px}}
label{{display:block;font-weight:600;font-size:13px;margin:12px 0 5px}}input[type=text],input[type=password],input[type=tel]{{width:100%;font:inherit;padding:11px 12px;border:1.5px solid #dde6e0;
border-radius:10px;outline:none}}input:focus{{border-color:#2f8a5b;box-shadow:0 0 0 4px rgba(47,138,91,.15)}}small{{color:#55645b}}
.ck{{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:13px;margin-top:14px}}button{{width:100%;margin-top:18px;font:inherit;font-weight:700;color:#fff;border:0;
border-radius:12px;padding:14px;cursor:pointer;background:linear-gradient(135deg,#2f8a5b,#1f5c3f)}}.erro{{background:#fde8e6;color:#b3261e;border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:8px}}
.hp{{position:absolute;left:-9999px}}.duas{{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}}</style></head><body><div class="cx">
<p style="margin:0 0 14px"><a href="/">← Licitatudo</a></p><h1>Teste grátis por {dias} dias</h1>
<p class="s">Sem cartão de crédito. Você já entra no painel e o assistente mostra cada passo.</p>
{f'<div class="erro">{e(erro)}</div>' if erro else ""}
<form method="post" action="/teste-gratis" autocomplete="on"><input type="hidden" name="t" value="{e(token)}">
<input class="hp" name="site" tabindex="-1" autocomplete="off" aria-hidden="true">
<label>Seu nome</label><input type="text" name="nome" value="{v("nome")}" required maxlength="80" autocomplete="name">
<label>Empresa</label><input type="text" name="empresa" value="{v("empresa")}" maxlength="120" autocomplete="organization">
<label>WhatsApp <small>(para te ajudarmos na configuração)</small></label><input type="tel" name="whatsapp" value="{v("whatsapp")}" required maxlength="20" placeholder="(75) 99999-9999" autocomplete="tel">
<label>Escolha um login <small>(letras minúsculas, números, ponto ou hífen)</small></label><input type="text" name="login" value="{v("login")}" required minlength="3" maxlength="32" autocomplete="username" pattern="[a-z0-9._-]+">
<div class="duas"><div><label>Senha</label><input type="password" name="senha" required minlength="8" autocomplete="new-password"></div>
<div><label>Repita</label><input type="password" name="senha2" required minlength="8" autocomplete="new-password"></div></div>
<small>Mínimo de 8 caracteres, com letras e números.</small>
<label class="ck"><input type="checkbox" name="aceite" value="1" required> Concordo em usar o teste para avaliar o sistema e que os dados que eu cadastrar ficam guardados na minha conta.</label>
<button>Criar minha conta grátis</button></form>
<p class="s" style="margin-top:14px;text-align:center">Já tem conta? <a href="/login">Entrar</a></p></div></body></html>"""
