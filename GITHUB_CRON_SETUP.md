# GitHub Scheduled Upload Setup (YouTube + IG/FB)

## 1. One-time local auth (YouTube)

Run this locally once:

```powershell
python refresh_youtube_token.py
```

This creates/updates `token.pkl` with your refresh token.

## 2. Create GitHub Secrets (base64)

From PowerShell in project root:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.pkl"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("meta_credentials.json"))
```

Add these as repository secrets:

- `YOUTUBE_CREDENTIALS_JSON_B64`
- `YOUTUBE_TOKEN_PKL_B64`
- `META_CREDENTIALS_JSON_B64`

## 3. Set GitHub Variables

Repository Variables used by workflow:

- `UPLOADER_METADATA_FOLDER`
  - Path to metadata JSON folder on runner.
  - Example (repo folder): `metadata`
- `UPLOADER_VIDEO_FOLDER` (optional)
  - Path to local mp4 files on runner.
  - If videos are not local, the headless runner can download from public URL fields in metadata.

## 4. Workflow

Workflow file:

- `.github/workflows/scheduled_upload.yml`

It supports:

- Scheduled run (every 6 hours by default)
- Manual run (`workflow_dispatch`) with optional:
  - `platforms` (default: `youtube,igfb`)
  - `dry_run` (`true/false`)

## 5. Metadata requirements

An asset is processed only when:

- `upload_status.youtube.approved == true`

Source video URL can come from any of:

- `youtube_video_url`
- `public_video_url`
- `instagram_video_url`
- `google_drive_link`
- `google_drive_url`

## 6. Notes

- TikTok is untouched.
- YouTube runs non-interactive by default in CI (no browser popup).
- IG/FB system token is used as-is from `meta_credentials.json`.
