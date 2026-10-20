# نقطة توقف — YOUSEF AI

> اقرأ هذا الملف أولًا في أي محادثة جديدة، وأكمل من "الخطوة التالية" ولا تبدأ من الصفر.
> قاعدة العمل: بعد كل خطوة عمل يُحدَّث هذا الملف (نسخة في المشروع `claude/yousef-ai-checkpoint.md` ونسخة داخل الكود `CHECKPOINT.md`) ويُرفع commit على GitHub.

آخر تحديث: 2026-10-20 — اكتملت 7.1 (محلل التداول) و7.2 (التحكم بالجهاز)؛ التالي 7.3 الوصول من الجوال

## الحالة الحالية
- المستودع: https://github.com/aswar0yousef-design/YOUSEF-AI (الفرع main). كل خطوة مكتملة = commit.
- آخر إصدار مُسلَّم: 0.7.0 (`yousef-ai-v0.7.0.zip` + `yousef-ai-vscode-0.7.0.vsix`).
- المرحلة 5 (العروض والباقات والمدير التقني) مكتملة ومُسلَّمة كإصدار 0.5.0.
- المرحلة 6 مكتملة: ناشر السوشيال (0.6.0)، ثم مدير الإعلانات وصانع الصور (0.7.0). التالي المرحلة 7 (انظر خطتها و«الخطوة التالية»).
- LayanX يبقى على حاله كمرجع فقط.

## المرحلة 6 — الخطة (ناشر السوشيال أولًا)
- [x] 6.1 موصل Meta الرسمي (Graph API v26.0): ربط صفحات فيسبوك وحسابات إنستغرام التجارية المرتبطة بها برمز واحد؛ رموز الصفحات في keys.dat؛ قراءة آخر المنشورات والمجدولة؛ النشر (نص، رابط، صورة، عدة صور) والجدولة الأصلية في فيسبوك؛ النشر في إنستغرام (صورة أو ألبوم حتى 10) مع فحص الحد اليومي؛ لوحة «حسابات السوشيال» في الإعدادات.
- [x] 6.2 وكيل «ناشر السوشيال»: مكتبة صور محلية (رفع من اللوحة)، أدوات الحسابات والمنشورات والصور، قراءة منتجات المتاجر وصورها للقراءة فقط، `propose_post` لكل منصة بنص مناسب، وجدولة إنستغرام محليًا (جدول `social_posts` ومجدول يعمل ما دام يوسف شغّالًا، والمتأخر أكثر من ساعتين يُعلَّم «فات موعده» ولا يُنشر تلقائيًا)، والنشر بعد الموافقة فقط.
- [x] 6.3 الواجهة: بطاقة معاينة المنشور (الحساب، المنصات، النص، الصور، الموعد)، لوحة المنشورات المجدولة مع الإلغاء و«انشر الآن» للفائت، إرفاق صور من خانة الطلب، وVS Code؛ فحص بالمتصفح.
- [x] 6.4 تحقق شامل (178 اختبارًا، فحوص المتصفح، VS Code، الانحدار) وتغليف 0.6.0 وREADME.
- [x] 6.5 «مدير الإعلانات» (Meta Marketing API): قراءة الحملات وأدائها أولًا (الإنفاق، الوصول، النقرات، التكلفة لكل نتيجة، ROAS) وتحليلها مع مبيعات المتجر؛ ثم اقتراح إيقاف/تشغيل/تعديل الميزانية بموافقة، بلا إنشاء إعلانات مدفوعة دون موافقة صريحة لكل إعلان.
- [x] 6.6 صانع الصور: بمفتاح OpenAI أو Gemini (توليد أو تعديل صورة المنتج)، تُحفظ في مكتبة الصور، ونشرها يمر بالموافقة كأي منشور.

## قرارات المرحلة 6
- Meta فقط في البداية (فيسبوك + إنستغرام عبر Facebook Login، المضيف graph.facebook.com، الإصدار v26.0 الصادر 29 يوليو 2026). X وTikTok وLinkedIn تحتاج اشتراكًا مدفوعًا أو مراجعة شراكة، فتؤجَّل.
- الربط: المستخدم ينشئ تطبيق Meta خاصًا به (وضع التطوير يكفي لصفحاته)، ويولّد رمز مستخدم من Graph API Explorer بصلاحيات pages_show_list وpages_read_engagement وpages_manage_posts وinstagram_basic وinstagram_content_publish وbusiness_management، ويمدّده من Access Token Debugger؛ يوسف يجلب الصفحات ورموزها (لا تنتهي عند اشتقاقها من رمز طويل) ويحفظها مشفّرة.
- فيسبوك: `/{page}/feed` و`/{page}/photos` (عدة صور: صور غير منشورة ثم attached_media)؛ الجدولة الأصلية من 10 دقائق إلى 30 يومًا.
- إنستغرام: حاوية `/{ig}/media` ثم `/{ig}/media_publish`؛ JPEG فقط من رابط عام؛ حتى 10 صور في الألبوم؛ النص حتى 2200 حرف و30 وسمًا و20 إشارة؛ حد 100 منشور في 24 ساعة (`content_publishing_limit`). الصور المحلية أو غير JPEG تُرفع أولًا كصورة غير منشورة في الصفحة ويُستخدم رابطها من خوادم فيسبوك، ثم تُحذف بعد النشر. لا جدولة في واجهة إنستغرام، فالجدولة محلية.

## طلب المستخدم (20 أكتوبر 12:15): «اكمل المراحل المتبقية حتى النهاية»
الترتيب المتفق عليه (أُبلغ به المستخدم): 6.5 مدير الإعلانات ← 6.6 صنع الصور (0.7.0) ← 7.1 محلل التداول ← 7.2 التحكم بالجهاز ← 7.3 الوصول من الجوال ← 7.4 الاستماع من النظام بدون متصفح ← 1.0.0.

## تفاصيل 6.5 (ما بُني)
- `integrations/meta_ads.py`: Marketing API (نفس Graph v26.0): `connect_ads` (`/me/adaccounts`)، `AdAccount` (campaigns مع insights بمستوى الحملة لفترة 1/7/14/30/90 يومًا، adsets، daily بـ time_increment=1، current، set_status ACTIVE/PAUSED، set_daily_budget)، `summarize` (omni_purchase أولًا مثل مدير الإعلانات، ROAS، تكلفة الشراء)، وحدات العملة الصغرى (100، و1 للعملات بلا كسور). `AdAccounts` (الإعداد `ads`، الرمز في keys.dat باسم `meta_ads`)؛ ربط صفحات السوشيال برمز مستخدم يربط الحسابات الإعلانية تلقائيًا إن كان للرمز صلاحية ads_read.
- `integrations/ads_tools.py`: `AdsToolbox` (list_ad_accounts، campaigns، adsets، daily، propose_pause، propose_resume، propose_budget مع تحذير الزيادة >20% (مرحلة التعلّم) والخفض >50% والأقل من 1، وفرق الشهر، ورفض الميزانية الكلية أو المستوى الخطأ)؛ لا إنشاء حملات أو إعلانات.
- وكيل `ads_manager` (phase 6، يحتاج "ads"، وبدونه ← analyst) + `core/ads_manager.py` (مع StoreReader لمقارنة إيرادات Meta بمبيعات المتجر)؛ المنسّق: `ADS_NOTE`، `ADS_HINT`، `_run_ads_step`، `_run_ads_actions` (فحص تعارض الحالة/الميزانية قبل التنفيذ). `_settle` صار عامًا بمجموعات `EXTERNAL` (store_، social_، ads_، trade_، device_) ورسائل كل مجموعة.
- الخادم: `GET/POST /api/ads`، `DELETE /api/ads/<act_id>`؛ اللوحة: قائمة الحسابات الإعلانية في «حسابات السوشيال»، بطاقات الإعلانات، أسماء الأدوات؛ VS Code كذلك.
- `tests/test_ads.py` (7) مع إضافات الإعلانات في `meta_fake.py`. 197 اختبارًا ناجحة؛ فحص بالمتصفح.

## تفاصيل 7.2 (ما بُني)
- `device/control.py`: `Device(settings, backend, home, workspaces)`: `apps()` (11 افتراضية + ما يضيفه المستخدم في `settings.device.apps`، الاسم `[a-z0-9_-]` والمسار `.exe` أو `C:\...\x.exe|.lnk` أو `ms-settings:` فقط، بلا وسائط)، `app(ref)` بالاسم أو الاسم الظاهر، `folder()` (desktop/documents/downloads/pictures/music/videos/home أو أسماؤها العربية أو مسار كامل) و`document()` (امتدادات مستندات/صور/وسائط فقط، لا .exe/.bat/.ps1) داخل المجلد الشخصي أو مجلدات المشاريع، ورفض أي مسار فيه `.ssh`/`.gnupg`/`.aws`/`.azure`/`.kube`/`.docker`/`AppData`؛ `execute(kind, payload)` يعيد التحقق من كل شيء قبل التنفيذ. الأنواع: device_open_app، device_open_url (http/https فقط)، device_open_folder، device_open_file، device_volume (up/down/mute، حتى 25 ضغطة ≈ 2% لكل ضغطة)، device_media (play_pause/next/previous/stop)، device_close_app (taskkill بلا /F، وليس explorer.exe)، device_lock (LockWorkStation).
- `WindowsBackend`: `os.startfile` (ShellExecute بلا shell)، `keybd_event` لمفاتيح الصوت والوسائط، `LockWorkStation`، `taskkill /IM`، `tasklist /FO CSV` للبرامج المفتوحة، ومعلومات النظام عبر ctypes (GlobalMemoryStatusEx، GetSystemPowerStatus، GetLogicalDrives/GetDriveTypeW + disk_usage، GetTickCount64). `GenericBackend` للينكس/ماك (فتح عبر xdg-open فقط). `build_app(device_backend=..., device_home=...)` للاختبار.
- `integrations/device_tools.py`: `DeviceToolbox` (system_info، running_apps، list_apps بلا موافقة؛ propose_open_app/url/folder/file، propose_volume، propose_media، propose_close_app، propose_lock)؛ حد 6 اقتراحات، ومنع التكرار.
- وكيل `device_control` متاح (phase 7، chat، `DEVICE_TOOLS`)؛ `core/device_control.py` (حتى 8 أدوات)؛ المنسّق: `DEVICE_NOTE` للمخطِّط، `DEVICE_HINT` («افتح»، «الصوت»، «اقفل»، «البطارية»…) يمرّر الطلبات القصيرة للمخطِّط، `_run_device_step`، و`_run_device_actions` الحقيقي. لم يعد أي وكيل «في المرحلة القادمة».
- الخادم: `GET /api/device` (المعلومات والبرامج ونوع الواجهة الخلفية)، `POST /api/device/apps`، `DELETE /api/device/apps/<name>` (المضافة فقط). اللوحة: قسم «التحكم بالجهاز» (الذاكرة والأقراص والبطارية، شرائح البرامج المسموحة مع إزالة المضاف، نموذج «أضف برنامجًا»)، بطاقات الإجراءات، بوابة «لن يحدث شيء على جهازك قبل موافقتك»؛ VS Code كذلك.
- `tests/test_device.py` (4). المجموع 219 اختبارًا ناجحة؛ فحص بالمتصفح.

## تفاصيل 7.1 (ما بُني)
- مصادر الأسعار (تحقق 20 أكتوبر): Binance للعملات الرقمية عبر `https://data-api.binance.vision/api/v3` (بيانات عامة فقط، بلا مفتاح: klines، ticker/24hr). Stooq صار يتطلب مفتاحًا منذ مارس 2026 فاستُبعد؛ الأسهم الأمريكية والفوركس والذهب عبر Twelve Data (`https://api.twelvedata.com`، خطة مجانية 8 طلبات/دقيقة و800/يوم، الأسهم الأمريكية فقط؛ المفتاح في ترويسة `Authorization: apikey <key>` لا في الرابط؛ الأخطاء تأتي أحيانًا بـ HTTP 200 و`status: error`). المفتاح في keys.dat باسم `twelvedata` (أُضيف إلى `SERVICE_KEYS`، ويظهر في «الإعدادات ← الخدمات» بعنوان «مفتاح API»).
- `trading/market.py`: `resolve()` يفهم «btc»، «بيتكوين»، «BTC/USD» (← BTCUSDT)، «ETHBTC»، «aapl»، «EURUSD»، «الذهب» (← XAU/USD) مع `market` اختياري؛ `Instrument(ref, kind, source, display, quote)` و`usd`. `Market.candles(symbol, interval 15m/1h/4h/1d/1w, limit ≤500)` و`quote()` مع تخزين مؤقت 60 ثانية (`fresh=True` لتجاوزه عند التنفيذ)، ورسائل عربية للأخطاء (رمز غير موجود، مفتاح خاطئ، نفاد الرصيد، خارج الخطة المجانية).
- `trading/indicators.py`: SMA، EMA (بذرة SMA)، RSI ويلدر، MACD(12,26,9)، بولينجر(20,2)، ATR ويلدر، قمم/قيعان متأرجحة مجمّعة كدعم/مقاومة، و`analyze()` (الاتجاه من SMA50 وميله، التقاطع الذهبي/المميت، تقاطع MACD، موقع السعر في بولينجر، ATR%، نسبة الحجم، التغير 1/7/30 شمعة، آخر 10 إغلاقات)؛ `fmt_price`، `fmt_pct`.
- `trading/paper.py`: `PaperBook` في `trading.db` منفصلة (account برصيد افتراضي 10,000$، positions بتكلفة شاملة الرسوم، trades مع الربح المحقق، alerts: price_above/below وrsi_above/below، الحالة active/triggered/cancelled، seen). الشراء بمبلغ بالدولار شامل الرسوم، البيع بكمية أو الكل، `valuation(prices)`، `reset(cash)` (التنبيهات تبقى)، حد 50 تنبيهًا فعّالًا، المحفظة بالدولار فقط (USD/USDT/USDC/FDUSD). `describe_alert`، `alert_value`.
- `trading/alerts.py`: `AlertWatcher.check()` (سعر واحد لكل رمز وRSI واحد لكل رمز/إطار في كل جولة؛ يُطلق التنبيه مرة واحدة؛ `listeners`) وخيط كل 60 ثانية يبدأ من `__main__`.
- `integrations/trade_tools.py`: `TradeToolbox` (price، analyze، compare حتى 6، portfolio، list_alerts، propose_trade، propose_alert، propose_cancel_alert)؛ تحقق: وقف الخسارة تحت السعر وجني الأرباح فوقه للشراء، النقد يكفي، تحذيرات (بلا وقف خسارة، المركز > `trading.max_position_pct` 25% من المحفظة، العائد أقل من المخاطرة). `execute()`: الشراء/البيع بسعر حي جديد، وإن تحرك أكثر من 3% منذ الاقتراح ← «conflict»؛ بعد الشراء يُنشئ تنبيهَي وقف الخسارة وجني الأرباح. لا يوجد أي مسار لأمر حقيقي.
- وكيل `trading_analyst` متاح دائمًا (phase 7، analysis، `TRADE_TOOLS`)؛ `core/trading_analyst.py` (حتى 12 أداة، يذكر توفر مفتاح Twelve Data، ويُلحق «هذا تحليل تعليمي وليس نصيحة مالية…» إن نسيه النموذج). المنسّق: `TRADING_NOTE` للمخطِّط دائمًا، `TRADE_HINT` يمرّر الطلبات القصيرة («حلل البيتكوين») إلى المخطِّط، `_run_trade_step`، و`_run_trade_actions` الحقيقي. الإعداد `trading` (`fee_pct` 0.1، `max_position_pct` 25).
- الخادم: `GET /api/trading[?prices=1]`، `POST /api/trading/reset`، `GET /api/trading/alerts` (المتحققة غير المقروءة)، `POST /api/trading/alerts/seen`، `DELETE /api/trading/alerts/<id>`.
- اللوحة: بطاقات الصفقات والتنبيهات (rows)، بوابة «كلها تجريبية بأموال افتراضية…»، شريط تنبيهات لاصق أعلى المحادثة يُفحص كل دقيقة (ويُقرأ بالصوت في الاستماع الدائم عبر `window.YousefVoice`)، قسم «التداول التجريبي» في الإعدادات (القيمة، النقد، الربح/الخسارة، المراكز، التنبيهات مع الحذف، آخر الصفقات، تصفير المحفظة)، و«الخدمات» بدل «GitHub وVercel». VS Code: أسماء الأدوات والبطاقات والبوابة.
- إصلاح جانبي: `audit/deps.py` كان فيه f-string بشرطة مائلة عكسية لا يعمل إلا على Python 3.12+؛ صار يعمل على 3.10 (README يطلب 3.10+). الكود كله يُترجم الآن على 3.10 و3.11 (Python 3.10 متاح في البيئة عبر `uv python find 3.10`).
- `tests/market_fake.py` (Binance وTwelve Data وهميان) و`tests/test_trading.py` (12). المجموع 215 اختبارًا ناجحة؛ فحص بالمتصفح (فاتح/داكن/جوال، بلا أخطاء ولا تمرير أفقي).

## تفاصيل 6.6 (ما بُني) — الإصدار 0.7.0
- `integrations/images.py`: `ImageMaker(settings, keystore, openai_url, gemini_url)`: `providers()` حسب الإعداد `images.provider` (auto/openai/gemini/off) والمفاتيح المحفوظة؛ `make(prompt, shape, source)` ← `{data, mime, provider, model}`. OpenAI: `POST /v1/images/generations` أو `/images/edits` (JSON مع `images: [{image_url: data URL}]`)، `output_format: jpeg`، `quality: medium`، المقاسات: مربع 1024، عرضي 1536x1024، طولي 1024x1280 (4:5) لعائلة gpt-image-2 و1024 لغيرها (2:3 يرفضه إنستغرام). Gemini: Interactions API `POST /v1beta/interactions` مع `response_format {type: image, mime_type, aspect_ratio}`، وعند 404 `models/<m>:generateContent` مع `responseModalities` و`imageConfig.aspectRatio`. `find_image` يبحث عن أول صورة base64 بأي شكل للرد. ترتيب النماذج: المختار في الإعداد `images.openai_model`/`gemini_model` ثم الافتراضية (gpt-image-2.5-flare، gpt-image-2، gpt-image-1.5، gpt-image-1 / gemini-nano-banana-2.1، gemini-3.1-flash-image، gemini-2.5-flash-image)؛ خطأ «نموذج» ينتقل للتالي، وخطأ مفتاح/حصة ينتقل للمزوّد التالي.
- `integrations/image_tools.py`: `ImageToolbox` (`list_media`، `generate_image` مع `shape` و`from`: صورة `media:` أو رابط https لصورة منتج يُنزَّل حتى 8MB JPEG/PNG)؛ حد 4 صور لكل مهمة (`MAX_PER_TASK`)؛ الأثر `note: "media:<name>"`.
- وكيل `designer` «صانع الصور» (phase 6، task writing، يحتاج "images"، وبدونه ← writer)؛ `core/designer.py` (مع قراءة المتاجر لصور المنتجات)؛ ناشر السوشيال يحصل على `generate_image` أيضًا إن كان صنع الصور متاحًا؛ المنسّق: `_run_design_step`، و"images" في `_connected` حين يتوفر مزوّد. صنع الصورة لا يحتاج موافقة (لا يغيّر شيئًا خارج الجهاز، وتكلفته محدودة بالحد)، ونشرها يمر بالموافقة.
- الحالة `/api/status` فيها `images` (المزوّدون الجاهزون). اللوحة: قسم «الصور» في الإعدادات (اختيار المزوّد، حالة الجاهزية، مكتبة الصور بمصغّرات وإضافة وحذف)؛ الصور المصنوعة تظهر تحت الخطوة مع «أرفقها بمنشور»؛ «صانع الصور» في خارطة الوكلاء بحاجة «مفتاح OpenAI أو Gemini». VS Code: الاسم والأداة وزر «افتح الصورة» (`openMedia` يفتح `/api/media/<name>` في المتصفح بعد فحص الاسم).
- README: قسما «مدير الإعلانات» و«صانع الصور»، جدول الوكلاء، الهيكل.
- `tests/test_images.py` (6) مع خادم OpenAI/Gemini وهمي. المجموع 203 اختبارات ناجحة؛ فحص بالمتصفح (فاتح/داكن/جوال، بلا أخطاء ولا تمرير أفقي).

## 0.6.4 — تشغيل النموذج على المعالج عند تعذّر الكرت (20 أكتوبر)
- مع 0.6.3 استمر نفس الخطأ (CUDA error: shared object initialization failed) من أول طلب (المخطِّط)، رغم إعادة `num_ctx` — فليس السبب حجم السياق. السبب الأرجح: الكرت (GTX 1660 Super 6GB) لا يستطيع تشغيل gpt-oss:20b في وضعه الحالي (ذاكرة مشغولة، الصوت على الكرت، أو مشكلة في Ollama/التعريف مع هذا الكرت)، مع أنه عمل مرة قبل ساعة.
- الإصلاح الذاتي في `OllamaProvider._recover`: عند توقف المشغّل: (1) `free_gpu` = `VoiceEngine.release_gpu()` ينقل Whisper من الكرت للمعالج إن كان عليه، ثم إعادة المحاولة؛ (2) إن توقف ثانية: `options.num_gpu = 0` وتسجيل النموذج في `Router.cpu_only` (مشترك بين الطلبات طوال الجلسة)؛ (3) إن فشل على المعالج أيضًا: رسالة بخطوات فحص Ollama (`ollama run gpt-oss:20b "مرحبا"`، تحديث Ollama والتعريف). الحالة `ollama.cpu_only` تظهر «(على المعالج)» بجانب النموذج مع تنبيه مرة واحدة.
- محاكاة كاملة: Ollama وهمي يتوقف ما لم يكن num_gpu=0 ← مدير المتاجر قرأ متجر WooCommerce وأكمل التقرير؛ الطلبات اللاحقة على المعالج مباشرة.
- وفي نفس الإصدار (بعد لقطة 00:30: Ollama لم يتوقف هذه المرة، مدير المتاجر نفّذ أداة واحدة ثم ردود فارغة ×3): gpt-oss يرد أحيانًا باستدعاء أداته المدمجة (Harmony) فيعيده Ollama في `message.tool_calls` بمحتوى فارغ. `native_call()` يحوّله إلى JSON يوسف (`functions.x` ← `{"tool": "x", ...}`، و done/final ← `{"done": ...}`)، و`json_in_reasoning()` كملاذ أخير يأخذ آخر `{"tool"…}`/`{"done"…}` من التفكير. `toolloop`: ميزانية 12000 حرف لنتائج الأدوات الكاملة (أحدث نتيجة دائمًا كاملة، حتى 4) حتى لا تتجاوز سياق 8K لنموذج محلي. 190 اختبارًا ناجحة.

## 0.6.3 — توقف Ollama (20 أكتوبر)
- مع 0.6.2 ظهر: `HTTP 500: llama-server process has terminated: exit status 0xc0000409 ... CUDA error: shared object initialization failed` مع gpt-oss:20b. الأرجح امتلاء ذاكرة الكرت (6GB): 0.6.2 رفع `num_ctx` لنماذج التفكير إلى 16384، فأعاد Ollama تحميل النموذج بسياق أكبر (وربما الصوت على كرت الشاشة أيضًا).
- الإصلاح: يعود `num_ctx` لقيمة المستخدم دائمًا (لا تجاوز)؛ عند توقف مشغّل Ollama (process has terminated، CUDA error، out of memory…) يعيد يوسف المحاولة مرة بعد 3 ثوانٍ (Ollama يعيد تشغيل المشغّل)، ثم رسالة عربية بخطوات (الصوت على المعالج، إغلاق برامج الكرت، qwen2.5:7b)؛ وإن رفض Ollama قديم حقل `think` يُعاد الطلب بدونه. ملاحظة في إعدادات الصوت عن تقاسم الكرت مع Ollama.
- 185 اختبارًا ناجحة.

## 0.6.2 — مدير المتاجر مع gpt-oss:20b (20 أكتوبر)
- المستخدم ربط متجر WooCommerce «اللواء الاخضر allwaa alakhdar» ويستخدم `gpt-oss:20b` محليًا. «اقرأ المتجر المضاف» ← المخطِّط عمل، لكن مدير المتاجر ردّ «لم يُرجع النموذج إجابة قابلة للفهم» بلا أي أداة: ردود فارغة من النموذج في حلقة الأدوات.
- الإصلاح في `OllamaProvider`: نماذج التفكير (`gpt-oss`) تُرسل بلا `format: json` (قيد JSON من أول رمز يتعارض مع قناة التفكير في Harmony) وبتعليمة «JSON فقط» في رسالة النظام، مع `think: "low"` و`num_ctx` ≥ 16384؛ ولأي نموذج: إن عاد رد JSON فارغًا يُسأل مرة ثانية بلا format وبالتعليمة، وإن كان السبب امتلاء السياق (`done_reason=length`) يظهر خطأ واضح. `toolloop`: حتى 3 محاولات للردود الفارغة مع مثال أداة حقيقي، وتسجيل الرد الخام في السجل، ورسالة مفيدة تسمّي النموذج وتقترح البدائل؛ الرد النثري المتكرر يبقى إجابة كما كان.
- لم يُجرَّب على gpt-oss حقيقي (لا Ollama في بيئة البناء)؛ إن بقيت المشكلة: طلب `yousef.log` (فيه الآن الرد الخام) أو تجربة qwen2.5:7b.
- 184 اختبارًا ناجحة.

## 0.6.1 — ملاحظات المستخدم الأولى (19 أكتوبر)
- المستخدم: «الصوت العربي غير مفهوم، الإنجليزي ممتاز» ← الإصلاح: الافتراضي صار `large-v3-turbo` (small يخطئ كثيرًا في العربية واللهجة)، ووضع «تلقائي» يختار بين العربية والإنجليزية فقط (إن كشف Whisper لغة أخرى مثل الفارسية يعيد القراءة بالأرجح منهما حسب `all_language_probs`)، وموجّه أولي عربي كامل للعربية وإنجليزي للإنجليزية، وتنبيه في الإعدادات عند اختيار نموذج صغير مع العربية. كرت الشاشة: `install-voice-gpu.bat` + `requirements-voice-gpu.txt` (nvidia-cublas-cu12، nvidia-cudnn-cu12 9) و`add_nvidia_dlls()` على ويندوز، وإن فشل CUDA يعمل على المعالج مع ملاحظة (`note`، `device_used` في الحالة). قراءة الردود: ترتيب الأصوات (الطبيعية/Neural أولًا، ثم الخليجية ar-OM…)، ونصيحة Edge + «الأصوات عبر الإنترنت» للصوت العربي الواضح.
- المستخدم: «لا يوجد مكان لإضافة المتاجر» ← الشاشة الرئيسية فيها الآن أزرار «اربط متجرك» و«اربط صفحاتك» و«أضف مجلد مشروع» تفتح الإعدادات على القسم والنموذج مفتوح؛ سطر الإصدار أسفل الشاشة؛ سطر الترحيب يذكر الوكلاء العاملين فعلًا. وإن بقيت نسخة قديمة تعمل على المنفذ نفسه، `start.bat` الجديد يطلب منها التوقف (`POST /api/system/stop` من 0.6.1 فصاعدًا) أو يطلب إغلاق نافذتها، بدل فتح الواجهة القديمة بصمت (سبب محتمل لما رآه المستخدم).
- 181 اختبارًا ناجحة؛ فحص بالمتصفح للشاشة الرئيسية والجوال.

## تفاصيل 6.1 (ما بُني)
- `integrations/meta.py`: `Graph` (طلبات Graph API بالرمز في الاستعلام، JSON أو multipart للملفات المحلية)، `explain` (أخطاء Meta بالعربية: رمز منتهٍ 190، صلاحيات، PPA، تعذّر جلب الصورة 9004، نسبة الأبعاد، موعد الجدولة، حدود الطلبات)، `connect(token)` (رمز مستخدم ← `/me/accounts` مع رمز كل صفحة وحساب إنستغرام المرتبط ومهامها CREATE_CONTENT؛ أو رمز صفحة مباشرة)، `check_facebook_schedule`، `caption_problems`، والصنف `Page`: info، facebook_posts، facebook_scheduled، instagram_posts، instagram_quota، `publish_facebook(text, images, link, when)` (نص/رابط، صورة واحدة بـ /photos، عدة صور غير منشورة ثم attached_media، جدولة أصلية)، `publish_instagram(caption, images)` (صورة أو ألبوم حتى 10؛ JPEG العام يُرسل مباشرة وغيره يمر بصورة فيسبوك غير منشورة بأقرب مقاس ≤1440 ثم تُحذف؛ انتظار الحاوية بفواصل 0،2،3،5،10،20،30،60…؛ فحص الحد اليومي)، delete_post.
- `integrations/social.py`: `SocialAccounts` (entries/public مع platforms، find بالمعرّف أو اسم الصفحة أو @مستخدم إنستغرام، page، connect يحفظ رموز الصفحات في keys.dat باسم `social:<page id>` ويحدّث الموجود، test، remove). الإعداد `social` في config مع تحقق؛ keystore يقبل `social:<digits>` وطول مفاتيح حتى 2048.
- الخادم: `GET/POST /api/social`، `POST /api/social/<id>/test`، `GET /api/social/<id>/posts`، `DELETE /api/social/<id>`؛ لا يُعاد أي رمز في الردود.
- اللوحة: قسم «حسابات السوشيال» (خطوات الحصول على الرمز، ربط، اختبار، آخر المنشورات، إزالة)؛ خارطة الوكلاء تعرف حاجة «حساب سوشيال مربوط».
- `build_app(meta_api=..., meta_sleep=...)` للاختبار؛ `tests/meta_fake.py` خادم Graph وهمي (صفحتان، إحداهما بإنستغرام؛ رموز صفحة/مستخدم؛ feed، photos JSON وmultipart، attached_media، الجدولة، الصور غير المنشورة وimages، حاويات إنستغرام وحالتها والنشر والحد اليومي، الحذف)؛ `tests/test_social.py` (7). المجموع 166 اختبارًا ناجحة.

## تفاصيل 6.2 (ما بُني)
- `media.py`: `MediaLibrary` في `%LOCALAPPDATA%\YousefAI\media`: JPEG/PNG فقط (بفحص البايتات الأولى)، حتى 8MB و500 صورة، الاسم `img-<sha256 16>.jpg|png` (نفس الصورة تُحفظ مرة)، وسم بالاسم الأصلي في ملف `.label`، `path` يرفض أي اسم آخر.
- `memory.py`: جدول `social_posts` (الحساب، المنصة، النص، الصور كمراجع، الرابط، `scheduled_at`، `native`، الحالة: scheduled/publishing/published/failed/cancelled/missed، remote_id، url، error)؛ `claim_social_post` ذري؛ عند بدء التشغيل ما بقي «publishing» يصير failed ولا يُعاد تلقائيًا.
- منتجات المتاجر صار فيها `image_urls` (Shopify: featuredMedia.preview + media(first: 6) في المنتج الواحد فقط؛ Woo: images.src)، و`get_product` يعرض `images:`.
- `integrations/social_tools.py`: `SocialToolbox` (list_accounts، recent_posts، list_scheduled، list_media، propose_post، propose_cancel) بتحقق: إنستغرام يحتاج صورة وحسابًا مرتبطًا، الصور `media:` أو https فقط وحتى 10، قيود النص، الرابط لفيسبوك فقط، «when» يلزمه وقت، فيسبوك 10 دقائق–30 يومًا، 60 يومًا كحد عام، تحذير الحد اليومي (≥90%) وتحذير بقاء يوسف شغّالًا لجدولة إنستغرام؛ `StoreReader` أدوات المتجر للقراءة فقط.
- `integrations/publisher.py`: `SocialPublisher.publish` (لكل منصة: نشر فوري، أو جدولة فيسبوك الأصلية، أو صف «scheduled» لإنستغرام)، `cancel` (يحذف المجدول في فيسبوك أو يلغي المحلي)، `publish_now` (لنقرة المستخدم على الفائت أو الفاشل)، `run_due` (فيسبوك الأصلي ← published مع الرابط بعد دقيقة؛ المحلي المتأخر أكثر من ساعتين ← missed؛ غير ذلك يُنشر)، خيط يعمل كل 30 ثانية يبدأ من `__main__`.
- وكيل `social_publisher` «ناشر السوشيال» (phase 6، يحتاج "social"، وبدونه ← writer)؛ `core/social_publisher.py` (حتى 16 أداة، نوع المهمة writing)؛ المنسّق: `SOCIAL_NOTE`، `SOCIAL_HINT` للطلبات القصيرة، `_run_social_step`، و`_settle` يفصل git والمتجر والمنشورات، و`_run_social_actions`.
- الخادم: `POST /api/media` (جسم خام image/jpeg|png، الاسم في X-File-Name)، `GET /api/media`، `GET /api/media/<name>` (يرسل الصورة)، `DELETE /api/media/<name>`، `GET /api/social/posts?status=`، `POST /api/social/posts/<id>/cancel`، `POST /api/social/posts/<id>/publish`؛ CSP يسمح بصور https للمعاينة.
- `tests/test_social_agent.py` (12). المجموع 178 اختبارًا ناجحة.

## تفاصيل 6.3 (ما بُني)
- اللوحة: بطاقة معاينة المنشور `postHTML` (الحساب و@إنستغرام، شارات المنصات، شبكة حتى 4 صور مع +N، نصّا فيسبوك وإنستغرام إن اختلفا مع تلوين الوسوم والإشارات، الموعد والرابط، التحذيرات، والنتيجة بروابط قابلة للنقر)، و`social_cancel` كبطاقة rows؛ البوابة: «نشر منشور واحد، وجدولة منشور واحد… لن يُنشر شيء قبل موافقتك»؛ زر صورة في خانة الطلب (يظهر مع حساب مربوط) + سحب وإفلات، يرفع إلى `/api/media` ويعرض شرائح الصور، ويضيف سطر «الصور المرفقة: media:…» للطلب (ويُعرض كصور مصغّرة تحت عنوان المهمة بدل النص)؛ «المنشورات المجدولة» داخل «حسابات السوشيال» (الحالة، الموعد، من ينشره، الخطأ، «ألغِه» و«انشر الآن»)، وتنبيه عند الفتح إن فات موعد منشور.
- VS Code: بطاقة منشور نصية (المنصات، الموعد، عدد الصور، النصان)، اسم «ناشر السوشيال»، أسماء الأدوات.
- فحص بالمتصفح: إرفاق صورتين ← منشور فوري (فيسبوك ألبوم + إنستغرام ألبوم) + تذكير إنستغرام مجدول ← طُبّق؛ قائمة المجدول والإلغاء؛ الداكن؛ الجوال 390px بلا تمرير أفقي؛ VS Code؛ وانحدار الصوت والمبرمج والمتاجر والمدير التقني سليم. 178 اختبارًا ناجحة.

## المرحلة 5 — الخطة (العروض والباقات والمدير التقني)
- [x] 5.1 العروض والباقات لمدير المتاجر: list_discounts؛ propose_discount (نسبة أو مبلغ، بكود أو تلقائي، منتجات محددة أو الطلب كله، بداية ونهاية، حد أدنى، حد استخدام)؛ propose_end_discount (إيقاف عرض)؛ جدولة سعر التخفيض في WooCommerce (sale_from/sale_to)؛ propose_bundle (باقة من عدة منتجات بسعر واحد)؛ حقول SEO لمنتجات Shopify. كلها اقتراحات تُنفَّذ بعد الموافقة.
- [x] 5.2 «المدير التقني»: فحص الموقع الحي (HTTPS، شهادة TLS، ترويسات الأمان، ملفات مكشوفة مثل .env و.git و debug.log، xmlrpc وكشف المستخدمين في ووردبريس، المحتوى المختلط، أساسيات SEO والسرعة)؛ فحص صحة المتجر (منتجات بلا صور/وصف/SKU، أسعار صفر، SKU مكرر؛ وفي WooCommerce: إصدارات ووردبريس وPHP، وضع التصحيح، الإضافات والقالب القديمة)؛ فحص كود مجلد المشروع (أسرار مكتوبة في الكود، أنماط خطرة في PHP/JS/Python)؛ فحص ثغرات الاعتماديات عبر قاعدة OSV المفتوحة. الإصلاحات: كود عبر فروقات المبرمج، وبيانات المتجر عبر اقتراحات المتجر، والباقي تعليمات.
- [x] 5.3 الواجهة: بطاقات العروض والباقات وإيقاف العروض، بوابة موافقة موحّدة (ملفات + git + متجر)، لوحة VS Code، وتنبيه صلاحيات الخصومات في Shopify.
- [x] 5.4 تحقق شامل (159 اختبارًا، فحص المتصفح، لوحة VS Code، انحدار الصوت والمبرمج) وتغليف 0.5.0 وREADME.

## قرارات المرحلة 5
- الخصومات في Shopify: `discountCodeBasicCreate` (بكود) أو `discountAutomaticBasicCreate` (تلقائي) مع `context: {all: ALL}` (الحقل `customerSelection` مهمل)؛ تحتاج صلاحيتي `read_discounts` و`write_discounts`. الإيقاف بـ `discountCodeDeactivate`/`discountAutomaticDeactivate`.
- الخصومات في WooCommerce: كوبونات REST (`/coupons`) بكود دائمًا؛ لا تاريخ بداية للكوبون في النواة (يُنبَّه المستخدم)، والإيقاف بجعل تاريخ الانتهاء أمس. العرض التلقائي في WooCommerce = سعر تخفيض مجدول على المنتج.
- الباقات في Shopify: باقة ثابتة أصلية `productBundleCreate` (غير متزامنة، تُتابَع بـ `productOperation`؛ مخزونها من مكوّناتها؛ حتى 30 مكوّنًا)، ثم سعر الباقة على متغيراتها، وتُترك مسودة. في WooCommerce (بدون إضافة مدفوعة): منتج بسيط يذكر مكوّناته، والمخزون غير مرتبط، ويظهر ذلك في بطاقة الموافقة.
- المدير التقني يفحص فقط مواقع المتاجر المتصلة (ملكية مثبتة بمفاتيح الإدارة) ومجلدات المشاريع المسموحة؛ الفحص الحي غير تدخلي: طلبات GET/HEAD قليلة لمسارات معروفة، بلا تخمين كلمات مرور ولا استغلال. لا يُعاد محتوى ملف مكشوف (مثل .env) للنموذج، فقط أنه مكشوف.
- فحص الاعتماديات يرسل أسماء الحزم وإصداراتها فقط (لا الكود) إلى api.osv.dev.
- الأسرار المكتوبة داخل ملفات عادية (رموز Shopify/WooCommerce/Stripe/OpenAI/AWS/GitHub/Google/Meta، مفاتيح خاصة، وقيم متغيرات password/secret/api_key/token) لا تصل للنموذج أبدًا: تظهر له كعلامة `[[SECRET_n]]` في كل مخرجات أدوات الملفات (قراءة، بحث، diff، أوامر)، وتُعاد للقيمة الحقيقية داخل تعديلاته قبل الاقتراح. ينطبق على المبرمج والمدير التقني معًا.
- المدير التقني يرى كل الأدوات المتاحة للطلب في صندوق واحد: الفحص دائمًا، والملفات وgit إن أُرفق مجلد، وأدوات المتاجر إن وُجد متجر؛ وموافقته موحّدة: الملفات ثم git ثم تغييرات المتجر، و«الملفات فقط» يرفض git والمتجر معًا.
- ترقيم المراحل: أُدرجت هذه المرحلة 5، فصارت «المحتوى والنشر والإعلانات» 6، و«التحكم بالجهاز والتداول والجوال» 7.

## تفاصيل 5.1 (ما بُني)
- `integrations/shopify.py`: `discounts()` (discountNodes لستة أنواع)، `create_discount(spec)` (بكود: discountCodeBasicCreate مع usageLimit وappliesOncePerCustomer؛ بدون كود: discountAutomaticBasicCreate؛ `context: {all: "ALL"}`؛ نسبة = percentage/100، مبلغ = discountAmount مع appliesOnEachItem عند تحديد منتجات؛ items: products.productsToAdd أو all)، `end_discount(id)` (Code/Automatic Deactivate حسب نوع المعرّف)، `product_options`، `bundle_check` (حد 3 خيارات يختارها المشتري)، `create_bundle(spec)` (productBundleCreate بأسماء خيارات فريدة ← متابعة productOperation حتى COMPLETE لـ30 ثانية ← سعر كل المتغيرات ← DRAFT والوصف)، `all_products` (صفحات)، حقول المنتج: images (featuredMedia) وseo_title/seo_description (تحديث أحدهما يحافظ على الآخر)، و`info.site` من primaryDomain.
- `integrations/woocommerce.py`: كوبونات (`discounts`، `create_discount`: percent/fixed_cart/fixed_product، الانتهاء = اليوم التالي لآخر يوم 00:00، usage_limit_per_user=1 لمرة لكل عميل؛ `end_discount`: الانتهاء أمس)، `create_bundle` (منتج بسيط مسودة بوسم «باقة» وقائمة مكوّناته)، `bundle_check` (غير أصلي، تنبيه المخزون)، sale_from/sale_to (date_on_sale_*)، images، `all_products`، `system_status`.
- `integrations/store_tools.py`: `parse_when`/`show_when` (تواريخ بالأرقام العربية، بتوقيت الجهاز)، حقول جديدة (Shopify: seo_title/seo_description؛ Woo: sale_from/sale_to)، `field_warnings` (أسعار، طول SEO، ترتيب التواريخ)، أدوات: list_discounts، propose_discount (تحقق: نسبة 1–100، كود 3–40 حرفًا إنجليزيًا غير مكرر، نهاية مستقبلية، منتجات موجودة؛ تحذيرات: خصم >50%، خصم ≥ سعر منتج، بداية مستقبلية في Woo)، propose_end_discount، propose_bundle (2 وحدات على الأقل، حتى 30 منتجًا، كل منتج مرة، حساب المجموع المنفصل والتوفير). حمولة الأنواع الجديدة: {store_id، store، platform، title، rows: [{label، value}] بالعربية، warnings، spec}.
- المنسّق ينفّذ `store_create_discount` و`store_end_discount` و`store_create_bundle` بعد الموافقة. تعليمات مدير المتاجر محدّثة.
- الاختبارات: 145 ناجحة؛ الخوادم الوهمية تدعم الخصومات والإيقاف والباقات غير المتزامنة وSEO والكوبونات وsystem_status.
- لم يُجرَّب على متجر حقيقي: خاصة `productBundleCreate` (أسماء الخيارات لمنتج بلا خيارات) و`context: {all: ALL}`.

## تفاصيل 5.2 (ما بُني)
- `yousef/audit/findings.py`: `Finding(severity, area, title, detail, fix, where)` بخمس درجات (critical/high/medium/low/info)، `format_findings` نص مرتب للنموذج.
- `audit/site.py`: `SiteAuditor(fetcher, cert_check).audit(site, wordpress)`: HTTPS وتحويل http، أيام الشهادة، ترويسات الأمان (HSTS، nosniff، Referrer-Policy، clickjacking)، كشف الإصدار في Server/X-Powered-By/generator، سرعة وحجم الصفحة، title/description/H1/alt/lang/viewport/canonical/Open Graph، محتوى مختلط ونماذج http، robots.txt (`blocks_everyone` يقرأ مجموعة `User-agent: *` فقط) وsitemap من نفس النطاق، و14 مسارًا مكشوفًا (.env، .git/HEAD، نسخ wp-config، debug.log، phpinfo، xmlrpc، users في REST، فهرسة uploads، readme.html) بطلب GET بلا تتبع تحويلات وبتحقق من التوقيع، ولا يُحفظ المحتوى.
- `audit/health.py`: بيانات المنتجات (سعر صفر، بلا صورة، وصف قصير، نفاد، بلا SKU، SKU مكرر، بلا وصف SEO في Shopify) ومنصة WooCommerce من system_status (PHP أقدم من 8.2 عالٍ، 8.2 منخفض؛ ووردبريس مقارنة بأحدث إصدار من api.wordpress.org؛ WP_DEBUG؛ HTTPS؛ إظهار الأخطاء؛ إضافات وقالب غير محدّثة؛ قوالب WooCommerce قديمة).
- `audit/code.py`: `scan_code(ws, path)`: ~30 قاعدة (أسرار مكتوبة مع إخفاء القيمة، أوامر نظام/unserialize/include/SQL بمدخلات الزائر، `$wpdb` بدون prepare، echo بلا تنقية، eval، رفع ملفات، md5 لكلمات المرور، innerHTML/dangerouslySetInnerHTML/new Function، رموز في localStorage، روابط http، eval/shell=True/pickle/yaml.load في Python، تعطيل TLS، وضع التصحيح)، ومعالجات `wp_ajax_` بلا nonce ومسارات REST بلا permission_callback. يتجاهل ملفات الاختبار (إلا للأسرار) والمصغّرة وملفات الأسرار.
- `audit/deps.py`: `check_dependencies(ws, post, get)`: package-lock.json (v1–v3)، package.json (تقريبي عند غياب القفل)، composer.lock، requirements.txt (`==` فقط) ← OSV querybatch ثم تفاصيل حتى 25 ثغرة (الخطورة، CVE، أول إصدار مُصلَح).
- `audit/tools.py`: `AuditToolbox` بأدوات site_check وstore_health (تحتاج متجرًا) وscan_code وcheck_dependencies (تحتاج مجلدًا)؛ موقع Shopify من primaryDomain، وموقع Woo من رابط المتجر؛ لا يكرر فحص نفس الموقع في المهمة؛ `reports` للواجهة.
- `workspace/secrets.py`: `SecretMasker` (انظر القرارات)، مدمج في `Toolbox.run` وreplace/write_file.
- `core/multitool.py`: `MultiToolbox` (توزيع حسب الأداة، trace مشترك، مساعدة مدمجة بسطر done واحد، تجميع التغييرات والإجراءات).
- `core/tech_manager.py`: `build_toolbox` و`run_tech_manager` (حتى 24 أداة، نوع المهمة code) بتعليمات: افحص أولًا، أكّد بقراءة الكود، أصلح كاقتراحات، تعليمات دقيقة لما لا تصلحه الأدوات، لا هجوم ولا استغلال ولا مواقع أخرى، تقرير مرتب بالخطورة.
- `core/agents.py`: وكيل `tech_manager` «المدير التقني» (phase 5)؛ `NEEDS` صار قائمة بدائل (`("stores", "workspace")`) و`FALLBACK` (tech_manager ← coder، store_manager ← analyst)؛ ناشر السوشيال ومدير الإعلانات phase 6، التداول والجهاز phase 7.
- `core/orchestrator.py`: `_connected(ws)` يضيف "workspace"؛ `TECH_NOTE` للمخطِّط؛ `TECH_HINT` يرسل الطلبات القصيرة مثل «افحص متجري» للمخطِّط؛ `_run_tech_step`؛ وتوحيد خطوات الأدوات الثلاث في `_finish_tool_step` و`_settle` (ملفات ← git ← متجر)؛ حقول حقن للاختبار: `site_auditor`، `osv`، `wp_latest`.
- الاختبارات: 159 ناجحة (`tests/test_tech.py`: 15، منها خادم HTTP محلي يتحقق أن التحويل لا يُتبع في المسارات المكشوفة).
- لم يُجرَّب على الشبكة الحقيقية من بيئة البناء (OSV وwordpress.org محجوبان هنا)؛ يعمل على جهاز المستخدم.

## تفاصيل 5.3 (ما بُني)
- اللوحة (`web/app.js`): بطاقات `rows` للعروض وإيقاف العروض والباقات (`PROPOSAL_KIND`، `textDir` لقيم عربية مختلطة مثل «23.000 OMR (توفير 13%)»)؛ حقول SEO وتواريخ التخفيض في بطاقات قبل/بعد؛ بوابة موافقة واحدة لكل الاقتراحات (ملفات، ثم git، «وفي المتجر: …») بأزرار «طبّق المحدد» و«الملفات فقط» و«ارفض»؛ شارات نتائج الفحص (`.audits`، حمراء عند حرج/عالٍ) من `note` في trace؛ أسماء عربية لكل الأدوات الجديدة؛ خارطة الوكلاء تعرض «يحتاج متجر متصل أو مجلد مشروع» (الخادم يرسل `needs` مع كل وكيل)؛ تنبيه صلاحيتي read_discounts/write_discounts؛ `.md { overflow-wrap: break-word }` لإصلاح اتساع الصفحة على الجوال بسبب الروابط الطويلة.
- VS Code (`media/chat.js`): نفس البطاقات والبوابة والشارات، «المدير التقني» في الأسماء، وأمر جديد «يوسف: افحص المشروع (ثغرات وأخطاء)» (`yousef.audit`) في لوحة الأوامر وقائمة المحرر.
- فحص بالمتصفح (Playwright): عرض + إيقاف عرض + باقة ← طُبّقت الثلاثة؛ خطوة المدير التقني (4 فحوص، تعديل ملف بإزالة الرمز عبر علامة السر، وصف SEO) ← طُبّقت؛ الوضع الداكن؛ الجوال 390px بلا تمرير أفقي؛ لوحة VS Code؛ وانحدار الصوت والمبرمج سليم. 159 اختبارًا ناجحة.

## ملخص ما بُني
**المرحلة 1 (0.1.0):** بايثون قياسي فقط؛ `start.bat`؛ خادم 127.0.0.1:8765 بحماية Host/Origin وترويسة `X-Yousef-Client` وCSP؛ البيانات في `%LOCALAPPDATA%\YousefAI`؛ مفاتيح DPAPI؛ موجّه نماذج Ollama/Claude/OpenAI/Gemini مع انتقال عند الفشل؛ منسّق (مخطِّط JSON حتى 5 خطوات) ووكلاء: العام، المبرمج، صانع المحتوى، المحلل؛ بوابة موافقة؛ تعلّم بالقواعد والتقييمات؛ لوحة عربية بخط Sakkal Majalla وألوان أبواب مسقط.

**المرحلة 2 (0.2.0):** `yousef/voice/` (faster-whisper اختياري عبر `install-voice.bat` في `.venv`، CPU int8 افتراضيًا)؛ كشف «يوسف» من نص Whisper؛ `/api/voice/{status,load,transcribe}`؛ المهام لها `source` (text/voice)؛ في اللوحة: زر ميكروفون، «الاستماع الدائم»، الموافقة بالصوت، قراءة الردود بأصوات محلية؛ `window.Yousef` في app.js لاستخدام voice.js. 78 اختبارًا.

**المرحلة 3 (0.3.0):** `yousef/workspace/` (مجلدات مسموحة، أدوات ملفات آمنة، رفض ملفات الأسرار، تعديلات مقترحة overlay تُكتب بعد الموافقة مع كشف التعارض)؛ `core/coder.py`؛ `core/jsonutil.py`؛ جداول `changes` و`actions`؛ git (status/diff، commit/push بعد الموافقة)، `integrations/github.py` (Pull Request)، `integrations/vercel.py` (حالة النشر)؛ أوامر مسموحة لكل مجلد؛ `scope: files`؛ إضافة VS Code في `vscode/` بدون خطوة بناء + `tools/build_vsix.py`. 110 اختبارات.

**المرحلة 4 (0.4.0):** `integrations/shopify.py` (Admin GraphQL `2026-10`، الصلاحيات read_products وwrite_products وread_orders)، `integrations/woocommerce.py` (REST v3، https إلزامي)، `integrations/stores.py` (`settings.stores` والسر في keystore باسم `store:<id>`، اختبار الاتصال قبل الحفظ، `sales_summary`)؛ الطلبات بلا أي بيانات عملاء؛ `core/toolloop.py` حلقة الأدوات المشتركة؛ `integrations/store_tools.py` (`StoreToolbox`: قراءة المنتجات والطلبات والمبيعات، واقتراحات `store_create_product`/`store_update_product` بقيم قبل/بعد وتحذيرات الأسعار)؛ وكيل `store_manager` (فقط مع متجر متصل، `agents.NEEDS`)؛ تنفيذ مستقل لكل اقتراح مع حالة `conflict`؛ `decide(exclude=[...])`؛ عمود `store` للمهام؛ واجهة: لوحة «المتاجر»، اختيار المتجر، بطاقات قبل/بعد بخانات اختيار، وفي VS Code. 135 اختبارًا.

## خطة المراحل
- [x] المرحلة 1: النواة (0.1.0)
- [x] المرحلة 2: الصوت (0.2.0)
- [x] المرحلة 3: وكيل المبرمج + VS Code + GitHub وVercel (0.3.0)
- [x] المرحلة 4: المتاجر — Shopify وWooCommerce (0.4.0)
- [x] المرحلة 5: العروض والباقات والمدير التقني للمتاجر (0.5.0)
- [x] المرحلة 6: المحتوى والنشر والإعلانات — ناشر السوشيال (0.6.0)، مدير الإعلانات وصانع الصور (0.7.0)
- [ ] المرحلة 7: محلل التداول، التحكم بالجهاز، الوصول من الجوال، المستمع الصوتي بدون متصفح ← 1.0.0

## المرحلة 7 — الخطة
- [x] 7.1 «محلل التداول»: بيانات السوق (العملات الرقمية من واجهة Binance العامة للأسعار بلا مفتاح؛ الأسهم من مصدر مجاني يُتحقق منه عند البناء)، مؤشرات بـ Python خالص (SMA، EMA، RSI، MACD، Bollinger، ATR، دعم/مقاومة)، محفظة تجريبية (رصيد افتراضي، صفقات `trade_buy`/`trade_sell` بموافقة وبسعر السوق الحالي مع رسوم)، تنبيهات سعر/مؤشر (`trade_alert` بموافقة، مجدول يفحصها ما دام يوسف شغّالًا، وتظهر في اللوحة). لا تداول حقيقي أبدًا، وتنبيه «ليست نصيحة مالية».
- [x] 7.2 «التحكم بالجهاز»: واجهة خلفية قابلة للاستبدال (ويندوز: فتح برنامج من قائمة مسموحة، رابط، مجلد؛ الصوت ووسائط التشغيل بمفاتيح النظام؛ قفل الشاشة؛ معلومات النظام للقراءة)، كل إجراء `device_*` بموافقة، بلا حذف ملفات ولا أوامر عشوائية ولا إيقاف تشغيل.
- [ ] 7.3 الوصول من الجوال: مستمع ثانٍ على الشبكة المنزلية يُفعَّل يدويًا، اقتران برمز من 6 أرقام يظهر على الكمبيوتر (صالح 10 دقائق، محاولات محدودة)، جلسة بملف تعريف (HttpOnly، SameSite=Strict) لكل جهاز مع الإلغاء، واجهة جوال + manifest؛ وTailscale للوصول من خارج البيت بـ https.
- [ ] 7.4 المستمع الصوتي بدون متصفح: `sounddevice` اختياري، تقطيع بالطاقة، كلمة التنبيه بـ Whisper الموجود، الرد بصوت ويندوز (System.Speech)؛ ثم تحقق شامل وإصدار 1.0.0.

## مبادئ ثابتة
- الجهاز: GTX 1660 Super 6GB، 32GB RAM، Ryzen 5 3600 — نموذج محلي مقترح qwen2.5:7b.
- التداول: تحليل وتنبيهات وتداول تجريبي فقط؛ الصفقة الحقيقية بموافقة يدوية.
- النشر عبر الواجهات الرسمية فقط. "التعلم" = ذاكرة، لا إعادة تدريب.
- أي إجراء خارج المحادثة يُقترح أولًا ويُنفَّذ بعد الموافقة فقط؛ لا حذف للمنتجات ولا تعديل للطلبات ولا قراءة لبيانات العملاء الشخصية.

## بانتظار المستخدم (تجربة على ويندوز)
- `start.bat`، حفظ مفتاح API (للتحقق من DPAPI)، طلب متعدد الخطوات مع Ollama.
- `install-voice.bat` ثم الميكروفون والاستماع الدائم (أول تجربة حقيقية لـ faster-whisper).
- `install-vscode-extension.bat` ثم «يوسف: أصلح هذا» على ملف في مشروع حقيقي.
- إضافة مجلد مشروع git، ورمز GitHub، وتجربة «أصلح وافتح Pull Request».
- إضافة متجر Shopify أو WooCommerce حقيقي (يُنصح بمتجر أو منتج تجريبي أولًا).
- تجربة عرض بكود وباقة على متجر تجريبي (أول تجربة حقيقية لـ productBundleCreate وdiscountCodeBasicCreate)، مع صلاحيتي read_discounts/write_discounts في Shopify.
- «افحص متجري» و«افحص المشروع» (أول تجربة حقيقية لفحص الموقع وOSV وwordpress.org من جهاز المستخدم).
- ربط صفحة فيسبوك (+ إنستغرام تجاري) برمز Meta، ثم منشور تجريبي فوري ومنشور مجدول (أول تجربة حقيقية لـ Graph API v26.0، وطريقة الصورة غير المنشورة لإنستغرام).
- التحكم بالجهاز: «افتح المفكرة» و«ارفع الصوت» و«اقفل الشاشة» (أول تجربة حقيقية لـ os.startfile وkeybd_event وLockWorkStation).
- محلل التداول: «حلل البيتكوين» (أول تجربة حقيقية لـ data-api.binance.vision من جهاز المستخدم)، ثم مفتاح Twelve Data و«كيف سهم أبل؟».
- مدير الإعلانات برمز فيه ads_read/ads_management (أول تجربة حقيقية لـ Marketing API)، وصانع الصور بمفتاح OpenAI أو Gemini (أول تجربة حقيقية لواجهتي الصور).
- عند أي خطأ: إرسال `%LOCALAPPDATA%\YousefAI\yousef.log`.

## الخطوة التالية
1. 7.3 الوصول من الجوال: الإعداد `remote` (متوقف افتراضيًا، المنفذ 8766)، مستمع ثانٍ على 0.0.0.0 يُشغَّل ويُوقف من اللوحة، اقتران برمز 6 أرقام صالح 10 دقائق (5 محاولات)، ملف تعريف جلسة لكل جهاز (يُخزَّن مُجزّأً hash) مع قائمة الأجهزة وإلغائها، كل طلب من الشبكة يحتاج الجلسة، `manifest.webmanifest` وأيقونة للشاشة الرئيسية، وتعليمات Tailscale للوصول من خارج البيت.
2. ثم 7.4 ← 1.0.0.
