import os, httpx
from dotenv import load_dotenv
load_dotenv()
h = {"Authorization": os.environ["REVGAS_API_TOKEN"], "Accept": "application/json",
     "Content-Type": "application/json"}
testes = {"/sales": {"customer_id": "abc"}, "/customers": {"phones": "abc"}}   # INVÁLIDOS de propósito
for rota, corpo in testes.items():
    x = httpx.post("https://api.revgas.com/v1" + rota, json=corpo, headers=h, timeout=10)
    print(x.status_code, "POST", rota, x.text[:120])