"""Camada de integração com o ERP de cada empresa. O resto do sistema só conhece esta interface:
produtos(), pagamentos(), buscar_cliente(tel), criar_venda(rascunho, cliente, tel), consultar(tel, id)."""
import json, os, re
from pathlib import Path

class ErpErro(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status   # código HTTP, quando houver

def _digitos(t) -> str:
    return re.sub(r"\D", "", t or "")

class ErpMock:
    """ERP de mentira, lido de data/<empresa>/erp.json. Nada sai do computador."""
    modo = "mock"

    def __init__(self, pasta: Path):
        self.d = json.loads((pasta / "erp.json").read_text(encoding="utf-8"))

    def produtos(self):
        return [{"id": str(p["id"]), "nome": p["nome"], "preco": p["preco"], "estoque": p["estoque"]}
                for p in self.d["produtos"]]

    def pagamentos(self):
        return list(self.d["pagamentos"])

    def buscar_cliente(self, telefone):
        for c in self.d.get("clientes", []):
            if _digitos(c["telefone"]) == _digitos(telefone):
                return {"id": c["telefone"], "nome": c["nome"], "endereco": c["endereco"]}
        return None

    def criar_venda(self, r, cliente, telefone):
        prod = next(p for p in self.d["produtos"] if str(p["id"]) == r["produto_id"])
        prod["estoque"] -= r["quantidade"]
        pedido = {"id": max(p["id"] for p in self.d["pedidos"]) + 1, "cliente": r["nome_cliente"],
                  "telefone": _digitos(telefone), "itens": "{}x {}".format(r["quantidade"], prod["nome"]),
                  "total": r["total"], "endereco": r["endereco"], "pagamento": r["pagamento"],
                  "status": "recebido", "eta_min": 20}
        self.d["pedidos"].append(pedido)
        return {"id": pedido["id"], "status": "recebido", "total": r["total"], "simulada": False}

    def consultar(self, telefone, id_pedido=0):
        ps = self.d["pedidos"]
        if id_pedido:
            return next((p for p in ps if p["id"] == id_pedido), None)
        meus = [p for p in ps if p.get("telefone") == _digitos(telefone)]
        return meus[-1] if meus else None

def criar_erp(pasta: Path):
    cfg = json.loads((pasta / "empresa.json").read_text(encoding="utf-8"))
    e = cfg.get("erp", {"tipo": "mock"})
    if e.get("tipo") == "revgas":
        modo = os.getenv(e.get("modo_env", "REVGAS_MODO"), "mock").strip().lower()
        if modo in ("simulado", "real"):
            from .erp_revgas import ErpRevgas
            return ErpRevgas(cfg, modo)
    return ErpMock(pasta)
