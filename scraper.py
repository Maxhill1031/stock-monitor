import requests
import pandas as pd
import yfinance as yf
import json
import os
import re
from datetime import datetime, date
from io import StringIO

# --- 設定區 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': 'https://www.twse.com.tw/zh/announcement/punish.html'
}

def send_tg(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def get_price(code, market):
    if not code or not str(code).isdigit() or len(str(code)) != 4:
        return "N/A", "N/A"
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        hist = ticker.history(period="1d", timeout=5)
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', None)
        if prev is None and len(hist['Open']) > 0: prev = hist['Open'].iloc[0]
        if prev:
            change = round(((close - prev) / prev) * 100, 2)
            return close, change
        return close, "N/A"
    except: return "N/A", "N/A"

def calc_countdown(period_str):
    """
    暴力解析日期：不管中間是 ~ 還是 - 或是空白
    直接找出所有 '115/01/27' 這種格式，取最後一個當作結束日
    """
    try:
        # 使用正則表達式尋找所有類似 '115/01/27' 的模式
        # \d{3} = 3個數字(年份), \d{2} = 2個數字(月/日)
        matches = re.findall(r'(\d{3})/(\d{2})/(\d{2})', period_str)
        
        if matches:
            # 取最後一組 (結束日期)
            y_str, m_str, d_str = matches[-1]
            
            # 民國轉西元
            y = int(y_str)
            y = y + 1911 if y < 1911 else y
            
            target = date(y, int(m_str), int(d_str))
            today = date.today()
            
            diff = (target - today).days
            return diff if diff >= 0 else 0
    except:
        return 0
    return 0

def scrape_current():
    data = []
    
    # --- 上市 (TWSE) ---
    print("正在抓取上市資料...")
    try:
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=HEADERS, timeout=15)
        js = res.json()
        if js['stat'] == 'OK':
            for r in js['data']:
                try:
                    # 依據你的截圖：row[2]代號, row[3]名稱, row[6]期間, row[7]措施
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_details = str(r[8]).strip()

                    # 判斷分盤
                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_details or "二十分鐘" in raw_details: level = "20分盤"
                    elif "45分鐘" in raw_details: level = "45分盤"
                    elif "60分鐘" in raw_details: level = "60分盤"

                    if raw_code.isdigit() and len(raw_code) == 4:
                        data.append({
                            "market": "上市",
                            "code": raw_code,
                            "name": raw_name,
                            "period": raw_period,
                            "reason": raw_measure, 
                            "level": level,
                            "end_date": raw_period # 原文存起來給 regex 解析
                        })
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 上櫃 (TPEx) ---
    print("正在抓取上櫃資料...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        if 'aaData' in js:
            for r in js['aaData']:
                try:
                    def clean(s): return re.sub('<[^<]+?>', '', str(s)).strip()
                    raw_code = clean(r[1])
                    raw_name = clean(r[2])
                    raw_reason = clean(r[3])
                    raw_period = clean(r[4])
                    
                    level = "5分盤"
                    if "20分鐘" in raw_reason or "二十分鐘" in raw_reason: level = "20分盤"

                    if raw_code.isdigit():
                        data.append({
                            "market": "上櫃",
                            "code": raw_code,
                            "name": raw_name,
                            "period": raw_period,
                            "reason": raw_reason,
                            "level": level,
                            "end_date": raw_period
                        })
                except: continue
    except Exception as e: print(f"上櫃錯誤: {e}")

    return data

def main():
    print("=== 程式開始執行 ===")
    
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except: pass
    
    # ⚠️ 關鍵修正：只保留「正常」的舊資料 (4位數字代號)
    # 這會自動把之前抓錯的 "115/01/13" 這種日期代號全部洗掉
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    old_codes = {s['code'] for s in valid_old_stocks}
    
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        if code not in old_codes and len(old_codes) > 0:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        
        # 這裡會呼叫新的 calc_countdown
        countdown = calc_countdown(s['end_date'])
        
        new_processed.append({
            **s, "price": price, "change": change, "countdown": countdown
        })

    new_processed.sort(key=lambda x: x['countdown'])

    # 處理出關 (只比對乾淨的舊資料)
    recently_exited = []
    
    # 1. 偵測剛出關
    for old_s in valid_old_stocks:
        if old_s['code'] not in new_codes:
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.append(old_s)

    # 2. 保留歷史出關 (5天內)
    for ex in old_data.get('exited_stocks', []):
        try:
            # 同樣要過濾掉髒資料
            if str(ex['code']).isdigit() and len(str(ex['code'])) == 4:
                if (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days <= 5:
                    if ex['code'] not in [x['code'] for x in recently_exited]:
                        recently_exited.append(ex)
        except: pass

    # 模擬 ETF
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    if tg_msg_list:
        msg = "🚨 **台股處置新增**\n" + "\n".join([f"{x['name']}({x['code']})\n{x['level']}" for x in tg_msg_list])
        send_tg(msg)

    final_output = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disposal_stocks": new_processed,
        "exited_stocks": recently_exited,
        "etf_stocks": etf_data
    }
    
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    print(f"=== 執行結束，成功處理 {len(new_processed)} 筆資料 ===")

if __name__ == "__main__":
    main()
