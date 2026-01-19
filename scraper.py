import requests
import pandas as pd
import yfinance as yf
import json
import os
import time
from datetime import datetime, date
from io import StringIO

# --- 設定區 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# 偽裝成 Chrome 瀏覽器 (關鍵！)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.twse.com.tw/zh/announcement/punish.html'
}

def send_tg(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"TG Error: {e}")

def get_price(code, market):
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        # 增加 timeout 避免卡死
        hist = ticker.history(period="1d", timeout=10)
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', hist['Open'].iloc[0])
        change = round(((close - prev) / prev) * 100, 2)
        return close, change
    except: return "N/A", "N/A"

def calc_countdown(end_date_str):
    try:
        parts = end_date_str.split('/')
        y = int(parts[0])
        y = y + 1911 if y < 1911 else y
        target = date(y, int(parts[1]), int(parts[2]))
        diff = (target - date.today()).days
        return diff if diff >= 0 else 0
    except: return 0

def scrape_current():
    data = []
    
    # 1. 抓取上市 (TWSE)
    print("正在抓取上市資料...")
    try:
        url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        
        # 檢查是否被擋
        if res.status_code != 200:
            print(f"上市抓取失敗，狀態碼: {res.status_code}")
        else:
            js = res.json()
            if js['stat'] == 'OK':
                print(f"上市成功抓到 {len(js['data'])} 筆")
                for r in js['data']:
                    data.append({
                        "market": "上市",
                        "code": str(r[1]),
                        "name": str(r[2]),
                        "reason": str(r[3]),
                        "period": str(r[4]),
                        "end_date": r[4].split('-')[1]
                    })
            else:
                print(f"上市回傳狀態非 OK: {js.get('stat')}")
    except Exception as e:
        print(f"上市抓取發生錯誤: {e}")

    # 2. 抓取上櫃 (TPEx)
    print("正在抓取上櫃資料...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information.php?l=zh-tw"
        # 先用 requests 抓取 HTML 文字，避免 pandas 直接被擋
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.encoding = 'utf-8' # 強制編碼
        
        if res.status_code == 200:
            # 用 StringIO 包裝 html 文字給 pandas 讀取
            dfs = pd.read_html(StringIO(res.text), header=0)
            if dfs:
                df = dfs[0]
                print(f"上櫃成功抓到 {len(df)} 筆")
                if '證券代號' in df.columns:
                    for _, r in df.iterrows():
                        p = str(r['處置期間'])
                        end_date = p.split('-')[1] if '-' in p else p
                        data.append({
                            "market": "上櫃",
                            "code": str(r['證券代號']),
                            "name": str(r['證券名稱']),
                            "reason": str(r['處置措施']),
                            "period": p,
                            "end_date": end_date
                        })
        else:
            print(f"上櫃抓取失敗，狀態碼: {res.status_code}")
    except Exception as e:
        print(f"上櫃抓取發生錯誤: {e}")

    return data

def main():
    print("=== 程式開始執行 ===")
    
    # 讀取舊資料
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except: pass
    
    old_codes = {s['code'] for s in old_data.get('disposal_stocks', [])}
    
    # 執行抓取
    raw_new = scrape_current()
    
    if len(raw_new) == 0:
        print("⚠️ 警告：本次沒有抓到任何處置股，請檢查 Log 確認是否被證交所封鎖 IP。")
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        if code not in old_codes:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        level = "20分盤" if "20分鐘" in s['reason'] else ("45分盤" if "45分鐘" in s['reason'] else "5分盤")
        
        new_processed.append({
            **s, "price": price, "change": change, "level": level, "countdown": calc_countdown(s['end_date'])
        })

    new_processed.sort(key=lambda x: x['countdown'])

    # 處理出關
    recently_exited = []
    for ex in old_data.get('exited_stocks', []):
        try:
            if (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days <= 5:
                recently_exited.append(ex)
        except: pass
    
    for old_s in old_data.get('disposal_stocks', []):
        if old_s['code'] not in new_codes:
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.insert(0, old_s)

    # 模擬 ETF
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    if tg_msg_list:
        msg = "🚨 **台股處置新增**\n" + "\n".join([f"{x['name']}({x['code']})" for x in tg_msg_list])
        send_tg(msg)

    final_output = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disposal_stocks": new_processed,
        "exited_stocks": recently_exited,
        "etf_stocks": etf_data
    }
    
    # 確保不管怎樣都存檔，不然網頁會壞掉
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    print("=== 執行結束，資料已儲存 ===")

if __name__ == "__main__":
    main()
