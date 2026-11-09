"""
定位模組（開發測試用）
====================================
重要說明：這個模組只給「筆電開發階段」使用，不是正式系統的定位方式。

正式系統（手機/眼鏡）的GPS定位：
    iOS 用 CoreLocation，Android 用 FusedLocationProviderClient，
    這些是手機/眼鏡上才有GPS晶片可以呼叫的原生功能，筆電沒有GPS硬體，
    所以沒辦法在筆電上真正測試「手機GPS」這件事。

這個模組的用途：
    在筆電上開發時，用「IP定位」取得一個大概的座標，讓你可以先測試
    「有了座標之後，後面的路線/地點/交通資訊邏輯能不能正常運作」。
    IP定位精確度只到城市等級（可能誤差幾公里），不能拿來測試「導航準不準」，
    只能拿來測試「資料串接邏輯對不對」。

    等你手機/眼鏡到位後，這個模組要整個替換成呼叫裝置原生GPS API，
    main.py 呼叫的介面（get_current_location()）不用改，方便之後直接替換。
"""

import requests


def get_current_location() -> dict:
    """
    開發測試用：透過IP位址取得大概位置。
    正式系統這裡要換成手機/眼鏡的GPS API呼叫。

    Returns:
        dict: {"lat": float, "lng": float}，取得失敗時回傳台北市中心當預設值
    """
    try:
        # ip-api.com 是免費的IP定位服務，不需要金鑰，但精確度只到城市等級
        resp = requests.get("http://ip-api.com/json/", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return {"lat": data["lat"], "lng": data["lon"]}
    except Exception as e:
        print(f"   [警告] IP定位失敗，改用預設座標（台北市中心）：{e}")

    # 失敗時的預設值：台北市中心（市政府附近）
    return {"lat": 25.0375, "lng": 121.5637}


if __name__ == "__main__":
    location = get_current_location()
    print(f"目前位置（IP定位，僅供開發測試，精確度不高）：{location}")
    print("提醒：正式系統要改用手機/眼鏡的真實GPS，這裡只是先讓後續模組有座標可以測試")