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
    'Referer': 'https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information.php?l=zh-tw'
}

def send_tg(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def get_price(code, market):
    # 嚴格檢查：只有 4 位數字才去查股價
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

def clean_str(s):
    """清除 HTML 標籤與特殊符號"""
    if s is None: return ""
    # 移除 HTML 標籤
    s = re.sub(r'<[^<]+?>', '', str(s))
    # 移除全形波浪號等干擾字元，統一變成空格
    return s.replace('～', ' ').replace('~', ' ').replace('-', ' ').strip()

def extract_date_range(full_text):
    """
    從一長串文字中暴力搜尋日期
    回傳: (倒數天數, 結束日期, 原始字串)
    """
    try:
        # 清洗整串文字
        cleaned = clean_str(full_text)
        
        # 搜尋所有類似 115/01/20 的日期
        # 格式：3位數字 + 分隔符 + 2位數字 + 分隔符 + 2位數字
        matches = re.findall(r'(\d{3})[./](\d{2})[./](\d{2})', cleaned)
        
        if len(matches) >= 2:
            # 只要找到兩個以上的日期，通常最後一個就是結束日
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            # 計算倒數
            y = int(y_end)
            y = y + 1911 if y < 1911 else y
            target = date(y, int(m_end), int(d_end))
            diff = (target - date.today()).days
            
            # 組合字串
            end_date_str = f"{y_end}/{m_end}/{d_end}"
            period_str = f"{y_start}/{m_start}/{d_start} ~ {end_date_str}"
            
            return (diff if diff >= 0 else 0), end_date_str, period_str
            
    except: pass
    return 0, "", ""

def scrape_current():
    data = []
    
    # --- 1. 上市 (TWSE) ---
    print("正在抓取上市資料...")
    try:
        twse_headers = HEADERS.copy()
        twse_headers.pop('Referer', None)
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=twse_headers, timeout=15)
        js = res.json()
        if js['stat'] == 'OK':
            print(f"上市抓到 {len(js['data'])} 筆")
            for r in js['data']:
                try:
                    # 上市欄位固定
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_detail = str(r[8]).strip()

                    # 過濾：只留 4 位數
                    if not (raw_code.isdigit() and len(raw_code) == 4): continue

                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_detail or "二十分鐘" in raw_detail: level = "20分盤"
                    elif "45分鐘" in raw_detail or "四十五分鐘" in raw_detail: level = "45分盤"
                    elif "60分鐘" in raw_detail: level = "60分盤"

                    countdown, pure_end_date, full_period = extract_date_range(raw_period)
                    if not pure_end_date: 
                        full_period = raw_period # 抓不到就顯示原文
                        pure_end_date = raw_period

                    data.append({
                        "market": "上市",
                        "code": raw_code,
                        "name": raw_name,
                        "period": full_period,
                        "level": level,
                        "end_date": pure_end_date,
                        "countdown": countdown
                    })
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 2. 上櫃 (TPEx) - 改回 Web API + 暴力掃描 ---
    print("正在抓取上櫃資料 (Web API)...")
    try:
        # 使用 Web API，因為你確認這裡有資料
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        rows = js.get('aaData', [])
        print(f"上櫃抓到 {len(rows)} 筆 (含權證)")
        
        for r in rows:
            try:
                # 1. 把整行資料轉成一個大字串，直接搜！
                # 這樣就不用管它到底在第幾欄，也不用管 HTML 標籤
                full_row_str = str(r) 
                
                # 2. 抓取代號 (暴力搜 4 位數)
                # 先嘗試標準位置 r[2]
                raw_code = clean_str(r[2])
                if not (raw_code.isdigit() and len(raw_code) == 4):
                    # 如果標準位置不是，就搜整行找 "3691" 這種格式
                    codes = re.findall(r'[^0-9]([1-9]\d{3})[^0-9]', " " + clean_str(full_row_str) + " ")
                    raw_code = ""
                    for c in codes:
                        if not c.startswith("11"): # 排除年份
                            raw_code = c
                            break
                
                # 【嚴格過濾】如果還是沒抓到 4 位數代號，直接跳過
                if not (raw_code.isdigit() and len(raw_code) == 4):
                    continue

                # 3. 抓取名稱 (r[3])
                raw_name = clean_str(r[3])

                # 4. 抓取日期 (暴力搜整行)
                # 你說的沒錯，如果能抓到 "20分盤"，一定也能抓到 "115/01/20"
                countdown, pure_end_date, full_period = extract_date_range(full_row_str)
                
                # 如果暴力搜不到，試著讀 r[5] (你指出的區間欄位)
                if not pure_end_date and len(r) > 5:
                     countdown, pure_end_date, full_period = extract_date_range(r[5])

                # 5. 抓取分盤 (暴力搜整行)
                level = "5分盤"
                if "20分鐘" in full_row_str or "二十分鐘" in full_row_str: level = "20分盤"
                elif "45分鐘" in full_row_str or "四十五分鐘" in full_row_str: level = "45分盤"
                elif "60分鐘" in full_row_str: level = "60分盤"
                elif "第二次" in full_row_str: level = "20分盤"

                # 只要有代號，就加進去。如果日期真的沒抓到，顯示「未抓取」但保留資料
                data.append({
                    "market": "上櫃",
                    "code": raw_code,
                    "name": raw_name,
                    "period": full_period if full_period else "日期未抓取",
                    "level": level,
                    "end_date": pure_end_date if pure_end_date else "日期未抓取",
                    "countdown": countdown
                })
            except Exception as ex: continue
            
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
    
    # 只保留舊資料裡的 4 位數股票
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 取得股價
        price, change = get_price(code, s['market'])
        
        new_processed.append({
            **s, "price": price, "change": change
        })

    # 排序
    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理出關 ---
    recently_exited = []
    
    # 只有當這次有抓到資料時，才去判斷出關
    if len(raw_new) > 0:
        for old_s in valid_old_stocks:
            if old_s['code'] not in new_codes:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)
    else:
        # 如果網路掛了抓到 0 筆，保留舊資料，不要誤刪
        new_processed = valid_old_stocks

    # 檢查剛出關清單 (只留 4 位數)
    for ex in old_data.get('exited_stocks', []):
        try:
            if not (str(ex['code']).isdigit() and len(str(ex['code'])) == 4): continue
            if ex['code'] in new_codes: continue # 復活機制

            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5:
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

    # TG 通知
    old_codes_set = {s['code'] for s in valid_old_stocks}
    for s in new_processed:
        if s['code'] not in old_codes_set and len(old_codes_set) > 0:
            tg_msg_list.append(s)

    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    if tg_msg_list:
        msg_lines = ["🚨 **台股處置新增**"]
        for x in tg_msg_list:
            msg_lines.append(f"{x['name']}({x['code']})\n{x['level']}")
        send_tg("\n\n".join(msg_lines))

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
