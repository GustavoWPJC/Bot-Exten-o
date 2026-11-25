import logging, threading, time
from pathlib import Path
from rapidfuzz import fuzz, process
from . import faq as faq_mod
from .erp import ErpErro, criar_erp

log = logging.getLogger("uvicorn.error")
AFIRMA = ["sim", "s", "ss", "sin", "simm", "confirmo", "confirmado", "pode", "isso", "ok", "blz", "fechado",
          "certo", "correto", "positivo", "manda", "bora", "claro", "beleza"]
NEGA = {"nao", "cancela", "cancelar", "espera", "mudar", "alterar", "errado", "errada", "troca", "trocar"}
_RECENTES: dict = {}          # trava anti-duplicidade: mesmo pedido em 10 min devolve o mesmo resultado
_lock = threading.Lock()

def e_confirmacao(texto: str) -> bool:
    """Código (não a IA) decide se a mensagem atual é um 'sim' do cliente."""
    toks = faq_mod.normalizar(texto).split()
    if not toks or len(toks) > 6 or any(t in NEGA for t in toks):
        return False
    return any(t in AFIRMA or process.extractOne(t, AFIRMA, scorer=fuzz.ratio, score_cutoff=80) for t in toks)

def _falha(e):
    log.error("ERP: %s", e)
    return {"erro": "O sistema da empresa está indisponível agora. Peça desculpas e transfira para um atendente."}

def criar_ferramentas(pasta: Path, numero_cliente: str, erp=None):
    """Funções que o LLM pode chamar, presas à empresa e ao cliente DESTA conversa."""
    erp = erp or criar_erp(pasta)
    faq = faq_mod.obter(pasta)
    estado = {"handoff": None, "turno": 0, "msg": "", "rascunho": None, "cliente": None, "pedidos": []}

    def listar_produtos() -> dict:
        """Lista os produtos disponíveis (id, nome, preço em R$, estoque) e as formas de pagamento aceitas."""
        try:
            return {"produtos": erp.produtos(), "pagamentos": erp.pagamentos()}
        except ErpErro as e:
            return _falha(e)

    def identificar_cliente() -> dict:
        """Procura o cliente pelo número do WhatsApp desta conversa. Retorna nome e endereço salvo, se houver.
        Confirme o endereço com o cliente antes de usá-lo."""
        try:
            c = erp.buscar_cliente(numero_cliente)
        except ErpErro as e:
            return _falha(e)
        estado["cliente"] = c or False
        if c:
            return {"encontrado": True, "nome": c["nome"], "endereco": c["endereco"]}
        if "customers" in getattr(erp, "bloqueadas", ()):
            return {"encontrado": False, "consulta_indisponivel": True,
                    "instrucao": "Não há consulta de cadastro. Peça nome e endereço completos; não diga que o cadastro não existe."}
        return {"encontrado": False}

    def preparar_pedido(nome_cliente: str, produto_id: str, quantidade: int, pagamento: str, rua: str,
                        numero: str, bairro: str, cidade: str, estado_uf: str, complemento: str = "",
                        troco_para: float = 0.0) -> dict:
        """Valida os dados e guarda um RASCUNHO. NÃO cria o pedido. Depois mostre o resumo ao cliente e peça confirmação."""
        try:
            prod = next((p for p in erp.produtos() if p["id"] == str(produto_id)), None)
            formas = erp.pagamentos()
        except ErpErro as e:
            return _falha(e)
        if not prod:
            return {"erro": "Produto inexistente ou sem preço. Use listar_produtos."}
        if not 1 <= quantidade <= 10:
            return {"erro": "Quantidade deve ser entre 1 e 10."}
        if quantidade > prod["estoque"]:
            return {"erro": "Estoque insuficiente (restam {}).".format(prod["estoque"])}
        escolhida = next((f for f in formas if faq_mod.normalizar(f) == faq_mod.normalizar(pagamento)), None)
        if not escolhida:
            return {"erro": "Forma de pagamento inválida. Aceitas: {}".format(formas)}
        end = {"rua": rua, "numero": numero, "bairro": bairro, "cidade": cidade, "estado": estado_uf}
        faltam = [k for k, v in end.items() if not str(v).strip()]
        if faltam:
            return {"erro": "Faltam dados do endereço: {}".format(faltam)}
        total = round(prod["preco"] * quantidade, 2)
        if troco_para and troco_para < total:
            return {"erro": "O valor para troco deve ser maior ou igual ao total."}
        estado["rascunho"] = {"nome_cliente": nome_cliente, "produto_id": prod["id"], "produto_nome": prod["nome"],
                              "quantidade": quantidade, "preco": prod["preco"], "total": total,
                              "pagamento": escolhida, "troco_para": troco_para or 0,
                              "endereco": {**end, "complemento": complemento}, "turno": estado["turno"]}
        r = estado["rascunho"]
        return {"ok": True, "resumo": {"item": "{}x {}".format(quantidade, prod["nome"]), "total": total,
                                       "pagamento": escolhida, "endereco": r["endereco"]},
                "proximo_passo": "Mostre o resumo e peça confirmação. Só chame confirmar_pedido depois da resposta dele."}

    def confirmar_pedido() -> dict:
        """Cria o pedido no sistema. Só funciona se o cliente confirmou na mensagem atual o rascunho feito antes."""
        r = estado["rascunho"]
        if not r:
            return {"erro": "Não há pedido preparado. Use preparar_pedido."}
        if estado["turno"] <= r["turno"] or not e_confirmacao(estado["msg"]):
            return {"erro": "O cliente ainda não confirmou. Mostre o resumo e peça uma confirmação explícita."}
        chave = (str(pasta), numero_cliente, r["produto_id"], r["quantidade"], r["pagamento"],
                 faq_mod.normalizar(str(r["endereco"])))
        with _lock:
            antigo = _RECENTES.get(chave)
            if antigo and time.time() - antigo[0] < 600:
                return {**antigo[1], "repetido": True}
        try:
            if estado["cliente"] is None:
                estado["cliente"] = erp.buscar_cliente(numero_cliente) or False
            res = erp.criar_venda(r, estado["cliente"] or None, numero_cliente)
        except ErpErro as e:
            estado["handoff"] = "Falha ao registrar o pedido no sistema ({}). Rascunho: {}x {} = R$ {:.2f}, {}, {}".format(
                e, r["quantidade"], r["produto_nome"], r["total"], r["pagamento"], r["endereco"])
            return _falha(e)
        out = {"ok": True, "pedido": res["id"], "status": res["status"], "total": res["total"]}
        if res.get("simulada"):
            out = {"ok": True, "modo_teste": True, "total": res["total"],
                   "aviso": "MODO TESTE: nada foi enviado ao sistema da empresa. Diga ao cliente que é uma simulação."}
        if res.get("payload"):   # o painel mostra o que foi (ou seria) enviado à API
            estado["envio"] = {"simulada": bool(res.get("simulada")), "payload": res["payload"], "id": res.get("id")}
        if res.get("id"):
            estado["pedidos"].append(res["id"])
        estado["rascunho"] = None
        with _lock:
            _RECENTES[chave] = (time.time(), out)
        return out

    def consultar_pedido(id_pedido: int = 0) -> dict:
        """Consulta o status de um pedido do próprio cliente. Sem número, traz o mais recente."""
        bloq = lambda: "customers" in getattr(erp, "bloqueadas", ())
        try:
            r = None if bloq() else erp.consultar(numero_cliente, id_pedido)
            if r is None and bloq():       # sem cadastro de clientes: só pedidos feitos nesta conversa
                alvo = id_pedido or (estado["pedidos"][-1] if estado["pedidos"] else 0)
                if not alvo or alvo not in estado["pedidos"]:
                    return {"erro": "Só consigo consultar pedidos feitos nesta conversa. Transfira para um atendente."}
                r = erp.detalhar(alvo)
        except ErpErro as e:
            return _falha(e)
        return r or {"erro": "Pedido não encontrado para este cliente."}

    def buscar_faq(pergunta: str) -> dict:
        """Busca nas políticas e perguntas frequentes da empresa."""
        return {"trechos": faq_mod.top(faq, pergunta) or ["Nada encontrado."]}

    def transferir_para_humano(resumo: str) -> dict:
        """Transfere para um atendente humano, com um resumo da conversa."""
        estado["handoff"] = resumo
        return {"ok": True, "mensagem": "Atendente humano acionado."}

    return [listar_produtos, identificar_cliente, preparar_pedido, confirmar_pedido, consultar_pedido,
            buscar_faq, transferir_para_humano], estado
