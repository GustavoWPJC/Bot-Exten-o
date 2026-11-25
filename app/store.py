"""Conversas em memória + avisos em tempo real (SSE). Trocar por banco/fila depois."""
import asyncio, time, threading
from .faq import normalizar

_lock = threading.Lock()
CONVS: dict = {}     # (empresa, cliente) -> conversa
LACUNAS: list = []   # perguntas que geraram handoff
_subs: dict = {}     # tópico -> {(loop, fila)}

# ---------- pub/sub ----------
def assinar(topico):
    q = asyncio.Queue()
    with _lock:
        _subs.setdefault(topico, set()).add((asyncio.get_running_loop(), q))
    return q

def cancelar(topico, q):
    with _lock:
        s = _subs.get(topico, set())
        s.difference_update({x for x in s if x[1] is q})

def _avisar(topico):
    with _lock:
        alvos = list(_subs.get(topico, ()))
    for loop, q in alvos:
        try:
            loop.call_soon_threadsafe(q.put_nowait, 1)   # pode vir de outra thread
        except RuntimeError:
            pass

def publicar(c: dict):
    _avisar(("painel", c["empresa"]))
    _avisar(("cliente", c["empresa"], c["cliente"]))

# ---------- conversas ----------
def _hora() -> str:
    return time.strftime("%H:%M:%S")

def obter(empresa: str, cliente: str) -> dict:
    with _lock:
        return CONVS.setdefault((empresa, cliente), {
            "empresa": empresa, "cliente": cliente, "status": "bot", "handoff": None,
            "atendente": None, "mensagens": [], "atualizada": 0})

def add(c: dict, autor: str, texto: str, origem: str | None = None):
    c["mensagens"].append({"autor": autor, "texto": texto, "origem": origem, "hora": _hora()})
    c["atualizada"] = time.time()
    publicar(c)

def handoff(c: dict, resumo: str, ultima: str):
    c["status"], c["handoff"] = "aguardando", resumo
    LACUNAS.append({"empresa": c["empresa"], "cliente": c["cliente"], "pergunta": ultima,
                    "resumo": resumo, "hora": _hora(), "resolvida": False})
    publicar(c)

def resolver_lacunas(empresa: str, perguntas: list[str]):
    alvo = {normalizar(p) for p in perguntas}
    for l in LACUNAS:
        if l["empresa"] == empresa and normalizar(l["pergunta"]) in alvo:
            l["resolvida"] = True
    _avisar(("painel", empresa))

def resumo(empresa: str) -> dict:
    with _lock:
        itens = list(CONVS.items())
    convs = sorted((c for (e, _), c in itens if e == empresa and c["mensagens"]),
                   key=lambda c: -c["atualizada"])
    cont = {"faq": 0, "ia": 0, "fallback": 0, "humano": 0}
    for c in convs:
        for m in c["mensagens"]:
            if m["autor"] == "bot":
                cont["faq" if m["origem"].startswith("faq") else m["origem"]] += 1
            elif m["autor"] == "atendente":
                cont["humano"] += 1
    return {"conversas": [dict(c, mensagens=list(c["mensagens"])) for c in convs],
            "metricas": cont,
            "lacunas": [l for l in LACUNAS if l["empresa"] == empresa and not l["resolvida"]][-20:]}
