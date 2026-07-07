# -*- coding: utf-8 -*-
"""
dashboard_core.py — Funções compartilhadas pelos 3 geradores de dashboard:
  • dashboard.py           (representante, local)
  • generate_diretoria.py  (diretoria Ibrac / VH, local)
  • generate_dashboard.py  (online, GitHub Actions)

REGRA DE OURO: lógica de dados mora AQUI. Os geradores só decidem o que
mostrar/esconder. Mudou aqui, mudou nos três.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

import pandas as pd

try:
    from analise_carteira import gerar_analise_carteira, ALIAS_CLIENTES
except ImportError:      # módulo ausente: dashboards continuam funcionando sem a aba Análises
    gerar_analise_carteira = None
    ALIAS_CLIENTES = {}

MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2028) for m in range(1, 13)]


def mi(m: str) -> int:
    try:
        return MONTHS.index(m)
    except ValueError:
        return -1


def emp_key(empresa: str) -> str:
    return "ibrac" if "ibrac" in str(empresa).lower() else "vh"


# ── Carregamento ───────────────────────────────────────────────────────────

def load_csv(csv_path) -> pd.DataFrame:
    """Lê o CSV do pipeline e aplica a unificação de nomes de clientes."""
    df = pd.read_csv(csv_path, dtype={"cod_produto": str, "mes": str, "ano": str},
                     parse_dates=["data_pedido"])
    return aplicar_alias(df)


def aplicar_alias(df: pd.DataFrame) -> pd.DataFrame:
    """Unifica variantes de nome do mesmo cliente (fonte: ALIAS_CLIENTES)."""
    if not df.empty and "cliente" in df.columns and ALIAS_CLIENTES:
        df = df.copy()
        df["cliente"] = df["cliente"].astype(str).str.strip().replace(ALIAS_CLIENTES)
    return df


def _clean_cod(raw) -> str:
    c = str(raw or "").strip()
    if c.lower() in ("nan", "none"):
        return ""
    if c.endswith(".0") and c[:-2].isdigit():
        c = c[:-2]                     # remove artefato de float (ex.: 0170.0)
    return c


# ── Construção dos blocos de dados ────────────────────────────────────────

def build_data(df: pd.DataFrame, include_comissao: bool = True) -> dict:
    """DATA do template: pedidos/orçamentos/cotações por empresa e cliente.
    include_comissao=False omite o campo 'cm' (versão diretoria)."""
    data = {"ibrac": defaultdict(list), "vh": defaultdict(list)}
    if df.empty:
        return {"ibrac": {}, "vh": {}}
    for keys, grp in df.groupby(
        ["empresa", "cliente", "file_name", "data_pedido", "mes", "tipo",
         "total_pedido", "pgto"], dropna=False
    ):
        empresa, cliente, _fn, dp, mes, tipo, tot, _pg = keys
        emp = emp_key(empresa)
        t = {"PEDIDO": "P", "ORCAMENTO": "O", "ORÇAMENTO": "O",
             "COTACAO": "C", "COTAÇÃO": "C"}.get(str(tipo).upper(), "P")
        cl = str(cliente).strip()
        if not cl or cl.lower() == "nan":
            continue
        prods = []
        for _, row in grp.iterrows():
            nome = str(row.get("nome_produto") or "").strip()
            if not nome or nome.lower() == "nan":
                continue
            p = {"c": _clean_cod(row.get("cod_produto")), "n": nome,
                 "q": int(row.get("qtd") or 0),
                 "p": float(row.get("preco_unit") or 0),
                 "v": float(row.get("valor_item") or 0)}
            if include_comissao:
                cm = row.get("comissao_rate")
                if pd.notna(cm) and float(cm) > 0:
                    p["cm"] = float(cm)
            prods.append(p)
        data[emp][cl].append({
            "t": t,
            "d": str(dp)[:10] if pd.notna(dp) else "",
            "m": str(mes) if pd.notna(mes) else "",
            "tot": float(tot) if pd.notna(tot) else 0.0,
            "p": prods,
        })
    for emp in data:
        for cl in data[emp]:
            data[emp][cl].sort(key=lambda x: x.get("d", "") or "", reverse=True)
        data[emp] = dict(data[emp])
    return data


def build_analysis(df: pd.DataFrame) -> dict:
    """ANALYSIS do template: histórico de produtos por cliente (gap, qtd média,
    valor médio, frequência, último mês, código)."""
    analysis = {"ibrac": {}, "vh": {}}
    if df.empty:
        return analysis
    cur_idx = mi(datetime.now().strftime("%Y-%m"))
    pedidos = df[df["tipo"] == "PEDIDO"]
    for (empresa, cliente), grp in pedidos.groupby(["empresa", "cliente"], dropna=False):
        emp = emp_key(empresa)
        ph = defaultdict(list)
        for _, row in grp.iterrows():
            n = str(row.get("nome_produto") or "").strip()
            m = str(row.get("mes") or "")
            q = float(row.get("qtd") or 0)
            v = float(row.get("valor_item") or 0)
            if n and n.lower() != "nan" and len(m) == 7 and q > 0 and v > 0:
                ph[n].append((m, q, v, _clean_cod(row.get("cod_produto"))))
        if not ph:
            continue
        prods = {}
        for prod, es in ph.items():
            es = sorted(es, key=lambda x: mi(x[0]))
            last = es[-1][0]
            gap = cur_idx - mi(last) if mi(last) >= 0 else 18
            cods = [e[3] for e in es if e[3]]
            prods[prod] = {
                "g": gap,
                "q": round(sum(e[1] for e in es) / len(es)),
                "v": round(sum(e[2] for e in es) / len(es), 2),
                "f": len(es), "lm": last,
                "c": cods[-1] if cods else "",
            }
        analysis[emp][str(cliente)] = prods
    return analysis


def build_locations(df: pd.DataFrame) -> dict:
    """LOCATIONS do template: cidade/estado por cliente (quando o CSV tiver)."""
    locs = {"ibrac": {}, "vh": {}}
    if df.empty or "cidade" not in df.columns:
        return locs
    for _, row in df.iterrows():
        emp = emp_key(row.get("empresa", ""))
        cl = str(row.get("cliente", "")).strip()
        cidade = str(row.get("cidade", "") or "").strip()
        estado = str(row.get("estado", "") or "").strip().upper()[:2]
        if cl and cidade and cl not in locs[emp]:
            locs[emp][cl] = {"cidade": cidade, "estado": estado}
    return locs


def build_carteira(df: pd.DataFrame, analysis: dict) -> dict:
    """CARTEIRA do template: positivação + curva ABC + painel de ação."""
    if gerar_analise_carteira is None or df.empty:
        return {}
    return gerar_analise_carteira(df, analysis)


# ── Injeção no template ────────────────────────────────────────────────────

def inject(html: str, const_name: str, obj) -> str:
    """Substitui `const NOME = {...};` no template pelo JSON do objeto."""
    js = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return re.sub(rf"const {const_name}\s*=\s*\{{.*?\}};",
                  f"const {const_name} = {js};", html, flags=re.DOTALL)


def fix_nbsp(html: str) -> str:
    """Escapa non-breaking spaces literais dentro do bloco <script>."""
    s0 = html.find("<script>") + 8
    s1 = html.rfind("</script>")
    if s0 > 8 and s1 > s0:
        html = html[:s0] + html[s0:s1].replace("\u00a0", "\\u00a0") + html[s1:]
    return html


def banner_html(now_str: str) -> str:
    """Barra fixa de 'última atualização' (mesma nos 3 dashboards)."""
    return (
        '<div id="lub" style="position:fixed;top:0;left:0;right:0;z-index:9999;'
        'background:#1a1a18;color:#fff;display:flex;align-items:center;'
        'justify-content:space-between;padding:7px 20px;'
        "font-family:DM Sans,sans-serif;font-size:12px;"
        'box-shadow:0 2px 8px rgba(0,0,0,.3)">'
        f'<span>&#128338;&nbsp;\u00daltima atualiza\u00e7\u00e3o: '
        f'<b style="color:#E1F5EE">{now_str}</b></span>'
        '<button onclick="document.getElementById(\'lub\').remove();'
        "document.body.style.paddingTop='0'\" "
        'style="background:none;border:none;color:#aaa;cursor:pointer;'
        'font-size:18px;line-height:1;padding:0">&#x2715;</button>'
        "</div>"
        "<style>body{padding-top:38px!important}</style>"
    )
