# Kopiyka
Витрати та облік витрат

## Доступ через ngrok

Для поточного dev-тунелю `5092-176-102-32-215.ngrok-free.app` уже дозволений у Django. Якщо ngrok видасть іншу адресу, перед запуском Django задай її через `NGROK_HOST` (без `https://`):

```bash
cd kopiyka
NGROK_HOST=your-tunnel.ngrok-free.app python manage.py runserver
```

Додаткові локальні або тестові hostnames можна передати через `DJANGO_ALLOWED_HOSTS`, розділивши їх комами. Налаштування додає лише точні hostname до `ALLOWED_HOSTS` та відповідні HTTP/HTTPS origins для CSRF; wildcard для всіх ngrok доменів не використовується.
