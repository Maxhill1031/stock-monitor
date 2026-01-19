import requests
import pandas as pd
import yfinance as yf
import json
import os
import time
from datetime import datetime, date

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
    # 防呆：如果代號不是數字（例如抓到日期），直接回傳 N/A
    if not code or not code[0].isdigit():
        return "N/A", "N/A"
        
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        hist = ticker.history(period="1d", timeout=10)
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', hist['Open'].iloc[0])
        change = round(((close - prev) / prev) * 100, 2)
        return close, change
    except: return "N/A", "N/A"

def calc_countdown(end_date_str):
    try:
        # 處理可能的民國年格式 113/05/20
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
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=HEADERS, timeout=15)
        js = res.json()
        if js['stat'] == 'OK':
            print(f"上市成功抓到 {len(js['data'])} 筆 raw data")
            for r in js['data']:
                try:
                    # [修正] 證交所欄位位移：r[1]是日期, r[2]是代號, r[3]是名稱
                    # 先確認 r[2] 是不是數字，如果不是就嘗試 r[1]
                    raw_code = str(r[2])
                    raw_name = str(r[3])
                    raw_reason = str(r[4])
                    raw_period = str(r[5])
                    
                    # 再次確認，避免欄位又改
                    if not raw_code.isdigit() and str(r[1]).isdigit():
                         raw_code = str(r[1]) # fallback

                    period = raw_period
                    end_date = period.split('-')[1] if '-' in period else period
                    
                    data.append({
                        "market": "上市",
                        "code": raw_code,
                        "name": raw_name,
                        "reason": raw_reason,
                        "period": period,
                        "end_date": end_date
                    })
                except Exception as row_err:
                    print(f"上市資料解析略過一筆: {row_err}")
                    continue
        else:
            print(f"上市回傳狀態: {js.get('stat')}")
    except Exception as e:
        print(f"上市抓取發生錯誤: {e}")

    # 2. 抓取上櫃 (TPEx) - 改用 JSON API
    print("正在抓取上櫃資料 (JSON Mode)...")
    try:
        # TPEx 的隱藏 JSON API，比爬 HTML 穩
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        
        # TPEx JSON 結構通常在 aaData 裡
        if 'aaData' in js:
            tpex_rows = js['aaData']
            print(f"上櫃成功抓到 {len(tpex_rows)} 筆")
            for r in tpex_rows:
                try:
                    # TPEx JSON 順序: [0]日期 [1]代號 [2]名稱 [3]處置措施 [4]處置期間
                    p = str(r[4])
                    end_date = p.split('-')[1] if '-' in p else p
                    # 移除 HTML tag (TPEx有時會回傳帶連結的代號)
                    clean_code = str(r[1]).replace(" ", "")
                    
                    data.append({
                        "market": "上櫃",
                        "code": clean_code,
                        "name": str(r[2]),
                        "reason": str(r[3]),
                        "period": p,
                        "end_date": end_date
                    })
                except: continue
        else:
            print("上櫃 JSON 回傳無 aaData 欄位")
            
    except Exception as e:
        print(f"上櫃抓取發生錯誤: {e}")

    return data

def main():
    print("=== 程式開始執行 ===")
    
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except: pass
    
    old_codes = {s['code'] for s in old_data.get('disposal_stocks', [])}
    
    raw_new = scrape_current()
    
    if len(raw_new) == 0:
        print("⚠️ 警告：沒有抓到任何處置股，請確認網站是否改版")
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        # 再次過濾非數字代號
        if not code.isdigit(): 
            continue
            
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
    
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    print(f"=== 執行結束，成功處理 {len(new_processed)} 筆資料 ===")

if __name__ == "__main__":
    main()
