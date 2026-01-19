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
    # 防呆：再次確認只有 4 位數才查價
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
    """清除字串中的特殊符號與 HTML"""
    if not s: return ""
    # 移除 HTML
    s = re.sub(r'<[^<]+?>', '', str(s))
    # 統一分隔符號
    return s.replace('～', '~').replace(' ', '').strip()

def parse_dates_from_text(text):
    """
    從任意文字中暴力抓取日期區間 (格式: 115/01/20)
    回傳: (倒數天數, 結束日期, 完整區間字串)
    """
    try:
        cleaned = clean_str(text)
        # 抓取所有日期 (民國年 3碼 + 月 2碼 + 日 2碼)
        matches = re.findall(r'(\d{3})[-/~](\d{2})[-/~](\d{2})', cleaned)
        
        if len(matches) >= 2:
            # 假設最後一個是結束日，倒數第二個是開始日
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            # 計算倒數
            y = int(y_end)
            y = y + 1911 if y < 1911 else y
            target = date(y, int(m_end), int(d_end))
            diff = (target - date.today()).days
            
            end_date_str = f"{y_end}/{m_end}/{d_end}"
            full_period = f"{y_start}/{m_start}/{d_start}~{end_date_str}"
            
            return (diff if diff >= 0 else 0), end_date_str, full_period
    except: pass
    return 0, "", ""

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

                    # 【嚴格過濾】只有 4 位數才要，其他直接跳過
                    if not (raw_code.isdigit() and len(raw_code) == 4):
                        continue

                    level = "5分盤"
                    if "第二次" in raw_measure: level = "20分盤"
                    elif "20分鐘" in raw_detail or "二十分鐘" in raw_detail: level = "20分盤"
                    elif "45分鐘" in raw_detail or "四十五分鐘" in raw_detail: level = "45分盤"
                    elif "60分鐘" in raw_detail: level = "60分盤"

                    countdown, pure_end_date, _ = parse_dates_from_text(raw_period)
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

    # --- 2. 上櫃 (TPEx) - OpenAPI + 暴力搜索 ---
    print("正在抓取上櫃資料 (OpenAPI)...")
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        res = requests.get(url, headers=HEADERS, timeout=15)
        rows = res.json()
        
        # OpenAPI 沒資料時備援 Web API
        if not rows:
             print("OpenAPI 無資料，嘗試 Web API...")
             url_web = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
             res_web = requests.get(url_web, headers={'Referer': 'https://www.tpex.org.tw/'}, timeout=15)
             rows = res_web.json().get('aaData', [])

        print(f"上櫃抓到 {len(rows)} 筆 (含非個股)")
        
        for r in rows:
            try:
                row_str = json.dumps(r, ensure_ascii=False)
                
                # 1. 抓取代號
                raw_code = ""
                if isinstance(r, dict):
                    raw_code = str(r.get('SecuritiesCompanyCode', r.get('證券代號', ''))).strip()
                elif isinstance(r, list) and len(r) > 2:
                    # Web API 格式
                    raw_code = clean_str(r[2])

                # 如果 Key 抓不到，用 Regex 補救 (只抓 4 位數)
                if not raw_code:
                    code_match = re.search(r'[^0-9]([1-9]\d{3})[^0-9]', " " + row_str + " ")
                    if code_match: raw_code = code_match.group(1)
                
                # 【嚴格過濾】重點在這：如果不是 4 位數，直接下一位
                if not (raw_code.isdigit() and len(raw_code) == 4):
                    continue

                # 2. 抓取名稱
                raw_name = "未知"
                if isinstance(r, dict):
                    raw_name = r.get('CompanyName', r.get('證券名稱', '未知'))
                elif isinstance(r, list) and len(r) > 3:
                    raw_name = clean_str(r[3])

                # 3. 解析日期
                # 優先嘗試欄位
                raw_period = ""
                if isinstance(r, dict):
                    raw_period = r.get('DisposePeriod', r.get('處置起迄時間', ''))
                
                countdown, pure_end_date, full_period = parse_dates_from_text(raw_period)
                if not pure_end_date:
                     # 欄位空的？搜整行
                     countdown, pure_end_date, full_period = parse_dates_from_text(row_str)

                # 4. 判斷分盤
                level = "5分盤"
                if "20分鐘" in row_str or "二十分鐘" in row_str: level = "20分盤"
                elif "45分鐘" in row_str or "四十五分鐘" in row_str: level = "45分盤"
                elif "60分鐘" in row_str: level = "60分盤"
                elif "第二次" in row_str: level = "20分盤"

                # 如果沒抓到日期，給個預設值，但資料一定要留著
                if not pure_end_date: 
                    pure_end_date = "日期未抓取"
                    full_period = raw_period if raw_period else "日期未抓取"

                data.append({
                    "market": "上櫃",
                    "code": str(raw_code),
                    "name": raw_name if raw_name else "未知",
                    "publish_date": "", 
                    "period": full_period,      
                    "reason": "", 
                    "level": level,
                    "end_date": pure_end_date,
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
    
    # 抓取新資料
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        price, change = get_price(code, s['market'])
        
        new_processed.append({
            **s, "price": price, "change": change
        })

    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理出關 ---
    recently_exited = []
    
    # 讀取舊的處置名單 (一樣只要 4 位數)
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]

    # 1. 檢查舊處置股是否消失
    # 只有當我們確定有抓到資料時 (raw_new > 0)，才敢判斷別人出關
    if len(raw_new) > 0:
        for old_s in valid_old_stocks:
            if old_s['code'] not in new_codes:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)
    else:
        print("⚠️ 警告：本次未抓到任何資料，保留舊資料防止誤判")
        new_processed = valid_old_stocks

    # 2. 檢查剛出關清單 (復活機制)
    for ex in old_data.get('exited_stocks', []):
        try:
            if ex['code'] in new_codes: continue
            
            # 確保出關的也是 4 位數 (過濾舊髒資料)
            if not (str(ex['code']).isdigit() and len(str(ex['code'])) == 4):
                continue

            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5:
                if ex['code'] not in [x['code'] for x in recently_exited]:
                    recently_exited.append(ex)
        except: pass

    # 產生通知
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
