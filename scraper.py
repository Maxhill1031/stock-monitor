import requests
import json
import re

# 這是櫃買中心官方的 Open Data API，專門給程式接的，不會擋 GitHub IP
URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"

def test_scrape():
    print(f"1. 正在連線到: {URL}")
    
    try:
        # 模擬瀏覽器 Header，雖然 OpenAPI 通常不需要，但加了保險
        headers = {
            'User-Agent': 'Mozilla/5.0'
        }
        res = requests.get(URL, headers=headers, timeout=10)
        data = res.json()
        
        print(f"2. 連線成功！總共抓到 {len(data)} 筆資料")
        
        if len(data) == 0:
            print("❌ 警告：回傳了空清單 (Empty List)。可能是官方資料庫目前真的沒資料，或 API 異常。")
            return

        print("-" * 30)
        print("3. 開始檢測前 5 筆資料的日期欄位：")
        
        for i, row in enumerate(data[:5]): # 只看前5筆
            code = row.get('SecuritiesCompanyCode', '無代號')
            name = row.get('CompanyName', '無名稱')
            raw_period = row.get('DisposePeriod', '無日期欄位')
            
            print(f"\n[第 {i+1} 筆] {name} ({code})")
            print(f"   ➤ 原始日期欄位 (DisposePeriod): 「{raw_period}」")
            
            # 測試解析
            # OpenAPI 的日期格式通常是 1150120 或 115/01/20
            # 我們來測試能不能切出來
            dates = re.findall(r'\d{3}[./-]?\d{2}[./-]?\d{2}', str(raw_period))
            if dates:
                print(f"   ✅ 成功解析出日期: {dates}")
            else:
                print(f"   ❌ 解析失敗，請檢查原始欄位內容")

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")

if __name__ == "__main__":
    test_scrape()
