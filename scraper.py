import requests
import pandas as pd
import yfinance as yf
import json
import os
import re
from datetime import datetime, date, timedelta

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

def clean_str(s):
    if s is None: return ""
    return str(s).replace('～', '~').replace(' ', '').strip()

def roc_to_ad_str(roc_date_str):
    """將 115/01/20 轉為 2026-01-20"""
    try:
        parts = re.split(r'[-/]', roc_date_str)
        if len(parts) == 3:
            y = int(parts[0]) + 1911
            return f"{y}-{parts[1]}-{parts[2]}"
    except: pass
    return datetime.now().strftime("%Y-%m-%d")

def parse_dates(period_str):
    """
    萬能日期解析
    回傳: (倒數天數, 結束日期ROC, 完整區間)
    注意：這裡不再鎖定倒數天數 >= 0，允許回傳負數
    """
    try:
        text = clean_str(period_str)
        matches = re.findall(r'(\d{3})[-/](\d{2})[-/](\d{2})', text)
        
        if not matches:
            matches = re.findall(r'(\d{3})(\d{2})(\d{2})', text)

        if len(matches) >= 2:
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            y = int(y_end)
            y = y + 1911 if y < 1911 else y
            target = date(y, int(m_end), int(d_end))
            diff = (target - date.today()).days
            
            # 【關鍵修改】這裡直接回傳 diff，不設下限 0
            # 這樣我們才能知道哪些已經過期了 (負數)
            
            end_date_str = f"{y_end}/{m_end}/{d_end}"
            full_period = f"{y_start}/{m_start}/{d_start}~{end_date_str}"
            
            return diff, end_date_str, full_period
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

    # --- 2. 上櫃 (TPEx) - OpenAPI ---
    print("正在抓取上櫃資料 (OpenAPI)...")
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        res = requests.get(url, headers=HEADERS, timeout=15)
        rows = res.json()
        
        print(f"上櫃 OpenAPI 抓到 {len(rows)} 筆")

        for r in rows:
            try:
                raw_code = r.get('SecuritiesCompanyCode', r.get('證券代號', ''))
                raw_name = r.get('CompanyName', r.get('證券名稱', ''))
                raw_period = r.get('DisposePeriod', r.get('處置起迄時間', ''))
                
                raw_code = str(raw_code).strip()
                raw_name = str(raw_name).strip()
                raw_period = str(raw_period).strip()
                
                if not (raw_code.isdigit() and len(raw_code) == 4): continue

                countdown, pure_end_date, full_period = parse_dates(raw_period)
                
                level = "5分盤"
                full_text = str(r)
                if "20分鐘" in full_text or "二十分鐘" in full_text: level = "20分盤"
                elif "45分鐘" in full_text or "四十五分鐘" in full_text: level = "45分盤"
                elif "60分鐘" in full_text: level = "60分盤"
                elif "第二次" in full_text: level = "20分盤"

                data.append({
                    "market": "上櫃",
                    "code": raw_code,
                    "name": raw_name,
                    "publish_date": "", 
                    "period": full_period if full_period else raw_period,
                    "reason": "", 
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
    
    # 抓取新資料
    raw_new = scrape_current()
    
    # 防呆
    if len(raw_new) == 0 and len(old_data.get('disposal_stocks', [])) > 0:
        print("⚠️ 警告：本次未抓到資料，暫時使用舊資料")
        raw_new = old_data['disposal_stocks']
        # 重新計算倒數
        for s in raw_new:
            try:
                y, m, d = re.split(r'[-/]', s['end_date'])
                y = int(y) + 1911 if int(y) < 1911 else int(y)
                s['countdown'] = (date(y, int(m), int(d)) - date.today()).days
            except: s['countdown'] = 0

    new_processed = [] # 準備放「處置中」
    recently_exited = [] # 準備放「剛出關」
    new_codes = set()
    
    # 讀取舊的已出關資料，避免被覆蓋
    old_exited = old_data.get('exited_stocks', [])

    # === 關鍵邏輯修改：分類 ===
    for s in raw_new:
        code = s['code']
        # 取得最新股價
        price, change = get_price(code, s['market'])
        s['price'] = price
        s['change'] = change

        if s['countdown'] >= 0:
            # 倒數天數 >= 0，代表還在處置中 (或今天是最後一天)
            if code not in new_codes:
                new_processed.append(s)
                new_codes.add(code)
        else:
            # 倒數天數 < 0，代表已經結束了 (API 尚未移除)
            # 強制加入「剛出關」清單
            print(f"發現過期股票仍在 API 清單中，強制移至出關: {s['name']} ({s['code']})")
            
            # 設定出關日期為它的結束日期 (轉為西元格式)
            exit_date = roc_to_ad_str(s['end_date'])
            s['exit_date'] = exit_date
            
            # 檢查是否已存在，避免重複
            is_exist = False
            for ex in recently_exited:
                if ex['code'] == code: is_exist = True
            
            if not is_exist:
                recently_exited.append(s)

    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理真正從 API 消失的股票 (原本的出關邏輯) ---
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    
    # 只有當我們有抓到新資料時才做比對
    if len(raw_new) > 0:
        for old_s in valid_old_stocks:
            # 如果舊股票不在「新的處置名單(new_codes)」裡
            # 也不在「剛剛被強制移出(recently_exited)」裡
            if old_s['code'] not in new_codes and old_s['code'] not in [x['code'] for x in recently_exited]:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)

    # --- 整合舊的出關資料 ---
    for ex in old_exited:
        try:
            if not (str(ex['code']).isdigit() and len(str(ex['code'])) == 4): continue
            
            # 如果它現在跑回去處置中了(復活)，就不加
            if ex['code'] in new_codes: continue
            
            # 如果它已經在新的出關清單了，也不加
            if ex['code'] in [x['code'] for x in recently_exited]: continue

            days_diff = (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days
            if days_diff <= 5: # 只保留 5 天內的
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
        
    print(f"=== 執行結束 ===")
    print(f"處置中: {len(new_processed)} 筆")
    print(f"已出關: {len(recently_exited)} 筆")

if __name__ == "__main__":
    main()
