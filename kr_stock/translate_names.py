"""Translate Korean stock names to English and Chinese using DeepSeek LLM.

LLM provides context-aware translations, avoiding the literal/awkward results
that Google Translate produces for proper nouns and company names.
"""
import duckdb
import json
import time
from openai import OpenAI

DB_PATH = "/Users/a80460/Desktop/cibo datahub/kr_stock/kr_stock.duckdb"
BATCH_SIZE = 50

DEEPSEEK_KEY = "sk-e62bc33301744b6f9bc6fae9a98c1962"
DEEPSEEK_URL = "https://api.deepseek.com"

SYSTEM_PROMPT = """You are a financial translator specializing in Korean stock market.
Translate the following Korean company names into both English and Chinese (Simplified).

Rules:
- For well-known companies (Samsung, Hyundai, LG, SK, etc.), use official English names
- For subsidiaries, preserve the group name (e.g. "Samsung SDI" not "Samsung Sdi")
- For financial firms, use standard naming: "Securities", "Insurance", "Bank", "Asset Management"
- For "우" (preferred shares), add " (Preferred)"
- Chinese translations should follow common Chinese financial media usage
- Do NOT add "Co., Ltd.", "Inc.", "Corporation" unless it's part of the official name
- Return ONLY valid JSON, no markdown, no explanation

Example input: ["삼성전자", "SK하이닉스", "현대차"]
Example output: {"삼성전자": {"en": "Samsung Electronics", "zh": "三星电子"}, "SK하이닉스": {"en": "SK Hynix", "zh": "SK海力士"}, "현대차": {"en": "Hyundai Motor", "zh": "现代汽车"}}"""


def main():
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_URL)
    conn = duckdb.connect(DB_PATH)

    rows = conn.execute("""
        SELECT code, name FROM kr_listed_stocks
        WHERE name_en IS NULL OR name_zh IS NULL
        ORDER BY market_cap DESC
    """).fetchall()

    print(f"Total to translate: {len(rows)}")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        name_map = {r[1]: r[0] for r in batch}
        names = list(name_map.keys())

        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(names, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            content = resp.choices[0].message.content.strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            translations = json.loads(content)
        except Exception as e:
            pct = min(i + BATCH_SIZE, len(rows))
            print(f"  Batch error at {pct}: {e}")
            time.sleep(5)
            continue

        for ko_name, trans in translations.items():
            code = name_map.get(ko_name)
            if not code:
                continue
            en = trans.get("en", "").strip() if trans.get("en") else None
            zh = trans.get("zh", "").strip() if trans.get("zh") else None
            if en:
                conn.execute(
                    "UPDATE kr_listed_stocks SET name_en = ? WHERE code = ?",
                    [en, code],
                )
            if zh:
                conn.execute(
                    "UPDATE kr_listed_stocks SET name_zh = ? WHERE code = ?",
                    [zh, code],
                )

        if (i // BATCH_SIZE) % 5 == 0:
            pct = min(i + BATCH_SIZE, len(rows))
            print(f"  {pct}/{len(rows)}")
            # Save checkpoint every 5 batches
            conn.execute("CHECKPOINT")

        time.sleep(1)

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
