# O'z O'ying — Jeopardy O'yini

Real-vaqtli WebSocket asosidagi Jeopardy viktorina o'yini.

## Xususiyatlar

- 🎯 **Space tugmasi buzzer** — Klaviaturadagi SPACE birinchi bosgan o'yinchi javob beradi
- ⏱ **Ikki bosqichli taymer** — 30 soniya savol vaqti + 10 soniya javob vaqti
- 📸 **Media qo'llab-quvvatlash** — Savol va javoblarda rasm, video, audio
- ✏️ **Ichki savol muharriri** — JSON talab qilmaydi, brauzerdan to'g'ridan-to'g'ri yarating
- ➖ **Minus ochko** — Noto'g'ri javobda ball ayriladi
- 🏠 **Host boshqaruvi** — To'g'ri/Noto'g'ri tugmalari, ball moslashtirish, o'yinchi chiqarish
- 💬 **Real-vaqtli chat**
- 👁 **Tomoshabin rejimi**

## 🚀 Render.com ga Bepul Deploy Qilish (Doimiy Link)

Loyihani bepul va doimiy web-manzilga ega qilish uchun quyidagi 3 ta qadamni bajaring:

### 1-qadam: Kodni GitHub-ga yuklang
1. [GitHub.com](https://github.com) da yangi repo (masalan: `jeopardy-uz`) oching.
2. Kompyuteringizda terminalda shu loyiha papkasiga kiring va kodni yuklang:
   ```bash
   git init
   git add .
   git commit -m "Deploy tayyor"
   git branch -M main
   git remote add origin https://github.com/SIZNING_PROFILINGIZ/REPA_NOMI.git
   git push -u origin main
   ```

### 2-qadam: Render.com-ga ulaning
1. [Render.com](https://render.com) ga kiring va GitHub orqali ro'yxatdan o'ting.
2. **"New +"** tugmasini bosing va **"Blueprint"** (yoki Web Service) ni tanlang.
3. GitHub reponi ulayotganda Render avtomatik ravishda `render.yaml` faylini topadi va hamma narsani (Python versiyasi, port va h.k.) o'zi sozlaydi.

### 3-qadam: Linkni oling
1. "Apply" (yoki Create) tugmasini bosing.
2. Deploy jarayoni 2-3 daqiqa davom etadi. Yakunlangach, ekranda `https://jeopardy-game-uz.onrender.com` kabi doimiy link paydo bo'ladi.

> ⚠️ **Muhim:** Render bepul planida server 15 daqiqa davomida hech kim kirmasa "uxlab qoladi". Birinchi marta kirganda server "uyg'onishi" uchun 30-60 soniya kutishingiz kerak bo'lishi mumkin. Yuklangan rasmlar server har safar o'chib yonganda (kunda 1 marta) o'chib ketadi.

## O'yin Tartibi

1. **Host:** Bosh sahifada "Xona Yaratish" bosing → "Savollar" muharririga kiring va savollar kiriting → "Saqlash" → "Boshlash"
2. **O'yinchilar:** Xona kodini kiritib qo'shilishadi
3. **Host** taxtadan savol tanlaydi
4. **O'yinchilar** SPACE yoki buzzer tugmasini bosadi
5. **Host** to'g'ri/noto'g'ri deb belgilaydi

## Fayl Tuzilmasi

```
jeopardy/
├── main.py          # FastAPI backend, WebSocket handler
├── game.py          # GameRoom klassi
├── manager.py       # ConnectionManager klassi
├── requirements.txt
├── Procfile         # Render uchun
├── uploads/         # Media fayllar (auto-yaratiladi)
└── static/
    ├── index.html   # Lobby
    ├── host.html    # Host paneli
    ├── player.html  # O'yinchi ko'rinishi
    └── editor.html  # Savol muharriri
```
