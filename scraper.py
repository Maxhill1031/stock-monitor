import requests
import pandas as pd
import yfinance as yf
import json
import os
import re
from datetime import datetime, date

# --- 設定區 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': 'https://www.twse.com.tw/'
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
        # 嘗試取得昨日收盤
        prev = ticker.info.get('previousClose', None)
        if prev is None and len(hist['Open']) > 0: prev = hist['Open'].iloc[0]
        
        if prev:
            change = round(((close - prev) / prev) * 100, 2)
            return close, change
        return close, "N/A"
    except: return "N/A", "N/A"

def calc_countdown(period_str):
    """
    暴力解析日期：抓取字串中最後一組 '115/01/27' 格式的日期
    """
    try:
        # 尋找所有類似 115/01/27 的日期
        matches = re.findall(r'(\d{3})/(\d{2})/(\d{2})', str(period_str))
        if matches:
            # 取最後一個當作結束日
            y_str, m_str, d_str = matches[-1]
            y = int(y_str)
            y = y + 1911 if y < 1911 else y
            
            target = date(y, int(m_str), int(d_str))
            diff = (target - date.today()).days
            return diff if diff >= 0 else 0
    except: return 0
    return 0

def clean_html(raw_html):
    """清除上櫃資料中的 HTML 標籤"""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', str(raw_html)).strip()

def scrape_current():
    data = []
    
    # --- 1. 上市 (TWSE) ---
    print("正在抓取上市資料...")
    try:
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=HEADERS, timeout=15)
        js = res.json()
        if js['stat'] == 'OK':
            print(f"上市抓到 {len(js['data'])} 筆")
            for r in js['data']:
                try:
                    # 依照截圖欄位：[2]代號 [3]名稱 [6]期間 [7]措施
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_detail = str(r[8]).strip()

                    # 判斷分盤
                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_detail or "二十分鐘" in raw_detail: level = "20分盤"
                    elif "45分鐘" in raw_detail: level = "45分盤"
                    elif "60分鐘" in raw_detail: level = "60分盤"

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
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 2. 上櫃 (TPEx) - 改用 API ---
    print("正在抓取上櫃資料 (API)...")
    try:
        # 這是櫃買中心新舊版網頁背後的共用 API，非常穩定
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        
        if 'aaData' in js:
            tpex_rows = js['aaData']
            print(f"上櫃抓到 {len(tpex_rows)} 筆")
            for r in tpex_rows:
                try:
                    # API 欄位：[1]代號 [2]名稱 [3]措施 [4]期間
                    # 注意：上櫃的代號欄位通常包著 HTML <a href..>，需要清洗
                    raw_code = clean_html(r[1])
                    raw_name = clean_html(r[2])
                    raw_reason = clean_html(r[3])
                    raw_period = clean_html(r[4])

                    # 判斷分盤
                    level = "5分盤"
                    if "20分鐘" in raw_reason or "二十分鐘" in raw_reason: level = "20分盤"
                    elif "45分鐘" in raw_reason: level = "45分盤"
                    elif "60分鐘" in raw_reason: level = "60分盤"

                    if raw_code.isdigit() and len(raw_code) == 4:
                        data.append({
                            "market": "上櫃",
                            "code": raw_code,
                            "name": raw_name,
                            "period": raw_period,
                            "reason": raw_reason,
                            "level": level,
                            "end_date": raw_period
                        })
                except Exception as ex: 
                    print(f"上櫃單筆錯誤: {ex}")
                    continue
    except Exception as e: print(f"上櫃錯誤: {e}")

    return data

def main():
    print("=== 程式開始執行 ===")
    
    # 讀取舊檔
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                old_data = json.load(f)
        except: pass
    
    # 1. 取得「乾淨的」舊資料 (過濾掉之前抓壞的日期代號)
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    old_codes = {s['code'] for s in valid_old_stocks}
    
    # 2. 抓新資料
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 只有在舊資料也乾淨的情況下才發通知，避免誤報
        if code not in old_codes and len(old_codes) > 0:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        
        # 呼叫新的倒數計算邏輯
        countdown = calc_countdown(s['end_date'])
        
        new_processed.append({
            **s, "price": price, "change": change, "countdown": countdown
        })

    # 排序
    new_processed.sort(key=lambda x: x['countdown'])

    # 3. 處理剛出關
    recently_exited = []
    
    # 偵測今天剛出關的 (舊名單有，新名單沒有)
    for old_s in valid_old_stocks:
        if old_s['code'] not in new_codes:
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.append(old_s)

    # 保留歷史出關紀錄 (5天內)
    for ex in old_data.get('exited_stocks', []):
        try:
            if str(ex['code']).isdigit() and len(str(ex['code'])) == 4:
                days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
                if days_diff <= 5:
                    # 避免重複添加
                    if ex['code'] not in [x['code'] for x in recently_exited]:
                        recently_exited.append(ex)
        except: pass

    # 模擬 ETF 資料
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    # TG 通知
    if tg_msg_list:
        msg = "🚨 **台股處置新增**\n" + "\n".join([f"{x['name']}({x['code']})\n{x['level']}" for x in tg_msg_list])
        send_tg(msg)

    # 存檔
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
