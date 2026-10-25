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
    ("Sou pequena empresa (ME/EPP) e estou começando. Serve para mim?",
     "Serve. O assistente guia cada passo, as dicas explicam os termos técnicos e o siga-me mostra o que fazer em cada licitação – inclusive os direitos de ME/EPP, como o empate ficto."),
    ("Por que não usar só os portais gratuitos?",
     "Os portais mostram cada pregão separado e não avisam no celular. O Licitatudo junta todos, lê o chat por você, calcula prazos, monta documentos e mostra o que fazer hoje."),
    ("E depois dos {dias} dias?", "Seus dados continuam guardados. É só falar com a gente para escolher o plano e seguir de onde parou."),
]


def pagina(dias=7, whatsapp="", cadastro_aberto=True):
    wa = ("https://wa.me/" + "".join(c for c in whatsapp if c.isdigit()) + "?text=" +
          urllib.parse.quote("Olá! Vim pelo site do Licitatudo e quero saber mais.")) if whatsapp else ""
    if cadastro_aberto:
        cta = f'<a class="cta" href="/teste-gratis">Testar {dias} dias grátis <span aria-hidden="true">→</span></a>'
        cta_topo = '<a class="cta cta-p" href="/teste-gratis">Testar grátis</a>'
    else:
        cta = (f'<a class="cta" href="{e(wa)}" target="_blank" rel="noopener">Falar com a gente →</a>' if wa else "")
        cta_topo = cta
    cta_wa = (f'<a class="cta-sec" href="{e(wa)}" target="_blank" rel="noopener">💬 Tirar dúvidas no WhatsApp</a>' if wa else
              '<a class="cta-sec" href="#como">Ver como funciona</a>')
    bento = "".join(f'<article class="b {cl}">{ic(i)}<h3>{e(t)}</h3><p>{e(d)}</p>{extra}</article>' for cl, i, t, d, extra in [
        ("b-grande", "sino", "O pregoeiro chamou? Seu celular toca.",
         "O monitor lê o chat de todos os seus pregões e, quando é com você – convocação, diligência, prazo curto –, manda no Telegram e toca alto no celular, mesmo no silencioso.",
         '<div class="notif"><b>🚨 Mensagem do pregoeiro · Pregão 45/2026</b><span>“Convoco a empresa para enviar a proposta ajustada em até 2 horas.”</span></div>'),
        ("", "quadro", "Siga-me de cada licitação",
         "Um quadro com as etapas e o passo a passo do que falta: edital, documentos, proposta, envio, resultado e contrato.",
         '<div class="mini-k"><i style="width:86%"></i></div><small class="mk">86% pronto · falta: pacote</small>'),
        ("", "balanca", "Prazos da lei, calculados",
         "Impugnação, esclarecimento, recurso e contrarrazões em dias úteis (Lei 14.133), com aviso 24 h e 2 h antes.", ""),
        ("", "calc", "Preço que dá lucro",
         "Formação de preço com impostos, frete e margem. Você sabe o lance mínimo antes da disputa começar.",
         '<div class="preco"><span>Lance mínimo</span><b>R$ 13,30</b></div>'),
        ("", "pasta", "Habilitação em um PDF",
         "Declarações com timbre, certidões em dia, atestados que atendem o edital – tudo num pacote com sumário.", ""),
        ("b-larga", "grafico", "Ganhou? Perdeu? Você sabe por quê.",
         "“Por que perdi?” lote a lote, ficha do órgão (paga em dia?), proposta readequada ao lance com um clique, contratos, atas e o lucro real.",
         '<div class="barras"><i style="height:40%"></i><i style="height:65%"></i><i style="height:50%"></i><i style="height:85%"></i><i style="height:70%"></i><i style="height:95%"></i></div>'),
    ])
    faq = "".join(f'<details><summary>{e(p.format(dias=dias))}<span class="mais">+</span></summary><p>{e(r.format(dias=dias))}</p></details>' for p, r in FAQ)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Licitatudo – sua licitação no piloto automático</title>
<meta name="description" content="Monitor do pregoeiro 24 h com alarme no celular, prazos da Lei 14.133, preço com lucro e habilitação em um PDF. Teste grátis por {dias} dias, sem cartão.">
<meta name="theme-color" content="#07160f"><link rel="icon" href="/icone-192.png"><link rel="manifest" href="/manifest.webmanifest">
<meta property="og:title" content="Licitatudo – sua licitação no piloto automático"><meta property="og:description" content="Teste grátis por {dias} dias, sem cartão.">
<style>
:root{{--v:#34c27a;--v2:#1f8f57;--esc:#07160f;--esc2:#0d2419;--txt:#0f1f17;--sub:#5a6a61;--borda:#e3ebe6;--bg:#f6f9f7}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--txt);background:var(--bg);line-height:1.55;-webkit-font-smoothing:antialiased}}
a{{color:inherit}}.w{{max-width:1160px;margin:auto;padding:0 22px}}
.ic{{width:22px;height:22px}}
/* topo */
.topo{{position:fixed;inset:0 0 auto 0;z-index:40;transition:.25s}}.topo .w{{display:flex;align-items:center;gap:20px;height:68px}}
.topo.rolou{{background:rgba(7,22,15,.82);backdrop-filter:blur(14px);border-bottom:1px solid rgba(255,255,255,.08)}}
.marca{{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;color:#fff;text-decoration:none;letter-spacing:-.2px}}
.marca i{{width:32px;height:32px;border-radius:10px;background:conic-gradient(from 210deg,#7ef0b4,#34c27a,#1f8f57,#7ef0b4);display:grid;place-items:center;font-style:normal;font-size:12.5px;color:#07160f;font-weight:900}}
.topo nav{{margin-left:auto;display:flex;gap:22px;align-items:center;font-size:14px}}.topo nav a{{color:#c6d8cd;text-decoration:none}}.topo nav a:hover{{color:#fff}}
.topo nav .entrar{{color:#fff;border:1px solid rgba(255,255,255,.22);padding:8px 14px;border-radius:10px}}
.cta{{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,#5ee39d,#34c27a 45%,#1f8f57);color:#04140c!important;text-decoration:none;font-weight:800;
padding:15px 26px;border-radius:14px;box-shadow:0 0 0 1px rgba(255,255,255,.15) inset,0 14px 34px rgba(52,194,122,.35);transition:.2s;white-space:nowrap}}
.cta:hover{{transform:translateY(-2px);box-shadow:0 0 0 1px rgba(255,255,255,.2) inset,0 20px 40px rgba(52,194,122,.45)}}.cta-p{{padding:9px 15px;font-size:14px;border-radius:11px}}
.cta-sec{{display:inline-flex;align-items:center;gap:8px;color:#e6f2eb;text-decoration:none;font-weight:600;padding:15px 20px;border-radius:14px;border:1px solid rgba(255,255,255,.2)}}
.cta-sec:hover{{background:rgba(255,255,255,.06)}}
/* hero */
.hero{{position:relative;overflow:hidden;background:var(--esc);color:#fff;padding:150px 0 90px}}
.hero:before{{content:"";position:absolute;inset:-20%;background:radial-gradient(600px 400px at 18% 20%,rgba(52,194,122,.35),transparent 60%),
radial-gradient(700px 500px at 85% 30%,rgba(31,143,87,.35),transparent 60%),radial-gradient(500px 300px at 60% 95%,rgba(126,240,180,.18),transparent 60%);filter:blur(10px)}}
.hero:after{{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);
background-size:56px 56px;mask-image:radial-gradient(ellipse at 50% 0,#000 30%,transparent 75%)}}
.hero .w{{position:relative;z-index:1;text-align:center}}
.pilula{{display:inline-flex;gap:8px;align-items:center;border:1px solid rgba(255,255,255,.16);background:rgba(255,255,255,.06);padding:7px 14px;border-radius:999px;font-size:13px;color:#cfe8da}}
.pilula b{{background:var(--v);color:#04140c;border-radius:999px;padding:1px 8px;font-size:11.5px}}
.hero h1{{font-size:clamp(36px,6.2vw,68px);line-height:1.02;letter-spacing:-2px;margin:22px auto 20px;max-width:900px;font-weight:850}}
.grad{{background:linear-gradient(90deg,#7ef0b4,#34c27a 40%,#c3f7da);-webkit-background-clip:text;background-clip:text;color:transparent}}
.hero p.lead{{font-size:clamp(16px,2vw,19px);color:#b9cec2;max-width:680px;margin:0 auto 32px}}
.botoes{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
.mini{{display:flex;gap:22px;justify-content:center;flex-wrap:wrap;margin-top:22px;font-size:13.5px;color:#9fb8aa}}.mini span{{display:flex;gap:6px;align-items:center}}.mini .ic{{width:16px;height:16px;color:var(--v)}}
/* tela */
.tela{{margin:64px auto 0;max-width:980px;border-radius:20px;padding:1px;background:linear-gradient(180deg,rgba(255,255,255,.28),rgba(255,255,255,.04));box-shadow:0 40px 90px rgba(0,0,0,.45)}}
.tela-in{{border-radius:19px;background:#f6f9f7;color:var(--txt);overflow:hidden;text-align:left}}
.tela .bar{{display:flex;align-items:center;gap:16px;background:#0f2a1d;color:#fff;padding:11px 16px;font-size:13px}}.tela .bar b{{font-weight:800}}.tela .bar span{{opacity:.6}}
.tela .bar .pts{{display:flex;gap:6px;margin-right:6px}}.tela .bar .pts i{{width:10px;height:10px;border-radius:50%;background:#ff5f57}}.tela .bar .pts i:nth-child(2){{background:#febc2e}}.tela .bar .pts i:nth-child(3){{background:#28c840}}
.tela .grid{{display:grid;grid-template-columns:1.4fr 1fr;gap:16px;padding:18px}}
.it{{display:flex;gap:10px;align-items:center;background:#fff;border:1px solid var(--borda);border-left:4px solid #e5484d;border-radius:12px;padding:10px 12px;margin-bottom:8px;font-size:13px}}
.it.l{{border-left-color:#f59e0b}}.it.v{{border-left-color:var(--v2)}}.it b{{display:block}}.it small{{color:var(--sub)}}
.it .bt{{margin-left:auto;background:#e8f6ee;color:#14663e;border-radius:8px;padding:4px 10px;font-size:11.5px;font-weight:700;white-space:nowrap}}
.kan{{background:#fff;border:1px solid var(--borda);border-radius:12px;padding:12px;font-size:12.5px}}.kan h5{{margin:0 0 8px;font-size:13px}}
.kc{{border:1px solid var(--borda);border-left:3px solid var(--v2);border-radius:9px;padding:8px;margin-bottom:7px}}.kc b{{display:block}}.kc small{{color:var(--sub)}}
.pb{{height:6px;background:#e3ebe6;border-radius:4px;margin-top:6px;overflow:hidden}}.pb i{{display:block;height:100%;background:linear-gradient(90deg,#1f8f57,#7ef0b4)}}
/* faixa plataformas */
.plats{{background:var(--esc2);color:#a9c2b4;padding:22px 0;border-top:1px solid rgba(255,255,255,.06)}}.plats .w{{display:flex;gap:26px;align-items:center;justify-content:center;flex-wrap:wrap;font-size:14px}}
.plats b{{color:#fff;font-weight:700;opacity:.9}}
section{{padding:96px 0}}.tit{{text-align:center;max-width:760px;margin:0 auto 48px}}.tag{{display:inline-block;color:var(--v2);font-weight:800;font-size:12.5px;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:10px}}
.tit h2{{font-size:clamp(28px,4vw,42px);line-height:1.1;letter-spacing:-1px;margin:0 0 14px}}.tit p{{color:var(--sub);font-size:17px;margin:0}}
/* antes x depois */
.vs{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.vs>div{{border-radius:20px;padding:28px}}
.sem{{background:#fff;border:1px solid var(--borda)}}.com{{background:linear-gradient(160deg,#0d2419,#07160f);color:#e8f4ed;position:relative;overflow:hidden}}
.com:before{{content:"";position:absolute;width:300px;height:300px;right:-80px;top:-80px;background:radial-gradient(circle,rgba(52,194,122,.35),transparent 70%)}}
.vs h3{{margin:0 0 16px;font-size:20px}}.vs ul{{list-style:none;padding:0;margin:0;display:grid;gap:12px}}.vs li{{display:flex;gap:10px;align-items:flex-start;font-size:15px}}
.sem li:before{{content:"✕";color:#e5484d;font-weight:800}}.com li:before{{content:"✓";color:var(--v);font-weight:800}}.com li{{position:relative}}
/* bento */
.bento{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.b{{background:#fff;border:1px solid var(--borda);border-radius:20px;padding:24px;position:relative;overflow:hidden;transition:.25s}}
.b:hover{{transform:translateY(-3px);box-shadow:0 20px 40px rgba(15,31,23,.08)}}.b .ic{{width:40px;height:40px;padding:9px;border-radius:12px;background:#e8f6ee;color:var(--v2)}}
.b h3{{font-size:19px;margin:16px 0 8px;letter-spacing:-.3px}}.b p{{color:var(--sub);margin:0;font-size:14.5px}}.b-grande{{grid-row:span 2;background:linear-gradient(170deg,#ffffff,#eef8f2)}}
.b-larga{{grid-column:1/-1}}.notif{{margin-top:22px;background:#fff;border:1px solid var(--borda);border-radius:14px;padding:12px 14px;font-size:13px;box-shadow:0 14px 30px rgba(15,31,23,.1);display:grid;gap:4px}}
.notif span{{color:var(--sub)}}.mini-k{{height:8px;background:#e3ebe6;border-radius:6px;margin-top:18px;overflow:hidden}}.mini-k i{{display:block;height:100%;background:linear-gradient(90deg,#1f8f57,#7ef0b4)}}
.mk{{color:var(--sub);font-size:12px}}.preco{{margin-top:16px;display:flex;justify-content:space-between;align-items:center;background:#0d2419;color:#fff;border-radius:12px;padding:12px 14px}}
.preco span{{color:#a9c2b4;font-size:13px}}.preco b{{font-size:20px;color:#7ef0b4}}.barras{{display:flex;gap:8px;align-items:flex-end;height:70px;margin-top:16px}}
.barras i{{flex:1;background:linear-gradient(180deg,#7ef0b4,#1f8f57);border-radius:6px 6px 2px 2px}}
/* passos */
.passos{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;counter-reset:p}}.p{{background:#fff;border:1px solid var(--borda);border-radius:18px;padding:22px}}
.p:before{{counter-increment:p;content:"0" counter(p);display:block;font-weight:900;font-size:13px;color:var(--v2);letter-spacing:1px;margin-bottom:10px}}.p b{{display:block;font-size:17px;margin-bottom:6px}}.p span{{color:var(--sub);font-size:14.5px}}
/* calculadora */
.calc{{background:linear-gradient(160deg,#0d2419,#07160f);color:#fff;border-radius:24px;padding:40px;display:grid;grid-template-columns:1fr 1fr;gap:34px;align-items:center;position:relative;overflow:hidden}}
.calc:before{{content:"";position:absolute;inset:auto -100px -140px auto;width:380px;height:380px;background:radial-gradient(circle,rgba(52,194,122,.3),transparent 70%)}}
.calc h2{{font-size:clamp(26px,3.4vw,36px);letter-spacing:-.8px;margin:0 0 10px;line-height:1.1}}.calc p{{color:#b9cec2;margin:0}}
.campo{{margin-bottom:18px;position:relative}}.campo label{{display:flex;justify-content:space-between;font-size:14px;color:#cfe8da;margin-bottom:8px}}.campo label b{{color:#fff}}
input[type=range]{{width:100%;accent-color:#34c27a}}.res{{position:relative;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:18px;margin-top:6px}}
.res b{{font-size:36px;color:#7ef0b4;letter-spacing:-1px}}.res small{{display:block;color:#9fb8aa;margin-top:4px}}
/* segurança */
.seg{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.seg div{{background:#fff;border:1px solid var(--borda);border-radius:18px;padding:24px}}
.seg .ic{{color:var(--v2)}}.seg b{{display:block;font-size:17px;margin:10px 0 6px}}.seg span{{color:var(--sub);font-size:14.5px}}
/* faq */
.faq{{max-width:780px;margin:auto;display:grid;gap:10px}}.faq details{{background:#fff;border:1px solid var(--borda);border-radius:16px;padding:18px 22px}}
.faq summary{{cursor:pointer;font-weight:700;list-style:none;display:flex;justify-content:space-between;gap:12px}}.faq summary::-webkit-details-marker{{display:none}}
.faq .mais{{color:var(--v2);font-size:20px;line-height:1;transition:.2s}}.faq details[open] .mais{{transform:rotate(45deg)}}.faq p{{margin:12px 0 0;color:var(--sub)}}
/* final */
.final{{background:var(--esc);color:#fff;text-align:center;position:relative;overflow:hidden}}.final:before{{content:"";position:absolute;inset:-30%;background:radial-gradient(500px 300px at 50% 30%,rgba(52,194,122,.3),transparent 60%)}}
.final .w{{position:relative}}.final h2{{font-size:clamp(30px,4.6vw,52px);letter-spacing:-1.5px;margin:0 0 14px;line-height:1.05}}.final p{{color:#b9cec2;font-size:18px;margin:0 0 30px}}
footer{{background:var(--esc);color:#7f998b;font-size:13px;padding:26px 0;border-top:1px solid rgba(255,255,255,.07)}}footer .w{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px}}
.wa{{position:fixed;right:18px;bottom:18px;z-index:50;background:#25d366;color:#fff;border-radius:999px;padding:13px 17px;font-weight:700;text-decoration:none;box-shadow:0 12px 28px rgba(0,0,0,.25);font-size:14px}}
.rv{{opacity:0;transform:translateY(18px);transition:opacity .7s ease,transform .7s ease}}.rv.on{{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){{.rv{{opacity:1;transform:none;transition:none}}*{{scroll-behavior:auto}}}}
@media(max-width:980px){{.bento{{grid-template-columns:1fr 1fr}}.b-grande{{grid-row:auto;grid-column:span 2}}.passos{{grid-template-columns:1fr 1fr}}.calc{{grid-template-columns:1fr;padding:28px}}.tela .grid{{grid-template-columns:1fr}}.tela .kan{{display:none}}}}
@media(max-width:680px){{.topo nav a.lnk{{display:none}}.topo nav{{gap:8px}}.topo .w{{padding:0 14px}}.marca{{font-size:17px}}.topo nav .entrar{{padding:8px 11px}}.tela .bar span:not(.pts){{display:none}}.vs,.seg,.bento,.passos{{grid-template-columns:1fr}}.b-grande,.b-larga{{grid-column:auto}}section{{padding:68px 0}}.hero{{padding:120px 0 64px}}.tela{{margin-top:44px}}.hero h1{{letter-spacing:-1px}}}}
</style></head><body>
<header class="topo" id="topo"><div class="w"><a class="marca" href="/conheca"><i>LT</i>Licitatudo</a>
<nav><a class="lnk" href="#recursos">Recursos</a><a class="lnk" href="#como">Como funciona</a><a class="lnk" href="#faq">Dúvidas</a>
<a class="entrar" href="/login">Entrar</a>{cta_topo}</nav></div></header>

<div class="hero"><div class="w">
<span class="pilula"><b>NOVO</b> Alarme do pregoeiro + prazos da Lei 14.133</span>
<h1>Sua licitação no <span class="grad">piloto automático</span>.<br>Do edital ao pagamento.</h1>
<p class="lead">O Licitatudo vigia o chat do pregoeiro 24 horas, toca no seu celular quando é com você, calcula os prazos da lei,
monta a proposta com preço que dá lucro e deixa a habilitação pronta em um PDF.</p>
<div class="botoes">{cta}{cta_wa}</div>
<div class="mini"><span>{ic("check")} {dias} dias grátis</span><span>{ic("check")} Sem cartão de crédito</span><span>{ic("check")} Conta pronta em 1 minuto</span><span>{ic("check")} Cancele quando quiser</span></div>
<div class="tela rv"><div class="tela-in"><div class="bar"><span class="pts"><i></i><i></i><i></i></span><b>Licitatudo</b><span>🏠 Hoje</span><span>🗂️ Licitações</span><span>🎯 Disputa</span><span>📄 Documentos</span></div>
<div class="grid"><div><div style="font-weight:800;margin-bottom:10px">🏠 Hoje – o que precisa de você</div>
<div class="it"><span>🏁</span><div><b>Disputa HOJE às 09:00 – Pregão 45/2026</b><small>Prefeitura Municipal · 86% pronto · falta: pacote</small></div><span class="bt">Abrir</span></div>
<div class="it"><span>⏰</span><div><b>Proposta ajustada em até 2 horas</b><small>Pedido do pregoeiro no chat · Pregão 33/2026</small></div><span class="bt">Ver</span></div>
<div class="it l"><span>📑</span><div><b>CND Federal vence em 3 dias</b><small>Link de emissão e CNPJ prontos para copiar</small></div><span class="bt">Renovar</span></div>
<div class="it v"><span>💰</span><div><b>Lance mínimo do lote 3: R$ 13,30</b><small>Custo + impostos + margem mínima</small></div><span class="bt">Piso</span></div></div>
<div class="kan"><h5>🗂️ Minhas licitações</h5>
<div class="kc"><b>Pregão 45/2026</b><small>📑 Documentos</small><div class="pb"><i style="width:86%"></i></div></div>
<div class="kc"><b>Pregão 52/2026</b><small>🚀 Em disputa · 2º lugar</small><div class="pb"><i style="width:64%"></i></div></div>
<div class="kc" style="border-left-color:#f59e0b"><b>Dispensa 12/2026</b><small>📖 Edital lido</small><div class="pb"><i style="width:30%"></i></div></div></div></div></div></div>
</div></div>
<div class="plats"><div class="w"><span>Acompanha as plataformas</span><b>BLL</b><b>BNC</b><b>Portal de Compras Públicas</b><b>Licitanet</b><span>· busca no</span><b>PNCP</b></div></div>

<section><div class="w"><div class="tit rv"><span class="tag">O problema</span><h2>Licitação não perdoa um minuto de distração</h2>
<p>Quem disputa vários pregões ao mesmo tempo vive com o portal aberto, planilha de prazos e medo de perder a convocação.</p></div>
<div class="vs rv"><div class="sem"><h3>Sem o Licitatudo</h3><ul>
<li>Atualizar o chat de cada pregão o dia inteiro</li><li>Convocação de 2 horas vista tarde demais</li><li>Prazo de recurso contado na mão (e errado)</li>
<li>Certidão vencida descoberta na hora de enviar</li><li>Lance no impulso, sem saber o custo real</li><li>Perder sem entender por quê</li></ul></div>
<div class="com"><h3>Com o Licitatudo</h3><ul>
<li>O monitor lê todos os chats por você, 24 horas</li><li>Alarme no celular quando o pregoeiro chama</li><li>Prazos da Lei 14.133 calculados e avisados</li>
<li>Certidões com aviso antes de vencer e link de emissão</li><li>Preço e lance mínimo calculados antes da disputa</li><li>“Por que perdi?” lote a lote, com dicas</li></ul></div></div></div></section>

<section id="recursos" style="padding-top:20px"><div class="w"><div class="tit rv"><span class="tag">Recursos</span><h2>Tudo o que o licitante precisa, num lugar só</h2>
<p>Do edital ao pagamento – com o sistema dizendo o que fazer, e quando.</p></div><div class="bento rv">{bento}</div></div></section>

<section id="como" style="background:#fff;border-top:1px solid var(--borda);border-bottom:1px solid var(--borda)"><div class="w"><div class="tit rv"><span class="tag">Como funciona</span>
<h2>Do cadastro ao contrato em 4 passos</h2><p>Sem instalar nada. No computador e no celular.</p></div>
<div class="passos rv"><div class="p"><b>Encontre</b><span>Busca no PNCP e alerta das licitações do seu perfil, com análise do edital por IA.</span></div>
<div class="p"><b>Prepare</b><span>Proposta com preço certo, declarações com timbre, certidões e atestados num PDF só.</span></div>
<div class="p"><b>Dispute</b><span>Monitor do chat com alarme, classificação ao vivo e o seu piso de lance na tela.</span></div>
<div class="p"><b>Receba</b><span>Contratos, entregas, notas e atas sob controle – com aviso de pagamento atrasado.</span></div></div></div></section>

<section><div class="w"><div class="calc rv"><div><span class="tag" style="color:#7ef0b4">Faça a conta</span><h2>Quanto tempo você gasta só vigiando o pregoeiro?</h2>
<p>Ajuste com a sua rotina. Esse é o tempo que o monitor do Licitatudo assume por você – para você usar vendendo mais.</p></div>
<div><div class="campo"><label>Pregões acompanhados por mês <b id="v1">10</b></label><input type="range" id="r1" min="1" max="60" value="10"></div>
<div class="campo"><label>Minutos por dia olhando o chat de cada um <b id="v2">20</b></label><input type="range" id="r2" min="5" max="90" step="5" value="20"></div>
<div class="campo"><label>Dias de sessão/acompanhamento por pregão <b id="v3">5</b></label><input type="range" id="r3" min="1" max="20" value="5"></div>
<div class="res"><b id="horas">17 h</b><small>por mês de tela de portal que o monitor faz por você</small></div></div></div></div></section>

<section style="padding-top:0"><div class="w"><div class="tit rv"><span class="tag">Segurança</span><h2>Feito para quem não pode errar</h2></div>
<div class="seg rv"><div>{ic("escudo")}<b>Só leitura nas plataformas</b><span>O sistema nunca envia mensagem, lance ou documento no seu lugar. O controle é sempre seu.</span></div>
<div>{ic("escudo")}<b>Seus dados só seus</b><span>Cada conta vê apenas as próprias empresas. Acesso por HTTPS, senhas criptografadas e registro de cada ação.</span></div>
<div>{ic("balanca")}<b>Na régua da Lei 14.133</b><span>Prazos de impugnação, esclarecimento e recurso em dias úteis, com os artigos citados.</span></div></div></div></section>

<section id="faq" style="padding-top:0"><div class="w"><div class="tit rv"><span class="tag">Dúvidas</span><h2>Perguntas frequentes</h2></div><div class="faq">{faq}</div></div></section>

<section class="final"><div class="w"><h2>Teste {dias} dias grátis.<br><span class="grad">Sem cartão. Sem compromisso.</span></h2>
<p>Crie a conta em 1 minuto e amanhã cedo receba no Telegram o resumo do seu dia de licitações.</p><div class="botoes">{cta}{cta_wa}</div></div></section>
<footer><div class="w"><span>© Licitatudo · licitação no piloto automático</span><span><a href="/login" style="text-decoration:none">Entrar</a></span></div></footer>
{f'<a class="wa" href="{e(wa)}" target="_blank" rel="noopener">💬 WhatsApp</a>' if wa else ""}
<script>
(function(){{const t=document.getElementById('topo');const f=()=>t.classList.toggle('rolou',scrollY>20);f();addEventListener('scroll',f,{{passive:true}});
const io=new IntersectionObserver(es=>es.forEach(x=>{{if(x.isIntersecting){{x.target.classList.add('on');io.unobserve(x.target);}}}}),{{threshold:.12}});
document.querySelectorAll('.rv').forEach(el=>io.observe(el));
const r=[1,2,3].map(i=>document.getElementById('r'+i)),v=[1,2,3].map(i=>document.getElementById('v'+i)),hs=document.getElementById('horas');
function calc(){{r.forEach((x,i)=>v[i].textContent=x.value);const h=r[0].value*r[1].value*r[2].value/60;hs.textContent=(h<10?h.toFixed(1).replace('.',','):Math.round(h))+' h';}}
r.forEach(x=>x.addEventListener('input',calc));calc();}})();
</script>
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
