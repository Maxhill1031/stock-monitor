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

# --- 關鍵修正：取得台灣時間 ---
def get_tw_now():
    # GitHub 主機是 UTC，台灣是 UTC+8
    return datetime.utcnow() + timedelta(hours=8)

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
    return get_tw_now().strftime("%Y-%m-%d")

def extract_dates_from_row(row_dict):
    """
    不指定欄位，直接掃描整筆資料的所有 Values，找出日期
    回傳: (倒數天數, 結束日期, 完整區間)
    """
    try:
        full_text = " ".join([str(v) for v in row_dict.values()])
        full_text = clean_str(full_text)
        
        matches = re.findall(r'(\d{3})[-/](\d{2})[-/](\d{2})', full_text)
        if not matches:
            matches = re.findall(r'(\d{3})(\d{2})(\d{2})', full_text)

        if len(matches) >= 2:
            y_end, m_end, d_end = matches[-1]
            y_start, m_start, d_start = matches[-2]
            
            y = int(y_end)
            y = y + 1911 if y < 1911 else y
            target = date(y, int(m_end), int(d_end))
            
            # 【關鍵修正】這裡也要用台灣時間的「今天」來計算倒數
            tw_today = get_tw_now().date()
            diff = (target - tw_today).days
            
            end_date_str = f"{y_end}/{m_end}/{d_end}"
            full_period = f"{y_start}/{m_start}/{d_start}~{end_date_str}"
            
            return diff, end_date_str, full_period
    except: pass
    return 0, "", ""

def scrape_current():
    data = []
    
    # --- 1. 上市 (TWSE) ---
    print("正在抓取上市資料...")
    try:
        res = requests.get("https://www.twse.
