import json, os, sys, tempfile
os.environ.setdefault("SAIDAS_DIR", tempfile.mkdtemp())   # testes não sujam a pasta do projeto
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.erp import ErpErro, ErpMock
from app.erp_revgas import ErpRevgas
from app import tools as T

CFG = {"erp": {"establishment_id": 1}, "precos": {"GLP 13KG": 125.0}}
END = {"rua": "Av. Joaquim Nelson", "numero": "48", "bairro": "Redonda", "cidade": "Teresina", "estado": "PI", "complemento": ""}
RASC = {"nome_cliente": "Carlos", "produto_id": "1", "produto_nome": "GLP 13KG", "quantidade": 2, "preco": 125.0,
        "total": 250.0, "pagamento": "dinheiro", "troco_para": 300.0, "endereco": END}
S: dict = {}

def fake(req: httpx.Request):
    S["chamadas"].append((req.method, req.url.path))
    if any(req.url.path.startswith("/v1" + b) for b in S.get("bloqueio", ())):
        return httpx.Response(403, json={"message": "User doesn't have access to this resource"})
    assert req.headers["authorization"] == "TOKEN_FAKE"
    p, q = req.url.path, dict(req.url.params)
    pag = {"has_next": False}
    if p == "/v1/products":
        return httpx.Response(200, json={"pagination": pag, "data": [
            {"id": 1, "description": "GLP 13KG", "stock_quantity": 10}, {"id": 2, "description": "SEM PRECO", "stock_quantity": 5}]})
    if p == "/v1/payment-types":
        return httpx.Response(200, json={"pagination": pag, "data": [
            {"payment_type": "PIX", "status": "ACTIVE"}, {"payment_type": "MONEY", "status": "ACTIVE"},
            {"payment_type": "CARTAO_DE_DEBITO", "status": "INACTIVE"}]})
    if p == "/v1/customers" and req.method == "GET":
        ph = q.get("phone")
        dados = {"+5586955554444": [{"id": 77, "name": "Carlos", "location": "Av. X", "number": "1", "phones": ["+5586955554444"]}],
                 "5586999990000": [{"id": 1}, {"id": 2}]}.get(ph, [])
        return httpx.Response(200, json={"pagination": pag, "data": dados})
    if p == "/v1/customers":
        S["clientes"].append(json.loads(req.content)); return httpx.Response(201, json={"id": 88})
    if p == "/v1/sales" and req.method == "POST":
        S["vendas"].append(json.loads(req.content))
        if S.get("falha_post"):
            return httpx.Response(500, json={"message": "erro interno"})
        return httpx.Response(201, json={"id": 900, "status": "OPEN", "total": 250})
    if p == "/v1/sales":
        return httpx.Response(200, json={"pagination": pag, "data": [{"id": 900, "status": "OPEN", "total": 250,
            "customer_id": 77, "products": [{"product_description": "GLP 13KG", "quantity": 2}], "deliverer_name": None}]})
    if p == "/v1/sales/900":
        return httpx.Response(200, json={"id": 900, "status": "WARNING", "total": 250, "customer_id": 77, "products": []})
    if p == "/v1/sales/901":
        return httpx.Response(200, json={"id": 901, "status": "OPEN", "customer_id": 5, "products": []})
    return httpx.Response(404, json={})

def novo(modo, falha_post=False, bloqueio=()):
    S.update(chamadas=[], vendas=[], clientes=[], falha_post=falha_post, bloqueio=set(bloqueio))
    http = httpx.Client(base_url="https://api.test/v1", headers={"Authorization": "TOKEN_FAKE"},
                        transport=httpx.MockTransport(fake))
    return ErpRevgas(CFG, modo, http=http)

def posts():
    return [c for c in S["chamadas"] if c[0] == "POST"]

def raises(fn, trecho=""):
    try:
        fn()
    except ErpErro as e:
        assert trecho in str(e), str(e); assert "TOKEN_FAKE" not in str(e); return
    raise AssertionError("deveria ter falhado")

def t_leitura():
    e = novo("simulado")
    assert [p["id"] for p in e.produtos()] == ["1"], "produto sem preço deve ser omitido"
    assert e.pagamentos() == ["pix", "dinheiro"], e.pagamentos()
def t_preco_por_id():
    e = novo("simulado"); e.precos = {"2": 9.9}
    assert [(p["id"], p["preco"]) for p in e.produtos()] == [("2", 9.9)], "preço por ID tem prioridade e filtra o resto"
def t_lista_branca():
    e = novo("real"); raises(lambda: e._req("GET", "/certificates"), "não permitida")
    raises(lambda: e._req("PUT", "/sales/9/cancel"), "não permitida"); assert S["chamadas"] == []
def t_simulado_nao_grava():
    e = novo("simulado"); r = e.criar_venda(RASC, None, "5586955554444")
    assert r["simulada"] and posts() == []
def t_real_cliente_novo():
    e = novo("real"); r = e.criar_venda(RASC, None, "5586955554444")
    assert [c[1] for c in posts()] == ["/v1/customers", "/v1/sales"] and r["id"] == 900
    v = S["vendas"][0]
    assert (v["channel"], v["origin"], v["option"], v["broadcast"]) == ("WHATSAPP", "INTEGRATION", "NORMAL", "NEW_SALE")
    assert v["customer_id"] == 88 and "deliverer_id" not in v
    assert v["products"] == [{"product_id": 1, "quantity": 2, "value": 125.0}]
    assert v["payments"][0]["payment_type"] == "MONEY" and "Troco" in v["observation"]
    assert S["clientes"][0]["phones"] == ["+5586955554444"]
def t_real_cliente_existente():
    e = novo("real"); c = e.buscar_cliente("5586955554444"); assert c["id"] == 77
    e.criar_venda(RASC, c, "5586955554444"); assert [x[1] for x in posts()] == ["/v1/sales"]
def t_falha_sem_repetir():
    e = novo("real", falha_post=True); raises(lambda: e.criar_venda(RASC, {"id": 77}, "1"), "500")
    assert len(posts()) == 1, "POST não pode ser repetido"
def t_cliente_ambiguo():
    assert novo("simulado").buscar_cliente("5586999990000") is None
def t_consulta_so_do_dono():
    e = novo("simulado")
    assert e.consultar("5586955554444", 900)["status"] == "entregador avisado"
    assert e.consultar("5586955554444", 901) is None
    assert e.consultar("5586955554444")["id"] == 900
    assert e.consultar("5586000000000") is None
def t_confirmacao_no_codigo():
    for ok in ("sim", "Confirmo!", "cofirmo", "pode confirmar", "sim, pode"): assert T.e_confirmacao(ok), ok
    for no in ("não", "não confirmo", "quero mudar o endereço", "", "sim mas troca o produto"): assert not T.e_confirmacao(no), no
def t_fluxo_ferramentas():
    T._RECENTES.clear()
    erp = ErpMock(Path("data/revgas"))
    ts, est = T.criar_ferramentas(Path("data/revgas"), "5586955554444", erp=erp)
    f = {x.__name__: x for x in ts}
    assert f["identificar_cliente"]()["nome"] == "Carlos"
    est["turno"] = 1; est["msg"] = "quero 2 botijões"
    args = dict(nome_cliente="Carlos", produto_id="p13", quantidade=2, pagamento="pix", rua="Av. Joaquim Nelson",
                numero="48", bairro="Redonda", cidade="Teresina", estado_uf="PI")
    assert f["preparar_pedido"](**args)["ok"]
    assert "erro" in f["confirmar_pedido"](), "mesmo turno não confirma"
    est["turno"] = 2; est["msg"] = "não confirmo"; assert "erro" in f["confirmar_pedido"]()
    est["turno"] = 3; est["msg"] = "sim, pode confirmar"; r = f["confirmar_pedido"](); assert r["ok"] and r["pedido"] == 1003, r
    assert "erro" in f["confirmar_pedido"](), "rascunho some depois de confirmado"
    est["turno"] = 4; assert f["preparar_pedido"](**args)["ok"]
    est["turno"] = 5; est["msg"] = "sim"; r2 = f["confirmar_pedido"](); assert r2["repetido"] and r2["pedido"] == 1003, r2
    assert "erro" in f["preparar_pedido"](**{**args, "quantidade": 50}) and "erro" in f["preparar_pedido"](**{**args, "pagamento": "bitcoin"})
    assert f["consultar_pedido"](1003)["status"] == "recebido"
def t_modo_teste_nas_ferramentas():
    T._RECENTES.clear(); erp = novo("simulado")
    ts, est = T.criar_ferramentas(Path("data/revgas"), "5586955554444", erp=erp); f = {x.__name__: x for x in ts}
    f["identificar_cliente"](); est["turno"] = 1
    assert f["preparar_pedido"](nome_cliente="Carlos", produto_id="1", quantidade=1, pagamento="pix", rua="A", numero="1",
                                bairro="B", cidade="C", estado_uf="PI")["ok"]
    est["turno"] = 2; est["msg"] = "sim"; r = f["confirmar_pedido"]()
    assert r["modo_teste"] and "pedido" not in r and posts() == []
    assert est["envio"]["simulada"] is True and est["envio"]["payload"]["channel"] == "WHATSAPP"
def t_falha_vira_handoff():
    T._RECENTES.clear(); erp = novo("real", falha_post=True)
    ts, est = T.criar_ferramentas(Path("data/revgas"), "5586955554444", erp=erp); f = {x.__name__: x for x in ts}
    f["identificar_cliente"](); est["turno"] = 1
    f["preparar_pedido"](nome_cliente="Carlos", produto_id="1", quantidade=1, pagamento="pix", rua="A", numero="1",
                         bairro="B", cidade="C", estado_uf="PI")
    est["turno"] = 2; est["msg"] = "sim"; r = f["confirmar_pedido"]()
    assert "erro" in r and "indisponível" in r["erro"] and est["handoff"] and "Rascunho" in est["handoff"]

def t_chave_limitada_adaptador():
    e = novo("real", bloqueio={"/payment-types", "/customers"})
    assert e.pagamentos() == ["pix", "dinheiro", "cartão de débito", "cartão de crédito"] and "payment-types" in e.bloqueadas
    assert e.buscar_cliente("5586955554444") is None and "customers" in e.bloqueadas
    n = len(S["chamadas"]); assert e.buscar_cliente("5586955554444") is None and len(S["chamadas"]) == n, "não insiste"
    e.criar_venda(RASC, None, "5586955554444")
    assert [c[1] for c in posts()] == ["/v1/sales"], posts()
    v = S["vendas"][0]; assert "customer_id" not in v and "Carlos" in v["observation"] and "+5586955554444" in v["observation"]
def t_cadastro_bloqueado_na_hora_da_venda():
    e = novo("real", bloqueio={"/customers"}); e.criar_venda(RASC, None, "5586955554444")
    assert [c[1] for c in posts()] == ["/v1/customers", "/v1/sales"] and "customer_id" not in S["vendas"][0]
def t_consulta_so_da_conversa():
    T._RECENTES.clear(); erp = novo("real", bloqueio={"/payment-types", "/customers"})
    ts, est = T.criar_ferramentas(Path("data/revgas"), "5586955554444", erp=erp); f = {x.__name__: x for x in ts}
    r = f["identificar_cliente"](); assert r["encontrado"] is False and r["consulta_indisponivel"]
    assert "erro" in f["consultar_pedido"](), "sem pedido na conversa"
    est["turno"] = 1
    assert f["preparar_pedido"](nome_cliente="Carlos", produto_id="1", quantidade=1, pagamento="pix", rua="A", numero="1",
                                bairro="B", cidade="C", estado_uf="PI")["ok"]
    est["turno"] = 2; est["msg"] = "sim"; assert f["confirmar_pedido"]()["pedido"] == 900 and est["pedidos"] == [900]
    assert f["consultar_pedido"]()["status"] == "entregador avisado"
    assert "erro" in f["consultar_pedido"](901), "pedido de outra pessoa"

if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("t_"):
            fn(); print("OK ", nome)
    print("TODOS OS TESTES PASSARAM")
