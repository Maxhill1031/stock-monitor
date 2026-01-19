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
}

def send_tg(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def get_price(code, market):
    # 嚴格防呆：只查 4 位數
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
    if s is None: return ""
    return str(s).replace('～', '~').replace(' ', '').strip()

def parse_dates(period_str):
    """
    萬能日期解析：支援 115/01/20 或 1150120
    回傳: (倒數天數, 結束日期, 完整區間)
    """
    try:
        text = clean_str(period_str)
        # 策略1: 抓取標準格式 115/01/20
        matches = re.findall(r'(\d{3})[-/](\d{2})[-/](\d{2})', text)
        
        # 策略2: 如果抓不到，抓取連續數字格式 1150120
        if not matches:
            matches = re.findall(r'(\d{3})(\d{2})(\d{2})', text)

        if len(matches) >= 2:
            # 假設最後一個是結束日，倒數第二個是開始日
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            y = int(y_end)
            y = y + 1911 if y < 1911 else y
            target = date(y, int(m_end), int(d_end))
            diff = (target - date.today()).days
            
            end_date_str = f"{y_end}/{m_end}/{d_end}"
            full_period = f"{y_start}/{m_start}/{d_start}~{end_date_str}"
            
            return (diff if diff >= 0 else 0), end_date_str, full_period
    except: pass
    return 0, "", period_str

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
                    raw_pub_date = str(r[1]).strip()
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_detail = str(r[8]).strip()

                    # 【嚴格過濾】非 4 位數直接丟棄
                    if not (raw_code.isdigit() and len(raw_code) == 4): continue

                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_detail or "二十分鐘" in raw_detail: level = "20分盤"
                    elif "45分鐘" in raw_detail or "四十五分鐘" in raw_detail: level = "45分盤"
                    elif "60分鐘" in raw_detail: level = "60分盤"

                    countdown, pure_end_date, _ = parse_dates(raw_period)
                    if not pure_end_date: pure_end_date = raw_period

                    data.append({
                        "market": "上市",
                        "code": raw_code,
                        "name": raw_name,
                        "publish_date": raw_pub_date,
                        "period": raw_period,
                        "reason": raw_measure,
                        "level": level,
                        "end_date": pure_end_date,
                        "countdown": countdown
                    })
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 2. 上櫃 (TPEx) - 強制使用 OpenAPI ---
    print("正在抓取上櫃資料 (OpenAPI)...")
    try:
        # 使用政府開放資料平台 (OpenAPI)，這個絕對不會擋 IP
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        res = requests.get(url, headers=HEADERS, timeout=15)
        rows = res.json()
        
        print(f"上櫃 OpenAPI 抓到 {len(rows)} 筆")

        for r in rows:
            try:
                # OpenAPI 是字典格式 (Key-Value)
                # 欄位通常是: SecuritiesCompanyCode, CompanyName, DisposePeriod
                
                raw_code = r.get('SecuritiesCompanyCode', r.get('證券代號', ''))
                raw_name = r.get('CompanyName', r.get('證券名稱', ''))
                raw_period = r.get('DisposePeriod', r.get('處置起迄時間', ''))
                
                # 強制轉字串
                raw_code = str(raw_code).strip()
                raw_name = str(raw_name).strip()
                raw_period = str(raw_period).strip()
                
                # 【嚴格過濾】只留 4 位數字 (踢掉權證)
                if not (raw_code.isdigit() and len(raw_code) == 4):
                    continue

                # 解析日期 (OpenAPI 的格式很乾淨，通常是 1150120 或 115/01/20)
                countdown, pure_end_date, full_period = parse_dates(raw_period)
                
                # 判斷分盤 (把整筆資料轉字串來搜)
                level = "5分盤"
                full_text = str(r)
                if "20分鐘" in full_text or "二十分鐘" in full_text: level = "20分盤"
                elif "45分鐘" in full_text or "四十五分鐘" in full_text: level = "45分盤"
                elif "60分鐘" in full_text: level = "60分盤"
                elif "第二次" in full_text: level = "20分盤"

                # 只要有代號就加進去
                data.append({
                    "market": "上櫃",
                    "code": raw_code,
                    "name": raw_name,
                    "publish_date": "", # OpenAPI 無公布日，留空
                    "period": full_period if full_period else raw_period,
                    "reason": "", 
                    "level": level,
                    "end_date": pure_end_date if pure_end_date else "日期未抓取",
                    "countdown": countdown
                })
            except Exception as ex: 
                continue
            
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
    
    # 【關鍵修正】
    # 只有當「完全抓不到資料」(API掛掉) 時，才保留舊資料。
    # 這裡我們信任 raw_new，因為 OpenAPI 不會擋人。
    # 如果 raw_new 是空的，而 old_data 有資料，才沿用舊的。
    if len(raw_new) == 0 and len(old_data.get('disposal_stocks', [])) > 0:
        print("⚠️ 警告：本次未抓到資料，暫時使用舊資料")
        new_processed = old_data['disposal_stocks']
    else:
        new_processed = []
        new_codes = set()
        for s in raw_new:
            code = s['code']
            new_codes.add(code)
            price, change = get_price(code, s['market'])
            new_processed.append({**s, "price": price, "change": change})

    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理出關 ---
    recently_exited = []
    
    # 讀取舊的 4 位數處置股
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    
    # 建立新名單的代號集合
    new_codes_set = {s['code'] for s in new_processed}

    # 1. 檢查誰消失了 (真的出關)
    # 只有當這次有抓到資料時才判斷
    if len(raw_new) > 0:
        for old_s in valid_old_stocks:
            if old_s['code'] not in new_codes_set:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)

    # 2. 檢查剛出關清單 (復活機制)
    for ex in old_data.get('exited_stocks', []):
        try:
            # 只留 4 位數
            if not (str(ex['code']).isdigit() and len(str(ex['code'])) == 4): continue
            
            # 如果它出現在新名單，代表之前誤判，把它從出關區移除
            if ex['code'] in new_codes_set: continue

            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5:
                # 避免重複
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

    # TG 通知
    tg_msg_list = []
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
            pub = x.get('publish_date', '')
            msg_lines.append(f"{x['name']}({x['code']})\n{x['level']} {pub}")
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
