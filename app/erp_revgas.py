"""Adaptador da API da RevGás. Regras de segurança:
 - só as rotas da lista PERMITIDAS podem ser chamadas (a API inteira tem certificados, financeiro etc.);
 - modo 'simulado' nunca grava; só 'real' faz POST;
 - POST nunca é repetido automaticamente;
 - o token vem do ambiente e nunca aparece em log ou erro."""
import datetime as dt, json, logging, os, re, time
from pathlib import Path
import httpx
from .erp import ErpErro, _digitos
from .faq import normalizar

log = logging.getLogger("uvicorn.error")
PERMITIDAS = {("GET", "/establishments"), ("GET", "/products"), ("GET", "/payment-types"),
              ("GET", "/customers"), ("GET", "/sales"), ("GET", "/sales/{id}"),
              ("POST", "/customers"), ("POST", "/sales")}
TIPOS = {"pix": "PIX", "dinheiro": "MONEY", "cartao de debito": "CARTAO_DE_DEBITO",
         "cartao de credito": "CARTAO_DE_CREDITO"}
NOMES = {"PIX": "pix", "MONEY": "dinheiro", "CARTAO_DE_DEBITO": "cartão de débito",
         "CARTAO_DE_CREDITO": "cartão de crédito"}
STATUS = {"OPEN": "recebido", "ACTIVE": "em andamento", "WARNING": "entregador avisado",
          "CONFIRM": "entregue", "CHECK": "conferido", "CANCEL": "cancelado"}   # a confirmar com a RevGás

class ErpRevgas:
    def __init__(self, cfg: dict, modo: str, http=None):
        e = cfg["erp"]
        self.modo, self.est = modo, int(e["establishment_id"])
        self.precos = {normalizar(k): float(v) for k, v in cfg.get("precos", {}).items()}
        self._cache = {}
        self.bloqueadas = set()   # recursos que a chave não pode acessar (descobertos ao usar)
        self.pag_cfg = list(cfg.get("pagamentos", list(NOMES.values())))
        if http is None:
            token = os.getenv(e.get("token_env", "REVGAS_API_TOKEN"), "")
            if not token:
                raise ErpErro("Defina REVGAS_API_TOKEN no .env")
            http = httpx.Client(base_url=e.get("base_url", "https://api.revgas.com/v1"), timeout=8.0,
                                headers={"Authorization": token, "Accept": "application/json"})
        self.http = http

    # ---------- HTTP ----------
    def _req(self, metodo, caminho, **kw):
        if (metodo, re.sub(r"/\d+$", "/{id}", caminho)) not in PERMITIDAS:
            raise ErpErro("Rota não permitida: {} {}".format(metodo, caminho))
        try:
            r = self.http.request(metodo, caminho, **kw)
        except httpx.HTTPError as ex:
            raise ErpErro("falha de rede ({})".format(type(ex).__name__)) from None
        if r.status_code >= 400:
            raise ErpErro("RevGás respondeu {} em {} {}: {}".format(r.status_code, metodo, caminho, r.text[:150]), status=r.status_code)
        return r.json()

    def _listar(self, caminho, params=None, paginas=5):
        out = []
        for pag in range(1, paginas + 1):
            j = self._req("GET", caminho, params={**(params or {}), "page": pag, "page_size": 50})
            out += j.get("data", [])
            if not j.get("pagination", {}).get("has_next"):
                break
        return out

    def _cache_get(self, chave, ttl):
        v = self._cache.get(chave)
        return v[1] if v and time.time() - v[0] < ttl else None

    # ---------- leitura ----------
    def produtos(self):
        c = self._cache_get("produtos", 60)
        if c is not None:
            return c
        out = []
        for p in self._listar("/products", {"establishment_id": self.est, "inactive": False}):
            preco = self.precos.get(str(p["id"]))   # por ID (nomes se repetem no catálogo)
            if preco is None:
                preco = self.precos.get(normalizar(p.get("description", "")))
            if preco is None:        # sem preço configurado: o bot não vende
                continue
            out.append({"id": str(p["id"]), "nome": p["description"], "preco": preco,
                        "estoque": p.get("stock_quantity") or 0})
        self._cache["produtos"] = (time.time(), out)
        return out

    def pagamentos(self):
        c = self._cache_get("pagamentos", 600)
        if c is not None:
            return c
        try:
            ativos = {i.get("payment_type") for i in self._listar("/payment-types") if i.get("status") == "ACTIVE"}
            out = [n for t, n in NOMES.items() if t in ativos]
        except ErpErro as e:
            if e.status not in (401, 403):
                raise
            self.bloqueadas.add("payment-types")   # chave sem permissão: usa a lista do empresa.json
            log.warning("Sem permissão em /payment-types; usando a lista do empresa.json")
            out = list(self.pag_cfg)
        self._cache["pagamentos"] = (time.time(), out)
        return out

    def buscar_cliente(self, telefone):
        if "customers" in self.bloqueadas:
            return None
        d = _digitos(telefone)
        try:
            for v in dict.fromkeys([d, d[2:] if d.startswith("55") else None, "+" + d]):
                if not v:
                    continue
                achados = self._req("GET", "/customers", params={"phone": v, "page_size": 3}).get("data", [])
                if len(achados) > 1:
                    return None          # ambíguo: não arrisca mostrar dados de outra pessoa
                if achados:
                    c = achados[0]
                    return {"id": c["id"], "nome": c.get("name"),
                            "endereco": {"rua": c.get("location") or "", "numero": c.get("number") or "",
                                         "bairro": c.get("neighborhood") or "", "cidade": c.get("city") or "",
                                         "estado": c.get("state_federation") or "",
                                         "complemento": c.get("complement") or ""}}
        except ErpErro as e:
            if e.status not in (401, 403):
                raise
            self.bloqueadas.add("customers")      # chave sem permissão em /customers
            log.warning("Sem permissão em /customers; seguindo sem cadastro de clientes")
        return None

    def _fmt(self, v):
        return {"id": v["id"], "status": STATUS.get(v.get("status"), v.get("status")), "total": v.get("total"),
                "itens": ", ".join("{}x {}".format(i.get("quantity"), i.get("product_description"))
                                   for i in v.get("products", [])),
                "entregador": v.get("deliverer_name"), "entregue_em": v.get("delivered_at")}

    def detalhar(self, id_pedido):
        """Sem checagem de dono: quem chama garante que o pedido foi criado na conversa."""
        return self._fmt(self._req("GET", "/sales/{}".format(int(id_pedido))))

    def consultar(self, telefone, id_pedido=0):
        cli = self.buscar_cliente(telefone)
        if not cli:
            return None
        if id_pedido:
            v = self._req("GET", "/sales/{}".format(int(id_pedido)))
            if v.get("customer_id") != cli["id"]:     # só o dono vê o pedido
                return None
        else:
            hoje = dt.date.today()
            lista = self._req("GET", "/sales", params={
                "customer_id": cli["id"], "from": (hoje - dt.timedelta(days=30)).isoformat(),
                "to": hoje.isoformat(), "page_size": 5}).get("data", [])
            if not lista:
                return None
            v = max(lista, key=lambda s: s["id"])
        return self._fmt(v)

    # ---------- escrita (só no modo real) ----------
    def criar_venda(self, r, cliente, telefone):
        a, tel = r["endereco"], "+" + _digitos(telefone)
        obs = "Pedido via assistente virtual (WhatsApp)."
        if r.get("troco_para"):
            obs += " Troco para R$ {:.2f}.".format(r["troco_para"])
        if not cliente:    # sem cadastro: nome e telefone vão na observação para o entregador
            obs += " Cliente: {}. Telefone: {}.".format(r["nome_cliente"], tel)
        payload = {
            "location": a["rua"], "number": str(a["numero"]), "neighborhood": a["bairro"], "city": a["cidade"],
            "state_federation": a["estado"], "complement": a.get("complemento", ""), "observation": obs,
            "establishment_id": self.est,
            "products": [{"product_id": int(r["produto_id"]), "quantity": r["quantidade"], "value": r["preco"]}],
            "payments": [{"payment_type": TIPOS[normalizar(r["pagamento"])], "value": r["total"],
                          "date": dt.date.today().isoformat()}],
            "channel": "WHATSAPP", "origin": "INTEGRATION", "option": "NORMAL", "broadcast": "NEW_SALE"}
        if cliente:
            payload["customer_id"] = cliente["id"]
        if self.modo != "real":
            texto = json.dumps(payload, ensure_ascii=False, indent=2)
            log.warning("[RevGás %s] venda NÃO enviada. Corpo do POST /sales:\n%s", self.modo, texto)
            pasta = Path(os.getenv("SAIDAS_DIR", "saidas")); pasta.mkdir(exist_ok=True)
            (pasta / "venda_simulada_{}.json".format(time.strftime("%Y%m%d_%H%M%S"))).write_text(texto, encoding="utf-8")
            return {"id": None, "status": "SIMULADA", "total": r["total"], "simulada": True, "payload": payload}
        if not cliente and "customers" not in self.bloqueadas:
            try:
                novo = self._req("POST", "/customers", json={
                    "name": r["nome_cliente"], "type": "PHYSICAL", "phones": [tel],
                    "location": a["rua"], "number": str(a["numero"]), "neighborhood": a["bairro"],
                    "city": a["cidade"], "state_federation": a["estado"], "complement": a.get("complemento", "")})
                if not novo.get("id"):
                    raise ErpErro("cadastro do cliente não devolveu id")
                payload["customer_id"] = novo["id"]
            except ErpErro as e:
                if e.status not in (401, 403):
                    raise
                self.bloqueadas.add("customers")      # sem permissão para cadastrar: segue sem cliente
        v = self._req("POST", "/sales", json=payload)
        return {"id": v.get("id"), "status": STATUS.get(v.get("status"), v.get("status")),
                "total": v.get("total", r["total"]), "simulada": False, "payload": payload}
