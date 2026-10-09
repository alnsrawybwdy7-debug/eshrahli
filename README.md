# اشرحلي — منصة الشروحات الخاصة

منصة Flask لشرح مواد القسم باشتراك:
- الطالب يسوي حساب ← يطلعله زر "مراسلة على تيليكرام" (برسالة جاهزة بيها اسم المستخدم مالته) + خانة رمز الاشتراك.
- كل رمز يشتغل **مرة وحدة**، ومدته **تبدي من لحظة إدخاله** (وإذا عنده اشتراك فعّال تنضاف على الباقي).
- انت تضيف المواد، وكل مادة بيها 3 أقسام: **فيديوات شرح / ملخصات / واجبات**. تكدر تخفي، ترتب، تعدل، تحذف.
- **بث مباشر داخل المنصة** (LiveKit): من تبدي بث يطلع لكل المشتركين شريط "انضمام للاتصال" بكل الصفحات. فيه مايك، كاميرا، مشاركة شاشة (لك)، ودردشة.

## نظام الاشتراك
- عند توليد الرموز تختار: **كل المواد** أو **مواد محددة** (وحدة أو أكثر).
- الطالب يشوف كل المواد، بس المشترك بيها تنفتح والباقي تطلع مقفولة ويا زر اشتراك.
- رمز مادة جديد يضيف المدة على الباقي بنفس المادة، ويفتح المواد الجديدة.
- البث: تختار المادة وقت البدء، فيطلع بس لمشتركي المادة ولمشتركي كل المواد. بدون مادة = لكل المشتركين.
- من صفحة الطلاب: تضيف أيام لكل المواد أو لمادة معينة، وتفلتر مشتركي كل مادة.
- الأسعار من `.env`: `PRICE_SUBJECT` و `PRICE_ALL` و `PLAN_INCLUDES`.

## تغيير الاسم والمدرّسة والتواصل
كلها من ملف `.env`:
```
SITE_NAME=اشرحلي
ADMIN_NAME=فاطمة ثائر
TEACHER_LABEL=المدرّسة
CONTACT_TELEGRAM=nomiya_0
```
اسم المدرّسة يتحدث تلقائياً على حساب الأدمن أول ما يشتغل السيرفر.

## التوافق مع الأجهزة
- **الموبايل**: شريط تنقل سفلي مثل التطبيقات، ويحترم نوتش الآيفون.
- **الآيباد**: عمودين أو ثلاثة حسب الوضع (عمودي/أفقي)، وأزرار بحجم مناسب للمس.
- **الحاسبة**: تخطيط كامل مع لوحات جانبية.

## التشغيل المحلي (Windows)

```bash
cd course_platform
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env      # وعبّي SECRET_KEY و ADMIN_USERNAME و ADMIN_PASSWORD
python app.py
```
افتح http://127.0.0.1:5000 وسجّل دخول بحساب الأدمن.

بدون R2، الملفات تنخزن بـ `instance/uploads` — زين للتجربة، بس للفيديوات الحقيقية اربط R2.

## Cloudflare R2 (الفيديوات والملفات)
1. Cloudflare ← R2 ← Create bucket (مثلاً `courses`). **خلّيه Private** (لا تفعّل Public access).
2. R2 ← Manage API tokens ← Create token بصلاحية **Object Read & Write** على هذا البكت.
3. عبّي `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` بـ `.env`.
4. بإعدادات البكت ← **CORS policy** الصق محتوى `r2-cors.json` وبدّل `YOUR-DOMAIN.com` بدومينك.

الرفع يروح من المتصفح لـ R2 مباشرة (السيرفر ما يتحمل حجم الفيديو)، والمشاهدة بروابط موقّعة تنتهي بعد 3 ساعات وما تنعطي غير للمشترك.

## LiveKit (البث المباشر)
1. سوّي حساب على https://cloud.livekit.io (فيه طبقة مجانية).
2. Settings ← Keys ← انسخ `URL` و `API Key` و `API Secret` لـ `.env`.
3. إذا تريد الطلاب بس يسمعون ويكتبون بالدردشة: `LIVE_STUDENTS_CAN_PUBLISH=0`.

ملاحظة: المايك والكاميرا بالمتصفح يحتاجن **HTTPS** (أو localhost).

## النشر
- **قاعدة البيانات**: فارغة = SQLite. لـ Neon حط رابطه بـ `DATABASE_URL`.
- **PythonAnywhere المجاني**: يشتغل مع SQLite (لأن المجاني يمنع الاتصال بـ Postgres الخارجي). توقيع روابط R2 وتوكن LiveKit يصيرن محلياً بدون اتصال خارجي، فيشتغلن.
- **Render / Koyeb**: أمر التشغيل `gunicorn app:app`، وخلي `TRUST_PROXY=1` و `COOKIE_SECURE=1`، واستخدم Neon لأن قرص هالمنصات مؤقت.
- بالإنتاج: `SECRET_KEY` ثابت وطويل، و `COOKIE_SECURE=1`.

نسيت كلمة مرور الأدمن؟
```bash
flask --app app create-admin abdullah NEW_PASSWORD
```

## النشر على PythonAnywhere (مجاني، بدون بطاقة)
1. حساب جديد على https://eu.pythonanywhere.com (السيرفرات الأوروبية أقرب للعراق). اسم المستخدم يصير رابطك: `USERNAME.eu.pythonanywhere.com`
2. من **Consoles ← Bash**:
```bash
git clone https://github.com/alnsrawybwdy7-debug/eshrahli.git
cd eshrahli
mkvirtualenv eshrahli --python=python3.11
pip install -r requirements.txt
nano .env     # الصق الإعدادات (DATABASE_URL فارغ + VERIFY_UPLOADS=0) ثم Ctrl+O و Enter و Ctrl+X
```
3. من **Web ← Add a new web app ← Manual configuration ← Python 3.11**:
   - **Source code**: `/home/USERNAME/eshrahli`
   - **Virtualenv**: `/home/USERNAME/.virtualenvs/eshrahli`
   - **WSGI configuration file**: امسح محتواه وحط:
     ```python
     import sys
     path = "/home/USERNAME/eshrahli"
     if path not in sys.path:
         sys.path.insert(0, path)
     from app import app as application
     ```
   - **Static files**: URL `/static/` ← Directory `/home/USERNAME/eshrahli/static`
   - فعّل **Force HTTPS** ثم اضغط **Reload**.
4. ضيف `https://USERNAME.eu.pythonanywhere.com` لـ CORS بكت R2.

ملاحظات النسخة المجانية: قاعدة البيانات SQLite (تنحفظ عادي)، السيرفر ما يكدر يتصل بـ R2 فـ `VERIFY_UPLOADS=0`، ولازم تضغط **Run until 3 months from today** بصفحة Web كل 3 شهور.

**التحديث بعدين:** `cd ~/eshrahli && git pull` بالـ Bash، ثم **Reload** من صفحة Web.

## ربط دومين خاص (بدون اشتراك مدفوع)
الموقع يبقى على PythonAnywhere، و Cloudflare Worker (`cloudflare-worker.js`) يعرضه على دومينك:
1. ضيف الدومين لـ Cloudflare (Free plan) وغيّر الـ Nameservers عند الشركة اللي اشتريت منها.
2. Workers & Pages ← Create ← Worker ← الصق `cloudflare-worker.js` ← Deploy.
3. Worker Settings ← Variables: `ORIGIN=https://bo01.eu.pythonanywhere.com` و `PROXY_SECRET` (Secret).
4. Worker Settings ← Domains & Routes ← Add Custom Domain ← دومينك.
5. بـ `.env` على PythonAnywhere: نفس `PROXY_SECRET`، ثم Reload.
6. ضيف `https://دومينك` لـ CORS بكت R2.

## الحماية المطبقة
- كلمات المرور مشفرة (Werkzeug scrypt)، وحماية CSRF على كل الطلبات.
- Rate limiting على تسجيل الدخول، إنشاء الحسابات، وإدخال الرموز (ضد التخمين).
- الرمز ينحجز بعملية ذرية، فما يكدر طالبين يستخدمون نفس الرمز بنفس اللحظة.
- **جهاز واحد لكل طالب** (`SINGLE_DEVICE=1`): إذا دخل من جهاز ثاني يطلع من الأول — يقلل مشاركة الحسابات.
- علامة مائية متحركة باسم الطالب فوق الفيديو (تردع تسريب تسجيل الشاشة)، ومنع زر التنزيل.
- Content-Security-Policy وهيدرات أمان، ومسارات الملفات محمية من path traversal.
- لوحة التحكم ترجع 404 لغير الأدمن.

ملاحظة صريحة: ماكو طريقة تمنع 100% تسجيل الشاشة أو تنزيل فيديو يشتغل بالمتصفح؛ الحماية هنا تصعّبها وتخلي أي تسريب ينعرف مصدره.

## هيكل المشروع
```
app.py            التطبيق والإعدادات العامة والهيدرات
config.py         المتغيرات من .env
models.py         الجداول: users, sub_codes, subjects, contents, live_sessions
security.py       CSRF، rate limit، صلاحيات
storage.py        R2 أو تخزين محلي
livekit_util.py   توكنات LiveKit
routes/           auth, student, admin, live
templates/        الواجهات (RTL)
static/           CSS و JS ومكتبة LiveKit
```
