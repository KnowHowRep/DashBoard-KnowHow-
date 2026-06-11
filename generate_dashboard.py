"""
generate_dashboard.py — Roda no GitHub Actions.
Lê orders_database.csv + contacts.json + dashboard_template.html
e gera index.html com os dados injetados.
"""
import re, json, pandas as pd
from datetime import datetime
from collections import defaultdict
from pathlib import Path

CSV_PATH      = Path("orders_database.csv")
CONTACTS_PATH = Path("contacts.json")
TEMPLATE_PATH = Path("dashboard_template.html")
OUTPUT_PATH   = Path("index.html")

MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2028) for m in range(1, 13)]
def mi(m):
    try: return MONTHS.index(m)
    except: return -1

def build_data(df):
    data = {"ibrac": defaultdict(list), "vh": defaultdict(list)}
    if df.empty: return {"ibrac": {}, "vh": {}}
    for keys, grp in df.groupby(
        ["empresa","cliente","file_name","data_pedido","mes","tipo","total_pedido","pgto"],
        dropna=False
    ):
        empresa,cliente,fn,dp,mes,tipo,tot,pgto = keys
        emp = "ibrac" if str(empresa).lower() in ("ibrac condimentos","ibrac") else "vh"
        t = {"PEDIDO":"P","ORCAMENTO":"O","ORÇAMENTO":"O","COTACAO":"C","COTAÇÃO":"C"}.get(str(tipo).upper(),"P")
        cl = str(cliente).strip()
        if not cl or cl.lower() == "nan": continue
        prods = []
        for _, row in grp.iterrows():
            nome = str(row.get("nome_produto") or "").strip()
            if not nome or nome.lower() == "nan": continue
            p = {"c": str(row.get("cod_produto") or ""), "n": nome,
                 "q": int(row.get("qtd") or 0),
                 "p": float(row.get("preco_unit") or 0),
                 "v": float(row.get("valor_item") or 0)}
            cm = row.get("comissao_rate")
            if pd.notna(cm) and float(cm) > 0: p["cm"] = float(cm)
            prods.append(p)
        data[emp][cl].append({
            "t": t,
            "d": str(dp)[:10] if pd.notna(dp) else "",
            "m": str(mes) if pd.notna(mes) else "",
            "tot": float(tot) if pd.notna(tot) else 0.0,
            "p": prods
        })
    for emp in data:
        for cl in data[emp]:
            data[emp][cl].sort(key=lambda x: x.get("d","") or "", reverse=True)
        data[emp] = dict(data[emp])
    return data

def build_analysis(df):
    analysis = {"ibrac": {}, "vh": {}}
    if df.empty: return analysis
    cur_idx = mi(datetime.now().strftime("%Y-%m"))
    for (empresa, cliente), grp in df[df["tipo"]=="PEDIDO"].groupby(
        ["empresa","cliente"], dropna=False
    ):
        emp = "ibrac" if str(empresa).lower() in ("ibrac condimentos","ibrac") else "vh"
        ph = defaultdict(list)
        for _, row in grp.iterrows():
            n = str(row.get("nome_produto") or "").strip()
            m = str(row.get("mes") or "")
            q = float(row.get("qtd") or 0)
            v = float(row.get("valor_item") or 0)
            if n and n.lower() != "nan" and len(m) == 7 and q > 0 and v > 0:
                ph[n].append((m, q, v))
        if not ph: continue
        prods = {}
        for prod, es in ph.items():
            es = sorted(es, key=lambda x: mi(x[0]))
            last = es[-1][0]
            gap = cur_idx - mi(last) if mi(last) >= 0 else 18
            prods[prod] = {
                "g": gap,
                "q": round(sum(e[1] for e in es) / len(es)),
                "v": round(sum(e[2] for e in es) / len(es), 2),
                "f": len(es), "lm": last
            }
        analysis[emp][str(cliente)] = prods
    return analysis

def main():
    print("Lendo CSV...")
    if not CSV_PATH.exists():
        print("ERRO: orders_database.csv nao encontrado")
        return
    df = pd.read_csv(CSV_PATH, dtype={"cod_produto": str, "mes": str, "ano": str},
                     parse_dates=["data_pedido"])
    print(f"  {len(df)} linhas, {df['cliente'].nunique()} clientes")

    DATA_JS     = json.dumps(build_data(df),     ensure_ascii=False, separators=(",",":"))
    ANALYSIS_JS = json.dumps(build_analysis(df), ensure_ascii=False, separators=(",",":"))

    contacts = {}
    if CONTACTS_PATH.exists():
        try:
            contacts = json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
            print(f"  Contatos: {len(contacts)} registros")
        except Exception as e:
            print(f"  Erro ao ler contacts.json: {e}")
    CONTACTS_JS = json.dumps(contacts, ensure_ascii=False, separators=(",",":"))

    print("Carregando template...")
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = re.sub(r"const DATA\s*=\s*\{.*?\};",     f"const DATA = {DATA_JS};",     html, flags=re.DOTALL)
    html = re.sub(r"const ANALYSIS\s*=\s*\{.*?\};", f"const ANALYSIS = {ANALYSIS_JS};", html, flags=re.DOTALL)
    html = re.sub(r"const CONTACTS\s*=\s*\{.*?\};", f"const CONTACTS = {CONTACTS_JS};", html, flags=re.DOTALL)

    s0 = html.find("<script>") + 8
    s1 = html.rfind("</script>")
    html = html[:s0] + html[s0:s1].replace("\u00a0", "\\u00a0") + html[s1:]

    now_str = datetime.now().strftime("%d/%m/%Y \u00e0s %H:%M UTC")
    banner = (
        '<div id="lub" style="position:fixed;top:0;left:0;right:0;z-index:9999;'
        'background:#1a1a18;color:#fff;display:flex;align-items:center;'
        'justify-content:space-between;padding:7px 20px;'
        'font-family:DM Sans,sans-serif;font-size:12px;'
        'box-shadow:0 2px 8px rgba(0,0,0,.3)">'
        f'<span>&#128338;&nbsp;\u00daltima atualiza\u00e7\u00e3o: '
        f'<b style="color:#E1F5EE">{now_str}</b></span>'
        '<button onclick="document.getElementById(\'lub\').remove();'
        'document.body.style.paddingTop=\'0\'" '
        'style="background:none;border:none;color:#aaa;cursor:pointer;'
        'font-size:18px;line-height:1;padding:0">&#x2715;</button>'
        '</div>'
        '<style>body{padding-top:38px!important}</style>'
    )
    html = html.replace("<body>", "<body>\n" + banner + "\n", 1)

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado: {OUTPUT_PATH} ({len(html)//1024} KB)")

if __name__ == "__main__":
    main()
