# نقطة توقف — YOUSEF AI

> اقرأ هذا الملف أولًا في أي محادثة جديدة، وأكمل من "الخطوة التالية" ولا تبدأ من الصفر.
> قاعدة العمل: بعد كل خطوة عمل يُحدَّث هذا الملف (نسخة في المشروع `claude/yousef-ai-checkpoint.md` ونسخة داخل الكود `CHECKPOINT.md`) ويُرفع commit على GitHub.

آخر تحديث: 2026-10-19 — اكتملت الخطوة 6.3

## الحالة الحالية
- المستودع: https://github.com/aswar0yousef-design/YOUSEF-AI (الفرع main). كل خطوة مكتملة = commit.
- آخر إصدار مُسلَّم: 0.5.0 (`yousef-ai-v0.5.0.zip` + `yousef-ai-0.5.0.vsix`).
- المرحلة 5 (العروض والباقات والمدير التقني) مكتملة ومُسلَّمة كإصدار 0.5.0.
- المرحلة 6 (ناشر السوشيال أولًا، بطلب المستخدم): 6.1 و6.2 و6.3 مكتملة ومرفوعة؛ التالي 6.4 (تحقق شامل وتغليف 0.6.0).
- LayanX يبقى على حاله كمرجع فقط.

## المرحلة 6 — الخطة (ناشر السوشيال أولًا)
- [x] 6.1 موصل Meta الرسمي (Graph API v26.0): ربط صفحات فيسبوك وحسابات إنستغرام التجارية المرتبطة بها برمز واحد؛ رموز الصفحات في keys.dat؛ قراءة آخر المنشورات والمجدولة؛ النشر (نص، رابط، صورة، عدة صور) والجدولة الأصلية في فيسبوك؛ النشر في إنستغرام (صورة أو ألبوم حتى 10) مع فحص الحد اليومي؛ لوحة «حسابات السوشيال» في الإعدادات.
- [x] 6.2 وكيل «ناشر السوشيال»: مكتبة صور محلية (رفع من اللوحة)، أدوات الحسابات والمنشورات والصور، قراءة منتجات المتاجر وصورها للقراءة فقط، `propose_post` لكل منصة بنص مناسب، وجدولة إنستغرام محليًا (جدول `social_posts` ومجدول يعمل ما دام يوسف شغّالًا، والمتأخر أكثر من ساعتين يُعلَّم «فات موعده» ولا يُنشر تلقائيًا)، والنشر بعد الموافقة فقط.
- [x] 6.3 الواجهة: بطاقة معاينة المنشور (الحساب، المنصات، النص، الصور، الموعد)، لوحة المنشورات المجدولة مع الإلغاء و«انشر الآن» للفائت، إرفاق صور من خانة الطلب، وVS Code؛ فحص بالمتصفح.
- [ ] 6.4 تحقق شامل وتغليف 0.6.0.

## قرارات المرحلة 6
- Meta فقط في البداية (فيسبوك + إنستغرام عبر Facebook Login، المضيف graph.facebook.com، الإصدار v26.0 الصادر 29 يوليو 2026). X وTikTok وLinkedIn تحتاج اشتراكًا مدفوعًا أو مراجعة شراكة، فتؤجَّل.
- الربط: المستخدم ينشئ تطبيق Meta خاصًا به (وضع التطوير يكفي لصفحاته)، ويولّد رمز مستخدم من Graph API Explorer بصلاحيات pages_show_list وpages_read_engagement وpages_manage_posts وinstagram_basic وinstagram_content_publish وbusiness_management، ويمدّده من Access Token Debugger؛ يوسف يجلب الصفحات ورموزها (لا تنتهي عند اشتقاقها من رمز طويل) ويحفظها مشفّرة.
- فيسبوك: `/{page}/feed` و`/{page}/photos` (عدة صور: صور غير منشورة ثم attached_media)؛ الجدولة الأصلية من 10 دقائق إلى 30 يومًا.
- إنستغرام: حاوية `/{ig}/media` ثم `/{ig}/media_publish`؛ JPEG فقط من رابط عام؛ حتى 10 صور في الألبوم؛ النص حتى 2200 حرف و30 وسمًا و20 إشارة؛ حد 100 منشور في 24 ساعة (`content_publishing_limit`). الصور المحلية أو غير JPEG تُرفع أولًا كصورة غير منشورة في الصفحة ويُستخدم رابطها من خوادم فيسبوك، ثم تُحذف بعد النشر. لا جدولة في واجهة إنستغرام، فالجدولة محلية.

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
- [ ] المرحلة 6: المحتوى والنشر والإعلانات (ناشر السوشيال أولًا) — التالية
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
- تجربة عرض بكود وباقة على متجر تجريبي (أول تجربة حقيقية لـ productBundleCreate وdiscountCodeBasicCreate)، مع صلاحيتي read_discounts/write_discounts في Shopify.
- «افحص متجري» و«افحص المشروع» (أول تجربة حقيقية لفحص الموقع وOSV وwordpress.org من جهاز المستخدم).
- عند أي خطأ: إرسال `%LOCALAPPDATA%\YousefAI\yousef.log`.

## الخطوة التالية
1. 6.4: الإصدار 0.6.0 (`yousef/__init__.py` و`vscode/package.json`)، README (ناشر السوشيال: الربط بخطوات الرمز، ما يفعله، الصور، الجدولة وشرط بقاء يوسف شغّالًا لإنستغرام، الحدود)، zip + vsix، رفع، وإرسال الملفات.
2. المرحلة 6 — «ناشر السوشيال» عبر Meta Graph API الرسمية (صفحات فيسبوك وحسابات إنستغرام التجارية): مسودات من صانع المحتوى، معاينة، جدولة، والنشر بعد الموافقة فقط؛ ثم «مدير الإعلانات» بقراءة أداء الحملات أولًا.
