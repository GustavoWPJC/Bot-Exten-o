"""Testa o FAQ sem usar a IA:  python testar_faq.py revgas "qual o horaro de funsionamento" """
import sys
from pathlib import Path
from app import faq

empresa = sys.argv[1] if len(sys.argv) > 1 else "revgas"
f = faq.carregar(Path("data") / empresa / "faq.json")
frases = sys.argv[2:] or ["qual o horaro de funsionamento", "vcs abrem domingo?", "aceitam pix?",
                         "Quero 2 botijoes", "pix"]
for t in frases:
    _, score, _ = faq._melhor(f, t)
    r = faq.responder(f, t)
    print(f"[{'FAQ' if r else 'IA '}] {score:5.1f}  {t!r}" + (f"\n        -> {r}" if r else ""))
