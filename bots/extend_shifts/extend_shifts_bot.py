import time
import os
import sys
import re
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_URL  = "https://docs.google.com/spreadsheets/d/126WG53_lRImEChRsNfWGSRR3V1ZoVFu5TNZZ829Q48c/edit"
SHEET_TAB_NAME   = "Extend Shifts"
RIDER_ID_COL     = 7   # Column G
SP_STATUS_COL    = 8   # Column H
END_TIME_COL     = 5   # Column E
STATUS_COL       = 9   # Column I
RESULT_COL       = 10  # Column J

SKIP_STATUSES    = ["مقبول", "مرفوض", "غير قابل", "عارض للبدل", "not found", "الشيفت ازيد من اللي مبعوت", "مش حاجز/ شيفته خلص"]
CHECK_INTERVAL   = 30

HURRIER_URL      = "https://eg.me.logisticsbackoffice.com/dashboard/v2/hurrier/active_couriers?cities=200"

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
CHROME_PROFILE   = os.path.join(BASE_DIR, "chrome_profile_extend")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def parse_time(time_str):
    if not time_str:
        return None
    time_str = time_str.strip()
    m = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)', time_str, re.IGNORECASE)
    if m:
        h, mi, p = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if p == 'PM' and h != 12: h += 12
        elif p == 'AM' and h == 12: h = 0
        return h, mi
    m = re.search(r'(\d{1,2}):(\d{2})', time_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None

def build_target_dt(ref_dt, t_hour, t_min):
    if t_hour == 0 and t_min == 0:
        base = ref_dt.date() + timedelta(days=1)
        return datetime(base.year, base.month, base.day, 0, 0)
    target = datetime(ref_dt.year, ref_dt.month, ref_dt.day, t_hour, t_min)
    if target <= ref_dt:
        target += timedelta(days=1)
    return target

def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds  = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(SPREADSHEET_URL).worksheet(SHEET_TAB_NAME)

def get_pending(sheet):
    rows = sheet.get_all_values()
    pending = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < RIDER_ID_COL:
            continue
        rider_id  = row[RIDER_ID_COL - 1].strip()
        sp_status = row[SP_STATUS_COL - 1].strip() if len(row) >= SP_STATUS_COL else ""
        end_time  = row[END_TIME_COL - 1].strip() if len(row) >= END_TIME_COL else ""
        status    = row[STATUS_COL   - 1].strip() if len(row) >= STATUS_COL   else ""
        result    = row[RESULT_COL   - 1].strip() if len(row) >= RESULT_COL   else ""
        if rider_id and rider_id != "Not Found" and end_time and status not in SKIP_STATUSES and result not in SKIP_STATUSES:
            pending.append((i, rider_id, end_time, sp_status))
    return pending

def update_sheet(sheet, row_num, value):
    sheet.update_cell(row_num, RESULT_COL, value)

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

def safe_esc(driver):
    try:
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.3)
    except:
        pass

def hard_reset(driver):
    try:
        btns = driver.find_elements(By.XPATH, "//button[contains(.,'No, Don') or contains(text(),'Cancel')]")
        for btn in btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.2)
    except:
        pass
    safe_esc(driver)

def detect_toast(driver, timeout=7):
    deadline = time.time() + timeout
    while time.time() < deadline:
        green = driver.find_elements(By.XPATH,
            "//*[contains(text(),'has been extended by') or contains(text(),'Shift has been extended')]")
        if any(g.is_displayed() for g in green):
            return "green"
        swap = driver.find_elements(By.XPATH,
            "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pending swap')]")
        if any(s.is_displayed() for s in swap):
            return "swap"
        red = driver.find_elements(By.XPATH,
            "//*[contains(text(),'request conflicts with an existing') or contains(text(),'conflicts with an existing resource')]")
        if any(r.is_displayed() for r in red):
            return "red"
        time.sleep(0.25)
    return "unknown"

TIMESTAMP_RE = re.compile(
    r'[A-Za-z]+\s*\d+\s*(?:st|nd|rd|th)?\s*,?\s*\d{4}[\s,]+\d+:\d+\s*[ap]m',
    re.IGNORECASE
)

def extract_dt(text):
    text = text.lower().strip()
    m = re.search(r'([a-z]+)\s*(\d+)(?:st|nd|rd|th)?\s*,?\s*(\d{4})[\s,]+(\d+):(\d+)\s*(am|pm)', text)
    if not m:
        return None
    months = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,'june':6,'july':7}
    h, mi, p = int(m.group(4)), int(m.group(5)), m.group(6)
    if p == 'pm' and h != 12: h += 12
    elif p == 'am' and h == 12: h = 0
    return datetime(int(m.group(3)), months.get(m.group(1)[:3], 1), int(m.group(2)), h, mi)

def read_row_times(row_el):
    try:
        stamps = TIMESTAMP_RE.findall(row_el.text)
        start  = extract_dt(stamps[0]) if len(stamps) >= 1 else None
        end    = extract_dt(stamps[1]) if len(stamps) >= 2 else (extract_dt(stamps[0]) if stamps else None)
        return start, end
    except:
        return None, None

def click_yes_modal(driver, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        btns = driver.find_elements(By.XPATH, "//button[contains(.,'Yes, Extend') or contains(text(),'Extend The Shift')]")
        for btn in btns:
            if btn.is_displayed():
                try:
                    btn.click(); return True
                except:
                    driver.execute_script("arguments[0].click();", btn); return True
        time.sleep(0.3)
    return False

def get_circles(driver):
    circles = driver.find_elements(By.CSS_SELECTOR, "[data-cy='shift-action-edit']")
    visible = [c for c in circles if c.is_displayed()]
    if not visible:
        visible = [c for c in driver.find_elements(By.XPATH, "//*[contains(@class,'HalfTimeIcon')]") if c.is_displayed()]
    return visible

def process_rider(driver, rider_id, end_time_str):
    try:
        target_time = parse_time(end_time_str)
        if not target_time:
            log(f"  ❌ تعذّر تحليل الوقت: '{end_time_str}'")
            return "غير قابل"
        t_hour, t_min = target_time

        hard_reset(driver)
        log(f"  🔍 بحث (ID: {rider_id})")

        try:
            wait = WebDriverWait(driver, 10)
            search = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "input[placeholder*='Search'], input[type='search']")
            ))
            search.click()
            search.send_keys(Keys.CONTROL + "a")
            search.send_keys(Keys.DELETE)
            search.send_keys(rider_id)
            time.sleep(2)
        except:
            return "غير قابل"

        try:
            no_res = driver.find_elements(By.XPATH, "//*[contains(text(),'No results') or contains(text(),'no results')]")
            if any(e.is_displayed() for e in no_res):
                log("  ❌ ID غير موجود.")
                return "not found"
            result = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
                By.XPATH, f"//*[contains(text(), '{rider_id}')]"
            )))
            result.click()
            time.sleep(2)
        except:
            return "not found"

        try:
            all_shift = driver.find_elements(By.XPATH, "//*[text()='Shift' or contains(text(),'Shift')]")
            shift_tab = None
            for elem in all_shift:
                if elem.is_displayed() and elem.text.strip() == "Shift":
                    shift_tab = elem; break
            if not shift_tab:
                for elem in all_shift:
                    if elem.is_displayed():
                        shift_tab = elem; break
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", shift_tab)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", shift_tab)
            time.sleep(1.5)
        except:
            return "غير قابل"

        circles = get_circles(driver)
        if not circles:
            log("  ⚠️ لا توجد دوائر.")
            return "غير قابل"

        log(f"  📋 {len(circles)} دائرة مرئية.")

        try:
            first_row = circles[0].find_element(By.XPATH, "./ancestor::tr | ./ancestor::div[contains(@class,'row')]")
            first_start, _ = read_row_times(first_row)
            today = datetime.now().date()
            if first_start and first_start.date() != today:
                log(f"  ⏭️ أول شيفت مش النهارده ({first_start.strftime('%b %d')}) — مش حاجز")
                hard_reset(driver)
                return "مش حاجز/ شيفته خلص"
        except:
            pass

        circle_idx    = 0
        max_attempts  = 50
        attempts      = 0
        last_circle   = None
        extended_once = False

        while attempts < max_attempts:
            attempts += 1
            time.sleep(0.5)

            try:
                if circle_idx >= len(circles):
                    if last_circle:
                        driver.execute_script("window.scrollBy(0, 300);")
                        time.sleep(1.5)
                        new_circles = get_circles(driver)
                        if new_circles and len(new_circles) > circle_idx:
                            circles = new_circles
                        else:
                            break
                    else:
                        break

                current = circles[circle_idx]
                last_circle = current

                try:
                    row_el = current.find_element(By.XPATH, "./ancestor::tr | ./ancestor::div[contains(@class,'row')]")
                except:
                    circle_idx += 1; continue

                start_dt, end_dt = read_row_times(row_el)
                if end_dt is None:
                    circle_idx += 1; continue

                ref_dt    = start_dt if start_dt else end_dt
                target_dt = build_target_dt(ref_dt, t_hour, t_min)

                log(f"  ⏰ الدائرة #{circle_idx+1} | الشاشة: {end_dt.strftime('%I:%M %p')} | الهدف: {target_dt.strftime('%I:%M %p')}")

                if end_dt > target_dt:
                    if extended_once:
                        log(f"  ✅ الشيفت اتمد وعدى الهدف — مقبول")
                        hard_reset(driver)
                        return "مقبول"
                    else:
                        log(f"  ⚠️ الشيفت ازيد من اللي مبعوت")
                        hard_reset(driver)
                        return "الشيفت ازيد من اللي مبعوت"

                if end_dt == target_dt:
                    log(f"  🎉 وصلنا للهدف!")
                    hard_reset(driver)
                    return "مقبول"

                try:
                    circle_btn = row_el.find_element(By.XPATH, ".//*[contains(@class,'HalfTimeIcon')]")
                    try:
                        circle_btn = circle_btn.find_element(By.XPATH, "./ancestor::button")
                    except:
                        pass
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", circle_btn)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", circle_btn)
                except:
                    circle_idx += 1; continue

                if not click_yes_modal(driver, timeout=8):
                    safe_esc(driver)
                    circle_idx += 1; continue

                toast = detect_toast(driver, timeout=7)
                log(f"  📢 Toast: [{toast}]")

                if toast == "green":
                    extended_once = True
                    time.sleep(1.5)
                    _, new_end = read_row_times(row_el)
                    if new_end and new_end >= target_dt:
                        hard_reset(driver)
                        return "مقبول"
                    continue

                elif toast == "swap":
                    hard_reset(driver)
                    return "عارض للبدل"

                elif toast == "red":
                    if extended_once:
                        log(f"  ✅ red بعد extend — مقبول")
                        hard_reset(driver)
                        return "مقبول"
                    else:
                        safe_esc(driver)
                        deadline = time.time() + 10
                        while time.time() < deadline:
                            red_still = driver.find_elements(By.XPATH, "//*[contains(text(),'request conflicts with an existing')]")
                            if not any(r.is_displayed() for r in red_still):
                                break
                            time.sleep(0.3)
                        circle_idx += 1; continue

                else:
                    time.sleep(2)
                    _, fallback = read_row_times(row_el)
                    if fallback and fallback != end_dt:
                        extended_once = True
                        if fallback >= target_dt:
                            hard_reset(driver)
                            return "مقبول"
                        continue
                    safe_esc(driver)
                    circle_idx += 1; continue

            except Exception as e:
                err = str(e).lower()
                if "stale" in err:
                    time.sleep(1); continue
                safe_esc(driver)
                circle_idx += 1; continue

        hard_reset(driver)
        return "غير قابل"

    except Exception as e:
        log(f"  ❌ خطأ: {e}")
        hard_reset(driver)
        return "غير قابل"

def main():
    print("=" * 55)
    print("   Nader")
    print("=" * 55)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ مش لاقي credentials.json في: {BASE_DIR}")
        input("اضغط Enter للخروج...")
        return

    try:
        sheet = get_sheet()
        log("✅ متصل بـ Google Sheets.")
    except Exception as e:
        log(f"❌ فشل الاتصال: {e}")
        input("اضغط Enter للخروج...")
        return

    driver = init_driver()
    driver.get(HURRIER_URL)
    time.sleep(4)

    print("\nلو طلب منك login، سجل دخول وبعدين اضغط Enter هنا...")
    input("اضغط Enter لما تكون جاهز ✅ ")

    log(f"🔄 البوت يعمل — بيتحقق كل {CHECK_INTERVAL} ثانية...")

    try:
        while True:
            pending = get_pending(sheet)
            if pending:
                log(f"⏳ وُجد {len(pending)} صف للمعالجة.")
                for row_num, rider_id, end_time, sp_status in pending:
                    log(f"🔄 صف [{row_num}] | ID: {rider_id} | الهدف: {end_time}")

                    # فحص Column H — لو Not Found يكتب مرفوض ويعدي
                    if sp_status.strip().lower() == "not found":
                        log(f"  ⚠️ Not Found في Column H — مرفوض")
                        update_sheet(sheet, row_num, "مرفوض")
                        time.sleep(1)
                        continue

                    result = process_rider(driver, rider_id, end_time)
                    update_sheet(sheet, row_num, result)
                    log(f"📝 صف [{row_num}] ← '{result}'")
                    time.sleep(2)
                log("✅ الدفعة اكتملت.")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 👀 لا يوجد صفوف معلقة — انتظار {CHECK_INTERVAL}s...", end="\r")
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("\n🛑 البوت أوقفه المستخدم.")
    finally:
        log("🏁 إغلاق المتصفح.")
        driver.quit()


if __name__ == "__main__":
    main()
