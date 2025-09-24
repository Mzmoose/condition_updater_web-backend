import os, io, zipfile, csv, re, time
from datetime import datetime
import requests
import xml.etree.ElementTree as ET

API_URL = "https://api.ebay.com/ws/api.dll"
HEADERS_BASE = {"Content-Type":"text/xml","X-EBAY-API-SITEID":"0","X-EBAY-API-COMPATIBILITY-LEVEL":"967"}
SAFE_CHAR = re.compile(r"[^A-Za-z0-9._-]+")

def _token() -> str:
    tok = os.environ.get("EBAY_TRADING_TOKEN","").strip()
    if not tok: raise RuntimeError("EBAY_TRADING_TOKEN not set")
    return tok

def _safe_name(s: str) -> str:
    if not s: return "untitled"
    s = SAFE_CHAR.sub("_", s.strip().replace(" ","_"))
    return s[:120] or "untitled"

def _prefix4(sku: str):
    m = re.match(r"^\s*(\d{4})", sku or "")
    return int(m.group(1)) if m else None

def _trading_call(call_name: str, xml_body: str, token: str) -> ET.Element:
    headers = {**HEADERS_BASE,"X-EBAY-API-CALL-NAME":call_name,"X-EBAY-API-IAF-TOKEN":token}
    r = requests.post(API_URL, data=xml_body.encode("utf-8"), headers=headers, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ack = root.findtext(".//Ack")
    if ack and ack.upper() in ("FAILURE","PARTIALFAILURE"):
        errs = []
        for e in root.findall(".//Errors"):
            code = e.findtext("ErrorCode") or "?"
            msg = e.findtext("LongMessage") or e.findtext("ShortMessage") or ""
            errs.append(f"{code}: {msg}")
        raise RuntimeError(f"{call_name} returned {ack}: " + " | ".join(errs))
    return root

def _get_all_active_items(token: str):
    items, ns, page, per_page = [], {"e":"urn:ebay:apis:eBLBaseComponents"}, 1, 100
    while True:
        body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <DetailLevel>ReturnAll</DetailLevel><OutputSelector>ActiveList</OutputSelector>
  <ActiveList><Include>true</Include><Pagination><EntriesPerPage>{per_page}</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination></ActiveList>
</GetMyeBaySellingRequest>'''
        root = _trading_call("GetMyeBaySelling", body, token)
        arr = root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns)
        if not arr: break
        for it in arr:
            items.append({
                "ItemID": (it.findtext("e:ItemID", default="", namespaces=ns) or "").strip(),
                "SKU": (it.findtext("e:SKU", default="", namespaces=ns) or "").strip(),
                "Title": (it.findtext("e:Title", default="", namespaces=ns) or "").strip(),
            })
        total_pages_txt = root.findtext(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=ns) or "1"
        try: total_pages = int(total_pages_txt)
        except: total_pages = page
        if page >= total_pages: break
        page += 1; time.sleep(0.12)
    return items

def _get_item_details(item_id: str, token: str):
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <ItemID>{item_id}</ItemID><DetailLevel>ReturnAll</DetailLevel><IncludeItemSpecifics>false</IncludeItemSpecifics>
</GetItemRequest>'''
    ns = {"e":"urn:ebay:apis:eBLBaseComponents"}
    root = _trading_call("GetItem", body, token)
    title = root.findtext(".//e:Item/e:Title", namespaces=ns) or ""
    sku = root.findtext(".//e:Item/e:SKU", namespaces=ns) or ""
    pics = [el.text for el in root.findall(".//e:Item/e:PictureDetails/e:PictureURL", namespaces=ns) if el.text]
    return {"Title": title, "SKU": sku, "PictureURLs": pics}

def run_bulk_download(start_prefix: str, count: int) -> bytes:
    token = _token()
    try: start = int(re.match(r"^\s*(\d{4})", start_prefix).group(1))
    except: raise RuntimeError("start_prefix must begin with 4 digits")
    actives = _get_all_active_items(token)
    by_prefix = {}
    for it in actives:
        pf = _prefix4(it.get("SKU",""))
        if pf is not None: by_prefix.setdefault(pf, []).append(it)
    selected, seen, p = [], set(), start
    while len(selected) < count:
        arr = by_prefix.get(p, [])
        if not arr and p > 9999: break
        if not arr: p += 1; continue
        for it in arr:
            if it["ItemID"] in seen: continue
            selected.append(it); seen.add(it["ItemID"])
            if len(selected) >= count: break
        p += 1
    batch_label = f"{datetime.utcnow().strftime('%Y%m%d_%H%M')}_{start}-{count}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        mf_rows = []
        for it in selected:
            det = _get_item_details(it["ItemID"], token)
            title = det["Title"] or it.get("Title","")
            sku = det["SKU"] or it.get("SKU","")
            folder = f"SKU_{_safe_name(sku)}" if sku else f"ITEM_{it['ItemID']}"
            log_rows, img_urls, num_saved = [], det["PictureURLs"] or [], 0
            for idx, url in enumerate(img_urls, 1):
                name = (f"SKU_{_safe_name(sku)}_{idx:03d}.jpg" if sku else f"ITEM_{it['ItemID']}_{idx:03d}.jpg")
                path = f"_BATCH_{batch_label}/{folder}/{name}"
                try:
                    with requests.get(url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        z.writestr(path, r.content)
                    num_saved += 1
                    log_rows.append({"sku": sku, "item_id": it["ItemID"], "image_url": url, "filename": name, "status": "saved"})
                except Exception as e:
                    log_rows.append({"sku": sku, "item_id": it["ItemID"], "image_url": url, "filename": name, "status": f"error:{e}"})
            mf_rows.append({"sku": sku, "item_id": it["ItemID"], "title": title, "images": num_saved})
            import io as _io
            log_csv = _io.StringIO()
            w = csv.DictWriter(log_csv, fieldnames=["sku","item_id","image_url","filename","status"])
            w.writeheader()
            for r in log_rows: w.writerow(r)
            z.writestr(f"_BATCH_{batch

cd ~/Desktop/SCRIPTS/condition_updater_web-backend
git checkout bootstrap_clean
mkdir -p app/services app/routers

cat > app/services/bulk_downloader.py <<'PY'
import os, io, zipfile, csv, re, time
from datetime import datetime
import requests
import xml.etree.ElementTree as ET

API_URL = "https://api.ebay.com/ws/api.dll"
HEADERS_BASE = {"Content-Type":"text/xml","X-EBAY-API-SITEID":"0","X-EBAY-API-COMPATIBILITY-LEVEL":"967"}
SAFE_CHAR = re.compile(r"[^A-Za-z0-9._-]+")

def _token() -> str:
    tok = os.environ.get("EBAY_TRADING_TOKEN","").strip()
    if not tok: raise RuntimeError("EBAY_TRADING_TOKEN not set")
    return tok

def _safe_name(s: str) -> str:
    if not s: return "untitled"
    s = SAFE_CHAR.sub("_", s.strip().replace(" ","_"))
    return s[:120] or "untitled"

def _prefix4(sku: str):
    m = re.match(r"^\s*(\d{4})", sku or "")
    return int(m.group(1)) if m else None

def _trading_call(call_name: str, xml_body: str, token: str) -> ET.Element:
    headers = {**HEADERS_BASE,"X-EBAY-API-CALL-NAME":call_name,"X-EBAY-API-IAF-TOKEN":token}
    r = requests.post(API_URL, data=xml_body.encode("utf-8"), headers=headers, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ack = root.findtext(".//Ack")
    if ack and ack.upper() in ("FAILURE","PARTIALFAILURE"):
        errs = []
        for e in root.findall(".//Errors"):
            code = e.findtext("ErrorCode") or "?"
            msg = e.findtext("LongMessage") or e.findtext("ShortMessage") or ""
            errs.append(f"{code}: {msg}")
        raise RuntimeError(f"{call_name} returned {ack}: " + " | ".join(errs))
    return root

def _get_all_active_items(token: str):
    items, ns, page, per_page = [], {"e":"urn:ebay:apis:eBLBaseComponents"}, 1, 100
    while True:
        body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <DetailLevel>ReturnAll</DetailLevel><OutputSelector>ActiveList</OutputSelector>
  <ActiveList><Include>true</Include><Pagination><EntriesPerPage>{per_page}</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination></ActiveList>
</GetMyeBaySellingRequest>'''
        root = _trading_call("GetMyeBaySelling", body, token)
        arr = root.findall(".//e:ActiveList/e:ItemArray/e:Item", ns)
        if not arr: break
        for it in arr:
            items.append({
                "ItemID": (it.findtext("e:ItemID", default="", namespaces=ns) or "").strip(),
                "SKU": (it.findtext("e:SKU", default="", namespaces=ns) or "").strip(),
                "Title": (it.findtext("e:Title", default="", namespaces=ns) or "").strip(),
            })
        total_pages_txt = root.findtext(".//e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", namespaces=ns) or "1"
        try: total_pages = int(total_pages_txt)
        except: total_pages = page
        if page >= total_pages: break
        page += 1; time.sleep(0.12)
    return items

def _get_item_details(item_id: str, token: str):
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <ItemID>{item_id}</ItemID><DetailLevel>ReturnAll</DetailLevel><IncludeItemSpecifics>false</IncludeItemSpecifics>
</GetItemRequest>'''
    ns = {"e":"urn:ebay:apis:eBLBaseComponents"}
    root = _trading_call("GetItem", body, token)
    title = root.findtext(".//e:Item/e:Title", namespaces=ns) or ""
    sku = root.findtext(".//e:Item/e:SKU", namespaces=ns) or ""
    pics = [el.text for el in root.findall(".//e:Item/e:PictureDetails/e:PictureURL", namespaces=ns) if el.text]
    return {"Title": title, "SKU": sku, "PictureURLs": pics}

def run_bulk_download(start_prefix: str, count: int) -> bytes:
    token = _token()
    try: start = int(re.match(r"^\s*(\d{4})", start_prefix).group(1))
    except: raise RuntimeError("start_prefix must begin with 4 digits")
    actives = _get_all_active_items(token)
    by_prefix = {}
    for it in actives:
        pf = _prefix4(it.get("SKU",""))
        if pf is not None: by_prefix.setdefault(pf, []).append(it)
    selected, seen, p = [], set(), start
    while len(selected) < count:
        arr = by_prefix.get(p, [])
        if not arr and p > 9999: break
        if not arr: p += 1; continue
        for it in arr:
            if it["ItemID"] in seen: continue
            selected.append(it); seen.add(it["ItemID"])
            if len(selected) >= count: break
        p += 1
    batch_label = f"{datetime.utcnow().strftime('%Y%m%d_%H%M')}_{start}-{count}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        mf_rows = []
        for it in selected:
            det = _get_item_details(it["ItemID"], token)
            title = det["Title"] or it.get("Title","")
            sku = det["SKU"] or it.get("SKU","")
            folder = f"SKU_{_safe_name(sku)}" if sku else f"ITEM_{it['ItemID']}"
            log_rows, img_urls, num_saved = [], det["PictureURLs"] or [], 0
            for idx, url in enumerate(img_urls, 1):
                name = (f"SKU_{_safe_name(sku)}_{idx:03d}.jpg" if sku else f"ITEM_{it['ItemID']}_{idx:03d}.jpg")
                path = f"_BATCH_{batch_label}/{folder}/{name}"
                try:
                    with requests.get(url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        z.writestr(path, r.content)
                    num_saved += 1
                    log_rows.append({"sku": sku, "item_id": it["ItemID"], "image_url": url, "filename": name, "status": "saved"})
                except Exception as e:
                    log_rows.append({"sku": sku, "item_id": it["ItemID"], "image_url": url, "filename": name, "status": f"error:{e}"})
            mf_rows.append({"sku": sku, "item_id": it["ItemID"], "title": title, "images": num_saved})
            import io as _io
            log_csv = _io.StringIO()
            w = csv.DictWriter(log_csv, fieldnames=["sku","item_id","image_url","filename","status"])
            w.writeheader()
            for r in log_rows: w.writerow(r)
            z.writestr(f"_BATCH_{batch_label}/{folder}/download_log.csv", log_csv.getvalue())
        import io as _io
        mf_csv = _io.StringIO()
        w = csv.DictWriter(mf_csv, fieldnames=["sku","item_id","title","images"])
        w.writeheader()
        for r in mf_rows: w.writerow(r)
        z.writestr(f"_BATCH_{batch_label}/manifest.csv", mf_csv.getvalue())
        z.writestr(f"_BATCH_{batch_label}/README.txt", f"Batch {batch_label}\nItems processed: {len(selected)} of {len(actives)} active.")
    return buf.getvalue()
