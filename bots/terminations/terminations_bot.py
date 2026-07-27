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
from selenium.webdriver.common.action_chains import ActionChains

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SPREADSHEET_URL  = "https://docs.google.com/spreadsheets/d/1KY1oNpGdaNklVhHvQ8Axgkf149eJPD96M0XBPiOyF2o/edit"
SKIP_TABS        = ["All Data", "2026"]
RIDER_ID_COL     = 2
RESULT_COL       = 9
CHECK_INTERVAL   = 30

ROSTER_URL       = "https://eg.me.logisticsbackoffice.com/dashboard/rooster/workers?filter_status=active_contract&page=1&size=10"

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
CHROME_PROFILE   = os.path.join(BASE_DIR, "chrome_profile_terminations")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    return gspread.authorize(creds)


def get_pending(client):
    spreadsheet = client.open_by_url(SPREADSHEET_URL)
    all_sheets  = spreadsheet.worksheets()
    pending     = []
    for sheet in all_sheets:
        if sheet.title in SKIP_TABS:
            continue
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if len(row) < RIDER_ID_COL:
                continue
            rider_id = row[RIDER_ID_COL - 1].strip()
            result   = row[RESULT_COL - 1].strip() if len(row) >= RESULT_COL else ""
            if rider_id and result == "":
                pending.append({
                    "sheet": sheet,
                    "tab":   sheet.title,
                    "row":   i,
                    "id":    rider_id
                })
    return pending


def update_sheet(entry, value):
    entry["sheet"].update_cell(entry["row"], RESULT_COL, value)


def init_driver():
    options = Options()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def remove_sp(driver, rider_id):
    try:
        wait = WebDriverWait(driver, 10)

        # 1. سرش على الطيار
        search_input = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//*[contains(text(), 'Filter by')]/following::input[1] | //label[contains(text(), 'Worker ID')]/..//input"
        )))
        ActionChains(driver).move_to_element(search_input).click().perform()
        time.sleep(0.5)
        active = driver.switch_to.active_element
        active.send_keys(Keys.CONTROL + "a")
        active.send_keys(Keys.BACKSPACE)
        active.send_keys(rider_id)
        active.send_keys(Keys.ENTER)
        time.sleep(4)

        # 2. اختيار الطيار
        try:
            rider_row = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.XPATH, f"//*[contains(text(), '{rider_id}') and not(self::input)]"))
            )
            rider_row.click()
            time.sleep(2)
        except:
            log(f"  ❌ ID غير موجود: {rider_id}")
            return "not found"

        # 3. دور على Starting Points وسكرول
        try:
            title_element = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, "//*[text()='Starting Points']"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", title_element)
            time.sleep(0.6)
        except:
            log(f"  ❌ مش لاقي Starting Points")
            return "غير قابل"

        # 4. كليك على الـ parent div بتاع الـ input
        try:
            target_input = title_element.find_element(By.XPATH, "./following::input[1]")
            box_to_click = target_input.find_element(By.XPATH, "./parent::div")
            ActionChains(driver).move_to_element(box_to_click).click().perform()
            time.sleep(0.5)
        except:
            log(f"  ⚠️ مش لاقي input")

        # 5. اضغط Delete 12 مرة عشان تمسح الـ SP
        active_input = driver.switch_to.active_element
        for _ in range(12):
            active_input.send_keys(Keys.DELETE)
            time.sleep(0.2)

        time.sleep(0.5)

        # 6. اضغط على السهم V عشان يقفل الـ dropdown
        try:
            dropdown_arrow = title_element.find_element(By.XPATH,
                "./following::div[contains(@class,'Select-arrow-zone') or contains(@class,'Select-arrow')][1]"
            )
            driver.execute_script("arguments[0].click();", dropdown_arrow)
            time.sleep(0.5)
        except:
            # لو مش لاقي السهم اضغط Escape
            active_input.send_keys(Keys.ESCAPE)
            time.sleep(0.5)

        # 7. اضغط Save Changes
        try:
            save_btn = title_element.find_element(By.XPATH, "./following::button[contains(normalize-space(.), 'Save')][1]")
            driver.execute_script("arguments[0].click();", save_btn)
            time.sleep(4.5)
            log(f"  ✅ تم مسح الـ SP وحفظ")
        except Exception as e:
            log(f"  ⚠️ مش لاقي Save: {e}")

        # 8. ESC
        try:
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
        except:
            pass

        # 9. ارجع للقايمة
        try:
            ActionChains(driver).move_to_element(search_input).click().perform()
            active = driver.switch_to.active_element
            active.send_keys(Keys.CONTROL + "a")
            active.send_keys(Keys.BACKSPACE)
            active.send_keys(Keys.ENTER)
            time.sleep(2)
        except:
            pass

        return "Removed SP"

    except Exception as e:
        log(f"  ❌ خطأ: {e}")
        return "غير قابل"


def main():
    print("=" * 55)
    print("   Terminations Bot")
    print("=" * 55)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ مش لاقي credentials.json في: {BASE_DIR}")
        input("اضغط Enter للخروج...")
        return

    try:
        client = get_client()
        log("✅ متصل بـ Google Sheets.")
    except Exception as e:
        log(f"❌ فشل الاتصال: {e}")
        input("اضغط Enter للخروج...")
        return

    driver = init_driver()
    try:
        driver.get(ROSTER_URL)
        time.sleep(4)

        while "workers" not in driver.current_url.lower():
            print("\nلو طلب منك login، سجل دخول وبعدين اضغط Enter هنا...")
            input("اضغط Enter لما تكون جاهز ✅ ")
            time.sleep(2)

        log(f"🔄 البوت يعمل — بيتحقق كل {CHECK_INTERVAL} ثانية...")

        while True:
            pending = get_pending(client)
            if pending:
                log(f"⏳ وُجد {len(pending)} طيار للمعالجة.")
                success = 0
                fail    = 0

                for i, entry in enumerate(pending, 1):
                    log(f"[{i}/{len(pending)}] Tab: {entry['tab']} | ID: {entry['id']}")
                    result = remove_sp(driver, entry["id"])
                    update_sheet(entry, result)
                    log(f"  📝 ← '{result}'")
                    if result == "Removed SP":
                        success += 1
                    else:
                        fail += 1
                    time.sleep(2)

                log(f"✅ خلص | نجح: {success} | فشل: {fail}")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 👀 لا يوجد طيارين معلقين — انتظار {CHECK_INTERVAL}s...", end="\r")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("\n🛑 البوت أوقفه المستخدم.")
    finally:
        log("🏁 إغلاق المتصفح.")
        driver.quit()


if __name__ == "__main__":
    main()
