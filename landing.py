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
     "Não. O Expert Licitante só lê as plataformas e prepara arquivos e rascunhos. Quem envia, assina e dá lance é sempre você – com tudo pronto e no prazo."),
    ("Preciso de cartão de crédito para testar?", "Não. O teste é grátis por {dias} dias e não pede cartão. No fim, você decide se quer continuar."),
    ("Quais plataformas ele acompanha?",
     "BLL, BNC, Portal de Compras Públicas e Licitanet para o monitor; PNCP para buscar editais e oportunidades. Você cadastra o login da sua empresa e ele começa a ler."),
    ("Meus dados ficam seguros?",
     "Cada conta vê só as próprias empresas – os dados não se cruzam entre clientes. O acesso é por HTTPS, as senhas do painel são guardadas com criptografia e toda ação fica registrada."),
    ("Funciona no celular?", "Sim. Os alertas chegam no Telegram e o painel pode ser instalado na tela inicial do celular como um aplicativo."),
    ("Sou pequena empresa (ME/EPP) e estou começando. Serve para mim?",
     "Serve. O assistente guia cada passo, as dicas explicam os termos técnicos e o siga-me mostra o que fazer em cada licitação – inclusive os direitos de ME/EPP, como o empate ficto."),
    ("Por que não usar só os portais gratuitos?",
     "Os portais mostram cada pregão separado e não avisam no celular. O Expert Licitante junta todos, lê o chat por você, calcula prazos, monta documentos e mostra o que fazer hoje."),
    ("Posso fazer o teste mais de uma vez?",
     "Não. O teste grátis é um por empresa: o mesmo CNPJ ou o mesmo login de plataforma não faz um segundo teste, mesmo em outra conta."),
    ("E depois dos {dias} dias?", "Seus dados continuam guardados. É só falar com a gente para escolher o plano e seguir de onde parou."),
]


def pagina(dias=7, whatsapp="", cadastro_aberto=True):
    wa = ("https://wa.me/" + "".join(c for c in whatsapp if c.isdigit()) + "?text=" +
          urllib.parse.quote("Olá! Vim pelo site do Expert Licitante e quero saber mais.")) if whatsapp else ""
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
    telas = [("tela-hoje", "🏠 Hoje", 1360, 458, "A tela Hoje junta, por urgência, tudo o que precisa de você: prazos, disputas, tarefas e certidões."),
             ("tela-siga-me", "🧭 Siga-me", 1360, 860, "Cada licitação com o passo a passo do que falta – do edital ao contrato."),
             ("tela-preco", "🧮 Formação de preço", 1360, 645, "Custo, impostos e margem viram o preço da proposta e o lance mínimo de cada item.")]
    abas_telas = "".join(f'<button type="button" role="tab" data-t="{i}" aria-selected="{str(i == 0).lower()}">{e(r)}</button>'
                         for i, (_, r, _, _, _) in enumerate(telas))
    figs_telas = "".join(f'<figure class="{"on" if i == 0 else ""}" data-t="{i}"><div class="moldura"><img src="/site/{n}.webp" width="{w}" height="{hh}" '
                         f'loading="lazy" alt="{e(leg)}"></div><figcaption>{e(leg)}</figcaption></figure>'
                         for i, (n, _, w, hh, leg) in enumerate(telas))
    faq = "".join(f'<details><summary>{e(p.format(dias=dias))}<span class="mais">+</span></summary><p>{e(r.format(dias=dias))}</p></details>' for p, r in FAQ)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Expert Licitante – sua licitação no piloto automático</title>
<meta name="description" content="Monitor do pregoeiro 24 h com alarme no celular, prazos da Lei 14.133, preço com lucro e habilitação em um PDF. Teste grátis por {dias} dias, sem cartão.">
<meta name="theme-color" content="#07160f"><link rel="icon" href="/site/favicon.svg" type="image/svg+xml"><link rel="manifest" href="/manifest.webmanifest">
<meta property="og:title" content="Expert Licitante – sua licitação no piloto automático"><meta property="og:image" content="https://expertlicitante.com.br/site/compartilhar.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image"><meta property="og:description" content="Teste grátis por {dias} dias, sem cartão.">
<style>
:root{{--v:#34c27a;--v2:#1f8f57;--esc:#07160f;--esc2:#0d2419;--txt:#0f1f17;--sub:#5a6a61;--borda:#e3ebe6;--bg:#f6f9f7}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--txt);background:var(--bg);line-height:1.55;-webkit-font-smoothing:antialiased}}
a{{color:inherit}}.w{{max-width:1160px;margin:auto;padding:0 22px}}
.ic{{width:22px;height:22px}}
/* topo */
.topo{{position:fixed;inset:0 0 auto 0;z-index:40;transition:.25s}}.topo .w{{display:flex;align-items:center;gap:20px;height:68px}}
.topo.rolou{{background:rgba(7,22,15,.82);backdrop-filter:blur(14px);border-bottom:1px solid rgba(255,255,255,.08)}}
.marca{{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;color:#fff;text-decoration:none;letter-spacing:-.2px}}
.marca i{{width:26px;height:42px;background:url(/site/escudo.svg) center/contain no-repeat}}
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
/* telas */
.abas{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:18px}}.abas button{{font:inherit;font-weight:700;font-size:14px;border:1px solid var(--borda);background:#fff;color:var(--txt);padding:10px 16px;border-radius:999px;cursor:pointer}}
.abas button[aria-selected=true]{{background:var(--esc2);color:#fff;border-color:var(--esc2)}}
.telas figure{{margin:0;display:none}}.telas figure.on{{display:block}}.telas .moldura{{border-radius:18px;padding:1px;background:linear-gradient(180deg,#cfe3d7,#eef5f1);box-shadow:0 30px 70px rgba(15,31,23,.14)}}
.telas img{{display:block;width:100%;height:auto;border-radius:17px}}.telas figcaption{{text-align:center;color:var(--sub);font-size:14.5px;margin-top:16px}}
.faixa-calc{{display:flex;gap:16px;align-items:center;text-decoration:none;background:#fff;border:1px solid var(--borda);border-radius:18px;padding:18px 22px;transition:.2s}}
.faixa-calc:hover{{border-color:var(--v2);box-shadow:0 14px 30px rgba(15,31,23,.08)}}.faixa-calc>span{{background:#e8f6ee;color:var(--v2);border-radius:12px;padding:10px;display:grid}}
.faixa-calc div{{flex:1}}.faixa-calc b{{display:block;font-size:17px}}.faixa-calc small{{color:var(--sub);font-size:14px}}.faixa-calc em{{font-style:normal;font-weight:800;color:var(--v2);white-space:nowrap}}
@media(max-width:680px){{.faixa-calc{{flex-wrap:wrap}}}}
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
<header class="topo" id="topo"><div class="w"><a class="marca" href="/conheca"><i></i><img src="/site/nome.svg" height="17" alt="Expert Licitante"></a>
<nav><a class="lnk" href="#recursos">Recursos</a><a class="lnk" href="#como">Como funciona</a><a class="lnk" href="#telas">Telas</a><a class="lnk" href="/prazos">Calculadora de prazos</a>
<a class="entrar" href="/login">Entrar</a>{cta_topo}</nav></div></header>

<div class="hero"><div class="w">
<span class="pilula"><b>NOVO</b> Alarme do pregoeiro + prazos da Lei 14.133</span>
<h1>Sua licitação no <span class="grad">piloto automático</span>.<br>Do edital ao pagamento.</h1>
<p class="lead">O Expert Licitante vigia o chat do pregoeiro 24 horas, toca no seu celular quando é com você, calcula os prazos da lei,
monta a proposta com preço que dá lucro e deixa a habilitação pronta em um PDF.</p>
<div class="botoes">{cta}{cta_wa}</div>
<div class="mini"><span>{ic("check")} {dias} dias grátis</span><span>{ic("check")} Sem cartão de crédito</span><span>{ic("check")} Conta pronta em 1 minuto</span><span>{ic("check")} Cancele quando quiser</span></div>
<div class="tela rv"><div class="tela-in"><div class="bar"><span class="pts"><i></i><i></i><i></i></span><b><img src="/site/escudo.svg" height="16" alt="" style="vertical-align:-3px;margin-right:6px">Expert Licitante</b><span>🏠 Hoje</span><span>🗂️ Licitações</span><span>🎯 Disputa</span><span>📄 Documentos</span></div>
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
<div class="vs rv"><div class="sem"><h3>Sem o Expert Licitante</h3><ul>
<li>Atualizar o chat de cada pregão o dia inteiro</li><li>Convocação de 2 horas vista tarde demais</li><li>Prazo de recurso contado na mão (e errado)</li>
<li>Certidão vencida descoberta na hora de enviar</li><li>Lance no impulso, sem saber o custo real</li><li>Perder sem entender por quê</li></ul></div>
<div class="com"><h3>Com o Expert Licitante</h3><ul>
<li>O monitor lê todos os chats por você, 24 horas</li><li>Alarme no celular quando o pregoeiro chama</li><li>Prazos da Lei 14.133 calculados e avisados</li>
<li>Certidões com aviso antes de vencer e link de emissão</li><li>Preço e lance mínimo calculados antes da disputa</li><li>“Por que perdi?” lote a lote, com dicas</li></ul></div></div></div></section>

<section id="recursos" style="padding-top:20px"><div class="w"><div class="tit rv"><span class="tag">Recursos</span><h2>Tudo o que o licitante precisa, num lugar só</h2>
<p>Do edital ao pagamento – com o sistema dizendo o que fazer, e quando.</p></div><div class="bento rv">{bento}</div></div></section>

<section id="telas" style="padding-top:0"><div class="w"><div class="tit rv"><span class="tag">Veja o sistema</span><h2>Telas reais, sem maquiagem</h2>
<p>É assim que o Expert Licitante aparece para você – aqui com uma empresa e órgãos de exemplo.</p></div>
<div class="abas rv" role="tablist">{abas_telas}</div><div class="telas rv">{figs_telas}</div></div></section>

<section id="como" style="background:#fff;border-top:1px solid var(--borda);border-bottom:1px solid var(--borda)"><div class="w"><div class="tit rv"><span class="tag">Como funciona</span>
<h2>Do cadastro ao contrato em 4 passos</h2><p>Sem instalar nada. No computador e no celular.</p></div>
<div class="passos rv"><div class="p"><b>Encontre</b><span>Busca no PNCP e alerta das licitações do seu perfil, com análise do edital por IA.</span></div>
<div class="p"><b>Prepare</b><span>Proposta com preço certo, declarações com timbre, certidões e atestados num PDF só.</span></div>
<div class="p"><b>Dispute</b><span>Monitor do chat com alarme, classificação ao vivo e o seu piso de lance na tela.</span></div>
<div class="p"><b>Receba</b><span>Contratos, entregas, notas e atas sob controle – com aviso de pagamento atrasado.</span></div></div></div></section>

<section><div class="w"><div class="calc rv"><div><span class="tag" style="color:#7ef0b4">Faça a conta</span><h2>Quanto tempo você gasta só vigiando o pregoeiro?</h2>
<p>Ajuste com a sua rotina. Esse é o tempo que o monitor do Expert Licitante assume por você – para você usar vendendo mais.</p></div>
<div><div class="campo"><label>Pregões acompanhados por mês <b id="v1">10</b></label><input type="range" id="r1" min="1" max="60" value="10"></div>
<div class="campo"><label>Minutos por dia olhando o chat de cada um <b id="v2">20</b></label><input type="range" id="r2" min="5" max="90" step="5" value="20"></div>
<div class="campo"><label>Dias de sessão/acompanhamento por pregão <b id="v3">5</b></label><input type="range" id="r3" min="1" max="20" value="5"></div>
<div class="res"><b id="horas">17 h</b><small>por mês de tela de portal que o monitor faz por você</small></div></div></div></div></section>

<section style="padding:0 0 70px"><div class="w"><a class="faixa-calc rv" href="/prazos"><span>{ic("balanca")}</span><div><b>Ferramenta grátis: calculadora de prazos da Lei 14.133</b>
<small>Impugnação, esclarecimento, recurso, contrarrazões, ME/EPP e vigência de ata – em dias úteis, sem cadastro.</small></div><em>Calcular agora →</em></a></div></section>

<section style="padding-top:0"><div class="w"><div class="tit rv"><span class="tag">Segurança</span><h2>Feito para quem não pode errar</h2></div>
<div class="seg rv"><div>{ic("escudo")}<b>Só leitura nas plataformas</b><span>O sistema nunca envia mensagem, lance ou documento no seu lugar. O controle é sempre seu.</span></div>
<div>{ic("escudo")}<b>Seus dados só seus</b><span>Cada conta vê apenas as próprias empresas. Acesso por HTTPS, senhas criptografadas e registro de cada ação.</span></div>
<div>{ic("balanca")}<b>Na régua da Lei 14.133</b><span>Prazos de impugnação, esclarecimento e recurso em dias úteis, com os artigos citados.</span></div></div></div></section>

<section id="faq" style="padding-top:0"><div class="w"><div class="tit rv"><span class="tag">Dúvidas</span><h2>Perguntas frequentes</h2></div><div class="faq">{faq}</div></div></section>

<section class="final"><div class="w"><img src="/site/brasao.svg" width="120" height="130" alt="Brasão Expert Licitante" style="display:block;margin:0 auto 18px"><h2>Teste {dias} dias grátis.<br><span class="grad">Sem cartão. Sem compromisso.</span></h2>
<p>Crie a conta em 1 minuto e amanhã cedo receba no Telegram o resumo do seu dia de licitações.</p><div class="botoes">{cta}{cta_wa}</div></div></section>
<footer><div class="w"><span><img src="/site/escudo.svg" height="22" alt="" style="vertical-align:middle;margin-right:8px">© Expert Licitante · licitação no piloto automático</span><span><a href="/prazos" style="text-decoration:none">Calculadora de prazos</a> · <a href="/login" style="text-decoration:none">Entrar</a></span></div></footer>
{f'<a class="wa" href="{e(wa)}" target="_blank" rel="noopener">💬 WhatsApp</a>' if wa else ""}
<script>
(function(){{const t=document.getElementById('topo');const f=()=>t.classList.toggle('rolou',scrollY>20);f();addEventListener('scroll',f,{{passive:true}});
const io=new IntersectionObserver(es=>es.forEach(x=>{{if(x.isIntersecting){{x.target.classList.add('on');io.unobserve(x.target);}}}}),{{threshold:.12}});
document.querySelectorAll('.rv').forEach(el=>io.observe(el));
const r=[1,2,3].map(i=>document.getElementById('r'+i)),v=[1,2,3].map(i=>document.getElementById('v'+i)),hs=document.getElementById('horas');
function calc(){{r.forEach((x,i)=>v[i].textContent=x.value);const h=r[0].value*r[1].value*r[2].value/60;hs.textContent=(h<10?h.toFixed(1).replace('.',','):Math.round(h))+' h';}}
r.forEach(x=>x.addEventListener('input',calc));calc();
document.querySelectorAll('.abas button').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('.abas button').forEach(x=>x.setAttribute('aria-selected',x===b));
document.querySelectorAll('.telas figure').forEach(f=>f.classList.toggle('on',f.dataset.t===b.dataset.t));}}));}})();
</script>
</body></html>"""


def pagina_cadastro(dias, token, erro="", campos=None, whatsapp=""):
    c = campos or {}
    v = lambda k: e(c.get(k, ""))  # noqa: E731
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teste grátis – Expert Licitante</title><meta name="theme-color" content="#173f2c"><link rel="icon" href="/site/favicon.svg" type="image/svg+xml">
<style>*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f7faf8;color:#14211a;
display:grid;place-items:center;min-height:100vh;padding:24px}}.cx{{width:100%;max-width:440px;background:#fff;border:1px solid #dde6e0;border-radius:18px;padding:28px;
box-shadow:0 24px 50px rgba(20,33,26,.1)}}a{{color:#1f5c3f}}h1{{font-size:24px;margin:0 0 6px}}p.s{{color:#55645b;margin:0 0 18px;font-size:14.5px}}
label{{display:block;font-weight:600;font-size:13px;margin:12px 0 5px}}input[type=text],input[type=password],input[type=tel]{{width:100%;font:inherit;padding:11px 12px;border:1.5px solid #dde6e0;
border-radius:10px;outline:none}}input:focus{{border-color:#2f8a5b;box-shadow:0 0 0 4px rgba(47,138,91,.15)}}small{{color:#55645b}}
.ck{{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:13px;margin-top:14px}}button{{width:100%;margin-top:18px;font:inherit;font-weight:700;color:#fff;border:0;
border-radius:12px;padding:14px;cursor:pointer;background:linear-gradient(135deg,#2f8a5b,#1f5c3f)}}.erro{{background:#fde8e6;color:#b3261e;border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:8px}}
.hp{{position:absolute;left:-9999px}}.duas{{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}}</style></head><body><div class="cx">
<p style="margin:0 0 14px"><a href="/conheca"><img src="/site/logo-horizontal.svg" height="46" alt="Expert Licitante"></a></p><h1>Teste grátis por {dias} dias</h1>
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


# ================================================================== calculadora pública de prazos (Lei 14.133)
TIPOS_55 = [
    ("bens_menor", "Compra de bens – menor preço ou maior desconto", 8, "art. 55, I, a"),
    ("bens_outros", "Compra de bens – demais critérios", 15, "art. 55, I, b"),
    ("serv_comum", "Serviços comuns / obras e serviços comuns de engenharia – menor preço ou maior desconto", 10, "art. 55, II, a"),
    ("serv_especial", "Serviços especiais / obras e serviços especiais de engenharia – menor preço ou maior desconto", 25, "art. 55, II, b"),
    ("integrada", "Obras e serviços – contratação integrada", 60, "art. 55, II, c"),
    ("semi", "Obras e serviços – semi-integrada ou demais casos", 35, "art. 55, II, d"),
    ("maior_lance", "Maior lance", 15, "art. 55, III"),
    ("tecnica", "Técnica e preço, melhor técnica ou conteúdo artístico", 35, "art. 55, IV"),
]
SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _data(txt):
    from datetime import date
    try:
        d = date.fromisoformat((txt or "").strip()[:10])
    except ValueError:
        return None
    return d if 2000 <= d.year <= 2100 else None


def _br(d):
    return f"{SEMANA[d.weekday()]}, {d:%d/%m/%Y}"


def _uteis_entre(a, b, dia_util):
    """Dias úteis depois de a até b (inclusive); negativo se b já passou."""
    from datetime import timedelta
    if b < a:
        return -1
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += dia_util(d)
    return n


def calcular_prazos(q, hoje=None):
    """q = campos do formulário. Devolve {"blocos": [(titulo, [linhas])], "avisos": [...]};
    linha = (rótulo, data, explicação, situação)."""
    import agenda
    from datetime import date, timedelta
    hoje = hoje or date.today()
    du = agenda.dia_util
    menos = lambda d, n: agenda.subtrair_dias_uteis(d, n).date()   # noqa: E731
    mais = lambda d, n: agenda.somar_dias_uteis(d, n).date()       # noqa: E731

    def sit(d):
        if d < hoje:
            return "passou"
        if d == hoje:
            return "hoje"
        n = _uteis_entre(hoje, d, du)
        return f"faltam {n} dia(s) útil(eis)" if n else f"faltam {(d - hoje).days} dia(s) corrido(s)"

    blocos, avisos = [], []
    ab, pub, prot = _data(q.get("abertura")), _data(q.get("publicacao")), _data(q.get("protocolo"))
    res, rec, ata = _data(q.get("resultado")), _data(q.get("recurso")), _data(q.get("ata"))
    if ab:
        ln = []
        if not du(ab):
            avisos.append(f"A abertura informada ({_br(ab)}) não é dia útil pelo calendário nacional – confira a data no edital.")
        lim = menos(ab, 3)
        ln.append(("Último dia para impugnar o edital ou pedir esclarecimento", lim,
                   "Até 3 dias úteis antes da abertura (art. 164). Veja no edital o horário-limite e o canal (sistema ou e-mail).", sit(lim)))
        resp_lim = menos(ab, 1)
        if prot:
            r = min(mais(prot, 3), resp_lim)
            ln.append(("Prazo para a Administração responder o seu pedido", r,
                       "3 dias úteis do protocolo, limitado ao último dia útil antes da abertura (art. 164, parágrafo único).", sit(r)))
            if prot > lim:
                avisos.append("O protocolo informado é depois do último dia para impugnar/esclarecer – o pedido pode ser considerado intempestivo.")
        else:
            ln.append(("Data-limite para a Administração publicar as respostas", resp_lim,
                       "Último dia útil antes da abertura (art. 164, parágrafo único).", sit(resp_lim)))
        ln.append(("Abertura da sessão", ab, "Data informada por você.", sit(ab)))
        tipo = next((t for t in TIPOS_55 if t[0] == q.get("tipo")), None)
        if pub and tipo:
            fim = mais(pub, tipo[2])
            if ab < fim:
                st = "⚠️ prazo menor que o mínimo legal"
                avisos.append(f"Entre a divulgação ({_br(pub)}) e a abertura não cabem os {tipo[2]} dias úteis mínimos ({tipo[3]}). "
                              "Isso pode justificar uma impugnação – confira a data de divulgação no PNCP e as republicações.")
            elif ab == fim:
                st = "no limite – confira a contagem"
            else:
                st = "✅ respeita o prazo mínimo"
            ln.append((f"Fim do prazo mínimo de {tipo[2]} dias úteis para propostas", fim,
                       f"{tipo[1]} ({tipo[3]}), contado da divulgação do edital.", st))
        blocos.append(("Antes da sessão", ln))
    ln = []
    if res:
        r = mais(res, 3)
        ln.append(("Razões do recurso", r, "3 dias úteis da ata/intimação do julgamento ou da habilitação (art. 165, I). "
                   "A intenção de recorrer é manifestada NA SESSÃO, logo depois do resultado (art. 165, §1º).", sit(r)))
        if q.get("me"):
            r5 = mais(res, 5)
            ln.append(("Regularização fiscal/trabalhista de ME/EPP", r5,
                       "5 dias úteis, prorrogáveis por mais 5, a partir da declaração de vencedor (LC 123/2006, art. 43, §1º).", sit(r5)))
    if rec:
        c = mais(rec, 3)
        ln.append(("Contrarrazões", c, "3 dias úteis da intimação/divulgação do recurso (art. 165, §4º).", sit(c)))
        recon = mais(c, 3)
        ln.append(("Reconsideração pela autoridade que praticou o ato (estimativa)", recon,
                   "Até 3 dias úteis depois das contrarrazões; se não reconsiderar, a autoridade superior decide em até 10 dias úteis (art. 165, §2º).", sit(recon)))
    if ln:
        blocos.append(("Depois do resultado", ln))
    if ata:
        try:
            fim = ata.replace(year=ata.year + 1)
        except ValueError:
            fim = ata + timedelta(days=365)
        blocos.append(("Ata de registro de preços", [
            ("Fim da vigência da ata", fim, "1 ano, prorrogável por mais 1 se o preço continuar vantajoso (art. 84). "
             "Confira a data exata de início no texto da ata.", sit(fim))]))
    return {"blocos": blocos, "avisos": avisos}


CSS_PUB = """
:root{--v:#34c27a;--v2:#1f8f57;--esc:#07160f;--esc2:#0d2419;--txt:#0f1f17;--sub:#5a6a61;--borda:#e3ebe6;--bg:#f6f9f7}
*{box-sizing:border-box}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--txt);background:var(--bg);line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}.w{max-width:1000px;margin:auto;padding:0 22px}.ic{width:22px;height:22px}
.topo{background:var(--esc)}.topo .w{display:flex;align-items:center;gap:16px;height:66px;max-width:1160px}
.marca{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;color:#fff;text-decoration:none}
.marca i{width:25px;height:40px;background:url(/site/escudo.svg) center/contain no-repeat}
.topo nav{margin-left:auto;display:flex;gap:10px;align-items:center;font-size:14px}.topo nav a{color:#c6d8cd;text-decoration:none}
.topo nav .entrar{color:#fff;border:1px solid rgba(255,255,255,.22);padding:8px 13px;border-radius:10px}
.cta{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,#5ee39d,#34c27a 45%,#1f8f57);color:#04140c!important;text-decoration:none;font-weight:800;padding:14px 24px;border-radius:14px;box-shadow:0 12px 30px rgba(52,194,122,.3);border:0;font-size:16px;cursor:pointer;font-family:inherit}
.cta-p{padding:9px 14px;font-size:14px;border-radius:11px}
.hero{background:var(--esc);color:#fff;padding:46px 0 120px;position:relative;overflow:hidden}
.hero:before{content:"";position:absolute;inset:-30%;background:radial-gradient(520px 340px at 20% 30%,rgba(52,194,122,.32),transparent 60%),radial-gradient(600px 400px at 85% 20%,rgba(31,143,87,.3),transparent 60%)}
.hero .w{position:relative}.hero h1{font-size:clamp(30px,5vw,48px);line-height:1.05;letter-spacing:-1.4px;margin:14px 0 12px}
.hero p{color:#b9cec2;font-size:17px;max-width:680px;margin:0}.tag{display:inline-block;color:#7ef0b4;font-weight:800;font-size:12.5px;letter-spacing:1.2px;text-transform:uppercase}
.caixa{background:#fff;border:1px solid var(--borda);border-radius:20px;padding:26px;box-shadow:0 30px 60px rgba(15,31,23,.08)}
.sobe{margin-top:-90px;position:relative}
fieldset{border:0;padding:0;margin:0 0 22px}legend{font-weight:800;font-size:17px;margin-bottom:12px;padding:0}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
label{display:block;font-size:13.5px;font-weight:600;color:#33453b}label small{display:block;font-weight:400;color:var(--sub)}
input,select{width:100%;margin-top:6px;padding:11px 12px;border:1px solid #cfdcd4;border-radius:11px;font:inherit;font-size:15px;background:#fff;color:var(--txt)}
input:focus,select:focus{outline:2px solid #9fe3bf;border-color:var(--v2)}
.chk{display:flex;gap:10px;align-items:center;font-weight:500;margin-top:12px}.chk input{width:auto;margin:0}
.res{margin-top:26px}.res h3{font-size:15px;text-transform:uppercase;letter-spacing:1px;color:var(--v2);margin:22px 0 10px}
.ln{display:grid;grid-template-columns:1fr auto;gap:6px 18px;border:1px solid var(--borda);border-left:4px solid var(--v2);border-radius:14px;padding:14px 16px;margin-bottom:10px;background:#fff}
.ln b{font-size:15.5px}.ln .dt{font-weight:800;font-size:18px;text-align:right;white-space:nowrap}.ln p{margin:0;color:var(--sub);font-size:13.5px}
.ln .st{font-size:12.5px;font-weight:700;text-align:right;color:var(--v2);white-space:nowrap}.ln.passou{border-left-color:#c9d3cd;opacity:.75}.ln.passou .st{color:#8a9890}
.ln.hoje{border-left-color:#e5484d}.ln.hoje .st{color:#e5484d}.ln.alerta{border-left-color:#f59e0b}.ln.alerta .st{color:#b45309}
.aviso{background:#fff7e6;border:1px solid #f6d58e;color:#7a4b00;border-radius:12px;padding:12px 14px;margin:10px 0;font-size:14px}
.vende{margin:30px 0;background:linear-gradient(160deg,#0d2419,#07160f);color:#fff;border-radius:22px;padding:30px;display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.vende h2{margin:0 0 6px;font-size:24px;letter-spacing:-.5px}.vende p{margin:0;color:#b9cec2}
.nota{color:var(--sub);font-size:13px;margin:18px 0 50px}
footer{background:var(--esc);color:#7f998b;font-size:13px;padding:22px 0}footer .w{display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;max-width:1160px}
@media(max-width:600px){.ln{grid-template-columns:1fr}.ln .dt,.ln .st{text-align:left}.topo nav .lnk{display:none}.caixa{padding:18px}}
"""


def pagina_prazos(q=None, cadastro_aberto=True, dias=7):
    q = q or {}
    calc = calcular_prazos(q) if any(q.get(k) for k in ("abertura", "resultado", "recurso", "ata")) else None
    val = lambda k: e(str(q.get(k, ""))[:10])   # noqa: E731
    opts = "".join(f'<option value="{k}"{" selected" if q.get("tipo") == k else ""}>{e(t)} – {n} dias úteis</option>'
                   for k, t, n, _ in TIPOS_55)
    cta = (f'<a class="cta" href="/teste-gratis">Testar {dias} dias grátis →</a>' if cadastro_aberto else "")
    cta_topo = ('<a class="cta cta-p" href="/teste-gratis">Testar grátis</a>' if cadastro_aberto else "")
    res = ""
    if calc is not None:
        partes = ["".join(f'<div class="aviso">⚠️ {e(a)}</div>' for a in calc["avisos"])]
        for tit, linhas in calc["blocos"]:
            partes.append(f"<h3>{e(tit)}</h3>")
            for rot, d, expl, st in linhas:
                cl = "passou" if st == "passou" else "hoje" if st == "hoje" else "alerta" if st.startswith("⚠️") else ""
                st_txt = "já passou" if st == "passou" else "é HOJE" if st == "hoje" else st
                fim = " até 23h59" if rot != "Abertura da sessão" else ""
                partes.append(f'<div class="ln {cl}"><b>{e(rot)}</b><span class="dt">{_br(d)}{fim}</span>'
                              f'<p>{e(expl)}</p><span class="st">{e(st_txt)}</span></div>')
        if not calc["blocos"]:
            partes.append('<div class="aviso">Informe pelo menos uma data válida.</div>')
        res = '<div class="res" id="resultado">' + "".join(partes) + "</div>"
    vende = (f'<div class="vende"><div><h2>Pare de contar prazo na mão.</h2><p>No Expert Licitante, cada licitação já nasce com esses prazos na agenda '
             f'e o aviso chega no Telegram e no celular 24 h e 2 h antes.</p></div>{cta}</div>') if cta else ""
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Calculadora de prazos de licitação – Lei 14.133 | Expert Licitante</title>
<meta name="description" content="Calcule grátis o prazo de impugnação, esclarecimento, recurso, contrarrazões, regularização ME/EPP e vigência de ata pela Lei 14.133, em dias úteis.">
<meta name="theme-color" content="#07160f"><link rel="icon" href="/site/favicon.svg" type="image/svg+xml"><style>{CSS_PUB}</style></head><body>
<header class="topo"><div class="w"><a class="marca" href="/conheca"><i></i><img src="/site/nome.svg" height="17" alt="Expert Licitante"></a><nav><a class="lnk" href="/conheca">Conheça</a>
<a class="entrar" href="/login">Entrar</a>{cta_topo}</nav></div></header>
<div class="hero"><div class="w"><span class="tag">Ferramenta gratuita</span><h1>Calculadora de prazos<br>de licitação – Lei 14.133</h1>
<p>Impugnação, esclarecimento, prazo mínimo do edital, recurso, contrarrazões, regularização de ME/EPP e vigência de ata – em dias úteis, com o artigo da lei.</p></div></div>
<main class="w"><div class="caixa sobe"><form method="get" action="/prazos#resultado">
<fieldset><legend>1. Antes da sessão</legend><div class="g">
<label>Data da abertura da sessão<small>como está no edital</small><input type="date" name="abertura" value="{val("abertura")}"></label>
<label>Data de divulgação do edital<small>opcional – confere o prazo mínimo</small><input type="date" name="publicacao" value="{val("publicacao")}"></label>
<label>Objeto e critério<small>opcional – para o prazo mínimo</small><select name="tipo"><option value="">—</option>{opts}</select></label>
<label>Protocolo do seu pedido<small>opcional – impugnação ou esclarecimento</small><input type="date" name="protocolo" value="{val("protocolo")}"></label>
</div></fieldset>
<fieldset><legend>2. Depois do resultado</legend><div class="g">
<label>Ata do julgamento / habilitação<small>ou da declaração de vencedor</small><input type="date" name="resultado" value="{val("resultado")}"></label>
<label>Divulgação do recurso de outro licitante<small>para as contrarrazões</small><input type="date" name="recurso" value="{val("recurso")}"></label>
</div><label class="chk"><input type="checkbox" name="me" value="1"{" checked" if q.get("me") else ""}> Sou ME/EPP e preciso regularizar certidão com restrição</label></fieldset>
<fieldset><legend>3. Ata de registro de preços</legend><div class="g">
<label>Início da vigência da ata<small>opcional</small><input type="date" name="ata" value="{val("ata")}"></label></div></fieldset>
<button class="cta" type="submit">Calcular prazos</button></form>{res}</div>
{vende}
<p class="nota">Contagem em dias úteis com os feriados nacionais (inclui Carnaval e Corpus Christi). Feriados estaduais e municipais, pontos facultativos
e regras próprias do edital podem mudar a data – confira sempre o edital e o sistema da licitação. Esta calculadora é uma ajuda e não substitui orientação jurídica.</p>
</main><footer><div class="w"><span><img src="/site/escudo.svg" height="22" alt="" style="vertical-align:middle;margin-right:8px">© Expert Licitante · licitação no piloto automático</span><span><a href="/conheca" style="text-decoration:none">Conheça o Expert Licitante</a></span></div></footer>
</body></html>"""
