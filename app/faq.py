import json, re, unicodedata
from pathlib import Path
from rapidfuzz import fuzz

ABREV = {"vc": "voce", "vcs": "voces", "oq": "o que", "q": "que", "qd": "quando",
         "qnd": "quando", "pq": "porque", "hj": "hoje", "hrs": "horas", "hr": "hora",
         "tb": "tambem", "td": "tudo", "msm": "mesmo", "mto": "muito", "ateh": "ate"}
SAUDACAO = re.compile(r"^(oi|ola|opa|e ai|bom dia|boa tarde|boa noite)\b[ ,]*")
FINAL = re.compile(r"\b(por favor|pfv|pf|obrigado|obg|valeu)$")

def normalizar(texto: str) -> str:
    """minúsculas, sem acento/pontuação, abreviações expandidas, letras repetidas reduzidas."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    t = " ".join(ABREV.get(w, w) for w in t.split())
    for _ in range(2):
        t = SAUDACAO.sub("", t).strip()
        t = FINAL.sub("", t).strip()
    return t

def carregar(caminho: Path) -> dict:
    entradas = json.loads(caminho.read_text(encoding="utf-8"))
    indice = [(normalizar(p), i) for i, e in enumerate(entradas) for p in e["perguntas"]]
    return {"entradas": entradas, "indice": indice, "caminho": caminho}

def _melhor(faq: dict, texto: str):
    q = normalizar(texto)
    melhor, idx = 0.0, None
    for variante, i in faq["indice"]:
        s = fuzz.token_sort_ratio(q, variante)
        if s > melhor:
            melhor, idx = s, i
    return q, melhor, idx

def responder(faq: dict, texto: str, limiar: float = 85, min_palavras: int = 2):
    """Resposta do FAQ se a pergunta for quase igual a uma conhecida; senão None."""
    q, score, idx = _melhor(faq, texto)
    if idx is None or len(q.split()) < min_palavras or score < limiar:
        return None
    return faq["entradas"][idx]["resposta"]

def top(faq: dict, texto: str, k: int = 2) -> list[str]:
    """Usado pela ferramenta do LLM: entradas mais parecidas (busca mais frouxa)."""
    q = normalizar(texto)
    pontos = {}
    for variante, i in faq["indice"]:
        pontos[i] = max(pontos.get(i, 0), fuzz.token_set_ratio(q, variante))
    ordem = sorted(pontos, key=pontos.get, reverse=True)
    return [faq["entradas"][i]["resposta"] for i in ordem[:k] if pontos[i] >= 50]

_REG: dict = {}

def obter(pasta: Path) -> dict:
    """Um único FAQ por empresa, compartilhado por todas as sessões (atualiza ao vivo)."""
    if pasta not in _REG:
        _REG[pasta] = carregar(pasta / "faq.json")
    return _REG[pasta]

def adicionar(faq: dict, perguntas: list[str], resposta: str):
    """Nova resposta automática. Cada forma de perguntar vira uma variante; o resto
    (erros de digitação, abreviações) é tratado pela normalização + busca aproximada."""
    vistos, unicas = set(), []
    for p in perguntas:
        n = normalizar(p)
        if n and n not in vistos:
            vistos.add(n); unicas.append(p.strip())
    if not unicas or not resposta.strip():
        raise ValueError("pergunta e resposta são obrigatórias")
    faq["entradas"].append({"perguntas": unicas, "resposta": resposta.strip()})
    i = len(faq["entradas"]) - 1
    faq["indice"].extend((normalizar(p), i) for p in unicas)
    faq["caminho"].write_text(json.dumps(faq["entradas"], ensure_ascii=False, indent=1), encoding="utf-8")
