import os
import sys
import time
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/126WG53_lRImEChRsNfWGSRR3V1ZoVFu5TNZZ829Q48c/edit"
SHEET_NAME = "Revoke Break"
RIDER_ID_COLUMN = 7
STATUS_COLUMN = 9
RESULT_COLUMN = 10
SKIP_STATUSES = ["مقبول", "مرفوض", "تم الرد", "مش بريك"]
CHECK_INTERVAL = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
CHROME_PROFILE = os.path.join(BASE_DIR, "chrome_profile")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(SPREADSHEET_URL).worksheet(SHEET_NAME)


def get_rider_ids(sheet):
    all_rows = sheet.get_all_values()
    rider_ids = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) < RIDER_ID_COLUMN:
            continue
        rider_id = row[RIDER_ID_COLUMN - 1].strip()
        status = row[STATUS_COLUMN - 1].strip() if len(row) >= STATUS_COLUMN else ""
        result = row[RESULT_COLUMN - 1].strip() if len(row) >= RESULT_COLUMN else ""
        if rider_id and rider_id != "Not Found" and status not in SKIP_STATUSES and result not in ["مقبول", "مش بريك", "ID غلط", "بريك سيستم غير قابل للفك"]:
            rider_ids.append({"id": rider_id, "row": i})
    return rider_ids


def update_sheet(sheet, row_num, value):
    sheet.update_cell(row_num, RESULT_COLUMN, value)


def init_driver():
    options = Options()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    return webdriver.Chrome(options=options)


def revoke_break(driver, rider_id, main_only=False):
    try:
        search = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[placeholder*='Search'], input[type='search']"))
        )
        search.click()
        search.send_keys(Keys.CONTROL + "a")
        search.send_keys(Keys.DELETE)
        search.send_keys(rider_id)
        time.sleep(2)

        try:
            result = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, f"//*[contains(text(), '{rider_id}')]"))
            )
            result.click()
            time.sleep(2)
        except:
            search.click()
            search.send_keys(Keys.CONTROL + "a")
            search.send_keys(Keys.DELETE)
            time.sleep(1)
            return "ID غلط"

        try:
            reset_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//*[text()='Reset' or contains(@class,'reset')]"))
            )

            if main_only:
                try:
                    reason_el = driver.find_element(By.XPATH, "//*[contains(text(), 'Reason:')]")
                    if "Issue::" in reason_el.text:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(1)
                        return "بريك سيستم غير قابل للفك"
                except:
                    pass

            reset_btn.click()
            time.sleep(2)
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
            return "مقبول"

        except:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
            return "مش بريك"

    except Exception as e:
        log(f"  خطأ: {e}")
        return None


def main(main_only=False):
    mode = "Revoke Main Breaks" if main_only else "Revoke All Breaks"
    print("=" * 55)
    print(f"   {mode}")
    print("=" * 55)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ مش لاقي credentials.json في: {BASE_DIR}")
        input("اضغط Enter للخروج...")
        return

    try:
        sheet = get_sheet()
        log("✅ متصل بـ Google Sheets.")
    except Exception as e:
        log(f"❌ مشكلة في الـ Sheet: {e}")
        input("اضغط Enter للخروج...")
        return

    driver = init_driver()
    try:
        driver.get("https://eg.me.logisticsbackoffice.com/dashboard/v2/hurrier/active_couriers?cities=200")
        time.sleep(4)

        print("\nلو طلب منك login، سجل دخول وبعدين اضغط Enter هنا...")
        input("اضغط Enter لما تكون جاهز ✅ ")

        log(f"🔄 البوت يعمل — بيتحقق كل {CHECK_INTERVAL} ثانية...")

        while True:
            pending = get_rider_ids(sheet)
            if pending:
                log(f"⏳ وُجد {len(pending)} طيار للمعالجة.")
                success_count = 0
                not_break_count = 0
                wrong_id_count = 0
                system_break_count = 0
                fail_count = 0

                for i, rider in enumerate(pending, 1):
                    log(f"[{i}/{len(pending)}] ID: {rider['id']} ... ")
                    result = revoke_break(driver, rider["id"], main_only=main_only)

                    if result == "مقبول":
                        log("✅ تم فك البريك")
                        update_sheet(sheet, rider["row"], "مقبول")
                        success_count += 1
                    elif result == "مش بريك":
                        log("⚠️ مش على بريك")
                        update_sheet(sheet, rider["row"], "مش بريك")
                        not_break_count += 1
                    elif result == "ID غلط":
                        log("❌ ID مش موجود")
                        update_sheet(sheet, rider["row"], "ID غلط")
                        wrong_id_count += 1
                    elif result == "بريك سيستم غير قابل للفك":
                        log("🔒 بريك سيستم")
                        update_sheet(sheet, rider["row"], "بريك سيستم غير قابل للفك")
                        system_break_count += 1
                    else:
                        log("❌ فشل")
                        fail_count += 1

                    time.sleep(2)

                log("=" * 55)
                log(f"تم فك البريك: {success_count} | مش بريك: {not_break_count} | بريك سيستم: {system_break_count} | ID غلط: {wrong_id_count} | فشل: {fail_count}")
                log("=" * 55)
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 👀 لا يوجد طيارين معلقين — انتظار {CHECK_INTERVAL}s...", end="\r")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("\n🛑 البوت أوقفه المستخدم.")
    finally:
        log("🏁 إغلاق المتصفح.")
        driver.quit()


if __name__ == "__main__":
    main_only = "--main" in sys.argv
    main(main_only=main_only)
