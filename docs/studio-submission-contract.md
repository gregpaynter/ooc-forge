# Forge → OOC Studio Submission Contract

Status: v1 contract for human-led OOC Studio Creative Sessions.

## Boundary

Forge makes and curates. OOC admits and publishes.

A local Forge Creative Session is never itself an OOC Work and never causes automatic publication. `Present to OOC` creates an immutable Studio Submission snapshot for human review in OOC System.

The Studio Submission carries only the web-ready representation needed by OOC:

1. selected **Work Image** (the web image / website thumbnail), and
2. selected **OOC Experience** (**mobile video with audio**).

The Forge retains Studies, Reference Image, Seed/source image, print master, print plate, production/silent video, standalone audio and intermediate render material. The manifest may carry hashes and Forge-local references for retained production assets, but those files are not uploaded to OOC.

Place and Placement are deliberately absent from this contract. They occur after the canonical Work exists in OOC.

## HTTP

`POST /api/studio/submissions`

Authentication:

```text
X-OOC-Machine-Token: <paired Forge machine token>
X-OOC-Submission-Id: <manifest submission_id>
```

Content type: `multipart/form-data`

Parts:

- `manifest` — canonical JSON text conforming to `ooc.studio-submission.v1`
- `work_image` — the exact selected Work Image binary
- `experience` — the exact selected `video_mobile_with_audio` MP4 binary

No other Forge artifact is attached.

## Manifest

```json
{
  "schema": "ooc.studio-submission.v1",
  "submission_id": "<uuid>",
  "presented_at": "2026-09-05T05:00:00.000Z",
  "source": {
    "kind": "FORGE_CREATIVE_SESSION",
    "forge_id": "<forge uuid>",
    "creative_session_id": "<session uuid>",
    "creative_session_updated_at": "<UTC timestamp>"
  },
  "creative_agency": {
    "kind": "HUMAN_STUDIO"
  },
  "proposed_attribution": {
    "creator_kind": "ARTIST",
    "creator_id": "<existing OOC artist id>",
    "display_name": "<optional local display label>",
    "role_code": "CREATOR"
  },
  "work": {
    "title": "Ball in the Forest",
    "description": null,
    "work_image": {
      "role": "thumbnail",
      "filename": "thumbnail.webp",
      "mime_type": "image/webp",
      "size_bytes": 123456,
      "sha256": "<sha256>"
    }
  },
  "experience": {
    "experience_type": "VIDEO",
    "media": {
      "role": "video_mobile_with_audio",
      "filename": "video-mobile-with-audio.mp4",
      "mime_type": "video/mp4",
      "size_bytes": 1234567,
      "sha256": "<sha256>"
    }
  },
  "provenance": {
    "creative_prompt": "ball in the forest, woodblock print",
    "negative_prompt": null,
    "seed_work_sha256": "<sha256>",
    "retained_production_assets": {
      "seed_work": {
        "role": "seed_work",
        "forge_ref": "library/works/<session>/seed-work.png",
        "sha256": "<sha256>",
        "size_bytes": 123456,
        "mime_type": "image/png"
      },
      "print_master": {
        "role": "print_master",
        "forge_ref": "library/.../print-master.png",
        "sha256": "<sha256>",
        "size_bytes": 12345678,
        "mime_type": "image/png"
      },
      "print_plate": {
        "role": "print_plate",
        "forge_ref": "library/works/<session>/etching-plate-inverse.png",
        "sha256": "<sha256>",
        "size_bytes": 123456,
        "mime_type": "image/png"
      },
      "video_master": {
        "role": "video_master",
        "forge_ref": "library/experiences/<job>/video-master.mp4",
        "sha256": "<sha256>",
        "size_bytes": 12345678,
        "mime_type": "video/mp4"
      },
      "audio_master": {
        "role": "audio_master",
        "forge_ref": "library/audio/<job>/audio-master.flac",
        "sha256": "<sha256>",
        "size_bytes": 12345678,
        "mime_type": "audio/flac"
      }
    }
  },
  "publication": {
    "requested": false,
    "note": "Forge presents a Studio Submission only; OOC Admin admits and publishes explicitly."
  }
}
```

`retained_production_assets` is sparse: only retained artifacts that currently exist are included.

## Curation invariants

- The Work Image is selected explicitly by the human operator; the latest file is never chosen implicitly.
- The Experience is selected explicitly and must have role `video_mobile_with_audio`.
- A silent mobile video, production video, video master or standalone audio cannot be selected as the OOC Experience.
- The proposed attribution is an existing OOC `ARTIST`; OOC Admin may confirm/change it during review.
- An artifact currently selected for OOC submission cannot be deleted until another artifact is selected.
- Presenting creates a snapshot. Later Forge changes do not mutate a submission already received by OOC.
- No publish request is sent by Forge. Publication authority remains in OOC System.

## Seed geometry and video aesthetic

Creative Session canvas choices are limited to:

- `1:1`
- `4:3` landscape
- `4:3` portrait
- `16:9` landscape
- `16:9` portrait

The selected Seed Work is the geometry and aesthetic authority for all video derivatives. Production/draft render dimensions are derived from the Seed ratio and orientation. The video prompt compiler and Wan segment prompts enforce the rule:

> Seed Work governs look. Video direction governs motion.

The derivative must preserve the Seed's medium, palette, texture, line quality, rendering style, compositional language, visual density and atmosphere. User video direction may introduce movement or transformation, but it does not authorize restyling.

## Expected OOC response

OOC should respond with JSON similar to:

```json
{
  "submission_id": "<same or canonical OOC submission id>",
  "status": "SUBMITTED"
}
```

Receiving this payload must not create a public Work automatically. OOC Admin review is the next authority boundary.
