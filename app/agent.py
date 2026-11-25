import json, logging, os, random, time
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors
from .tools import criar_ferramentas
from .llm_openai import OpenAIChat
from . import faq as faq_mod

load_dotenv()
log = logging.getLogger("uvicorn.error")   # aparece no terminal do uvicorn
DATA = Path(__file__).parent.parent / "data"
MODELO = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
_gemini = None          # cliente criado só se o provedor for o Gemini
_sessoes: dict = {}      # (empresa, sessao) -> (chat, estado)
_faqs: dict = {}         # empresa -> FAQ carregado

def empresas() -> list[str]:
    return sorted(p.name for p in DATA.iterdir() if p.is_dir())

def _faq(empresa: str) -> dict:
    if empresa not in _faqs:
        _faqs[empresa] = faq_mod.obter(DATA / empresa)
    return _faqs[empresa]

def _prompt(cfg: dict) -> str:
    regras = "\n".join(f"- {r}" for r in cfg["regras"])
    return (
        f"Você é o assistente de atendimento e vendas da {cfg['nome']} ({cfg['segmento']}).\n"
        f"Tom: {cfg['tom']}. Responda em português do Brasil.\n"
        "Use SEMPRE as ferramentas para preços, estoque, pedidos e políticas. "
        "Nunca invente informações; se não souber, transfira para um humano.\n"
        "Fluxo de venda: (1) identificar_cliente e, se houver endereço salvo, confirme-o; (2) listar_produtos; "
        "(3) colete produto, quantidade, forma de pagamento e endereço; (4) preparar_pedido e mostre o resumo "
        "pedindo confirmação; (5) só depois do 'sim' chame confirmar_pedido. Nunca diga que o pedido foi feito "
        "antes de confirmar_pedido retornar ok. Se uma ferramenta der erro, não invente: transfira para um humano.\n"
        f"Regras da empresa:\n{regras}"
    )

def _provedor() -> str:
    return os.getenv("LLM_PROVIDER", "gemini").strip().lower()   # gemini | openai

def _nova_sessao(empresa: str, sessao: str):
    global _gemini
    pasta = DATA / empresa
    cfg = json.loads((pasta / "empresa.json").read_text(encoding="utf-8"))
    tools, estado = criar_ferramentas(pasta, sessao)
    if _provedor() == "openai":
        chat = OpenAIChat(os.getenv("OPENAI_MODEL", "gpt-5.5"), _prompt(cfg), tools)
    else:
        if _gemini is None:
            _gemini = genai.Client()   # lê GEMINI_API_KEY
        chat = _gemini.chats.create(
            model=MODELO,
            config=types.GenerateContentConfig(system_instruction=_prompt(cfg), tools=tools, temperature=0.3))
    return chat, estado

TENTATIVAS = int(os.getenv("IA_TENTATIVAS", "4"))
TRANSITORIOS = (429, 500, 503, 504)   # só estes valem a pena repetir

def _enviar(chat, mensagem):
    for i in range(TENTATIVAS):
        try:
            return chat.send_message(mensagem)
        except Exception as e:   # vale para Gemini e OpenAI
            codigo = getattr(e, "status_code", None) or getattr(e, "code", None)
            repete = codigo in TRANSITORIOS and i < TENTATIVAS - 1
            log.warning("IA erro %s (tentativa %d/%d, %s): %s", codigo, i + 1, TENTATIVAS,
                        "repetindo" if repete else "desistindo", str(e)[:300])
            if not repete:
                raise
            time.sleep(1.5 * 2 ** i + random.random())   # ~1,5s, 3s, 6s (+ aleatório)

def responder(empresa: str, sessao: str, mensagem: str) -> dict:
    faq = _faq(empresa)

    # 1) FAQ primeiro: instantâneo, de graça e não depende da IA
    r = faq_mod.responder(faq, mensagem)
    if r:
        return {"resposta": r, "handoff": None, "origem": "faq"}

    # 2) IA (com memória de conversa e ferramentas do ERP)
    chave = (empresa, sessao)
    estado = {"handoff": None}
    try:
        if chave not in _sessoes:   # pode falhar (ex.: token da RevGás ausente): cai no plano B
            _sessoes[chave] = _nova_sessao(empresa, sessao)
        chat, estado = _sessoes[chave]
        estado["turno"] += 1; estado["msg"] = mensagem   # o código usa isto para validar confirmações
        estado["handoff"] = None  # só vale o handoff desta mensagem
        resp = _enviar(chat, mensagem)
        return {"resposta": resp.text or "Pode repetir, por favor?",
                "handoff": estado["handoff"], "origem": "ia", "envio": estado.pop("envio", None)}
    except Exception:   # API fora do ar, sem internet, chave inválida, modelo inexistente...
        log.exception("Falha na IA; acionando plano B")
        # 3) IA fora do ar: tenta o FAQ com critério mais frouxo; senão, humano
        r = faq_mod.responder(faq, mensagem, limiar=60, min_palavras=1)
        if r:
            return {"resposta": r, "handoff": None, "origem": "faq-fallback"}
        return {"resposta": "Estou com uma instabilidade no momento. Vou chamar um atendente para te ajudar! 🙏",
                "handoff": f"IA indisponível. Última mensagem do cliente: {mensagem}",
                "origem": "fallback"}
