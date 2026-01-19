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
    if raw_html is None: return ""
    return re.sub(re.compile('<[^<]+?>'), '', str(raw_html)).strip()

def parse_dates(period_str):
    """
    從字串中暴力抓取日期區間
    回傳: (倒數天數, 結束日期字串, 完整區間字串)
    """
    try:
        # 1. 統一分隔符號
        clean_str = clean_html(period_str).replace('～', '~').replace(' ', '')
        
        # 2. 抓取所有日期 (格式: 115/01/20)
        # 這裡會抓取字串中所有的日期
        matches = re.findall(r'(\d{3})[-/~](\d{2})[-/~](\d{2})', clean_str)
        
        if len(matches) >= 2:
            # 假設最後一個是結束日，倒數第二個是開始日
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            # 結束日計算
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
                    # 上市欄位: [1]公布日 [2]代號 [3]名稱 [6]期間 [7]措施
                    raw_pub_date = str(r[1]).strip()
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_detail = str(r[8]).strip()

                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_detail or "二十分鐘" in raw_detail: level = "20分盤"
                    elif "45分鐘" in raw_detail or "四十五分鐘" in raw_detail: level = "45分盤"
                    elif "60分鐘" in raw_detail: level = "60分盤"

                    if raw_code.isdigit() and len(raw_code) == 4:
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

    # --- 2. 上櫃 (TPEx) - 改用 OpenAPI + 暴力搜索 ---
    print("正在抓取上櫃資料 (OpenAPI)...")
    try:
        # 使用官方 Open Data，這不會被擋
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        res = requests.get(url, headers=HEADERS, timeout=15)
        rows = res.json()
        
        # 如果 OpenAPI 也是空的 (極少見)，嘗試 Web API 備援
        if not rows:
             print("OpenAPI 無資料，嘗試 Web API...")
             url_web = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
             res_web = requests.get(url_web, headers={'Referer': 'https://www.tpex.org.tw/'}, timeout=15)
             rows = res_web.json().get('aaData', [])

        print(f"上櫃抓到 {len(rows)} 筆")
        
        for r in rows:
            try:
                # 不管 r 是 list 還是 dict，先轉成字串方便搜索
                row_str = json.dumps(r, ensure_ascii=False)
                
                # 1. 暴力搜代號 (4位數字)
                # 排除年份 (11x) 開頭的，通常代號在 3xxx-9xxx
                code_matches = re.findall(r'[^0-9]([1-9]\d{3})[^0-9]', " " + row_str + " ")
                raw_code = ""
                for c in code_matches:
                    # 簡單過濾：通常不是年份
                    if not c.startswith("11"): 
                        raw_code = c
                        break
                
                # 2. 暴力搜日期區間 (115/01/20~115/02/02)
                # 這裡會回傳 (countdown, end_date, full_period)
                countdown, pure_end_date, full_period = parse_dates(row_str)
                
                # 3. 暴力搜名稱 (假設名稱在代號附近，這裡先簡化，如果 OpenAPI 有 key 就用 key)
                raw_name = "未知"
                if isinstance(r, dict):
                    raw_name = r.get('CompanyName', r.get('證券名稱', '未知'))
                    if not raw_code: raw_code = r.get('SecuritiesCompanyCode', r.get('證券代號', ''))
                elif isinstance(r, list):
                    # 如果是 Web API 格式，Index 3 是名稱
                    if len(r) > 3: raw_name = clean_html(r[3])
                    if not raw_code and len(r) > 2: raw_code = clean_html(r[2])

                # 4. 暴力搜分盤資訊
                level = "5分盤"
                if "20分鐘" in row_str or "二十分鐘" in row_str: level = "20分盤"
                elif "45分鐘" in row_str or "四十五分鐘" in row_str: level = "45分盤"
                elif "60分鐘" in row_str: level = "60分盤"
                elif "第二次" in row_str: level = "20分盤"

                if raw_code and raw_code.isdigit() and len(raw_code) == 4:
                    # 如果沒抓到日期，暫時用空白，但一定要加進去，不能讓它消失
                    if not pure_end_date: 
                        pure_end_date = "日期未抓取"
                        full_period = "日期未抓取"

                    data.append({
                        "market": "上櫃",
                        "code": raw_code,
                        "name": raw_name,
                        "publish_date": "", # OpenAPI 可能沒這欄，不重要
                        "period": full_period,      # 顯示用：完整區間
                        "reason": "", 
                        "level": level,
                        "end_date": pure_end_date,  # 邏輯用：只存結束日
                        "countdown": countdown
                    })
            except Exception as ex: 
                # print(f"解析錯誤: {ex}")
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
    
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        old_codes = {s['code'] for s in valid_old_stocks}
        if code not in old_codes and len(old_codes) > 0:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        
        new_processed.append({
            **s, "price": price, "change": change
        })

    new_processed.sort(key=lambda x: x['countdown'])

    recently_exited = []
    
    # 1. 檢查舊處置股是否消失
    for old_s in valid_old_stocks:
        if old_s['code'] not in new_codes:
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.append(old_s)

    # 2. 檢查剛出關清單 (復活機制)
    for ex in old_data.get('exited_stocks', []):
        try:
            if ex['code'] in new_codes: continue
            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5:
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

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
