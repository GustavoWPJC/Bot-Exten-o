"""Chat da OpenAI (API Responses) com o mesmo formato do chat do Gemini: send_message(texto) -> objeto com .text.
As funções de tools.py viram ferramentas e o laço 'IA pede ferramenta -> executamos -> devolvemos' é manual."""
import inspect, json
from types import SimpleNamespace

TIPOS = {int: "integer", float: "number", bool: "boolean", str: "string"}
MAX_RODADAS = 8   # trava contra laço infinito de ferramentas

def esquema(f) -> dict:
    """Converte uma função Python (com type hints e docstring) em ferramenta da API."""
    props, obrigatorios = {}, []
    for nome, p in inspect.signature(f).parameters.items():
        props[nome] = {"type": TIPOS.get(p.annotation, "string")}
        if p.default is inspect.Parameter.empty:
            obrigatorios.append(nome)
    return {"type": "function", "name": f.__name__, "description": inspect.getdoc(f) or "",
            "parameters": {"type": "object", "properties": props, "required": obrigatorios,
                           "additionalProperties": False},
            "strict": False}   # permite parâmetros opcionais

class OpenAIChat:
    def __init__(self, modelo: str, instrucoes: str, ferramentas: list, cliente=None):
        if cliente is None:
            from openai import OpenAI
            cliente = OpenAI(timeout=30.0)   # lê OPENAI_API_KEY do ambiente
        self.client, self.modelo, self.instrucoes = cliente, modelo, instrucoes
        self.funcs = {f.__name__: f for f in ferramentas}
        self.tools = [esquema(f) for f in ferramentas]
        self.itens: list = []                # histórico da conversa (guardado aqui, não na OpenAI)

    def send_message(self, mensagem: str):
        self.itens.append({"role": "user", "content": mensagem})
        for _ in range(MAX_RODADAS):
            r = self.client.responses.create(
                model=self.modelo, instructions=self.instrucoes, input=self.itens, tools=self.tools,
                store=False, include=["reasoning.encrypted_content"])   # sem guardar a conversa na OpenAI
            self.itens += list(r.output)
            chamadas = [i for i in r.output if getattr(i, "type", "") == "function_call"]
            if not chamadas:
                return SimpleNamespace(text=r.output_text)
            for c in chamadas:
                try:
                    if c.name not in self.funcs:
                        raise KeyError("ferramenta desconhecida: " + c.name)
                    saida = self.funcs[c.name](**json.loads(c.arguments or "{}"))
                except Exception as e:   # erro da ferramenta volta para a IA em vez de derrubar a conversa
                    saida = {"erro": "chamada inválida: {}".format(e)}
                self.itens.append({"type": "function_call_output", "call_id": c.call_id,
                                   "output": json.dumps(saida, ensure_ascii=False, default=str)})
        return SimpleNamespace(text="Desculpe, não consegui concluir. Vou chamar um atendente.")
