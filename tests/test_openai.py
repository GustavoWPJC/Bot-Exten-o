import json, sys
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.llm_openai import OpenAIChat, esquema
from app.tools import criar_ferramentas

class Falso:   # imita client.responses.create
    def __init__(self, respostas):
        self.respostas, self.chamadas = list(respostas), []
        self.responses = NS(create=self.create)
    def create(self, **kw): self.chamadas.append(kw); return self.respostas.pop(0)

def chamada(nome, args, cid="c1"): return NS(type="function_call", name=nome, arguments=json.dumps(args), call_id=cid)
def resp(output, texto=""): return NS(output=output, output_text=texto)

def t_esquema_das_ferramentas_reais():
    ts, _ = criar_ferramentas(Path("data/revgas"), "1")
    e = {f.__name__: esquema(f) for f in ts}
    p = e["preparar_pedido"]["parameters"]
    assert p["properties"]["quantidade"]["type"] == "integer" and p["properties"]["troco_para"]["type"] == "number"
    assert "complemento" not in p["required"] and "troco_para" not in p["required"] and "rua" in p["required"]
    assert e["preparar_pedido"]["strict"] is False and e["consultar_pedido"]["parameters"]["required"] == []
    assert all(x["description"] for x in e.values())

def t_laco_de_ferramentas():
    visto = []
    def soma(a: int, b: int) -> int:
        """Soma."""
        visto.append((a, b)); return a + b
    fake = Falso([resp([chamada("soma", {"a": 2, "b": 3})]), resp([], "É 5!")])
    chat = OpenAIChat("m", "instr", [soma], cliente=fake)
    r = chat.send_message("2+3?")
    assert r.text == "É 5!" and visto == [(2, 3)]
    assert fake.chamadas[0]["store"] is False and fake.chamadas[0]["instructions"] == "instr"
    saida = [i for i in chat.itens if isinstance(i, dict) and i.get("type") == "function_call_output"][0]
    assert saida["call_id"] == "c1" and saida["output"] == "5"
    assert fake.chamadas[1]["input"][-1]["type"] == "function_call_output", "resultado volta para a IA"

def t_memoria_entre_mensagens():
    fake = Falso([resp([], "oi"), resp([], "tchau")]); chat = OpenAIChat("m", "i", [], cliente=fake)
    chat.send_message("a"); chat.send_message("b")
    assert [i["content"] for i in fake.chamadas[1]["input"] if isinstance(i, dict) and "content" in i] == ["a", "b"]

def t_erro_de_ferramenta_nao_derruba():
    def f(x: int) -> int:
        """F."""
        return x
    fake = Falso([resp([chamada("inexistente", {})]), resp([chamada("f", {"errado": 1}, "c2")]), resp([], "ok")])
    chat = OpenAIChat("m", "i", [f], cliente=fake)
    assert chat.send_message("x").text == "ok"
    saidas = [i["output"] for i in chat.itens if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert all("erro" in s for s in saidas) and len(saidas) == 2

def t_trava_contra_laco_infinito():
    def f() -> int:
        """F."""
        return 1
    fake = Falso([resp([chamada("f", {})]) for _ in range(20)]); chat = OpenAIChat("m", "i", [f], cliente=fake)
    assert "atendente" in chat.send_message("x").text and len(fake.chamadas) == 8

if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("t_"): fn(); print("OK ", n)
    print("TODOS OS TESTES PASSARAM")
