# Neo Nile Studio V1 — Dedicated GPU Pod

هذه النسخة مبنية من الصفر لتعمل داخل **RunPod GPU Pod واحد**:

- واجهة الموقع والـBackend داخل نفس الحاوية.
- ACE-Step داخل نفس الحاوية.
- Job Queue داخلية؛ طلب التوليد يرجع فورًا ولا ينتظر الصوت داخل اتصال المتصفح.
- الملفات والموديلات والمشروعات محفوظة تحت `/workspace/neo-nile`.
- Diagnostics وLogs داخل واجهة البرنامج.
- تصدير Original WAV وMaster WAV 24-bit وMP3 Preview.
- لا Streamlit، ولا Serverless، ولا Load Balancer Endpoint، ولا API Keys بين خدمات متعددة.

## المحرك

- ACE-Step Docker image: `ghcr.io/ace-step/ace-step-1.5:0.1.8`
- Music model: `acestep-v15-xl-turbo`
- LM: `acestep-5Hz-lm-1.7B`
- GPU target: 24 GB VRAM
- Web port: `8000`

## قبل النشر

شغّل:

```bash
python self_test.py
```

## طريقة البناء

ملف GitHub Actions الموجود داخل `.github/workflows/build-image.yml`:

1. يفحص Python.
2. يبني Docker image.
3. ينشرها تلقائيًا إلى:

```text
ghcr.io/<github-user>/<repository>:latest
```

## إعداد Pod المقترح

- GPU: 24 GB أو أعلى.
- Container image: صورة GHCR الناتجة.
- Expose HTTP port: `8000`.
- Container disk: 25 GB.
- Network Volume: 40–60 GB، mounted automatically at `/workspace`.
- Environment variable واحد مطلوب:

```text
NEO_NILE_PASSWORD = كلمة مرور قوية
```

اختياري:

```text
NEO_NILE_USER = studio
```

## أول تشغيل

1. افتح رابط HTTP Service للبورت 8000.
2. المتصفح سيطلب Username وPassword.
3. افتح Diagnostics.
4. انتظر حتى Engine = Ready.
5. أنشئ Project.
6. أول اختبار: 30 sec، 1 version، BPM 104.
7. بعد نجاح 3 اختبارات، استخدم مدد أطول.

## حفظ البيانات

تُحفظ في:

```text
/workspace/neo-nile/checkpoints
/workspace/neo-nile/projects
/workspace/neo-nile/database
/workspace/neo-nile/logs
```

Network Volume تبقى مستقلة عن الـPod. مع ذلك، نزّل الأعمال المهمة إلى جهازك أيضًا.

## وضع المحاكاة

لتجربة الواجهة من غير GPU:

```text
NEO_NILE_FAKE_ENGINE=true
```

في هذا الوضع البرنامج يولّد ملف WAV اختباريًا ويختبر المشروع والـQueue والماستر والتنزيل، لكنه لا يولّد موسيقى AI حقيقية.
