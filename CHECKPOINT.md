# نقطة توقف — YOUSEF AI

> اقرأ هذا الملف أولًا في أي محادثة جديدة، وأكمل من "الخطوة التالية" ولا تبدأ من الصفر.
> قاعدة العمل: بعد كل خطوة عمل يُحدَّث هذا الملف (نسخة في المشروع `claude/yousef-ai-checkpoint.md` ونسخة داخل الكود `CHECKPOINT.md`) ويُرفع commit على GitHub.

آخر تحديث: 2026-10-19 — اكتملت الخطوة 5.2

## الحالة الحالية
- المستودع: https://github.com/aswar0yousef-design/YOUSEF-AI (الفرع main). كل خطوة مكتملة = commit.
- آخر إصدار مُسلَّم: 0.4.0 (`yousef-ai-v0.4.0.zip` + `yousef-ai-0.4.0.vsix`).
- المرحلة 5 (العروض والباقات والمدير التقني، بطلب المستخدم قبل ناشر السوشيال): 5.1 و5.2 مكتملتان ومرفوعتان؛ التالي 5.3 (الواجهة).
- LayanX يبقى على حاله كمرجع فقط.

## المرحلة 5 — الخطة (العروض والباقات والمدير التقني)
- [x] 5.1 العروض والباقات لمدير المتاجر: list_discounts؛ propose_discount (نسبة أو مبلغ، بكود أو تلقائي، منتجات محددة أو الطلب كله، بداية ونهاية، حد أدنى، حد استخدام)؛ propose_end_discount (إيقاف عرض)؛ جدولة سعر التخفيض في WooCommerce (sale_from/sale_to)؛ propose_bundle (باقة من عدة منتجات بسعر واحد)؛ حقول SEO لمنتجات Shopify. كلها اقتراحات تُنفَّذ بعد الموافقة.
- [x] 5.2 «المدير التقني»: فحص الموقع الحي (HTTPS، شهادة TLS، ترويسات الأمان، ملفات مكشوفة مثل .env و.git و debug.log، xmlrpc وكشف المستخدمين في ووردبريس، المحتوى المختلط، أساسيات SEO والسرعة)؛ فحص صحة المتجر (منتجات بلا صور/وصف/SKU، أسعار صفر، SKU مكرر؛ وفي WooCommerce: إصدارات ووردبريس وPHP، وضع التصحيح، الإضافات والقالب القديمة)؛ فحص كود مجلد المشروع (أسرار مكتوبة في الكود، أنماط خطرة في PHP/JS/Python)؛ فحص ثغرات الاعتماديات عبر قاعدة OSV المفتوحة. الإصلاحات: كود عبر فروقات المبرمج، وبيانات المتجر عبر اقتراحات المتجر، والباقي تعليمات.
- [ ] 5.3 الواجهة: بطاقات العروض والباقات وإيقاف العروض، بوابة موافقة موحّدة (ملفات + git + متجر)، لوحة VS Code، وتنبيه صلاحيات الخصومات في Shopify.
- [ ] 5.4 تحقق شامل وتغليف 0.5.0.

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
- [ ] المرحلة 5: العروض والباقات والمدير التقني للمتاجر (قيد العمل)
- [ ] المرحلة 6: المحتوى والنشر والإعلانات (ناشر السوشيال أولًا)
- [ ] المرحلة 7: التحكم بالجهاز، تحليل التداول، تطبيق الجوال
- [ ] لاحقًا: مستمع صوتي أصلي بدون متصفح

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
- عند أي خطأ: إرسال `%LOCALAPPDATA%\YousefAI\yousef.log`.

## الخطوة التالية
1. بناء 5.3 (الواجهة): بطاقات `rows` لاقتراحات العروض والباقات وإيقاف العروض في اللوحة وVS Code؛ بوابة موافقة موحّدة لخطوة المدير التقني (ملفات + git + متجر في بطاقة واحدة، وزر «الملفات فقط»)؛ تقرير الفحص (عدّاد الخطورة من `note` في trace)؛ أسماء الأدوات الجديدة في TOOL_LABEL؛ اسم «المدير التقني» في VS Code؛ تنبيه صلاحيتي read_discounts/write_discounts في لوحة المتاجر؛ أرقام المراحل الجديدة في خارطة الطريق؛ ثم فحص بالمتصفح.
2. 5.4: اختبار شامل، الإصدار 0.5.0، README، zip + vsix.
3. بعد المرحلة 5: المرحلة 6 — «ناشر السوشيال» عبر Meta Graph API الرسمية (صفحات فيسبوك وحسابات إنستغرام التجارية): مسودات من صانع المحتوى، معاينة، جدولة، والنشر بعد الموافقة فقط؛ ثم «مدير الإعلانات» بقراءة أداء الحملات أولًا.
