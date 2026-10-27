"""Testes do robô de lances (decisão). Rode: python3 tests_robo.py"""
import robo

C = {**robo.PADRAO, "ativo": True, "decremento": "0,50", "dif_minima": "0,10", "intervalo": "20", "max_lances": "30", "salto_max": "5"}
T0 = 1_000_000.0
falhas = 0


def caso(nome, esperado_acao, esperado_valor=None, **k):
    global falhas
    args = dict(cfg=C, piso=90.0, melhor=100.0, somos_primeiro=False, nosso_ultimo=101.0, viva=True, agora_ts=T0, est={})
    args.update(k)
    a, v, m = robo.decidir(**args)
    ok = a == esperado_acao and (esperado_valor is None or (v is not None and abs(v - esperado_valor) < 1e-9))
    if not ok:
        falhas += 1
    print(("✅" if ok else "❌"), nome, "->", a, v, "|", m)


caso("cobre o 1º com decremento", "lance", 99.50)
caso("desligado", "nada", cfg={**C, "ativo": False})
caso("fora da fase de lances", "nada", viva=False)
caso("sem piso não dá lance", "parar", piso=None)
caso("já em 1º", "nada", somos_primeiro=True)
caso("melhor não lido", "nada", melhor=0)
caso("intervalo mínimo", "nada", est={"ultimo_ts": T0 - 5, "n": 1})
caso("intervalo cumprido", "lance", 99.50, est={"ultimo_ts": T0 - 25, "n": 1})
caso("limite de lances", "parar", est={"n": 30})
caso("decremento passa do piso -> lance no piso", "lance", 90.00, piso=90.0, melhor=90.30)
caso("1º já no piso -> para", "parar", piso=90.0, melhor=90.0)
caso("1º abaixo do piso -> para", "parar", piso=90.0, melhor=85.0)
caso("piso a menos que a dif mínima -> para", "parar", piso=99.95, melhor=100.0)
caso("decremento em %", "lance", 99.00, cfg={**C, "dec_tipo": "pct", "decremento": "1"})
caso("dif mínima maior que decremento", "lance", 99.00, cfg={**C, "decremento": "0,10", "dif_minima": "1,00"})
caso("arredonda para baixo", "lance", 99.66, melhor=100.0, cfg={**C, "decremento": "0,333"})
caso("trava de salto (piso muito baixo e decremento grande)", "parar", piso=10.0, cfg={**C, "decremento": "20"})
caso("melhora o próprio lance pela dif mínima", "lance", 99.45, nosso_ultimo=99.55)
caso("próprio lance perto: desce pela dif mínima", "lance", 99.40, nosso_ultimo=99.50, cfg={**C, "decremento": "0,05", "dif_minima": "0,10"})
caso("melhorar o próprio passaria do piso", "parar", piso=99.45, nosso_ultimo=99.50, melhor=99.50, cfg={**C, "decremento": "0,01", "dif_minima": "0,10"})
caso("valores grandes", "lance", 1_234_566.50, melhor=1_234_567.00, nosso_ultimo=1_300_000.0, piso=1_000_000.0)
caso("usa último lance do robô se não lê o nosso", "lance", 99.40, nosso_ultimo=None, est={"ultimo_valor": 99.50, "ultimo_ts": T0 - 30, "n": 1},
     cfg={**C, "decremento": "0,05", "dif_minima": "0,10"})

caso("nunca cobre o próprio lance (empate de nome não reconhecido)", "nada", melhor=99.50, est={"ultimo_valor": 99.50, "ultimo_ts": T0 - 60, "n": 1})

# validação da configuração
cfg, err = robo.validar({"ativo": "1", "decremento": "0,50", "dif_minima": "0,01", "intervalo": "20", "max_lances": "30", "salto_max": "5"})
print("✅" if cfg and not err else "❌", "validar ok", err)
falhas += 0 if cfg and not err else 1
for ruim in ({"decremento": "0"}, {"decremento": "50", "dec_tipo": "pct"}, {"decremento": "1", "intervalo": "1"}, {"decremento": "1", "salto_max": "90"}):
    c2, e2 = robo.validar({**{"intervalo": "20", "max_lances": "30", "salto_max": "5"}, **ruim})
    ok = c2 is None and e2
    falhas += 0 if ok else 1
    print("✅" if ok else "❌", "recusa", ruim, "->", e2)
for txt, v in (("1.234,56", 1234.56), ("R$ 21.940,00", 21940.0), ("99,5", 99.5), ("100", 100.0), ("1.200", 1200.0), ("12.5", 12.5)):
    ok = abs(robo.num(txt) - v) < 1e-9
    falhas += 0 if ok else 1
    print("✅" if ok else "❌", "num", txt, robo.num(txt))
print("\nFALHAS:", falhas)
raise SystemExit(1 if falhas else 0)
