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

def roc_to_ad_str(roc_date_str):
    """將 115/01/20 轉為 2026-01-20，用於設定出關日期"""
    try:
        parts = re.split(r'[-/]', roc_date_str)
        if len(parts) == 3:
            y = int(parts[0]) + 1911
            return f"{y}-{parts[1]}-{parts[2]}"
    except: pass
    return datetime.now().strftime("%Y-%m-%d")

def extract_dates_from_row(row_dict):
    """
    不指定欄位，直接掃描整筆資料的所有 Values，找出日期
    回傳: (倒數天數, 結束日期, 完整區間)
    """
    try:
        # 把整筆資料的所有值串在一起變成一個大字串
        full_text = " ".join([str(v) for v in row_dict.values()])
        full_text = clean_str(full_text)
        
        # 策略1: 抓取標準格式 115/01/20
        matches = re.findall(r'(\d{3})[-/](\d{2})[-/](\d{2})', full_text)
        
        # 策略2: 如果抓不到，抓取連續數字格式 1150120 (OpenAPI 常見格式)
        if not matches:
            matches = re.findall(r'(\d{3})(\d{2})(\d{2})', full_text)

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
            
            # 【修正】：移除 if diff >= 0 的限制，允許回傳負數以便判斷過期
            return diff, end_date_str, full_period
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

                    # 這裡用原本的解析邏輯
                    countdown, pure_end_date, _ = extract_dates_from_row({'p': raw_period})
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

    # --- 2. 上櫃 (TPEx) - OpenAPI 正規軍 ---
    print("正在抓取上櫃資料 (OpenAPI)...")
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        res = requests.get(url, headers=HEADERS, timeout=15)
        rows = res.json()
        
        print(f"上櫃 OpenAPI 連線成功！共 {len(rows)} 筆 raw data")
        
        for r in rows:
            try:
                # 1. 抓代號 & 名稱 (優先用已知 Key，失敗則掃描)
                raw_code = str(r.get('SecuritiesCompanyCode', r.get('證券代號', ''))).strip()
                raw_name = str(r.get('CompanyName', r.get('證券名稱', ''))).strip()
                
                # 如果 Key 抓不到，嘗試從 Values 裡找 4 位數
                if not raw_code:
                    for v in r.values():
                        if str(v).isdigit() and len(str(v)) == 4:
                            raw_code = str(v)
                            break

                # 【嚴格過濾】只留 4 位數字 (踢掉權證 36913 等)
                if not (raw_code.isdigit() and len(raw_code) == 4):
                    continue

                # 2. 全欄位掃描找日期 (不管日期欄位叫什麼，直接搜內容)
                countdown, pure_end_date, full_period = extract_dates_from_row(r)
                
                # 3. 判斷分盤 (掃描整筆資料)
                level = "5分盤"
                full_text = str(r)
                if "20分鐘" in full_text or "二十分鐘" in full_text: level = "20分盤"
                elif "45分鐘" in full_text or "四十五分鐘" in full_text: level = "45分盤"
                elif "60分鐘" in full_text: level = "60分盤"
                elif "第二次" in full_text: level = "20分盤"

                data.append({
                    "market": "上櫃",
                    "code": raw_code,
                    "name": raw_name if raw_name else "未知",
                    "publish_date": "", 
                    "period": full_period if full_period else "日期未抓取",
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
    
    # 防呆：如果完全沒抓到 (0筆)，且舊資料有東西，則沿用舊資料
    if len(raw_new) == 0 and len(old_data.get('disposal_stocks', [])) > 0:
        print("⚠️ 警告：本次未抓到有效資料，暫時使用舊資料")
        raw_new = old_data['disposal_stocks']
        # 重新計算倒數 (避免舊資料的 countdown 過期)
        for s in raw_new:
            try:
                 # 嘗試重新解析日期來更新倒數
                 diff, _, _ = extract_dates_from_row({'period': s['period']})
                 s['countdown'] = diff
            except: pass

    new_processed = []     # 準備放「處置中」
    recently_exited = []   # 準備放「剛出關」
    new_codes = set()
    
    # 讀取舊的已出關資料
    old_exited = old_data.get('exited_stocks', [])

    # === 【修正重點】分類邏輯：根據 countdown 決定去留 ===
    for s in raw_new:
        code = s['code']
        # 取得最新股價
        price, change = get_price(code, s['market'])
        s['price'] = price
        s['change'] = change

        if s['countdown'] >= 0:
            # 倒數天數 >= 0，代表還在處置中
            if code not in new_codes:
                new_processed.append(s)
                new_codes.add(code)
        else:
            # 倒數天數 < 0，代表已經過期 (API 還沒移除)，強制移送出關
            print(f"發現過期股票: {s['name']} ({s['code']})，移至已出關")
            
            # 設定出關日期 (轉為西元格式以便前端顯示)
            exit_date = roc_to_ad_str(s['end_date'])
            s['exit_date'] = exit_date
            
            # 避免重複加入
            if not any(ex['code'] == code for ex in recently_exited):
                recently_exited.append(s)

    new_processed.sort(key=lambda x: x['countdown'])

    # --- 處理真正從 API 消失的股票 (原本的出關邏輯) ---
    valid_old_stocks = [s for s in old_data.get('disposal_stocks', []) 
                        if str(s['code']).isdigit() and len(str(s['code'])) == 4]
    
    # 只有當我們有抓到新資料時才做比對
    if len(raw_new) > 0:
        for old_s in valid_old_stocks:
            # 如果舊股票不在「新的處置名單」且不在「剛被移出的過期名單」
            if old_s['code'] not in new_codes and old_s['code'] not in [x['code'] for x in recently_exited]:
                p, c = get_price(old_s['code'], old_s['market'])
                old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
                recently_exited.append(old_s)

    # --- 整合舊的出關資料 ---
    for ex in old_exited:
        try:
            if not (str(ex['code']).isdigit() and len(str(ex['code'])) == 4): continue
            
            # 如果它現在復活了(回到處置中)，就不加
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
        
    print(f"=== 執行結束，成功處理 {len(new_processed)} 筆資料 ===")
    print(f"=== 已出關資料共 {len(recently_exited)} 筆 ===")

if __name__ == "__main__":
    main()
