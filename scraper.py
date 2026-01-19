import requests
import pandas as pd
import yfinance as yf
import json
import os
from datetime import datetime, date

# --- 設定區 (自動讀取 GitHub 設定的密碼) ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# --- 輔助函式 ---

def send_tg(message):
    """發送 Telegram 通知"""
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def get_price(code, market):
    """抓即時股價"""
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        hist = ticker.history(period="1d")
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', hist['Open'].iloc[0])
        change = round(((close - prev) / prev) * 100, 2)
        return close, change
    except: return "N/A", "N/A"

def calc_countdown(end_date_str):
    """計算倒數日"""
    try:
        parts = end_date_str.split('/') # 格式 113/05/20
        y = int(parts[0])
        y = y + 1911 if y < 1911 else y
        target = date(y, int(parts[1]), int(parts[2]))
        diff = (target - date.today()).days
        return diff if diff >= 0 else 0
    except: return 0

def scrape_current():
    """抓取當下最新名單"""
    data = []
    # 上市
    try:
        res = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json").json()
        if res['stat'] == 'OK':
            for r in res['data']:
                data.append({"market":"上市","code":r[1],"name":r[2],"reason":r[3],"period":r[4],"end_date":r[4].split('-')[1]})
    except: pass
    # 上櫃
    try:
        dfs = pd.read_html("https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information.php?l=zh-tw", header=0)
        if dfs:
            for _, r in dfs[0].iterrows():
                p = str(r['處置期間'])
                data.append({"market":"上櫃","code":str(r['證券代號']),"name":str(r['證券名稱']),"reason":str(r['處置措施']),"period":p,"end_date":p.split('-')[1] if '-' in p else p})
    except: pass
    return data

# --- 主程式 ---
def main():
    # 1. 讀取舊資料 (記憶)
    old_data = {"disposal_stocks": [], "exited_stocks": []}
    if os.path.exists('data.json'):
        try:
            with open('data.json','r',encoding='utf-8') as f: old_data = json.load(f)
        except: pass
    
    old_codes = {s['code'] for s in old_data.get('disposal_stocks', [])}
    
    # 2. 抓新資料
    raw_new = scrape_current()
    new_processed = []
    new_codes = set()
    tg_msg_list = []

    for s in raw_new:
        code = s['code']
        new_codes.add(code)
        
        # 判斷新進榜
        if code not in old_codes:
            tg_msg_list.append(s)
            
        # 補全資訊
        price, change = get_price(code, s['market'])
        level = "20分盤" if "20分鐘" in s['reason'] else ("45分盤" if "45分鐘" in s['reason'] else "5分盤")
        
        new_processed.append({
            **s, "price": price, "change": change, "level": level, "countdown": calc_countdown(s['end_date'])
        })

    new_processed.sort(key=lambda x: x['countdown'])

    # 3. 處理「剛出關」 (舊的有，新的沒有)
    recently_exited = []
    # 先把舊的出關名單拿進來，並過濾掉超過 5 天的
    for ex in old_data.get('exited_stocks', []):
        try:
            d = datetime.strptime(ex['exit_date'], "%Y-%m-%d")
            if (datetime.now() - d).days <= 5: recently_exited.append(ex)
        except: pass
    
    # 檢查誰今天剛出關
    for old_s in old_data.get('disposal_stocks', []):
        if old_s['code'] not in new_codes:
            # 抓出關後的最新價
            p, c = get_price(old_s['code'], old_s['market'])
            old_s.update({"price": p, "change": c, "exit_date": datetime.now().strftime("%Y-%m-%d")})
            recently_exited.insert(0, old_s) # 加到最前面

    # 4. ETF 資料 (需手動維護或另外寫爬蟲，這裡放範例)
    etf_data = [
        {"code":"00940","name":"元大臺灣價值高息","action":"新增","stock":"長榮航(2618)","date":"2026-05-17"},
        {"code":"00878","name":"國泰永續高股息","action":"刪除","stock":"英業達(2356)","date":"2026-05-20"}
    ]

    # 5. 發送通知
    if tg_msg_list:
        msg = "🚨 **台股處置新增**\n" + "\n".join([f"{x['name']}({x['code']})" for x in tg_msg_list])
        send_tg(msg)

    # 6. 存檔
    final_output = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disposal_stocks": new_processed,
        "exited_stocks": recently_exited,
        "etf_stocks": etf_data
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()