"""Recursos para o licitante iniciante: checagem "Estou pronto?", trilha "Minha primeira licitação", modelos prontos,
explicação das mensagens do pregoeiro, filtro de oportunidades boas para começar e o prompt do "edital explicado".
Só conteúdo e regras – as telas ficam no painel. Nada é enviado às plataformas."""
import json
import re
import unicodedata


def norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


# ================================================================== 1. Estou pronto para licitar?
# (chave, título, peso, por que importa, como resolver, link, automático?)
PRONTO = [
    ("cnae", "Empresa com CNPJ ativo e CNAE do que você vai vender", 3,
     "O órgão confere se o que você vende está entre as atividades (CNAE) do cartão CNPJ e do contrato social. Atividade fora do objeto pode inabilitar.",
     "Consulte o cartão CNPJ na Receita e, se faltar a atividade, peça ao contador para incluir.",
     "https://solucoes.receita.fazenda.gov.br/servicos/cnpjreva/cnpjreva_solicitacao.asp", False),
    ("dados", "Dados da empresa completos no Licitatudo", 2,
     "CNPJ, endereço e representante entram sozinhos em todas as declarações, propostas e peças.",
     "Preencha em Documentos → Declarações → Dados da empresa (dá para puxar da Receita pelo CNPJ).", "/documentos#dados", True),
    ("porte", "Porte informado (ME, EPP ou demais)", 1,
     "Microempresa e EPP têm vantagens na lei (LC 123): disputas exclusivas até R$ 80 mil por item, empate ficto e prazo extra para regularizar certidão.",
     "Informe o porte em Dados da empresa. Na dúvida, pergunte ao contador.", "/documentos#dados", True),
    ("cert_basicas", "Certidões Federal, FGTS e Trabalhista válidas", 3,
     "São pedidas em quase toda licitação. Vencida no dia da sessão = risco de inabilitação.",
     "Emita grátis nos sites oficiais e cadastre em Certidões – o sistema avisa antes de vencer.", "/certidoes", True),
    ("cert_locais", "Certidões estadual e municipal", 2,
     "Muitos editais pedem a regularidade com o estado (ICMS) e o município (ISS/IPTU).",
     "Emita no site da SEFAZ do seu estado e da prefeitura da sua cidade e cadastre em Certidões.", "/certidoes", True),
    ("falencia", "Certidão de falência e recuperação judicial", 1,
     "Faz parte da qualificação econômico-financeira na maioria dos editais.",
     "Emita no site do Tribunal de Justiça do seu estado e cadastre em Certidões.", "/certidoes", True),
    ("contrato", "Contrato social e alterações em PDF", 2,
     "É o documento que prova quem é a empresa e quem pode assinar por ela.",
     "Peça a versão consolidada à Junta Comercial ou ao contador e guarde em Certidões (tipo Contrato social).", "/certidoes", True),
    ("balanco", "Balanço patrimonial do último ano", 2,
     "Muitos editais pedem balanço e índices (liquidez) para provar saúde financeira.",
     "Peça ao contador o balanço do último exercício assinado e guarde em Certidões (tipo Balanço).", "/certidoes", True),
    ("certificado", "Certificado digital (e-CNPJ) ou conta gov.br prata/ouro", 2,
     "Serve para assinar declarações e propostas e para entrar em alguns portais (como o Compras.gov.br).",
     "Compre um e-CNPJ A1 numa certificadora ou eleve sua conta gov.br. Veja em Documentos → Como assinar.", "/assinatura", False),
    ("sicaf", "Cadastro no SICAF / Compras.gov.br", 1,
     "Obrigatório para vender ao governo federal e usado por vários estados e municípios.",
     "Faça o credenciamento no portal Compras.gov.br com a conta gov.br da empresa.", "https://www.gov.br/compras/pt-br", False),
    ("plataforma", "Cadastro em pelo menos uma plataforma de licitação", 2,
     "É onde a disputa acontece: BLL, BNC, Portal de Compras Públicas, Licitanet ou Compras.gov.br.",
     "Cadastre-se na plataforma usada pelos órgãos da sua região e ligue o monitor em Empresas.", "/empresas", True),
    ("atestado", "Pelo menos um atestado de capacidade técnica", 2,
     "Prova que você já forneceu algo parecido. Vale de empresa privada também.",
     "Peça a clientes uma declaração de fornecimento (com produto, quantidade e período) e cadastre em Atestados.", "/atestados", True),
    ("conta_pj", "Conta bancária PJ para receber", 1,
     "O pagamento do órgão vai para a conta da empresa informada na proposta.",
     "Informe banco, agência e conta em Dados da empresa.", "/documentos#dados", True),
    ("custos", "Custos dos produtos que você vai vender", 2,
     "Sem saber o custo com frete e impostos, o lance vira chute – e dá prejuízo.",
     "Cadastre os produtos e os percentuais em Catálogo e preços.", "/catalogo", True),
    ("nf", "Emite nota fiscal eletrônica do que vende", 1,
     "Depois da entrega, o pagamento só sai com a nota fiscal certa.",
     "Confira com o contador se a empresa já emite NF-e (produtos) ou NFS-e (serviços).", "", False),
    ("alertas", "Alertas no celular ligados (Telegram e alarme)", 1,
     "O pregoeiro dá prazos de minutos ou horas. O alarme evita perder a convocação.",
     "Ligue em ⚙️ Minhas configurações.", "/preferencias", True),
]


def nivel_pronto(pct):
    if pct >= 85:
        return "🟢 Pronto para licitar", "Sua empresa tem o essencial. Agora é achar a primeira licitação."
    if pct >= 60:
        return "🟡 Quase lá", "Faltam poucos itens – resolva os de peso maior primeiro."
    return "🔴 Começando", "Normal no início. Siga a lista de cima para baixo; o sistema ajuda em cada passo."


# ================================================================== 2. Trilha "Minha primeira licitação"
TRILHA = [
    ("jogo", "Entenda como funciona", "📚",
     "Licitação é a compra do governo. A Lei 14.133/2021 define as regras: o órgão publica o edital, as empresas mandam proposta, "
     "disputam por lances, a vencedora mostra os documentos (habilitação) e, se estiver tudo certo, assina o contrato ou a ata.",
     ["Pregão eletrônico: o mais comum para produtos e serviços comuns – ganha o menor preço.",
      "Dispensa eletrônica: compras menores (até cerca de R$ 60 mil para bens), mais rápidas e simples – ótimas para começar.",
      "Ordem do pregão: proposta → lances → aceitação → habilitação → recurso → adjudicação/homologação → contrato/ata → entrega → nota → pagamento."],
     [("❓ Central de ajuda", "/ajuda"), ("⚖️ Calculadora de prazos", "/prazos")],
     "Comece por compras pequenas e perto de você. A primeira vitória ensina mais que qualquer curso."),
    ("pronto", "Deixe a empresa pronta", "✅",
     "Antes de disputar, a empresa precisa dos documentos básicos em dia: certidões, contrato social, balanço, atestado e cadastro na plataforma.",
     ["Faça a checagem “Estou pronto para licitar?” e resolva os itens de peso maior.",
      "Cadastre as certidões com validade – o sistema avisa antes de vencer.",
      "Preencha os dados da empresa: eles entram sozinhos em todos os documentos."],
     [("✅ Estou pronto?", "/pronto"), ("📑 Certidões", "/certidoes")],
     "Certidão vencida é o motivo nº 1 de inabilitação de quem está começando."),
    ("achar", "Ache a primeira licitação", "🔎",
     "Procure compras do que você já vende, de valor baixo e perto da sua cidade. Prefira dispensas e pregões exclusivos para ME/EPP.",
     ["Em Oportunidades, use o filtro 🌱 Bom para começar.",
      "Em Buscar editais, pesquise pelo produto e pela sua região.",
      "Gostou? Clique em “📌 Vou participar” – a licitação vira um cartão com o passo a passo."],
     [("🎯 Oportunidades", "/oportunidades?f=iniciante"), ("🔍 Buscar editais", "/buscar")],
     "Valor estimado pequeno = menos concorrência grande e menos exigências."),
    ("edital", "Leia o edital sem medo", "📖",
     "O edital diz o que será comprado, os documentos exigidos, prazos de entrega e pagamento e o que pode desclassificar. Leia com atenção o termo de referência.",
     ["Use “Analisar edital (IA)” e depois “🌱 Explicar para iniciante”: você recebe o resumo em linguagem simples e um semáforo de vale a pena.",
      "Anote o prazo para pedir esclarecimento (3 dias úteis antes da abertura).",
      "Dúvida? Pergunte ao órgão por esclarecimento – é seu direito e é de graça."],
     [("🤖 Editais (IA)", "/editais"), ("⚖️ Prazos", "/prazos")],
     "Procure no edital: amostra, visita técnica, atestado e prazo de entrega. São as maiores armadilhas."),
    ("documentos", "Monte documentos e proposta", "📄",
     "Proposta com preço certo e documentos de habilitação organizados num PDF só.",
     ["Cadastre os custos em Catálogo e preços e veja o lance mínimo de cada item.",
      "Monte a proposta (dá para trazer os itens do edital) e gere as declarações com o timbre.",
      "Gere o pacote de habilitação e assine (gov.br ou certificado digital)."],
     [("💰 Proposta", "/propostas"), ("📦 Pacote", "/pacote"), ("✍️ Como assinar", "/assinatura")],
     "Nunca dê lance abaixo do seu lance mínimo: ganhar com prejuízo é pior que perder."),
    ("disputa", "Treine e dispute", "🎮",
     "Na sessão você disputa por lances. O monitor do Licitatudo lê o chat e toca o alarme quando o pregoeiro chama.",
     ["Treine no Simulador de disputa até se sentir seguro.",
      "Ligue o monitor da plataforma e o alarme no celular.",
      "Fique de olho: depois dos lances o pregoeiro costuma pedir a proposta ajustada em poucas horas."],
     [("🎮 Simulador", "/simulador"), ("🎯 Disputa ao vivo", "/disputa"), ("📝 Modelos", "/modelos")],
     "ME/EPP: se você ficar até 5% acima de uma empresa grande, tem direito de cobrir o lance (empate ficto)."),
    ("depois", "Depois da disputa", "🏁",
     "Ganhou: envie a proposta ajustada e os documentos, aguarde a homologação e assine o contrato ou a ata. Perdeu: veja o porquê e tente de novo.",
     ["Use “Por que perdi?” para comparar seu preço com o do vencedor.",
      "Discorda do resultado? Manifeste a intenção de recurso na sessão e use as Peças com IA.",
      "Ganhou: cadastre o contrato, as entregas e as notas – o sistema avisa atraso de pagamento."],
     [("💼 Contratos", "/contratos"), ("⚖️ Peças com IA", "/pecas"), ("📈 Minha evolução", "/evolucao")],
     "Cada licitação perdida mostra o preço do mercado. Use isso para ajustar fornecedores e margem."),
]


# ================================================================== 5. Modelos prontos
# (chave, título, onde usar, "chat" = texto curto para o chat da plataforma | "doc" = documento com timbre, texto)
MODELOS = [
    ("chat_ciente", "Chat: ciente da convocação (proposta ajustada)", "Chat da plataforma", "chat",
     "Prezado(a) Pregoeiro(a), boa tarde. A empresa {RAZAO} está ciente e enviará a proposta ajustada ao último lance, "
     "com os documentos solicitados, dentro do prazo concedido. Atenciosamente."),
    ("chat_prorrogacao", "Chat: pedir mais prazo para enviar documento", "Chat da plataforma", "chat",
     "Prezado(a) Pregoeiro(a), a empresa {RAZAO} solicita, respeitosamente, a prorrogação do prazo por mais [PREENCHER: ex. 2 horas / 1 dia útil] "
     "para o envio de [PREENCHER: documento], tendo em vista [PREENCHER: motivo – ex. aguardamos a ficha técnica do fabricante]. Agradecemos a compreensão."),
    ("chat_negociacao_aceita", "Chat: aceitar a negociação de preço", "Chat da plataforma", "chat",
     "Prezado(a) Pregoeiro(a), a empresa {RAZAO} aceita reduzir o valor do lote/item [PREENCHER] para R$ [PREENCHER], "
     "mantidas as demais condições da proposta. Atenciosamente."),
    ("chat_negociacao_recusa", "Chat: não conseguir reduzir mais o preço", "Chat da plataforma", "chat",
     "Prezado(a) Pregoeiro(a), agradecemos a oportunidade. Após análise dos custos (produto, frete e tributos), a empresa {RAZAO} "
     "informa que já ofertou o menor valor possível, R$ [PREENCHER], que está dentro do valor estimado pela Administração. Atenciosamente."),
    ("chat_amostra", "Chat: amostra enviada", "Chat da plataforma", "chat",
     "Prezado(a) Pregoeiro(a), informamos que a amostra do item [PREENCHER] foi enviada em [PREENCHER: data] para [PREENCHER: endereço do órgão], "
     "código de rastreio [PREENCHER]. Ficamos à disposição."),
    ("of_proposta", "Ofício: encaminhamento da proposta ajustada", "Anexo na plataforma", "doc",
     "Encaminhamos a Vossa Senhoria a proposta comercial ajustada ao valor final ofertado na sessão pública do {MODALIDADE} nº {PROCESSO}, "
     "acompanhada dos documentos solicitados.\n"
     "Declaramos que nos preços estão incluídos todos os custos diretos e indiretos, tributos, fretes e demais despesas necessárias ao fornecimento, "
     "e que mantemos as condições de prazo de entrega, validade da proposta e garantia previstas no edital.\n"
     "Colocamo-nos à disposição para quaisquer esclarecimentos."),
    ("of_prorrogacao", "Ofício: pedido de prorrogação de prazo (diligência)", "Anexo ou e-mail ao pregoeiro", "doc",
     "A empresa {RAZAO}, participante do {MODALIDADE} nº {PROCESSO}, vem respeitosamente solicitar a prorrogação do prazo concedido para "
     "[PREENCHER: o que foi pedido], por mais [PREENCHER] dias úteis.\n"
     "O pedido se justifica porque [PREENCHER: motivo objetivo – ex. o fabricante emite o laudo em até 3 dias úteis], circunstância alheia à vontade da empresa.\n"
     "A prorrogação não traz prejuízo ao andamento do certame e preserva a busca da proposta mais vantajosa para a Administração.\n"
     "Nestes termos, pede deferimento."),
    ("of_amostra", "Ofício: envio de amostras", "Junto com a amostra", "doc",
     "Encaminhamos as amostras referentes ao {MODALIDADE} nº {PROCESSO}, conforme solicitado:\n"
     "Item [PREENCHER] – [PREENCHER: descrição, marca e modelo] – quantidade: [PREENCHER].\n"
     "As amostras estão identificadas com o nome da empresa e o número do item. Solicitamos, após a análise, a devolução ou o descarte conforme previsto no edital."),
    ("of_prorrogacao_entrega", "Ofício: prorrogação do prazo de entrega (contrato)", "Ao fiscal/gestor do contrato", "doc",
     "A empresa {RAZAO}, contratada no âmbito do {MODALIDADE} nº {PROCESSO}, solicita a prorrogação do prazo de entrega da ordem de fornecimento/nota de empenho nº [PREENCHER] "
     "para [PREENCHER: nova data].\n"
     "O motivo é [PREENCHER: fato alheio à empresa – ex. atraso do fabricante, greve de transportes], conforme documento anexo.\n"
     "Reafirmamos o compromisso com a entrega e permanecemos à disposição."),
    ("of_troca_marca", "Ofício: troca de marca/modelo por equivalente ou superior", "Ao fiscal/gestor do contrato", "doc",
     "A empresa {RAZAO}, contratada no âmbito do {MODALIDADE} nº {PROCESSO}, solicita autorização para entregar o item [PREENCHER] da marca/modelo "
     "[PREENCHER: nova], em substituição à marca/modelo [PREENCHER: proposta], que [PREENCHER: saiu de linha / está em falta no fabricante].\n"
     "O novo produto tem qualidade igual ou superior, conforme ficha técnica anexa, sem qualquer custo adicional para a Administração."),
    ("of_cobranca", "Ofício: cobrança de pagamento atrasado", "Ao setor financeiro do órgão", "doc",
     "A empresa {RAZAO}, contratada no âmbito do {MODALIDADE} nº {PROCESSO}, informa que a nota fiscal nº [PREENCHER], de [PREENCHER: data], "
     "no valor de R$ [PREENCHER], referente a fornecimento já entregue e atestado, ainda não foi paga, embora o prazo de pagamento tenha vencido em [PREENCHER].\n"
     "Solicitamos a regularização do pagamento, com a atualização monetária prevista no contrato, ou a informação da data prevista.\n"
     "Agradecemos a atenção."),
]


def preencher(texto, ctx):
    trocas = {"RAZAO": ctx.get("razao") or "[RAZÃO SOCIAL]", "CNPJ": ctx.get("cnpj_fmt") or "[CNPJ]",
              "REP_NOME": ctx.get("rep_nome") or "[REPRESENTANTE]", "ORGAO": ctx.get("orgao") or "[ÓRGÃO]",
              "PROCESSO": ctx.get("processo") or "[Nº/ANO]", "MODALIDADE": ctx.get("modalidade") or "Pregão Eletrônico"}
    return re.sub(r"\{(RAZAO|CNPJ|REP_NOME|ORGAO|PROCESSO|MODALIDADE)\}", lambda m: trocas[m.group(1)], texto)


# ================================================================== 6. O que significa essa mensagem?
# (padrão no texto normalizado, título, o que significa, o que fazer, atalhos [(rótulo, tipo)])
EXPLICAR = [
    (r"proposta (ajustada|readequada|atualizada|reformulada|final)|readequ|ajust\w* (ao|a|com o) (ultimo|valor)|ao ultimo lance",
     "Mandar a proposta ajustada",
     "Você (ou a empresa chamada) ficou em 1º lugar e o pregoeiro quer a proposta com os preços do último lance. O prazo costuma ser curto (ex.: 2 horas).",
     "Na proposta, use “Readequar ao lance”, confira, gere o PDF, assine e anexe na plataforma ANTES do prazo. Responda no chat que está ciente.",
     [("💰 Abrir propostas", "propostas"), ("📝 Modelo de resposta", "m:chat_ciente"), ("📝 Ofício de encaminhamento", "m:of_proposta")]),
    (r"diligenc", "Diligência",
     "O pregoeiro pediu um esclarecimento ou um documento a mais para conferir sua proposta ou habilitação. Não é punição – é uma chance de corrigir.",
     "Leia exatamente o que foi pedido e o prazo. Responda pelo canal indicado (sistema ou e-mail). Se não der tempo, peça prorrogação ANTES do prazo acabar.",
     [("📝 Pedir prorrogação", "m:of_prorrogacao"), ("⚖️ Resposta com IA", "pecas")]),
    (r"amostra|catalogo|ficha tecnica|prospecto|folder", "Amostra / catálogo",
     "O órgão quer ver o produto (amostra) ou a documentação técnica (catálogo, ficha técnica) para conferir se atende ao edital.",
     "Confira no edital o prazo e o endereço. Envie identificado com o nº do item e guarde o rastreio. Avise no chat.",
     [("📝 Ofício de amostras", "m:of_amostra"), ("📝 Aviso no chat", "m:chat_amostra")]),
    (r"negocia|contraproposta|melhor (oferta|proposta|preco|valor)|reduzir (o |seu )?(valor|preco)|reducao (do|de) (valor|preco)|desconto",
     "Negociação de preço",
     "O pregoeiro está pedindo um preço menor. É normal, principalmente quando o valor está acima do estimado.",
     "Veja seu lance mínimo na formação de preço. Se der, aceite um valor que ainda tenha lucro. Se não der, responda com educação que é o menor valor possível.",
     [("🧮 Formação de preço", "propostas"), ("📝 Aceitar", "m:chat_negociacao_aceita"), ("📝 Recusar", "m:chat_negociacao_recusa")]),
    (r"intencao de recurso|manifest\w* (a |sua )?intencao|registr\w* (a )?intencao|prazo (para|de) (manifestacao|recurso)",
     "Intenção de recurso",
     "Abriu o momento de dizer que vai recorrer. A intenção é manifestada na hora, no sistema, em poucos minutos – depois não dá mais.",
     "Discorda do resultado? Registre a intenção com um motivo curto. Depois você tem 3 dias úteis para enviar as razões.",
     [("⚖️ Recurso com IA", "pecas"), ("⚖️ Calcular prazo", "prazos")]),
    (r"inabilitad", "Empresa inabilitada",
     "A empresa foi reprovada nos documentos de habilitação (certidão, atestado, balanço…).",
     "Veja o motivo no chat/ata. Se achar que está errado, manifeste intenção de recurso na hora. Use o aprendizado para a próxima.",
     [("⚖️ Recurso com IA", "pecas"), ("📑 Certidões", "certidoes")]),
    (r"desclassificad", "Proposta desclassificada",
     "A proposta foi retirada da disputa por não atender ao edital (produto, marca, preço inexequível, documento da proposta…).",
     "Leia o motivo. Se discordar, manifeste intenção de recurso na sessão. Confira o edital para não repetir o erro.",
     [("⚖️ Recurso com IA", "pecas")]),
    (r"suspens", "Sessão suspensa",
     "A sessão foi pausada. Vai continuar em outra data/hora (que o pregoeiro informa no chat ou no sistema).",
     "Anote a data de retomada – o Licitatudo já tenta colocar na Agenda. Esteja on-line na volta.",
     [("📅 Agenda", "agenda")]),
    (r"retom|reabert|reinici|continuidade|sessao (sera|vai ser|fica) (retomada|reaberta)", "Retomada da sessão",
     "A disputa vai continuar na data/hora informada.",
     "Confira a data na Agenda e esteja on-line alguns minutos antes. Deixe a proposta e os documentos prontos.",
     [("📅 Agenda", "agenda")]),
    (r"(documentos? de )?habilitacao|anex(e|ar|o)s?|envi(e|ar|o) (os |o )?documento", "Envio de documentos",
     "O pregoeiro pediu os documentos de habilitação (ou outro anexo) pela plataforma.",
     "Gere o pacote de habilitação em PDF, assine e anexe no sistema dentro do prazo.",
     [("📦 Pacote de habilitação", "pacote"), ("📝 Pedir mais prazo", "m:chat_prorrogacao")]),
    (r"empate|me/epp|microempresa|direito de preferencia|lc 123|art\.? ?4[45]", "Empate ficto ME/EPP",
     "Se você é ME/EPP e ficou até 5% acima de uma empresa que não é, a lei dá o direito de cobrir o lance (no pregão, em 5 minutos).",
     "Fique atento ao chamado no sistema e dê o novo lance abaixo do 1º colocado, sem passar do seu lance mínimo.",
     [("🎮 Treinar no simulador", "simulador")]),
    (r"aleatori|randomic|encerrad\w* (a )?(fase|etapa) de lances|fase de lances (foi )?encerrad|encerramento (da|dos) lances",
     "Fim da fase de lances",
     "Os lances acabaram (no modo aberto/fechado, depois de um tempo aleatório). Agora vem a análise da proposta do 1º colocado.",
     "Veja sua posição na classificação. Se ficou em 1º, prepare a proposta ajustada. Se não, acompanhe – o 1º pode ser desclassificado.",
     [("📊 Disputa", "disputa")]),
    (r"aceit(a|o|e|ou)|aceitacao", "Proposta aceita",
     "O pregoeiro aceitou a proposta (preço e produto). Falta a habilitação ou o prazo de recurso.",
     "Mantenha os documentos prontos e acompanhe o chat.", []),
    (r"adjudica|homologa", "Adjudicação / homologação",
     "A licitação foi concluída oficialmente: o objeto foi entregue ao vencedor (adjudicação) e a autoridade aprovou tudo (homologação).",
     "Se você venceu: aguarde a convocação para assinar o contrato/ata ou a nota de empenho e cadastre em Contratos.",
     [("💼 Contratos", "contratos")]),
    (r"convoca|assinatura (do|da) (contrato|ata)|assinar (o |a )?(contrato|ata)", "Convocação",
     "A empresa foi chamada para algo com prazo: enviar documento, assinar contrato/ata ou retirar empenho.",
     "Veja o prazo e responda dentro dele. Perder o prazo de assinatura pode gerar multa e impedimento.",
     [("💼 Contratos", "contratos"), ("📅 Agenda", "agenda")]),
    (r"vencedor|arrematante|melhor classificad|primeiro colocad|1o colocad", "Resultado / arrematante",
     "O pregoeiro informou quem ficou em 1º (arrematou).",
     "Se foi você, prepare-se para a proposta ajustada e a habilitação. Se não, veja “Por que perdi?” depois.",
     [("📊 Disputa", "disputa")]),
    (r"recurso", "Recurso",
     "Alguém recorreu ou o prazo de recurso está correndo.",
     "Se outra empresa recorreu contra você, você pode apresentar contrarrazões em 3 dias úteis.",
     [("⚖️ Contrarrazões com IA", "pecas"), ("⚖️ Calcular prazo", "prazos")]),
    (r"esclarecimento|impugna", "Esclarecimento / impugnação",
     "Alguém pediu esclarecimento ou impugnou o edital. A resposta pode mudar regras ou datas.",
     "Leia a resposta publicada e confira se muda algo para você (data, documento, produto).", [("⚖️ Prazos", "prazos")]),
]
_EXPL = [(re.compile(p), t, s, f, a) for p, t, s, f, a in EXPLICAR]


def explicar(texto):
    """Até 2 explicações [(título, significa, fazer, atalhos)] para a mensagem do pregoeiro/sistema."""
    n = norm(texto)
    out = []
    for rx, t, s, f, a in _EXPL:
        if rx.search(n):
            out.append((t, s, f, a))
            if len(out) == 2:
                break
    return out


# ================================================================== 7. Oportunidades boas para começar
RE_DIFICIL = re.compile(r"\bobras?\b|engenharia|pavimenta|reforma|construcao de|mao de obra|terceiriza|dedicacao exclusiva|"
                        r"servicos? continuad|locacao de veiculos|transporte escolar|coleta de (lixo|residuos)|vigilancia")
RE_ME = re.compile(r"exclusiv\w* (para |a |as )?(me|epp|microempresa)|me/epp|cota reservada|lc 123")


def pontos_iniciante(r, km=None, agora=None):
    """(pontos, motivos) – quanto a oportunidade é boa para quem está começando."""
    from datetime import datetime
    agora = agora or datetime.now()
    pts, mot = 0, []
    mod = norm(r.get("modalidade"))
    obj = norm(r.get("objeto"))
    if "dispensa" in mod or "cotacao" in mod:
        pts += 3
        mot.append("dispensa (mais simples)")
    v = r.get("valor") or 0
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0
    me_txt = bool(RE_ME.search(obj))
    if 0 < v <= 80000:
        pts += 2
        mot.append("valor até R$ 80 mil" + ("" if me_txt else " (costuma ser exclusiva ME/EPP)"))
    elif 0 < v <= 250000:
        pts += 1
        mot.append("valor baixo")
    elif v > 1500000:
        pts -= 2
    if me_txt:
        pts += 2
        mot.append("exclusiva ME/EPP")
    if km is not None:
        if km <= 150:
            pts += 1
            mot.append(f"perto ({round(km)} km)")
        elif km > 600:
            pts -= 1
    if RE_DIFICIL.search(obj):
        pts -= 3
    try:
        fim = datetime.fromisoformat(str(r.get("fim") or "")[:19])
        dias = (fim - agora).days
        if dias >= 5:
            pts += 1
            mot.append(f"{dias} dias para preparar")
        elif dias < 2:
            pts -= 2
    except ValueError:
        pass
    return pts, mot


# ================================================================== 3. Edital explicado
PROMPT_SIMPLES = """Você é um consultor que ajuda uma pequena empresa que está COMEÇANDO a participar de licitações.
Abaixo está a análise técnica de um edital. Explique em português simples, sem juridiquês, frases curtas.
Responda SOMENTE com um JSON válido (sem markdown), neste formato:
{"o_que_compra": "1-2 frases", "quem_compra": "órgão e cidade", "valor_estimado": "texto ou 'não informado'",
 "data_sessao": "AAAA-MM-DDTHH:MM ou ''", "como_funciona": "modalidade e critério explicados em 1-2 frases (ex.: pregão eletrônico, ganha o menor preço por item)",
 "exclusivo_me_epp": "sim|não|parcial|não informado", "exige_atestado": "sim|não|não informado", "exige_amostra": "sim|não|não informado",
 "visita_tecnica": "obrigatória|facultativa|não", "entrega": "prazo e local em 1 frase", "pagamento": "prazo em 1 frase",
 "documentos": ["lista curta dos documentos de habilitação, com nome simples"],
 "armadilhas": ["até 5 pontos que costumam derrubar iniciantes NESTE edital (amostra, prazo curto, marca, garantia, multa alta...)"],
 "veredito": "verde|amarelo|vermelho",
 "motivos": ["2 a 4 motivos do veredito, pensando numa pequena empresa iniciante"],
 "primeiros_passos": ["3 a 5 ações práticas e ordenadas para participar"]}
Critério do veredito: verde = simples e acessível (poucos documentos, sem amostra/visita, valor pequeno/médio);
amarelo = dá para participar com cuidado (alguma exigência extra); vermelho = difícil para iniciante (obra/serviço complexo,
atestados específicos, garantia alta, amostra com prazo curto, valor muito alto). Não invente: se não souber, diga "não informado".
"""


def ler_json(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {}
    lim = lambda v, n=300: str(v or "")[:n]   # noqa: E731
    lista = lambda v, k=8: [lim(x, 240) for x in (v if isinstance(v, list) else []) if str(x).strip()][:k]   # noqa: E731
    ver = lim(d.get("veredito"), 10).lower()
    return {"o_que_compra": lim(d.get("o_que_compra"), 400), "quem_compra": lim(d.get("quem_compra")), "valor_estimado": lim(d.get("valor_estimado"), 80),
            "data_sessao": lim(d.get("data_sessao"), 16), "como_funciona": lim(d.get("como_funciona"), 400),
            "exclusivo_me_epp": lim(d.get("exclusivo_me_epp"), 20), "exige_atestado": lim(d.get("exige_atestado"), 20),
            "exige_amostra": lim(d.get("exige_amostra"), 20), "visita_tecnica": lim(d.get("visita_tecnica"), 20),
            "entrega": lim(d.get("entrega")), "pagamento": lim(d.get("pagamento")),
            "documentos": lista(d.get("documentos"), 20), "armadilhas": lista(d.get("armadilhas"), 5),
            "veredito": ver if ver in ("verde", "amarelo", "vermelho") else "amarelo",
            "motivos": lista(d.get("motivos"), 4), "primeiros_passos": lista(d.get("primeiros_passos"), 5)}
