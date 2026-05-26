# Railway Turso Setup — One-Time Fix

## Why
Without these env vars, Railway falls back to local SQLite inside the Docker container.
Every deploy wipes that SQLite. Users disappear on restart.

## What to do
Run these 2 commands (you'll need to `railway login` first, which opens a browser):

```
railway login

railway variables set \
  TURSO_DATABASE_URL=libsql://bankparse-preview-mitchellagoma.aws-eu-west-1.turso.io \
  TURSO_AUTH_TOKEN=eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE3Nzg5MzAyNjUsImlkIjoiMDE5ZTFlMDMtZjIwMS03MjcyLWI4MzgtMzBiMzNiNWQ2OGQ2IiwicmlkIjoiZTU3MTM0OWYtZTMyMS00NDEzLWFmN2MtNGYyZGZkNTkyMjRkIn0.gvcZhh0IxkVvpJB-2z2ruy1_6fbKvjsdHpmlbiGKdQYZoo0WrBXfT7h6nvja-UepKSaqK-cekGnN6JtvSaF2Ag
```

Then redeploy:
```
railway up
```

## Verify
After deploy, `https://bankscanai.com/admin` should show users persisting across deploys.
