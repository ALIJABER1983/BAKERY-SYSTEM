# نظام إدارة المخبوزات - نسخة جاهزة للنشر

هذا المشروع جاهز للنشر على أي استضافة تدعم Python/Flask، ومهيأ ليعمل بقاعدة بيانات SQLite واحدة مشتركة بين جميع المستخدمين.

## ما الذي تم تجهيزه
- تسجيل دخول وصلاحيات: `admin` كامل الصلاحيات، و `user1/user2/user3` للإدخال فقط.
- قاعدة بيانات موحدة واحدة.
- مزامنة تلقائية دورية لإظهار التحديثات لجميع المستخدمين خلال ثوانٍ.
- دعم متغير `DB_PATH` حتى يمكن ربط القاعدة بقرص دائم على الاستضافة.
- إضافة `gunicorn` وملفات نشر جاهزة (`Procfile` و `render.yaml`).

## بيانات الدخول الحالية
- admin / 1234
- user1 / 1111
- user2 / 2222
- user3 / 3333

## التشغيل المحلي
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
ثم افتح `http://localhost:8000`

## النشر على Render
### الأسرع: باستخدام render.yaml
1. ارفع المشروع إلى GitHub.
2. من Render اختر **New + > Blueprint**.
3. اربط المستودع.
4. سيتم إنشاء خدمة Python مع قرص دائم تلقائيًا.
5. بعد أول نشر، انسخ ملف قاعدة البيانات إلى القرص الدائم بحيث يصبح في المسار:
   `/var/data/bakery.sqlite3`
6. افتح رابط الخدمة العام الذي تعطيه Render.

### بديل يدوي
- Build Command:
  `pip install -r requirements.txt`
- Start Command:
  `gunicorn --bind 0.0.0.0:$PORT app:app`
- Environment Variables:
  - `SECRET_KEY` = أي قيمة قوية
  - `DB_PATH` = `/var/data/bakery.sqlite3`
- Persistent Disk:
  - Mount Path = `/var/data`

## مهم جدًا
- إذا لم تستخدم قرصًا دائمًا، فقد تضيع البيانات عند إعادة النشر.
- إذا شغلت أكثر من نسخة من التطبيق مع SQLite نفسها فلن يكون هذا مناسبًا؛ المطلوب نسخة خدمة واحدة تستخدم قاعدة واحدة.
- لتغيير كلمات المرور أو المستخدمين، استخدم حساب الأدمن من داخل الإعدادات.
