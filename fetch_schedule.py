#!/usr/bin/env python3
"""
班表抓取腳本 — 每月初在連VPN的電腦上執行
執行後會更新 schedule.json，再 push 到 GitHub 即完成更新

用法：
  python fetch_schedule.py            ← 抓當月
  python fetch_schedule.py 2026 7     ← 抓指定年月
"""

import sys
import json
import re
import datetime
from urllib.request import urlopen, Request
from html.parser import HTMLParser

BASE_URL = "http://home.elifemall.com.tw/home/newportal/ivychao/prd/worktbl/page/worktblC.php"
DEPT = "03902B"          # 桃區營業二部
OUTPUT = "schedule.json" # 輸出檔案（放在與 index.html 同一目錄）

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

STATUS_WORDS = {"休", "例", "年", "國", "事", "病", "喪", "生", "婚", "公", "會", "支", "援",
                "內", "外", "訓", "出", "務", "H", "4", "8"}


class ScheduleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stores = {}
        self._current_store = None
        self._rows = []
        self._in_tbody = False
        self._in_tr = False
        self._in_td = False
        self._td_idx = 0
        self._cell_texts = []
        self._current_cell = []
        self._in_thead = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "thead":
            self._in_thead = True
        if tag == "tbody":
            self._in_tbody = True
            self._in_thead = False
        if tag == "td" and self._in_thead:
            pass
        if tag == "tr" and self._in_tbody:
            self._in_tr = True
            self._td_idx = 0
            self._cell_texts = []
            self._cell_dests = []
        if tag == "td" and self._in_tr:
            self._in_td = True
            self._current_cell = []
            self._current_dest = ''
        # 支援目的地：<a class="tooltip" id="門市名稱">
        if tag == "a" and self._in_td:
            cls = attrs.get('class', '')
            if 'tooltip' in cls:
                dest = attrs.get('id', '').strip()
                if dest:
                    self._current_dest = dest

    def handle_endtag(self, tag):
        if tag == "thead":
            self._in_thead = False
        if tag == "tbody":
            self._in_tbody = False
        if tag == "td" and self._in_td:
            self._in_td = False
            text = " ".join(self._current_cell).strip()
            # 若有目的地，格式存為 "支援|門市名"
            if self._current_dest and '支' in text:
                text = f"支援|{self._current_dest}"
            self._cell_texts.append(text)
            self._td_idx += 1
        if tag == "tr" and self._in_tr:
            self._in_tr = False
            self._process_row(self._cell_texts)

    def handle_data(self, data):
        if self._in_td:
            t = data.strip()
            if t:
                self._current_cell.append(t)

    def _process_row(self, cells):
        if not cells:
            return
        first = cells[0]
        # Employee row: first cell contains ID and name
        m = re.search(r'(\d{5})\s*([^\[]+)\[([^\]]+)\]', first)
        if m and self._current_store is not None:
            emp_id = m.group(1)
            emp_name = m.group(2).strip()
            emp_title = m.group(3).strip()
            is_manager = ("副理" in emp_title or "主任" in emp_title or "店長" in emp_title)
            # days: cells[1:] — up to 30 days
            days = []
            dests = []
            for c in cells[1:31]:
                t = c.strip()
                days.append(t if t else "")
                # 目的地門市：格式為 "支援|03132 竹北大遠百門市" 或單純 "支援"
                if '|' in t and t.startswith('支援'):
                    dests.append(t.split('|', 1)[1].strip())
                else:
                    dests.append('')
            # Pad to 30
            while len(days) < 30:
                days.append("")
                dests.append("")
            self.stores.setdefault(self._current_store, []).append({
                "id": emp_id,
                "name": emp_name,
                "title": emp_title,
                "isManager": is_manager,
                "days": days[:30],
                "dests": dests[:30]
            })

    def set_store(self, name):
        self._current_store = name


def fetch_html(year, month, storeno=""):
    url = (f"{BASE_URL}?search_type=&cal_y={year}&cal_m={month}"
           f"&areano=&storedepno={DEPT}&storeno={storeno}&empnotype=a&doing=1")
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_store_names(html):
    """Extract store codes and names from the HTML."""
    # Look for patterns like [03009Digital City-頭份店]
    stores = re.findall(r'\[(\d{5})([^\]]+?)\]', html)
    seen = []
    result = []
    for code, name in stores:
        if code not in seen and len(code) == 5 and code.startswith('0'):
            seen.append(code)
            result.append((code, name.strip()))
    return result


def parse_employees(html, store_label):
    """Parse employee rows for a given store block."""
    parser = ScheduleParser()
    parser.set_store(store_label)

    # Find the table section for this store
    # Each store has its own table, identified by the store name in thead
    parser.feed(html)
    return parser.stores.get(store_label, [])


def main():
    now = datetime.datetime.now()
    year = int(sys.argv[1]) if len(sys.argv) > 1 else now.year
    month = int(sys.argv[2]) if len(sys.argv) > 2 else now.month

    print(f"抓取 {year}年{month}月 {DEPT} 班表...")

    # Fetch main page to get store list
    try:
        html_all = fetch_html(year, month)
    except Exception as e:
        print(f"❌ 無法連線：{e}")
        print("請確認已連線公司VPN，並重試")
        sys.exit(1)

    store_pairs = parse_store_names(html_all)
    if not store_pairs:
        print("⚠ 找不到門市清單，請確認網址和VPN狀態")
        sys.exit(1)

    print(f"  找到 {len(store_pairs)} 間門市")

    all_stores = {}

    for store_code, store_name in store_pairs:
        label = f"{store_code} {store_name}"
        print(f"  抓取 {label}...")
        try:
            html_store = fetch_html(year, month, storeno=store_code)
            # Parse employees
            parser = ScheduleParser()
            parser.set_store(label)
            parser.feed(html_store)
            emps = parser.stores.get(label, [])
            if emps:
                all_stores[label] = emps
            else:
                print(f"    (無資料)")
        except Exception as e:
            print(f"    ⚠ 抓取失敗: {e}")

    if not all_stores:
        print("❌ 所有門市均無資料，請確認VPN連線")
        sys.exit(1)

    # Build output
    output = {
        "meta": {
            "year": year,
            "month": month,
            "dept": f"{DEPT} 桃區營業二部",
            "updated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        },
        "stores": all_stores
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_emp = sum(len(v) for v in all_stores.values())
    print(f"\n✅ 完成！共 {len(all_stores)} 間門市、{total_emp} 位員工")
    print(f"   已儲存至 {OUTPUT}")
    print()
    print("下一步：")
    print("  git add schedule.json")
    print("  git commit -m '更新班表 %d年%d月'" % (year, month))
    print("  git push")
    print()
    print("Netlify 會在 1 分鐘內自動重新部署 🚀")


if __name__ == "__main__":
    main()
