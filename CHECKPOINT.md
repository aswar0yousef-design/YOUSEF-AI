# نقطة توقف — YOUSEF AI

> اقرأ هذا الملف أولًا في أي محادثة جديدة، وأكمل من "الخطوة التالية" ولا تبدأ من الصفر.
> قاعدة العمل: بعد كل خطوة عمل يُحدَّث هذا الملف (نسخة في المشروع `claude/yousef-ai-checkpoint.md` ونسخة داخل الكود `CHECKPOINT.md`) ويُرفع commit على GitHub.

آخر تحديث: 2026-10-19 — اكتملت الخطوة 4.1

## الحالة الحالية
- المستودع: https://github.com/aswar0yousef-design/YOUSEF-AI (الفرع main). كل خطوة مكتملة = commit.
- آخر إصدار مُسلَّم: 0.3.0 (`yousef-ai-v0.3.0.zip` + `yousef-ai-0.3.0.vsix`) — النواة + الصوت + المبرمج في المشاريع + VS Code + GitHub وVercel.
- المرحلة 4 (المتاجر): الخطوة 4.1 مكتملة ومرفوعة؛ التالي 4.2 (وكيل مدير المتاجر).
- LayanX يبقى على حاله كمرجع فقط.

## المرحلة 4 — الخطة (المتاجر)
- [x] 4.1 موصلات المتاجر (قراءة): سجل متاجر في الإعدادات، رموز كل متجر في keystore، Shopify (Admin GraphQL) وWooCommerce (REST v3): معلومات المتجر، المنتجات، الطلبات بدون بيانات العملاء الشخصية، ملخص المبيعات.
- [ ] 4.2 وكيل «مدير المتاجر»: حلقة أدوات عامة، أدوات المتجر، اقتراحات إنشاء/تعديل منتج وتغيير سعر (كمسودة افتراضيًا) تظهر قبل/بعد وتُنفَّذ بعد الموافقة فقط.
- [ ] 4.3 الواجهة: لوحة «المتاجر» في الإعدادات مع اختبار الاتصال، جدول قبل/بعد في بوابة الموافقة، وعرضها في VS Code.
- [ ] 4.4 تحقق شامل وتغليف 0.4.0.

## قرارات المرحلة 4
- لا حذف للمنتجات ولا ردّ مبالغ ولا تعديل طلبات في هذه المرحلة.
- المنتجات الجديدة تُنشأ كمسودة (draft) ما لم يطلب المستخدم النشر صراحة، وتظهر الحالة في الموافقة.
- لا يقرأ يوسف أسماء العملاء ولا بريدهم ولا عناوينهم ولا هواتفهم من الطلبات؛ فقط الأرقام والمنتجات والحالات، حتى لا تُرسل بيانات شخصية للنماذج السحابية.

## تفاصيل 4.1 (ما بُني)
- `integrations/shopify.py`: Admin GraphQL (الإصدار الافتراضي `2026-10`، أحدث مستقر في 2026-10-19، قابل للتغيير لكل متجر)، ترويسة `X-Shopify-Access-Token`؛ info (الاسم، العملة، عدد المنتجات)، products/product (مع أول 10 متغيرات)، orders بدون أي حقول عملاء؛ create_product (productCreate بحالة DRAFT ثم productVariantsBulkUpdate للسعر وSKU لأن السعر على المتغير الافتراضي)، update_product (productUpdate + المتغير). الصلاحيات المطلوبة: read_products، write_products، read_orders.
- `integrations/woocommerce.py`: REST v3 بـ Basic auth (consumer key:secret)، https إلزامي (عدا localhost)، الطلبات بـ `_fields` بدون billing/shipping؛ إنشاء كمسودة simple، تعديل بـ PUT.
- `integrations/stores.py`: `Stores` — `settings.stores` [{id (8 hex)، name، platform، url، api_version}] والسر في keystore باسم `store:<id>`؛ الإضافة تختبر الاتصال أولًا ولا تحفظ إن فشل؛ `sales_summary` (عدد الطلبات، الإيراد لكل عملة، المتوسط، الحالات، أكثر المنتجات مبيعًا).
- API: `GET/POST /api/stores`، `POST /api/stores/{id}/test`، `GET /api/stores/{id}/summary?days=`، `DELETE /api/stores/{id}`.
- رسائل خطأ واضحة لـ 401/403/404. الاختبارات: خوادم Shopify وWooCommerce وهمية (`tests/store_fakes.py`) تتحقق أيضًا أن استعلامات الطلبات لا تطلب بيانات العملاء.

## ملخص ما بُني
**المرحلة 1 (0.1.0):** بايثون قياسي فقط؛ `start.bat`؛ خادم 127.0.0.1:8765 بحماية Host/Origin وترويسة `X-Yousef-Client` وCSP؛ البيانات في `%LOCALAPPDATA%\YousefAI`؛ مفاتيح DPAPI؛ موجّه نماذج Ollama/Claude/OpenAI/Gemini مع انتقال عند الفشل؛ منسّق (مخطِّط JSON حتى 5 خطوات) ووكلاء: العام، المبرمج، صانع المحتوى، المحلل؛ بوابة موافقة؛ تعلّم بالقواعد والتقييمات؛ لوحة عربية بخط Sakkal Majalla وألوان أبواب مسقط.

**المرحلة 2 (0.2.0):** `yousef/voice/` (faster-whisper اختياري عبر `install-voice.bat` في `.venv`، CPU int8 افتراضيًا)؛ كشف «يوسف» من نص Whisper؛ `/api/voice/{status,load,transcribe}`؛ المهام لها `source` (text/voice)؛ في اللوحة: زر ميكروفون، «الاستماع الدائم»، الموافقة بالصوت، قراءة الردود بأصوات محلية؛ `window.Yousef` في app.js لاستخدام voice.js. 78 اختبارًا.

**المرحلة 3 (0.3.0):** `yousef/workspace/` (مجلدات مسموحة، أدوات ملفات آمنة، رفض ملفات الأسرار، تعديلات مقترحة overlay تُكتب بعد الموافقة مع كشف التعارض)؛ `core/coder.py` حلقة أدوات JSON؛ `core/jsonutil.py`؛ جداول `changes` و`actions`؛ git (status/diff، commit/push بعد الموافقة)، `integrations/github.py` (Pull Request)، `integrations/vercel.py` (حالة النشر، قراءة)؛ أوامر مسموحة لكل مجلد؛ `scope: files`؛ إضافة VS Code في `vscode/` بدون خطوة بناء + `tools/build_vsix.py`. 110 اختبارات.

## خطة المراحل
- [x] المرحلة 1: النواة (0.1.0)
- [x] المرحلة 2: الصوت (0.2.0)
- [x] المرحلة 3: وكيل المبرمج + VS Code + GitHub وVercel (0.3.0)
- [ ] المرحلة 4: المتاجر — Shopify وWooCommerce (قيد العمل)
- [ ] المرحلة 5: المحتوى والنشر والإعلانات
- [ ] المرحلة 6: التحكم بالجهاز، تحليل التداول، تطبيق الجوال
- [ ] لاحقًا: مستمع صوتي أصلي بدون متصفح

## مبادئ ثابتة
- الجهاز: GTX 1660 Super 6GB، 32GB RAM، Ryzen 5 3600 — نموذج محلي مقترح qwen2.5:7b.
- التداول: تحليل وتنبيهات وتداول تجريبي فقط؛ الصفقة الحقيقية بموافقة يدوية.
- النشر عبر الواجهات الرسمية فقط. "التعلم" = ذاكرة، لا إعادة تدريب.

## بانتظار المستخدم (تجربة على ويندوز)
- `start.bat`، حفظ مفتاح API (للتحقق من DPAPI)، طلب متعدد الخطوات مع Ollama.
- `install-voice.bat` ثم الميكروفون والاستماع الدائم (أول تجربة حقيقية لـ faster-whisper).
- `install-vscode-extension.bat` ثم «يوسف: أصلح هذا» على ملف في مشروع حقيقي (أول تجربة داخل VS Code حقيقي).
- إضافة مجلد مشروع git، ورمز GitHub، وتجربة «أصلح وافتح Pull Request».
- عند أي خطأ: إرسال `%LOCALAPPDATA%\YousefAI\yousef.log`.

## الخطوة التالية
1. بناء 4.2: حلقة أدوات عامة (من coder.py)، `StoreToolbox` (list_stores، store_info، list_products، get_product، list_orders، sales_summary، propose_product، propose_update)، إجراءات `store_create_product`/`store_update_product` بقيم قبل/بعد تُنفَّذ بعد الموافقة، تفعيل وكيل store_manager.
