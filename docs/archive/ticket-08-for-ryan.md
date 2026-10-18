# Iris: spoken Gemini descriptions (ticket 08)

Hey Ryan, the server is adding spoken scene descriptions. Here's what changes for the app, what I need you to OK, and how we'll test it. Nothing changes for you until you're ready: the new fields are optional, and the current app keeps working as is.

## What the user will hear

1. **Instantly, unchanged:** the short alert, e.g. *"Possible person, left."* The app already speaks this.
2. **About 1 to 2 s later, new:** one sentence from Gemini describing the scene, e.g. *"Two possible people to your left, appear to be standing in shallow water."*

Gemini only returns **text**. The app reads it aloud with the same Apple voice it already uses for alerts (`AVSpeechSynthesizer`). No audio files and no new audio player.

## What changes in the `/frame` reply

Two optional fields, both additive. Nothing is removed or renamed.

| Field | Type | Meaning |
| --- | --- | --- |
| `description` | `String?` | Already in the contract and already decoded by the app. Now it's meant to be **spoken**, not just shown. |
| `description_id` | `Int?` | **New.** Increases with each new description. `null` whenever `description` is `null`. |

Example reply right after a description arrives:
```json
{
  "detections": [{"label": "person", "conf": 0.92, "side": "left"}],
  "alert": null,
  "description": "Two possible people to your left, appear to be standing in shallow water.",
  "description_id": 7,
  "frame_id": 1042,
  "server_ms": 68
}
```
The same description and id appear on every reply for up to 10 s. That's why the app needs the id: to speak it only once.

## What the app needs to do

1. **Decode `description_id`** as an optional `Int` in `FrameResponse` (`CodingKeys`: `descriptionID = "description_id"`).
2. **Speak each `description_id` once.** Remember the last id you spoke. Only speak when a reply has a non-null `description_id` you haven't spoken yet.
3. **Alerts always win:**
   - A new **alert** interrupts a description that's playing.
   - A **description never interrupts an alert.** It waits until the alert finishes.
   - If a description is already waiting and a newer one arrives, keep only the newest.
4. **Show the text on screen** too, as the status screen already does.
5. **Reset the remembered id** when streaming stops, or when the server URL or key changes.

Heads-up: today `FrameAlertSpeaker.speak()` stops whatever is playing before speaking. That's right for alerts, but a description arriving mid-alert would cut the alert off. So descriptions need a separate path that waits for the synthesizer to finish (for example via `AVSpeechSynthesizerDelegate`'s `didFinish`).

## What the server does (so you know what to expect)

- **One sentence per scene, not per person.** Two or three people get one combined sentence, grouped by direction. More than three gets summarized ("several people ahead").
- **No spam:** at most one description every 15 s, and none if the scene hasn't changed (same number of people, same directions). Someone standing still is described once, then silence until something changes.
- **Late answers are dropped:** if Gemini takes more than 5 s, the server throws the answer away instead of sending a stale description.
- **If Gemini fails or is slow,** `description` just stays `null`. The short alert still works exactly as today.

## What I need from you

1. **OK the plan:** adding `description_id`, and speaking each new description once, after alerts.
2. **One question:** if a description has been waiting behind alerts for a while (say more than 5 s), should the app **drop it** instead of speaking it late? My suggestion is yes, drop it, since a late description may no longer match what's in front of the user. Your call.

## Testing plan

- **Server side:** I'm building this on a branch called `gemini-voice`. It won't be merged into `main` until we've both tested it, and the current server stays on `main` meanwhile.
- **Your side:** please make the app change on a matching branch.
- **Together:** when both are ready, I'll deploy `gemini-voice` to the Vultr server (same URL and key as now), and you point your branch build at it. If we don't like it, I can switch the server back to the pre-Gemini version in about a minute.

Done when:
- a real alert is followed by a spoken description within about 3 s
- someone standing still isn't described over and over
- two people get one combined sentence
- the office test video doesn't produce made-up disaster details (no "flood" in the office)

This is separate from the overlay flicker fix, which can land first and independently.
