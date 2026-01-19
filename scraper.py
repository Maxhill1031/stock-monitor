import requests
import pandas as pd
import yfinance as yf
import json
import os
import time
from datetime import datetime, date
from io import StringIO

# --- 設定區 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# 偽裝成 Chrome 瀏覽器 (關鍵！)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.twse.com.tw/zh/announcement/punish.html'
}

def send_tg(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"TG Error: {e}")

def get_price(code, market):
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        ticker = yf.Ticker(f"{code}{suffix}")
        # 增加 timeout 避免卡死
        hist = ticker.history(period="1d", timeout=10)
        if hist.empty: return "N/A", "N/A"
        close = round(hist['Close'].iloc[-1], 2)
        prev = ticker.info.get('previousClose', hist['Open'].iloc[0])
        change = round(((close - prev) / prev) * 100, 2)
        return close, change
    except: return "N/A", "N/A"

def calc_countdown(end_date_str):
    try:
        parts = end_date_str.split('/')
        y = int(parts[0])
        y = y + 1911 if y < 1911 else y
        target = date(y, int(parts[1]), int(parts[2]))
        diff = (target - date.today()).days
        return diff if diff >= 0 else 0
    except: return 0

def scrape_current():
    data = []
    
    # 1. 抓取上市 (TWSE)
    print("正在抓取上市資料...")
    try:
        url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        
        # 檢查是否被擋
        if res.status_code != 200:
            print(f"上市抓取失敗，狀態碼: {res.status_code}")
        else:
            js = res.json()
            if js['stat'] == 'OK':
                print(f"上市成功抓到 {len(js['data'])} 筆")
                for r in js['data']:
                    data.append({
                        "market": "上市",
                        "code": str(r[1]),
                        "name": str(r[2]),
                        "reason": str(r[3]),
                        "period": str(r[4]),
                        "end_date": r[4].split('-')[1]
                    })
            else:
                print(f"上市回傳狀態非 OK: {js.get('stat')}")
    except Exception as e:
        print(f"上市抓取發生錯誤: {e}")

    # 2. 抓取上櫃 (TPEx)
    print("正在抓取上櫃資料...")
    try:
        url = "https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information.php?l=zh-tw"
        # 先用 requests 抓取 HTML 文字，避免 pandas 直接被擋
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.encoding = 'utf-8' # 強制編碼
        
        if res.status_code == 200:
            #
