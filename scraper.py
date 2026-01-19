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
        hist = ticker.history(period="1d", timeout=10)
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', hist['Open'].iloc[0])
        change = round(((close - prev) / prev) * 100, 2)
        return close, change
    except: return "N/A", "N/A"

def calc_countdown(period_str):
    try:
        # 從 "115/01/01-115/01/15" 中抓出結束日
        if '-' in period_str:
            end_date_str = period_str.split('-')[1]
        else:
            end_date_str = period_str
            
        parts = end_date_str.split('/')
        y = int(parts[0])
        y = y + 1911 if y < 1911 else y
        target = date(y, int(parts[1]), int(parts[2]))
        diff = (target - date.today()).days
        return diff if diff >= 0 else 0
    except: return 0

def smart_parse_row(row, market):
    """智慧辨識每一欄的資料"""
    item = {"market": market, "code": "", "name": "", "period": "", "reason": "", "date": ""}
    
    # 將所有欄位轉字串並去除空白
    row_str = [str(x).strip() for x in row]
    
    for cell in row_str:
        # 1. 辨識期間 (特徵：長度>10 且 包含 - 和 / )
        # 例如: 115/01/13-115/01/26
        if '-' in cell and '/' in cell and len(cell) > 12:
            item['period'] = cell
            continue
            
        # 2. 辨識代號 (特徵：純數字 且 長度剛好等於 4)
        if cell.isdigit() and len(cell) == 4:
            item['code'] = cell
            continue
            
        # 3. 辨識原因 (特徵：有中文關鍵字 或 長度很長)
        if "處置" in cell or "撮合" in cell or "分鐘" in cell or len(cell) > 15:
            if '-' not in cell: # 排除期間
                item['reason'] = cell
                continue

        # 4. 辨識日期 (特徵：有 / 但沒 - )
        if '/' in cell and '-' not in cell and len(cell) < 12:
            item['date'] = cell
            continue

        # 5. 辨識股名 (特徵：剩下的非數字、長度短)
        # 排除序號(如 "1", "2") 和日期
        if not cell.isdigit() and '/' not in cell and len(cell) < 10:
             item['name'] = cell

    return item

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
                    # 使用智慧辨識取代固定索引
                    parsed = smart_parse_row(r, "上市")
                    
                    # 雙重確認：如果沒抓到股名，可能原因欄位太短被誤判，嘗試修補
                    if not parsed['name'] and parsed['code']:
                        # 通常 row[3] 或 row[4] 是名字，這裡做個簡單的備援
                        # 但依靠 smart_parse 應該就夠了
                        pass

                    if parsed['code']: # 只有抓到代號才算有效資料
                        data.append(parsed)
                except Exception as e:
                    print(f"Row error: {e}")
    except Exception as e:
        print(f"上市抓取失敗: {e}")

    # --- 2. 抓取上櫃 (TPEx) JSON API ---
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
                    # 上櫃資料通常比較乾淨，含有 HTML 標籤需移除
                    # 先把 list 裡的 HTML tag 清掉再丟給智慧辨識
                    clean_row = []
                    for cell in r:
                        clean_text = re.sub('<[^<]+?>', '', str(cell)) # 移除 HTML
                        clean_row.append(clean_text)
                    
                    parsed = smart_parse_row(clean_row, "上櫃")
                    if parsed['code']:
                        data.append(parsed)
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
    
    old_codes = {s['code'] for s in old_data.get('disposal_stocks', [])}
    
    raw_new = scrape_current()
    
    if len(raw_new) == 0:
        print("⚠️ 警告：沒有抓到任何處置股，請確認網站是否改版")
    
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        if code not in old_codes:
            tg_msg_list.append(s)
            
        # 抓取股價
        price, change = get_price(code, s['market'])
        
        # 判斷處置等級
        # 優先檢查是否包含 "20分鐘" (最嚴重) -> "45分鐘" -> 預設 "5分盤"
        reason_text = s['reason']
        if "20分鐘" in reason_text or "二十分鐘" in reason_text:
            level = "20分盤"
        elif "45分鐘" in reason_text: # 處置二可能會有
            level = "45分盤"
        elif "60分鐘" in reason_text:
            level = "60分盤"
        else:
            level = "5分盤"
        
        new_processed.append({
            **s, 
            "price": price, 
            "change": change, 
            "level": level, 
            "countdown": calc_countdown(s['period']) # 使用 period 來算倒數
        })

    # 排序：倒數天數少的排前面
    new_processed.sort(key=lambda x: x['countdown'])

    # 處理出關
    recently_exited = []
    # 1. 保留舊的出關紀錄 (5天內)
    for ex in old_data.get('exited_stocks', []):
        try:
            if (datetime.now() - datetime.strptime(ex['exit_date'], "%Y-%m-%d")).days <= 5:
                recently_exited.append(ex)
        except: pass
    
    # 2. 檢查新出關的
    for old_s in old_data.get('disposal_stocks', []):
        if old_s['code'] not in new_codes:
            # 出關後重新查價
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.insert(0, old_s)

    # 模擬 ETF
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    # 發送通知
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
