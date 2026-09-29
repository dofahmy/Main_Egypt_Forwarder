"""
بوت نقل العروض — مصر (تاج حسب القناة المصدر)
بيتابع قناتين:
  - @AmazonEgyptOffers → اللينك بالتاج nonzz-21
  - @Belnos          → اللينك بالتاج nooss-21
بياخد الصورة من البوست الأصلي، ويمنع تكرار نفس المنتج في نفس اليوم.
مفيش أسئلة — بيشتغل على طول.
التثبيت:
   pip install telethon playwright requests
   python3 -m playwright install chromium
التشغيل:
   python3 channel_forwarder_eg.py
   (أول مرة هيطلب رقمك + كود من تليجرام)
"""
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import re, os, time, json, asyncio, requests
import socket as _sock
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from telethon import TelegramClient, events

# ============ Railway / server mode ============
SERVER_MODE = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID") or os.getenv("SERVER_MODE") == "1")
if SERVER_MODE:
    print("☁️ Server mode: Railway/headless")

def _env_bool(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def _restore_session_from_env():
    """Restore Telethon SQLite session from a base64 Railway variable before client creation."""
    if not SERVER_MODE:
        return
    import base64
    raw = os.getenv("TELEGRAM_SESSION_B64", "").strip()
    if not raw:
        return
    path = os.getenv("TELEGRAM_SESSION_PATH", "forwarder_eg_session.session")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    try:
        with open(path, "wb") as f:
            f.write(base64.b64decode(raw))
        print(f"🔐 Telegram session restored to {path}")
    except Exception as e:
        print(f"❌ Failed to restore Telegram session: {e}")

_restore_session_from_env()
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT = True
except:
    PLAYWRIGHT = False
# ============ DNS resolver لـ creatorsapi.amazon ============
_FALLBACK_IPS = ["108.159.120.21", "108.159.120.6", "108.159.120.71", "108.159.120.45"]
def _resolve():
    try:
        import dns.resolver
        r = dns.resolver.Resolver(configure=False); r.nameservers = ["8.8.8.8", "1.1.1.1"]
        return [x.address for x in r.resolve("creatorsapi.amazon", "A")]
    except:
        return _FALLBACK_IPS
_orig = _sock.getaddrinfo
def _p(h, *a, **k):
    if h == "creatorsapi.amazon":
        res = []
        for ip in _resolve():
            try: res.extend(_orig(ip, *a, **k))
            except: pass
        if res: return res
    return _orig(h, *a, **k)
_sock.getaddrinfo = _p
# ============ بيانات API أمازون مصر ============
CLIENT_ID = os.getenv("AMAZON_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("AMAZON_CLIENT_SECRET", "")
PARTNER_TAG = "wishitworkthe-21"
MARKETPLACE = "www.amazon.eg"
_TOKEN = None
def get_token():
    r = requests.post("https://api.amazon.com/auth/o2/token",
        headers={"Content-Type": "application/json"},
        json={"grant_type": "client_credentials", "client_id": CLIENT_ID,
              "client_secret": CLIENT_SECRET, "scope": "creatorsapi::default"}, timeout=15)
    return r.json().get("access_token")
def get_product(asin):
    """يجيب اسم المنتج والسعر من أمازون. None لو فشل."""
    global _TOKEN
    import requests as _rq
    if not _TOKEN:
        _TOKEN = get_token()
    for attempt in range(3):       # 3 محاولات (كان 2) — عشان الفشل المؤقت
        try:
            H = {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json",
                 "x-marketplace": MARKETPLACE}
            payload = {"itemIds": [asin], "itemIdType": "ASIN", "partnerTag": PARTNER_TAG,
                "partnerType": "Associates", "marketplace": MARKETPLACE,
                "languagesOfPreference": ["ar_AE"],
                "resources": ["itemInfo.title", "offersV2.listings.price",
                              "offersV2.listings.availability",
                              "offersV2.listings.merchantInfo",
                              "offersV2.listings.type",
                              "offersV2.listings.condition",
                              "offersV2.listings.dealDetails",
                              "itemInfo.classifications", "browseNodeInfo.browseNodes"]}
            r = _rq.post("https://creatorsapi.amazon/catalog/v1/getItems",
                         headers=H, json=payload, timeout=25)
            if r.status_code == 401:
                _TOKEN = get_token(); continue
            # لو الرد نفسه مش سليم (500/503/429) → نحاول تاني (مؤقت)
            if r.status_code >= 500 or r.status_code == 429:
                print(f"   ⚠️ API رجّع {r.status_code} — بحاول تاني ({attempt+1}/3)")
                time.sleep(2)
                continue
            items = r.json().get("itemsResult", {}).get("items", [])
            if not items:
                # طباعة تشخيصية: نشوف الـ API رجّع إيه بالظبط
                try:
                    _err = r.json().get("errors") or r.json()
                    print(f"   🔍 [تشخيص] API رجّع بدون items لـ {asin}: {str(_err)[:200]}")
                except Exception:
                    print(f"   🔍 [تشخيص] API status={r.status_code}, نص={r.text[:200]}")
                if attempt < 2:
                    time.sleep(2)
                    continue
                return None
            item = items[0]
            title = item.get("itemInfo", {}).get("title", {}).get("displayValue", "")
            # كل العروض المتاحة (بائعين مختلفين) — نختار أرخص عرض جديد حقيقي
            # (نستبعد الريسيل/المستعمل والاشتراك والتوفير من البوست الأساسي)
            _all_listings = item.get("offersV2", {}).get("listings") or []
            def _listing_price(lst):
                try:
                    return lst.get("price", {}).get("money", {}).get("amount")
                except Exception:
                    return None
            def _is_new_listing(_l):
                c = _l.get("condition")
                if isinstance(c, dict):
                    c = c.get("value") or c.get("displayValue") or ""
                c = str(c or "").strip().lower()
                return (not c) or c == "new" or "جديد" in c
            def _is_resale_listing(_l):
                _mn = ((_l.get("merchantInfo", {}) or {}).get("name") or "").strip().lower()
                return "resale" in _mn or "ريسيل" in _mn
            def _is_subscribe(_l):
                t = _l.get("type")
                if isinstance(t, dict):
                    t = t.get("value") or ""
                return "subscribe" in str(t or "").lower()
            # نفلتر: جديد بس، مش ريسيل، مش اشتراك — وليها سعر
            _priced = []
            for l in _all_listings:
                p = _listing_price(l)
                if p is None:
                    continue
                if not _is_new_listing(l) or _is_resale_listing(l) or _is_subscribe(l):
                    continue
                _priced.append((l, p))
            if _priced:
                _priced.sort(key=lambda x: x[1])   # الأرخص الأول
                L = _priced[0][0]
                if len(_priced) > 1:
                    print(f"   💰 {len(_priced)} بائع جديد — اخترت الأرخص: {_priced[0][1]:.0f} (بدل {_priced[-1][1]:.0f})")
                # تشخيص: نطبع سعر كل بائع (عشان لو السعر طلع غلط نعرف منين جه)
                try:
                    _dbg = " | ".join(f"{(_l.get('merchantInfo',{}) or {}).get('name','?')}={_p:.0f}" for _l, _p in _priced)
                    print(f"   🔍 [أسعار البائعين] {asin}: {_dbg}")
                except Exception:
                    pass
            else:
                L = (_all_listings or [{}])[0]
            # نكتشف عرض أمازون ريسيل (بائعه "Amazon Resale") لو موجود
            resale_info = None
            for _l in _all_listings:
                _mi = _l.get("merchantInfo", {}) or {}
                _mn = (_mi.get("name") or "").strip().lower()
                if "resale" in _mn or "ريسيل" in _mn:
                    _rp = _listing_price(_l)
                    if _rp:
                        # نجيب نوت الحالة (conditionNote) من عرض الريسيل
                        _cond = _l.get("condition") or {}
                        _note = ""
                        if isinstance(_cond, dict):
                            _note = (_cond.get("conditionNote") or "").strip()
                        resale_info = {"price": _rp,
                                       "seller_id": (_mi.get("id") or "").strip(),
                                       "note": _note}
                    break
            po = L.get("price", {})
            _money = po.get("money", {}) or {}
            cur = _money.get("amount")
            orig = po.get("savingBasis", {}).get("money", {}).get("amount")
            disc = po.get("savings", {}).get("percentage")
            if not disc and orig and cur and orig > cur:
                disc = round((orig - cur) / orig * 100)
            if not cur:
                # مفيش سعر — نطبع تشخيص عشان نعرف ليه (listings فاضية؟ availability؟)
                try:
                    _lst = item.get("offersV2", {}).get("listings")
                    print(f"   🔍 [تشخيص] {asin} مفيش سعر — listings={str(_lst)[:250]}")
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(2)
                    continue
                return None
            # معلومة البائع (من merchantInfo.name زي "Amazon.eg")
            mi = L.get("merchantInfo", {}) or {}
            seller_name = (mi.get("name") or "").strip()
            seller_id = (mi.get("id") or "").strip()
            # البائع أمازون: الاسم فيه amazon، أو الـ id بتاع أمازون مصر
            # (A1ZVRGNO5AYLOV = Amazon.eg — البائع الرسمي)
            seller_is_amazon = ("amazon" in seller_name.lower()) or (seller_id == "A1ZVRGNO5AYLOV")
            # لما البائع أمازون فهو البائع والشاحن معاً (أمازون بتشحن منتجاتها)
            fulfilled_by_amazon = seller_is_amazon
            _seller_raw = seller_name or "(مش متاح)"
            # أسماء الأقسام (عشان نفلتر ونجيب إيموجي) — نشمل سلسلة الآباء + الإنجليزي
            def _node_chain(node):
                names = []
                cur_n = node
                seen = 0
                while cur_n and seen < 12:
                    # نجمع الاسم المعروض (عربي) + contextFreeName (إنجليزي غالباً)
                    for key in ("displayName", "contextFreeName"):
                        nm = cur_n.get(key)
                        if nm:
                            names.append(nm)
                    cur_n = cur_n.get("ancestor")
                    seen += 1
                return names
            nodes = []
            for n in item.get("browseNodeInfo", {}).get("browseNodes", []):
                nodes.extend(_node_chain(n))
            cls = item.get("itemInfo", {}).get("classifications", {})
            for k in ("productGroup", "binding"):
                v = cls.get(k, {}).get("displayValue")
                if v:
                    nodes.append(v)
            _brand = item.get("itemInfo", {}).get("byLineInfo", {}).get("brand", {}).get("displayValue")
            # وصف العرض (البادج) — زي "عرض رجوع المدارس المبكر"
            deal_badge = ""
            try:
                dd = L.get("dealDetails") or {}
                deal_badge = (dd.get("badge") or "").strip()
            except Exception:
                deal_badge = ""
            return {"title": title, "cur": cur, "orig": orig, "disc": disc or 0,
                    "nodes": nodes,
                    "seller": _seller_raw,
                    "seller_is_amazon": seller_is_amazon,
                    "fulfilled_by_amazon": fulfilled_by_amazon,
                    "deal_badge": deal_badge,
                    "resale": resale_info,
                    "seller_id": seller_id,
                    "_offer_raw": L}
        except Exception as e:
            print(f"   ⚠️ API: {str(e)[:80]}")
            time.sleep(2)
    return None
# ============ جمل السطر الأول (من ملف خارجي) ============
HEADLINES_FILE = "headlines_eg.txt"
# جمل البوستات اللي مفيهاش خصم (لكنز)
HEADLINES_NODISC_FILE = "headlines_nodisc_eg.txt"
# جمل احتياطية لو الملف مش موجود أو فاضي
_FALLBACK_HEADLINES = [
    "🔥 الحق العرض ده", "💥 عرض مايتفوتش", "⚡ لقطة بجد", "🎯 فرصة ما تتفوت",
    "🚀 بسرعة قبل ما يخلص", "💸 وفر فلوسك", "👌 سعر مش هيتكرر",
]
_FALLBACK_NODISC = [
    "😍 السعرررر تحفه", "🚨 متفكرش كتير", "⏰ مستني إيه؟",
    "💯 سعر ممتاز", "👀 خلي عينك على السعر ده",
]
_hl_cache = {}   # {filename: {"mtime": .., "lines": [..]}}
def _load_lines(path, fallback):
    """يقرا سطور من ملف نصي. بيعيد القراءة بس لو الملف اتغيّر."""
    c = _hl_cache.setdefault(path, {"mtime": 0, "lines": []})
    try:
        mt = os.path.getmtime(path)
        if mt != c["mtime"]:
            with open(path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f
                         if ln.strip() and not ln.strip().startswith("#")]
            if lines:
                c["lines"] = lines
                c["mtime"] = mt
                print(f"   📄 اتقرت {len(lines)} جملة من {path}")
    except FileNotFoundError:
        if not c["lines"]:
            print(f"   ⚠️ {path} مش موجود — هستخدم الجمل الاحتياطية")
    except Exception as e:
        print(f"   ⚠️ مشكلة في قراءة {path}: {e}")
    return c["lines"] or fallback
def load_headlines():
    """جمل البوستات العادية"""
    return _load_lines(HEADLINES_FILE, _FALLBACK_HEADLINES)
def load_headlines_nodisc():
    """جمل البوستات اللي مفيهاش خصم"""
    return _load_lines(HEADLINES_NODISC_FILE, _FALLBACK_NODISC)
PRICE_ICONS = ["💵", "💰", "🏷️", "💳", "🪙", "💸", "🧾", "💲", "🤑", "🛒", "🛍️", "📌",
               "✅", "✔️", "🟢", "💎", "🔖", "📦", "👛", "💷", "💶", "💴", "⭐", "🌟"]
DISCOUNT_ICONS = ["💥", "🔥", "⚡", "🎯", "📉", "🚨", "🎉", "✂️", "🏷️", "🔻", "⬇️", "🎊",
                  "💣", "🌪️", "‼️", "❗", "🔔", "📢", "🎁", "🤯", "😱", "🥳", "💫"]
def _fmt_price(v):
    """يعرض السعر من غير كسور ولا تقريب (بيقص الكسور: 182.9 → 182)"""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)
def _esc(s):
    """نحمي النص من رموز HTML اللي بتلخبط تليجرام"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def build_multi_post(items, tag, uname=None):
    """بوست واحد فيه كذا منتج — items = [(asin, data), ...]"""
    import random
    lines = [random.choice(load_headlines()), ""]
    for asin, d in items:
        lines.append(f"⬅️ {_esc(d['title'])}")
        price_line = f"{random.choice(PRICE_ICONS)} {_fmt_price(d['cur'])} جنيه"
        if d['orig'] and d['orig'] > d['cur']:
            price_line += f"  ({random.choice(DISCOUNT_ICONS)} خصم {d['disc']:.0f}%)"
        lines.append(price_line)
        lines.append(f"🔗 {_buy_link(asin, tag, (d or {}).get('seller_id'))}")
        _rl = _resale_lines(d, asin, tag)
        if _rl:
            lines.append(_rl)
        lines.append("")
    return "\n".join(lines).strip()
# ============ كلمات ممنوعة تماماً (البوست بيتلغى لو فيه أي كلمة منها) ============
# الكلمات في ملف خارجي blocked_words_eg.txt — تقدري تعدّليه من غير ما تلمسي الكود
# بنفحص: نص البوست الأصلي + عنوان المنتج + كلمة البحث
BLOCKED_WORDS_FILE = "blocked_words_eg.txt"
_FALLBACK_BLOCKED = ["لانجري", "لانجيري", "لنجري", "لنجيري", "lingerie",
                     "سكسي", "سيكسي", "سكس", "sexy"]
def load_blocked_words():
    """يقرا الكلمات الممنوعة من الملف (بيعيد القراءة لو الملف اتغيّر)"""
    return _load_lines(BLOCKED_WORDS_FILE, _FALLBACK_BLOCKED)
def has_blocked_word(*texts):
    """يرجّع الكلمة الممنوعة لو لقاها ككلمة كاملة في أي نص، وإلا None"""
    blob = " ".join(t for t in texts if t).lower()
    # حروف الكلمات: إنجليزي/أرقام + الحروف العربية الفعلية بس
    # (مش علامات الترقيم زي الفاصلة العربية ، اللي كانت بتلخبط الحدود)
    L = r'\w\u0621-\u063A\u0641-\u064A\u0660-\u0669'
    for w in load_blocked_words():
        wl = w.lower()
        # نطابق الكلمة كاملة (بحدود) عشان مانمنعش كلمة جوه كلمة تانية
        # زي "سكس" اللي كانت بتطابق "ريكس" بالغلط
        if re.search(rf'(?<![{L}]){re.escape(wl)}(?![{L}])', blob):
            return w
    return None
# ============ أقسام ممنوعة (المنتج بيتمنع لو تصنيفه فيه أي كلمة منها) ============
# ملف خارجي — بيفحص أسماء أقسام المنتج (nodes) اللي بتيجي من أمازون
BLOCKED_CATEGORIES_FILE = "blocked_categories_eg.txt"
_FALLBACK_BLOCKED_CAT = ["لانجري", "لانجيري", "ملابس داخلية نسائية", "حمالات صدر",
                         "ملابس نوم نسائية", "بيكيني", "lingerie", "bras"]
def load_blocked_categories():
    return _load_lines(BLOCKED_CATEGORIES_FILE, _FALLBACK_BLOCKED_CAT)
def has_blocked_category(data):
    """يرجّع اسم القسم الممنوع لو المنتج متصنّف تحته، وإلا None"""
    if not data or not data.get("nodes"):
        return None
    nodes_txt = " | ".join(data["nodes"]).lower()
    for w in load_blocked_categories():
        if w.lower() in nodes_txt:
            return w
    return None
# ============ فلتر الملابس النسائية ============
# القنوات دي مابتستقبلش ملابس/لانجري نسائية
WOMEN_CLOTHING_EXCLUDE = set()   # اتشال الفلتر — الملابس النسائية بتتبعت عادي على كل القنوات
# كلمات القسم اللي تدل على ملابس نسائية
_W_CLOTHING = [
    "ملابس نسائية", "ملابس حريمي", "فساتين", "بلوزات", "تنانير", "جيبة",
    "عبايات", "قفطان", "بيجامات نسائية", "ملابس نوم نسائية",
    "لانجيري", "لانجري", "ملابس داخلية نسائية", "حمالات صدر", "بيكيني",
    "مايوهات نسائية", "بناطيل نسائية", "جاكيت نسائي", "كارديجان",
    "women's clothing", "dresses", "lingerie", "blouses", "skirts",
    "women's intimates", "bras", "nightwear", "sleepwear",
]
# استثناءات: دي مش ملابس حتى لو ظهرت مع كلمات نسائية
_W_NOT_CLOTHING = ["حقائب", "شنط", "احذية", "أحذية", "صنادل", "مجوهرات",
                   "ساعات", "عطور", "مكياج", "shoes", "bags", "watches",
                   "jewelry", "beauty", "fragrance"]
def is_women_clothing(data):
    """يفحص لو المنتج ملابس/لانجري نسائية من أقسامه"""
    if not data or not data.get("nodes"):
        return False
    nodes_txt = " | ".join(data["nodes"]).lower()
    # لو فيه إشارة لحاجة مش ملابس (شنط/أحذية) → مش ملابس
    if any(x.lower() in nodes_txt for x in _W_NOT_CLOTHING):
        return False
    return any(x.lower() in nodes_txt for x in _W_CLOTHING)
# ============ بوست العروض (لينكات البحث/الأقسام) ============
OFFER_ICONS = ["🛍️", "🎁", "🛒", "✨", "🔖", "💫", "🎯", "🏷️"]
_HAD_AMAZON = []     # علامة إن فيه لينك أمازون اتعالج (حتى لو اتختصر)
_LAST_FULL_URL = []  # اللينك الكامل قبل الاختصار (للسكرين شوت)
_IS_BAZAAR = []      # علامة إن اللينك بازار (s=bazaar)
def _search_keyword(text):
    """يطلّع كلمة البحث من اللينك اللي في النص، وإلا من اللينك الكامل المحفوظ"""
    from urllib.parse import unquote
    # الأولوية للينك الموجود في النص نفسه (أدق)
    m = re.search(r'[?&]k=([^&\s]+)', text)
    if not m and _LAST_FULL_URL:
        m = re.search(r'[?&]k=([^&\s]+)', _LAST_FULL_URL[0])
    if not m:
        return None
    kw = unquote(m.group(1)).replace("+", " ").strip()
    return kw if kw else None
def build_offer_post(text, keyword):
    """بوست لعروض البحث: إيموجي + عرض على [الكلمة] + اللينك"""
    import random
    link = ""
    for u in LINK_RE.findall(text):
        if "amazon." in u:
            link = u
            break
    lines = [f"{random.choice(OFFER_ICONS)} عرض على {_esc(keyword)}"]
    if link:
        lines.append("")
        lines.append(f"🔗 {link}")
    return "\n".join(lines)
# ============ فورمات خاص لبوستات Elwyy (منتج منفرد) ============
# قاموس إيموجي حسب كلمات في اسم المنتج (أول تطابق يكسب — رتّبي الأدق فوق)
PRODUCT_EMOJI = [
    (["موبايل", "هاتف", "smartphone", "جالاكسي", "ايفون", "iphone", "شاومي", "ريدمي", "poco", "تابلت", "تاب"], "📱"),
    (["لابتوب", "laptop", "نوت بوك"], "💻"),
    (["سماعة", "سماعات", "ايربود", "earbuds", "headphone"], "🎧"),
    (["ساعة", "ساعه", "smart watch", "سمارت ووتش"], "⌚"),
    (["كاميرا", "camera"], "📷"),
    (["شاشة", "تلفزيون", "tv", "تليفزيون"], "📺"),
    (["فلاش", "فلاشة", "ميموري", "usb", "هارد", "ssd"], "💾"),
    (["شاحن", "باور بانك", "كابل", "charger", "power bank"], "🔌"),
    (["ماوس", "كيبورد", "لوحة مفاتيح", "mouse", "keyboard"], "🖱️"),
    (["حلة", "حلل", "طاسة", "طاجن", "مقلاة", "حلة طهي"], "🍳"),
    (["خلاط", "عجان", "كبة", "محضر طعام", "عصارة", "مضرب"], "🍹"),
    (["غسالة", "washing"], "🧺"),
    (["ثلاجة", "fridge", "ديب فريزر"], "🧊"),
    (["مكنسة", "شفاط"], "🧹"),
    (["مكواة", "iron"], "👔"),
    (["ميكروويف", "فرن", "microwave", "توستر"], "🔥"),
    (["مروحة", "تكييف", "fan"], "❄️"),
    (["غلاية", "كيتل", "كوب", "مج", "برّاد شاي", "ترمس"], "🫖"),
    (["عطر", "برفان", "بارفان", "او دو", "perfume", "بادى سبلاش", "بخاخ"], "🌸"),
    (["كريم", "سيروم", "لوشن", "غسول", "مرطب", "ماسك"], "🧴"),
    (["شامبو", "بلسم", "زيت شعر", "زيت الشعر", "زيت للشعر", "سيروم شعر", "سيروم الشعر", "كريم شعر", "حمام كريم"], "🧴"),
    (["مكياج", "روج", "احمر شفاه", "ماسكارا", "كونسيلر", "فاونديشن", "آيلاينر", "كحل"], "💄"),
    (["حذاء", "كوتشي", "شبشب", "صندل", "سنيكرز", "شوز", "بوت"], "👟"),
    (["تيشرت", "تيشيرت", "قميص", "بلوزة", "بلوفر", "سويت شيرت", "هودي", "جاكيت", "بنطلون", "شورت", "فستان"], "👕"),
    (["شنطة", "حقيبة", "باك باك", "شنطه"], "👜"),
    (["نظارة", "نضارة", "sunglasses"], "🕶️"),
    (["لعبة", "العاب", "toy", "مكعبات", "عروسة", "دباديب"], "🧸"),
    (["حفاض", "حفاضات", "pampers", "مناديل مبللة", "رضاعة", "ببرونة"], "🍼"),
    (["كتاب", "قصة", "دفتر", "قلم", "أقلام", "notebook"], "📚"),
    (["ارز", "أرز", "سكر", "شاي", "قهوة", "عسل", "مكرونة", "دقيق", "توابل", "بهارات", "صوص", "شوكولاتة", "بسكويت", "زيت زيتون", "زيت طبخ", "زيت عباد", "زيت الطعام"], "🛒"),
    (["حليب", "لبن", "جبنة", "زبادي"], "🥛"),
    (["مكمل", "بروتين", "فيتامين", "كرياتين"], "💪"),
    (["مناديل", "تواليت", "منظف", "صابون", "مسحوق غسيل", "معطر"], "🧼"),
]
def _product_emoji(title):
    """يرجّع إيموجي متعلق باسم المنتج، أو "" (مفيش) لو مفيش تطابق"""
    t = (title or "").lower()
    for words, emoji in PRODUCT_EMOJI:
        for w in words:
            if w in t:
                return emoji
    return ""
# إيموجي من قسم المنتج (تصنيف أمازون الرسمي) — أدق من الاسم
# كل عنصر: (كلمات تظهر في اسم القسم, الإيموجي)
CATEGORY_EMOJI = [
    # موبايل وتابلت
    (["جوال", "موبايل", "هاتف", "cellular phone", "smartphone", "cell phone", "mobile phone"], "📱"),
    (["تابلت", "tablet", "ايباد", "ipad", "e-reader", "kindle"], "📱"),
    # كمبيوتر
    (["لابتوب", "laptop", "notebook computer", "كمبيوتر محمول", "macbook"], "💻"),
    (["كمبيوتر", "computer", "desktop", "pc "], "🖥️"),
    # صوتيات
    (["سماعة", "سماعات", "headphone", "earbud", "earphone", "headset", "speaker", "audio"], "🎧"),
    # ساعات
    (["ساعة", "watch", "smartwatch", "wristwatch"], "⌚"),
    # كاميرات
    (["كاميرا", "camera", "camcorder", "webcam"], "📷"),
    # تلفزيون وشاشات
    (["تلفزيون", "television", "شاشات", "monitor", "\"tv\"", "led tv"], "📺"),
    # تخزين
    (["تخزين", "فلاش", "ذاكرة", "memory card", "flash drive", "storage", "usb", "hard drive", "ssd", "sd card"], "💾"),
    # شحن وطاقة
    (["شاحن", "بطارية", "باور", "charger", "battery", "power bank", "كابل", "cable", "adapter"], "🔌"),
    # ماوس وكيبورد
    (["ماوس", "كيبورد", "لوحة مفاتيح", "mouse", "keyboard"], "🖱️"),
    # مطبخ - أدوات
    (["أدوات المطبخ", "ادوات المطبخ", "طهي", "cookware", "حلل", "مقلاة", "أواني", "pot", "pan", "kitchen tool", "utensil", "cutting board", "bakeware"], "🍳"),
    # مطبخ - أجهزة
    (["أجهزة المطبخ", "خلاطات", "blender", "عصارة", "mixer", "food processor", "juicer", "coffee maker"], "🍹"),
    # غسالات
    (["غسالات", "washing machine", "washer"], "🧺"),
    # تبريد
    (["ثلاجات", "تبريد", "refrigerator", "freezer", "fridge"], "🧊"),
    # مكانس
    (["مكانس", "vacuum", "تنظيف السجاد", "vacuum cleaner"], "🧹"),
    # مكواة
    (["مكواة", "iron", "steam iron", "garment steamer"], "👔"),
    # أفران
    (["ميكروويف", "افران", "أفران", "microwave", "oven", "توستر", "toaster", "air fryer"], "🔥"),
    # تكييف ومراوح
    (["مراوح", "تكييف", "fan", "air condition", "cooler", "heater"], "❄️"),
    # عطور
    (["عطور", "perfume", "fragrance", "cologne", "eau de", "body spray", "body splash", "deodorant"], "🌸"),
    # عناية بالبشرة
    (["العناية بالبشرة", "بشرة", "skin care", "skincare", "كريمات", "سيروم", "serum", "moisturizer", "lotion", "sunscreen", "face wash", "cleanser", "cream"], "🧴"),
    # عناية بالشعر
    (["العناية بالشعر", "شعر", "hair care", "haircare", "شامبو", "shampoo", "conditioner", "hair oil"], "🧴"),
    # مكياج
    (["مكياج", "makeup", "make-up", "تجميل", "cosmetic", "lipstick", "mascara", "foundation", "eyeliner", "concealer"], "💄"),
    # أحذية
    (["أحذية", "احذية", "shoe", "shoes", "footwear", "صنادل", "sandal", "sneaker", "slipper", "boot"], "👟"),
    # ملابس
    (["ملابس", "clothing", "apparel", "تيشيرت", "قمصان", "بناطيل", "فساتين", "shirt", "t-shirt", "pants", "trouser", "dress", "jacket", "hoodie", "jeans"], "👕"),
    # حقائب
    (["حقائب", "شنط", "bag", "handbag", "backpack", "luggage", "أمتعة", "wallet"], "👜"),
    # نظارات
    (["نظارات", "eyewear", "sunglasses", "glasses"], "🕶️"),
    # ألعاب
    (["ألعاب", "العاب", "toy", "toys", "game", "games", "puzzle"], "🧸"),
    # أطفال
    (["حفاضات", "مستلزمات الأطفال", "baby", "diaper", "رضاعة", "infant", "stroller"], "🍼"),
    # كتب وقرطاسية
    (["كتب", "قرطاسية", "أقلام", "book", "books", "stationery", "office product", "منتج للمكتب", "pen", "notebook", "pencil"], "📚"),
    # بقالة
    (["بقالة", "أطعمة", "اطعمة", "grocery", "food", "مواد غذائية", "توابل", "spice", "snack", "rice", "oil", "coffee", "tea", "sauce"], "🛒"),
    # ألبان
    (["ألبان", "حليب", "dairy", "milk", "cheese", "yogurt"], "🥛"),
    # مكملات
    (["مكملات", "بروتين", "supplement", "فيتامين", "nutrition", "protein", "vitamin", "whey"], "💪"),
    # منظفات
    (["منظفات", "detergent", "صابون", "soap", "مناديل", "tissue", "cleaning", "cleaner", "wipes"], "🧼"),
    # مجوهرات واكسسوار
    (["مجوهرات", "jewelry", "necklace", "bracelet", "ring", "earring", "اكسسوار"], "💍"),
    # رياضة
    (["رياضة", "sport", "fitness", "exercise", "yoga", "gym", "لياقة"], "🏋️"),
    # إلكترونيات عام (آخر حاجة — أعم من غيرها)
    (["إلكترونيات", "الكترونيات", "electronic", "electronics"], "🔌"),
]
def _category_emoji(nodes):
    """يرجّع إيموجي من قسم المنتج (تصنيف أمازون). أدق من الاسم."""
    if not nodes:
        return None
    blob = " | ".join(nodes).lower()
    for words, emoji in CATEGORY_EMOJI:
        for w in words:
            if w in blob:
                return emoji
    return None
def _pick_emoji(data):
    """يختار الإيموجي: أول من القسم (أدق)، وإلا من الاسم، وإلا مفيش"""
    ce = _category_emoji(data.get("nodes"))
    if ce:
        return ce
    return _product_emoji(data.get("title"))
def _short_title(title, max_words=7):
    """اسم مختصر للمنتج — أول جزء قبل الفاصلة، بحد أقصى كام كلمة"""
    if not title:
        return ""
    # ناخد أول جزء قبل أول فاصلة (عربي أو إنجليزي) أو شرطة
    t = re.split(r'[،,\-–]', title)[0].strip()
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words])
    return t.strip()
def _resale_lines(data, asin, tag):
    """لو المنتج متاح في أمازون ريسيل، يرجّع السطور اللي تتحط تحت اللينك الأساسي.
       (سطر فاضي + 🔥 متوفر في أمازون ريسيل + السعر + لينك الريسيل). وإلا سلسلة فاضية.
       شرط: الريسيل لازم يكون أرخص من السعر العادي بـ 3% على الأقل."""
    r = (data or {}).get("resale")
    if not r or not r.get("price"):
        return ""
    price = r["price"]
    # الريسيل لازم يكون أرخص من العادي بـ 3% على الأقل (عشان الخصومات الكبيرة
    # ممكن تخلّي العادي أرخص من الريسيل أو مساوي له)
    _cur = (data or {}).get("cur")
    if _cur and price:
        _gap = (_cur - price) / _cur * 100
        if _gap < 3:
            return ""   # الريسيل مش أرخص كفاية → مانعرضهوش
    smid = r.get("seller_id")
    link = f"https://www.amazon.eg/dp/{asin}?tag={tag}"
    if smid:
        link += f"&smid={smid}"
    # في وضع HTML نهرّب & عشان الـ parser مايكسرش اللينك
    link = link.replace("&", "&amp;")
    # نفرمت السعر زي باقي البوست
    try:
        p = f"{float(price):,.0f}"
    except Exception:
        p = str(price)
    out = (f"\n\n🔥 متوفر في أمازون ريسيل\n"
           f"<blockquote>💰السعر : {p} جنيه</blockquote>\n\n"
           f"{link}")
    # سطر حالة منتج الريسيل (النوت اللي بتيجي من أمازون) لو موجودة
    _note = (r.get("note") or "").strip()
    if _note:
        out += f"\n\nحالة منتج الريسيل : {_esc(_note)}"
    return out

# ═══ لينكات مخصصة لبعض المنتجات (ASIN → لينك مكتوب بشكل معين) ═══
# الملف: custom_links.txt جنب البوت. كل سطر: ASIN<تاب أو مسافات>اللينك
# مثال:   B0DJPNLRQ5    https://a.y-ay.com/xxxxx
# السطور اللي بتبدأ بـ # بتتجاهل (تعليقات)
_CUSTOM_LINKS = None
def load_custom_links():
    """يقرا custom_links.txt ويرجّع dict {ASIN: لينك}. يتقرا مرة واحدة (كاش)."""
    global _CUSTOM_LINKS
    if _CUSTOM_LINKS is not None:
        return _CUSTOM_LINKS
    _CUSTOM_LINKS = {}
    try:
        with open("custom_links.txt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # نفصل الـ ASIN عن اللينك (بأول مسافة/تاب)
                parts = line.split(None, 1)
                if len(parts) == 2:
                    _asin = parts[0].strip().upper()
                    _lnk = parts[1].strip()
                    if _asin and _lnk:
                        _CUSTOM_LINKS[_asin] = _lnk
        if _CUSTOM_LINKS:
            print(f"   🔗 اتحمّل {len(_CUSTOM_LINKS)} لينك مخصص من custom_links.txt")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"   ⚠️ مشكلة في قراءة custom_links.txt: {str(e)[:50]}")
    return _CUSTOM_LINKS

def _buy_link(asin, tag, smid=None):
    """يرجّع لينك الشراء. لو الـ ASIN ليه لينك مخصص في custom_links.txt،
       يرجّع اللينك المخصص زي ما هو. وإلا يبني اللينك العادي (بالتاج بس).
       ملاحظة: مابنحطش smid — عشان أمازون يعرض الأرخص (Buy Box الحالي) تلقائياً،
       مش بائع مثبّت ممكن يكون أغلى وقت ما العميل يفتح اللينك."""
    custom = load_custom_links().get((asin or "").upper())
    if custom:
        # اللينك المخصص زي ما هو (بس نهرّب & للـ HTML)
        return custom.replace("&", "&amp;")
    buy = f"https://www.amazon.eg/dp/{asin}?tag={tag}"
    return buy.replace("&", "&amp;")

def build_post_elwyy(asin, tag, data):
    """فورمات خاص لبوستات Elwyy (منتج منفرد)"""
    lines = []
    disc = data.get('disc') or 0
    has_disc = bool(disc and data.get('orig') and data['orig'] > data['cur'])
    # لو الخصم فوق 20% → سطر أول: 💥 خصم X% 💥
    if has_disc and disc > 20:
        lines.append(f"💥 خصم {disc:.0f}% 💥")
    # سطر العرض: 👑 عرض على [اسم مختصر] (من غير إيموجي بعده، والعنوان مش bold)
    short = data['title']
    lines.append(f"👑 عرض على {_esc(short)}")
    lines.append("")
    # سطر السعر داخل Quote (مع الخصومات الإضافية لو موجودة)
    lines.append(f"<blockquote>{_build_price_line(data)}</blockquote>")
    lines.append("")
    # لينك الشراء (بالـ smid بتاع البائع الأرخص، أو لينك مخصص لو الـ ASIN ليه واحد)
    _smid = (data or {}).get("seller_id")
    _buy = _buy_link(asin, tag, _smid)
    lines.append(f"لينك الشراء: {_buy}")
    _rl = _resale_lines(data, asin, tag)
    if _rl:
        lines.append(_rl)
    return "\n".join(lines)
def build_post(asin, tag, data, uname=None):
    """يبني البوست من بيانات أمازون"""
    import random
    # Elwyy: فورمات خاص للمنتجات المنفردة
    if uname == "elwyy":
        return build_post_elwyy(asin, tag, data)
    lines = []
    has_disc = bool(data['disc'] and data['orig'] and data['orig'] > data['cur'])
    # القنوات اللي ليها عنوان ثابت → العنوان الثابت بس (من غير جملة ولا سطر خصم بولد)
    if uname in FIXED_HEADER_SOURCES:
        lines.append(FIXED_HEADER)
    else:
        badge = (data.get("deal_badge") or "").strip()   # وصف العرض من أمازون
        if badge:
            # فيه وصف عرض (بادج) → نستخدمه في أول سطر
            #   لو فيه خصم:  🔥 خصم X% 🏷️ وصف البادج
            #   لو مفيش خصم: 🏷️ وصف البادج
            if has_disc:
                lines.append(f"🔥 <b>خصم {data['disc']:.0f}%</b> 🏷️ {_esc(badge)}")
            else:
                lines.append(f"🏷️ {_esc(badge)}")
        else:
            # مفيش بادج → الجملة العشوائية زي الأول
            # بوستات كنز اللي مفيهاش خصم → جملة من الملف الخاص بيها
            if not has_disc and uname in NODISC_HEADLINE_SOURCES:
                headline = random.choice(load_headlines_nodisc())
            else:
                headline = random.choice(load_headlines())
            # لو الخصم 25%+ → السطر الأول: 🔥 خصم X% (بولد) + الجملة
            if data['disc'] and data['disc'] >= 25:
                lines.append(f"🔥 <b>خصم {data['disc']:.0f}%</b> {headline}")
            else:
                lines.append(headline)
    lines.append(_esc(data['title']))
    lines.append("")
    # سطر السعر + الخصم دايماً في blockquote (عن طريق _build_price_line)
    lines.append(f"<blockquote>{_build_price_line(data)}</blockquote>")
    lines.append("")
    _smid2 = (data or {}).get("seller_id")
    _buy2 = _buy_link(asin, tag, _smid2)
    lines.append(f"🔗 {_buy2}")
    _rl = _resale_lines(data, asin, tag)
    if _rl:
        lines.append(_rl)
    return "\n".join(lines)
# ============ الإعدادات ============
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
# القنوات المصدر + التاج بتاع كل واحدة (المفتاح = اسم القناة lowercase من غير @)
# القنوات المصدر: (اسم القناة الأصلي) → (التاج, ياخد سكرين شوت؟)
# مهم: اكتبي اسم القناة زي ما هو بالظبط على تليجرام (بالحروف الكبيرة والصغيرة الصح)
SOURCES = {
    "@AmazonEgyptOffers":     {"tag": "burmit-21",  "screenshot": True},
    "@Belnos":                {"tag": "burmit-21",  "screenshot": True},
    "@EGFastAmzn":            {"tag": "burmit-21", "screenshot": True},
    "@Yo_Ayman":              {"tag": "burmit-21",  "screenshot": True},
    "@ba3bou3_deals":         {"tag": "burmit-21", "screenshot": True},
    "@melook_content":        {"tag": None, "screenshot": False},
    "@Dina_Contents":         {"tag": "burmit-21", "screenshot": True},
    "@dodohanem":             {"tag": "burmit-21", "screenshot": True},
    "@Elwyy":                 {"tag": "burmit-21", "screenshot": True},
    "@HalaAmaz":              {"tag": "halola-21", "screenshot": True},
}
# نبني جداول مساعدة بالـ lowercase للمقارنة
SOURCE_TAGS = {k.lstrip("@").lower(): v["tag"] for k, v in SOURCES.items()}
SCREENSHOT_SOURCES = {k.lstrip("@").lower() for k, v in SOURCES.items() if v["screenshot"]}
# القنوات اللي بنبني بوستها من بيانات أمازون (بدل نقل نص البوست)
# Yo_Ayman فيها → بتتبني بعنوان ثابت 👑
BUILD_POST_SOURCES = {"amazonegyptoffers", "belnos", "egfastamzn", "ba3bou3_deals", "dodohanem", "yo_ayman", "elwyy", "halaamaz", "dina_contents"}
# قنوات لازم يكون المنتج فيها البائع أمازون والشاحن أمازون
# + مابناخدش منها بوستات بحث/أقسام ولا بروموشن خالص
AMAZON_ONLY_SOURCES = {"amazonegyptoffers", "belnos", "egfastamzn", "ba3bou3_deals", "halaamaz"}
def is_amazon_seller_shipper(data):
    """يرجّع True لو البائع أمازون والشاحن أمازون (للقنوات المشروطة)"""
    if not data:
        return False
    return bool(data.get("seller_is_amazon")) and bool(data.get("fulfilled_by_amazon"))
# الحماية بتتكتشف تلقائياً من كل رسالة (msg.noforwards) — مفيش قايمة يدوية
# قنوات بننقل نصها زي ما هو، بس بعنوان ثابت فوقه (مش عنوان عشوائي)
FIXED_HEADER = "👑 العروض الملكيه اللي ما تتفوتش"
FIXED_HEADER_SOURCES = {"yo_ayman"}
# قنوات بوستاتها بتتعمل PIN (مع إشعار، من غير ما نشيل القديم)
PIN_SOURCES = {"yo_ayman"}
# قنوات بننقل بوستها زي ما هو بالظبط (نص + صور + لينك بتاجهم الأصلي)
# بس نفك اللينك عشان منع التكرار — مانغيّرش أي حاجة في البوست
PASSTHROUGH_SOURCES = {"melook_content"}
# قنوات بناخد منها بس في نافذة وقت معيّنة (بتوقيت مصر)
# المفتاح = اسم القناة lowercase، القيمة = (ساعة البداية, ساعة النهاية) بنظام 24
# مثال: (20, 24) يعني من 8 مساءً لـ 12 منتصف الليل
TIME_WINDOW_SOURCES = {
    "dina_contents": (10, 14),   # من 10 صباحاً لـ 2 ظهراً بتوقيت مصر
    # melook_content: مفتوح على مدار اليوم (مفيش نافذة وقت)
}
def _egypt_hour():
    """الساعة الحالية بتوقيت مصر (بيتعامل مع التوقيت الصيفي تلقائياً)"""
    import datetime as _dtmod
    try:
        from zoneinfo import ZoneInfo
        return _dtmod.datetime.now(ZoneInfo("Africa/Cairo")).hour
    except Exception:
        # احتياطي لو zoneinfo مش متاح — نجرّب pytz، وإلا UTC+2
        try:
            import pytz
            return _dtmod.datetime.now(pytz.timezone("Africa/Cairo")).hour
        except Exception:
            return (_dtmod.datetime.utcnow().hour + 2) % 24
def _in_time_window(uname):
    """يرجّع True لو القناة مالهاش نافذة وقت، أو لو دلوقتي داخل نافذتها (توقيت مصر)"""
    win = TIME_WINDOW_SOURCES.get(uname)
    if not win:
        return True
    start, end = win
    egypt_hour = _egypt_hour()
    if start <= end:
        return start <= egypt_hour < end
    else:  # نافذة بتعدّي منتصف الليل (زي 22 لـ 3)
        return egypt_hour >= start or egypt_hour < end
# قنوات بوستاتها اللي مفيهاش خصم بتاخد جملة من ملف خاص (headlines_nodisc_eg.txt)
NODISC_HEADLINE_SOURCES = {"egfastamzn"}
# حد أدنى للخصم لكل قناة (القنوات اللي مش هنا = مفيش حد أدنى)
MIN_DISCOUNT = {
    "egfastamzn": 40,   # كنز: 40% أو أكتر بس
}
# حد أقصى للسعر: منتج أغلى من كده مايتبعتش (لكل قناة)
MAX_PRICE = {
    "belnos": 2000,   # بلنوس: مايتبعتش أي منتج أغلى من 2000 جنيه
}
# حد أقصى للسعر حسب البراند (على كل القنوات)
# لو عنوان المنتج فيه أي صيغة من البراند وسعره أغلى من الحد → يتمنع
# كل عنصر: (قايمة صيغ البراند اللي تتفحص في العنوان, الحد الأقصى للسعر)
BRAND_MAX_PRICE = [
    (["ASTK", "ايه اس تي كيه", "استك"], 600),   # براند ASTK: مايزيدش عن 600
]
def brand_price_block(title, price):
    """يرجّع اسم البراند لو المنتج تبع براند محدود السعر وسعره أعلى من الحد، وإلا None"""
    if not title:
        return None
    t = title.lower()
    for brands, cap in BRAND_MAX_PRICE:
        for b in brands:
            if b.lower() in t and price > cap:
                return f"{b} (>{cap})"
    return None
# القنوات اللي بنبعت عليها (كلها مع بعض)
MY_CHANNELS = ["@EgyptOffersHunter", "@HalaOffersEgypt"]
# EgyptOffersHunter: يستقبل كل القنوات زي ما هو.
# HalaOffersEgypt: يستقبل بس المصادر اللي في القايمة دي (بتاج halola-21).
HALA_CHANNEL = "@HalaOffersEgypt"
HUNTER_CHANNEL = "@EgyptOffersHunter"
MELOOK_CHANNEL = "@melook_content"
# لو True: أي بوست يروح Hunter يروح كمان melook (بتاج burnit-21) — بيتحدد بسؤال أول التشغيل
MIRROR_HUNTER_TO_MELOOK = False
HALA_ALLOWED_SOURCES = set()   # مفيش مصدر بيتبعت على هالة تلقائياً (دينا ليها توجيه خاص)
# توجيه خاص لكل مصدر: قنوات هدف محددة (بيتغلّب على التوجيه العام)
# القناة اللي مش هنا بتتبعت على HUNTER (+ هالة لو في HALA_ALLOWED_SOURCES)
SOURCE_TARGETS = {
    "dina_contents": ["@HalaOffersEgypt", "@melook_content"],   # دينا → حلا + ملوك (في نافذتها)
}
# علامة مخفية (حروف صفرية العرض — مش بتظهر) بتتحط في محتوى دينا اللي بيروح ملوك
# عشان لما البوت يقرا ملوك يعرف إن ده جاي من دينا فمايبعتوش على هنتر
DINA_MARKER = "\u200b\u200c\u200b"   # ZWSP + ZWNJ + ZWSP
# قنوات ليها تاج ثابت خاص بيها (بغض النظر عن القناة المصدر)
# القنوات اللي مش هنا بتستخدم تاج القناة المصدر
CHANNEL_TAG_OVERRIDE = {
    "@HalaOffersEgypt": "burnit-21",
    "@melook_content": "burnit-21",   # أي حاجة تروح ملوك أو حلا تاخد burnit-21
}
# ============ السكرين شوت (في thread منفصل) ============
_PROFILE = os.getenv("PLAYWRIGHT_PROFILE", "/tmp/amazon_bot_profile_forwarder_eg" if SERVER_MODE else os.path.expanduser("~/amazon_bot_profile_forwarder_eg"))
def _clear_thread_loop():
    """يتنفّذ مرة عند إنشاء thread الـ executor — يضمن إنه نظيف من أي asyncio loop
       عشان Playwright sync يشتغل صح (مهم في Python 3.14)."""
    try:
        import asyncio as _aio
        _aio.set_event_loop(None)
    except Exception:
        pass

_EXECUTOR = ThreadPoolExecutor(max_workers=1, initializer=_clear_thread_loop)
def _read_price_from_page(asin, smid=None):
    """يفتح صفحة المنتج (بالـ smid) ويقرا السعر الفعلي الظاهر — عشان لو الـ API
       رجّع سعر قديم/غلط، ناخد السعر الحقيقي من الصفحة. يرجّع رقم أو None."""
    if not PLAYWRIGHT:
        return None
    try:
        import asyncio as _aio
        try:
            _ex = _aio.get_event_loop()
        except Exception:
            _ex = None
        if _ex is not None and not _ex.is_running():
            _aio.set_event_loop(None)
    except Exception:
        pass
    for use_chrome in ((False,) if SERVER_MODE else (True, False)):
        pw = ctx = None
        try:
            pw = sync_playwright().start()
            kwargs = dict(viewport={"width": 1000, "height": 800}, locale="ar-EG",
                          args=["--lang=ar-EG", "--no-sandbox", "--disable-dev-shm-usage"], ignore_default_args=["--disable-extensions"])
            if use_chrome:
                kwargs["channel"] = "chrome"; kwargs["headless"] = False
            else:
                kwargs["headless"] = True
            ctx = pw.chromium.launch_persistent_context(_PROFILE, **kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.context.add_cookies([{"name": "lc-acbeg", "value": "ar_EG",
                                       "domain": ".amazon.eg", "path": "/"}])
            _u = f"https://www.amazon.eg/-/ar/dp/{asin}"
            if smid:
                _u += f"?smid={smid}"
            page.goto(_u, wait_until="domcontentloaded", timeout=40000)
            time.sleep(2)
            # نقرا السعر من الأماكن المعروفة في صفحة أمازون
            price = None
            for sel in ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                        "#corePrice_feature_div .a-price .a-offscreen",
                        "span.a-price span.a-offscreen",
                        "#priceblock_ourprice", "#priceblock_dealprice"):
                try:
                    el = page.query_selector(sel)
                    if el:
                        txt = el.inner_text() or el.text_content() or ""
                        nums = re.findall(r"[\d,]+(?:\.\d+)?", txt.replace("\xa0", ""))
                        for n in nums:
                            v = float(n.replace(",", ""))
                            if v > 0:
                                price = v
                                break
                    if price:
                        break
                except Exception:
                    pass
            return price
        except Exception:
            pass
        finally:
            try:
                if ctx: ctx.close()
                if pw: pw.stop()
            except:
                pass
    return None
# نخزّن السعر اللي نقرأه وقت السكرين شوت (عشان مانفتحش الصفحة مرتين)
_LAST_PAGE_PRICE = {}
# نخزّن مكان السعر الظاهر في الصورة المقصوصة (عشان نرسم فريم حواليه)
_LAST_PRICE_BOX = {}
# نخزّن الخصومات الإضافية اللي نقرأها من الصفحة (وفر عند الدفع/كوبون/كمية)
_LAST_EXTRA_DISC = {}

def _parse_extra_discounts(page_text):
    """يطلّع الخصومات الإضافية من نص صفحة المنتج:
       {pay_pct, coupon_pct, coupon_amount, qty_pct, qty_num, bxpy_get, bxpy_pay}"""
    norm = (page_text or "").replace("\xa0", " ").replace("\u202f", " ")
    # نشيل التشكيل (شدة/فتحة/كسرة/ضمة...) عشان "وفِّر" و"وفّر" و"وفر" يتعاملوا زي بعض
    norm = re.sub(r'[\u0617-\u061A\u064B-\u0652\u0670]', '', norm)
    out = {"pay_pct": 0, "coupon_pct": 0, "coupon_amount": 0, "qty_pct": 0,
           "qty_num": 0, "bxpy_get": 0, "bxpy_pay": 0}
    # وفر X% عند الدفع (نقبل الشدّة: وفّر/وفِّر)
    m = re.search(r'وفر\s*(\d+)\s*%\s*عند الدفع', norm)
    if m:
        out["pay_pct"] = int(m.group(1))
    # وفر Y% على Z سلع/سلعة
    m = re.search(r'وفر\s*(\d+)\s*%\s*على\s*(\d+)\s*سلع', norm)
    if m:
        out["qty_pct"] = int(m.group(1)); out["qty_num"] = int(m.group(2))
    # احصل على X بسعر Y (اشتري X وادفع سعر Y)
    m = re.search(r'احصل على\s*(\d+)\s*بسعر\s*(\d+)', norm)
    if m:
        out["bxpy_get"] = int(m.group(1)); out["bxpy_pay"] = int(m.group(2))
    # كوبون نسبة أو مبلغ
    m = re.search(r'(?:تطبيق\s*)?(\d+)\s*%\s*كوبون|كوبون\s*(\d+)\s*%', norm)
    if m:
        out["coupon_pct"] = int(m.group(1) or m.group(2))
    else:
        m = re.search(r'تطبيق\s*EGP\s*([\d,]+)|EGP\s*([\d,]+)\s*كوبون|كوبون\s*([\d,]+)\s*جنيه', norm)
        if m:
            _v = m.group(1) or m.group(2) or m.group(3)
            out["coupon_amount"] = int(_v.replace(",", ""))
    # وفّر X% رمز العرض الترويجي (خصم نسبة على الصفحة) — نعامله زي عند الدفع
    if not out["pay_pct"]:
        m = re.search(r'وفر\s*(\d+)\s*%\s*رمز العرض', norm)
        if m:
            out["pay_pct"] = int(m.group(1))
    return out

def _build_price_line(data):
    """يبني سطر السعر مع الخصومات الإضافية (لو موجودة).
       يرجّع نص السطر (بالسعر النهائي + الخصومات)."""
    cur = data.get("cur")
    orig = data.get("orig") or cur
    disc = data.get("disc") or 0
    ex = data.get("extra") or {}
    pay_pct = ex.get("pay_pct", 0)
    coupon_pct = ex.get("coupon_pct", 0)
    coupon_amount = ex.get("coupon_amount", 0)
    qty_pct = ex.get("qty_pct", 0)
    qty_num = ex.get("qty_num", 0)
    bxpy_get = ex.get("bxpy_get", 0)
    bxpy_pay = ex.get("bxpy_pay", 0)

    # لو مفيش خصومات إضافية → السعر + سطر الخصم (لو موجود) في نفس الـ quote
    if not (pay_pct or coupon_pct or coupon_amount or bxpy_get):
        _line = f"💰 السعر : <b>{_fmt_price(cur)} جنيه</b>"
        if disc and orig and orig > cur:
            _line += f"\n📉 خصم {disc:.0f}% (بدلاً من {_fmt_price(orig)} جنيه)"
        return _line

    # نحسب كل السيناريوهات ونختار الأرخص، ونكتبه هو بس مع سبب خصمه
    options = []   # [(السعر, الوصف)]

    # 1) عند الدفع + كوبون (نسبة/مبلغ) — كلها تتطبّق على طول للقطعة الواحدة
    extra_pct = pay_pct + coupon_pct
    if extra_pct or coupon_amount:
        p = cur - (orig * extra_pct / 100)
        if coupon_amount:
            p -= coupon_amount
        _parts = []
        if pay_pct:
            _parts.append(f"{pay_pct}% عند الدفع")
        if coupon_pct:
            _parts.append(f"كوبون {coupon_pct}%")
        elif coupon_amount:
            _parts.append(f"كوبون {coupon_amount} جنيه")
        options.append((p, "بعد خصم " + " و".join(_parts)))

    # 3) احصل على X بسعر Y (متوسط القطعة)
    if bxpy_get and bxpy_pay:
        p = (orig * bxpy_pay) / bxpy_get
        options.append((p, f"عند شراء {bxpy_get} قطع"))

    if not options:
        return f"💰 السعر : <b>{_fmt_price(cur)} جنيه</b>"

    # نختار الأرخص
    best_price, best_desc = min(options, key=lambda x: x[0])
    return f"💰 السعر : <b>{_fmt_price(best_price)} جنيه</b> ({best_desc})"

def _draw_crown(draw, cx, cy, size, color=(255, 200, 40), outline=(220, 160, 0), angle=-28):
    """تاج مائل فاضي من جوّه (حدود بس) — 3 رؤوس، الأوسط أطول، قاعدة منحنية."""
    import math as _m
    w = size
    h = size * 0.75
    left = -w / 2
    right = w / 2
    top = -h / 2
    mid_top = -h * 0.66
    valley = -h * 0.05
    base_top = h * 0.15
    a = _m.radians(angle)
    ca, sa = _m.cos(a), _m.sin(a)
    def _rot(px, py):
        return (cx + px * ca - py * sa, cy + px * sa + py * ca)
    top_pts = [
        (left, base_top), (left, top), (-w * 0.18, valley),
        (0.0, mid_top), (w * 0.18, valley), (right, top), (right, base_top),
    ]
    base_curve = []
    steps = 20
    base_bottom = h * 0.42
    lift = h * 0.30
    for i in range(steps + 1):
        t = i / steps
        px = right + (left - right) * t
        curve = base_bottom - lift * _m.sin(_m.pi * t)
        base_curve.append((px, curve))
    tpts = [_rot(px, py) for (px, py) in top_pts + base_curve]
    # فاضي من جوّه (مفيش تعبئة) — الحدود بس
    draw.line(tpts + [tpts[0]], fill=outline, width=max(1, int(size * 0.035)), joint="curve")
    r = max(2, int(size * 0.10))
    for (px, py) in [(left, top), (0.0, mid_top), (right, top)]:
        bx, by = _rot(px, py)
        draw.ellipse([bx - r, by - r, bx + r, by + r], fill=(230, 60, 60), outline=outline)

def add_price_frame(shot_bytes, price_box=None):
    """يحط فريم rounded square حوالين السعر + تاج فوق الركن الشمال.
       يُستخدم بس للمنتج الواحد (مش المتعدد ولا البروموشن)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return shot_bytes
    import io as _iof
    im = Image.open(_iof.BytesIO(shot_bytes)).convert("RGB")
    W, H = im.size
    draw = ImageDraw.Draw(im)
    if price_box and price_box.get("w") and price_box.get("h"):
        pad_top = 3
        # الطول لتحت: زيادة بسيطة عشان ياخد السعر المشطوب اللي تحت السعر
        pad_bottom = int(price_box["h"] * 0.35) + 5
        # الحد الشمال: عند بداية السعر/الخصم (هامش صغير)
        left = max(0, price_box["x"] - 4)
        # الحد اليمين: يبدأ من يمين العنوان (بداية العنوان في RTL)،
        # وياخد في اعتباره الخصم لو طالع بره العنوان شوية على اليمين
        # (+ هامش عشان علامة "-" اللي قبل نسبة الخصم متتداسش)
        _price_right = price_box["x"] + price_box["w"]
        if price_box.get("title_right") is not None:
            right = min(W, int(max(price_box["title_right"], _price_right)) + 12)
        else:
            right = min(W, _price_right + int(price_box["w"] * 0.18) + 12)
        top = max(0, price_box["y"] - pad_top)
        bottom = min(H, price_box["y"] + price_box["h"] + pad_bottom)
        bh = bottom - top
    else:
        # مالقيناش مكان السعر — مانرسمش فريم (أحسن من مكان غلط)
        return shot_bytes
    radius = max(8, int(bh * 0.35))
    draw.rounded_rectangle([left, top, right, bottom],
                           radius=radius, outline=(230, 60, 60), width=2)
    _csize = max(24, int(bh * 0.7))
    _cx = left + int(_csize * 0.15)
    _cy = top - int(_csize * 0.20)
    _draw_crown(draw, _cx, _cy, size=_csize)
    out = _iof.BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()

def _shot_and_price_sync(asin, smid=None):
    """نسخة طبق الأصل من take_shot_and_price بتاعة التست:
       ياخد سكرين شوت لصورة+عنوان+سعر المنتج، ويرجّع (الصورة، مكان السعر نسبة للقص)."""
    if not PLAYWRIGHT:
        return None, None
    for use_chrome in ((False,) if SERVER_MODE else (True, False)):
        pw = ctx = None
        try:
            pw = sync_playwright().start()
            kwargs = dict(viewport={"width": 1280, "height": 1100}, locale="ar-EG",
                          args=["--lang=ar-EG", "--no-sandbox", "--disable-dev-shm-usage"], ignore_default_args=["--disable-extensions"])
            if use_chrome:
                kwargs["channel"] = "chrome"; kwargs["headless"] = False
            else:
                kwargs["headless"] = True
            ctx = pw.chromium.launch_persistent_context(_PROFILE, **kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.context.add_cookies([{"name": "lc-acbeg", "value": "ar_EG",
                                       "domain": ".amazon.eg", "path": "/"}])
            url = f"https://www.amazon.eg/-/ar/dp/{asin}"
            if smid:
                url += f"?smid={smid}"
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            time.sleep(3)

            # لو المنتج فيه "الاشتراك والتوفير"، نضغط "الشراء لمرة واحدة"
            # عشان الصفحة تعرض السعر العادي مش سعر الاشتراك (قبل السكرين شوت)
            try:
                _body_chk = page.evaluate("() => document.body.innerText")
                if "الاشتراك والتوفير" in _body_chk:
                    for _sel in ("#newAccordionRow", "#buyOneTimeRow",
                                 "#oneTimeBuyBox_feature_div",
                                 "text=الشراء لمرة واحدة"):
                        try:
                            _el = page.query_selector(_sel)
                            if _el and _el.bounding_box():
                                _el.scroll_into_view_if_needed()
                                time.sleep(0.3)
                                _el.click(timeout=3000)
                                time.sleep(2)   # نستنى الواجهة تتحدّث
                                break
                        except Exception:
                            continue
            except Exception:
                pass

            # نصغّر الصفحة كلها (zoom out) قبل السكرين شوت — 0.67 = 67%
            ZOOM = 0.67
            try:
                page.evaluate(f"document.body.style.zoom = '{ZOOM}'")
                time.sleep(1)
            except Exception:
                pass

            def _box(selectors):
                for s in selectors:
                    try:
                        el = page.query_selector(s)
                        if el:
                            b = el.bounding_box()
                            if b and b["height"] > 20:
                                return b
                    except Exception:
                        pass
                return None

            img_box = _box(["#imgTagWrapperId", "#main-image-container",
                            "#imageBlock", "#leftCol", "#altImages"])
            center_box = _box(["#centerCol", "#title_feature_div", "#productTitle"])
            price_box = _box(["#corePriceDisplay_desktop_feature_div span.a-price",
                              "#corePrice_feature_div span.a-price",
                              "#apex_desktop span.a-price",
                              "span.priceToPay",
                              "#corePriceDisplay_desktop_feature_div",
                              "#corePrice_feature_div"])
            # نسبة الخصم (زي "خصم 28%") — بتكون جنب السعر، نضمّها للمربع
            pct_box = _box([".savingsPercentage",
                            "#corePriceDisplay_desktop_feature_div .savingsPercentage",
                            "#corePrice_feature_div .savingsPercentage"])
            if price_box and pct_box:
                _x1 = min(price_box["x"], pct_box["x"])
                _y1 = min(price_box["y"], pct_box["y"])
                _x2 = max(price_box["x"] + price_box["width"], pct_box["x"] + pct_box["width"])
                _y2 = max(price_box["y"] + price_box["height"], pct_box["y"] + pct_box["height"])
                price_box = {"x": _x1, "y": _y1, "width": _x2 - _x1, "height": _y2 - _y1}

            # نقرا قيمة السعر الفعلية من الصفحة (عشان الـ API أحياناً متأخّر)
            page_price = None
            for psel in ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                         "#corePrice_feature_div .a-price .a-offscreen",
                         "span.a-price span.a-offscreen",
                         "#priceblock_ourprice", "#priceblock_dealprice"):
                try:
                    pe = page.query_selector(psel)
                    if pe:
                        txt = pe.inner_text() or pe.text_content() or ""
                        nums = re.findall(r"[\d,]+(?:\.\d+)?", txt.replace("\xa0", ""))
                        for n in nums:
                            v = float(n.replace(",", ""))
                            if v > 0:
                                page_price = v
                                break
                    if page_price:
                        break
                except Exception:
                    pass

            # نقرا الخصومات الإضافية من الصفحة (وفر عند الدفع/كوبون/كمية)
            try:
                _body = page.evaluate("() => document.body.innerText")
                _LAST_EXTRA_DISC[asin] = _parse_extra_discounts(_body)
            except Exception:
                pass

            if not img_box:
                shot = page.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": 900},
                                       type="jpeg", quality=88)
                return shot, None, page_price

            top = max(0, img_box["y"] - 12)
            # الطول: لحد أبعد نقطة (صورة المنتج أو السعر + مساحة تحته للمشطوب)
            bottoms = [img_box["y"] + img_box["height"] + 15]
            if price_box:
                bottoms.append(price_box["y"] + price_box["height"] + int(price_box["height"] * 0.9) + 15)
            bottom = max(bottoms)
            boxes = [b for b in (img_box, center_box, price_box) if b]
            left = max(0, min(b["x"] for b in boxes) - 12)
            right = min(1280, max(b["x"] + b["width"] for b in boxes) + 12)
            h = bottom - top
            print(f"   ✂️ القص: {right-left:.0f}×{h:.0f}px")
            shot = page.screenshot(
                clip={"x": left, "y": top, "width": right - left, "height": h},
                type="jpeg", quality=90)

            rel_price = None
            if price_box:
                rel_price = {
                    "x": price_box["x"] - left,
                    "y": price_box["y"] - top,
                    "w": price_box["width"],
                    "h": price_box["height"],
                }
                # يمين العنوان (بداية العنوان في RTL) — عشان الحد اليمين للفريم يتحاذى معاها
                if center_box:
                    rel_price["title_right"] = (center_box["x"] + center_box["width"]) - left
            return shot, rel_price, page_price
        except Exception as e:
            if not use_chrome:
                print(f"   ⚠️ فشل سكرين شوت المنتج الواحد: {str(e)[:80]}")
        finally:
            try:
                if ctx: ctx.close()
                if pw: pw.stop()
            except:
                pass
    return None, None, None

async def get_shot_with_frame(asin, smid=None):
    """للمنتج الواحد: سكرين شوت + فريم وتاج حوالين السعر (زي التست بالظبط).
       يرجّع (الصورة، سعر الصفحة)."""
    loop = asyncio.get_running_loop()
    shot, pbox, page_price = await loop.run_in_executor(_EXECUTOR, _shot_and_price_sync, asin, smid)
    if shot and pbox:
        try:
            shot = add_price_frame(shot, pbox)
            print(f"   👑 اتحط الفريم والتاج حوالين السعر")
        except Exception as e:
            print(f"   ⚠️ مقدرتش أحط الفريم: {str(e)[:50]}")
    elif shot:
        print(f"   ⚠️ مالقيتش مكان السعر — البوست هيتبعت من غير فريم")
    return shot, page_price

def _screenshot_sync(asin_or_url, is_url=False, tall=False, smid=None):
    """ياخد سكرين شوت لصفحة منتج (بالـ ASIN) أو لأي URL (is_url=True).
       smid: لو موجود، نفتح صفحة البائع ده عشان السكرين شوت يعرض سعره."""
    if not PLAYWRIGHT:
        return None
    # مهم (Python 3.14): نتأكد إن الـ thread ده مافيهوش asyncio event loop،
    # عشان Playwright sync مايرفضش الشغل ("Sync API inside asyncio loop").
    try:
        import asyncio as _aio
        try:
            _existing = _aio.get_event_loop()
        except Exception:
            _existing = None
        if _existing is not None and not _existing.is_running():
            _aio.set_event_loop(None)
    except Exception:
        pass
    # نجرّب Chrome الأول، ولو فشل نجرّب Chromium بتاع Playwright
    for use_chrome in ((False,) if SERVER_MODE else (True, False)):
        pw = ctx = page = None
        try:
            pw = sync_playwright().start()
            _h = 1400 if tall else 900
            kwargs = dict(
                viewport={"width": 1280, "height": _h}, locale="ar-EG",
                args=["--lang=ar-EG", "--no-sandbox", "--disable-dev-shm-usage"], ignore_default_args=["--disable-extensions"])
            if use_chrome:
                kwargs["channel"] = "chrome"
                kwargs["headless"] = False
            else:
                kwargs["headless"] = True   # Chromium بدون واجهة (أضمن)
            ctx = pw.chromium.launch_persistent_context(_PROFILE, **kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.context.add_cookies([{"name": "lc-acbeg", "value": "ar_EG",
                                       "domain": ".amazon.eg", "path": "/"}])
            target = asin_or_url if is_url else f"https://www.amazon.eg/-/ar/dp/{asin_or_url}"
            if smid and not is_url:
                target += f"?smid={smid}"
            page.goto(target, wait_until="domcontentloaded", timeout=40000)
            time.sleep(4 if is_url else 2)   # صفحات البحث محتاجة وقت أطول
            # لصفحات البحث/العروض: ناخد أول صف منتجات بس (مش الصفحة كلها)
            if is_url and "/s?" in target:
                shot = None
                try:
                    cards = page.query_selector_all('div[data-component-type="s-search-result"]')
                    boxes = []
                    for el in cards[:8]:
                        b = el.bounding_box()
                        if b and b["height"] > 100:
                            boxes.append(b)
                    if boxes:
                        # أول صف = الكروت اللي على نفس الارتفاع تقريباً
                        top = min(b["y"] for b in boxes)
                        row = [b for b in boxes if abs(b["y"] - top) < 40]
                        x0 = min(b["x"] for b in row)
                        x1 = max(b["x"] + b["width"] for b in row)
                        y1 = max(b["y"] + b["height"] for b in row)
                        shot = page.screenshot(
                            clip={"x": max(0, x0 - 5), "y": max(0, top - 5),
                                  "width": min(1280 - x0, x1 - x0 + 10),
                                  "height": min(900, y1 - top + 10)},
                            type="jpeg", quality=88)
                        print(f"      📐 أول صف: {len(row)} منتج")
                except Exception as e:
                    print(f"      ⚠️ مالقيتش صف المنتجات: {str(e)[:60]}")
                if shot is None:
                    shot = page.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": _h},
                                           type="jpeg", quality=85)
            else:
                # للمنتج المفرد: نقص من أعلى صورة المنتج لحد أسفل العنوان
                shot = None
                try:
                    # تخطيط أمازون: leftCol=صورة المنتج، centerCol=العنوان/السعر، rightCol=المربعات/البايبوكس
                    # عايزين: العرض = من أول صورة المنتج لآخر مربع (الأعمدة الـ3)
                    #         الطول = من فوق لحد نهاية صورة المنتج
                    def _box(selectors):
                        for s in selectors:
                            el = page.query_selector(s)
                            if el:
                                b = el.bounding_box()
                                if b and b["height"] > 30 and b["width"] > 30:
                                    return b, s
                        return None, None
                    img_box, _ = _box(["#imgTagWrapperId", "#main-image-container",
                                       "#imageBlock", "#leftCol", "#altImages"])
                    center_box, _ = _box(["#centerCol", "#title_feature_div", "#productTitle"])
                    right_box, _ = _box(["#rightCol", "#buybox", "#desktop_buybox",
                                         "#apex_desktop", "#corePriceDisplay_desktop_feature_div"])
                    if img_box:
                        # الطول: من أعلى الصفحة المفيدة لحد نهاية صورة المنتج
                        top = max(0, img_box["y"] - 10)
                        bottom = img_box["y"] + img_box["height"] + 12
                        # العرض: من أبعد نقطة يمين لأبعد نقطة شمال (الأعمدة الموجودة)
                        boxes = [b for b in (img_box, center_box, right_box) if b]
                        left = max(0, min(b["x"] for b in boxes) - 10)
                        right = min(1280, max(b["x"] + b["width"] for b in boxes) + 10)
                        h = min(bottom - top, _h - top)
                        _crop_left, _crop_top = left, top
                        print(f"   ✂️ القص: العرض {right-left:.0f}px (الأعمدة) × الطول {h:.0f}px (لحد نهاية الصورة)")
                        shot = page.screenshot(
                            clip={"x": left, "y": top, "width": right - left, "height": h},
                            type="jpeg", quality=88)
                except Exception as e:
                    print(f"   ⚠️ مالقيتش عناصر المنتج: {str(e)[:60]}")
                if shot is None:
                    shot = page.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": _h},
                                           type="jpeg", quality=85)
                # نقرا السعر من نفس الصفحة (عشان مانفتحش الصفحة تاني للسعر)
                if not is_url:
                    try:
                        _pv = None
                        for sel in ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                                    "#corePrice_feature_div .a-price .a-offscreen",
                                    "span.a-price span.a-offscreen",
                                    "#priceblock_ourprice", "#priceblock_dealprice"):
                            el = page.query_selector(sel)
                            if el:
                                txt = el.inner_text() or el.text_content() or ""
                                nums = re.findall(r"[\d,]+(?:\.\d+)?", txt.replace("\xa0", ""))
                                for nnum in nums:
                                    v = float(nnum.replace(",", ""))
                                    if v > 0:
                                        _pv = v
                                        break
                            if _pv:
                                break
                        _key = asin_or_url if not is_url else None
                        if _key:
                            _LAST_PAGE_PRICE[_key] = _pv
                        # نجيب مكان السعر الظاهر (المرئي) عشان نرسم فريم حواليه
                        # بالنسبة للقص: نطرح إزاحة القص (left, top)
                        try:
                            _pbox = None
                            for psel in ("#corePriceDisplay_desktop_feature_div span.a-price",
                                         "#corePrice_feature_div span.a-price",
                                         "#apex_desktop span.a-price",
                                         "span.priceToPay",
                                         "#corePriceDisplay_desktop_feature_div",
                                         "#corePrice_feature_div"):
                                pe = page.query_selector(psel)
                                if pe:
                                    pb = pe.bounding_box()
                                    if pb and pb["width"] > 20 and pb["height"] > 10:
                                        _pbox = pb
                                        break
                            if _pbox and _key:
                                # الإزاحة: نطرح مكان بداية القص عشان الإحداثيات تبقى نسبة للصورة المقصوصة
                                try:
                                    _ox = _crop_left
                                    _oy = _crop_top
                                except NameError:
                                    _ox = _oy = 0
                                _LAST_PRICE_BOX[_key] = {
                                    "x": _pbox["x"] - _ox,
                                    "y": _pbox["y"] - _oy,
                                    "w": _pbox["width"],
                                    "h": _pbox["height"],
                                }
                                print(f"   📍 لقيت مكان السعر للفريم: {_LAST_PRICE_BOX[_key]}")
                            elif _key:
                                print(f"   ⚠️ مالقيتش مكان السعر الظاهر — مفيش فريم")
                        except Exception:
                            pass
                    except Exception:
                        pass
            return shot
        except Exception as e:
            if use_chrome:
                print(f"   ⚠️ Chrome فشل — بجرّب Chromium...")
            else:
                print(f"   ⚠️ مقدرتش آخد سكرين شوت: {str(e)[:120]}")
        finally:
            try:
                if ctx: ctx.close()
                if pw: pw.stop()
            except:
                pass
    return None
def _multi_cards_sync(items):
    """ياخد سكرين شوت لصورة+عنوان كل منتج من صفحته (بالـ smid بتاع البائع الأرخص)
       ويدمجهم في صورة واحدة. items = list من (asin, smid) أو asin عادي."""
    if not PLAYWRIGHT:
        return None
    try:
        from PIL import Image
    except ImportError:
        print("   ⚠️ Pillow مش متثبّتة — py -m pip install Pillow")
        return None
    import io as _io
    # نوحّد الشكل: كل عنصر (asin, smid)
    norm = []
    for it in items:
        if isinstance(it, (tuple, list)):
            norm.append((it[0], it[1] if len(it) > 1 else None))
        else:
            norm.append((it, None))
    shots = []
    for use_chrome in ((False,) if SERVER_MODE else (True, False)):
        pw = ctx = None
        try:
            pw = sync_playwright().start()
            kwargs = dict(viewport={"width": 1280, "height": 900}, locale="ar-EG",
                          args=["--lang=ar-EG", "--no-sandbox", "--disable-dev-shm-usage"], ignore_default_args=["--disable-extensions"])
            if use_chrome:
                kwargs["channel"] = "chrome"; kwargs["headless"] = False
            else:
                kwargs["headless"] = True
            ctx = pw.chromium.launch_persistent_context(_PROFILE, **kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.context.add_cookies([{"name": "lc-acbeg", "value": "ar_EG",
                                       "domain": ".amazon.eg", "path": "/"}])
            for a, smid in norm:
                try:
                    # نفتح صفحة بحث بالـ ASIN (الكارت الكامل الكبير والواضح)
                    # ونضيف smid عشان الكارت يعرض سعر البائع الأرخص
                    _s = f"https://www.amazon.eg/-/ar/s?k={a}"
                    if smid:
                        _s += f"&smid={smid}"
                    page.goto(_s, wait_until="domcontentloaded", timeout=40000)
                    time.sleep(3)
                    el = None
                    for sel in (f'div[data-asin="{a}"]',
                                'div[data-component-type="s-search-result"]',
                                'div.s-result-item[data-asin]'):
                        el = page.query_selector(sel)
                        if el:
                            box = el.bounding_box()
                            if box and box["height"] > 100:
                                break
                            el = None
                    if el:
                        shots.append(el.screenshot(type="jpeg", quality=88))
                        print(f"      ✅ كارت {a} اتجاب")
                    else:
                        # مالقيناش الكارت في البحث → نفتح صفحة المنتج نفسها ونقص صورته+عنوانه
                        print(f"      ⚠️ كارت {a} مش في البحث — بفتح صفحة المنتج...")
                        try:
                            _u = f"https://www.amazon.eg/-/ar/dp/{a}"
                            if smid:
                                _u += f"?smid={smid}"
                            page.goto(_u, wait_until="domcontentloaded", timeout=40000)
                            time.sleep(2)
                            pel = None
                            for s2 in ("#imgTagWrapperId", "#main-image-container", "#imageBlock", "#centerCol"):
                                pel = page.query_selector(s2)
                                if pel and pel.bounding_box() and pel.bounding_box()["height"] > 50:
                                    break
                                pel = None
                            if pel:
                                shots.append(pel.screenshot(type="jpeg", quality=88))
                                print(f"      ✅ كارت {a} اتجاب من صفحة المنتج")
                            else:
                                print(f"      ❌ كارت {a} مش موجود خالص — اتخطّى")
                        except Exception as e2:
                            print(f"      ❌ كارت {a} صفحة المنتج فشلت: {str(e2)[:50]}")
                except Exception as e:
                    print(f"   ⚠️ كارت {a} فشل: {str(e)[:60]}")
            break
        except Exception as e:
            if use_chrome:
                print("   ⚠️ Chrome فشل — بجرّب Chromium...")
            else:
                print(f"   ⚠️ مقدرتش آخد الكروت: {str(e)[:100]}")
        finally:
            try:
                if ctx: ctx.close()
                if pw: pw.stop()
            except:
                pass
    if not shots:
        return None
    if len(shots) == 1:
        return shots[0]
    # ندمجهم في صفوف (5 في الصف) — نوّحّد حجم كل كارت قبل الدمج عشان مايبقاش فيه فراغات
    try:
        imgs = [Image.open(_io.BytesIO(s)).convert("RGB") for s in shots]
        GAP = 14
        PER_ROW = 5                 # أقصى عدد كروت في الصف
        # عرض موحّد لكل كارت — نستخدم متوسط عرض الكروت الفعلي (مش رقم صغير ثابت)
        # عشان الكروت تفضل بحجمها الطبيعي الواضح، مش مصغّرة
        _avg_w = sum(im.width for im in imgs) / len(imgs)
        CARD_W = max(460, int(_avg_w))   # على الأقل 460، أو المتوسط الفعلي لو أكبر
        # 1) نوّحّد عرض كل كارت لـ CARD_W (بنسبة وتناسب — من غير تشويه)
        scaled = []
        for im in imgs:
            r = CARD_W / im.width
            nh = max(1, int(im.height * r))
            scaled.append(im.resize((CARD_W, nh), Image.LANCZOS))
        # 2) نوّحّد الطول لأطول كارت (نحط الكارت فوق خلفية بيضاء بنفس الطول)
        CARD_H = max(im.height for im in scaled)
        cards = []
        for im in scaled:
            if im.height == CARD_H:
                cards.append(im)
            else:
                # خلفية بيضاء بنفس المقاس، والكارت في النص (أفقياً) من فوق
                bg = Image.new("RGB", (CARD_W, CARD_H), (255, 255, 255))
                bg.paste(im, (0, 0))
                cards.append(bg)
        # 3) نقسّمهم صفوف (5 في الصف)
        n = len(cards)
        rows = [cards[i:i+PER_ROW] for i in range(0, n, PER_ROW)]
        # 4) نبني الكانفاس — كل صف نفس ارتفاع الكارت، كل كارت نفس العرض
        max_in_row = max(len(r) for r in rows)
        W = max_in_row * CARD_W + GAP * (max_in_row - 1)
        H = len(rows) * CARD_H + GAP * (len(rows) - 1)
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        y = 0
        for row in rows:
            # ترتيب من اليمين للشمال (RTL): أول منتج على أقصى اليمين
            x = W - CARD_W
            for im in row:
                canvas.paste(im, (x, y))
                x -= CARD_W + GAP
            y += CARD_H + GAP
        buf = _io.BytesIO()
        canvas.save(buf, "JPEG", quality=92)
        return buf.getvalue()
    except Exception as e:
        print(f"   ⚠️ مشكلة في دمج الصور: {str(e)[:80]}")
        return shots[0]
async def get_multi_cards(asins):
    """سكرين شوت مدموج لكذا منتج"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _multi_cards_sync, asins)
async def get_clean_screenshot(asin, smid=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _screenshot_sync, asin, False, False, smid)
async def read_page_price(asin, smid=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _read_price_from_page, asin, smid)

# ============ فريم ملوّن حول السكرين شوت حسب التاج ============
# كل تاج له لون مميز ثابت
TAG_FRAME_COLORS = {
    "nonzz-21":        (231, 76, 60),    # أحمر
    "nooss-21":        (41, 128, 185),   # أزرق
    "kenzzz-21":       (39, 174, 96),    # أخضر
    "YouAy-21":        (155, 89, 182),   # بنفسجي
    "lowestlow-21":    (230, 126, 34),   # برتقالي
    "halola-21":       (26, 188, 156),   # فيروزي
    "wishitworkthe-21":(241, 196, 15),   # أصفر ذهبي
    "burmit-21":       (192, 57, 43),    # أحمر غامق (يوسف أيمن)
    "dndnamer-21":     (142, 68, 173),   # موف (Dina)
}
def _color_for_tag(tag):
    """لون التاج — من القاموس، وإلا لون ثابت متحسب من اسم التاج"""
    if not tag:
        return (120, 120, 120)   # رمادي للي مالوش تاج
    if tag in TAG_FRAME_COLORS:
        return TAG_FRAME_COLORS[tag]
    # لون ثابت من hash اسم التاج (عشان نفس التاج ياخد نفس اللون دايماً)
    import hashlib
    h = int(hashlib.md5(tag.encode()).hexdigest(), 16)
    palette = [(52,152,219),(46,204,113),(155,89,182),(230,126,34),
               (231,76,60),(26,188,156),(241,196,15),(52,73,94),(211,84,0)]
    return palette[h % len(palette)]
def add_frame(shot_bytes, tag, width=3):
    """يحط فريم ملوّن حول الصورة بلون التاج. يرجّع bytes."""
    if not shot_bytes:
        return shot_bytes
    try:
        import io as _io2
        from PIL import Image, ImageOps
        im = Image.open(_io2.BytesIO(shot_bytes)).convert("RGB")
        color = _color_for_tag(tag)
        framed = ImageOps.expand(im, border=width, fill=color)
        buf = _io2.BytesIO()
        framed.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:
        print(f"   ⚠️ الفريم فشل: {str(e)[:50]}")
        return shot_bytes
def _promo_sync(url):
    """يفتح صفحة البروموشن: ياخد سكرين شوت + يطلّع نص العرض والبراندات"""
    if not PLAYWRIGHT:
        return None, None, []
    for use_chrome in ((False,) if SERVER_MODE else (True, False)):
        pw = ctx = None
        try:
            pw = sync_playwright().start()
            kwargs = dict(viewport={"width": 1280, "height": 1400}, locale="ar-EG",
                          args=["--lang=ar-EG", "--no-sandbox", "--disable-dev-shm-usage"], ignore_default_args=["--disable-extensions"])
            if use_chrome:
                kwargs["channel"] = "chrome"; kwargs["headless"] = False
            else:
                kwargs["headless"] = True
            ctx = pw.chromium.launch_persistent_context(_PROFILE, **kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.context.add_cookies([{"name": "lc-acbeg", "value": "ar_EG",
                                       "domain": ".amazon.eg", "path": "/"}])
            target = url.replace("/-/en/", "/-/ar/")
            if "/-/ar/" not in target:
                target = target.replace("amazon.eg/", "amazon.eg/-/ar/")
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            try:
                page.mouse.wheel(0, 700); time.sleep(2)
                page.mouse.wheel(0, -700); time.sleep(1)
            except:
                pass
            # نص العرض من h1 (العنوان الثابت للصفحة)
            offer = None
            h1 = None
            try:
                h1 = page.query_selector("h1")
                if h1:
                    t = (h1.inner_text() or "").strip().rstrip(".")
                    t = re.sub(r'\s+', ' ', t)
                    if t and len(t) < 90:
                        offer = t
            except:
                pass
            # البراندات من الفلاتر الجانبية
            brands = []
            try:
                for el in page.query_selector_all('#brandsRefinements span, [id*="brand"] span')[:20]:
                    b = (el.inner_text() or "").strip()
                    if 1 < len(b) < 30 and b not in brands and "العلامات" not in b and "التجارية" not in b:
                        brands.append(b)
            except:
                pass
            # السكرين شوت: من وصف العرض (h1) لحد آخر أول صف منتجات
            clip = {"x": 0, "y": 0, "width": 1280, "height": 1400}
            try:
                # ننزل شوية عشان المنتجات تحمّل (lazy load)
                try:
                    page.mouse.wheel(0, 1200); time.sleep(2)
                    page.mouse.wheel(0, -1200); time.sleep(1)
                except:
                    pass
                top = 0
                if h1:
                    hb = h1.bounding_box()
                    if hb:
                        top = max(0, hb["y"] - 15)
                # كروت المنتجات — نجرّب كذا selector (أمازون بتغيّر الـ HTML)
                boxes = []
                selectors = [
                    '[data-asin]',
                    'div[data-csa-c-item-id]',
                    '[data-component-type="s-search-result"]',
                    'div.a-cardui [data-asin]',
                    'li[class*="ProductGrid"]',
                    'div[class*="productGrid"] > div',
                    'div[class*="gridItem"]',
                ]
                for sel in selectors:
                    try:
                        els = page.query_selector_all(sel)
                    except:
                        continue
                    for el in els[:16]:
                        b = el.bounding_box()
                        # كارت منتج معقول: عرض وارتفاع محترمين، وتحت العنوان
                        if b and b["height"] > 100 and b["width"] > 100 and b["y"] >= top - 5:
                            boxes.append(b)
                    if boxes:
                        print(f"      📐 لقيت {len(boxes)} كارت بـ selector: {sel[:30]}")
                        break
                if boxes:
                    first_y = min(b["y"] for b in boxes)
                    row = [b for b in boxes if abs(b["y"] - first_y) < 50]
                    bottom = max(b["y"] + b["height"] for b in row) + 12
                    print(f"      📐 أول صف: {len(row)} منتج")
                else:
                    print(f"      ⚠️ مالقيتش كروت منتجات — هاخد مساحة معقولة تحت العنوان")
                    bottom = top + 750
                h = max(200, min(1400, bottom - top))
                clip = {"x": 0, "y": top, "width": 1280, "height": h}
                print(f"      ✂️ القص: من y={top:.0f} لحد y={bottom:.0f} (ارتفاع {h:.0f})")
            except Exception as _e:
                print(f"      ⚠️ مظبطتش القص: {str(_e)[:60]}")
            shot = page.screenshot(clip=clip, type="jpeg", quality=85)
            return shot, offer, brands
        except Exception as e:
            if use_chrome:
                print("   ⚠️ Chrome فشل — بجرّب Chromium...")
            else:
                print(f"   ⚠️ مشكلة في صفحة العرض: {str(e)[:100]}")
        finally:
            try:
                if ctx: ctx.close()
                if pw: pw.stop()
            except:
                pass
    return None, None, []
async def get_promo_info(url):
    """سكرين شوت + نص العرض + البراندات من صفحة بروموشن"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _promo_sync, url)
async def get_page_screenshot(url):
    """سكرين شوت لصفحة كاملة (بحث/عرض) — أطول شوية عشان تبان النتايج"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _screenshot_sync, url, True, True)
# ============ منع التكرار (بالوقت — 8 ساعات) ============
SENT_TODAY_FILE = "forwarder_eg_sent_today.json"
DEDUP_HOURS = 8   # نفس المنتج مايتبعتش تاني على نفس القناة إلا بعد كام ساعة
def _now_ts():
    return time.time()
def load_sent_today():
    """يرجّع قاموس {مفتاح: وقت آخر إرسال} — بيشيل القديم (أكبر من DEDUP_HOURS)"""
    try:
        with open(SENT_TODAY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        sent = data.get("sent", {})
        # ندعم الصيغة القديمة (لو الملف من نسخة قديمة) — نتجاهلها
        if not isinstance(sent, dict):
            return {}
        cutoff = _now_ts() - DEDUP_HOURS * 3600
        # نشيل المفاتيح القديمة (عدّى عليها أكتر من 8 ساعات)
        return {k: v for k, v in sent.items() if v > cutoff}
    except:
        return {}
def save_sent_today(sent):
    try:
        with open(SENT_TODAY_FILE, "w", encoding="utf-8") as f:
            json.dump({"sent": sent}, f, ensure_ascii=False)
    except Exception as e:
        print(f"   ⚠️ مقدرتش أحفظ: {e}")

# ---- سجل بوتات الأسعار (السعر الثابت + نزول السعر) ----
# نشيك عليه عشان مانبعتش منتج اتبعت من خلالهم آخر 48 ساعة
PRICE_BOTS_SENT_FILE = "price_drop_sent.json"   # سجل price_stable_discount + price_drop
PRICE_BOTS_DEDUP_HOURS = 12
def load_price_bots_sent():
    """يرجّع set بالـ ASINs اللي بوتات الأسعار بعتها خلال آخر 48 ساعة"""
    try:
        with open(PRICE_BOTS_SENT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return set()
        cutoff = _now_ts() - PRICE_BOTS_DEDUP_HOURS * 3600
        return {k for k, v in data.items() if isinstance(v, (int, float)) and v > cutoff}
    except Exception:
        return set()      # الملف مش موجود → مفيش منع
def reserve_key(key, uname=None):
    """
    يحجز المفتاح ذرّياً: يقرأ من الديسك, يتأكد إن المنتج ماتبعتش خلال آخر 8 ساعات,
    يسجّل وقت دلوقتي ويكتب فوراً. يرجّع True لو الحجز نجح، False لو اتبعت قريّب.
    """
    global SENT_TODAY
    current = load_sent_today()      # أحدث نسخة من الديسك (شايلة القديم أصلاً)
    if key in current:               # موجود يعني اتبعت خلال آخر 8 ساعات
        SENT_TODAY = current
        return False
    current[key] = _now_ts()
    save_sent_today(current)
    SENT_TODAY = current
    return True
# قفل عشان الحجز يبقى ذرّي حتى لو بوستات كتير اشتغلوا في نفس الوقت
_RESERVE_LOCK = asyncio.Lock()
async def reserve_key_safe(key, uname=None):
    async with _RESERVE_LOCK:
        return reserve_key(key, uname)
def release_key(key):
    """يشيل الحجز من الديسك (لو البوست فشل أو اتلغى)"""
    global SENT_TODAY
    current = load_sent_today()
    current.pop(key, None)
    save_sent_today(current)
    SENT_TODAY = current
SENT_TODAY = load_sent_today()
# رسايل اتخطّت لأنها مفيهاش لينك — دي بس اللي نراجعها لو اتعدّلت
# بتتصفّر كل يوم جديد
NO_LINK_MSGS = set()
def _date_str():
    return datetime.now().strftime("%Y-%m-%d")
NO_LINK_DAY = _date_str()
def _reset_nolink_if_new_day():
    """يفضّي قايمة الرسايل من غير لينك لو اليوم اتغيّر"""
    global NO_LINK_MSGS, NO_LINK_DAY
    if NO_LINK_DAY != _date_str():
        NO_LINK_MSGS = set()
        NO_LINK_DAY = _date_str()
# ============ فك اللينكات + التاج ============
SHORT_DOMAINS = ("amzn.to", "amzn.eu", "a.co", "shorturl.at", "tinyurl.com",
                 "bit.ly", "cutt.ly", "link.amazon", "amzn.asia", "a.y-ay.com", "y-ay.com")
def extract_asin(url):
    import requests
    final = url
    if any(d in url for d in SHORT_DOMAINS):
        for attempt in (1, 2):        # محاولتين لو الشبكة اتلغبطت
            try:
                r = requests.get(url, allow_redirects=True, timeout=20,
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ar-EG"})
                final = r.url
                break
            except Exception as e:
                if attempt == 2:
                    print(f"   ⚠️ مقدرتش أفك اللينك: {str(e)[:70]}")
                    final = url
                else:
                    time.sleep(1)
    # الأنماط الأكيدة: /dp/ASIN أو /gp/product/ASIN
    m = re.search(r'/dp/([A-Z0-9]{10})', final) or re.search(r'/gp/product/([A-Z0-9]{10})', final)
    # حالة خاصة: لينك بحث بس كلمة البحث نفسها ASIN (s?k=B0XXXXXXXX)
    # نعامله كمنتج عشان نجيب بياناته ونفحص قسمه (مش بحث عام)
    if not m:
        km = re.search(r'[?&]k=(B0[A-Z0-9]{8})(?:&|$|%)', final)
        if km:
            m = km
    # بحث احتياطي عن B0xxx — بس لو مش صفحة قائمة (deals/بحث/قسم/عروض)
    # عشان مانلقطش ASIN من بارامترات زي promotionsSearchLastSeenAsin
    if not m:
        is_listing = any(x in final for x in ("/deals", "/s?", "/b/", "/fmc/",
                                              "/promotion/", "/l/", "SearchLastSeen",
                                              "node=", "refinementFilters",
                                              "redirectAsin", "/events/", "/deal/",
                                              "LastSeenAsin"))
        if not is_listing:
            m = re.search(r'\b(B0[A-Z0-9]{8})\b', final)
    asin = m.group(1) if m else None
    return asin, final
def retag(url, tag):
    asin, final = extract_asin(url)
    # علامة البازار: من اللينك المفكوك (قبل التنضيف)
    if re.search(r'[?&]s=bazaar\b', final):
        _IS_BAZAAR.append(1)
    if asin:
        _HAD_AMAZON.append(1)
        return f"https://www.amazon.eg/dp/{asin}?tag={tag}", asin
    if "amazon." in final:
        # لينك أمازون من غير ASIN (بروموشن/بحث/قسم) → ننضّفه ونسيب المهم بس
        base = final.split("?")[0]                    # المسار من غير بارامترات
        base = base.replace("/-/ar/", "/").replace("/-/en/", "/")  # نشيل بادئة اللغة
        # نحافظ على بارامترات البحث المهمة (كلمة البحث + الفلاتر + الترتيب)
        keep = []
        for p in ("k", "rh", "i", "s", "node", "bbn"):
            pm = re.search(rf'[?&]{p}=([^&\s]+)', final)
            if pm:
                keep.append(f"{p}={pm.group(1)}")
        _HAD_AMAZON.append(1)
        keep.append(f"tag={tag}")
        full = f"{base}?{'&'.join(keep)}"
        _LAST_FULL_URL.append(full)
        return full, None
    # اللينك مش أمازون (ما اتفكش لأمازون) → نشيله خالص
    return "", None
# اللينك بينتهي عند أول مسافة أو رمز مش من رموز اللينكات
# (بيحمي من الأقواس والرموز الغريبة في بوستات الماركداون)
LINK_RE = re.compile(r'https?://[^\s\]\)\[\(<>"\'\uFFFC]+')
def process_text(text, tag, keep_lines=False):
    _HAD_AMAZON.clear()
    _LAST_FULL_URL.clear()
    _IS_BAZAAR.clear()
    if not text:
        return text, None, []
    # استبدال الرموز: الدايرة الخضرا → صح، النار → انفجار
    text = text.replace("🟢", "✅").replace("🔥", "💥")
    # نشيل خطوط الفواصل ┄┄┄ (وأي أسطر فيها بس رموز خطوط)
    text = re.sub(r'[┄─━┅┈┉_]{3,}', '', text)
    # نشيل الأسطر الفاضية المتكررة اللي ممكن تنتج
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    found_asin = [None]
    all_asins = []          # كل الـ ASINs بترتيب ظهورها (من غير تكرار)
    def repl(mm):
        u = mm.group(0)
        if any(d in u for d in SHORT_DOMAINS) or "amazon." in u or "amzn" in u or "link.amazon" in u:
            new_url, asin = retag(u, tag)
            if asin:
                if not found_asin[0]:
                    found_asin[0] = asin
                if asin not in all_asins:
                    all_asins.append(asin)
            return new_url
        # أي لينك تاني (تليجرام، مواقع تانية...) → يتشال
        return ""
    new_text = LINK_RE.sub(repl, text)
    if not keep_lines:
        # تنضيف بعد شيل اللينكات: أسطر بقت فاضية أو فيها كلمات دلالية من غير لينك
        # أي سطر قصير بينتهي بنقطتين ومفيهوش لينك (بقى فاضي بعد شيل اللينك)
        # زي "نون :" أو "كوبونات خصم باقي المواقع :" أو "لينك الشراء :"
        new_text = re.sub(r'(?m)^[^\n]{0,40}[:：][^\S\n]*$', '', new_text)
        # سطور دلالية لوحدها من غير نقطتين
        new_text = re.sub(r'(?m)^[^\S\n]*(لينك الشراء|رابط المنتج|اضغط هنا|للشراء|كوبونات خصم[^\n]*)[^\S\n]*$', '', new_text)
    new_text = re.sub(r'\n{3,}', '\n\n', new_text).strip()
    return new_text, found_asin[0], all_asins
# ============ البوت ============
client = TelegramClient(
    os.getenv("TELEGRAM_SESSION_NAME", "forwarder_eg_session"), API_ID, API_HASH,
    connection_retries=None,      # يحاول يتصل لأجل غير مسمّى
    retry_delay=5,                # 5 ثواني بين المحاولات
    auto_reconnect=True,          # يعيد الاتصال تلقائياً
    request_retries=5,            # يعيد الطلبات الفاشلة
    timeout=30,
)
def _retag_all_links(text, new_tag):
    """يحط/يستبدل tag في كل لينك أمازون في النص (للنص الخام passthrough)"""
    def _fix(url):
        if "tag=" in url:
            return re.sub(r'([?&]tag=)[^&\s]+', r'\1' + new_tag, url)
        # مفيش tag → نضيفه
        sep = "&" if "?" in url else "?"
        return url + sep + "tag=" + new_tag
    # بس لينكات أمازون
    def _repl(m):
        u = m.group(0)
        if "amazon." in u or "amzn" in u or "link.amazon" in u:
            return _fix(u)
        return u
    return LINK_RE.sub(_repl, text)

async def _send_promo_post(client, uname, src_name, promo_text, first_promo_url, msg, n_promo=1):
    """يبعت بوست بروموشنز.
       - بروموشن واحد: عنوان (عرض على..) + سكرين شوت للصفحة
       - أكتر من واحد: النص الخام باللينكات مظبّطة + سكرين شوت لأول واحد
       لو مفيش سكرين شوت → يتخطّى (مايبعتش)."""
    # القنوات الوجهة (نفس منطق الهاندلر)
    if uname in SOURCE_TARGETS:
        channels = list(SOURCE_TARGETS[uname])
    else:
        channels = [HUNTER_CHANNEL]
        if uname in HALA_ALLOWED_SOURCES:
            channels.append(HALA_CHANNEL)
    # منع التكرار: بمعرّف الرسالة (البروموشنز مالهاش ASIN ثابت)
    global SENT_TODAY
    SENT_TODAY = load_sent_today()
    dedup_key = f"promopost:{src_name}:{msg.id}"
    if dedup_key in SENT_TODAY:
        print(f"   🔁 بوست البروموشنز ده اتبعت قبل كده — اتخطّى")
        return

    # سكرين شوت + (للبروموشن الواحد) العنوان من صفحة البروموشن
    promo_title = None
    if n_promo == 1:
        print(f"   📸 بحاول آخد سكرين شوت + عنوان البروموشن...")
        shot, promo_title, _brands = await get_promo_info(first_promo_url)
    else:
        print(f"   📸 بحاول آخد سكرين شوت لأول بروموشن...")
        shot = await get_page_screenshot(first_promo_url)

    if not shot:
        print("   ⛔ مفيش سكرين شوت للبروموشن — البوست اتخطّى خالص")
        return
    SENT_TODAY[dedup_key] = _now_ts()
    save_sent_today(SENT_TODAY)
    sent_ok = False
    for chan in channels:
        try:
            # نظبّط اللينكات بتاج القناة دي
            chan_tag = CHANNEL_TAG_OVERRIDE.get(chan, SOURCE_TAGS.get(uname))
            if n_promo == 1 and promo_title:
                # بروموشن واحد: "عرض على [العنوان]" + اللينك مظبّط
                retagged, _, _ = process_text(promo_text, chan_tag, keep_lines=True)
                links = LINK_RE.findall(retagged)
                link = links[0] if links else first_promo_url
                chan_text = f"⚡️ عرض على {promo_title}\n\n{link}"
            else:
                # أكتر من واحد: النص الخام باللينكات مظبّطة
                chan_text, _, _ = process_text(promo_text, chan_tag, keep_lines=True)
            if "amazon." not in chan_text:
                print(f"   ⛔ {chan}: مفيش لينك — اتخطّى")
                continue
            import io
            img = io.BytesIO(shot)
            img.name = "promo.jpg"
            await client.send_file(chan, img, caption=chan_text[:1024], force_document=False, parse_mode=None)
            print(f"   ✅ اتبعت بوست البروموشنز على {chan} (تاج {chan_tag})")
            sent_ok = True
        except Exception as e:
            print(f"   ❌ خطأ في الإرسال على {chan}: {e}")
    if not sent_ok:
        SENT_TODAY.pop(dedup_key, None)
        save_sent_today(SENT_TODAY)

async def handler(event):
    msg = event.message
    text = msg.message or ""
    # لو الرسالة على ملوك وفيها علامة "جاية من دينا" → نتخطّاها (متروحش هنتر)
    # (دينا بتبعت على ملوك، بس محتواها المفروض مايوصلش هنتر)
    try:
        _ch0 = await event.get_chat()
        _un0 = (getattr(_ch0, "username", "") or "").lower()
    except Exception:
        _un0 = ""
    if _un0 == "melook_content" and DINA_MARKER in text:
        print(f"\n📩 [@melook_content] بوست فيه علامة دينا — اتخطّى (متروحش هنتر)")
        return
    # نكتشف لو القناة محمية تلقائياً (Restrict saving content)
    is_women = False   # هل المنتج ملابس نسائية (بيتحدد بعد جلب البيانات)
    multi_items = []   # المنتجات المطابقة في البوستات المتعددة
    is_protected = bool(getattr(msg, "noforwards", False)) or \
                   bool(getattr(getattr(event, "chat", None), "noforwards", False))
    try:
        ch = await event.get_chat()
        uname = (getattr(ch, "username", "") or "").lower()
    except:
        uname = ""
    real_uname = uname   # الاسم الحقيقي (للعرض)
    # لو دي قناة بديلة → نعاملها بمنطق القناة الأصلية (نفس التاج والشروط والبناء)
    if ("@" + uname) in SUBST_TO_ORIGINAL:
        orig = SUBST_TO_ORIGINAL["@" + uname]
        uname = orig.lstrip("@").lower()
    tag = SOURCE_TAGS.get(uname)
    src_name = f"@{real_uname}" if real_uname else "?"
    # لو القناة مش نشطة في التشغيلة دي → نتجاهلها
    # (ملوك بقى وجهة بس — مش مصدر — فنتجاهل أي بوست جاي منه)
    if real_uname == "melook_content":
        return
    if real_uname and real_uname not in ACTIVE_SOURCES:
        return
    preview = (text[:60].replace("\n", " ") + "...") if text else "(مفيش نص)"
    pin_note = " 📌" if uname in PIN_SOURCES else ""
    print(f"\n📩 [{src_name}]{pin_note} بوست: {preview}")
    # قناة يوسف أيمن: نستنى دقيقتين قبل المعالجة عشان الأسعار والخصومات تتحدّث
    if uname == "yo_ayman":
        print(f"   ⏳ [{src_name}] بستنى دقيقتين عشان الأسعار تتحدّث...")
        await asyncio.sleep(120)
    # فحص نافذة الوقت (قنوات معينة بناخد منها بس في ساعات محددة بتوقيت مصر)
    if not _in_time_window(uname):
        win = TIME_WINDOW_SOURCES.get(uname)
        print(f"   ⏰ [{src_name}] برّه وقت الاستقبال ({win[0]}:00–{win[1]}:00 بتوقيت مصر) — مش هنبعته")
        return
    # فحص الكلمات الممنوعة في النص الأصلي (زي لانجري) → البوست يتلغى خالص
    _bw = has_blocked_word(text)
    if _bw:
        print(f"   🚫 فيه كلمة ممنوعة ('{_bw}') — مش هنبعته")
        return
    # القنوات المشروطة: لو البوست الأصلي مالوش صورة → اسكيب
    # (لو صاحب القناة مانزّلش صورة، غالباً الصورة مش كويسة)
    # ماعدا halaamaz — بتتبعت عادي بصورة أو من غيرها
    if uname in AMAZON_ONLY_SOURCES and uname != "halaamaz" and not getattr(msg, "photo", None):
        print(f"   ⏭️ [{src_name}] البوست الأصلي مالوش صورة — مش هنبني له بوست")
        return
    if not any(x in text for x in ["amazon.", "amzn", "link.amazon", "y-ay", "shorturl",
                                    "tinyurl", "bit.ly", "a.co", "cutt.ly"]):
        print("   ⏭️ مفيهوش لينك أمازون — اتخطّى")
        _reset_nolink_if_new_day()
        NO_LINK_MSGS.add((event.chat_id, msg.id))   # نراجعها لو اتعدّلت
        if len(NO_LINK_MSGS) > 500:                 # نحد الحجم
            NO_LINK_MSGS.pop()
        return
    if not tag and uname not in PASSTHROUGH_SOURCES:
        print(f"   ⚠️ القناة {src_name} مش معروف تاجها — اتخطّى")
        return
    # قناة النقل الكامل (passthrough): نفك اللينك للـ ASIN بس، والنص يفضل أصلي
    all_asins = []
    if uname in PASSTHROUGH_SOURCES:
        # نطلّع أول ASIN من البوست (للتكرار) من غير ما نغيّر النص
        asin = None
        for u in LINK_RE.findall(text):
            if any(d in u for d in SHORT_DOMAINS) or "amazon." in u or "amzn" in u or "link.amazon" in u:
                a, _ = extract_asin(u)
                if a:
                    asin = a
                    break
        new_text = text   # النص زي ما هو بالظبط
        _had_promo_with_asin = False
    else:
        print(f"   🔗 فيه لينك — بحط التاج {tag}...")
        new_text, asin, all_asins = process_text(text, tag)
        # ═══ أي بوست فيه ASIN(s) + لينك بروموشن → نطنّش البروموشن خالص ═══
        # (لينك أمازون /promotion/ أو /fmc/ أو /deal/ من غير ASIN = بروموشن)
        if all_asins:
            _before = new_text
            # نشيل سطور لينكات البروموشن (أمازون promotion/fmc/deal من غير dp)
            def _is_promo_link(u):
                return (("amazon." in u or "amzn" in u or "link.amazon" in u)
                        and not re.search(r'/dp/[A-Z0-9]{10}', u)
                        and re.search(r'/promotion/|/fmc/|/deal/|/l/|/events/|/b/', u))
            _kept_lines = []
            for _ln in new_text.split("\n"):
                _urls = LINK_RE.findall(_ln)
                if _urls and all(_is_promo_link(_u) for _u in _urls):
                    # السطر ده كله لينكات بروموشن → نشيله
                    continue
                # لو السطر فيه لينك بروموشن جوّه نص، نشيل اللينك بس
                for _u in _urls:
                    if _is_promo_link(_u):
                        _ln = _ln.replace(_u, "")
                _kept_lines.append(_ln)
            new_text = "\n".join(_kept_lines)
            new_text = re.sub(r'\n{3,}', '\n\n', new_text).strip()
            if new_text != _before:
                print(f"   🎟️ فيه ASIN + بروموشن — طنّشت البروموشن، بتعامل مع المنتجات بس")
                _had_promo_with_asin = True
            else:
                _had_promo_with_asin = False
        else:
            _had_promo_with_asin = False
        # ═══ كشف بوست "كله بروموشنز": أول لينك أمازون يفكّ لـ ASIN=None ═══
        # (زي link.amazon/B0gvWsk6N — بروموشن مش منتج). دلوقتي: بس لقناة elwyy
        is_all_promo = False
        first_promo_url = None
        n_promo = 0
        if uname == "elwyy" and not asin:   # بس elwyy + مفيش أول ASIN
            for u in LINK_RE.findall(text):
                if any(d in u for d in SHORT_DOMAINS) or "amazon." in u or "amzn" in u or "link.amazon" in u:
                    a, final_u = extract_asin(u)
                    if not a and ("amazon." in final_u or "link.amazon" in final_u or "amzn" in final_u):
                        # لينك بروموشن (من غير ASIN)
                        n_promo += 1
                        if not first_promo_url:
                            is_all_promo = True
                            first_promo_url = final_u
        # لينكات البازار (s=bazaar) → مش هنبعتها (حتى لو اللينك اتحوّل لـ dp نضيف)
        if _IS_BAZAAR or "s=bazaar" in new_text:
            print("   🚫 لينك بازار (s=bazaar) — مش هنبعته")
            return
        # بعد الفك: نتأكد إن فيه لينك أمازون فعلي (مش مجرد لينك مختصر لموقع تاني)
        if "amazon." not in new_text and not _HAD_AMAZON:
            print("   ⏭️ اللينكات مش أمازون بعد الفك — مش هنبعته")
            # نوضّح كل لينك اتفك لإيه (للتشخيص)
            for u in LINK_RE.findall(text)[:4]:
                if any(d in u for d in SHORT_DOMAINS) or "amazon" in u or "amzn" in u:
                    try:
                        _a, _f = extract_asin(u)
                        print(f"      🔍 {u[:45]} → {_f[:75]}")
                    except Exception as _e:
                        print(f"      🔍 {u[:45]} → فشل الفك: {str(_e)[:50]}")
            return
        # ═══ لو بوست بروموشنز ═══
        # بروموشن واحد → عنوان (عرض على..) + سكرين شوت. أكتر من واحد → النص الخام باللينكات.
        if is_all_promo and first_promo_url:
            if n_promo == 1:
                print(f"   🎟️ بروموشن واحد — هجيب عنوانه وأعمل 'عرض على'")
            else:
                print(f"   🎟️ بوست فيه {n_promo} بروموشنز — سكرين شوت لأول واحد + النص باللينكات")
            await _send_promo_post(client, uname, src_name, new_text, first_promo_url, msg, n_promo)
            return
    global SENT_TODAY
    # مفتاح منع التكرار الأساسي (base) — بنضيف عليه اسم القناة الهدف بعدين
    #  1) معرّف البروموشن أولاً (لو فيه promotion — بتتكرر كتير جداً)
    #  2) الـ ASIN
    #  3) رقم الرسالة (آخر حل)
    pm = re.search(r'/promotion/(?:psp/)?([A-Za-z0-9]+)', new_text)
    fmc = re.search(r'/fmc/[^?\s]*[?&]node=(\d+)', new_text) or re.search(r'/fmc/([a-z0-9\-]+)', new_text)
    if pm:
        dedup_base = f"promo:{pm.group(1)}"
    elif fmc:
        dedup_base = f"fmc:{fmc.group(1)}"
    elif asin:
        dedup_base = asin
    else:
        dedup_base = f"msg:{uname}:{msg.id}"
    # القنوات الهدف:
    #  - لو المصدر في SOURCE_TARGETS → قنواته المحددة بس (زي دينا → حلا + ملوك)
    #  - غير كده: EgyptOffersHunter (+ هالة لو في HALA_ALLOWED_SOURCES)
    if uname in SOURCE_TARGETS:
        channels = list(SOURCE_TARGETS[uname])
    else:
        channels = [HUNTER_CHANNEL]
        if uname in HALA_ALLOWED_SOURCES:
            channels.append(HALA_CHANNEL)
    # فحص: لو المنتج اتبعت من خلال بوتات الأسعار (السعر الثابت/نزول السعر) آخر 48 ساعة → نتخطّاه
    # فحص بوت الأسعار — نتخطّاه لملوك (مسار نقل مباشر لحلا، مش المفروض يخضع لمنع تكرار الأسعار)
    if asin and uname != "melook_content" and asin in load_price_bots_sent():
        print(f"   🔁 {asin} اتبعت من خلال بوت الأسعار خلال آخر {PRICE_BOTS_DEDUP_HOURS} ساعة — اتخطّى")
        return
    # منع التكرار على مستوى القناة — نحجز كل القنوات المتاحة دلوقتي (قبل البناء)
    # عشان مايحصلش تكرار لو نفس المنتج جه من قناتين في نفس الوقت (البناء بياخد وقت)
    # استثناء: لو الـ ASIN في الكاستم لينكس وجاي من elwyy → يتخطّى منع التكرار (ينتشر كل مرة)
    _skip_dedup = False
    try:
        if uname == "elwyy" and asin and asin.upper() in load_custom_links():
            _skip_dedup = True
    except Exception:
        pass
    reserved = []
    async with _RESERVE_LOCK:
        already = load_sent_today()
        for c in channels:
            k = f"{dedup_base}:{c}"
            if _skip_dedup or k not in already:
                already[k] = _now_ts()
                reserved.append((c, k))
        if reserved:
            save_sent_today(already)
            SENT_TODAY = already
    if not reserved:
        print(f"   🔁 {dedup_base} اتبعت خلال آخر {DEDUP_HOURS} ساعات — اتخطّى")
        return
    # القنوات اللي هنبعت عليها فعلاً = اللي حجزناها
    channels = [c for c, k in reserved]
    _reserved_keys = {c: k for c, k in reserved}
    def _release_all():
        for _c, _k in reserved:
            release_key(_k)
    # نبني البوست من بيانات أمازون
    # لو البوست فيه لينكات عروض (promotion) → نبني بوست عرض من الصفحة
    has_promo = "/promotion/" in new_text or "/fmc/" in new_text
    promo_shot = None
    built = False          # هل اتبنى بوست فعلاً (مش نقل نص)
    _reuse_shot = None     # سكرين شوت اتاخد وقت البناء (نعيد استخدامه)
    # القنوات المشروطة (أمازون بس): مابناخدش منها بروموشن خالص
    if has_promo and uname in AMAZON_ONLY_SOURCES:
        print(f"   ⏭️ [{src_name}] بوست بروموشن — مابناخدش بروموشن من القناة دي")
        _release_all()
        return
    if has_promo:
        promo_url = next((u for u in LINK_RE.findall(new_text)
                          if "/promotion/" in u or "/fmc/" in u), None)
        if promo_url:
            print(f"   🎁 بوست عرض — بجيب تفاصيله من الصفحة...")
            promo_shot, offer_txt, brands = await get_promo_info(promo_url)
            if offer_txt:
                import random as _r
                line = f"{_r.choice(OFFER_ICONS)} {_esc(offer_txt)}"
                if brands:
                    line += " على منتجات " + " و ".join(_esc(b) for b in brands[:4])
                new_text = f"{line}\n\n🔗 {promo_url}"
                built = True
                print(f"   ✏️ {offer_txt}" + (f" | براندات: {', '.join(brands[:4])}" if brands else ""))
            else:
                print(f"   ⚠️ مقدرتش أجيب نص العرض")
    elif (uname in BUILD_POST_SOURCES or _had_promo_with_asin) and len(all_asins) > 1:
        # بوست فيه كذا منتج → نجيب بياناتهم كلهم ونبنيهم في بوست واحد
        print(f"   📦 البوست فيه {len(all_asins)} منتجات — بجيب بياناتهم...")
        items = []
        min_disc = MIN_DISCOUNT.get(uname)
        max_price = MAX_PRICE.get(uname)
        for a in all_asins[:8]:          # حد أقصى 8 منتجات
            d = get_product(a)
            if not d:
                d = get_product(a)       # محاولة تانية لو فشلت
            if not d:
                print(f"      ⚠️ {a}: مقدرتش أجيب بياناته — اتخطّى")
                continue
            # كلمة ممنوعة في العنوان → نستبعد المنتج ده من البوست
            _bw = has_blocked_word(d.get('title'))
            if _bw:
                print(f"      🚫 {a}: عنوانه فيه كلمة ممنوعة ('{_bw}') — اتشال")
                continue
            # قسم ممنوع (تصنيف المنتج) → نستبعده من البوست
            _bc = has_blocked_category(d)
            if _bc:
                print(f"      🚫 {a}: تصنيفه قسم ممنوع ('{_bc}') — اتشال")
                continue
            # القنوات المشروطة: نستبعد اللي بائعه/شاحنه مش أمازون
            if uname in AMAZON_ONLY_SOURCES and not is_amazon_seller_shipper(d):
                print(f"      🚫 {a}: البائع/الشاحن مش أمازون — اتشال")
                continue
            if min_disc and d['disc'] < min_disc:
                print(f"      ⏭️ {a}: خصم {d['disc']:.0f}% أقل من {min_disc}%")
                continue                 # فلتر الخصم لكل منتج
            if max_price and d['cur'] > max_price:
                print(f"      ⏭️ {a}: السعر {d['cur']:,.0f} أغلى من {max_price:,}")
                continue                 # فلتر السعر الأقصى لكل منتج
            _bp = brand_price_block(d.get('title'), d['cur'])
            if _bp:
                print(f"      ⏭️ {a}: براند {_bp} والسعر {d['cur']:,.0f} — اتشال")
                continue                 # فلتر البراند المحدود السعر
            items.append((a, d))
            if is_women_clothing(d):
                is_women = True          # لو فيه منتج نسائي واحد → استثني Hala
        multi_items = items
        if items:
            new_text = build_multi_post(items, tag, uname)
            built = True
            print(f"   ✏️ اتبنى بوست بـ {len(items)} منتج (من {len(all_asins)})")
            if is_women and WOMEN_CLOTHING_EXCLUDE:
                print(f"   👗 فيه ملابس نسائية — مش هيتبعت على {', '.join(WOMEN_CLOTHING_EXCLUDE)}")
            # نحجز كل منتجات البوست المجمّع (لكل قناة) عشان مايتكرروش لوحدهم بعدين
            for _a, _ in items:
                if _a == dedup_base:
                    continue          # ده المفتاح الأساسي المحجوز أصلاً
                for _c in channels:
                    reserve_key(f"{_a}:{_c}")
        else:
            print(f"   ⏭️ مفيش منتج مطابق للشروط — مش هنبعته")
            _release_all()
            return
    elif (uname in BUILD_POST_SOURCES or _had_promo_with_asin) and asin:
        print(f"   📦 بجيب بيانات {asin} من أمازون...")
        data = get_product(asin)
        if data:
            # كلمة ممنوعة في عنوان المنتج → يتلغى
            _bw = has_blocked_word(data.get('title'))
            if _bw:
                print(f"   🚫 عنوان المنتج فيه كلمة ممنوعة ('{_bw}') — مش هنبعته")
                _release_all()
                return
            # قسم ممنوع (تصنيف المنتج) → يتلغى
            _bc = has_blocked_category(data)
            if _bc:
                print(f"   🚫 المنتج تصنيفه قسم ممنوع ('{_bc}') — مش هنبعته")
                _release_all()
                return
            # القنوات المشروطة: لازم البائع أمازون والشاحن أمازون
            if uname in AMAZON_ONLY_SOURCES and not is_amazon_seller_shipper(data):
                print(f"   🚫 البائع/الشاحن مش أمازون (البائع: {data.get('seller','?')}) — مش هنبعته")
                _release_all()
                return
            # فلتر الخصم (للقنوات اللي ليها حد أدنى)
            min_disc = MIN_DISCOUNT.get(uname)
            if min_disc and data['disc'] < min_disc:
                print(f"   ⏭️ الخصم {data['disc']:.0f}% أقل من {min_disc}% — مش هنبعته")
                _release_all()
                return
            # فلتر السعر الأقصى (للقنوات اللي ليها حد أعلى زي بلنوس)
            max_price = MAX_PRICE.get(uname)
            if max_price and data['cur'] > max_price:
                print(f"   ⏭️ السعر {data['cur']:,.0f} أغلى من {max_price:,} — مش هنبعته")
                _release_all()
                return
            # فلتر البراند المحدود السعر (زي ASTK: مايزيدش عن 600)
            _bp = brand_price_block(data.get('title'), data['cur'])
            if _bp:
                print(f"   ⏭️ براند {_bp} والسعر {data['cur']:,.0f} — مش هنبعته")
                _release_all()
                return
            # سكرين شوت المنتج الواحد + فريم وتاج حوالين السعر (زي التست بالظبط)
            _pre_shot, _page_price = await get_shot_with_frame(asin, data.get("seller_id"))
            # لو سعر الصفحة مختلف عن الـ API (الأحدث/الصح) نستخدمه في البوست
            if _page_price and data.get("cur"):
                _diff = abs(_page_price - data["cur"])
                if _diff >= 1 and _diff / data["cur"] > 0.005:
                    print(f"   🔄 سعر الصفحة {_page_price:,.0f} مختلف عن API {data['cur']:,.0f} — بستخدم سعر الصفحة")
                    data["cur"] = _page_price
            # نضيف الخصومات الإضافية اللي اتقرأت من الصفحة (وفر عند الدفع/كوبون/كمية)
            data["extra"] = _LAST_EXTRA_DISC.get(asin)
            new_text = build_post(asin, tag, data, uname)
            built = True
            # نمرّر السكرين شوت اللي اتاخد للاستخدام بعدين (مانعيدش أخذه)
            _reuse_shot = _pre_shot
            is_women = is_women_clothing(data)
            if is_women and WOMEN_CLOTHING_EXCLUDE:
                print(f"   👗 ملابس نسائية — مش هيتبعت على {', '.join(WOMEN_CLOTHING_EXCLUDE)}")
            print(f"   ✏️ اتبنى البوست: {data['title'][:40]}... | {data['cur']:,.0f} جنيه")
        else:
            print(f"   ⚠️ مقدرتش أجيب بيانات المنتج")
    elif uname in BUILD_POST_SOURCES and not asin:
        # القنوات المشروطة: مابناخدش منها بوستات بحث/أقسام خالص
        if uname in AMAZON_ONLY_SOURCES:
            print(f"   ⏭️ [{src_name}] بوست بحث/قسم — مابناخدش بحث من القناة دي")
            _release_all()
            return
        # لينك بحث/عرض (مفيهوش منتج واحد) → بوست عرض بكلمة البحث
        kw = _search_keyword(new_text)
        if kw:
            # كلمة ممنوعة في كلمة البحث → يتلغى
            _bw = has_blocked_word(kw)
            if _bw:
                print(f"   🚫 كلمة البحث فيها كلمة ممنوعة ('{_bw}') — مش هنبعته")
                _release_all()
                return
            new_text = build_offer_post(new_text, kw)
            built = True
            print(f"   🛍️ بوست عرض: {kw}")
    # القنوات اللي بتبني البوست: لو ما اتبناش بوست (لأي سبب) → مايتبعتش نص أصلي خالص
    if uname in BUILD_POST_SOURCES and not built:
        print(f"   ⏭️ [{src_name}] مقدرتش أبني بوست — مش هنقل النص الأصلي (اتلغى)")
        _release_all()
        return
    # لو القناة من ضمن اللي بتاخد سكرين شوت (زي Belnos) → ناخد سكرين شوت
    # منطق الصورة:
    # - قناة محمية → سكرين شوت دايماً (لأول لينك أمازون)، لأن صورة البوست ممنوعة
    # - قناة عادية → سكرين شوت لو لينك واحد، وصورة البوست لو أكتر
    shot = None
    if has_promo and promo_shot is not None:
        shot = promo_shot          # اتاخد وقت بناء البوست
    elif uname in SCREENSHOT_SOURCES and asin:
        if is_protected:
            # محمية: سكرين شوت لأول منتج مهما كان عدد اللينكات
            print(f"   📸 [{src_name}] (محمية) ASIN {asin} — بحاول آخد سكرين شوت...")
            shot = await get_clean_screenshot(asin, (data or {}).get("seller_id"))
        elif len(all_asins) > 1 and uname in BUILD_POST_SOURCES:
            # بوست فيه كذا منتج → كارت لكل منتج مدموجين في صورة واحدة
            # نمرّر (asin, smid البائع الأرخص) عشان الصورة تكون للبائع الأرخص الصح
            if multi_items:
                shot_pairs = [(a, (d or {}).get("seller_id")) for a, d in multi_items]
            else:
                shot_pairs = [(a, None) for a in all_asins[:5]]
            print(f"   📸 [{src_name}] بوست متعدد — بجيب {len(shot_pairs)} كارت...")
            shot = await get_multi_cards(shot_pairs)
        else:
            # بوست منتج واحد → سكرين شوت للمنتج (حتى لو النص الأصلي فيه كذا لينك)
            # لو أخدنا السكرين شوت بالفعل وقت بناء البوست، نعيد استخدامه (مانفتحش الصفحة تاني)
            if _reuse_shot is not None:
                shot = _reuse_shot
                print(f"   📸 [{src_name}] ASIN {asin} — بعيد استخدام السكرين شوت (اتاخد مرة واحدة)")
            else:
                print(f"   📸 [{src_name}] ASIN {asin} — بحاول آخد سكرين شوت...")
                shot = await get_clean_screenshot(asin, (data or {}).get("seller_id"))
    elif uname in SCREENSHOT_SOURCES and not asin:
        # لينك بحث/عرض → سكرين شوت للصفحة نفسها
        # نستخدم اللينك الكامل (قبل الاختصار) عشان السكرين شوت أسرع وأضمن
        page_url = next((u for u in LINK_RE.findall(new_text) if "amazon." in u), None)
        if page_url:
            print(f"   📸 [{src_name}] صفحة عرض — بحاول آخد سكرين شوت...")
            shot = await get_page_screenshot(page_url)
    sent_ok = False
    for chan in channels:
        chan_key = _reserved_keys[chan]     # اتحجز مسبقاً قبل البناء
        # ملابس نسائية مابتتبعتش على القنوات المستثناة
        if is_women and chan in WOMEN_CLOTHING_EXCLUDE:
            print(f"   ⏭️ {chan}: ملابس نسائية — اتخطّى")
            release_key(chan_key)
            continue
        sent_msg = None
        # لو القناة ليها تاج خاص، نستبدل التاج في اللينكات
        # للقنوات passthrough: عادةً بتفضل بتاجها الأصلي، إلا لو القناة الهدف
        # ليها CHANNEL_TAG_OVERRIDE صريح (زي HalaOffersEgypt=halola-21) — ساعتها نستبدل
        chan_tag = CHANNEL_TAG_OVERRIDE.get(chan)
        _is_pass = uname in PASSTHROUGH_SOURCES
        if chan_tag and chan_tag != tag and (not _is_pass or chan in CHANNEL_TAG_OVERRIDE):
            if _is_pass:
                # نص خام (passthrough): نحط/نستبدل tag في كل لينك أمازون
                chan_text = _retag_all_links(new_text, chan_tag)
            else:
                chan_text = re.sub(r'([?&]tag=)[^&\s]+', r'\1' + chan_tag, new_text)
        else:
            chan_text = new_text
        # لو ده محتوى دينا رايح ملوك → نحط العلامة المخفية عشان مايترجّعش لهنتر
        if uname == "dina_contents" and chan == MELOOK_CHANNEL:
            chan_text = DINA_MARKER + chan_text
        # HTML بس للبوستات اللي إحنا بنيناها (فيها <b>)، والنصوص المنقولة من غير parse
        pm = "html" if ("<b>" in chan_text or "<blockquote>" in chan_text) else None
        # ⛔ القنوات اللي بتبني بوست: لو مفيش سكرين شوت → نتخطّى خالص
        # (تحت أي ظرف مانبعتش صورة البوست الأصلي للقنوات دي)
        if uname in BUILD_POST_SOURCES and not shot:
            print(f"   ⛔ {chan}: مفيش سكرين شوت — البوست اتخطّى خالص (مش هنبعت صورة أصلية)")
            release_key(chan_key)
            continue
        try:
            if shot:
                # سكرين شوت (للقنوات زي Belnos) — بفريم ملوّن حسب تاج القناة
                import io
                effective_tag = chan_tag or tag
                framed_shot = add_frame(shot, effective_tag)
                img = io.BytesIO(framed_shot)
                img.name = "product.jpg"
                sent_msg = await client.send_file(chan, img, caption=chan_text[:1024], force_document=False, parse_mode=pm)
                print(f"   ✅ اتبعت بالسكرين شوت على {chan}" + (f" (تاج {chan_tag})" if chan_tag else ""))
            elif msg.media and not is_protected:
                # صورة البوست الأصلي (للقنوات غير المحمية)
                sent_msg = await client.send_file(chan, msg.media, caption=chan_text[:1024], parse_mode=pm)
                print(f"   ✅ اتبعت بصورة البوست الأصلي على {chan}" + (f" (تاج {chan_tag})" if chan_tag else ""))
            else:
                # نص بس (قناة محمية من غير سكرين شوت، أو بوست من غير ميديا)
                sent_msg = await client.send_message(chan, chan_text, parse_mode=pm)
                note = " (محمية — نص بس)" if (msg.media and is_protected) else ""
                print(f"   ✅ اتبعت نص بس على {chan}{note}" + (f" (تاج {chan_tag})" if chan_tag else ""))
            sent_ok = True
            # PIN للقنوات المحددة (مع إشعار، ومن غير ما نشيل القديم)
            if uname in PIN_SOURCES:
                if not sent_msg:
                    print(f"   ⚠️ PIN: مفيش رسالة مرجعة من {chan}")
                else:
                    try:
                        mid = getattr(sent_msg, "id", None)
                        await client.pin_message(chan, mid or sent_msg, notify=True)
                        print(f"   📌 اتعمله PIN على {chan}")
                    except Exception as pe:
                        print(f"   ⚠️ مقدرتش أعمل PIN على {chan}: {type(pe).__name__}: {str(pe)[:120]}")
            elif uname:
                pass   # القناة دي مش من قنوات الـ PIN
        except Exception as e:
            err = str(e)
            # لو الفشل بسبب حماية القناة (منع الفورورد) → نبعت نص بس
            if "ChatForwards" in type(e).__name__ or "forward" in err.lower() or "noforward" in err.lower():
                try:
                    sent_msg = await client.send_message(chan, chan_text, parse_mode=pm)
                    print(f"   ✅ اتبعت نص بس على {chan} (القناة محمية — fallback)")
                    sent_ok = True
                except Exception as e2:
                    print(f"   ❌ خطأ في الإرسال على {chan}: {e2}")
                    release_key(chan_key)
            # لو الفشل بسبب تنسيق النص → نعيد من غير parse_mode
            elif "parse" in err.lower() or "entit" in err.lower():
                try:
                    plain = re.sub(r'</?b>', '', chan_text)   # نشيل تاجات البولد
                    if shot:
                        import io
                        img2 = io.BytesIO(shot); img2.name = "product.jpg"
                        sent_msg = await client.send_file(chan, img2, caption=plain[:1024],
                                                          force_document=False, parse_mode=None)
                    elif msg.media and not is_protected:
                        sent_msg = await client.send_file(chan, msg.media, caption=plain[:1024], parse_mode=None)
                    else:
                        sent_msg = await client.send_message(chan, plain, parse_mode=None)
                    print(f"   ✅ اتبعت على {chan} (من غير تنسيق — fallback)")
                    sent_ok = True
                except Exception as e2:
                    print(f"   ❌ خطأ في الإرسال على {chan}: {e2}")
                    release_key(chan_key)
            else:
                print(f"   ❌ خطأ في الإرسال على {chan}: {e}")
                release_key(chan_key)
    # لو مفعّل والبوست راح Hunter → ننقل نفس البوست المبني والسكرين شوت لملوك وحلا
    # (مش النص وصورة المصدر الأصلي — البوست بتاعنا اللي راح Hunter)
    if MIRROR_HUNTER_TO_MELOOK and HUNTER_CHANNEL in channels:
        await _mirror_to_melook_hala(new_text, shot, msg)
SOURCE_CHANNELS = list(SOURCES.keys())
# القنوات النشطة في التشغيلة الحالية (بتتحدد بالسؤال أول التشغيل)
# افتراضياً كلها نشطة — لو المستخدم مااختارش، البوت يشتغل عادي على الكل
ACTIVE_SOURCES = {k.lstrip("@").lower() for k in SOURCES.keys()}
# قنوات بديلة اتدخلت وقت التشغيل (اسم القناة الأصلية → القناة البديلة + إعداداتها)
SUBSTITUTE_SOURCES = {}   # {"@NewChannel": {"tag":..., "screenshot":...}}
SUBST_TO_ORIGINAL = {}    # {"@NewChannel": "@OriginalChannel"} — نعامل البديلة بمنطق الأصلية

# أول 4 قنوات — دول اللي البوت يسأل عنهم السؤال الموسّع (انقل/بديلة/تجاهل)
FIRST_FOUR = ["@AmazonEgyptOffers", "@Belnos", "@EGFastAmzn", "@Yo_Ayman"]

def _ask_active_sources():
    """يسأل عن كل قناة مصدر. أول 4 قنوات: انقل منها / قناة بديلة / تجاهل.
       الباقي: Y/N عادي."""
    global ACTIVE_SOURCES, SUBSTITUTE_SOURCES, SUBST_TO_ORIGINAL
    print("\n" + "=" * 50)
    print("🎛️  اختاري القنوات اللي تحبي تشغّليها دلوقتي:")
    print("=" * 50)
    chosen = set()
    SUBSTITUTE_SOURCES = {}
    SUBST_TO_ORIGINAL = {}
    for ch in SOURCES.keys():
        if ch in FIRST_FOUR:
            # السؤال الموسّع للأربع قنوات الأولى
            print(f"\n   📡 {ch}:")
            print(f"      1) انقلي من {ch} زي ما هي")
            print(f"      2) دخّلي اسم قناة بديلة (تنقلي منها بدلها)")
            print(f"      3) ما تنقليش خالص")
            while True:
                ans = input(f"      اختاري 1 / 2 / 3 (Enter = 1): ").strip()
                if ans in ("", "1"):
                    chosen.add(ch.lstrip("@").lower())
                    break
                elif ans == "2":
                    newch = input(f"      اكتبي اسم القناة البديلة (بـ @ أو من غير): ").strip()
                    if not newch:
                        print("      ⚠️ مادخلتيش اسم — هنقل من الأصلية")
                        chosen.add(ch.lstrip("@").lower())
                        break
                    if "t.me/" in newch:
                        newch = "@" + newch.split("t.me/")[-1].strip("/")
                    elif not newch.startswith("@"):
                        newch = "@" + newch
                    # البديلة بتاخد نفس إعدادات القناة الأصلية (التاج والسكرين شوت)
                    orig_cfg = SOURCES[ch]
                    SUBSTITUTE_SOURCES[newch] = dict(orig_cfg)
                    SUBST_TO_ORIGINAL[newch] = ch   # نعاملها بمنطق الأصلية
                    chosen.add(newch.lstrip("@").lower())
                    print(f"      ✅ هنقل من {newch} بدل {ch} (بنفس إعداداتها)")
                    break
                elif ans == "3":
                    print(f"      ⏭️ هتجاهل {ch}")
                    break
                else:
                    print("      اكتبي 1 أو 2 أو 3")
        else:
            # الباقي: Y/N عادي
            while True:
                ans = input(f"   {ch} ؟ (Y/N، Enter=Y): ").strip().lower()
                if ans in ("", "y", "yes", "آه", "ا", "نعم"):
                    chosen.add(ch.lstrip("@").lower())
                    break
                elif ans in ("n", "no", "لا", "لأ"):
                    break
                else:
                    print("      اكتبي Y أو N")
    if not chosen:
        print("   ⚠️ مااخترتيش أي قناة — هشغّل الكل احتياطياً")
        chosen = {k.lstrip("@").lower() for k in SOURCES.keys()}
    ACTIVE_SOURCES = chosen
    print(f"\n   ✅ هشتغل على {len(chosen)} قناة: {', '.join(sorted(chosen))}")
    if SUBSTITUTE_SOURCES:
        print(f"   🔀 قنوات بديلة: {', '.join(SUBSTITUTE_SOURCES.keys())}")
    # سؤال: أي بوست يتنشر على Hunter يتنقل منه لملوك؟
    global MIRROR_HUNTER_TO_MELOOK
    print()
    ans = input(f"📢 أي بوست يتنشر على {HUNTER_CHANNEL} يتنقل لـ {MELOOK_CHANNEL} "
                f"(نقل مباشر بتاج burnit-21)؟ (Y/N، Enter=Y): ").strip().lower()
    MIRROR_HUNTER_TO_MELOOK = ans not in ("n", "no", "لا", "لأ")
    if MIRROR_HUNTER_TO_MELOOK:
        print(f"   ✅ هتابع {HUNTER_CHANNEL} وأنقل أي بوست عليه لـ {MELOOK_CHANNEL} (تاج burnit-21)")
    else:
        print(f"   ⏭️ مش هننقل من Hunter لـ {MELOOK_CHANNEL}")
    print()

def _print_banner():
    print("=" * 50)
    print("🤖 بوت نقل العروض — مصر")
    print("=" * 50)
    for k, v in SOURCES.items():
        ss = " (سكرين شوت)" if v["screenshot"] else " (صورة البوست)"
        print(f"   {k} → تاج {v['tag']}{ss}")
    print(f"   بيبعت على: {', '.join(MY_CHANNELS)}")
    print(f"   سكرين شوت لـ: {', '.join('@'+s for s in SCREENSHOT_SOURCES)} ({'شغّال' if PLAYWRIGHT else 'مش متاح'})")
    print("=" * 50)
    print("(أول مرة هيطلب رقمك + كود من تليجرام)\n")
# نسمع البوستات الجديدة + التعديلات
# (لو بوست اتنشر من غير لينك وبعدين اتعدّل واتحط فيه لينك، نمسكه)
# منع التكرار بالـ ASIN بيحمي من الازدواج
async def edit_handler(event):
    """يشتغل بس لو البوست كان مفيهوش لينك واتعدّل واتحط فيه لينك"""
    _reset_nolink_if_new_day()
    key = (event.chat_id, event.message.id)
    if key not in NO_LINK_MSGS:
        return   # البوست كان فيه لينك من الأول → مش محتاجين نراجعه
    NO_LINK_MSGS.discard(key)
    print("   ✏️ بوست كان من غير لينك واتعدّل — براجعه")
    await handler(event)
# ملاحظة: تسجيل الـ handlers اتنقل لـ _register_source_handlers (بعد سؤال القنوات)
# loop يعيد الاتصال تلقائياً لو انقطع (بدل ما البوت يقف ويحتاج restart يدوي)
# ملحوظة: الحلقة دي تحت __main__ عشان لو ملف تاني استورد البوت (زي متتبّع الأسعار)
#         مايشغّلش البوت نفسه — بس ياخد الدوال والإعدادات
def _register_source_handlers():
    """يسجّل الـ handler على القنوات المصدر + أي قنوات بديلة اتدخلت"""
    # القنوات البديلة اللي المستخدمة دخّلتها — نضيفها لإعدادات المصادر
    for newch, cfg in SUBSTITUTE_SOURCES.items():
        key = newch.lstrip("@").lower()
        SOURCE_TAGS[key] = cfg["tag"]
        if cfg.get("screenshot"):
            SCREENSHOT_SOURCES.add(key)
        # نورّث كل الخصائص المشروطة من القناة الأصلية للبديلة
        # (البديلة بتشتغل بنفس منطق القناة اللي حلّت محلها)
    # كل القنوات اللي هنسمعها: الأصلية النشطة + البديلة
    all_chats = list(SOURCES.keys()) + list(SUBSTITUTE_SOURCES.keys())
    client.add_event_handler(handler, events.NewMessage(chats=all_chats))
    client.add_event_handler(edit_handler, events.MessageEdited(chats=all_chats))
    if MIRROR_HUNTER_TO_MELOOK:
        print(f"   📢 أي بوست يروح {HUNTER_CHANNEL} هينقل مباشرة لـ {MELOOK_CHANNEL} + {HALA_CHANNEL} (تاج burnit-21)")

async def _mirror_to_melook_hala(built_text, shot, msg):
    """ينقل نفس البوست المبني والسكرين شوت اللي راح Hunter لقناة واحدة بالقرعة
       (ملوك أو حلا) — مفيش تكرار، وكل قناة ليها شكل الإيموجي بتاعها."""
    if not built_text:
        return
    global SENT_TODAY
    SENT_TODAY = load_sent_today()
    dedup_key = f"hunter2melook:{msg.id}"
    if dedup_key in SENT_TODAY:
        return
    # نختار قناة واحدة بالقرعة العشوائية
    import random
    chan, chan_tag = random.choice([
        (MELOOK_CHANNEL, CHANNEL_TAG_OVERRIDE.get(MELOOK_CHANNEL, "burnit-21")),
        (HALA_CHANNEL, CHANNEL_TAG_OVERRIDE.get(HALA_CHANNEL, "burnit-21")),
    ])
    print(f"\n📢 [Hunter→{chan}] بوست: {built_text[:45]}...")
    try:
        # نفس البوست المبني بالتاج بتاع القناة + شكل الإيموجي بتاعها
        chan_text = _retag_all_links(built_text, chan_tag)
        chan_text = _restyle_for_channel(chan_text, chan)
        pm = "html" if ("<b>" in chan_text or "<blockquote>" in chan_text) else None
        if shot:
            import io
            framed = add_frame(shot, chan_tag)
            img = io.BytesIO(framed)
            img.name = "product.jpg"
            await client.send_file(chan, img, caption=chan_text[:1024], force_document=False, parse_mode=pm)
        else:
            await client.send_message(chan, chan_text, parse_mode=pm)
        print(f"   ✅ اتنقل لـ {chan} (تاج {chan_tag})")
        SENT_TODAY[dedup_key] = _now_ts()
        save_sent_today(SENT_TODAY)
    except Exception as e:
        print(f"   ❌ خطأ في النقل لـ {chan}: {e}")

def _restyle_for_channel(text, chan):
    """يدّي كل قناة هويتها الثابتة (إيموجي العنوان + السعر + الخصم):
       ملوك: 💰 عنوان / 💵 سعر / 🔥 خصم
       حلا:  💝 عنوان / 🌸 سعر / ✨ خصم"""
    import re
    if chan == MELOOK_CHANNEL:
        title_e, price_e, disc_e = "💰", "💵", "🔥"
    elif chan == HALA_CHANNEL:
        title_e, price_e, disc_e = "💝", "🌸", "✨"
    else:
        return text
    lines = text.split("\n")
    out = []
    # كل الإيموجيهات المحتملة للسعر والخصم (عشان نستبدلها)
    price_icons = ["💵","💰","🏷️","💳","🪙","💸","🧾","💲","🤑","🛒","🛍️","📌"]
    disc_icons = ["💥","🔥","⚡","🎯","📉","🚨","🎉","✂️","🏷️","🔻","⬇️","🎊"]
    for ln in lines:
        s = ln
        # 1) سطر العنوان: 👑 عرض على / 👑 العروض الملكية → إيموجي القناة
        if "عرض على" in s and s.strip().startswith("👑"):
            s = s.replace("👑", title_e, 1)
        elif "العروض الملكيه" in s or "العروض الملكية" in s:
            s = s.replace("👑", title_e, 1)
        # 2) سطر "خصم X%" لوحده (بادج فوق العنوان) زي: 💥 خصم 21% 💥
        elif re.match(r'^\s*\S+\s*خصم\s+\d+%\s*\S*\s*$', s) and "بدلاً" not in s:
            mm = re.search(r'خصم\s+\d+%', s)
            if mm:
                s = f"{disc_e} {mm.group(0)} {disc_e}"
        # 3) سطر السعر: أي إيموجي + "السعر" → إيموجي سعر القناة
        elif "السعر" in s:
            m = re.match(r'^(\s*<blockquote>)?\s*(\S+)\s*(السعر.*)', s)
            if m:
                pre = m.group(1) or ""
                rest = m.group(3)
                s = f"{pre}{price_e} {rest}"
        # 4) سطر الخصم بالتفصيل: أي إيموجي + "خصم ... بدلاً" → إيموجي خصم القناة
        elif "خصم" in s and ("بدلاً" in s or "%" in s):
            m = re.match(r'^\s*(\S+)\s*(خصم.*)', s)
            if m:
                s = f"{disc_e} {m.group(2)}"
        out.append(s)
    return "\n".join(out)

def _run_forever():
    import time as _t
    _print_banner()
    if SERVER_MODE:
        # Railway has no interactive stdin. Configure sources from environment variables.
        global ACTIVE_SOURCES, MIRROR_HUNTER_TO_MELOOK
        raw_sources = os.getenv("ACTIVE_SOURCES", "all").strip()
        if raw_sources.lower() in ("", "all", "*"):
            ACTIVE_SOURCES = {k.lstrip("@").lower() for k in SOURCES.keys()}
        else:
            ACTIVE_SOURCES = {x.strip().lstrip("@").lower() for x in raw_sources.split(",") if x.strip()}
        MIRROR_HUNTER_TO_MELOOK = _env_bool("MIRROR_HUNTER_TO_MELOOK", False)
        print(f"☁️ Railway active sources: {', '.join(sorted(ACTIVE_SOURCES))}")
        print(f"☁️ Mirror Hunter → Melook/Hala: {MIRROR_HUNTER_TO_MELOOK}")
        if not API_ID or not API_HASH:
            raise RuntimeError("Missing TELEGRAM_API_ID / TELEGRAM_API_HASH")
        if not CLIENT_ID or not CLIENT_SECRET:
            raise RuntimeError("Missing AMAZON_CLIENT_ID / AMAZON_CLIENT_SECRET")
        if not os.path.exists(os.getenv("TELEGRAM_SESSION_NAME", "forwarder_eg_session") + ".session"):
            raise RuntimeError("Missing Telegram session. Set TELEGRAM_SESSION_B64 in Railway Variables.")
    else:
        _ask_active_sources()          # local interactive mode
    _register_source_handlers()    # نسجّل الـ handler بعد ما نعرف القنوات (والبديلة)
    while True:
        try:
            client.start()
            print("✅ البوت متصل وشغّال...")
            client.run_until_disconnected()
            print("⚠️ الاتصال اتقطع — بعيد الاتصال بعد 10 ثواني...")
            _t.sleep(10)
        except KeyboardInterrupt:
            print("\n👋 اتوقف بأمر منك")
            break
        except Exception as e:
            print(f"⚠️ خطأ: {str(e)[:100]} — بعيد المحاولة بعد 15 ثانية...")
            _t.sleep(15)
if __name__ == "__main__":
    _run_forever()
