"""SÓ LEITURA. Resumo da API da RevGás SEM dados pessoais (sem nomes, endereços ou telefones).
Uso (com o token no .env):  python scripts/descobrir_revgas.py"""
import collections, datetime as dt, json, re, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from app.erp_revgas import ErpRevgas

cfg = json.loads(Path("data/revgas/empresa.json").read_text(encoding="utf-8"))
erp = ErpRevgas(cfg, "simulado")

def secao(titulo, fn):
    print("\n=== " + titulo + " ===")
    try:
        fn()
    except Exception as e:
        print("  (falhou: {})".format(e))

def estabelecimentos():
    for e in erp._listar("/establishments", paginas=1):
        print("  id={} | {} | {}".format(e["id"], e.get("fantasy_name"), e.get("city")))

def produtos():
    for p in erp._listar("/products", {"establishment_id": erp.est}, paginas=2):
        print("  id={} | {} | estoque={}".format(p["id"], p.get("description"), p.get("stock_quantity")))

def pagamentos():
    for p in erp._listar("/payment-types", paginas=1):
        print("  {} | {}".format(p.get("payment_type"), p.get("status")))

def vendas():
    hoje = dt.date.today()
    v = erp._listar("/sales", {"establishment_id": erp.est, "from": (hoje - dt.timedelta(days=30)).isoformat(),
                               "to": hoje.isoformat()}, paginas=5)
    print("  vendas nos últimos 30 dias:", len(v))
    print("  status:", dict(collections.Counter(s.get("status") for s in v)))
    print("  canais:", dict(collections.Counter(s.get("channel") for s in v)))
    print("  com entregador:", sum(1 for s in v if s.get("deliverer_id")), "| entregues:", sum(1 for s in v if s.get("delivered_at")))
    valores = collections.defaultdict(list)
    for s in v:
        for i in s.get("products", []):
            valores[(i.get("product_id"), i.get("product_description"))].append(i.get("value"))
    print("  VALOR por item (use para conferir a tabela de preços):")
    for (pid, desc), vs in valores.items():
        vs = [x for x in vs if x is not None]
        if vs:
            print("    {} | {}: n={} min={} mediana={} max={}".format(pid, desc, len(vs), min(vs), statistics.median(vs), max(vs)))

    mais = collections.Counter(i.get("product_id") for s in v for i in s.get("products", [])).most_common(1)
    if mais:
        pid, por_qtd = mais[0][0], collections.defaultdict(list)
        for s in v:
            for i in s.get("products", []):
                if i.get("product_id") == pid and i.get("value") is not None:
                    por_qtd[i.get("quantity")].append(i["value"])
        print("  'value' do item muda com a quantidade? (produto {}): se ficar parecido = preço unitário; se crescer = total".format(pid))
        for q, vs in sorted(por_qtd.items()):
            print("    quantidade {}: n={} mediana={}".format(q, len(vs), statistics.median(vs)))

def telefones():
    cl = erp._req("GET", "/customers", params={"page_size": 20}).get("data", [])
    pads = collections.Counter(re.sub(r"\d", "9", p) for c in cl for p in c.get("phones", []))
    print("  formatos de telefone (dígitos trocados por 9):", dict(pads))
    amostra = next((p for c in cl for p in c.get("phones", [])), None)
    if amostra:
        d = re.sub(r"\D", "", amostra)
        for rot, v in (("como cadastrado", amostra), ("só dígitos", d), ("sem 55", d[2:] if d.startswith("55") else d)):
            n = len(erp._req("GET", "/customers", params={"phone": v, "page_size": 5}).get("data", []))
            print("  filtro phone={!r:<12} -> {} resultado(s)".format(rot, n))

for t, f in (("ESTABELECIMENTOS (preencha establishment_id)", estabelecimentos), ("PRODUTOS", produtos),
             ("FORMAS DE PAGAMENTO", pagamentos), ("VENDAS RECENTES", vendas), ("TELEFONES", telefones)):
    secao(t, f)
