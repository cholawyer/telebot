import requests
import json

GEMINI_API_KEY = "AIzaSyDcrsDsXuL2NphD7hgGzopG893LKkFC6rc"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

BOT_TOKEN = "8769818135:AAF5k5vv5F-IaxVg9Y6l925TL910YHn5hYM"
CHAT_ID = 508862099
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_LEN = 4000

def send_message(text):
    requests.post(f"{API_URL}/sendMessage", data={
        "chat_id": CHAT_ID,
        "text": text
    }, timeout=10)

def summarize(text, max_len=MAX_LEN):
    if len(text) <= max_len:
        return text
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    result = []
    total = 0
    for p in paragraphs:
        if total + len(p) + 1 > max_len - 100:
            result.append("...(이하 생략)")
            break
        result.append(p)
        total += len(p) + 1
    return '\n'.join(result)

def fetch_gemini():
    print("Gemini API 호출 중...")
    prompt = (
        "오늘 날짜 기준으로 글로벌 및 국내 주요 경제·주식 시장 동향을 분석해줘. "
        "MarketWatch, 한국경제, 매일경제 등의 최신 뉴스를 참고해서 "
        "핵심 이슈, 주요 지수 흐름, 투자자 유의사항을 간결하게 요약해줘. "
        "한국어로 작성하고, 항목별로 구분해서 읽기 쉽게 정리해줘."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    try:
        res = requests.post(GEMINI_URL, json=payload, timeout=30)
        data = res.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip()
    except Exception as e:
        print(f"Gemini API 오류: {e}")
        return None

if __name__ == "__main__":
    text = fetch_gemini()
    if text:
        summary = summarize(text)
        send_message("📈 오늘의 주식시장 분석\n\n" + summary)
        print("전송 완료")
    else:
        send_message("❌ Gemini 주식 분석을 가져오지 못했어요.")
        print("실패")
