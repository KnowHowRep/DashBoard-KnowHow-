"""
generate_dashboard.py — Roda no GitHub Actions (dashboard ONLINE).
Lê orders_database.csv + jsons + dashboard_template.html e gera index.html.
Lógica de dados no dashboard_core (commitar dashboard_core.py e
analise_carteira.py no repositório junto com este arquivo).
"""
import json
from datetime import datetime
from pathlib import Path

import dashboard_core as core

CSV_PATH      = Path("orders_database.csv")
CONTACTS_PATH = Path("contacts.json")
DEBTS_PATH    = Path("debts.json")
MSG_PATH      = Path("msg_timers.json")
LOC_PATH      = Path("locations.json")
TEMPLATE_PATH = Path("dashboard_template.html")
OUTPUT_PATH   = Path("index.html")


def _load_json(path: Path, nome: str) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Erro ao ler {nome}: {e}")
        return {}


def main():
    print("Lendo CSV...")
    if not CSV_PATH.exists():
        print("ERRO: orders_database.csv nao encontrado")
        return
    df = core.load_csv(CSV_PATH)
    print(f"  {len(df)} linhas, {df['cliente'].nunique()} clientes")

    analysis = core.build_analysis(df)

    print("Carregando template...")
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = core.inject(html, "DATA", core.build_data(df, include_comissao=True))
    html = core.inject(html, "ANALYSIS", analysis)
    html = core.inject(html, "CARTEIRA", core.build_carteira(df, analysis))
    html = core.inject(html, "CONTACTS", _load_json(CONTACTS_PATH, "contacts.json"))
    html = core.inject(html, "DEBTS", _load_json(DEBTS_PATH, "debts.json"))
    html = core.inject(html, "MSG_TIMERS", _load_json(MSG_PATH, "msg_timers.json"))
    html = core.inject(html, "LOCATIONS", _load_json(LOC_PATH, "locations.json"))
    html = core.fix_nbsp(html)

    now_str = datetime.now().strftime("%d/%m/%Y \u00e0s %H:%M UTC")
    html = html.replace("<body>", "<body>\n" + core.banner_html(now_str) + "\n", 1)

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado: {OUTPUT_PATH} ({len(html)//1024} KB)")


if __name__ == "__main__":
    main()
