# Deploying Claimit Backend to Vercel

## One-time setup

### 1. Install Vercel CLI
```bash
npm install -g vercel
```

### 2. Login
```bash
vercel login
```

---

## Deploy

From inside the `vercel_backend/` folder:

```bash
cd fastapi/backend/vercel_backend
vercel --prod
```

Vercel will ask a few questions the first time:
- **Set up and deploy?** → Y
- **Which scope?** → your account
- **Link to existing project?** → N (first time)
- **Project name?** → `claimit-backend` (or any name)
- **Directory?** → `.` (current folder)

After deploy you'll get a URL like:
```
https://claimit-backend-xxxx.vercel.app
```

---

## Set Environment Variables on Vercel

Go to: https://vercel.com → your project → Settings → Environment Variables

Add these (copy values from your local `.env`):

| Variable | Value |
|---|---|
| `MONGODB_URL` | `mongodb+srv://user:pass@cluster.mongodb.net/...` |
| `DATABASE_NAME` | `claimit_db` |
| `SECRET_KEY` | your JWT secret key |
| `STATIC_OTP` | `123456` (or remove for production) |
| `TWILIO_ACCOUNT_SID` | (if using SMS OTP) |
| `TWILIO_AUTH_TOKEN` | (if using SMS OTP) |
| `TWILIO_PHONE_NUMBER` | (if using SMS OTP) |
| `SMTP_USERNAME` | (if using email OTP) |
| `SMTP_PASSWORD` | (if using email OTP) |

After adding variables, redeploy:
```bash
vercel --prod
```

---

## Connect Flutter app

Open `claimitapporg/lib/core/constants/app_constants.dart` and switch the baseUrl:

```dart
// Comment out the local line:
// static const String baseUrl = 'http://10.103.197.67:8001';

// Uncomment and fill in your Vercel URL:
static const String baseUrl = 'https://claimit-backend-xxxx.vercel.app';
```

That's it — rebuild the Flutter app and it will talk to Vercel.

---

## Re-deploying after code changes

Any time you add a new route or fix a bug:

```bash
cd fastapi/backend/vercel_backend
vercel --prod
```

If you added a new route file, also copy it from `app/routes/` to `vercel_backend/app/routes/` first.

---

## Verify the deployment

Open in browser:
```
https://your-project.vercel.app/docs
```

You should see the FastAPI Swagger UI with all routes listed.
