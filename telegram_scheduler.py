import requests
import json
import os
import re
import time
import schedule
import threading
import calendar
from datetime import datetime, timedelta

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


BOT_TOKEN = os.environ.get("SCHEDULER_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SCHEDULE_FILE = os.path.expanduser("~/telegram_schedules.json")
USERS_FILE = os.path.expanduser("~/telegram_users.json")
OFFSET_FILE = os.path.expanduser("~/telegram_offset.json")
MORNING_SENT_FILE = os.path.expanduser("~/telegram_morning_sent.json")
STOCK_SENT_FILE = os.path.expanduser("~/telegram_stock_sent.json")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── 사용자 관리 ──────────────────────────────────────────────

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 관리자는 항상 등록
    return {"allowed": [ADMIN_ID], "pending": []}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def is_allowed(chat_id):
    return chat_id in load_users()["allowed"]

# ── 일정 관리 ────────────────────────────────────────────────

def load_schedules():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            schedules = json.load(f)
        seen = set()
        return [s for s in schedules if (s["datetime"], s["title"]) not in seen and not seen.add((s["datetime"], s["title"]))]
    return []

def save_schedules(schedules):
    seen = set()
    deduped = [s for s in schedules if (s["datetime"], s["title"]) not in seen and not seen.add((s["datetime"], s["title"]))]
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

# ── 메시지 전송 ──────────────────────────────────────────────

def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{API_URL}/sendMessage", data=data, timeout=10)
    except:
        pass

def broadcast(text):
    """모든 허용된 사용자에게 메시지 전송"""
    for uid in load_users()["allowed"]:
        send_message(uid, text)


# ── 일정 파싱 ────────────────────────────────────────────────

def parse_schedule(text):
    text = re.sub(r'^/일정\s*', '', text.strip())
    text = text.strip()
    now = datetime.now()

    # 반납예정일 패턴 → 반납예정일 날짜만 추출, 대출일 무시
    if '반납예정일' in text:
        # 날짜 추출: 반납예정일 뒤의 날짜
        date_m = re.search(r'반납예정일\s*[:\s]?\s*(\d{1,2}[/\-월]\d{1,2}일?)', text)
        if not date_m:
            date_m = re.search(r'반납예정일\s*[:\s]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})', text)
        # 책 제목 추출: 도서명/책 뒤, 또는 대출일/반납예정일 앞
        book_m = re.search(r'(?:도서명|책|제목)\s*[:\s]?\s*([가-힣a-zA-Z0-9·\s]+?)(?:\s*대출일|\s*반납예정일|$)', text)
        if not book_m:
            # 날짜 패턴들 제거 후 남은 한글/영문 추출
            cleaned = re.sub(r'\d{1,4}[/\-월]\d{1,2}일?', '', text)
            cleaned = re.sub(r'반납예정일|대출일|도서명|예정일|대출|반납', '', cleaned)
            cleaned = re.sub(r'[:\s]+', ' ', cleaned).strip()
            book_title = cleaned if len(cleaned) >= 2 else "도서"
        else:
            book_title = book_m.group(1).strip()
        if date_m:
            return parse_schedule(f"{date_m.group(1)} 📚 {book_title} 반납")
        return None
    year = now.year
    month = None
    day = None
    hour = None
    minute = 0

    # 요일 처리 (이번주/다음주 + 요일)
    weekday_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    m = re.search(r'(다음주|이번주)?\s*([월화수목금토일])요일', text)
    if m:
        next_week = m.group(1) == "다음주"
        wd = weekday_map[m.group(2)]
        diff = (wd - now.weekday()) % 7
        if diff == 0 and not next_week:
            diff = 0
        elif next_week:
            diff += 7
        elif diff == 0:
            diff = 7
        target = now + timedelta(days=diff)
        month, day = target.month, target.day
        text = text[:m.start()] + text[m.end():]

    # 내일 / 모레
    elif text.startswith("내일"):
        target = now + timedelta(days=1)
        month, day = target.month, target.day
        text = text[2:].strip()
    elif text.startswith("모레"):
        target = now + timedelta(days=2)
        month, day = target.month, target.day
        text = text[2:].strip()

    # 날짜 파싱
    if month is None:
        m = re.search(r'(\d{1,2})월\s*(\d{1,2})일?', text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            text = text[:m.start()] + text[m.end():]
        else:
            m = re.search(r'(\d{1,2})[-/](\d{1,2})', text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                text = text[:m.start()] + text[m.end():]
            else:
                m = re.search(r'(\d{1,2})일', text)
                if m:
                    month, day = now.month, int(m.group(1))
                    text = text[:m.start()] + text[m.end():]

    if month is None or day is None:
        return None

    text = text.strip()

    # 시간 파싱 - 점심/저녁/아침
    if re.search(r'점심', text):
        hour, minute = 12, 0
        text = re.sub(r'점심', '', text)
    elif re.search(r'저녁', text):
        hour, minute = 18, 0
        text = re.sub(r'저녁', '', text)
    elif re.search(r'아침', text):
        hour, minute = 8, 0
        text = re.sub(r'아침', '', text)

    if hour is None:
        # 오전/오후 + 시 + 반
        m = re.search(r'(오전|오후)\s*(\d{1,2})시\s*(반)?(?:\s*(\d{1,2})분)?', text)
        if m:
            ampm, h = m.group(1), int(m.group(2))
            half, mn = m.group(3), m.group(4)
            minute = 30 if half else (int(mn) if mn else 0)
            if ampm == "오후" and h != 12:
                h += 12
            elif ampm == "오전" and h == 12:
                h = 0
            hour = h
            text = text[:m.start()] + text[m.end():]
        else:
            # HH:MM
            m = re.search(r'(\d{1,2}):(\d{2})', text)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))
                text = text[:m.start()] + text[m.end():]
            else:
                # 숫자시 반
                m = re.search(r'(\d{1,2})시\s*(반)?(?:\s*(\d{1,2})분)?', text)
                if m:
                    h, half, mn = int(m.group(1)), m.group(2), m.group(3)
                    minute = 30 if half else (int(mn) if mn else 0)
                    hour = h
                    text = text[:m.start()] + text[m.end():]

    # 시간 없으면 종일 일정으로 처리 (00:00)
    if hour is None:
        hour, minute = 0, 0
        all_day = True
    else:
        all_day = False

    # 불필요한 단어 제거
    title = re.sub(r'(?<=[가-힣]{2})[을를에서]\s+', ' ', text)
    title = re.sub(r'있어.*|해야.*|갈거야.*|예약.*했어.*', '', title)
    title = title.strip(" ,.\n")
    if not title:
        return None

    try:
        dt = datetime(year, month, day, hour, minute)
        if dt < now:
            dt = dt.replace(year=year + 1)
        return {"datetime": dt.strftime("%Y-%m-%d %H:%M"), "title": title, "all_day": all_day}
    except ValueError:
        return None

# ── 알림 체크 ────────────────────────────────────────────────

def get_weather():
    WMO_DESC = {
        0:"맑음", 1:"대체로 맑음", 2:"부분 흐림", 3:"흐림",
        45:"안개", 48:"안개", 51:"이슬비", 53:"이슬비", 55:"이슬비",
        61:"비", 63:"비", 65:"강한 비", 71:"눈", 73:"눈", 75:"강한 눈",
        80:"소나기", 81:"소나기", 82:"강한 소나기", 95:"뇌우", 99:"뇌우"
    }
    try:
        res = requests.get(
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=37.434&longitude=126.999"
            "&current=temperature_2m,apparent_temperature,weathercode,relativehumidity_2m"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
            "&timezone=Asia/Seoul&forecast_days=2",
            timeout=10,
            verify=False
        )
        data = res.json()
        cur = data["current"]
        daily = data["daily"]

        temp = cur["temperature_2m"]
        feels = cur["apparent_temperature"]
        humidity = cur["relativehumidity_2m"]
        desc = WMO_DESC.get(cur["weathercode"], "")
        max_t = daily["temperature_2m_max"][0]
        min_t = daily["temperature_2m_min"][0]
        rain_today = daily["precipitation_probability_max"][0]
        tom_max = daily["temperature_2m_max"][1]
        tom_min = daily["temperature_2m_min"][1]
        tom_rain = daily["precipitation_probability_max"][1]
        tom_desc = WMO_DESC.get(daily["weathercode"][1], "")

        lines = [
            f"🌤 과천 날씨",
            f"현재 {temp}°C (체감 {feels}°C) {desc}",
            f"오늘 최고 {max_t}° / 최저 {min_t}° 💧강수 {rain_today}% 💦습도 {humidity}%",
            f"내일 최고 {tom_max}° / 최저 {tom_min}° 💧강수 {tom_rain}% {tom_desc}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"🌤 날씨 정보를 가져오지 못했어요 ({e})"

def get_exchange_rates():
    try:
        res = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=10
        )
        data = res.json()["rates"]
        usd_krw = data["KRW"]
        jpy_krw = data["KRW"] / data["JPY"]
        eur_krw = data["KRW"] / data["EUR"]

        return (
            f"💱 환율\n"
            f"달러  $1 = {usd_krw:,.0f}원\n"
            f"엔화  ¥100 = {jpy_krw*100:,.0f}원\n"
            f"유로  €1 = {eur_krw:,.0f}원"
        )
    except Exception as e:
        return f"💱 환율 정보를 가져오지 못했어요 ({e})"

def cleanup_past_schedules():
    schedules = load_schedules()
    now = datetime.now()
    updated = [s for s in schedules if datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M").date() >= now.date()]
    if len(updated) < len(schedules):
        save_schedules(updated)
        print(f"지난 일정 {len(schedules)-len(updated)}개 삭제")

def check_reminders():
    schedules = load_schedules()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # 오늘/내일 일정 알림
    alerts = []
    updated = []
    for s in schedules:
        dt = datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M")
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
        by = f" ({s.get('added_by','')})" if s.get("added_by") else ""

        if date_str == tomorrow:
            if "📚" in s.get("title", ""):
                alerts.append(f"📚 내일 도서 반납일")
            else:
                alerts.append(f"📅 내일: {s['title']} {time_str}{by}")
        elif date_str == today:
            if "📚" in s.get("title", ""):
                alerts.append(f"📚 오늘 도서 반납일")
            else:
                alerts.append(f"🔔 오늘: {s['title']} {time_str}{by}")

        if dt.date() >= now.date():
            updated.append(s)

    save_schedules(updated)

    # 날씨 + 환율
    weather = get_weather()
    rates = get_exchange_rates()

    # 달력 + 오늘/내일 알림 함께 전송
    header = f"🌅 좋은 아침이에요! ({now.strftime('%m월 %d일')})\n\n"
    header += weather + "\n\n"
    header += rates + "\n"
    if alerts:
        header += "\n" + "\n".join(alerts) + "\n"
    header += "\n"
    broadcast(header + format_monthly_calendar(updated))
    mark_morning_sent()

def send_stock_analysis():
    """8시 주식 분석 전송"""
    try:
        import subprocess
        result = subprocess.run(["python3", os.path.expanduser("~/gemini_fetch.py")], timeout=180)
        if result.returncode == 0:
            mark_stock_sent()
        else:
            print(f"Gemini 실행 실패 (returncode={result.returncode}) - 미전송 상태 유지")
    except Exception as e:
        print(f"Gemini 실행 오류: {e} - 미전송 상태 유지")

def format_simple_list(schedules):
    now = datetime.now()
    # 날짜순 정렬
    upcoming = sorted(
        [s for s in schedules if s["datetime"] >= now.strftime("%Y-%m-%d") and "📚" not in s.get("title", "")],
        key=lambda x: x["datetime"]
    )
    if not upcoming: return "📋 등록된 일정이 없어요."

    lines = ["📌 향후 주요 일정 (최근 5개)"]
    for i, s in enumerate(upcoming[:5], 1): # 최대 5개만 출력해서 중복/피로도 감소
        dt = datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M")
        wd = ["월","화","수","목","금","토","일"][dt.weekday()]
        dday = (dt.date() - now.date()).days
        dday_str = "오늘⭐" if dday == 0 else f"D-{dday}"
        
        # 출력 가독성 개선
        lines.append(f"{i}. {dt.strftime('%m/%d')}({wd}) {s['title']} [{dday_str}]")
    
    if len(upcoming) > 5:
        lines.append(f"   ...외 {len(upcoming)-5}개의 일정이 더 있습니다.")
    return "\n".join(lines)

def format_monthly_calendar(schedules):
    now = datetime.now()
    year, month = now.year, now.month

    # 이번 달 + 다음 달 일정 모두 포함
    upcoming = [s for s in schedules if s["datetime"] >= now.strftime("%Y-%m-%d")]
    event_days = {}  # {(year, month, day): [schedule, ...]}
    for s in upcoming:
        dt = datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M")
        key = (dt.year, dt.month, dt.day)
        event_days.setdefault(key, []).append(s)

    def make_month_block(y, m):
        cal = calendar.monthcalendar(y, m)
        month_names = ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"]
        lines = [f"📅 {y}년 {month_names[m-1]}"]
        lines.append("월  화  수  목  금  토  일")
        lines.append("─" * 27)
        for week in cal:
            row = []
            for d in week:
                if d == 0:
                    row.append("   ")
                elif (y, m, d) in event_days:
                    row.append(f"[{d:2d}]" if d < 10 else f"[{d}]")
                else:
                    today = now.date()
                    if y == today.year and m == today.month and d == today.day:
                        row.append(f"*{d:2d} " if d < 10 else f"*{d}")
                    else:
                        row.append(f" {d:2d} " if d < 10 else f" {d} ")
            lines.append(" ".join(row))
        return "\n".join(lines)

    def make_event_list(y, m):
        month_events = [(k, v) for k, v in event_days.items() if k[0] == y and k[1] == m]
        if not month_events:
            return ""
        lines = []
        for (ey, em, ed), evs in sorted(month_events):
            for s in sorted(evs, key=lambda x: x["datetime"]):
                dt = datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M")
                by = f" ({s.get('added_by','')})" if s.get("added_by") else ""
                diff = (dt.date() - now.date()).days
                dday = "오늘⭐" if diff == 0 else f"D-{diff}"
                time_str = "종일" if s.get("all_day") else dt.strftime('%H:%M')
                title = "📚 도서 반납일" if "📚" in s.get("title", "") else s['title']
                lines.append(f"  {ed}일 {time_str} {title}{by} [{dday}]")
        return "\n".join(lines)

    # 이번 달
    result = make_month_block(year, month)
    event_list = make_event_list(year, month)
    if event_list:
        result += "\n\n" + event_list

    # 다음 달에 일정 있으면 추가
    if month == 12:
        ny, nm = year + 1, 1
    else:
        ny, nm = year, month + 1

    next_events = [(k, v) for k, v in event_days.items() if k[0] == ny and k[1] == nm]
    if next_events:
        result += "\n\n" + make_month_block(ny, nm)
        next_list = make_event_list(ny, nm)
        if next_list:
            result += "\n\n" + next_list

    if not upcoming:
        result += "\n\n등록된 일정이 없어요."

    return result

def parse_library_message(text):
    """과천시정보과학도서관 메시지 파싱 - 여러 권 처리"""
    if '[과천시정보과학도서관]' not in text and '[문원도서관]' not in text:
        return None

    # 반납예정일 추출
    return_m = re.search(r'반납예정일\s*[:\：]\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', text)
    if not return_m:
        return None
    return_date = f"{return_m.group(1)}-{int(return_m.group(2)):02d}-{int(return_m.group(3)):02d}"

    # 도서명 추출: ▦ 로 시작하는 모든 줄
    books = re.findall(r'▦\s*(.+)', text)
    books = [b.strip() for b in books if b.strip()]
    if not books:
        books = ["도서"]

    # 연체일수 추출
    overdue_m = re.search(r'연체\s*일수\s*[:\：]?\s*(\d+)\s*일?', text)
    overdue = int(overdue_m.group(1)) if overdue_m else 0

    return {
        "books": books,
        "return_date": return_date,
        "overdue": overdue
    }

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    first_name = msg.get("from", {}).get("first_name", "사용자")

    # 신규 사용자 처리
    if not is_allowed(chat_id):
        if text in ["/start", "/시작"]:
            users = load_users()
            if chat_id not in [u["id"] for u in users.get("pending", []) if isinstance(u, dict)]:
                users.setdefault("pending", []).append({"id": chat_id, "name": first_name})
                save_users(users)
            send_message(chat_id, f"안녕하세요 {first_name}님! 관리자 승인 후 사용 가능해요.")
            markup = {
                "inline_keyboard": [[
                    {"text": f"✅ {first_name} 승인", "callback_data": f"approve_{chat_id}"},
                    {"text": "❌ 거절", "callback_data": f"reject_{chat_id}"}
                ]]
            }
            send_message(ADMIN_ID, f"🔔 새 사용자 참여 요청\n이름: {first_name}\nID: {chat_id}", reply_markup=markup)
        else:
            send_message(chat_id, "먼저 /시작 을 입력해서 승인을 요청하세요.")
        return

    # 과천시정보과학관 도서관 메시지 처리
    library = parse_library_message(text)
    if library:
        books = library["books"]
        return_date = library["return_date"]
        schedules = load_schedules()

        for book in books:
            schedules.append({
                "datetime": f"{return_date} 00:00",
                "title": f"📚 {book}",
                "all_day": True,
                "added_by": first_name
            })
        save_schedules(schedules)

        book_summary = ", ".join(books[:3]) + (f" 외 {len(books)-3}권" if len(books) > 3 else "")
        res_lines = [
            f"✅ 도서관 알림 등록 완료",
            f"📖 도서: {book_summary}",
            f"📅 반납일: {return_date} (D-2)",
            f"\n{format_simple_list(schedules)}"
        ]
        broadcast("\n".join(res_lines))
        return

    # 승인된 사용자
    if text.startswith("/일정") or not text.startswith("/"):
        parsed = parse_schedule(text)
        if parsed:
            parsed["added_by"] = first_name
            schedules = load_schedules()
            schedules.append(parsed)
            save_schedules(schedules)
            schedules = load_schedules()
            dt = datetime.strptime(parsed["datetime"], "%Y-%m-%d %H:%M")
            # 등록 확인 + 전체 일정표 함께 전송
            broadcast(
                f"📌 새 일정 등록 ({first_name})\n"
                f"→ {parsed['title']} | {dt.strftime('%m월 %d일 %H:%M')}\n\n"
                + format_simple_list(schedules)
            )
        else:
            send_message(chat_id,
                "📅 날짜를 포함해서 다시 입력해주세요!\n\n"
                "예시:\n"
                "4/15 주완 공개수업\n"
                "내일 오후 3시 치과\n"
                "다음주 화요일 점심 친구 만남\n"
                "25일 오전 10시 병원"
            )

    elif text == "반납":
        schedules = load_schedules()
        now = datetime.now()
        overdue = [s for s in schedules if "📚" in s.get("title","") and datetime.strptime(s["datetime"], "%Y-%m-%d %H:%M").date() < now.date()]
        if overdue:
            remaining = [s for s in schedules if s not in overdue]
            save_schedules(remaining)
            titles = "\n".join(f"· {s['title'].replace('📚','').replace('반납','').strip()}" for s in overdue)
            broadcast(f"📚 반납 처리 완료 ({first_name})\n{titles}\n\n{format_simple_list(remaining)}")
        else:
            send_message(chat_id, "연체된 도서가 없어요.")

    elif text == "/목록":
        schedules = load_schedules()
        send_message(chat_id, format_simple_list(schedules))

    elif text.startswith("/삭제"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            schedules = sorted(load_schedules(), key=lambda x: x["datetime"])
            idx = int(parts[1]) - 1
            if 0 <= idx < len(schedules):
                removed = schedules.pop(idx)
                save_schedules(schedules)
                broadcast(
                    f"🗑️ 일정 삭제 ({first_name})\n"
                    f"→ {removed['title']}\n\n"
                    + format_simple_list(schedules)
                )
            else:
                send_message(chat_id, "해당 번호의 일정이 없어요.")
        else:
            send_message(chat_id, "예: /삭제 1")

    elif text == "/도움말":
        send_message(chat_id,
            "📌 사용법\n\n"
            "일정 등록:\n"
            "/일정 03-25 14:00 치과\n"
            "/일정 내일 오후 3시 반 미용실\n"
            "/일정 다음주 화요일 점심 친구\n"
            "/일정 25일 오전 10시 병원\n\n"
            "/목록 - 전체 일정 보기\n"
            "/삭제 1 - 1번 일정 삭제"
        )

def handle_callback(callback):
    """인라인 버튼 처리 (승인/거절)"""
    chat_id = callback["from"]["id"]
    data = callback.get("data", "")
    callback_id = callback["id"]

    if chat_id != ADMIN_ID:
        return

    if data.startswith("approve_"):
        new_user_id = int(data.split("_")[1])
        users = load_users()
        users["allowed"].append(new_user_id)
        users["pending"] = [u for u in users.get("pending", []) if (u["id"] if isinstance(u, dict) else u) != new_user_id]
        save_users(users)
        requests.post(f"{API_URL}/answerCallbackQuery", data={"callback_query_id": callback_id, "text": "승인 완료!"})
        send_message(new_user_id, "✅ 승인되었어요! 이제 일정을 공유할 수 있어요.\n/도움말 로 사용법 확인하세요.")
        send_message(ADMIN_ID, "승인 완료!")

    elif data.startswith("reject_"):
        new_user_id = int(data.split("_")[1])
        users = load_users()
        users["pending"] = [u for u in users.get("pending", []) if (u["id"] if isinstance(u, dict) else u) != new_user_id]
        save_users(users)
        requests.post(f"{API_URL}/answerCallbackQuery", data={"callback_query_id": callback_id, "text": "거절됨"})
        send_message(new_user_id, "❌ 승인이 거절되었어요.")

# ── 메인 폴링 ────────────────────────────────────────────────

def stock_already_sent():
    if os.path.exists(STOCK_SENT_FILE):
        with open(STOCK_SENT_FILE) as f:
            return json.load(f).get("date") == datetime.now().strftime("%Y-%m-%d")
    return False

def mark_stock_sent():
    with open(STOCK_SENT_FILE, "w") as f:
        json.dump({"date": datetime.now().strftime("%Y-%m-%d")}, f)

def morning_already_sent():
    if os.path.exists(MORNING_SENT_FILE):
        with open(MORNING_SENT_FILE) as f:
            data = json.load(f)
        return data.get("date") == datetime.now().strftime("%Y-%m-%d")
    return False

def mark_morning_sent():
    with open(MORNING_SENT_FILE, "w") as f:
        json.dump({"date": datetime.now().strftime("%Y-%m-%d")}, f)

def load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset")
    return None

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)

def poll_messages():
    offset = load_offset()
    if offset:
        print(f"이전 offset에서 재개: {offset}")
    while True:
        try:
            params = {"timeout": 10, "allowed_updates": ["message", "callback_query", "channel_post"]}
            if offset:
                params["offset"] = offset
            res = requests.get(f"{API_URL}/getUpdates", params=params, timeout=15)
            updates = res.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
            time.sleep(0.5)
        except KeyboardInterrupt:
            time.sleep(2)
        except Exception as e:
            print(f"오류: {e}")
            time.sleep(1)

if __name__ == "__main__":
    print("텔레그램 일정 봇 시작!")

    # 관리자 초기 등록
    users = load_users()
    if ADMIN_ID not in users["allowed"]:
        users["allowed"].append(ADMIN_ID)
        save_users(users)

    now = datetime.now()

    # 6시 이후인데 아침 메시지 미전송 → 즉시 전송
    if now.hour >= 6 and not morning_already_sent():
        print("아침 메시지 미전송 - 즉시 전송")
        threading.Thread(target=check_reminders, daemon=True).start()

    cleanup_past_schedules()  # 시작 시 지난 일정 정리

    schedule.every().day.at("00:00").do(cleanup_past_schedules)
    schedule.every().day.at("06:00").do(check_reminders)

    t = threading.Thread(target=lambda: [time.sleep(30) or schedule.run_pending() for _ in iter(int, 1)], daemon=True)
    t.start()

    poll_messages()
