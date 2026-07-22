# SteamBoy Highlights MVP

SteamBoy uploads short gameplay/trailer videos directly from the browser to Vercel Blob, then uses a durable Vercel Workflow to inspect, score, and export ranked highlights. Processing uses temporary `/tmp` space only; the source, job record, clips, contact sheets, and JSON report remain in Blob.

## Architecture

1. `@vercel/blob/client` performs a direct multipart browser upload (250 MB enforced by the token route).
2. `POST /api/jobs` writes a durable JSON job record and starts `analyzeVideo` with Vercel Workflow.
3. A retryable workflow step downloads to `/tmp`, invokes the Python analyzer, and uploads every artifact.
4. The browser keeps the job ID in `localStorage` and polls Blob-backed status, so reloads and function instance changes are safe.
5. PySceneDetect locates adaptive cuts; OpenCV samples optical flow, contrast/variety, and black frames; FFmpeg decodes audio energy and exports H.264/AAC clips. Candidate windows combine adjacent scenes, favor 8–30 seconds, penalize black/static passages, and remove overlaps.

Results are explicitly marked **heuristic estimates**. `OPENAI_API_KEY` is reserved for a future optional semantic scoring adapter; the current MVP never needs it and does not send video to a model.

## Local development

Prerequisites: Node 22, Python 3.12, and the Vercel CLI.

```bash
npm install
python3 -m pip install -r requirements.txt
vercel link
vercel env pull .env.local
npm run dev
```

Run the scoring tests with `npm test`. Run a production compile with `npm run build`. `ffmpeg-static` packages the Linux-compatible executable used by the workflow; startup fails with a clear error if it is unavailable.

## Vercel deployment

1. Create a Vercel project and a Blob store, then connect this repository.
2. Enable **Fluid Compute** (also declared in `vercel.json`) and Vercel Workflow for the project.
3. Add environment variables in every relevant environment:

| Variable | Required | Purpose |
| --- | --- | --- |
| `BLOB_READ_WRITE_TOKEN` | Yes | Direct upload tokens and persistent job/artifact storage |
| `OPENAI_API_KEY` | No | Reserved for an optional multimodal evaluator; omitted means heuristic mode |

4. Deploy with `vercel --prod`. Confirm build output contains the workflow and that its maximum duration is accepted by your Vercel plan.
5. Upload a short test MP4, reload while it is processing, and verify that status returns. Preview and download every result, then confirm `jobs/<id>.json` and `outputs/<id>/` in Blob.

## Production smoke-test checklist

- Upload rejects files over 250 MB and unsupported MIME types.
- Analysis rejects actual decoded durations over 180 seconds, even if the client metadata is misleading.
- Refreshing or opening the saved job URL in the same browser retains progress.
- The workflow creates playable H.264/AAC MP4s, JPEG contact sheets, and `report.json` in Blob.
- A deployment without `OPENAI_API_KEY` completes and labels results heuristic.
- To verify execution headroom, test a three-minute 1080p input. If the processing step times out, the job fails with guidance to shorten the source.

The processing boundary is the `processVideo` workflow step. It consumes a Blob URL and produces Blob artifacts, so it can later be replaced by a dedicated worker without changing the upload, polling, or results UI.

## MVP constraints

- Maximum input: 3 minutes and 250 MB; candidate clips: 4–30 seconds; 1–10 results.
- One current job is retained per browser session. There are intentionally no accounts, publishing, captions, music selection, vertical reframing, or long-recording support.
- Local files are ephemeral and are deleted in `finally`; Blob lifecycle/retention should be configured in the Vercel dashboard for your privacy policy.
