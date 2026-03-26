# نظام المخبوزات - نسخة مجانية باستخدام Postgres

هذه النسخة معدلة لتعمل مجانًا على Render Web Service مع قاعدة بيانات Postgres خارجية مجانية مثل Supabase.

## الفكرة
- التطبيق يعمل على Render Free
- البيانات لا تحفظ داخل ملفات Render المحلية
- البيانات تحفظ داخل قاعدة Postgres خارجية عبر `DATABASE_URL`
- عند أول تشغيل، إذا كانت قاعدة Postgres فارغة وكان ملف SQLite موجودًا داخل المشروع، يتم استيراد البيانات الحالية تلقائيًا

## المتغيرات المطلوبة
- `SECRET_KEY`
- `DATABASE_URL`
- `MIGRATE_SQLITE_ON_FIRST_RUN=true`

## نشر سريع
1. ارفع هذا المشروع إلى GitHub
2. في Supabase أنشئ مشروعًا مجانيًا جديدًا
3. انسخ رابط الاتصال Postgres من Supabase
4. في Render أنشئ Web Service مجاني من هذا المستودع
5. أضف متغير البيئة `DATABASE_URL`
6. انشر الخدمة

## ملاحظات
- لا تحتاج Persistent Disk
- لا تعتمد على SQLite بعد النشر
- صلاحيات admin و user1/user2/user3 ما زالت محفوظة من البيانات الأصلية عند أول استيراد
