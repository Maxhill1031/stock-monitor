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

def clean_html(raw_html):
    """清除 HTML 標籤"""
    return re.sub(re.compile('<[^<]+?>'), '', str(raw_html)).strip()

def calc_countdown(period_str):
    """
    暴力解析日期：處理 115/01/20~115/02/02 或 - 或 ～
    """
    try:
        # 統一分隔符號，把各種波浪號、全形半形都換成空格
        clean_str = str(period_str).replace('～', ' ').replace('~', ' ').replace('-', ' ')
        
        # 抓取所有日期 (民國年 3碼/2碼/2碼)
        matches = re.findall(r'(\d{3})[/](\d{2})[/](\d{2})', clean_str)
        
        if matches:
            # 取最後一組 (結束日)
            y_str, m_str, d_str = matches[-1]
            y = int(y_str)
            y = y + 1911 if y < 1911 else y
            
            target = date(y, int(m_str), int(d_str))
            diff = (target - date.today()).days
            return diff if diff >= 0 else 0
    except: return 0
    return 0

def parse_row_blindly(row_list, market_name):
    """
    【核心邏輯】盲搜：不依賴欄位順序，掃描整行資料找特徵
    """
    item = {
        "market": market_name,
        "code": "",
        "name": "",
        "period": "",
        "reason": "",
        "level": "5分盤",
        "end_date": "",
        "publish_date": ""
    }
    
    full_text = ""
    
    # 第一次掃描：找代號、日期、關鍵字
    for cell in row_list:
        txt = clean_html(cell)
        full_text += txt + " "
        
        # 1. 找代號 (4位數字) - 如果還沒找到
        if not item['code'] and re.match(r'^\d{4}$', txt):
            item['code'] = txt
            continue
            
        # 2. 找處置期間 (特徵：有日期且有波浪號或兩個日期)
        # 格式如: 115/01/20~115/02/02
        dates = re.findall(r'\d{3}/\d{2}/\d{2}', txt)
        if not item['period'] and len(dates) >= 2:
            item['period'] = txt
            item['end_date'] = txt
            continue
            
        # 3. 找公布日期 (特徵：單一個日期，且不是處置期間)
        if not item['publish_date'] and len(dates) == 1 and len(txt) < 12:
             item['publish_date'] = txt
             continue

        # 4. 找名稱 (非數字、長度短、不是日期)
        if not item['name'] and not re.search(r'\d', txt) and len(txt) > 1 and len(txt) < 10:
            if "檢視" not in txt and "處置" not in txt:
                item['name'] = txt

    # 判斷分盤 (全文字搜尋)
    if "20分鐘" in full_text or "二十分鐘" in full_text:
        item['level'] = "20分盤"
    elif "45分鐘" in full_text:
        item['level'] = "45分盤"
    elif "60分鐘" in full_text:
        item['level'] = "60分盤"
    elif "第二次" in full_text:
        item['level'] = "20分盤"

    # 如果沒抓到期間，再試一次暴力搜尋
    if not item['period']:
        all_dates = re.findall(r'\d{3}/\d{2}/\d{2}', full_text)
        if len(all_dates) >= 2:
            # 取最後兩個當作區間
            item['period'] = f"{all_dates[-2]}~{all_dates[-1]}"
            item['end_date'] = item['period']

    return item

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
                    # 盲搜上市資料
                    parsed = parse_row_blindly(r, "上市")
                    if parsed['code']:
                        data.append(parsed)
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 2. 上櫃 (TPEx) ---
    print("正在抓取上櫃資料...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        rows = js.get('aaData', [])
        print(f"上櫃抓到 {len(rows)} 筆")
        
        for r in rows:
            try:
                # 盲搜上櫃資料
                parsed = parse_row_blindly(r, "上櫃")
                if parsed['code']:
                    data.append(parsed)
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
    
    # 抓取新資料
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    # 處理新資料
    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 這裡不比對舊資料，直接視為最新狀態
        # 因為舊資料可能壞了
        price, change = get_price(code, s['market'])
        countdown = calc_countdown(s['end_date'])
        
        new_processed.append({
            **s, "price": price, "change": change, "countdown": countdown
        })

    # 排序
    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理出關與復活 ---
    recently_exited = []
    
    # 1. 檢查舊的處置股 (如果新名單沒有，才算與出關)
    for old_s in old_data.get('disposal_stocks', []):
        if old_s['code'] not in new_codes:
            # 真的消失了，加入出關清單
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.append(old_s)

    # 2. 檢查原本在「剛出關」清單裡的
    for ex in old_data.get('exited_stocks', []):
        try:
            # 【復活機制】如果這個股票出現在新抓到的名單(new_codes)裡
            # 代表它之前被誤判出關了，現在要忽略它(讓它留在 disposal_stocks)
            if ex['code'] in new_codes:
                continue

            # 正常的出關邏輯
            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5:
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

    # 產生通知 (只通知真的新出現的)
    # 讀取舊的 valid codes 來比對，避免重複通知
    old_valid_codes = {s['code'] for s in old_data.get('disposal_stocks', [])}
    for s in new_processed:
        if s['code'] not in old_valid_codes and len(old_valid_codes) > 0:
            tg_msg_list.append(s)

    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    if tg_msg_list:
        msg_lines = ["🚨 **台股處置新增**"]
        for x in tg_msg_list:
            pub = x.get('publish_date', '未知')
            msg_lines.append(f"{x['name']}({x['code']})\n{x['level']} | 公布: {pub}")
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
