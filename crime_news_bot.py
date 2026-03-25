#!/usr/bin/env python3
# crime_news_bot.py - 형사사건 뉴스 분석 봇 (7시/20시)

import requests
import json
import os
import re
import time
import subprocess
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

# .env 파일 로드
def _load_env():
    env_path = os.path.expanduser("~/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

BOT_TOKEN = os.environ.get("CHOILAWYER_TOKEN", "")  # @Choilawyer_bot
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRIME_USERS_FILE = os.path.expanduser("~/crime_bot_users.json")
CRIME_OFFSET_FILE = os.path.expanduser("~/crime_bot_offset.json")
CRIME_HISTORY_FILE = os.path.expanduser("~/crime_news_history.json")
CRIME_SENT_FILE = os.path.expanduser("~/crime_bot_sent.json")
CRIME_TODAY_FILE = os.path.expanduser("~/crime_bot_today.json")
CRIME_PREDICTIONS_FILE = os.path.expanduser("~/crime_bot_predictions.json")
CRIME_SCORE_FILE = os.path.expanduser("~/crime_bot_score.json")
CRIME_USAGE_FILE = os.path.expanduser("~/crime_bot_usage.json")
DAILY_LIMIT = 5

def load_usage():
    if os.path.exists(CRIME_USAGE_FILE):
        with open(CRIME_USAGE_FILE) as f:
            return json.load(f)
    return {}

def save_usage(data):
    with open(CRIME_USAGE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def check_and_increment_usage(chat_id):
    """사용 횟수 확인 및 증가. 제한 초과 시 False 반환"""
    today = datetime.now().strftime("%Y-%m-%d")
    usage = load_usage()
    key = str(chat_id)
    if key not in usage or usage[key]["date"] != today:
        usage[key] = {"date": today, "count": 0}
    if usage[key]["count"] >= DAILY_LIMIT:
        return False
    usage[key]["count"] += 1
    save_usage(usage)
    return True

def get_remaining(chat_id):
    today = datetime.now().strftime("%Y-%m-%d")
    usage = load_usage()
    key = str(chat_id)
    if key not in usage or usage[key]["date"] != today:
        return DAILY_LIMIT
    return max(0, DAILY_LIMIT - usage[key]["count"])
# 판결 완료 기사 제외 키워드 (이미 결론 난 사건)
VERDICT_EXCLUDE = ["선고", "무죄", "유죄", "실형", "집행유예", "파기환송", "확정판결", "대법원 확정"]

# 형사 범죄 키워드
CRIME_KEYWORDS = [
    "살인", "강도", "성폭행", "성추행", "강간", "마약", "사기", "횡령", "뇌물",
    "폭행", "협박", "방화", "납치", "감금", "절도", "스토킹", "전자발찌",
    "구속", "기소", "징역", "실형", "무죄", "유죄", "영장", "검거", "체포"
]

# 뉴스 검색 쿼리
NEWS_QUERIES = [
    "검찰 경찰 수사 when:1d",
    "구속 영장 체포 기소 when:1d",
    "마약 사기 횡령 뇌물 when:1d",
    "살인 폭행 성범죄 강도 when:1d",
]


# ── 유저/전송 ─────────────────────────────────────────────────

def load_crime_users():
    if os.path.exists(CRIME_USERS_FILE):
        with open(CRIME_USERS_FILE) as f:
            return json.load(f)
    return {"allowed": [ADMIN_ID]}

def save_crime_users(users):
    with open(CRIME_USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def send_message(chat_id, text):
    try:
        requests.post(f"{API_URL}/sendMessage", data={
            "chat_id": chat_id, "text": text
        }, timeout=10)
    except Exception as e:
        print(f"전송 오류 ({chat_id}): {e}")

def broadcast(text):
    for uid in load_crime_users()["allowed"]:
        send_message(uid, text)
        time.sleep(0.3)

def load_offset():
    if os.path.exists(CRIME_OFFSET_FILE):
        with open(CRIME_OFFSET_FILE) as f:
            return json.load(f).get("offset")
    return None

def save_offset(offset):
    with open(CRIME_OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)

def crime_already_sent(slot):
    """slot: 'morning'(7시) 또는 'evening'(20시)"""
    if os.path.exists(CRIME_SENT_FILE):
        with open(CRIME_SENT_FILE) as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        return data.get(slot) == today
    return False

def mark_crime_sent(slot):
    data = {}
    if os.path.exists(CRIME_SENT_FILE):
        with open(CRIME_SENT_FILE) as f:
            data = json.load(f)
    data[slot] = datetime.now().strftime("%Y-%m-%d")
    with open(CRIME_SENT_FILE, "w") as f:
        json.dump(data, f)

# ── 당일 기사 목록 관리 ───────────────────────────────────────

def load_today_articles():
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(CRIME_TODAY_FILE):
        with open(CRIME_TODAY_FILE) as f:
            data = json.load(f)
        if data.get("date") == today:
            return data.get("articles", [])
    return []

def save_today_articles(articles):
    with open(CRIME_TODAY_FILE, "w") as f:
        json.dump({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "articles": articles
        }, f, ensure_ascii=False, indent=2)

def delete_today_article(num):
    """번호로 당일 기사 삭제. 히스토리에서도 제거(재선정 가능하게)"""
    articles = load_today_articles()
    target = next((a for a in articles if a["num"] == num), None)
    if not target:
        return None
    # 당일 목록에서 제거
    articles = [a for a in articles if a["num"] != num]
    save_today_articles(articles)
    # 히스토리에서도 제거 (재선정 허용)
    h = load_history()
    h["sent"] = [s for s in h["sent"] if s.get("url") != target.get("url")
                 and s.get("title", "")[:20] != target.get("title", "")[:20]]
    save_history(h)
    return target

def handle_pin(msg):
    """관리자가 pin하면 전체 브로드캐스트"""
    sender_id = msg.get("from", {}).get("id")
    print(f"[pin] sender_id={sender_id}, ADMIN_ID={ADMIN_ID}, msg keys={list(msg.keys())}")
    if sender_id != ADMIN_ID:
        print(f"[pin] 관리자 아님 - 무시")
        return
    pinned = msg.get("pinned_message", {})
    content = pinned.get("text") or pinned.get("caption", "")
    print(f"[pin] 내용: {content[:50] if content else '없음'}")
    if content:
        broadcast(f"📌 공지\n\n{content}")
        print(f"[pin] 공지 브로드캐스트 완료")

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    first_name = msg.get("from", {}).get("first_name", "사용자")

    if "pinned_message" in msg:
        handle_pin(msg)
        return

    users = load_crime_users()
    if text in ["/start", "/시작"]:
        if chat_id not in users["allowed"]:
            users["allowed"].append(chat_id)
            save_crime_users(users)
            send_message(ADMIN_ID, f"🔔 새 구독자: {first_name} ({chat_id})")
        intro = (
            "안녕하세요! ⚖️\n\n"
            "🤖 AI판사 봇 입니다.\n\n"
            "🔗 이슈가 되는 형사사건의 네이버 웹주소를 넣으면 판결 예측해 드립니다.\n"
            "   (클로드+재미나이) 유죄확률 + 예상형량을 전송합니다. (하루 최대 5건)\n\n"
            "랜딩페이지 : https://cholawyer.github.io/divine-justice/\n\n"
            "📩 대면 상담문의(유료) : chojh208@gmail.com\n\n"
            "\"이작 법무법인에서는 코인 등 최신 유형의 형사사건을 비롯\n"
            "성범죄, 음주운전 등 기본적인 형사사건도 성공적으로 수행 합니다\""
        )
        image_path = "/Users/limkipyo/Desktop/자료/1.JPG"
        try:
            with open(image_path, "rb") as img:
                requests.post(f"{API_URL}/sendPhoto", files={"photo": img},
                              data={"chat_id": chat_id, "caption": intro}, timeout=30)
        except:
            send_message(chat_id, intro)
    elif text == "/구독취소":
        if chat_id in users["allowed"] and chat_id != ADMIN_ID:
            users["allowed"].remove(chat_id)
            save_crime_users(users)
            send_message(chat_id, "구독이 취소되었습니다.")
    elif text == "/지금":
        send_message(chat_id, "⏳ 분석 중입니다... (2~3분 소요)")
        import threading
        threading.Thread(target=send_crime_update, daemon=True).start()
    elif text == "/목록":
        articles = load_today_articles()
        if not articles:
            send_message(chat_id, "오늘 전송된 기사가 없어요.")
        else:
            lines = ["📋 오늘 전송된 기사 목록"]
            for a in articles:
                lines.append(f"{a['num']}. {a['title'][:45]}")
            lines.append("\n삭제: 삭제1  삭제2  ...")
            send_message(chat_id, "\n".join(lines))
    else:
        # /삭제N 명령 처리
        m = re.match(r'^삭제\s*(\d+)$', text)
        if m:
            num = int(m.group(1))
            deleted = delete_today_article(num)
            if deleted:
                broadcast(f"🗑 {num}번 기사 삭제 완료\n「{deleted['title'][:50]}」\n\n/목록 으로 남은 기사 확인")
            else:
                send_message(chat_id, f"{num}번 기사를 찾을 수 없어요. /목록 으로 확인하세요.")
        # URL 감지 → 기사 분석
        elif re.search(r'https?://\S+', text):
            url = re.search(r'https?://\S+', text).group()
            send_message(chat_id, "⏳ 기사 분석 중입니다...")
            import threading
            threading.Thread(target=analyze_url, args=(chat_id, url), daemon=True).start()

def poll_messages():
    """봇 메시지 폴링"""
    offset = load_offset()
    while True:
        try:
            params = {"timeout": 10, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            res = requests.get(f"{API_URL}/getUpdates", params=params, timeout=15)
            for update in res.json().get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                if "message" in update:
                    handle_message(update["message"])
            time.sleep(0.5)
        except KeyboardInterrupt:
            time.sleep(2)
        except Exception as e:
            print(f"폴링 오류: {e}")
            time.sleep(1)


# ── 예측 저장 / 승패 집계 ────────────────────────────────────

def load_predictions():
    if os.path.exists(CRIME_PREDICTIONS_FILE):
        with open(CRIME_PREDICTIONS_FILE) as f:
            return json.load(f)
    return []

def save_predictions(preds):
    with open(CRIME_PREDICTIONS_FILE, "w") as f:
        json.dump(preds, f, ensure_ascii=False, indent=2)

def load_score():
    if os.path.exists(CRIME_SCORE_FILE):
        with open(CRIME_SCORE_FILE) as f:
            return json.load(f)
    return {"win": 0, "loss": 0}

def save_score(score):
    with open(CRIME_SCORE_FILE, "w") as f:
        json.dump(score, f, ensure_ascii=False)

def save_prediction(title, url, sentence_line):
    """예측 기사 저장 (나중에 1심 결과와 비교)"""
    preds = load_predictions()
    preds.append({
        "title": title,
        "url": url,
        "sentence": sentence_line,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "result": None
    })
    save_predictions(preds)

def _to_months(s):
    m_yr = re.search(r'(\d+)년', s)
    m_mo = re.search(r'(\d+)월', s)
    return (int(m_yr.group(1)) * 12 if m_yr else 0) + (int(m_mo.group(1)) if m_mo else 0)

def parse_prediction_range(sentence_line):
    """예측 형량 텍스트 → 구조화된 범위"""
    m = re.search(r'벌금\s*(\d+)[~～](\d+)만원', sentence_line)
    if m:
        return {"type": "벌금", "min": int(m.group(1)), "max": int(m.group(2))}
    m = re.search(r'벌금\s*(\d+)만원', sentence_line)
    if m:
        v = int(m.group(1))
        return {"type": "벌금", "min": v, "max": v}
    m = re.search(r'징역\s*([\d년월]+)[~～]([\d년월]+)', sentence_line)
    if m:
        return {"type": "징역", "min": _to_months(m.group(1)), "max": _to_months(m.group(2))}
    m = re.search(r'징역\s*([\d년월]+)', sentence_line)
    if m:
        v = _to_months(m.group(1))
        return {"type": "징역", "min": v, "max": v}
    return None

def extract_actual_sentence(title, summary):
    """판결 기사에서 실제 선고 형량 추출"""
    full = title + " " + summary
    if "무죄" in full:
        return {"type": "무죄", "value": 0, "text": "무죄"}
    m = re.search(r'벌금\s*(\d+)만원', full)
    if m:
        return {"type": "벌금", "value": int(m.group(1)), "text": f"벌금 {m.group(1)}만원"}
    m = re.search(r'징역\s*([\d년월\s]+?)(?:에\s*집행유예|집행유예|선고|확정|,|\.|$)', full)
    if m:
        months = _to_months(m.group(1))
        suspended = "집행유예" in full
        label = "집행유예" if suspended else "실형"
        return {"type": "징역", "value": months, "suspended": suspended,
                "text": f"징역 {m.group(1).strip()} {label}"}
    return None

def match_prediction(verdict_title, predictions):
    """판결 기사 제목과 기존 예측 매칭 (키워드 겹침 3개 이상)"""
    def kws(text):
        return set(re.findall(r'[가-힣]{2,}', text))
    verdict_kws = kws(verdict_title)
    best, best_score = None, 0
    for pred in predictions:
        if pred.get("result"):
            continue
        overlap = len(verdict_kws & kws(pred["title"]))
        if overlap > best_score:
            best_score, best = overlap, pred
    return best if best_score >= 3 else None

def compare_and_score(pred, actual):
    """예측 vs 실제 비교 → True=승, False=패, None=판단불가"""
    pred_range = parse_prediction_range(pred["sentence"])
    if not pred_range or not actual:
        return None
    if actual["type"] == "무죄":
        return pred_range["type"] == "무죄"
    if pred_range["type"] != actual["type"]:
        return False
    return pred_range["min"] <= actual["value"] <= pred_range["max"]

def check_verdict_articles(articles):
    """판결 기사 중 기존 예측과 매칭되면 승패 판정 후 전송"""
    verdict_articles = [
        a for a in articles
        if any(k in a["title"] for k in ["선고", "무죄", "실형", "집행유예", "확정"])
    ]
    if not verdict_articles:
        return

    preds = load_predictions()
    updated = False

    for article in verdict_articles:
        pred = match_prediction(article["title"], preds)
        if not pred:
            continue
        actual = extract_actual_sentence(article["title"], article.get("summary", ""))
        if not actual:
            continue

        result = compare_and_score(pred, actual)
        if result is None:
            continue

        score = load_score()
        if result:
            score["win"] += 1
            outcome = "✅ 예측 성공"
        else:
            score["loss"] += 1
            outcome = "❌ 예측 실패"
        save_score(score)

        pred["result"] = "win" if result else "loss"
        pred["actual"] = actual["text"]
        updated = True

        broadcast(
            f"⚖️ 1심 결과 확인\n"
            f"📰 {article['title'][:60]}\n\n"
            f"예측: {pred['sentence']}\n"
            f"실제: {actual['text']}\n\n"
            f"{outcome}\n"
            f"📊 누적 {score['win']}승 {score['loss']}패"
        )
        print(f"  판결 비교: {outcome} | 예측={pred['sentence']} 실제={actual['text']}")

    if updated:
        save_predictions(preds)


# ── 히스토리 (중복 방지) ──────────────────────────────────────

def load_history():
    if os.path.exists(CRIME_HISTORY_FILE):
        with open(CRIME_HISTORY_FILE) as f:
            return json.load(f)
    return {"sent": []}

def save_history(h):
    with open(CRIME_HISTORY_FILE, "w") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def cleanup_history():
    """14일 지난 히스토리 삭제"""
    h = load_history()
    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    h["sent"] = [s for s in h["sent"] if s.get("date", "2000-01-01") >= cutoff]
    save_history(h)

def is_sent(url, title):
    h = load_history()
    sent_urls = {s.get("url", "") for s in h["sent"]}
    sent_prefixes = {s.get("title", "")[:20] for s in h["sent"]}
    return url in sent_urls or title[:20] in sent_prefixes

def mark_sent(url, title):
    h = load_history()
    h["sent"].append({
        "url": url,
        "title": title,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
    })
    save_history(h)


# ── 뉴스 수집 ────────────────────────────────────────────────

def resolve_url(google_url):
    """Google News 리다이렉트 URL → 원본 기사 URL 해결"""
    try:
        resp = requests.head(google_url, allow_redirects=True, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        final = resp.url
        # Google 로그인 페이지로 리다이렉트된 경우 원래 URL 사용
        if "accounts.google" in final or "google.com/sorry" in final:
            return google_url
        return final
    except:
        return google_url

def parse_rss(content):
    """RSS XML 파싱"""
    try:
        root = ET.fromstring(content)
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            desc = re.sub(r'<[^>]+>', '', desc).strip()  # HTML 태그 제거
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            pub = item.findtext("pubDate", "")
            if title and link:
                items.append({
                    "title": title, "url": link,
                    "summary": desc, "source": source, "published": pub
                })
        return items
    except Exception as e:
        print(f"RSS 파싱 오류: {e}")
        return []

def fetch_crime_news():
    """Google News RSS에서 형사 뉴스 수집 후 인기도 순 정렬"""
    all_articles = []
    seen_urls = set()
    title_freq = {}  # 유사 제목 등장 횟수 → 인기도 proxy

    for query in NEWS_QUERIES:
        encoded = requests.utils.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            articles = parse_rss(resp.content)
            for a in articles:
                # 제목 정규화 (인기도 계산용)
                norm = re.sub(r'[^가-힣a-zA-Z]', '', a["title"])[:18]
                title_freq[norm] = title_freq.get(norm, 0) + 1
                a["norm"] = norm
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    all_articles.append(a)
            time.sleep(0.8)
        except Exception as e:
            print(f"뉴스 수집 오류 ({query[:15]}): {e}")

    # 인기도 점수 반영
    for a in all_articles:
        a["score"] = title_freq.get(a["norm"], 1)

    # 당일 날짜 필터링 (KST 기준)
    from email.utils import parsedate_to_datetime
    from datetime import timezone, timedelta
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    today_articles = []
    for a in all_articles:
        pub = a.get("published", "")
        try:
            pub_dt = parsedate_to_datetime(pub).astimezone(KST)
            pub_date = pub_dt.strftime("%Y-%m-%d")
        except:
            pub_date = ""
        if pub_date == today:
            today_articles.append(a)

    print(f"당일 기사: {len(today_articles)}개 / 전체: {len(all_articles)}개")

    # 형사 키워드 필터링
    filtered = [
        a for a in today_articles
        if any(k in (a["title"] + " " + a["summary"]) for k in CRIME_KEYWORDS)
    ]

    # 인기도 높은 순 정렬
    filtered.sort(key=lambda x: x["score"], reverse=True)
    print(f"수집된 형사 기사: {len(filtered)}개")
    return filtered

def is_sufficient(title, summary):
    """lbox 검색이 가능한 충분한 정보 여부 - 제목 기반으로도 판단"""
    full = title + " " + summary

    # 1) 구체적 범죄 유형 (제목에 있으면 충분)
    specific_crimes = [
        "살인", "강도", "성폭행", "성추행", "강간", "마약", "사기", "횡령",
        "뇌물", "폭행", "방화", "납치", "절도", "스토킹", "내란", "반란",
        "주가조작", "체포방해", "선거법위반", "선거법 위반", "위증", "탈세",
        "불법촬영", "딥페이크", "랜섬웨어", "보이스피싱", "추행", "강제추행",
        "공갈", "배임", "탈주", "도주", "음주운전", "뺑소니", "아동학대"
    ]
    has_crime = any(c in full for c in specific_crimes)

    # 2) 판결/구형/선고 정보가 제목에 있으면 그 자체로 충분
    verdict_signals = ["징역", "구형", "선고", "1심", "2심", "대법", "실형",
                       "집행유예", "무죄", "유죄", "법정구속", "구속기소", "기소"]
    has_verdict = any(v in title for v in verdict_signals)

    # 3) 피의자/피고인 특정 가능 여부 (제목 기준)
    has_subject = any(k in title for k in [
        "씨", "전 ", "前 ", "전직", "대통령", "의원", "검사", "판사",
        "경찰", "교사", "목사", "의사", "교수", "대표"
    ])

    # 기사 내용이 너무 없는 경우만 스킵 (사설, 해설 등)
    skip_patterns = ["사설", "칼럼", "오피니언", "사설", "팩트체크", "Q&A", "[Q&"]
    is_editorial = any(p in title for p in skip_patterns)
    if is_editorial:
        return False

    # 판결/구형 정보 있으면 바로 통과
    if has_crime and has_verdict:
        return True

    # 범죄 유형 + 피의자 특정 가능하면 통과
    if has_crime and has_subject:
        return True

    # 충분한 요약이 있으면 통과
    if has_crime and len(summary) > 60:
        return True

    return False


# ── 규칙 기반 유죄확률 추정 ──────────────────────────────────

def estimate_injury_sentence(full):
    """상해·폭행 사건 부상 정도별 전치 주수 + 형량 세분화"""
    # 중상해 (뇌손상·내출혈·장기파열·척추)
    if any(k in full for k in ["뇌손상", "뇌출혈", "뇌진탕", "내출혈", "장기파열", "척추", "사지마비", "식물인간"]):
        return "형법 제258조(중상해) 위반 전치 8주 이상 · 징역 1년~3년 (실형 가능)"
    # 골절 (코뼈·갈비뼈·손목·발목 등)
    if any(k in full for k in ["골절", "뼈", "늑골", "쇄골", "손목골절", "발목골절"]):
        return "형법 제257조(상해) 위반 전치 4~6주 · 벌금 300~500만원 또는 징역 6월(집행유예)"
    # 열상·봉합·치아파절
    if any(k in full for k in ["열상", "봉합", "치아", "파절", "찢김"]):
        return "형법 제257조(상해) 위반 전치 2~4주 · 벌금 100~300만원"
    # 타박상·멍·찰과상
    if any(k in full for k in ["타박", "멍", "찰과", "擦過", "찰상"]):
        return "형법 제260조(폭행) 위반 전치 1~2주 · 벌금 50~100만원 (초범 기소유예 가능)"
    return None


def estimate_verdict_gemini(title, summary):
    """Gemini API로 기사 내용 분석 후 유죄 확률 + 법조항 + 형량 반환"""
    prompt = (
        f"아래 형사사건 기사를 분석해서 딱 2줄만 출력해.\n\n"
        f"출력 형식:\n"
        f"1번째 줄: 유죄 확률: X%\n"
        f"2번째 줄: [해당 법조항] 위반 [예상 형량 범위] · [한줄 코멘트]\n\n"
        f"예시:\n"
        f"유죄 확률: 85%\n"
        f"도로교통법 위반 징역 6월~2년 · 초범 벌금·집행유예, 재범은 실형\n\n"
        f"규칙:\n"
        f"- 반드시 정확히 2줄만 출력, 부연설명 없음\n"
        f"- 유죄 확률은 실제 한국 법원 판례 통계 기반으로 판단\n"
        f"- 법조항은 실제 적용 가능한 한국 법률로\n"
        f"- 형량은 실제 선고 통계 범위로\n\n"
        f"기사 제목: {title}\n"
        f"기사 내용: {summary[:800]}"
    )
    try:
        res = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20
        )
        text = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    except Exception as e:
        print(f"Gemini 분석 실패: {e}")
    return None, None


def estimate_verdict(title, summary):
    """범죄 유형별 유죄 확률 + 법조항 + 실제 선고 통계 기반 형량"""
    # Gemini로 먼저 시도
    prob_line, sentence_line = estimate_verdict_gemini(title, summary)
    if prob_line and sentence_line:
        return prob_line, sentence_line

    # Gemini 실패 시 키워드 매칭 폴백
    # 범죄 유형 판단은 제목 우선, 본문은 앞 300자만 사용 (관련기사 오염 방지)
    full = title + " " + summary[:300]
    # (키워드, 유죄확률, 법조항, 실통계형량, 코멘트)
    CRIME_TABLE = [
        (["살인", "살해", "피살"],
            95, "형법 제250조", "징역 20년~무기", "초범도 실형, 계획적이면 무기·사형"),
        (["방화"],
            92, "형법 제164조", "징역 5년~15년", "인명피해 없어도 실형 대부분"),
        (["마약", "필로폰", "대마", "코카인"],
            91, "마약류관리법", "징역 1년~3년", "초범 집행유예 가능, 재범은 실형"),
        (["강간", "성폭행"],
            90, "성폭력처벌법", "징역 5년~12년", "초범도 실형, 신상공개 가능"),
        (["강제추행", "추행"],
            85, "성폭력처벌법", "징역 1년~3년", "초범 집행유예 가능"),
        (["납치", "약취", "감금"],
            88, "형법 제276조", "징역 2년~7년", "피해자 석방 여부에 따라 감경"),
        (["아동학대"],
            87, "아동학대처벌법", "징역 1년~5년", "상습·중상해는 실형"),
        (["뇌물", "수뢰"],
            85, "형법 제129조", "징역 2년~5년", "공무원은 집행유예 드묾"),
        (["음주운전"],
            85, "도로교통법", "징역 6월~2년", "초범 벌금·집행유예, 재범은 실형"),
        (["뺑소니"],
            88, "특정범죄가중법", "징역 1년~5년", "사망사고 시 최대 무기징역"),
        (["보이스피싱"],
            85, "전기통신금융사기법", "징역 1년~4년", "주범 실형, 인출책도 실형 증가"),
        (["횡령"],
            82, "형법 제355조", "징역 1년~3년", "5억 이상 특경법 적용, 변제 시 감경"),
        (["배임"],
            80, "형법 제355조", "징역 1년~3년", "손해액 크면 특경법·실형"),
        (["딥페이크", "불법촬영"],
            83, "성폭력처벌법", "징역 1년~3년", "유포 시 가중, 초범 집행유예 가능"),
        (["주가조작"],
            82, "자본시장법", "징역 2년~5년", "차익 규모에 따라 특경법 가중"),
        (["특경법", "배임수재"],
            80, "특정경제범죄법", "징역 3년~7년", "50억 이상은 최하 5년 이상"),
        (["강도"],
            85, "형법 제333조", "징역 3년~7년", "흉기 사용 시 특수강도 가중"),
        (["절도"],
            78, "형법 제329조", "징역 6월~2년", "초범 집행유예 많음, 상습은 실형"),
        (["사기"],
            78, "형법 제347조", "징역 1년~3년", "피해액 5억 이상 특경법, 변제 시 감경"),
        (["스토킹"],
            75, "스토킹처벌법", "징역 6월~2년", "접근금지 위반 시 가중"),
    ]
    for keywords, prob, law, sentence, comment in CRIME_TABLE:
        if any(k in full for k in keywords):
            return f"유죄 확률: {prob}%", f"{law} 위반 {sentence} · {comment}"

    # 폭행·상해는 부상 결과 키워드로 판단
    if any(k in full for k in ["폭행", "때리", "구타", "가격", "상해"]):
        injury = estimate_injury_sentence(full)
        if injury:
            # 부상 결과 있으면 상해죄(75%), 없으면 폭행죄(68%)
            has_injury = any(k in full for k in ["골절", "뼈", "열상", "봉합", "치아", "뇌손상", "뇌출혈", "타박", "찰과", "멍"])
            prob = 75 if has_injury else 68
            return f"유죄 확률: {prob}%", injury
        has_injury = any(k in full for k in ["골절", "뼈", "열상", "봉합", "치아", "뇌손상", "뇌출혈"])
        if has_injury:
            return "유죄 확률: 75%", "형법 제257조(상해) 위반 · 중상해 아니면 초범 집행유예"
        return "유죄 확률: 68%", "형법 제260조(폭행) 위반 벌금 100~300만원 · 초범 대부분 벌금"

    return "유죄 확률: 75%", "형법 위반 징역 1년~3년 · 사안에 따라 집행유예 가능"




# ── 메시지 포맷 ────────────────────────────────────────────────

def fetch_article(url):
    """URL에서 제목과 본문 텍스트 추출"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.encoding and res.encoding.lower() in ('utf-8', 'utf8'):
            res.encoding = 'utf-8'
        elif 'charset' in res.headers.get('content-type', '').lower():
            pass
        else:
            res.encoding = res.apparent_encoding or 'utf-8'
        from html.parser import HTMLParser
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style', 'nav', 'footer', 'header'):
                    self.skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style', 'nav', 'footer', 'header'):
                    self.skip = False
            def handle_data(self, data):
                if not self.skip:
                    self.text.append(data.strip())
        parser = TextExtractor()
        parser.feed(res.text)
        full_text = ' '.join(t for t in parser.text if t)[:2000]
        # 제목 추출 (og:title 또는 <title>)
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', res.text, re.IGNORECASE)
        og_m = re.search(r'og:title[^>]*content=["\']([^"\']+)["\']', res.text, re.IGNORECASE)
        title = (og_m.group(1) if og_m else (title_m.group(1) if title_m else ""))
        title = re.sub(r'\s+', ' ', title).strip()
        return title, full_text
    except Exception as e:
        return "", ""

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

VERDICT_DONE_KEYWORDS = ["선고", "확정판결", "대법원 확정", "파기환송", "무죄 선고", "유죄 선고", "징역 선고", "실형 선고", "집행유예 선고"]

def analyze_url(chat_id, url):
    """URL 기사 분석 후 결과 전송 (Gemini 기반 판결 예측)"""
    # 관리자는 제한 없음
    if chat_id != ADMIN_ID and not check_and_increment_usage(chat_id):
        send_message(chat_id, f"⚠️ 오늘 검색 한도({DAILY_LIMIT}건)를 초과했습니다.\n내일 다시 이용해주세요.")
        return

    title, body = fetch_article(url)
    if not title and not body:
        send_message(chat_id, "❌ 기사를 가져오지 못했어요. URL을 확인해주세요.")
        return

    # 선고 기사 감지
    if any(k in title for k in VERDICT_DONE_KEYWORDS):
        send_message(chat_id, f"⚖️ {title}\n\n이미 선고되었습니다.")
        return

    prob_line, sentence_line = estimate_verdict(title, body)
    if not prob_line:
        send_message(chat_id, "⚠️ 형사사건 키워드를 찾지 못했어요.\n분석 가능한 형사사건 기사 URL을 보내주세요.")
        return
    msg = f"⚖️ {title}\n🔗 {url}\n\n{prob_line}\n{sentence_line}"
    send_message(chat_id, msg)

def format_message(article, prob_line, sentence_line, num):
    url = article.get("real_url") or article["url"]
    lines = [
        f"⚖️ {article['title']}",
        f"🔗 {url}",
        f"{prob_line}",
        f"{sentence_line}",
    ]
    return "\n".join(lines)


# ── 메인 업데이트 함수 ────────────────────────────────────────

def send_crime_update(slot=None):
    """형사사건 뉴스 분석 업데이트 (7시/20시). slot: 'morning'|'evening'|None(수동)"""
    print(f"\n{'='*45}")
    print(f"[형사봇] 업데이트 시작: {datetime.now().strftime('%Y-%m-%d %H:%M')} (slot={slot})")
    print(f"{'='*45}")

    cleanup_history()

    # 뉴스 수집
    try:
        articles = fetch_crime_news()
    except Exception as e:
        print(f"뉴스 수집 실패: {e}")
        return

    # 판결 기사로 기존 예측 승패 체크
    check_verdict_articles(articles)

    selected = []
    for article in articles:
        if len(selected) >= 1:
            break

        title = article["title"]
        url = article["url"]
        summary = article["summary"]

        # 이미 전송한 기사 스킵
        if is_sent(url, title):
            continue

        # 판결 완료 기사 스킵
        if any(k in title for k in VERDICT_EXCLUDE):
            print(f"  ⏭ 스킵(판결완료): {title[:40]}")
            continue

        # 정보 부족 기사 스킵
        if not is_sufficient(title, summary):
            print(f"  ⏭ 스킵(정보부족): {title[:40]}")
            continue

        print(f"  ✓ 선택: {title[:45]} (점수:{article['score']})")
        selected.append(article)

    if not selected:
        print("[형사봇] 특이 사건 없음 - 업데이트 스킵")
        return

    processed = []
    for article in selected:
        article["real_url"] = resolve_url(article["url"])
        prob_line, sentence_line = estimate_verdict(article["title"], article["summary"])
        mark_sent(article["url"], article["title"])
        save_prediction(article["title"], article.get("real_url") or article["url"], sentence_line)
        processed.append((article, prob_line, sentence_line))
        print(f"  ✅ 분석: {article['title'][:40]}")

    # 당일 기사 목록 누적 저장
    today_articles = load_today_articles()
    start_num = (today_articles[-1]["num"] + 1) if today_articles else 1

    for i, (article, _, __) in enumerate(processed):
        article["_num"] = start_num + i
        today_articles.append({
            "num": start_num + i,
            "title": article["title"],
            "url": article.get("real_url") or article["url"]
        })
    save_today_articles(today_articles)

    # 전송
    for article, prob_line, sentence_line in processed:
        global_num = article["_num"]
        msg = format_message(article, prob_line, sentence_line, global_num)
        broadcast(msg)
        time.sleep(1)

    # 전송 완료 기록 (스케줄 slot일 경우에만)
    if slot:
        mark_crime_sent(slot)

    print(f"\n[형사봇] 완료: {len(processed)}개 전송")


if __name__ == "__main__":
    send_crime_update()
