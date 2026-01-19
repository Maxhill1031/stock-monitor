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
    解析日期：處理 115/01/20~115/02/02
    """
    try:
        # 1. 統一分隔符號，把全形～半形~都換成空格，方便 regex
        clean_str = str(period_str).replace('～', ' ').replace('~', ' ').replace('-', ' ')
        
        # 2. 抓取所有像是日期的字串
        matches = re.findall(r'(\d{3})[/](\d{2})[/](\d{2})', clean_str)
        
        if matches:
            # 取最後一組當作結束日
            y_str, m_str, d_str = matches[-1]
            y = int(y_str)
            y = y + 1911 if y < 1911 else y # 民國轉西元
            
            target = date(y, int(m_str), int(d_str))
            diff = (target - date.today()).days
            return diff if diff >= 0 else 0
    except: return 0
    return 0

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
                    # [1]公布日 [2]代號 [3]名稱 [6]期間 [7]措施
                    raw_pub_date = str(r[1]).strip()
                    raw_code = str(r[2]).strip()
                    raw_name = str(r[3]).strip()
                    raw_period = str(r[6]).strip()
                    raw_measure = str(r[7]).strip()
                    raw_detail = str(r[8]).strip()

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
                            "publish_date": raw_pub_date,
                            "period": raw_period,
                            "reason": raw_measure,
                            "level": level,
                            "end_date": raw_period
                        })
                except: continue
    except Exception as e: print(f"上市錯誤: {e}")

    # --- 2. 上櫃 (TPEx) - 指定欄位版 ---
    print("正在抓取上櫃資料 (Web API)...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php?l=zh-tw&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        js = res.json()
        rows = js.get('aaData', [])
        print(f"上櫃抓到 {len(rows)} 筆")
        
        for r in rows:
            try:
                # 依照你的截圖指定索引 (0-based index)
                # 截圖欄位2(公布日期) -> Index 1
                # 截圖欄位3(證券代號) -> Index 2
                # 截圖欄位4(證券名稱) -> Index 3
                # 截圖欄位6(處置區間) -> Index 5
                
                raw_pub_date = clean_html(r[1])
                raw_code = clean_html(r[2])
                raw_name = clean_html(r[3])
                raw_period = clean_html(r[5])
                
                # 判斷分盤：檢查整行內容
                full_row_str = str(r)
                level = "5分盤"
                if "20分鐘" in full_row_str or "二十分鐘" in full_row_str: level = "20分盤"
                elif "45分鐘" in full_row_str: level = "45分盤"
                elif "60分鐘" in full_row_str: level = "60分盤"
                elif "第二次" in full_row_str: level = "20分盤"

                # 只要代號正確，就算日期有問題，也先加進去 (避免被誤判為出關)
                if raw_code.isdigit() and len(raw_code) == 4:
                    
                    # 嘗試修復日期格式：如果抓到的日期包含HTML，再清一次
                    if '<' in raw_period:
                         raw_period = clean_html(raw_period)

                    data.append({
                        "market": "上櫃",
                        "code": raw_code,
                        "name": raw_name,
                        "publish_date": raw_pub_date,
                        "period": raw_period,
                        "reason": "", 
                        "level": level,
                        "end_date": raw_period
                    })
            except Exception as ex: 
                print(f"上櫃單筆解析失敗: {ex}")
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
    old_codes = {s['code'] for s in valid_old_stocks}
    
    # 抓取新資料
    raw_new = scrape_current()
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 通知邏輯
        if code not in old_codes and len(old_codes) > 0:
            tg_msg_list.append(s)
            
        price, change = get_price(code, s['market'])
        
        # 計算倒數，如果日期格式真的很爛算不出來，會回傳 0
        # 但資料本身已經被加入 new_processed，所以不會跑去出關區
        countdown = calc_countdown(s['end_date'])
        
        new_processed.append({
            **s, "price": price, "change": change, "countdown": countdown
        })

    # 排序：倒數天數少的排前面，如果天數是 0 (日期解析失敗的) 也會排在一起方便檢查
    new_processed.sort(key=lambda x: x['countdown'])

    # === 關鍵：只有當新名單裡「真的沒有」該代號時，才算已出關 ===
    recently_exited = []
    for old_s in valid_old_stocks:
        if old_s['code'] not in new_codes:
            # 這裡代表 old_s 在今天的名單 raw_new 裡完全找不到，才算真的出關
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.append(old_s)

    for ex in old_data.get('exited_stocks', []):
        try:
            if str(ex['code']).isdigit() and len(str(ex['code'])) == 4:
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
