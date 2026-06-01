# save as check_models.py and run: python check_models.py
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

models = client.models.list()
gpt_models = sorted(
    [m.id for m in models.data if "gpt" in m.id],
    reverse=True
)
for m in gpt_models:
    print(m)