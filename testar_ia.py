"""Testa só a chave e o modelo (inclusive uma chamada de ferramenta), sem subir o servidor:  python testar_ia.py"""
import os
from dotenv import load_dotenv

load_dotenv()
prov = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
print("Provedor:", prov)
try:
    if prov == "openai":
        from app.llm_openai import OpenAIChat
        modelo = os.getenv("OPENAI_MODEL", "gpt-5.5")
        def soma(a: int, b: int) -> int:
            """Soma dois números inteiros."""
            return a + b
        chat = OpenAIChat(modelo, "Use a ferramenta soma sempre que precisar somar.", [soma])
        r = chat.send_message("Quanto é 2 + 3? Use a ferramenta.")
        print("Modelo:", modelo, "| OK ->", r.text)
    else:
        from google import genai
        modelo = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
        r = genai.Client().models.generate_content(model=modelo, contents="Responda apenas: ok")
        print("Modelo:", modelo, "| OK ->", r.text)
except Exception as e:
    print("ERRO", getattr(e, "status_code", None) or getattr(e, "code", ""), "->", e)
