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
    # 防呆：確保是 4 位數代號才查價
    if not code or not str(code).isdigit() or len(str(code)) != 4:
        return "N/A", "N/A"
        
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        hist = ticker.history(period="1d", timeout=5)
        if hist.empty: return "N/A", "N/A"
        
        close = round(hist['Close'].iloc[-1], 2)
        # 嘗試取得昨日收盤價計算漲跌
        prev = ticker.info.get('previousClose', None)
        if prev is None and len(hist['Open']) > 0:
             prev = hist['Open'].iloc[0]
             
        if prev:
            change = round(((close - prev) / prev) * 100, 2)
            return close, change
        return close, "N/A"
    except: return "N/A", "N/A"

def calc_countdown(period_str):
    """
    處理格式：115/01/14 ~ 115/01/27
    關鍵修正：處理 '~' 符號
    """
    try:
        # 1. 統一分隔符號，把 '~' 換成 '-'
        clean_str = period_str.replace('~', '-').replace(' ', '')
        
        # 2. 抓取結束日期 (dash 後面的部分)
        if '-' in clean_str:
            end_date_str = clean_str.split('-')[1] # 拿到 115/01/27
            
            parts = end_date_str.split('/')
            y = int(parts[0])
            y = y + 1911 if y < 1911 else y # 民國轉西元
            
            target = date(y, int(parts[1]), int(parts[2]))
            diff = (target - date.today()).days
            return diff if diff >= 0 else 0
    except: 
        return 0
    return 0

def scrape_current():
    data = []
    
    # --- 1. 抓取上市 (TWSE) ---
    print("正在抓取上市資料...")
    try:
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=HEADERS, timeout=15)
        js = res.json()
        if js['stat'] == 'OK':
            print(f"上市成功抓到 {len(js['data'])} 筆 raw data")
            for r in js['data']:
                try:
                    # === 關鍵修正：依照你的截圖指定欄位 ===
                    # row[2]: 證券代號 (如 1789)
                    # row[3]: 證券名稱 (如 神隆)
                    # row[6]: 處置期間 (如 115/01/14 ~ 115/01/27)
                    # row[7]: 處置措施 (如 第一次處置 / 第二次處置)
                    
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip() # 措施 (第一次/第二次)

                    # 判斷分盤等級
                    level = "5分盤" # 預設
                    if "第二次" in raw_measure:
                        level = "20分盤" # 第二次處置通常是 20 分鐘
                    elif "20分鐘" in str(r[8]): # 檢查詳細內容 row[8]
                        level = "20分盤"
                    elif "45分鐘" in str(r[8]):
                        level = "45分盤"

                    # 只有代號是 4 位數字才加入 (過濾掉表頭或異常資料)
                    if raw_code.isdigit() and len(raw_code) == 4:
                        data.append({
                            "market": "上市",
                            "code": raw_code,
                            "name": raw_name,
                            "period": raw_period,
                            "reason": raw_measure, 
                            "level": level,
                            "end_date": raw_period
                        })
                except Exception as row_err:
                    print(f"上市資料解析略過一筆: {row_err}")
                    continue
    except Exception as e:
        print(f"上市抓取失敗: {e}")

    # --- 2. 抓取上櫃 (TPEx) ---
    print("正在抓取上櫃資料...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        
        if 'aaData' in js:
            tpex_rows = js['aaData']
            print(f"上櫃成功抓到 {len(tpex_rows)} 筆")
            for r in tpex_rows:
                try:
                    def clean(s): return re.sub('<[^<]+?>', '', str(s)).strip()
                    
                    raw_code = clean(r[1])
                    raw_name = clean(r[2])
                    raw_reason = clean(r[3])
                    raw_period = clean(r[4])
                    
                    level = "5分盤"
                    if "20分鐘" in raw_reason or "二十分鐘" in raw_reason:
                        level = "20分盤"

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
    except Exception as e:
        print(f"上櫃抓取失敗: {e}")

    return data

def main():
    print("=== 程式開始執行 ===")
    
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except: pass
    
    # --- 關鍵修正：清理舊資料的髒數據 ---
    # 只有當舊代號是 4 位數字時，才認列為有效舊資料
    # 這樣可以排除之前抓錯的 "115/01/13" 這種日期代號
    old_codes = {s['code'] for s in old_data.get('disposal_stocks', []) if s['code'].isdigit() and len(s['code'])==4}
    
    raw_new = scrape_current()
    
    if len(raw_new) == 0:
        print("⚠️ 警告：沒有抓到任何處置股")
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 只有當舊資料也是乾淨的時候，才發通知，避免全部重發
        if code not in old_codes and len(old_codes) > 0:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        
        new_processed.append({
            **s, 
            "price": price, 
            "change": change, 
            "countdown": calc_countdown(s['end_date'])
        })

    new_processed.sort(key=lambda x: x['countdown'])

    # 處理出關 (保留5天)
    recently_exited = []
    
    # 1. 檢查真正剛出關的 (舊的有，新的沒有)
    for old_s in old_data.get('disposal_stocks', []):
        # 防呆：只處理正常的舊資料
        if old_s['code'].isdigit() and len(old_s['code'])==4:
            if old_s['code'] not in new_codes:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)

    # 2. 加上之前已出關的
    for ex in old_data.get('exited_stocks', []):
        try:
            if (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days <= 5:
                # 避免重複
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

    # 模擬 ETF
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    # TG 通知
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
