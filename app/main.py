import asyncio, hmac, json, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from google.genai import errors
from . import agent, store, faq as faq_mod

BASE = Path(__file__).parent.parent
NUMEROS = json.loads((BASE / "data" / "numeros.json").read_text(encoding="utf-8"))
TOKENS: dict = {}   # token de sessão -> (empresa, nome do atendente)
app = FastAPI(title="Chatbot adaptativo")

class ChatIn(BaseModel):
    numero_empresa: str   # número do WhatsApp que o cliente chamou
    numero_cliente: str   # quem está falando (vira o ID da sessão)
    mensagem: str

class LoginIn(BaseModel):
    codigo: str
    usuario: str
    senha: str

class FaqIn(BaseModel):
    pergunta: str          # forma "limpa" da pergunta
    original: str = ""     # como o cliente escreveu (com os erros), também vira variante
    variacoes: str = ""    # outras formas de perguntar, uma por linha
    resposta: str
    cliente: str = ""
    enviar: bool = False   # enviar a resposta ao cliente agora

class AcaoIn(BaseModel):
    cliente: str
    acao: str             # assumir | responder | devolver
    texto: str = ""

def _cfg(empresa: str) -> dict:
    return json.loads((BASE / "data" / empresa / "empresa.json").read_text(encoding="utf-8"))

def _empresa(numero: str) -> str:
    e = NUMEROS.get(numero)
    if not e:
        raise HTTPException(404, "Número não cadastrado.")
    return e

def _sessao(token: str):
    s = TOKENS.get(token)
    if not s:
        raise HTTPException(401, "Sessão expirada.")
    return s

def _sse(topico, snapshot):
    """Mantém a conexão aberta e só envia dados quando algo muda (sem polling)."""
    async def gen():
        q = store.assinar(topico)
        try:
            yield f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n"
            while True:
                try:
                    await asyncio.wait_for(q.get(), 15)
                    while not q.empty():
                        q.get_nowait()
                    yield f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"   # mantém a conexão viva
        finally:
            store.cancelar(topico, q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ---------- páginas ----------
@app.get("/")
def painel_pagina():
    return FileResponse(BASE / "static" / "painel.html")

@app.get("/cliente")
def cliente_pagina():
    return FileResponse(BASE / "static" / "cliente.html")

@app.get("/simulador/contatos")   # só para o simulador de demonstração
def contatos():
    return [{"numero": n, "nome": _cfg(e)["nome"]} for n, e in NUMEROS.items()]

# ---------- canal do cliente ----------
@app.post("/chat")
def chat(d: ChatIn):
    empresa = _empresa(d.numero_empresa)
    c = store.obter(empresa, d.numero_cliente)
    store.add(c, "cliente", d.mensagem)
    if c["status"] != "bot":   # humano no comando: o bot fica calado
        return {"resposta": None, "handoff": None, "origem": "humano", "status": c["status"]}
    try:
        r = agent.responder(empresa, d.numero_cliente, d.mensagem)
    except errors.APIError:
        raise HTTPException(503, "Modelo sobrecarregado, tente novamente em instantes.")
    if r.get("envio"):
        c["envio"] = r["envio"]   # painel mostra o corpo da venda enviada/simulada
    store.add(c, "bot", r["resposta"], r["origem"])
    if r["handoff"]:
        store.handoff(c, r["handoff"], d.mensagem)
    return {**r, "status": c["status"]}

@app.get("/chat/{numero_empresa}/{cliente}/stream")
async def stream_cliente(numero_empresa: str, cliente: str):
    empresa = _empresa(numero_empresa)
    c = store.obter(empresa, cliente)
    return _sse(("cliente", empresa, cliente),
                lambda: {"status": c["status"], "mensagens": list(c["mensagens"])})

# ---------- painel (empresa vem do token, nunca do cliente da requisição) ----------
@app.post("/painel/login")
def login(d: LoginIn):
    emp, cfg = None, None
    for pasta in (BASE / "data").iterdir():
        if (pasta / "empresa.json").exists():
            c = _cfg(pasta.name)
            if c.get("codigo_acesso", "").lower() == d.codigo.strip().lower():
                emp, cfg = pasta.name, c
                break
    u = next((u for u in (cfg or {}).get("usuarios", [])
              if u["login"] == d.usuario.strip().lower()), None)
    if not u or not hmac.compare_digest(u["senha"].encode(), d.senha.encode()):
        raise HTTPException(401, "Credenciais inválidas.")   # mesma resposta para qualquer erro
    token = secrets.token_urlsafe(24)
    TOKENS[token] = (emp, u["nome"])
    return {"token": token, "empresa": cfg["nome"], "usuario": u["nome"]}

@app.post("/painel/sair")
def sair(x_token: str = Header("")):
    TOKENS.pop(x_token, None)
    return {"ok": True}

@app.get("/painel/stream")   # EventSource não envia headers, então o token vai na URL
async def stream_painel(token: str = ""):
    empresa, _ = _sessao(token)
    return _sse(("painel", empresa), lambda: store.resumo(empresa))

@app.post("/painel/faq")
def novo_faq(d: FaqIn, x_token: str = Header("")):
    empresa, nome = _sessao(x_token)
    perguntas = [p for p in [d.pergunta, d.original, *d.variacoes.splitlines()] if p.strip()]
    try:
        faq_mod.adicionar(faq_mod.obter(BASE / "data" / empresa), perguntas, d.resposta)
    except ValueError:
        raise HTTPException(400, "Informe a pergunta e a resposta.")
    store.resolver_lacunas(empresa, perguntas)
    if d.enviar and d.cliente:
        c = store.obter(empresa, d.cliente)
        c["status"], c["atendente"] = "atendente", nome
        store.add(c, "atendente", d.resposta.strip(), nome)
    return {"ok": True}

@app.post("/painel/acao")
def acao(a: AcaoIn, x_token: str = Header("")):
    empresa, nome = _sessao(x_token)
    c = store.obter(empresa, a.cliente)
    if a.acao == "assumir":
        c["status"], c["atendente"] = "atendente", nome
    elif a.acao == "responder" and a.texto.strip():
        c["status"], c["atendente"] = "atendente", nome
        store.add(c, "atendente", a.texto.strip(), nome)  # com WhatsApp: enviar também pela API aqui
    elif a.acao == "devolver":
        c["status"], c["handoff"], c["atendente"] = "bot", None, None
    else:
        raise HTTPException(400, "Ação inválida.")
    store.publicar(c)
    return {"status": c["status"]}
