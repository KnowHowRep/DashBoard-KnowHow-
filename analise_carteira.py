# -*- coding: utf-8 -*-
r"""
analise_carteira.py — Positivação, Curva ABC e Painel de Ação
Dashboard KnowHow (Ibrac Condimentos / VH Devro)

Uso no pipeline (dentro de dashboard.py):
    from analise_carteira import gerar_analise_carteira
    carteira = gerar_analise_carteira(df, analysis)   # analysis = _build_analysis(df)
    carteira_js = json.dumps(carteira, ensure_ascii=False, separators=(",", ":"))
    html = re.sub(r'const CARTEIRA\s*=\s*\{.*?\};', f'const CARTEIRA = {carteira_js};',
                  html, flags=re.DOTALL)

Aceita tanto as colunas do CSV do pipeline (empresa, cliente, tipo,
data_pedido, valor_item) quanto as do export xlsx (Empresa, Cliente, ...).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

# ── CONFIGURAÇÃO ───────────────────────────────────────────────────────────
MAPA_COLUNAS = {          # nomes alternativos -> nome interno
    "empresa": "empresa", "Empresa": "empresa",
    "cliente": "cliente", "Cliente": "cliente",
    "tipo": "tipo", "Tipo": "tipo",
    "data_pedido": "data_pedido", "Data Pedido": "data_pedido",
    "valor_item": "valor_item", "Valor Item": "valor_item",
}
DATA_MINIMA = "2020-01-01"     # descarta datas mal parseadas (ex.: 1986)
CLIENTES_EXCLUIR = {"FEVEREIRO_25", "RINALDO1", "MAURICEA", "FRIGO GOMES",
                    "CORDEIRO PATR", "MM CASINGS"}   # mesmos excluídos no render() do template
ALIAS_CLIENTES = {                 # variantes -> nome oficial (o do pedido)
    "KI DELICIA": "BRITO & SANTOS",
}
CICLO_PADRAO_DIAS = 45         # cliente com menos de 3 pedidos usa este ciclo
CICLO_MIN_DIAS, CICLO_MAX_DIAS = 15, 120
FATOR_RISCO, FATOR_INATIVO = 1.25, 2.5
MIN_PEDIDOS_CICLO = 3
JANELA_ABC_MESES = 12
CORTE_A, CORTE_B = 0.80, 0.95
PESO_ABC = {"A": 3.0, "B": 2.0, "C": 1.0}
PESO_STATUS = {"inativo": 3.0, "risco": 2.0, "ativo": 0.0}
PESO_PRODUTO_CRITICO = 1.5
GAP_CRITICO_MESES = 3          # produto crítico: parado há >= N meses...
FREQ_MIN_CRITICO = 2           # ...tendo sido comprado >= N vezes
PRODUTO_INATIVO_MESES = 5      # produto parado há >= N meses: sai dos alertas,
                               # vai para a seção recolhida "produtos inativos"
ARQUIVAR_CLIENTE_DIAS = 365    # cliente parado há >= N dias: sai do painel e
                               # da tabela, vai para "inativos há 12+ meses"


def _slug(texto: str) -> str:
    import unicodedata
    txt = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "-" for c in txt).strip("-").lower()


def _emp_key(empresa: str) -> str:
    return "ibrac" if "ibrac" in str(empresa).lower() else "vh"


def _preparar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: MAPA_COLUNAS[c] for c in df.columns if c in MAPA_COLUNAS})
    faltando = {"empresa", "cliente", "data_pedido", "valor_item"} - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas ausentes no DataFrame: {faltando}")
    df = df.copy()
    if "tipo" in df.columns:                       # orçamento/cotação não é compra
        df = df[df["tipo"].astype(str).str.upper().str.startswith("PED")]
    df["data_pedido"] = pd.to_datetime(df["data_pedido"], errors="coerce")
    df["valor_item"] = pd.to_numeric(df["valor_item"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["data_pedido", "cliente"])
    df = df[df["data_pedido"] >= pd.Timestamp(DATA_MINIMA)]
    df = df[df["cliente"].astype(str).str.strip() != ""]
    df["cliente"] = df["cliente"].astype(str).str.strip().replace(ALIAS_CLIENTES)
    df = df[~df["cliente"].isin(CLIENTES_EXCLUIR)]
    df["emp_key"] = df["empresa"].map(_emp_key)
    return df


def _ciclo_medio(datas: pd.Series) -> tuple[float, bool]:
    dias = pd.Series(sorted(datas.dt.normalize().unique()))
    if len(dias) < MIN_PEDIDOS_CICLO:
        return float(CICLO_PADRAO_DIAS), False
    itv = dias.diff().dropna().dt.days
    itv = itv[itv > 0]
    if itv.empty:
        return float(CICLO_PADRAO_DIAS), False
    return max(CICLO_MIN_DIAS, min(CICLO_MAX_DIAS, float(itv.median()))), True


def _status(dias_parado: int, ciclo: float) -> str:
    if dias_parado <= ciclo * FATOR_RISCO:
        return "ativo"
    if dias_parado <= ciclo * FATOR_INATIVO:
        return "risco"
    return "inativo"


def _curva_abc(df_emp: pd.DataFrame, hoje: pd.Timestamp) -> dict[str, str]:
    recorte = df_emp[df_emp["data_pedido"] >= hoje - pd.DateOffset(months=JANELA_ABC_MESES)]
    if recorte.empty:
        recorte = df_emp
    fat = recorte.groupby("cliente")["valor_item"].sum().sort_values(ascending=False)
    total = fat.sum()
    curvas: dict[str, str] = {}
    if total > 0:
        acum = fat.cumsum() / total
        for cli, a in acum.items():
            curvas[cli] = "A" if a <= CORTE_A else "B" if a <= CORTE_B else "C"
        curvas[fat.index[0]] = "A"
    for cli in df_emp["cliente"].unique():
        curvas.setdefault(cli, "C")
    return curvas


def _produtos_por_cliente(analysis_emp: dict) -> tuple[dict, dict]:
    """A partir do ANALYSIS que o dashboard.py já calcula
    ({cliente: {produto: {g: gap_meses, f: freq, c: cod, ...}}}), separa:
      - críticos : parados entre GAP_CRITICO e PRODUTO_INATIVO meses -> geram alerta
      - inativos : parados há PRODUTO_INATIVO+ meses -> só na seção recolhida"""
    criticos_out: dict[str, list[dict]] = {}
    inativos_out: dict[str, list[dict]] = {}
    for cliente, prods in (analysis_emp or {}).items():
        criticos, inativos = [], []
        for nome, info in prods.items():
            gap, freq = info.get("g", 0), info.get("f", 0)
            if freq < FREQ_MIN_CRITICO:
                continue
            item = {"produto": nome, "cod": info.get("c", ""), "gap_meses": gap}
            if gap >= PRODUTO_INATIVO_MESES:
                inativos.append(item)
            elif gap >= GAP_CRITICO_MESES:
                criticos.append(item)
        if criticos:
            criticos.sort(key=lambda x: -x["gap_meses"])
            criticos_out[cliente] = criticos[:5]   # top 5 para não poluir o painel
        if inativos:
            inativos.sort(key=lambda x: -x["gap_meses"])
            inativos_out[cliente] = inativos
    return criticos_out, inativos_out


def _motivo(c: dict) -> str:
    partes = []
    if c["status"] == "inativo":
        partes.append(f"sem comprar há {c['dias_sem_comprar']} dias (ciclo ~{c['ciclo_dias']:.0f}d)")
    elif c["status"] == "risco":
        partes.append(f"passou do ciclo de recompra ({c['dias_sem_comprar']}d parado, ciclo ~{c['ciclo_dias']:.0f}d)")
    if c["curva"] == "A":
        partes.append("cliente curva A — prioridade máxima")
    if c.get("produtos_criticos"):
        partes.append(f"{len(c['produtos_criticos'])} produto(s) parado(s) há {GAP_CRITICO_MESES}+ meses")
    return "; ".join(partes) or "acompanhamento de rotina"


def _painel(clientes: list[dict]) -> list[dict]:
    itens = []
    for c in clientes:
        tem_critico = bool(c.get("produtos_criticos"))
        if c["status"] == "ativo" and not tem_critico:
            continue
        score = PESO_ABC[c["curva"]] * (1.0 + PESO_STATUS[c["status"]])
        if tem_critico:
            score += PESO_PRODUTO_CRITICO
        itens.append({
            **{k: c[k] for k in ("cliente", "cliente_id", "curva", "status",
                                 "dias_sem_comprar", "ciclo_dias", "produtos_criticos")},
            "score": round(score, 2),
            "motivo": _motivo(c),
            "acao_sugerida": ("Visitar/ligar — reativação" if c["status"] == "inativo"
                              else "Contato preventivo esta semana" if c["status"] == "risco"
                              else "Ofertar produtos parados no próximo contato"),
        })
    itens.sort(key=lambda x: (-x["score"], -x["dias_sem_comprar"]))
    return itens


def gerar_analise_carteira(df_pedidos: pd.DataFrame,
                           analysis: dict | None = None,
                           hoje: datetime | None = None) -> dict:
    """Retorna {"gerado_em", "ibrac": {resumo, clientes, painel_acao}, "vh": {...}}
    — mesmas chaves ibrac/vh que DATA e ANALYSIS usam no template."""
    hoje_ts = pd.Timestamp(hoje or datetime.now()).normalize()
    df = _preparar(df_pedidos)
    analysis = analysis or {}
    resultado: dict = {"gerado_em": hoje_ts.strftime("%Y-%m-%d")}

    for emp_key in ("ibrac", "vh"):
        df_emp = df[df["emp_key"] == emp_key]
        if df_emp.empty:
            resultado[emp_key] = {"resumo": {}, "clientes": [], "painel_acao": []}
            continue
        curvas = _curva_abc(df_emp, hoje_ts)
        criticos, prod_inativos = _produtos_por_cliente(analysis.get(emp_key, {}))
        inicio_12m = hoje_ts - pd.DateOffset(months=JANELA_ABC_MESES)

        clientes = []
        for cliente, df_cli in df_emp.groupby("cliente"):
            ciclo, proprio = _ciclo_medio(df_cli["data_pedido"])
            ultima = df_cli["data_pedido"].max()
            dias = max(0, int((hoje_ts - ultima.normalize()).days))
            clientes.append({
                "cliente": str(cliente),
                "cliente_id": _slug(cliente),
                "curva": curvas.get(cliente, "C"),
                "status": _status(dias, ciclo),
                "arquivado": dias >= ARQUIVAR_CLIENTE_DIAS,
                "ciclo_dias": round(ciclo, 1),
                "ciclo_proprio": proprio,
                "dias_sem_comprar": dias,
                "ultima_compra": ultima.strftime("%Y-%m-%d"),
                "faturamento_12m": round(float(
                    df_cli.loc[df_cli["data_pedido"] >= inicio_12m, "valor_item"].sum()), 2),
                "produtos_criticos": criticos.get(str(cliente), []),
                "produtos_inativos": prod_inativos.get(str(cliente), []),
            })
        clientes.sort(key=lambda c: -c["faturamento_12m"])

        # Carteira ativa = clientes não arquivados; positivação e painel só
        # consideram ela. Arquivados (12+ meses) ficam na seção recolhida.
        ativos_cart = [c for c in clientes if not c["arquivado"]]
        total = len(ativos_cart)
        n_a = sum(1 for c in ativos_cart if c["status"] == "ativo")
        n_r = sum(1 for c in ativos_cart if c["status"] == "risco")
        resultado[emp_key] = {
            "resumo": {
                "total": total, "ativos": n_a, "risco": n_r,
                "inativos": total - n_a - n_r,
                "arquivados": len(clientes) - total,
                "positivacao_pct": round(100.0 * n_a / total, 1) if total else 0.0,
                "curva": {L: sum(1 for c in ativos_cart if c["curva"] == L) for L in "ABC"},
            },
            "clientes": clientes,
            "painel_acao": _painel(ativos_cart),
        }
    return resultado
