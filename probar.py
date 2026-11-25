import os, httpx
from dotenv import load_dotenv
load_dotenv()
h = {"Authorization": os.environ["REVGAS_API_TOKEN"], "Accept": "application/json"}
for r in ["/establishments", "/products", "/payment-types", "/customers", "/sales", "/payment-types", "/neighborhoods", "/fiscal/payment-types"]:
    x = httpx.get("https://api.revgas.com/v1" + r, params={"page_size": 1}, headers=h, timeout=10)
    print(x.status_code, "GET", r, "" if x.status_code == 200 else x.text[:80])