# GitHub Scheduled Upload Setup (YouTube + IG/FB + Pinterest)

## 1. One-time local auth (YouTube + Pinterest)

Run this locally once:

```powershell
python refresh_youtube_token.py
python refresh_pinterest_token.py
```

This creates/updates:

- `token.pkl` for YouTube refresh
- `pinterest_oauth_token.json` for Pinterest refresh
- `pinterest_credentials.json` with the latest Pinterest access token

## 2. Create GitHub Secrets (base64)

From PowerShell in project root
(replace `google_service_account.json` with your actual local service-account key filename if needed):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.pkl"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("meta_credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("pinterest_credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("pinterest_oauth_credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("pinterest_oauth_token.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("google_service_account.json"))
```

Add these as repository secrets:

- `YOUTUBE_CREDENTIALS_JSON_B64`
- `YOUTUBE_TOKEN_PKL_B64`
- `META_CREDENTIALS_JSON_B64`
- `PINTEREST_CREDENTIALS_JSON_B64`
- `PINTEREST_OAUTH_CREDENTIALS_JSON_B64`
- `PINTEREST_OAUTH_TOKEN_JSON_B64`
- `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (required when syncing metadata/video folders from Google Drive)
- `AUTH_SYNC_GITHUB_TOKEN` (optional token that lets the workflow update the auth-cache GitHub secrets after a successful refresh)
- `ALERT_WEBHOOK_URL` (optional webhook for YouTube/Pinterest auth failure alerts)

Notes:

- `YOUTUBE_TOKEN_PKL_B64` must be the full single-line base64 value.
- The JSON-based secrets can be stored either as the full single-line base64 value or as raw JSON text.
- `PINTEREST_OAUTH_TOKEN_JSON_B64` should come from `pinterest_oauth_token.json` after a successful local `python refresh_pinterest_token.py` run so it includes the latest `refresh_token`.
- If `AUTH_SYNC_GITHUB_TOKEN` is set, the workflow updates `YOUTUBE_TOKEN_PKL_B64`, `PINTEREST_CREDENTIALS_JSON_B64`, `PINTEREST_OAUTH_CREDENTIALS_JSON_B64`, and `PINTEREST_OAUTH_TOKEN_JSON_B64` automatically after a successful auth refresh in CI.
- If `ALERT_WEBHOOK_URL` is set, the workflow sends a JSON webhook when YouTube or Pinterest auth refresh fails.
- The workflow now refreshes Pinterest non-interactively before upload when Pinterest is selected. If the Pinterest OAuth secrets are missing or the refresh fails, the run removes `pinterest` from the platform list and emits a step summary entry.
- The workflow also writes auth output to `.runtime/logs/youtube_auth.log` and `.runtime/logs/pinterest_auth.log`, and adds a GitHub Actions step summary entry on failure.

Recommended for stable scheduled auth:

- Create a private Google Drive folder for auth cache.
- Put these files in it: `token.pkl`, `pinterest_credentials.json`, `pinterest_oauth_credentials.json`, `pinterest_oauth_token.json`.
- Share that folder with the service-account email from `google_service_account.json`.
- Set repository variable `GOOGLE_DRIVE_AUTH_FOLDER_URL`.
- With `GOOGLE_DRIVE_AUTH_FOLDER_URL` set, the workflow restores those auth files before refresh checks and pushes updated copies back after each run. That keeps rotating Pinterest tokens and the latest YouTube token file alive across GitHub runners.
- In that mode, `YOUTUBE_TOKEN_PKL_B64`, `PINTEREST_CREDENTIALS_JSON_B64`, `PINTEREST_OAUTH_CREDENTIALS_JSON_B64`, and `PINTEREST_OAUTH_TOKEN_JSON_B64` become bootstrap fallbacks instead of always-required secrets.

Optional alternative when you do not want to use Google Drive for auth cache:

- Create a repository secret named `AUTH_SYNC_GITHUB_TOKEN`.
- Use a token that can update Actions repository secrets for this repo.
- The workflow will push refreshed auth-cache files back into the corresponding GitHub secrets after successful CI refreshes.
- You still need the initial bootstrap secrets once, and you still need to re-auth locally if Google/Pinterest rejects the saved refresh token entirely.

## 3. Set GitHub Variables

Repository Variables used by workflow:

- `UPLOADER_METADATA_FOLDER`
  - Path to metadata JSON folder on runner.
  - Example (repo folder): `metadata`
- `UPLOADER_VIDEO_FOLDER` (optional)
  - Path to local mp4 files on runner.
  - If videos are not local, the headless runner can download from public URL fields in metadata.
- `GOOGLE_DRIVE_AUTH_FOLDER_URL` (optional, recommended)
  - Google Drive folder URL for auth-cache files.
  - When set, the workflow restores `token.pkl`, `pinterest_credentials.json`, `pinterest_oauth_credentials.json`, and `pinterest_oauth_token.json` from Drive before auth checks and pushes refreshed copies back after the run.
- `GOOGLE_DRIVE_METADATA_FOLDER_URL` (optional)
  - Google Drive folder URL for metadata JSON files.
  - When set, the workflow downloads metadata from Drive into a temporary runner folder and syncs upload-status updates back to Drive after the run.
- `GOOGLE_DRIVE_VIDEO_FOLDER_URL` (optional)
  - Google Drive folder URL for source video files.
  - When set, the workflow downloads mp4/mov/m4v files into a temporary runner folder before the upload job starts.

If you use Google Drive-backed auth, metadata, or video folders, share those folders with the service-account email from `google_service_account.json`.

## 4. Workflow

Workflow file:

- `.github/workflows/scheduled_upload.yml`

It supports:

- Scheduled runs 4 times daily with preset-specific slots:
  - `00:03 UTC` (`08:03 Asia/Singapore`) -> `faith`
  - `06:12 UTC` (`14:12 Asia/Singapore`) -> `love`
  - `12:21 UTC` (`20:21 Asia/Singapore`) -> `sentimental`
  - `18:30 UTC` (`02:30 Asia/Singapore next day`) -> `neutral`
- Manual run (`workflow_dispatch`) with optional:
  - `platforms` (default: `youtube,igfb,pinterest`)
  - `pinterest_only` (`true/false`) to force a Pinterest-only run via checkbox
  - `dry_run` (`true/false`)
- Each scheduled run picks exactly one approved asset at random for that run's preset and uses that same asset for the selected platforms in that run.
- When multiple assets match a preset, the scheduler now prioritizes assets with the most pending selected platforms first. This avoids all-platform runs randomly picking assets that only still need Pinterest while YouTube/IG-FB-ready assets are still available.

Drive-backed mode:

- If `GOOGLE_DRIVE_AUTH_FOLDER_URL` is set, the workflow restores the auth-cache files before auth checks and pushes refreshed copies back after the run.
- If `GOOGLE_DRIVE_METADATA_FOLDER_URL` is set, you do not need a `metadata/` folder in the repo.
- If `GOOGLE_DRIVE_VIDEO_FOLDER_URL` is set, you do not need local videos checked into the repo.
- Metadata files are downloaded from Drive before the job and pushed back to the same Drive folder after upload-status changes.

## 5. Metadata requirements

An asset is processed only when:

- `upload_status.youtube.approved == true`
- It still has pending work for at least one selected platform.

Source video URL can come from any of:

- `youtube_video_url`
- `public_video_url`
- `instagram_video_url`
- `google_drive_link`
- `google_drive_url`

## 6. Notes

- TikTok is untouched.
- YouTube runs non-interactive by default in CI (no browser popup).
- Pinterest now refreshes non-interactively in CI using `pinterest_oauth_credentials.json` + `pinterest_oauth_token.json`.
- IG/FB system token is used as-is from `meta_credentials.json`.
- For CI, set `google_service_account_json` in `meta_credentials.json` to `google_service_account.json`.
- If you want unattended YouTube uploads to keep working long-term, do not leave the Google OAuth consent screen in `Testing`. Google can expire refresh tokens from external apps in testing after 7 days.
- Automatic secret syncing only keeps successfully refreshed tokens current in GitHub. It cannot recover from a revoked or expired refresh token; that still requires running local OAuth again.
